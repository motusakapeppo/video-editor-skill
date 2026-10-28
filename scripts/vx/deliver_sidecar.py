"""vx.deliver_sidecar — the artifacts beside a deliverable that are not the picture.

Soft subtitles and chapter marks leave the house as their own files: they are
never muxed into the deliverable unless a target says so (no target does), and
nothing is invented when the document declares nothing. Pure text production --
no ffmpeg, no filesystem -- so every rule here is cheap to test on its own.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Sequence, Tuple

__all__ = ["cues", "document_cues", "srt", "chapter_marks", "ffmetadata"]

Cue = Tuple[float, float, str]
Mark = Tuple[float, float, str]


def _value(holder: Any, key: str) -> Any:
    if holder is None:
        return None
    return holder.get(key) if isinstance(holder, Mapping) else getattr(holder, key, None)


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def cues(items: Any) -> List[Cue]:
    """Normalise caption cues from objects, dicts or (start, end, text) triples."""
    out: List[Cue] = []
    for item in items or ():
        if isinstance(item, (tuple, list)) and len(item) == 3:
            start, end, text = _float(item[0]), _float(item[1]), str(item[2])
        else:
            start = _value(item, "time_in")
            end = _value(item, "time_out")
            start = _value(item, "in") if start is None else start
            end = _value(item, "out") if end is None else end
            text = str(_value(item, "text") or "")
            start, end = _float(start), _float(end)
        if end > start and text.strip():
            out.append((start, end, text.strip()))
    return sorted(out)


def document_cues(document: Any) -> List[Cue]:
    """Every caption cue the document carries, across all of its tracks."""
    out: List[Cue] = []
    for track in _value(document, "captions") or ():
        out.extend(cues(_value(track, "clips") or ()))
    return sorted(out)


def _stamp(seconds: float) -> str:
    whole = int(round(max(0.0, seconds) * 1000))
    hours, whole = divmod(whole, 3600000)
    minutes, whole = divmod(whole, 60000)
    return f"{hours:02d}:{minutes:02d}:{whole // 1000:02d},{whole % 1000:03d}"


def srt(rows: Sequence[Cue]) -> str:
    """A SubRip file: one numbered block per cue, comma milliseconds."""
    return "\n".join(
        f"{index}\n{_stamp(start)} --> {_stamp(end)}\n{text}\n"
        for index, (start, end, text) in enumerate(rows, 1)
    )


def _declared(ext: Any, name: str) -> Any:
    """``ext[name]``, or the round-tripped ``x-`` form a document actually keeps."""
    if not isinstance(ext, Mapping):
        return None
    return ext.get(name, ext.get(f"x-{name}"))


def _scene_starts(document: Any) -> List[float]:
    """Where each clip lands on the timeline; the timeline's own scene boundaries."""
    points = []
    for clip in _value(document, "clips") or ():
        start = _value(clip, "dst_in")
        points.append(0.0 if start is None else _float(start))
    return points


def _spans(points: Sequence[float], duration: float) -> List[Mark]:
    starts = sorted({point for point in points if 0.0 <= point < duration})
    marks = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else duration
        marks.append((start, end, f"Chapter {index + 1}"))
    return marks


def chapter_marks(document: Any, duration: float) -> List[Mark]:
    """Chapter spans the document declares, or an empty list -- never invented.

    A document may declare them literally (``chapters``: ``[{title, start, end}]``)
    or ask for the timeline's own boundaries (``chapters: "scenes"``, or an
    explicit ``scene_boundaries`` list). Either form is read from the document or
    from the ``x-`` fields it round-trips through.
    """
    if document is None:
        return []
    ext = _value(document, "ext") or {}
    declared = _value(document, "chapters")
    boundaries = _value(document, "scene_boundaries")
    if declared is None:
        declared = _declared(ext, "chapters")
    if boundaries is None:
        boundaries = _declared(ext, "scene_boundaries")
    if isinstance(declared, (list, tuple)) and not boundaries:
        spans = []
        for index, item in enumerate(declared):
            start = _float(_value(item, "start"))
            end = _value(item, "end")
            title = str(_value(item, "title") or f"Chapter {index + 1}")
            spans.append((start, duration if end is None else _float(end), title))
        return [span for span in spans if span[1] > span[0] < duration]
    if boundaries is None and str(declared or "").strip().lower() in {"scenes", "scene", "boundaries"}:
        boundaries = _scene_starts(document)
    if not boundaries:
        return []
    return _spans([_float(point) for point in boundaries], duration)


def ffmetadata(marks: Sequence[Mark]) -> str:
    """An FFmetadata chapter file, in milliseconds, one block per mark."""
    lines = [";FFMETADATA1"]
    for start, end, title in marks:
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={int(round(start * 1000))}",
            f"END={int(round(end * 1000))}",
            f"title={title}",
        ]
    return "\n".join(lines) + "\n"
