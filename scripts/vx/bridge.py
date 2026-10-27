"""vx.bridge — the analysis -> assembly bridge (references/26, section 4).

The bridge is the only place where a measurement becomes an edit. It is not a
pipeline: it takes a validated ``EDLPlan`` plus the audio analyses of its
sources and returns a **new** plan (the input is never mutated) and a
``BridgeReport`` that declares what was applied and what was not.

Two blocks live here:

* **A5, silence cuts.** Every layer-0 clip is cut against the non-speech spans
  of its own source. The spans come from the VAD ``speech_regions`` complement
  inside the clip window, and fall back to ``dead_air`` only when the VAD is
  absent. A removed interval that does not fall inside the union of those spans
  is a ``VxError``: the law of the evidence is not a convention here.
* **A3, per-clip loudness on the used segment.** Each dialogue fragment is
  measured on the kept span it actually plays, not on the whole source file. The
  gain is ``target_lufs - measured_i``; when that gain falls outside
  ``[-60, 24]`` it is clamped and the shortfall is declared (``shortfall_lu``),
  never absorbed.

``not_applied`` is always present in the report, even when empty: a report with
nothing declared means the bridge did not look, not that nothing happened.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import edl_plan
from .contract import THRESHOLDS, VxError

BRIDGE_SCHEMA = "vx.bridge_report/1.0"

KINDS = (
    "transcript_span",
    "word_span",
    "silence_span",
    "face_track",
    "shot",
    "beat",
    "keyword",
    "brief",
    "music_section",
)

# ``edl_plan._validate_clip`` rejects a gain outside this window; the bridge
# clamps to the same contract bound instead of emitting a plan it would refuse.
GAIN_LIMITS_DB = (-60.0, 24.0)
# An integrated loudness outside this window is not a measurement: ``loudnorm``
# reports 0.0 for a digital-silence fallback, and nothing is above 0 LUFS. The
# bounds are exclusive on both ends: 0.0 and -70.0 are failures, not readings.
MEASURED_I_LIMITS = (-70.0, 0.0)

_EPS = 1e-9


@dataclass(frozen=True)
class Anchor:
    """A measurement pinned to a source time range (spec 4.1)."""

    anchor_id: str
    kind: str
    source_id: str
    t0: float
    t1: Optional[float] = None
    text: str = ""
    confidence: Optional[float] = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.anchor_id, str) or not self.anchor_id:
            raise VxError("anchor_id must be a non-empty string")
        if self.kind not in KINDS:
            raise VxError(
                f"anchor {self.anchor_id!r}: kind {self.kind!r} is not in the registry"
            )
        if not _finite(self.t0):
            raise VxError(f"anchor {self.anchor_id!r}: t0 must be finite")
        if self.t1 is not None and not _finite(self.t1):
            raise VxError(f"anchor {self.anchor_id!r}: t1 must be finite")
        object.__setattr__(self, "payload", dict(self.payload or {}))

    def to_dict(self) -> Dict[str, Any]:
        """Neutral fields are omitted, exactly like the EDL 2.2 writer."""
        data: Dict[str, Any] = {
            "anchor_id": self.anchor_id,
            "kind": self.kind,
            "source_id": self.source_id,
            "t0": round(float(self.t0), 3),
        }
        if self.t1 is not None:
            data["t1"] = round(float(self.t1), 3)
        if self.text:
            data["text"] = self.text
        if self.confidence is not None:
            data["confidence"] = self.confidence
        if self.payload:
            data["payload"] = dict(self.payload)
        return data


@dataclass
class BridgeReport:
    """The bridge's referto: seven lists, ``not_applied`` always present."""

    anchors: List[Anchor] = field(default_factory=list)
    cuts: List[Dict[str, Any]] = field(default_factory=list)
    captions: List[Dict[str, Any]] = field(default_factory=list)
    broll: List[Dict[str, Any]] = field(default_factory=list)
    reframe: List[Dict[str, Any]] = field(default_factory=list)
    audio: List[Dict[str, Any]] = field(default_factory=list)
    not_applied: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": BRIDGE_SCHEMA,
            "anchors": [
                anchor.to_dict() if isinstance(anchor, Anchor) else anchor
                for anchor in self.anchors
            ],
            "cuts": [dict(item) for item in self.cuts],
            "captions": [dict(item) for item in self.captions],
            "broll": [dict(item) for item in self.broll],
            "reframe": [dict(item) for item in self.reframe],
            "audio": [dict(item) for item in self.audio],
            "not_applied": [dict(item) for item in self.not_applied],
        }

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return target


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _finite(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _analysis_field(analysis: Any, name: str) -> Any:
    """Read a field from ``contract.AudioAnalysis`` or from its ``to_dict()`` form."""
    if analysis is None:
        return None
    if isinstance(analysis, Mapping):
        return analysis.get(name)
    return getattr(analysis, name, None)


def _spans(raw: Any) -> List[Tuple[float, float]]:
    """Normalise a span list to sorted finite ``(start, end)`` pairs."""
    out: List[Tuple[float, float]] = []
    for item in raw or ():
        if isinstance(item, Mapping):
            start, end = item.get("start"), item.get("end")
        else:
            try:
                start, end = item[0], item[1]
            except (TypeError, IndexError, KeyError):
                continue
        if not (_finite(start) and _finite(end)):
            continue
        first, last = float(start), float(end)
        if last <= first:
            continue
        out.append((first, last))
    out.sort()
    return out


def _complement(lo: float, hi: float, regions: Sequence[Tuple[float, float]]):
    """The gaps inside ``[lo, hi]`` left by ``regions``: the only place to cut."""
    gaps: List[Tuple[float, float]] = []
    cursor = lo
    for start, end in regions:
        if end <= lo:
            continue
        if start >= hi:
            break
        if start > cursor + _EPS:
            gaps.append((cursor, min(start, hi)))
        cursor = max(cursor, end)
    if cursor < hi - _EPS:
        gaps.append((cursor, hi))
    return gaps


def _intersections(lo: float, hi: float, spans: Sequence[Tuple[float, float]]):
    out: List[Tuple[float, float]] = []
    for start, end in spans:
        first = max(lo, start)
        last = min(hi, end)
        if last > first + _EPS:
            out.append((first, last))
    return out


def _silence_windows(clip: Any, analysis: Any) -> List[Tuple[float, float]]:
    """Measured non-speech spans inside the clip window, VAD first, dead air second."""
    lo, hi = float(clip.src_in), float(clip.src_out)
    if hi <= lo:
        return []
    speech = _spans(_analysis_field(analysis, "speech_regions"))
    if speech:
        return _complement(lo, hi, speech)
    return _intersections(lo, hi, _spans(_analysis_field(analysis, "dead_air")))


def _covered(start: float, end: float, spans: Sequence[Tuple[float, float]]) -> bool:
    cursor = start
    for first, last in sorted(spans):
        if last <= cursor + _EPS:
            continue
        if first > cursor + 1e-6:
            return False
        cursor = max(cursor, last)
        if cursor >= end - 1e-6:
            return True
    return cursor >= end - 1e-6


def assert_removals_within(removals: Any, spans: Any) -> None:
    """The A5 gate: every removed interval lies inside the measured silence.

    This is a hard error, not a warning. ``build`` calls it on every removal it
    derives, so a future change to the derivation cannot quietly cut speech.
    """
    covers = _spans(spans)
    for item in removals or ():
        if isinstance(item, Mapping):
            start, end = item.get("start"), item.get("end")
        else:
            try:
                start, end = item[0], item[1]
            except (TypeError, IndexError, KeyError) as exc:
                raise VxError(f"bridge: invalid removal {item!r}") from exc
        if not (_finite(start) and _finite(end)):
            raise VxError(f"bridge: invalid removal {item!r}")
        first, last = float(start), float(end)
        if last <= first:
            raise VxError(f"bridge: empty removal {first:.3f}..{last:.3f}")
        if not _covered(first, last, covers):
            raise VxError(
                "bridge: removed interval "
                f"{first:.3f}..{last:.3f} is not inside the measured silence"
            )


def _candidate_removals(
    windows: Sequence[Tuple[float, float]],
    pad: float,
    silence_min: float,
    src_in: float,
    src_out: float,
):
    """Shrink each window inward by ``pad``, drop short removals, merge overlaps.

    A window that already touches ``src_in`` (leading) or ``src_out`` (trailing)
    has content on one side only: padding that side would leave a sub-fragment
    stub of silence-adjacent content that merges straight back, so the whole
    head/tail silence would survive. The pad is skipped on the boundary side and
    the removal keeps the measured span's own edge.

    The bounds are rounded toward the inside (ceil on the start, floor on the
    end) so three-decimal times stay inside the measured silence even after the
    rounding.
    """
    shrunk: List[List[float]] = []
    for start, end in windows:
        leading = start <= src_in + _EPS
        trailing = end >= src_out - _EPS
        floor_start = start if leading else start + pad
        ceil_end = end if trailing else end - pad
        first = math.ceil(floor_start * 1000.0 - 1e-9) / 1000.0
        last = math.floor(ceil_end * 1000.0 + 1e-9) / 1000.0
        if last - first >= silence_min - _EPS:
            shrunk.append([first, last])
    merged: List[List[float]] = []
    for first, last in sorted(shrunk):
        if merged and first <= merged[-1][1] + _EPS:
            merged[-1][1] = max(merged[-1][1], last)
        else:
            merged.append([first, last])
    return [(first, last) for first, last in merged]


def _keeps(
    src_in: float, src_out: float, removals: Sequence[Mapping[str, Any]]
) -> List[Tuple[float, float]]:
    keeps: List[Tuple[float, float]] = []
    cursor = src_in
    for removal in removals:
        start = float(removal["start"])
        end = float(removal["end"])
        if start > cursor + _EPS:
            keeps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < src_out - _EPS:
        keeps.append((cursor, src_out))
    return keeps


def _apply_min_fragment(
    src_in: float,
    src_out: float,
    removals: List[Dict[str, Any]],
    min_fragment: float,
    report: BridgeReport,
    clip: Any,
) -> Tuple[List[Dict[str, Any]], List[Tuple[float, float]]]:
    """A keep shorter than ``min_fragment`` merges back and is declared."""
    remaining = list(removals)
    while True:
        keeps = _keeps(src_in, src_out, remaining)
        if len(keeps) <= 1:
            return remaining, keeps
        short = next(
            (
                (index, span)
                for index, span in enumerate(keeps)
                if span[1] - span[0] < min_fragment - _EPS
            ),
            None,
        )
        if short is None:
            return remaining, keeps
        index, span = short
        victim = remaining.pop(index - 1 if index > 0 else 0)
        report.not_applied.append(
            {
                "anchor_id": victim["anchor_id"],
                "reason": "fragment_below_min",
                "clip_id": clip.clip_id,
                "t0": round(span[0], 3),
                "t1": round(span[1], 3),
                "min_fragment_sec": round(min_fragment, 3),
            }
        )


def _transition_cap(length: float) -> float:
    """``edl_plan._validate_clip`` caps a transition at ``min(clip_length, 1.5)``."""
    return min(float(length), 1.5)


def _fit_transition(transition: Any, length: float, report: BridgeReport, clip_id: str):
    """A transition longer than its fragment is dropped and declared, never clipped silently."""
    if transition is None:
        return None
    cap = _transition_cap(length)
    if float(transition.duration_sec) > cap + 1e-6:
        report.not_applied.append(
            {
                "anchor_id": None,
                "reason": "transition_dropped",
                "clip_id": clip_id,
                "transition": transition.type,
                "duration_sec": transition.duration_sec,
                "fragment_sec": round(length, 3),
                "cap_sec": round(cap, 3),
            }
        )
        return None
    return transition


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _media_duration(plan: edl_plan.EDLPlan, source_id: str, fallback: float) -> float:
    ref = plan.sources.get(source_id)
    value = getattr(ref, "duration", None)
    if _finite(value) and float(value) > 0:
        return float(value)
    return fallback


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #


def build(
    plan: edl_plan.EDLPlan,
    analyses: Mapping[str, Any],
    *,
    target_lufs: float,
    loudness_spans: Callable[[str, Sequence[Sequence[float]]], Mapping[str, Any]],
) -> Tuple[edl_plan.EDLPlan, BridgeReport]:
    """Cut the a_roll chain on measured silence and gain it per used segment.

    ``analyses`` maps ``source_id`` to an audio analysis: either a
    ``contract.AudioAnalysis``, its ``to_dict()`` form, or the exception that
    prevented the analysis. A failed analysis never crashes the bridge: the clip
    stays whole, un-gained, and the failure is declared in ``not_applied``.
    """
    if not isinstance(plan, edl_plan.EDLPlan):
        raise VxError("bridge.build expects an EDLPlan")
    if not _finite(target_lufs):
        raise VxError("bridge.build: target_lufs must be finite")
    min_fragment = float(THRESHOLDS["bridge_min_fragment_ms"]) / 1000.0
    if min_fragment <= 0.0:
        raise VxError("bridge: bridge_min_fragment_ms must be positive")
    pad = float(THRESHOLDS["silence_pad"])
    silence_min = float(THRESHOLDS["silence_min"])

    report = BridgeReport()
    layer0_tracks = [
        index
        for index, track in enumerate(plan.video_tracks)
        if any(int(getattr(clip, "layer", 0)) == 0 for clip in track.clips)
    ]
    if len(layer0_tracks) > 1:
        raise VxError(
            "bridge: layer 0 must live on at most one video track, found on "
            + ", ".join(
                f"track_id={plan.video_tracks[index].track_id!r} "
                f"role={plan.video_tracks[index].role!r}"
                for index in layer0_tracks
            )
        )
    chain = sorted(
        (
            (track_index, clip)
            for track_index, track in enumerate(plan.video_tracks)
            for clip in track.clips
            if int(getattr(clip, "layer", 0)) == 0
        ),
        key=lambda pair: float(pair[1].dst_in),
    )
    if not chain:
        report.not_applied.append({"anchor_id": None, "reason": "no_a_roll_layer0"})
        return _replace_plan(
            plan, plan.video_tracks, plan.audio_tracks, plan.duration_sec
        ), report

    failed_sources: set = set()
    mirror: Dict[Tuple[str, float, float], List[Dict[str, Any]]] = {}
    layer0_by_track: Dict[int, List[edl_plan.ClipSegment]] = {}
    anchor_seq = 0
    cursor = 0.0

    for track_index, clip in chain:
        analysis = analyses.get(clip.source_id) if analyses else None
        fragments: List[Tuple[float, float]]
        if isinstance(analysis, BaseException):
            failed_sources.add(clip.source_id)
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": clip.clip_id,
                    "source_id": clip.source_id,
                    "reason": f"analysis_failed: {analysis}",
                }
            )
            fragments = [(float(clip.src_in), float(clip.src_out))]
        elif analysis is None:
            failed_sources.add(clip.source_id)
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": clip.clip_id,
                    "source_id": clip.source_id,
                    "reason": f"analysis_failed: no audio analysis for {clip.source_id}",
                }
            )
            fragments = [(float(clip.src_in), float(clip.src_out))]
        else:
            windows = _silence_windows(clip, analysis)
            candidates = _candidate_removals(
                windows,
                pad,
                silence_min,
                float(clip.src_in),
                float(clip.src_out),
            )
            origin = (
                "vad_complement"
                if _spans(_analysis_field(analysis, "speech_regions"))
                else "dead_air"
            )
            all_removals: List[Dict[str, Any]] = []
            for start, end in candidates:
                touching = [
                    span
                    for span in windows
                    if span[1] > start - _EPS and span[0] < end + _EPS
                ]
                all_removals.append(
                    {
                        "start": start,
                        "end": end,
                        "anchor_id": f"a_silence_{anchor_seq:03d}",
                        "t0": min(span[0] for span in touching) if touching else start,
                        "t1": max(span[1] for span in touching) if touching else end,
                    }
                )
                anchor_seq += 1
            if all_removals:
                assert_removals_within(all_removals, windows)
            removals, fragments = _apply_min_fragment(
                float(clip.src_in),
                float(clip.src_out),
                list(all_removals),
                min_fragment,
                report,
                clip,
            )
            applied = {removal["anchor_id"] for removal in removals}
            for removal in all_removals:
                report.anchors.append(
                    Anchor(
                        anchor_id=removal["anchor_id"],
                        kind="silence_span",
                        source_id=clip.source_id,
                        t0=removal["t0"],
                        t1=removal["t1"],
                        payload={
                            "clip_id": clip.clip_id,
                            "origin": origin,
                            "applied": removal["anchor_id"] in applied,
                            "removed": [
                                round(float(removal["start"]), 3),
                                round(float(removal["end"]), 3),
                            ],
                        },
                    )
                )
            for removal in removals:
                report.cuts.append(
                    {
                        "t": round(float(removal["start"]), 3),
                        "anchor_id": removal["anchor_id"],
                        "reason": "dead_air",
                    }
                )
            if not fragments:  # pragma: no cover - _apply_min_fragment keeps one
                raise VxError(f"bridge: clip {clip.clip_id!r} lost every frame")

        records: List[Dict[str, Any]] = []
        for index, (first, last) in enumerate(fragments):
            length = last - first
            clip_id = f"{clip.clip_id}_{index}"
            new_clip = edl_plan.ClipSegment(
                clip_id=clip_id,
                source_id=clip.source_id,
                src_in=first,
                src_out=last,
                dst_in=round(cursor, 6),
                dst_out=round(cursor + length, 6),
                layer=0,
                role=clip.role,
                transition_in=_fit_transition(
                    clip.transition_in if index == 0 else None,
                    length,
                    report,
                    clip_id,
                ),
                transition_out=_fit_transition(
                    clip.transition_out if index == len(fragments) - 1 else None,
                    length,
                    report,
                    clip_id,
                ),
                gain_db=0.0,
            )
            layer0_by_track.setdefault(track_index, []).append(new_clip)
            records.append(
                {
                    "src_in": first,
                    "src_out": last,
                    "dst_in": new_clip.dst_in,
                    "dst_out": new_clip.dst_out,
                }
            )
            cursor = round(cursor + length, 6)
        mirror[
            (
                clip.source_id,
                round(float(clip.dst_in), 6),
                round(float(clip.dst_out), 6),
            )
        ] = records

    duration = cursor
    if duration <= 1e-6:
        raise VxError("bridge: the a_roll chain is empty after the cuts")

    # -- dialogue: 1:1 mirror of the video fragments + A3 gain ------------- #
    audio_tracks: List[edl_plan.Track] = []
    for track in plan.audio_tracks:
        if track.role != "dialogue":
            audio_tracks.append(track)
            continue
        clips: List[edl_plan.ClipSegment] = []
        for clip in track.clips:
            key = (
                clip.source_id,
                round(float(clip.dst_in), 6),
                round(float(clip.dst_out), 6),
            )
            records = mirror.get(key)
            if records is None:
                report.not_applied.append(
                    {
                        "anchor_id": None,
                        "clip_id": clip.clip_id,
                        "reason": "dialogue_not_mirrored",
                    }
                )
                continue
            for index, record in enumerate(records):
                fragment = edl_plan.ClipSegment(
                    clip_id=f"{clip.clip_id}_{index}",
                    source_id=clip.source_id,
                    src_in=record["src_in"],
                    src_out=record["src_out"],
                    dst_in=record["dst_in"],
                    dst_out=record["dst_out"],
                    layer=0,
                    role=clip.role,
                    gain_db=0.0,
                )
                clips.append(
                    _gain_dialogue(
                        plan,
                        fragment,
                        target_lufs=target_lufs,
                        loudness_spans=loudness_spans,
                        report=report,
                        failed_sources=failed_sources,
                    )
                )
        audio_tracks.append(
            edl_plan.Track(track_id=track.track_id, clips=tuple(clips), role=track.role)
        )

    # -- music re-timed, other audio clamped: nothing keeps a stale dst ------ #
    retimed_tracks: List[edl_plan.Track] = []
    for track in audio_tracks:
        if track.role == "music":
            rebuilt = _retime_music(plan, track, duration, report)
        elif track.role == "dialogue":
            rebuilt = track
        else:
            rebuilt = _retime_audio_track(track, duration, report)
        if rebuilt is not None:
            retimed_tracks.append(rebuilt)
    audio_tracks = retimed_tracks

    # -- b_roll: anchored dst kept, uniform only when it no longer fits ---- #
    video_tracks: List[edl_plan.Track] = []
    for track_index, track in enumerate(plan.video_tracks):
        overlaid = _rebuild_broll(plan, track, duration, report)
        layer0 = layer0_by_track.get(track_index, [])
        clips = tuple(layer0) + tuple(overlaid)
        if not clips:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "reason": "track_empty",
                    "track_id": track.track_id,
                    "layer": min(
                        (int(getattr(clip, "layer", 0)) for clip in track.clips),
                        default=0,
                    ),
                }
            )
            continue
        video_tracks.append(
            edl_plan.Track(track_id=track.track_id, clips=clips, role=track.role)
        )
    if not video_tracks:
        raise VxError(
            "bridge: every video track became empty after the cuts (dropped: "
            + ", ".join(
                f"track_id={track.track_id!r} role={track.role!r}"
                for track in plan.video_tracks
            )
            + ")"
        )

    notes = tuple(plan.notes) + (
        f"bridge: {len(report.cuts)} tagli su silenzio, "
        f"{len(report.audio)} clip dialogati con gain dichiarato",
    )
    new_plan = _replace_plan(
        plan, tuple(video_tracks), tuple(audio_tracks), duration, notes=notes
    )
    errors = new_plan.validate()
    if errors:
        raise VxError("bridge: plan non valido dopo il taglio: " + "; ".join(errors))
    return new_plan, report


