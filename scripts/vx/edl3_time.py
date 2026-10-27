"""vx.edl3_time — speed ramps and the declared audio time policy (F2-T05).

Pure planning module: it turns a retiming request into ffmpeg filter fragments
and never touches a file, a subprocess or a renderer. The orchestrator wires the
fragments into ``edl3_render``; this module only does the mathematics.

Why a cascade
-------------
``atempo`` cannot vary over time and accepts only one factor per instance. A
per-instance factor outside its range is rejected by ffmpeg, not silently
clamped. A ramp therefore needs a *chain* of ``atempo`` instances whose product
is the requested ratio, and the renderer must never rediscover that fact.

Constants
---------
Every tunable is declared here with the range it accepts:

* ``MIN_FACTOR = 0.5``  — lowest ``atempo`` factor per instance. Range:
  ``(0.0, 1.0]``; the ffmpeg floor is 0.5, so a smaller value is only allowed
  for builds that raised it, and 0.5 is the declared default.
* ``MAX_FACTOR = 2.0``  — highest ``atempo`` factor per instance. Range:
  ``[1.0, 100.0)``; the ffmpeg ceiling is 2.0, so 2.0 is the declared default.
* ``TOLERANCE = 1e-6``  — accepted error on the cascade product and on unit
  factors. Range: ``(0.0, 1e-3)``.

Audio policy
------------
:class:`AudioPolicy` is the declared three-way choice:

* ``segments`` — preferred. The clip is cut into short segments and the same
  ``atempo`` cascade is applied to each, so the tempo follows the ramp without
  an unstable time-varying audio filter.
* ``silence``  — the audio is muted across the ramp. The mute is declared in the
  returned note instead of being an accidental side effect.
* ``pitch``    — ``asetrate`` + ``aresample`` accept the pitch shift. The
  returned fragment says so: the pitch moves with the ratio and is not
  corrected.

``audio_fragment`` returns a :class:`AudioFragment`, a real ``list[str]`` of
filters that also carries a ``to_dict()`` payload (``policy``, ``pitch_moved``,
``note``), so a caller that only wants filters uses it as a list and a caller
that must declare the policy reads the dict.

Refusal codes
-------------
* ``ATEMPO_RATIO_INVALID`` — ratio is not finite or is ``<= 0``.
* ``ATEMPO_BOUNDS``        — ``min_factor``/``max_factor`` are not finite,
  are ``<= 0``, or ``min_factor > max_factor``.
* ``ATEMPO_CASCADE_UNREACHABLE`` — the rounded cascade does not multiply back
  to the ratio within ``TOLERANCE`` (a build with unusable bounds).
* ``AUDIO_SAMPLE_RATE_INVALID`` — sample rate is not finite or is ``<= 0``.
* ``AUDIO_POLICY_UNKNOWN`` — the policy is not one of the three declared names.
* ``REMAP_SHAPE``          — a keyframe is not a pair or a mapping.
* ``REMAP_EMPTY``          — no keyframes.
* ``REMAP_NONFINITE``      — a time is not finite.
* ``REMAP_NEGATIVE``       — a time is negative.
* ``REMAP_ORDER``          — a time axis goes backwards.
* ``REMAP_DUPLICATE``      — a time repeats exactly (span would be zero).
* ``REMAP_TOO_SHORT``      — a ramp needs at least two keyframes.
* ``RAMP_FPS_INVALID``     — fps is not finite or is ``<= 0``.

``validate()`` never raises: it returns ``[{"code", "detail", "where"}]``.
``sample`` and ``ramp_fragment`` raise :class:`TimeError` on a broken curve.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence

__all__ = [
    "AudioFragment",
    "AudioPolicy",
    "MAX_FACTOR",
    "MIN_FACTOR",
    "RemapCurve",
    "RemapPoint",
    "TOLERANCE",
    "TimeError",
    "atempo_chain",
    "audio_fragment",
    "ramp_fragment",
]

#: Per-instance ``atempo`` floor. Range: ``(0.0, 1.0]``.
MIN_FACTOR = 0.5
#: Per-instance ``atempo`` ceiling. Range: ``[1.0, 100.0)``.
MAX_FACTOR = 2.0
#: Accepted cascade-product error and the unit-factor slack. Range: ``(0.0, 1e-3)``.
TOLERANCE = 1e-6


class TimeError(Exception):
    """A retiming request could not be planned, with a stable code."""

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


class AudioPolicy(str, Enum):
    """The declared audio time policy. One of three names, never inferred."""

    SEGMENTS = "segments"
    SILENCE = "silence"
    PITCH = "pitch"


class AudioFragment(list):
    """A ``list[str]`` of ffmpeg audio filters that also declares its policy.

    ``list`` semantics are deliberate: a caller that only needs filters iterates
    it. ``to_dict()`` is the declaration a report can carry.
    """

    def __init__(
        self,
        filters: Sequence[str],
        *,
        policy: AudioPolicy,
        pitch_moved: bool,
        note: str,
    ) -> None:
        super().__init__(filters)
        self.policy = policy
        self.pitch_moved = pitch_moved
        self.note = note

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filters": list(self),
            "policy": self.policy.value,
            "pitch_moved": self.pitch_moved,
            "note": self.note,
        }


# ------------------------------------------------------------------ helpers


def _finite(value: Any, name: str, code: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TimeError(code, f"{name} is not a number: {value!r}") from exc
    if not math.isfinite(number):
        raise TimeError(code, f"{name} must be finite, got {value!r}")
    return number


def _fmt(value: float, digits: int = 9) -> str:
    """Deterministic number text for a filter graph (no locale, no exponent)."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    if text in ("", "-0"):
        return "0"
    return text


