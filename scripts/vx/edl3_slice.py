"""vx.edl3_slice — cut a placed ``vx.edl3`` document into compileable pieces.

A chunk boundary is a boundary between two placed layer-0 clips, so a chunk never
cuts a clip: no effect, keyframe or transition is re-timed, and every piece is a
document the ordinary compiler already knows how to read. This module owns that
geometry and nothing else — it runs no ffmpeg and writes no file.

Pure by contract: :func:`plan_windows` and :func:`document` only read the
document, so the refusals below are raised before a single frame is encoded.

Refused, never silently degraded: a document this module cannot slice *exactly*
is refused with ``CHUNK_UNSUPPORTED`` — ducking (one sidechain envelope over the
whole dialogue bus), nested sequences (rendered once for the whole document),
generator captures (materialized once), text layers and captions (not sliced
here, so chunking would drop them), a declared ``profile_ref`` (its structure is
measured against the whole edit, and a piece is a fragment), an overlay without
placement or straddling a boundary (slicing it would re-time its keyframes), a
layer 0 that does not tile, or fewer layer-0 clips than chunks.

Supported, with the same sum as the master: an audio bus is sliced at the chunk
boundary (:func:`slice_audio`) and a layer-0 clip without ``dst_in``/``dst_out``
gets the concatenation cursor as its placement (:func:`placements`).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Mapping, NoReturn, Sequence, Tuple

from . import stagecache
from .edl3 import Timeline3


def canonical(value: Any) -> str:
    """Stable JSON used for hashing: sorted keys, no insignificant spaces."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _refuse(detail: str, where: str) -> NoReturn:
    """Refuse with the compiler's own exception type and a declared code."""
    from .edl3_render import CompileError  # noqa: PLC0415 - avoids the cycle

    raise CompileError("CHUNK_UNSUPPORTED", detail, where)


def placements(timeline: Timeline3) -> Dict[int, Tuple[float, float]]:
    """Document index -> ``(dst_in, dst_out)``, derived when the document omits it.

    A clip without placement means "right after the previous layer-0 clip", the
    same concatenation cursor :attr:`Timeline3.duration` uses. Deriving it here
    is what lets ``--live`` accept a document that never wrote ``dst_in`` /
    ``dst_out``: a chunk boundary is still a clip boundary, and the times the
    sub-document receives are the ones the single-pass graph would have used.
    """
    cursor = 0.0
    found: Dict[int, Tuple[float, float]] = {}
    for index, clip in enumerate(timeline.clips):
        if clip.dst_in is None or clip.dst_out is None:
            start = cursor
            end = start + max(0.0, float(clip.timeline_duration))
        else:
            start, end = float(clip.dst_in), float(clip.dst_out)
        found[index] = (start, end)
        if int(clip.layer) == 0:
            cursor = max(cursor, end)
    return found


def plan_windows(timeline: Timeline3, chunks: int) -> List[Dict[str, Any]]:
    """Split ``timeline`` into ``chunks`` contiguous runs of layer-0 clips.

    A window is ``{"index", "t0", "t1", "clips"}``, where ``clips`` are the
    document indices it renders, in document order.
    """
    count = int(chunks)
    if count < 2:
        raise ValueError("chunked rendering needs at least two chunks")
    _refuse_unsliceable(timeline)
    base = [
        (index, clip) for index, clip in enumerate(timeline.clips) if clip.layer == 0
    ]
    if len(base) < count:
        _refuse(
            f"{count} chunks cannot be placed on {len(base)} layer-0 clip(s): a "
            "boundary is a clip boundary, so a chunk can never cut a clip",
            "clips",
        )
    times = placements(timeline)
    edges = [round(position * len(base) / count) for position in range(count + 1)]
    windows: List[Dict[str, Any]] = []
    for position in range(count):
        run = base[edges[position] : edges[position + 1]]
        windows.append(
            {
                "index": position,
                "t0": times[run[0][0]][0],
                "t1": times[run[-1][0]][1],
                "clips": [index for index, _clip in run],
            }
        )
    _refuse_broken_tiling(timeline, windows)
    _refuse_straddling_overlays(timeline, windows)
    return windows


def _refuse_unsliceable(timeline: Timeline3) -> None:
    """Everything this module cannot cut at a clip boundary, named one by one."""
    if timeline.ducking is not None:
        _refuse(
            "the document declares ducking: its sidechain envelope is measured "
            "over the whole dialogue bus, and a chunk boundary would restart the "
            "music bed against a different envelope. Drop ducking or render the "
            "document whole",
            "ducking",
        )
    if timeline.sequences:
        _refuse(
            "the document declares nested sequences: an inner timeline is "
            "rendered once for the whole document",
            "sequences",
        )
    if timeline.text_layers or timeline.captions:
        _refuse(
            "the document carries text layers or caption tracks, which this "
            "module does not slice: chunking would drop them",
            "text_layers" if timeline.text_layers else "captions",
        )
    if timeline.profile_ref is not None:
        _refuse(
            f"profile_ref {timeline.profile_ref!r} is measured against the "
            "structure of the whole edit, and a chunk is a fragment: refusing "
            "rather than judging (or dropping) the declaration on a slice",
            "profile_ref",
        )
    for index, clip in enumerate(timeline.clips):
        if clip.generator_declaration is not None:
            _refuse(
                f"clip {clip.id!r} is a generator clip: its capture is "
                "materialized once for the whole document",
                f"clips[{index}].generator",
            )
        if int(clip.layer) != 0 and (clip.dst_in is None or clip.dst_out is None):
            _refuse(
                f"overlay clip {clip.id!r} declares no dst_in/dst_out: a layer "
                "above zero is only visible inside its window, so the slice "
                "cannot derive where to place it",
                f"clips[{index}]",
            )