def _gain_dialogue(
    plan: edl_plan.EDLPlan,
    clip: edl_plan.ClipSegment,
    *,
    target_lufs: float,
    loudness_spans: Callable[[str, Sequence[Sequence[float]]], Mapping[str, Any]],
    report: BridgeReport,
    failed_sources: Any,
) -> edl_plan.ClipSegment:
    """Measure the used span only, then apply the clamped gain or declare it."""
    if clip.source_id in failed_sources:
        return clip
    ref = plan.sources.get(clip.source_id)
    path = str(getattr(ref, "path", "") or "")
    spans = [(float(clip.src_in), float(clip.src_out))]
    try:
        measured = loudness_spans(path, spans)
    except Exception as exc:  # noqa: BLE001 - declared, never fatal
        report.not_applied.append(
            {
                "anchor_id": None,
                "clip_id": clip.clip_id,
                "reason": f"analysis_failed: {exc}",
            }
        )
        return clip
    input_i = measured.get("input_i") if isinstance(measured, Mapping) else None
    if not _finite(input_i) or not (
        MEASURED_I_LIMITS[0] < float(input_i) < MEASURED_I_LIMITS[1]
    ):
        report.not_applied.append(
            {
                "anchor_id": None,
                "clip_id": clip.clip_id,
                "reason": "measurement_invalid",
                "input_i": input_i,
            }
        )
        return clip
    gain_unclamped = round(float(target_lufs) - float(input_i), 2)
    gain_db = round(_clamp(gain_unclamped, GAIN_LIMITS_DB[0], GAIN_LIMITS_DB[1]), 2)
    shortfall = round(abs(gain_unclamped - gain_db), 2)
    report.audio.append(
        {
            "clip_id": clip.clip_id,
            "measured_i": round(float(input_i), 3),
            "gain_db": gain_db,
            "target_i": float(target_lufs),
            "declared": f"target {float(target_lufs):g}",
            "shortfall_lu": shortfall,
        }
    )
    if shortfall > 1e-9:
        report.not_applied.append(
            {
                "anchor_id": None,
                "clip_id": clip.clip_id,
                "reason": "gain_beyond_limit",
                "required_gain_db": gain_unclamped,
                "gain_db": gain_db,
                "shortfall_lu": shortfall,
            }
        )
    return replace(clip, gain_db=gain_db)


