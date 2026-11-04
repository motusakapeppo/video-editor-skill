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
boundary (:func:`slice_audio`), a layer-0 clip without ``dst_in``/``dst_out``
gets the concatenation cursor as its placement (:func:`placements`), and a
document with dissolves is cut *between* joins instead of through them. A
dissolve is an overlap between two consecutive layer-0 clips, so a boundary
placed where a crossfade enters would either duplicate the overlap or orphan
the transition; :func:`plan_windows` moves such a boundary to the nearest edge
that is not a join, and says so when it has to drop a chunk to do it.

When a document is sequential (its layer-0 clips never declare ``dst_in`` /
``dst_out``) the piece keeps that form: the times are left to the compiler's
cursor, exactly as :func:`placements` derives them, so a sub-document with a
dissolve stays a legal sequential document instead of becoming an absolutely
placed one that the validator refuses.
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


def _join_seconds(timeline: Timeline3) -> Dict[int, float]:
    """Base position -> dissolve seconds, from the document that declares them."""
    joins: Dict[int, float] = {}
    getter = getattr(timeline, "crossfade_joins", None)
    if callable(getter):
        for position, seconds in getter():
            joins[int(position)] = float(seconds)
    return joins


def placements(timeline: Timeline3) -> Dict[int, Tuple[float, float]]:
    """Document index -> ``(dst_in, dst_out)``, derived when the document omits it.

    A clip without placement means "right after the previous layer-0 clip", the
    same concatenation cursor :attr:`Timeline3.duration` uses, *minus* the
    dissolve that the clip declares: a crossfade is an overlap, so the entering
    clip starts ``seconds`` before the previous one ends. Deriving it here is
    what lets ``--live`` accept a document that never wrote ``dst_in`` /
    ``dst_out``: a chunk boundary is still a clip boundary, and the times the
    sub-document receives are the ones the single-pass graph would have used.
    """
    cursor = 0.0
    position = -1
    joins = _join_seconds(timeline)
    found: Dict[int, Tuple[float, float]] = {}
    for index, clip in enumerate(timeline.clips):
        base = int(clip.layer) == 0
        if base:
            position += 1
        if clip.dst_in is None or clip.dst_out is None:
            overlap = joins.get(position, 0.0) if base else 0.0
            start = max(0.0, cursor - overlap)
            end = start + max(0.0, float(clip.timeline_duration))
        else:
            start, end = float(clip.dst_in), float(clip.dst_out)
        found[index] = (start, end)
        if base:
            cursor = max(cursor, end)
    return found


def _choose_edges(
    n: int, count: int, forbidden: set
) -> Tuple[List[int], List[int]]:
    """Interior boundaries for ``count`` chunks, and the joins they duplicate.

    Each ideal edge (an even split of the base clips) is moved to the nearest
    edge that is not the *start* of a dissolve. A dissolve occupies the tail of
    the previous clip and the head of the entering one, so cutting at the
    entering clip's start would duplicate the overlap and leave the transition
    pointing at a clip the piece does not carry. Moving the boundary one clip
    earlier or later keeps the dissolve whole inside a single piece.

    When the document has no clean edge left (every join dissolves) the split
    falls back to the even edges and returns them in ``duplicated``: the piece
    starts on the entering clip, the overlap is replayed at the boundary and the
    caller declares it instead of dropping content.
    """
    chosen: List[int] = []
    for step in range(1, count):
        remaining = count - step
        low = (chosen[-1] if chosen else 0) + 1
        high = n - remaining
        ideal = round(step * n / count)
        options = [edge for edge in range(low, high + 1) if edge not in forbidden]
        if not options:
            return _even_edges(n, count, forbidden)
        chosen.append(min(options, key=lambda edge: (abs(edge - ideal), edge)))
    return chosen, []