# ------------------------------------------------------------------- atempo


def atempo_chain(
    ratio: float,
    *,
    min_factor: float = MIN_FACTOR,
    max_factor: float = MAX_FACTOR,
) -> List[float]:
    """Shortest cascade of ``atempo`` factors whose product is ``ratio``.

    Greedy from the declared bound: consume ``max_factor`` while the remainder is
    above it, consume ``min_factor`` while the remainder is below it, then emit
    the remainder. ``ratio == 1`` is the single identity factor ``[1.0]``.
    """
    value = _finite(ratio, "ratio", "ATEMPO_RATIO_INVALID")
    low = _finite(min_factor, "min_factor", "ATEMPO_BOUNDS")
    high = _finite(max_factor, "max_factor", "ATEMPO_BOUNDS")
    if low <= 0.0 or high <= 0.0 or low > high:
        raise TimeError(
            "ATEMPO_BOUNDS",
            f"atempo bounds must satisfy 0 < min <= max, got [{low!r}, {high!r}]",
            {"min_factor": low, "max_factor": high},
        )
    if value <= 0.0:
        raise TimeError(
            "ATEMPO_RATIO_INVALID",
            f"ratio must be > 0, got {value!r}",
            {"ratio": value},
        )
    if abs(value - 1.0) <= TOLERANCE:
        return [1.0]

    factors: List[float] = []
    remaining = value
    if remaining > 1.0:
        while remaining > high + TOLERANCE:
            factors.append(high)
            remaining /= high
    else:
        while remaining < low - TOLERANCE:
            factors.append(low)
            remaining /= low

    if abs(remaining - 1.0) > TOLERANCE:
        factors.append(remaining)

    # Round each factor and clamp it back into the declared window. The last
    # factor is the remainder, so it can only overshoot by float epsilon.
    cascade = [
        min(high, max(low, round(factor, 12))) for factor in factors if factor > 0.0
    ]
    product = 1.0
    for factor in cascade:
        product *= factor
    if cascade and abs(product - value) > TOLERANCE:
        raise TimeError(
            "ATEMPO_CASCADE_UNREACHABLE",
            f"cascade {cascade!r} multiplies to {product!r}, not {value!r}",
            {"ratio": value, "factors": cascade, "product": product},
        )
    return cascade


# ------------------------------------------------------------- audio policy


