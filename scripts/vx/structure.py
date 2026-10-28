"""vx.structure -- the edit's shape, judged against a declared profile (F26-T04).

:mod:`vx.profiles` says what a genre is; this module says whether a document is
built that way. It reads the timeline and nothing else: no render, no ffmpeg, no
filesystem, no import of the registry at runtime.

The refusals are the document's own numbers, never a guess. Beat alignment is
deliberately *not* checked: a document carries no beat grid, no BPM and no marker
on the timeline, so a beat check would have to invent the beats. When the format
carries that evidence, the check can be added here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only, never an import at runtime
    from .edl3 import Timeline3
    from .profiles import Profile

__all__ = ["DENSITY_WINDOW", "check_structure"]

#: The window every ``cut_density_*`` number is quoted over, in seconds: the
#: profile bands are "cuts per ten seconds", the unit D031 already uses.
DENSITY_WINDOW = 10.0


def _layer0_spans(timeline: "Timeline3") -> List[Tuple[float, float]]:
    """The ``(start, end)`` of every layer-0 clip, in timeline order.

    Same anchoring as :func:`vx.edl3._clip_start_times`: ``dst_in`` when the
    document declares it, otherwise the concatenation cursor. Overlay layers are
    windows over this base track and never extend it.
    """
    cursor = 0.0
    spans: List[Tuple[float, float]] = []
    for clip in timeline.clips:
        if clip.layer != 0:
            continue
        start = cursor if clip.dst_in is None else float(clip.dst_in)
        end = start + max(0.0, clip.timeline_duration)
        spans.append((start, end))
        cursor = max(cursor, end)
    spans.sort(key=lambda span: span[0])
    return spans


def _cut_count(spans: List[Tuple[float, float]]) -> int:
    """Boundaries where one layer-0 clip ends and the next begins on the timeline.

    The first layer-0 clip's start is not a cut (it is the opening frame), and a
    boundary counts only when the two windows meet: a gap is a gap, not a cut.
    """
    return sum(
        1
        for (_, previous_end), (start, _) in zip(spans, spans[1:])
        if abs(start - previous_end) <= 1e-6
    )


def _hook_instant(
    timeline: "Timeline3", spans: List[Tuple[float, float]]
) -> Optional[float]:
    """The first instant the picture or an opening overlay visibly changes.

    Candidates are: the first hard cut on layer 0 (the start of the second
    layer-0 clip), every text layer's ``time_in``, and the start of every
    generator overlay on a layer >= 1. The earliest candidate wins, so a text
    card visible from frame 0 is a hook at ``0.0``; an opening generator on
    layer 0 is the picture itself, not a change.
    """
    instants: List[float] = []
    if len(spans) > 1 and abs(spans[1][0] - spans[0][1]) <= 1e-6:
        instants.append(spans[1][0])
    instants.extend(float(layer.time_in) for layer in timeline.text_layers)
    for clip in timeline.clips:
        if clip.layer >= 1 and clip.generator is not None:
            instants.append(0.0 if clip.dst_in is None else float(clip.dst_in))
    return min(instants) if instants else None


def _has_interview_evidence(timeline: "Timeline3") -> bool:
    """Whether the document itself declares spoken/interview material.

    ``Clip3.audio_policy`` is a *time* policy (``segments``/``silence``/``pitch``,
    see :class:`vx.edl3_time.AudioPolicy`), not a role, so it cannot carry
    "dialogue". The two carriers the document really has are a nested sequence
    named as an interview (``interview``/``intervista`` in its id) and an audio
    bus whose role is ``dialogue`` and which carries at least one clip. Nothing
    is inferred from the picture.
    """
    for clip in timeline.clips:
        name = (clip.sequence or "").lower()
        if "interview" in name or "intervista" in name:
            return True
    for track in timeline.audio:
        if track.role.strip().lower() == "dialogue" and track.clips:
            return True
    return any(
        "interview" in str(key).lower() or "intervista" in str(key).lower()
        for key in timeline.sequences
    )


def check_structure(timeline: "Timeline3", profile: "Profile") -> Tuple[str, ...]:
    """One human detail per way the edit breaks ``profile``'s declared grammar.

    Pure: the document is read, never rendered; nothing is guessed. Each detail
    starts with its stable code, so ``Timeline3.validate()`` can report it.

    *Duration.* The timeline is the extent of layer 0, from the first base clip's
    start to the last base clip's end (overlays do not lengthen the edit). It must
    sit inside ``[min_seconds, max_seconds]``.

    *Cut count and rate.* A cut is a boundary where one layer-0 clip ends and the
    next begins at the same instant (within ``1e-6`` s); the first clip's start is
    the opening frame, not a cut. The rate is ``cuts * 10 / duration`` -- cuts per
    :data:`DENSITY_WINDOW` seconds over the whole timeline -- and must sit inside
    ``[cut_density_min, cut_density_max]``. A transition across the boundary still
    counts as a boundary; a gap does not count as a cut.

    *Hook.* Only when ``hook_seconds > 0``. The hook instant is the earliest of:
    the first hard cut on layer 0; any text layer's ``time_in``; the start of any
    generator overlay on a layer >= 1. A text card up from frame 0 hooks at 0.0.
    The instant must be ``<= hook_seconds``; a one-shot document with no text and
    no overlay has no visible change at all and is reported as such.

    *Interview.* Only when ``interview_driven`` is true. Only what the document
    carries counts: a nested sequence whose id names an interview, or a
    ``dialogue`` audio bus holding at least one clip. (``Clip3.audio_policy`` is a
    time policy, not a role.) Without either, the detail says the grammar is
    unbacked; no interview is inferred from the picture.

    *Beat.* Not checked, deliberately. ``beat_driven`` profiles declare that cuts
    should land on a musical grid, but the document carries no beat grid, no BPM
    and no marker on the timeline: a check would have to invent the beats. This
    function stays silent about it rather than guess.
    """
    spans = _layer0_spans(timeline)
    span = max((end for _, end in spans), default=0.0)
    cuts = _cut_count(spans)
    problems: List[str] = []

    if span < profile.min_seconds - 1e-9 or span > profile.max_seconds + 1e-9:
        problems.append(
            f"STRUCTURE_DURATION: {span:.3f}s is outside the "
            f"{profile.min_seconds:g}-{profile.max_seconds:g}s window for "
            f"{profile.id}"
        )

    if span > 0.0:
        rate = cuts * DENSITY_WINDOW / span
        if rate < profile.cut_density_min - 1e-9 or rate > profile.cut_density_max + 1e-9:
            problems.append(
                f"STRUCTURE_DENSITY: {rate:.1f} cuts per {DENSITY_WINDOW:g}s "
                f"({cuts} cuts between layer-0 clips over {span:.3f}s) is outside "
                f"the {profile.cut_density_min:g}-{profile.cut_density_max:g} band "
                f"for {profile.id}"
            )

    if profile.hook_seconds > 0.0:
        instant = _hook_instant(timeline, spans)
        if instant is None:
            problems.append(
                f"STRUCTURE_HOOK: no visible change in {span:.3f}s: one shot runs "
                f"to the end, so the {profile.hook_seconds:g}s hook for "
                f"{profile.id} never lands"
            )
        elif instant > profile.hook_seconds + 1e-9:
            problems.append(
                f"STRUCTURE_HOOK: the first visible change is at {instant:.3f}s, "
                f"after the {profile.hook_seconds:g}s hook for {profile.id}"
            )

    if profile.interview_driven and not _has_interview_evidence(timeline):
        problems.append(
            f"STRUCTURE_INTERVIEW: the {profile.id} grammar is interview-driven, "
            "but no nested sequence is named as an interview and no audio track "
            "declares dialogue material"
        )

    return tuple(problems)