def _replace_plan(
    plan: edl_plan.EDLPlan,
    video_tracks: Sequence[edl_plan.Track],
    audio_tracks: Sequence[edl_plan.Track],
    duration: float,
    *,
    notes: Optional[Sequence[str]] = None,
) -> edl_plan.EDLPlan:
    return edl_plan.EDLPlan(
        contract_version=plan.contract_version,
        fps=plan.fps,
        duration_sec=float(duration),
        aspect_ratio=plan.aspect_ratio,
        resolution=plan.resolution,
        sources=dict(plan.sources),
        video_tracks=tuple(video_tracks),
        audio_tracks=tuple(audio_tracks),
        notes=tuple(notes if notes is not None else plan.notes),
        ducking=plan.ducking,
    )


def _retime_music(
    plan: edl_plan.EDLPlan,
    track: edl_plan.Track,
    duration: float,
    report: BridgeReport,
) -> Optional[edl_plan.Track]:
    if track.role != "music" or not track.clips:
        return track
    clips: List[edl_plan.ClipSegment] = []
    for index, clip in enumerate(track.clips):
        length = min(
            _media_duration(plan, clip.source_id, float(clip.src_out)), duration
        )
        if index > 0:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": clip.clip_id,
                    "reason": "music_clip_dropped",
                }
            )
            continue
        if length <= 1e-6:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": clip.clip_id,
                    "reason": "music_outside_timeline",
                }
            )
            continue
        clips.append(
            edl_plan.ClipSegment(
                clip_id=clip.clip_id,
                source_id=clip.source_id,
                src_in=0.0,
                src_out=length,
                dst_in=0.0,
                dst_out=length,
                layer=0,
                role=clip.role,
                gain_db=clip.gain_db,
            )
        )
    if not clips:
        report.not_applied.append(
            {
                "anchor_id": None,
                "reason": "track_empty",
                "track_id": track.track_id,
                "layer": 0,
            }
        )
        return None
    return edl_plan.Track(
        track_id=track.track_id,
        clips=tuple(clips),
        role=track.role,
    )