def _even_edges(n: int, count: int, forbidden: set) -> Tuple[List[int], List[int]]:
    """The even split when it cannot avoid every dissolve: those joins repeat."""
    chosen: List[int] = []
    for step in range(1, count):
        remaining = count - step
        low = (chosen[-1] if chosen else 0) + 1
        high = n - remaining
        ideal = round(step * n / count)
        options = list(range(low, high + 1))
        if not options:
            return chosen, [edge for edge in chosen if edge in forbidden]
        chosen.append(min(options, key=lambda edge: (abs(edge - ideal), edge)))
    return chosen, [edge for edge in chosen if edge in forbidden]


def _refuse_cut_join(
    timeline: Timeline3, base: Sequence[Tuple[int, Any]], joins: Mapping[int, float]
) -> NoReturn:
    """The split must land on a join and cannot: name it and how to move it."""
    position = sorted(joins)[0] if joins else 0
    entering = base[position][1] if 0 < position < len(base) else None
    leaving = base[position - 1][1] if 0 < position <= len(base) else None
    where = (
        f"between {leaving.id!r} and {entering.id!r}"
        if entering is not None and leaving is not None
        else f"at base position {position}"
    )
    _refuse(
        f"the only available chunk boundary falls on the {joins.get(position, 0.0):.3f}s "
        f"dissolve {where}, and this document also carries overlays: repeating the "
        "overlap would re-time their keyframes. Move the join (add a clip between "
        "them), or render the document whole instead of in chunks",
        "transitions",
    )


