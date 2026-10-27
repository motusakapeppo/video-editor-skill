"""vx.edl3_extremes — reverse and freeze with a declared cap (F2-T06).

Both treatments are pure planners. They never run ffmpeg, never read a file and
return the same dict for the same inputs; the orchestrator wires the fragments
into ``edl3_render``.

Why a cap
---------
``reverse`` and ``areverse`` buffer the whole clip before emitting the first
frame: the decoded frames live in RAM, so a long clip is an OOM, not a slow
render. Refusing over a declared ceiling is cheaper and truthful.

``tpad=stop_mode=clone`` holds a single decoded frame, so a freeze costs one
frame plus the output timeline; the ceiling here guards accidental hour-long
holds, not memory.

Declared constants
------------------
* ``MAX_REVERSE_DURATION_SEC = 30.0`` — reverse/areverse ceiling in seconds.
  Range: ``(0.0, 3600.0]``. Default 30.0.
* ``MAX_FREEZE_DURATION_SEC = 300.0`` — freeze hold ceiling in seconds.
  Range: ``(0.0, 3600.0]``. Default 300.0.
* ``REFERENCE_FPS = 30`` — frame rate used for the memory estimate. Range:
  ``(0, 240]``.
* ``REFERENCE_FRAME_BYTES = 3110400`` — one yuv420p 1080p frame
  (``1920 * 1080 * 3 // 2``). Range: ``(0, 50_000_000]``.

Returned plans
--------------
``reverse_plan`` returns ``video_filters`` (``reverse``), ``audio_filters``
(``areverse`` only when the clip has audio), ``duration`` and a one-line
``memory_note`` stating the buffering consequence.

``freeze_plan`` quantises ``t_freeze`` onto the frame grid: ``frame_index`` is
``round(t_freeze * fps)``, the held frame is ``[frame_index, frame_index + 1)``.
The returned chains are, in order, the untouched head (when the freeze is not at
0), the held frame extended with ``tpad=stop_mode=clone:stop_duration=<hold>``,
and the untouched tail. ``split`` is the fan-out the caller must prepend (every
branch trims the same source pad), ``concat`` glues the branches (``None`` for a
single branch, which cannot happen with a valid freeze), ``post`` carries the
single ``fps`` resample. ``duration`` is the timeline time at which the hold
ends (``frame_start + hold``); the full output is the source plus ``hold``.

Refusal codes
-------------
* ``EXTREMES_DURATION_INVALID`` — reverse duration is not finite or is ``< 0``.
* ``EXTREMES_TIME_INVALID``     — freeze time is not finite or is ``< 0``.
* ``EXTREMES_HOLD_INVALID``     — hold is not finite or is ``<= 0``.
* ``EXTREMES_FPS_INVALID``      — fps is not finite or is ``<= 0``.
* ``REVERSE_TOO_LONG``          — duration exceeds ``MAX_REVERSE_DURATION_SEC``;
  the payload carries the requested duration and the cap.
* ``FREEZE_TOO_LONG``           — hold exceeds ``MAX_FREEZE_DURATION_SEC``; the
  payload carries the requested hold and the cap.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional

__all__ = [
    "ExtremesError",
    "MAX_FREEZE_DURATION_SEC",
    "MAX_REVERSE_DURATION_SEC",
    "REFERENCE_FPS",
    "REFERENCE_FRAME_BYTES",
    "freeze_plan",
    "reverse_plan",
]

#: Reverse/areverse ceiling in seconds. Range: ``(0.0, 3600.0]``.
MAX_REVERSE_DURATION_SEC = 30.0
#: Freeze hold ceiling in seconds. Range: ``(0.0, 3600.0]``.
MAX_FREEZE_DURATION_SEC = 300.0
#: Frame rate behind the reverse memory estimate. Range: ``(0, 240]``.
REFERENCE_FPS = 30
#: One yuv420p 1080p frame in bytes. Range: ``(0, 50_000_000]``.
REFERENCE_FRAME_BYTES = 1920 * 1080 * 3 // 2


class ExtremesError(Exception):
    """An extreme treatment could not be planned, with a stable code."""

    def __init__(
        self,
        code: str,
        detail: str,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.code = code
        self.detail = detail
        self.payload = dict(payload or {})
        super().__init__(f"{code}: {detail}")

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "payload": dict(self.payload)}


def _finite(value: Any, name: str, code: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ExtremesError(code, f"{name} is not a number: {value!r}") from exc
    if not math.isfinite(number):
        raise ExtremesError(code, f"{name} must be finite, got {value!r}")
    return number


def _fmt(value: float, digits: int = 9) -> str:
    """Deterministic number text for a filter graph (no locale, no exponent)."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    if text in ("", "-0"):
        return "0"
    return text