def audio_fragment(
    policy: AudioPolicy,
    ratio: float,
    *,
    sample_rate: int = 48000,
) -> AudioFragment:
    """The ffmpeg audio filters for ``policy`` at ``ratio``, with its declaration."""
    if isinstance(policy, AudioPolicy):
        chosen = policy
    else:
        try:
            chosen = AudioPolicy(str(policy))
        except ValueError as exc:
            raise TimeError(
                "AUDIO_POLICY_UNKNOWN",
                f"policy must be one of segments/silence/pitch, got {policy!r}",
            ) from exc

    value = _finite(ratio, "ratio", "ATEMPO_RATIO_INVALID")
    if value <= 0.0:
        raise TimeError(
            "ATEMPO_RATIO_INVALID",
            f"ratio must be > 0, got {value!r}",
            {"ratio": value},
        )
    rate = _finite(sample_rate, "sample_rate", "AUDIO_SAMPLE_RATE_INVALID")
    if rate <= 0.0 or rate != int(rate):
        raise TimeError(
            "AUDIO_SAMPLE_RATE_INVALID",
            f"sample_rate must be a positive integer, got {sample_rate!r}",
            {"sample_rate": sample_rate},
        )
    rate_hz = int(rate)

    if chosen is AudioPolicy.SEGMENTS:
        cascade = atempo_chain(value)
        filters = [f"atempo={_fmt(factor)}" for factor in cascade]
        note = (
            f"audio is cut into short segments, one atempo per segment, applied "
            f"{len(cascade)} time(s) to reach {_fmt(value)}x; pitch is preserved"
        )
        return AudioFragment(filters, policy=chosen, pitch_moved=False, note=note)

    if chosen is AudioPolicy.SILENCE:
        return AudioFragment(
            ["volume=0"],
            policy=chosen,
            pitch_moved=False,
            note=(
                "audio is muted for the whole ramp and declared as such: no "
                "atempo, no resampling, no hidden audio"
            ),
        )

    shifted = max(1, int(round(rate_hz * value)))
    filters = [f"asetrate={shifted},aresample={rate_hz}"]
    note = (
        f"pitch moves with the ratio: asetrate shifts the sample rate to "
        f"{shifted} Hz and aresample returns to {rate_hz} Hz, so the pitch is "
        f"{_fmt(value)}x and is NOT corrected"
    )
    return AudioFragment(filters, policy=chosen, pitch_moved=True, note=note)


# -------------------------------------------------------------- remap curve


class RemapPoint:
    """One keyframe: a timeline time mapped onto a source time."""

    __slots__ = ("t_timeline", "t_source")

    def __init__(self, t_timeline: float, t_source: float) -> None:
        self.t_timeline = float(t_timeline)
        self.t_source = float(t_source)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RemapPoint):
            return NotImplemented
        return (self.t_timeline, self.t_source) == (other.t_timeline, other.t_source)

    def __repr__(self) -> str:
        return f"RemapPoint({self.t_timeline!r}, {self.t_source!r})"

    def to_dict(self) -> Dict[str, float]:
        return {"t_timeline": self.t_timeline, "t_source": self.t_source}


def _coerce_point(raw: Any, where: str) -> RemapPoint:
    if isinstance(raw, RemapPoint):
        return RemapPoint(raw.t_timeline, raw.t_source)
    if isinstance(raw, Mapping):
        if "t_timeline" in raw and "t_source" in raw:
            return RemapPoint(raw["t_timeline"], raw["t_source"])
        if "timeline" in raw and "source" in raw:
            return RemapPoint(raw["timeline"], raw["source"])
        raise TimeError(
            "REMAP_SHAPE",
            f"{where}: mapping needs t_timeline/t_source, got {raw!r}",
        )
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        if len(raw) == 2:
            return RemapPoint(raw[0], raw[1])
        raise TimeError("REMAP_SHAPE", f"{where}: a pair has 2 values, got {len(raw)}")
    raise TimeError("REMAP_SHAPE", f"{where}: not a pair or mapping: {raw!r}")