def plan_windows(timeline: Timeline3, chunks: int) -> List[Dict[str, Any]]:
    """Split ``timeline`` into contiguous runs of layer-0 clips.

    A window is ``{"index", "t0", "t1", "clips"}``, where ``clips`` are the
    document indices it renders, in document order. Dissolves are honoured: a
    boundary is moved off a join; when the document leaves no clean edge the
    split lands on the join and the window declares ``overlap_in``, the seconds
    that will be replayed (see :func:`_choose_edges`).
    """
    count = int(chunks)
    if count < 2:
        raise ValueError("chunked rendering needs at least two chunks")
    _refuse_unsliceable(timeline)
    base = [
        (index, clip) for index, clip in enumerate(timeline.clips) if clip.layer == 0
    ]
    if len(base) < count:
        joins = _join_seconds(timeline)
        extra = (
            " (the document also declares "
            f"{len(joins)} dissolve(s), which can only move a boundary further)"
            if joins
            else ""
        )
        _refuse(
            f"{count} chunks cannot be placed on {len(base)} layer-0 clip(s): a "
            "boundary is a clip boundary, so a chunk can never cut a clip"
            + extra,
            "clips",
        )
    times = placements(timeline)
    joins = _join_seconds(timeline)
    edges, duplicated = _choose_edges(len(base), count, set(joins))
    if duplicated and any(clip.layer != 0 for clip in timeline.clips):
        _refuse_cut_join(timeline, base, joins)
    bounds = [0, *edges, len(base)]
    windows: List[Dict[str, Any]] = []
    for position in range(len(bounds) - 1):
        run = base[bounds[position] : bounds[position + 1]]
        boundary = bounds[position]
        windows.append(
            {
                "index": position,
                "t0": times[run[0][0]][0],
                "t1": times[run[-1][0]][1],
                "clips": [index for index, _clip in run],
                # Seconds replayed because the split landed on a dissolve.
                "overlap_in": joins.get(boundary, 0.0) if position else 0.0,
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
    """The windows must cover the placed timeline exactly once, in order.

    A boundary that had to land on a dissolve replays ``overlap_in`` seconds, so
    the next window starts that much earlier: the duplication is declared in the
    window, not silently hidden.
    """
    cursor = 0.0
    for window in windows:
        overlap = float(window.get("overlap_in") or 0.0)
        if abs(float(window["t0"]) - (cursor - overlap)) > 1e-6:
            _refuse(
                f"layer 0 does not tile the timeline: chunk {window['index']} "
                f"starts at {float(window['t0']):.6f} while the previous chunk "
                f"ends at {cursor:.6f}"
                + (f" minus the replayed {overlap:.6f}s dissolve" if overlap else ""),
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


def _audio_range(timeline: Timeline3, window: Mapping[str, Any]) -> Tuple[float, float]:
    """The declared-time range of the audio bus a piece replays.

    Video time and audio time differ once a document dissolves: ``xfade``
    shortens the picture, while the audio bus is written on its own declared
    clock. A piece that starts after some joins must read the bus from
    ``start + overlap`` (the time the master's audio really reaches), and a
    piece that ends before a join must not read the entering clip's head at all.
    A join whose entering clip *is* in the piece is left in: the sub-document
    still declares it, so the compiler shortens the piece's audio by the same
    overlap. Without dissolves both offsets are zero and this is the identity.
    """
    times = placements(timeline)
    joins = _join_seconds(timeline)
    if not joins:
        return float(window["t0"]), float(window["t1"])
    keep = set(window["clips"])
    start = float(window["t0"])
    end = float(window["t1"])
    positions = {
        index: position
        for position, (index, _clip) in enumerate(
            (item for item in enumerate(timeline.clips) if item[1].layer == 0)
        )
    }

    def cumulative(moment: float) -> float:
        return sum(
            seconds
            for position, seconds in joins.items()
            if times[position][0] <= moment + 1e-9
        )

    entering_kept = {
        positions[index] for index in keep if index in positions
    }
    first = start + cumulative(start)
    last = end + cumulative(end)
    for position, seconds in joins.items():
        if position in entering_kept:
            continue
        join_start = times[position][0]
        if start - 1e-9 <= join_start < end - 1e-9:
            # The entering clip is not in this piece: the overlap at this join is
            # not replayed, so the bus must not read its head.
            last -= seconds
    return first, last


def document(timeline: Timeline3, window: Mapping[str, Any]) -> Dict[str, Any]:
    """The sub-document one window renders, with its clock re-based to zero.

    Placement the document omitted stays omitted: a sequential layer-0 clip is
    written without ``dst_in``/``dst_out`` so the compiler's cursor places it
    (the same rule :func:`placements` follows), which is what keeps a dissolve
    legal inside a piece — an absolutely placed clip cannot carry one. A clip
    that did declare its window is re-based by ``window["t0"]``; an overlay
    always is, because its window is the only place it is visible. An audio bus
    is sliced with :func:`slice_audio`: the sum of the pieces is the bus the
    master would have mixed.
    """
    start = float(window["t0"])
    keep = set(window["clips"])
    times = placements(timeline)
    clips: List[Dict[str, Any]] = []
    first_base = True
    for index, clip in enumerate(timeline.clips):
        if clip.layer == 0 and index not in keep:
            continue
        data = clip.to_dict()
        first, last = times[index]
        if int(clip.layer) == 0 and clip.dst_in is None and clip.dst_out is None:
            # Sequential document: leave the placement to the cursor, exactly as
            # the whole document would have (a crossfade needs this form).
            data["dst_in"] = None
            data["dst_out"] = None
        else:
            data["dst_in"] = first - start
            data["dst_out"] = last - start
        if int(clip.layer) == 0:
            transition = clip.transition_in
            if (
                first_base
                and transition is not None
                and transition.kind == "crossfade"
            ):
                # The piece starts on this clip: the clip it dissolved from is
                # not in it, so the transition has nothing to cross into. The
                # boundary that produced this window replayed the overlap, and
                # the report declares it.
                data["transition_in"] = None
            first_base = False
        clips.append(data)
    sliced: Dict[str, Any] = {
        "version": timeline.version,
        "fps": timeline.fps,
        "size": [timeline.width, timeline.height],
        "clips": clips,
    }
    audio_start, audio_end = _audio_range(timeline, window)
    audio = slice_audio(timeline, audio_start, audio_end)
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