# ------------------------------------------------------------------ reverse


def reverse_plan(duration: float, has_audio: bool) -> Dict[str, Any]:
    """Plan a full reverse, or refuse it against the declared ceiling."""
    length = _finite(duration, "duration", "EXTREMES_DURATION_INVALID")
    if length < 0.0:
        raise ExtremesError(
            "EXTREMES_DURATION_INVALID",
            f"duration must be >= 0, got {length!r}",
            {"duration": length},
        )
    if length > MAX_REVERSE_DURATION_SEC:
        raise ExtremesError(
            "REVERSE_TOO_LONG",
            f"reverse buffers {length:g}s in RAM, over the declared cap of "
            f"{MAX_REVERSE_DURATION_SEC:g}s",
            {"duration": length, "cap": MAX_REVERSE_DURATION_SEC},
        )

    reference_bytes = length * REFERENCE_FPS * REFERENCE_FRAME_BYTES
    memory_note = (
        f"reverse holds all {length:g}s of decoded frames in RAM before the first "
        f"frame is emitted (~{reference_bytes / 1e9:.2f} GB at "
        f"{REFERENCE_FPS} fps 1080p yuv420p)"
    )
    return {
        "video_filters": ["reverse"],
        "audio_filters": ["areverse"] if has_audio else [],
        "duration": length,
        "has_audio": bool(has_audio),
        "memory_note": memory_note,
    }


# ------------------------------------------------------------------- freeze


def freeze_plan(
    t_freeze: float,
    hold_duration: float,
    *,
    fps: float,
) -> Dict[str, Any]:
    """Plan a single-frame hold at ``t_freeze`` for ``hold_duration`` seconds."""
    at = _finite(t_freeze, "t_freeze", "EXTREMES_TIME_INVALID")
    hold = _finite(hold_duration, "hold_duration", "EXTREMES_HOLD_INVALID")
    rate = _finite(fps, "fps", "EXTREMES_FPS_INVALID")
    if at < 0.0:
        raise ExtremesError(
            "EXTREMES_TIME_INVALID",
            f"t_freeze must be >= 0, got {at!r}",
            {"t_freeze": at},
        )
    if hold <= 0.0:
        raise ExtremesError(
            "EXTREMES_HOLD_INVALID",
            f"hold_duration must be > 0, got {hold!r}",
            {"hold_duration": hold},
        )
    if rate <= 0.0:
        raise ExtremesError(
            "EXTREMES_FPS_INVALID", f"fps must be > 0, got {fps!r}", {"fps": fps}
        )
    if hold > MAX_FREEZE_DURATION_SEC:
        raise ExtremesError(
            "FREEZE_TOO_LONG",
            f"freeze holds {hold:g}s, over the declared cap of "
            f"{MAX_FREEZE_DURATION_SEC:g}s",
            {"hold_duration": hold, "cap": MAX_FREEZE_DURATION_SEC},
        )

    frame_index = int(round(at * rate))
    frame_start = frame_index / rate
    frame_end = (frame_index + 1) / rate

    chains: List[str] = []
    roles: List[str] = []
    if frame_start > 0.0:
        chains.append(f"trim=start=0:end={_fmt(frame_start)},setpts=PTS-STARTPTS")
        roles.append("before")
    chains.append(
        f"trim=start={_fmt(frame_start)}:end={_fmt(frame_end)},"
        f"setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={_fmt(hold)}"
    )
    roles.append("freeze")
    chains.append(f"trim=start={_fmt(frame_end)},setpts=PTS-STARTPTS")
    roles.append("after")

    count = len(chains)
    return {
        "video_filters": chains,
        "roles": roles,
        "split": f"split={count}" if count > 1 else None,
        "concat": f"concat=n={count}:v=1:a=0" if count > 1 else None,
        "post": [f"fps={_fmt(rate)}"],
        "duration": frame_start + hold,
        "frame_index": frame_index,
        "freeze_at": frame_start,
        "hold_duration": hold,
        "tail_start": frame_end + hold,
        "source_frame_span": [frame_start, frame_end],
        "fps": rate,
        "audio_note": (
            "video-only plan: the caller must hold or silence the audio across "
            "the freeze instead of letting it drift"
        ),
    }