def _refuse_broken_tiling(
    timeline: Timeline3, windows: Sequence[Mapping[str, Any]]
) -> None:
    """The windows must cover the placed timeline exactly once, in order."""
    cursor = 0.0
    for window in windows:
        if abs(float(window["t0"]) - cursor) > 1e-6:
            _refuse(
                f"layer 0 does not tile the timeline: chunk {window['index']} "
                f"starts at {float(window['t0']):.6f} while the previous chunk "
                f"ends at {cursor:.6f}",
                "clips",
            )
        cursor = float(window["t1"])
    if abs(cursor - float(timeline.duration)) > 1e-6:
        _refuse(
            f"layer 0 covers {cursor:.6f}s but the document declares "
            f"{float(timeline.duration):.6f}s",
            "clips",
        )


def _refuse_straddling_overlays(
    timeline: Timeline3, windows: Sequence[Mapping[str, Any]]
) -> None:
    """An overlay must fall inside exactly one window, so its timing is intact."""
    for index, clip in enumerate(timeline.clips):
        if clip.layer == 0:
            continue
        start = float(clip.dst_in)
        end = float(clip.dst_out)
        inside = [
            window
            for window in windows
            if start >= float(window["t0"]) - 1e-6
            and end <= float(window["t1"]) + 1e-6
        ]
        if len(inside) != 1:
            _refuse(
                f"overlay clip {clip.id!r} spans {start:.3f}s..{end:.3f}s and "
                f"crosses a chunk boundary ({len(inside)} window(s) contain it): "
                "slicing it would re-time its keyframes",
                f"clips[{index}]",
            )


def slice_audio(
    timeline: Timeline3, start: float, end: float
) -> List[Dict[str, Any]]:
    """The audio bus cut to ``[start, end)`` and re-based to zero.

    Each declared segment contributes the part that overlaps the window, with
    its source span advanced by the same offset: the pieces of one segment then
    concatenate back into the segment the master mixes. A track with nothing
    inside the window is left out, because an empty input is not a track.
    """
    base = float(start)
    keep: List[Dict[str, Any]] = []
    for track in timeline.audio:
        clips: List[Dict[str, Any]] = []
        for clip in track.clips:
            first = max(float(clip.dst_in), float(start))
            last = min(float(clip.dst_out), float(end))
            if last - first <= 1e-9:
                continue
            offset = first - float(clip.dst_in)
            entry: Dict[str, Any] = {
                "source": clip.source,
                "src_in": float(clip.src_in) + offset,
                "src_out": float(clip.src_in) + offset + (last - first),
                "dst_in": first - base,
                "dst_out": last - base,
                "gain_db": clip.gain_db,
            }
            # A fade survives only where the slice edge is also an edge of the
            # declared segment; an interior cut is a chunk boundary, not an end.
            if first - float(clip.dst_in) > 1e-9:
                entry["fade_in"] = False
            elif clip.fade_in is not None:
                entry["fade_in"] = clip.fade_in
            if float(clip.dst_out) - last > 1e-9:
                entry["fade_out"] = False
            elif clip.fade_out is not None:
                entry["fade_out"] = clip.fade_out
            clips.append(entry)
        if clips:
            keep.append({"role": track.role, "clips": clips})
    return keep


def document(timeline: Timeline3, window: Mapping[str, Any]) -> Dict[str, Any]:
    """The sub-document one window renders, with its clock re-based to zero.

    Placement the document omitted is derived here too (see :func:`placements`),
    and an audio bus is sliced with :func:`slice_audio`: the sum of the pieces is
    the bus the master would have mixed.
    """
    start = float(window["t0"])
    keep = set(window["clips"])
    times = placements(timeline)
    clips: List[Dict[str, Any]] = []
    for index, clip in enumerate(timeline.clips):
        if clip.layer == 0 and index not in keep:
            continue
        data = clip.to_dict()
        first, last = times[index]
        data["dst_in"] = first - start
        data["dst_out"] = last - start
        clips.append(data)
    sliced: Dict[str, Any] = {
        "version": timeline.version,
        "fps": timeline.fps,
        "size": [timeline.width, timeline.height],
        "clips": clips,
    }
    audio = slice_audio(timeline, start, float(window["t1"]))
    if audio:
        sliced["audio"] = audio
    return sliced


def source_hashes(
    clips: Sequence[Mapping[str, Any]], memo: Dict[str, str]
) -> Dict[str, str]:
    """Content hash of every source the sub-document reads, each file read once.

    ``memo`` is shared across windows so a file is read once per render, and it
    is the caller's: this module keeps no state between calls.
    """
    found: Dict[str, str] = {}
    for clip in clips:
        path = str(clip.get("source") or "")
        if not path:
            continue
        if path not in memo:
            memo[path] = stagecache.file_sha256(path)
        found[path] = memo[path]
    return found
