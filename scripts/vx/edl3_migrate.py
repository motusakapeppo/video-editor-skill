"""vx.edl3_migrate — migrate a ``vx.edl/2.2`` document to ``vx.edl3/1.2``.

``migrate(edl2)`` parses the 2.2 document through
:class:`vx.edl_plan.EDLPlan` (so the strict 2.2 validation runs first), maps
every representable field, and returns a canonical ``vx.edl3/1.2`` dict. It adds
no effect, no clip and no timing that the source document did not declare.
``audit(edl2)`` is the other half of the same promise: it lists what the 2.2
document declares and ``vx.edl3/1.2`` cannot carry, so ``vx edl3 migrate`` can
refuse instead of writing a document that lost pieces in silence.

Mapping
-------
================  ==========================================================
EDL 2.2           EDL3
================  ==========================================================
timeline.fps      fps, the declared rate verbatim (``vx.edl3/1.2`` stores a
                  rate: 29.97 and 29.645583 are kept as they are, never
                  rounded — see ``allow_fractional_fps``)
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
  this mapping: refused with ``UNSUPPORTED_LAYER`` instead of dropped.
* ``allow_fractional_fps=False`` restores the old ``FPS_NOT_INTEGRAL``
  refusal, for a caller that targets ``vx.edl3/1`` (which stored an integer
  fps). The CLI targets ``vx.edl3/1.2`` and keeps the declared rate.
* ``timeline.aspect_ratio`` is derivable from ``resolution`` and is not
  stored; ``sources[].sha256`` / ``duration`` / ``camera`` are provenance, not
  edit, and are not carried; ``sources[].source_id`` names become file paths.
* ``timeline.duration_sec`` beyond the last clip end is not representable:
  EDL3 derives its duration from the clips. :func:`audit` reports it as
  ``TAIL``.
* a 2.2 transition with ``duration_sec == 0`` is a no-op and is not carried.
* the 2.2 renderer applies ``transition_in``/``transition_out`` only to
  ``layer >= 1`` clips, so carrying a layer-0 transition is document-faithful
  but not pixel-faithful to that renderer (declared by :func:`notes`).
* per-bed ducking (``ducking_tracks``) and ``clip.compress`` are audible in
  2.2 and have no field in ``vx.edl3/1``: :func:`audit` reports them as
  ``DUCKING_TRACKS`` / ``AUDIO_COMPRESS`` and the CLI refuses the document
  instead of writing it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from . import edl_plan
from .edl3 import EDL3_VERSION, Timeline3

#: The 2.2 schema string the migration report names as its starting point.
EDL2_SCHEMA = edl_plan.EDL_SCHEMA

#: 2.2 transition type -> EDL3 kind. An unknown type is refused, not guessed.
TRANSITION_MAP = {
    "fade": "fade_in",
    "fade_in": "fade_in",
    "fade_out": "fade_out",
    "crossfade": "crossfade",
    "dissolve": "crossfade",
}

#: What ``audit`` can find: every code means "2.2 declares this and vx.edl3/1
#: would drop it", so the CLI refuses instead of writing a lossy document.
LOSS_CODES = {
    "AUDIO_COMPRESS": "a 2.2 audio clip asks for the compressor; EDL3 has no "
    "per-clip compressor",
    "DUCKING_TRACKS": "2.2 can duck each bed with its own spec; EDL3 carries a "
    "single `ducking` field",
    "TAIL": "the declared timeline is longer than its last clip; EDL3 derives "
    "the duration from the clips",
}

__all__ = [
    "EDL2_SCHEMA",
    "LOSS_CODES",
    "MigrationError",
    "TRANSITION_MAP",
    "audit",
    "migrate",
    "notes",
]


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


def migrate(
    edl2: Mapping[str, Any], *, allow_fractional_fps: bool = True
) -> Dict[str, Any]:
    """Return the canonical ``vx.edl3/1.2`` document equivalent to ``edl2``.

    ``allow_fractional_fps=False`` restores the pre-1.2 policy: refuse a rate
    that is not an integer instead of carrying it verbatim.
    """
    plan = _load(edl2)

    fps = float(plan.fps)
    if not fps.is_integer() and not allow_fractional_fps:
        raise MigrationError(
            "FPS_NOT_INTEGRAL",
            f"an integer fps was requested; {fps} would have to be rounded, "
            "which would retime every clip",
            "timeline.fps",
        )
    # ``vx.edl3/1.2`` stores a rate (D6): 29.97 is a deliverable rate and the
    # declared value is kept as it is, so no clip is retimed.
    rate = int(fps) if fps.is_integer() else fps

    video_clips = [clip for track in plan.video_tracks for clip in track.clips]
    ordered = sorted(video_clips, key=lambda clip: (float(clip.dst_in), clip.clip_id))

    document: Dict[str, Any] = {
        "version": EDL3_VERSION,
        "fps": rate,
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


def audit(edl2: Mapping[str, Any]) -> List[Dict[str, str]]:
    """Return what ``edl2`` declares that ``vx.edl3/1.2`` cannot carry.

    An empty list means the migration is faithful: every audible or visible
    choice of the 2.2 document has a field on the other side. A non-empty list
    is a refusal with the offending field named — ``vx edl3 migrate`` does not
    write a document that would drop it in silence.
    """
    plan = _load(edl2)
    losses: List[Dict[str, str]] = []

    for track_index, track in enumerate(plan.audio_tracks):
        for clip_index, clip in enumerate(track.clips):
            if not bool(getattr(clip, "compress", False)):
                continue
            losses.append(
                {
                    "code": "AUDIO_COMPRESS",
                    "where": f"tracks.audio[{track_index}].clips[{clip_index}]",
                    "detail": (
                        f"role {track.role!r} clip {clip.clip_id!r} declares "
                        "compress=true; EDL3 has no per-clip compressor, so the "
                        "mixed audio would differ"
                    ),
                }
            )

    # ``vx.edl3/1.2`` has one ``ducking`` field; a per-bed spec that is not the
    # default would be left unducked, which is audible.
    for index, spec in enumerate(plan.ducking_tracks):
        if plan.ducking is not None and spec.to_dict() == plan.ducking.to_dict():
            continue
        losses.append(
            {
                "code": "DUCKING_TRACKS",
                "where": f"ducking_tracks[{index}]",
                "detail": (
                    f"target_role {spec.target_role!r} has its own ducking; "
                    "vx.edl3/1 carries a single `ducking` spec"
                ),
            }
        )

    ends = [
        float(clip.dst_out)
        for track in plan.video_tracks
        for clip in track.clips
    ]
    end = max(ends) if ends else 0.0
    tolerance = 1.0 / float(plan.fps) + 1e-9 if float(plan.fps) > 0 else 1e-9
    if float(plan.duration_sec) > end + tolerance:
        losses.append(
            {
                "code": "TAIL",
                "where": "timeline.duration_sec",
                "detail": (
                    f"the timeline declares {float(plan.duration_sec):.3f}s but "
                    f"its last clip ends at {end:.3f}s; EDL3 derives the "
                    "duration from the clips"
                ),
            }
        )
    return losses


def notes(edl2: Mapping[str, Any]) -> List[str]:
    """Return the declared differences a faithful migration still has."""
    plan = _load(edl2)
    out: List[str] = []
    fps = float(plan.fps)
    if not fps.is_integer():
        out.append(
            f"fps {fps!r} portato com'e' (vx.edl3 memorizza un rate: "
            "nessun arrotondamento, nessun clip ritoccato)"
        )

    transitions = 0
    for track in plan.video_tracks:
        for clip in track.clips:
            for transition in (clip.transition_in, clip.transition_out):
                if transition is not None and float(transition.duration_sec) > 0:
                    transitions += 1
    if transitions:
        out.append(
            f"{transitions} transizione/i su clip di layer 0: il renderer 2.2 le "
            "applicava solo ai layer >= 1, EDL3 le applica"
        )

    roles = sorted(
        {
            str(clip.role)
            for track in plan.video_tracks
            for clip in track.clips
            if str(clip.role) not in ("", "main_talking_head")
        }
    )
    if roles:
        out.append(
            "ruoli clip "
            + ", ".join(roles)
            + ": etichette del piano 2.2, non trasportate in EDL3"
        )

    if any(track.clips for track in plan.audio_tracks):
        out.append(
            "il documento porta le tracce audio: `vx edl3 render` le monta con "
            "--audio mix (o le dichiara scartate con --audio drop); il default "
            "del render e' --audio refuse"
        )
    return out

