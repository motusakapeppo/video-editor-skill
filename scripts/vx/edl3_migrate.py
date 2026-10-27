"""vx.edl3_migrate — migrate a ``vx.edl/2.2`` document to ``vx.edl3/1``.

``migrate(edl2)`` parses the 2.2 document through
:class:`vx.edl_plan.EDLPlan` (so the strict 2.2 validation runs first), maps
every representable field, and returns a canonical ``vx.edl3/1`` dict. It adds
no effect, no clip and no timing that the source document did not declare.

Mapping
-------
================  ==========================================================
EDL 2.2           EDL3
================  ==========================================================
timeline.fps      fps (integer; a fractional fps is refused, see below)
resolution        size
sources[sid].path Clip3.source / AudioClip3.source (looked up by source_id)
clip.layer == 0   clips, sorted by (dst_in, clip_id), in 2.2 render order
src_in/src_out    src_in/src_out, verbatim
dst_in/dst_out    dst_in/dst_out, verbatim (EDL3 no longer implies them)
transition_in     clip.transition_in (``fade`` -> ``fade_in``)
transition_out    clip.transition_out
audio tracks      audio (role, clips, src/dst window, gain_db)
ducking           ducking, field for field
================  ==========================================================

Gaps — reported, never invented
-------------------------------
* ``layer >= 1`` video (B-roll overlays) has no flat-clip representation in
  ``vx.edl3/1``: refused with ``UNSUPPORTED_LAYER`` instead of dropped.
* a fractional fps cannot be stored in an integer ``fps``: refused with
  ``FPS_NOT_INTEGRAL`` instead of rounded.
* ``timeline.aspect_ratio`` is derivable from ``resolution`` and is not
  stored; ``sources[].sha256`` / ``duration`` / ``camera`` are provenance, not
  edit, and are not carried; ``sources[].source_id`` names become file paths.
* ``timeline.duration_sec`` beyond the last clip end is not representable:
  EDL3 derives its duration from the clips.
* a 2.2 transition with ``duration_sec == 0`` is a no-op and is not carried.
* the 2.2 renderer applies ``transition_in``/``transition_out`` only to
  ``layer >= 1`` clips, so carrying a layer-0 transition is document-faithful
  but not pixel-faithful to that renderer (declared, not hidden).
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from . import edl_plan
from .edl3 import EDL3_VERSION, Timeline3

#: 2.2 transition type -> EDL3 kind. An unknown type is refused, not guessed.
TRANSITION_MAP = {
    "fade": "fade_in",
    "fade_in": "fade_in",
    "fade_out": "fade_out",
    "crossfade": "crossfade",
    "dissolve": "crossfade",
}

__all__ = ["MigrationError", "TRANSITION_MAP", "migrate"]


class MigrationError(Exception):
    """A 2.2 document could not be migrated without inventing something."""

    def __init__(self, code: str, detail: str, where: str = "") -> None:
        self.code = code
        self.detail = detail
        self.where = where
        text = f"{code}: {detail}"
        if where:
            text = f"{text} ({where})"
        super().__init__(text)


def _load(edl2: Mapping[str, Any]) -> Any:
    if not isinstance(edl2, Mapping):
        raise MigrationError("EDL2_SHAPE", f"document is not a mapping: {edl2!r}")
    try:
        return edl_plan.EDLPlan.from_dict(dict(edl2))
    except edl_plan.EDLValidationError as exc:
        raise MigrationError("EDL2_INVALID", "; ".join(exc.errors)) from exc


def _transition(raw: Any, where: str) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if float(raw.duration_sec) <= 0.0:
        return None
    kind = TRANSITION_MAP.get(str(raw.type).strip().lower())
    if kind is None:
        raise MigrationError(
            "UNSUPPORTED_TRANSITION",
            f"2.2 transition type {raw.type!r} has no EDL3 kind "
            f"(known: {sorted(TRANSITION_MAP)})",
            where,
        )
    return {"kind": kind, "duration": float(raw.duration_sec)}


def _clip_dict(plan: Any, clip: Any, index: int) -> Dict[str, Any]:
    where = f"tracks.video.clips[{index}]"
    if clip.layer != 0:
        raise MigrationError(
            "UNSUPPORTED_LAYER",
            f"layer {clip.layer} clip {clip.clip_id!r} has no flat-clip "
            "representation in vx.edl3/1",
            where,
        )
    reference = plan.sources.get(clip.source_id)
    if reference is None:
        raise MigrationError(
            "EDL2_INVALID",
            f"source {clip.source_id!r} is not declared",
            where,
        )
    return {
        "id": str(clip.clip_id),
        "source": str(reference.path),
        "sequence": None,
        "src_in": float(clip.src_in),
        "src_out": float(clip.src_out),
        "dst_in": float(clip.dst_in),
        "dst_out": float(clip.dst_out),
        "effects": [],
        "transition_in": _transition(clip.transition_in, f"{where}.transition_in"),
        "transition_out": _transition(clip.transition_out, f"{where}.transition_out"),
    }


def _audio_track(plan: Any, track: Any) -> Dict[str, Any]:
    clips: List[Dict[str, Any]] = []
    for clip in sorted(track.clips, key=lambda item: float(item.dst_in)):
        reference = plan.sources.get(clip.source_id)
        if reference is None:
            raise MigrationError(
                "EDL2_INVALID",
                f"audio source {clip.source_id!r} is not declared",
                f"tracks.audio[{track.track_id}]",
            )
        clips.append(
            {
                "source": str(reference.path),
                "src_in": float(clip.src_in),
                "src_out": float(clip.src_out),
                "dst_in": float(clip.dst_in),
                "dst_out": float(clip.dst_out),
                "gain_db": float(clip.gain_db),
            }
        )
    return {"role": str(track.role), "clips": clips}


def migrate(edl2: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the canonical ``vx.edl3/1`` document equivalent to ``edl2``."""
    plan = _load(edl2)

    fps = float(plan.fps)
    if not fps.is_integer():
        raise MigrationError(
            "FPS_NOT_INTEGRAL",
            f"vx.edl3/1 stores an integer fps; {fps} would have to be rounded, "
            "which would retime every clip",
            "timeline.fps",
        )

    video_clips = [clip for track in plan.video_tracks for clip in track.clips]
    ordered = sorted(video_clips, key=lambda clip: (float(clip.dst_in), clip.clip_id))

    document: Dict[str, Any] = {
        "version": EDL3_VERSION,
        "fps": int(fps),
        "size": [int(plan.resolution[0]), int(plan.resolution[1])],
        "clips": [_clip_dict(plan, clip, index) for index, clip in enumerate(ordered)],
        "sequences": {},
        "adjustments": [],
        "text_layers": [],
        "captions": [],
        "audio": [_audio_track(plan, track) for track in plan.audio_tracks],
        "ducking": plan.ducking.to_dict() if plan.ducking is not None else None,
    }

    timeline = Timeline3.from_dict(document)
    problems = timeline.validate()
    if problems:
        detail = "; ".join(
            f"{problem['code']} at {problem['where']}: {problem['detail']}"
            for problem in problems
        )
        raise MigrationError("EDL3_INVALID", detail)
    return timeline.to_dict()
