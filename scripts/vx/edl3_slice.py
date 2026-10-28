"""vx.edl3_slice — cut a placed ``vx.edl3`` document into compileable pieces.

A chunk boundary is a boundary between two placed layer-0 clips, so a chunk never
cuts a clip: no effect, keyframe or transition is re-timed, and every piece is a
document the ordinary compiler already knows how to read. This module owns that
geometry and nothing else — it runs no ffmpeg and writes no file.

Pure by contract: :func:`plan_windows` and :func:`document` only read the
document, so the refusals below are raised before a single frame is encoded.

Refused, never silently degraded: a document this module cannot slice *exactly*
is refused with ``CHUNK_UNSUPPORTED`` — an audio bus (mixed over the whole
timeline), nested sequences (rendered once for the whole document), generator
captures (materialized once), text layers and captions (not sliced here, so
chunking would drop them), a declared ``profile_ref`` (its structure is measured
against the whole edit, and a piece is a fragment), a clip without declared
placement, an overlay that straddles a boundary (slicing it would re-time its
keyframes), a layer 0 that does not tile, or fewer layer-0 clips than chunks.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Mapping, NoReturn, Sequence

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
    edges = [round(position * len(base) / count) for position in range(count + 1)]
    windows: List[Dict[str, Any]] = []
    for position in range(count):
        run = base[edges[position] : edges[position + 1]]
        windows.append(
            {
                "index": position,
                "t0": float(run[0][1].dst_in),
                "t1": float(run[-1][1].dst_out),
                "clips": [index for index, _clip in run],
            }
        )
    _refuse_broken_tiling(timeline, windows)
    _refuse_straddling_overlays(timeline, windows)
    return windows


def _refuse_unsliceable(timeline: Timeline3) -> None:
    """Everything this module cannot cut at a clip boundary, named one by one."""
    if timeline.audio:
        _refuse(
            "the document carries an audio bus: it is mixed over the whole "
            "timeline, and this module does not slice audio",
            "audio",
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
        if clip.dst_in is None or clip.dst_out is None:
            _refuse(
                f"clip {clip.id!r} declares no dst_in/dst_out: a chunk boundary "
                "is a boundary between placed layer-0 clips",
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


def document(timeline: Timeline3, window: Mapping[str, Any]) -> Dict[str, Any]:
    """The sub-document one window renders, with its clock re-based to zero."""
    start = float(window["t0"])
    keep = set(window["clips"])
    clips: List[Dict[str, Any]] = []
    for index, clip in enumerate(timeline.clips):
        if clip.layer == 0 and index not in keep:
            continue
        data = clip.to_dict()
        data["dst_in"] = float(clip.dst_in) - start
        data["dst_out"] = float(clip.dst_out) - start
        clips.append(data)
    return {
        "version": timeline.version,
        "fps": timeline.fps,
        "size": [timeline.width, timeline.height],
        "clips": clips,
    }


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