def _retime_audio_track(
    track: edl_plan.Track,
    duration: float,
    report: BridgeReport,
) -> Optional[edl_plan.Track]:
    """Clamp an audio track the bridge does not otherwise own to the new duration.

    Its dst times came from the pre-bridge timeline and can now point past the
    end. ``src_out`` follows ``dst_out`` because ``validate`` requires the two
    durations to match within one frame.
    """
    clips: List[edl_plan.ClipSegment] = []
    for clip in track.clips:
        dst_in = min(float(clip.dst_in), duration)
        dst_out = min(float(clip.dst_out), duration)
        length = dst_out - dst_in
        if length <= 1e-9 or length < 0.05 - _EPS:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": clip.clip_id,
                    "track_id": track.track_id,
                    "reason": "audio_clip_dropped",
                    "length_sec": round(max(0.0, length), 3),
                }
            )
            continue
        if dst_in != float(clip.dst_in) or dst_out != float(clip.dst_out):
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": clip.clip_id,
                    "track_id": track.track_id,
                    "reason": "audio_track_retimed",
                    "dst_in": round(dst_in, 3),
                    "dst_out": round(dst_out, 3),
                }
            )
        clips.append(
            replace(
                clip,
                src_out=float(clip.src_in) + length,
                dst_in=dst_in,
                dst_out=dst_out,
            )
        )
    if not clips:
        report.not_applied.append(
            {
                "anchor_id": None,
                "reason": "track_empty",
                "track_id": track.track_id,
                "layer": min(
                    (int(getattr(clip, "layer", 0)) for clip in track.clips), default=0
                ),
            }
        )
        return None
    return edl_plan.Track(track_id=track.track_id, clips=tuple(clips), role=track.role)