class RemapCurve:
    """Keyframes ``(t_timeline, t_source)``, sampled linearly.

    Construction is permissive so ``validate()`` can report instead of throwing;
    ``sample`` and ``ramp_fragment`` refuse a broken curve.
    """

    def __init__(self, points: Sequence[Any]) -> None:
        if isinstance(points, (str, bytes)) or not isinstance(points, Sequence):
            raise TimeError("REMAP_SHAPE", f"points must be a sequence, got {points!r}")
        self.points: List[RemapPoint] = [
            _coerce_point(raw, f"point {index}") for index, raw in enumerate(points)
        ]

    # -- properties -------------------------------------------------------
    @property
    def duration_source(self) -> float:
        if len(self.points) < 2:
            return 0.0
        return self.points[-1].t_source - self.points[0].t_source

    @property
    def duration_timeline(self) -> float:
        if len(self.points) < 2:
            return 0.0
        return self.points[-1].t_timeline - self.points[0].t_timeline

    # -- validation -------------------------------------------------------
    def validate(self) -> List[Dict[str, str]]:
        """Return every problem found; never raises."""
        problems: List[Dict[str, str]] = []
        if not self.points:
            problems.append(
                {"code": "REMAP_EMPTY", "detail": "no keyframes", "where": "curve"}
            )
            return problems
        for index, point in enumerate(self.points):
            where = f"point {index}"
            if not math.isfinite(point.t_timeline) or not math.isfinite(point.t_source):
                problems.append(
                    {
                        "code": "REMAP_NONFINITE",
                        "detail": f"{point!r} is not finite",
                        "where": where,
                    }
                )
                continue
            if point.t_timeline < 0.0 or point.t_source < 0.0:
                problems.append(
                    {
                        "code": "REMAP_NEGATIVE",
                        "detail": f"{point!r} is negative",
                        "where": where,
                    }
                )
        for index in range(1, len(self.points)):
            left = self.points[index - 1]
            right = self.points[index]
            if not (
                math.isfinite(left.t_timeline)
                and math.isfinite(right.t_timeline)
                and math.isfinite(left.t_source)
                and math.isfinite(right.t_source)
            ):
                continue
            where = f"point {index}"
            if right.t_timeline == left.t_timeline or right.t_source == left.t_source:
                problems.append(
                    {
                        "code": "REMAP_DUPLICATE",
                        "detail": (
                            f"{right!r} repeats a time of {left!r}; a segment "
                            f"would have zero span"
                        ),
                        "where": where,
                    }
                )
            elif right.t_timeline < left.t_timeline or right.t_source < left.t_source:
                problems.append(
                    {
                        "code": "REMAP_ORDER",
                        "detail": f"{right!r} does not advance past {left!r}",
                        "where": where,
                    }
                )
        return problems

    def _trusted(self) -> List[RemapPoint]:
        problems = self.validate()
        if problems:
            first = problems[0]
            raise TimeError(first["code"], f"{first['where']}: {first['detail']}")
        return self.points

    # -- sampling ---------------------------------------------------------
    def sample(self, t: float) -> float:
        """Source time at timeline ``t``; linear between points, clamped outside."""
        points = self._trusted()
        at = _finite(t, "t", "REMAP_SHAPE")
        if at <= points[0].t_timeline:
            return points[0].t_source
        if at >= points[-1].t_timeline:
            return points[-1].t_source
        for left, right in zip(points, points[1:]):
            if left.t_timeline <= at <= right.t_timeline:
                span = right.t_timeline - left.t_timeline
                if span <= 0.0:
                    return right.t_source
                x = (at - left.t_timeline) / span
                return left.t_source + (right.t_source - left.t_source) * x
        return points[-1].t_source

    def to_dict(self) -> List[Dict[str, float]]:
        return [point.to_dict() for point in self.points]


# ------------------------------------------------------------------- ramp


def ramp_fragment(curve: RemapCurve, *, fps: float) -> Dict[str, Any]:
    """Per-segment ``trim``+``setpts`` filters for ``curve`` at ``fps``.

    Each segment consumes ``source_span`` of the source and emits
    ``timeline_span`` of the timeline; the ``setpts`` divisor is exactly that
    ratio, so the caller never recomputes it. ``video_filters`` holds one chain
    per segment, ``split`` is the fan-out the caller must prepend when there is
    more than one segment, ``concat`` glues them (``None`` for a single segment)
    and ``post`` carries the single ``fps`` resample.
    """
    rate = _finite(fps, "fps", "RAMP_FPS_INVALID")
    if rate <= 0.0:
        raise TimeError(
            "RAMP_FPS_INVALID", f"fps must be > 0, got {fps!r}", {"fps": fps}
        )
    points = curve._trusted()
    if len(points) < 2:
        raise TimeError(
            "REMAP_TOO_SHORT",
            f"a ramp needs at least 2 keyframes, got {len(points)}",
            {"points": len(points)},
        )

    segments: List[Dict[str, Any]] = []
    chains: List[str] = []
    for left, right in zip(points, points[1:]):
        timeline_span = right.t_timeline - left.t_timeline
        source_span = right.t_source - left.t_source
        ratio = source_span / timeline_span
        chain = (
            f"trim=start={_fmt(left.t_source)}:end={_fmt(right.t_source)},"
            f"setpts=(PTS-STARTPTS)/{_fmt(ratio)}"
        )
        chains.append(chain)
        segments.append(
            {
                "timeline_start": left.t_timeline,
                "timeline_end": right.t_timeline,
                "source_start": left.t_source,
                "source_end": right.t_source,
                "timeline_span": timeline_span,
                "source_span": source_span,
                "ratio": ratio,
                "video_filter": chain,
            }
        )

    count = len(segments)
    duration = curve.duration_timeline
    return {
        "video_filters": chains,
        "split": f"split={count}" if count > 1 else None,
        "concat": f"concat=n={count}:v=1:a=0" if count > 1 else None,
        "post": [f"fps={_fmt(rate)}"],
        "duration": duration,
        "frames": int(round(duration * rate)),
        "segments": count,
        "fps": rate,
        "source_duration": curve.duration_source,
        "ratio": (curve.duration_source / duration if duration > 0.0 else 0.0),
        "plan": segments,
    }