def _rebuild_broll(
    plan: edl_plan.EDLPlan,
    track: edl_plan.Track,
    duration: float,
    report: BridgeReport,
) -> List[edl_plan.ClipSegment]:
    """Keep or re-place b_roll, declaring every drop and every mutated field.

    An existing ``dst_in`` that still fits after the cut is the anchor the
    payload bridge already honoured, so it is kept; only a clip that no longer
    fits falls back to the uniform placement of ``payload_to_edl_plan``.
    """
    sources = [clip for clip in track.clips if int(getattr(clip, "layer", 0)) >= 1]
    if not sources:
        return []
    count = len(sources)
    kept: List[edl_plan.ClipSegment] = []
    for index, clip in enumerate(sources):
        media = _media_duration(plan, clip.source_id, float(clip.src_out))
        length = min(float(clip.src_out) - float(clip.src_in), media, duration)
        if length <= 1e-6:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": clip.clip_id,
                    "reason": "broll_outside_timeline",
                }
            )
            continue
        max_start = max(0.0, duration - length)
        anchored = (
            _finite(clip.dst_in)
            and float(clip.dst_in) >= 0.0
            and float(clip.dst_in) + length <= duration + 1e-6
        )
        at = duration * (index + 1) / (count + 1)
        dst_in = float(clip.dst_in) if anchored else _clamp(at, 0.0, max_start)
        fade = clip.transition_in
        fade_cap = _transition_cap(length)
        fade_clamped = fade is not None and float(fade.duration_sec) > fade_cap + 1e-6
        if fade_clamped:
            fade = edl_plan.Transition(fade.type, fade_cap)
        if kept and dst_in < kept[-1].dst_out - 1e-6:
            report.broll.append(
                {
                    "asset_id": clip.source_id,
                    "at": round(dst_in, 3),
                    "anchor_id": None,
                    "accepted": False,
                }
            )
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": clip.clip_id,
                    "asset_id": clip.source_id,
                    "reason": "broll_overlap_after_cut",
                    "at": round(dst_in, 3),
                }
            )
            continue
        if fade_clamped:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": clip.clip_id,
                    "asset_id": clip.source_id,
                    "reason": "broll_fade_clamped",
                    "duration_sec": clip.transition_in.duration_sec,
                    "clamped_sec": round(fade_cap, 3),
                }
            )
        if clip.transition_out is not None:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": clip.clip_id,
                    "asset_id": clip.source_id,
                    "reason": "transition_out_dropped",
                    "transition": clip.transition_out.type,
                    "duration_sec": clip.transition_out.duration_sec,
                }
            )
        kept.append(
            edl_plan.ClipSegment(
                clip_id=clip.clip_id,
                source_id=clip.source_id,
                src_in=float(clip.src_in),
                src_out=float(clip.src_in) + length,
                dst_in=dst_in,
                dst_out=dst_in + length,
                layer=clip.layer,
                role=clip.role,
                transition_in=fade,
                gain_db=clip.gain_db,
            )
        )
        report.broll.append(
            {
                "asset_id": clip.source_id,
                "at": round(dst_in, 3),
                "anchor_id": None,
                "accepted": True,
            }
        )
    return kept
