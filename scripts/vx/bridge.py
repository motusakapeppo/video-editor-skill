"""vx.bridge — the analysis -> assembly bridge (references/26, section 4).

The bridge is the only place where a measurement becomes an edit. It is not a
pipeline: it takes a validated ``EDLPlan`` plus the audio analyses of its
sources and returns a **new** plan (the input is never mutated) and a
``BridgeReport`` that declares what was applied and what was not.

Five blocks live here:

* **A5, silence cuts.** Every layer-0 clip is cut against the non-speech spans
  of its own source. The spans come from the VAD ``speech_regions`` complement
  inside the clip window, and fall back to ``dead_air`` only when the VAD is
  absent. A removed interval that does not fall inside the union of those spans
  is a ``VxError``: the law of the evidence is not a convention here.
* **A3, per-clip loudness on the used segment.** Each dialogue fragment is
  measured on the kept span it actually plays, not on the whole source file. The
  gain is ``target_lufs - measured_i``; when that gain falls outside
  ``[-60, 24]`` it is clamped and the shortfall is declared (``shortfall_lu``),
  never absorbed. The gain is also peak-aware: a clip whose own true peak would
  cross ``bridge_gain_tp_ceiling_dbtp`` is clamped to the headroom it has, and
  the loudness it loses is declared (``loudness_shortfall_lu``). Without a
  usable ``input_tp`` the clamp is skipped and declared
  (``peak_headroom_unknown``), never guessed.
* **A4, transcript anchors.** Words above the confidence floor become
  ``word_span`` anchors with source time; consecutive anchors are grouped into
  captions, and each caption is remapped from source time to destination time
  through the kept fragments of its source. A caption that spans a cut splits
  into one entry per surviving piece. Every b-roll entry cites the anchors its
  destination window covers, and :func:`caption_ass` turns the captions into the
  ASS the renderer burns.
* **A6, reframe.** The face track of a source goes through
  ``autoframe.build_crop_plan`` unchanged, then each box is matched to the kept
  fragment that owns its source instant and shifted to destination time. A box
  that only exists inside a removed silence produces no keyframe: the track is
  the source of truth, the cut is not negotiable.
* **A7, restore.** Denoise and stabilisation are gated on the same findings the
  EditPlan path uses (``snr_db`` below ``noise_snr_low``, ``jitter.score`` at or
  above ``jitter_shaky``) and declared per clip with the measured value.

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
from .contract import THRESHOLDS, CropPlan, Cue, FaceTracks, VxError, plan_music_loop

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

_HARD_BREAK = ".!?…"
_TRAILING = "\"'”’»)]}"


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
    """The bridge's referto: eight lists, ``not_applied`` always present."""

    anchors: List[Anchor] = field(default_factory=list)
    cuts: List[Dict[str, Any]] = field(default_factory=list)
    captions: List[Dict[str, Any]] = field(default_factory=list)
    broll: List[Dict[str, Any]] = field(default_factory=list)
    reframe: List[Dict[str, Any]] = field(default_factory=list)
    restore: List[Dict[str, Any]] = field(default_factory=list)
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
            "restore": [dict(item) for item in self.restore],
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
# A4: transcript anchors -> captions
# --------------------------------------------------------------------------- #


def _word_field(item: Any, name: str) -> Any:
    """``subs.transcribe`` emits dicts; a stand-in transcript may emit attributes."""
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _ends_hard(text: str) -> bool:
    stripped = str(text).rstrip(_TRAILING)
    return bool(stripped) and stripped[-1] in _HARD_BREAK


def _midpoint(anchor: Anchor) -> float:
    last = float(anchor.t0 if anchor.t1 is None else anchor.t1)
    return (float(anchor.t0) + last) / 2.0


def _remap_span(
    t0: float,
    t1: float,
    fragments: Sequence[Tuple[float, float, float, float]],
) -> List[Tuple[float, float, float, float]]:
    """Clip a source window to each kept fragment and shift it to destination time.

    Each result is ``(src0, src1, dst0, dst1)``; a window that crosses a cut comes
    back as more than one piece, and a window that falls entirely inside a removed
    interval comes back empty.
    """
    pieces: List[Tuple[float, float, float, float]] = []
    for src0, src1, dst0, _ in fragments:
        first = max(float(t0), float(src0))
        last = min(float(t1), float(src1))
        if last - first <= _EPS:
            continue
        pieces.append(
            (first, last, float(dst0) + (first - src0), float(dst0) + (last - src0))
        )
    return pieces


def _anchor_ids_for(
    dst_in: float,
    dst_out: float,
    anchor_dst: Mapping[str, Sequence[Tuple[float, float]]],
) -> List[str]:
    """The anchors whose destination span overlaps the window, in declaration order."""
    found: List[str] = []
    for anchor_id, spans in anchor_dst.items():
        if any(end > dst_in + _EPS and start < dst_out - _EPS for start, end in spans):
            found.append(anchor_id)
    return found


def _broll_score(
    dst_in: float,
    dst_out: float,
    anchor_dst: Mapping[str, Sequence[Tuple[float, float]]],
    anchors_by_id: Mapping[str, Anchor],
) -> Tuple[Optional[float], float, Optional[float]]:
    """Score a B-roll window against the word anchors it illustrates.

    ``coverage`` is the union of the window's intersections with the remapped
    word spans, over the window length; ``confidence`` is the mean word
    confidence weighted by the seconds each anchor covers. Both are already
    measured values (``Anchor.confidence`` and the remapped spans), both live in
    ``[0, 1]``, so ``coverage * confidence`` is normalised with no rescaling.

    The result is ``(None, coverage, None)`` when no word anchor — or no anchor
    with a measured confidence — overlaps: silence anchors are not semantic
    evidence, and an unmeasured score is declared, never invented.
    """
    window = float(dst_out) - float(dst_in)
    if window <= _EPS:
        return None, 0.0, None
    intervals: List[Tuple[float, float]] = []
    weighted = 0.0
    measured = 0.0
    for anchor_id, spans in anchor_dst.items():
        anchor = anchors_by_id.get(anchor_id)
        if anchor is None or anchor.kind != "word_span" or anchor.confidence is None:
            continue
        for start, end in spans:
            first = max(float(dst_in), float(start))
            last = min(float(dst_out), float(end))
            if last - first <= _EPS:
                continue
            intervals.append((first, last))
            weighted += float(anchor.confidence) * (last - first)
            measured += last - first
    if measured <= _EPS:
        return None, 0.0, None
    merged: List[Tuple[float, float]] = []
    for first, last in sorted(intervals):
        if merged and first <= merged[-1][1] + _EPS:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    covered = sum(last - first for first, last in merged)
    coverage = _clamp(covered / window, 0.0, 1.0)
    confidence = _clamp(weighted / measured, 0.0, 1.0)
    return round(coverage * confidence, 4), round(coverage, 4), round(confidence, 4)


@dataclass(frozen=True)
class _BrollSlot:
    """A placed B-roll candidate, before the overlap policy decides its fate."""

    clip: Any
    index: int
    length: float
    dst_in: float
    anchor_ids: Tuple[str, ...]
    score: Optional[float]
    coverage: float
    confidence: Optional[float]
    fade: Any
    fade_clamped: bool

    def overlaps(self, other: "_BrollSlot") -> bool:
        return (
            self.dst_in < other.dst_in + other.length - 1e-6
            and other.dst_in < self.dst_in + self.length - 1e-6
        )


def _broll_overlap_winner(candidate: _BrollSlot, kept: _BrollSlot) -> _BrollSlot:
    """Higher score wins; a tie keeps the earlier window.

    A comparison needs two numbers, so when either score is missing the pair is
    treated as a tie: the earlier window survives and the drop is still
    declared with both scores, one of them ``None``.
    """
    if candidate.score is not None and kept.score is not None:
        if candidate.score > kept.score + _EPS:
            return candidate
        if kept.score > candidate.score + _EPS:
            return kept
    return (
        candidate
        if (candidate.dst_in, candidate.index)
        < (
            kept.dst_in,
            kept.index,
        )
        else kept
    )


def _caption_style(overrides: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Live thresholds, with per-job overrides validated on top of them."""
    style: Dict[str, Any] = {
        "min_confidence": float(THRESHOLDS["bridge_caption_min_confidence"]),
        "gap_sec": float(THRESHOLDS["bridge_caption_gap_ms"]) / 1000.0,
        "max_chars": int(THRESHOLDS["bridge_caption_max_chars"]),
        "bottom_inset_ratio": float(THRESHOLDS["bridge_caption_bottom_inset_ratio"]),
        "style": "clean",
    }
    if not overrides:
        return style
    for key in ("min_confidence", "gap_ms", "max_chars", "bottom_inset_ratio"):
        if overrides.get(key) is None:
            continue
        try:
            number = float(overrides[key])
        except (TypeError, ValueError) as exc:
            raise VxError(f"bridge: caption_style.{key} must be numeric") from exc
        if not math.isfinite(number):
            raise VxError(f"bridge: caption_style.{key} must be finite")
        if key == "gap_ms":
            style["gap_sec"] = number / 1000.0
        elif key == "max_chars":
            style["max_chars"] = int(number)
        else:
            style[key] = number
    if overrides.get("style") is not None:
        style["style"] = str(overrides["style"])
    if not 0.0 <= style["min_confidence"] <= 1.0:
        raise VxError("bridge: caption_style.min_confidence outside [0, 1]")
    if style["gap_sec"] < 0.0:
        raise VxError("bridge: caption_style.gap_ms must not be negative")
    if style["max_chars"] < 1:
        raise VxError("bridge: caption_style.max_chars must be positive")
    if not 0.0 <= style["bottom_inset_ratio"] <= 1.0:
        raise VxError("bridge: caption_style.bottom_inset_ratio outside [0, 1]")
    if style["style"] not in ("clean", "karaoke"):
        raise VxError("bridge: caption_style.style must be clean or karaoke")
    return style


def _caption_anchors(
    report: BridgeReport,
    transcripts: Mapping[str, Any],
    source_order: Sequence[str],
    fragments_by_source: Mapping[str, Sequence[Tuple[float, float, float, float]]],
    style: Mapping[str, Any],
    anchor_dst: Dict[str, List[Tuple[float, float]]],
) -> None:
    """Word anchors for every source, then captions remapped on the kept fragments.

    The rejected words are counted per source; a caption that crosses a cut is
    emitted once per surviving piece with the anchors of its own words; a caption
    that falls inside a removed interval is declared with its word count. The
    count of words lost to a cut travels on the first surviving piece.
    """
    min_confidence = float(style["min_confidence"])
    gap_sec = float(style["gap_sec"])
    max_chars = int(style["max_chars"])
    word_seq = 0
    caption_seq = 0

    for source_id in source_order:
        transcript = transcripts.get(source_id)
        if isinstance(transcript, BaseException):
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "source_id": source_id,
                    "reason": f"transcript_failed: {transcript}",
                }
            )
            continue
        if transcript is None:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "source_id": source_id,
                    "reason": f"transcript_failed: no transcript for {source_id}",
                }
            )
            continue

        fragments = fragments_by_source.get(source_id) or []
        rejected = 0
        group: List[Anchor] = []
        groups: List[List[Anchor]] = []
        source_anchors: List[Anchor] = []
        for item in getattr(transcript, "words", None) or ():
            token = str(_word_field(item, "word") or "").strip()
            if not token:
                continue
            start = _word_field(item, "start")
            end = _word_field(item, "end")
            if not (_finite(start) and _finite(end)):
                continue
            raw = _word_field(item, "probability")
            confidence = float(raw) if _finite(raw) else 0.0
            if confidence < min_confidence - _EPS:
                rejected += 1
                continue
            anchor = Anchor(
                anchor_id=f"a_word_{word_seq:04d}",
                kind="word_span",
                source_id=source_id,
                t0=float(start),
                t1=max(float(end), float(start)),
                text=token,
                confidence=round(confidence, 4),
            )
            word_seq += 1
            source_anchors.append(anchor)
            report.anchors.append(anchor)
            anchor_dst[anchor.anchor_id] = [
                (piece[2], piece[3])
                for piece in _remap_span(anchor.t0, anchor.t1, fragments)
            ]
            if group:
                gap = anchor.t0 - float(group[-1].t1)
                text = " ".join([word.text for word in group] + [anchor.text])
                if gap > gap_sec + _EPS or len(text) > max_chars:
                    groups.append(group)
                    group = []
            group.append(anchor)
            if _ends_hard(anchor.text):
                groups.append(group)
                group = []
        if group:
            groups.append(group)

        if rejected:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "source_id": source_id,
                    "reason": "word_below_min_confidence",
                    "count": rejected,
                    "min_confidence": min_confidence,
                }
            )
        outside = sum(
            1 for anchor in source_anchors if not anchor_dst.get(anchor.anchor_id)
        )
        if outside:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "source_id": source_id,
                    "reason": "word_outside_timeline",
                    "count": outside,
                }
            )

        for group in groups:
            first = float(group[0].t0)
            last = float(group[-1].t0 if group[-1].t1 is None else group[-1].t1)
            surviving: List[Tuple[Tuple[float, float, float, float], List[Anchor]]] = []
            for piece in _remap_span(first, last, fragments):
                src0, src1 = piece[0], piece[1]
                assigned = [
                    anchor
                    for anchor in group
                    if src0 - _EPS <= _midpoint(anchor) <= src1 + _EPS
                ]
                if assigned:
                    surviving.append((piece, assigned))
            if not surviving:
                report.not_applied.append(
                    {
                        "anchor_id": None,
                        "source_id": source_id,
                        "caption_id": f"cap_{caption_seq:04d}",
                        "reason": "caption_in_silence",
                        "text": " ".join(anchor.text for anchor in group),
                        "words_dropped": len(group),
                    }
                )
                caption_seq += 1
                continue
            dropped = len(group) - sum(len(words) for _, words in surviving)
            for index, (piece, words) in enumerate(surviving):
                src0, src1, dst0, _ = piece
                head, tail = words[0], words[-1]
                tail_end = float(tail.t0 if tail.t1 is None else tail.t1)
                dst_in = dst0 + max(0.0, float(head.t0) - src0)
                dst_out = dst0 + min(src1 - src0, tail_end - src0)
                if dst_out - dst_in <= _EPS:
                    continue
                report.captions.append(
                    {
                        "caption_id": f"cap_{caption_seq:04d}",
                        "dst_in": round(dst_in, 3),
                        "dst_out": round(dst_out, 3),
                        "text": " ".join(anchor.text for anchor in words).strip(),
                        "anchor_ids": [anchor.anchor_id for anchor in words],
                        "words_dropped": dropped if index == 0 else 0,
                    }
                )
                caption_seq += 1


def caption_ass(
    captions: Sequence[Mapping[str, Any]],
    dst: str | Path,
    *,
    aspect: str = "9:16",
    out_w: int = 1080,
    out_h: int = 1920,
    caption_style: Optional[Mapping[str, Any]] = None,
    font_family: Optional[str] = None,
) -> Optional[Path]:
    """Write the ASS burned by the EDL render; ``None`` when no caption survives.

    The look comes from :func:`vx.subs.write_ass` unchanged: same font, size,
    outline and colours as every other subtitle this engine burns. Only
    ``MarginV`` is set here, from the canvas and not from the source geometry or
    the platform safe zone: ``round(out_h * ratio)`` clamped to
    ``[24, out_h // 4]``.

    ``font_family`` is the ``Fontname`` the caller chose for this job (see
    ``cli._job_font``); ``None`` keeps the preset family, so a caller without a
    choice produces the same ASS as before. The burn filter must point at the
    directory holding that font (``fonts_dir=`` in ``render_edl``).
    """
    from . import subs

    style = _caption_style(caption_style)
    cues = _caption_cues(captions, style)
    if not cues:
        return None
    margin_v = min(
        int(out_h) // 4,
        max(24, int(round(float(out_h) * float(style["bottom_inset_ratio"])))),
    )
    path = subs.write_ass(
        cues,
        dst,
        aspect=aspect,
        style=str(style["style"]),
        out_w=int(out_w),
        out_h=int(out_h),
        margin_v=margin_v,
        font_family=font_family or None,
    )
    return Path(path)


def _caption_cues(
    captions: Sequence[Mapping[str, Any]],
    style: Mapping[str, Any],
) -> List[Cue]:
    """Cues from the bridge captions: the one place that decides what is a cue."""
    cues: List[Cue] = []
    for item in captions or ():
        text = " ".join(str(item.get("text") or "").split())
        start, end = item.get("dst_in"), item.get("dst_out")
        if not text or not (_finite(start) and _finite(end)):
            continue
        if float(end) - float(start) <= _EPS:
            continue
        cues.append(
            Cue(
                start=float(start),
                end=float(end),
                text=text,
                words=[],
                style=str(style["style"]),
                line_count=1,
            )
        )
    return cues


def caption_srt(
    captions: Sequence[Mapping[str, Any]],
    dst: str | Path,
    *,
    caption_style: Optional[Mapping[str, Any]] = None,
) -> Optional[Path]:
    """Write the sidecar SRT from the same cues the master burns.

    The burned ASS and the delivered SRT must say the same words at the same
    times, so both come from :func:`_caption_cues` and cannot drift. Without
    that, a master with captions on screen ships next to an SRT that claims
    there was no speech.
    """
    from . import subs

    cues = _caption_cues(captions, _caption_style(caption_style))
    if not cues:
        return None
    return Path(subs.write_srt(cues, dst))


# --------------------------------------------------------------------------- #
# A6: reframe driven by the face tracks
# --------------------------------------------------------------------------- #

# ``afftdn`` accepts its noise floor in this window; outside it the filter is
# not an expression of the measurement any more and plan.py's passa-alto stands
# in. These are ffmpeg's own bounds, not a tunable: nothing to calibrate.
AFFTDN_NF_LIMITS_DB = (-80.0, -20.0)


@dataclass(frozen=True)
class _FaceSource:
    """One source's crop plan, or the reason there is none."""

    crop: Optional[CropPlan] = None
    samples: Tuple[float, ...] = ()
    reason: str = ""
    detail: str = ""


def _face_entry(item: Any) -> Tuple[Optional[Dict[str, Any]], str, str]:
    """Normalise one ``faces`` value into ``(payload, reason, detail)``.

    A ``FaceTracks`` carries the boxes but not the frame size, and the boxes are
    pixels: without ``width``/``height`` no crop can be placed in the normalised
    coordinates the renderer animates, so that case is declared instead of
    guessed. A mapping may carry both (``tracks`` + ``width``/``height``) plus
    the scene cuts of the source.
    """
    if isinstance(item, BaseException):
        return None, "reframe_face_analysis_failed", str(item)
    if item is None:
        return None, "reframe_no_face_track", "nessuna analisi volti per la sorgente"
    if isinstance(item, Mapping):
        tracks = item.get("tracks")
        width, height = item.get("width"), item.get("height")
        framing, span, cuts = (
            item.get("framing"),
            item.get("span_x_ratio"),
            item.get("scene_cuts"),
        )
    else:
        tracks = getattr(item, "tracks", None)
        width, height = getattr(item, "width", None), getattr(item, "height", None)
        framing = getattr(item, "framing", None)
        span = getattr(item, "span_x_ratio", None)
        cuts = getattr(item, "scene_cuts", None)
    rows = [dict(row) for row in (tracks or ()) if isinstance(row, Mapping)]
    if not rows:
        return None, "reframe_no_face_track", "l'analisi volti non ha tracce"
    if not (_finite(width) and _finite(height)) or (
        float(width) <= 0.0 or float(height) <= 0.0
    ):
        return None, "reframe_no_source_geometry", "traccia volti senza geometria"
    return (
        {
            "tracks": rows,
            "framing": str(framing or "center"),
            "span_x_ratio": float(span) if _finite(span) else 0.0,
            "scene_cuts": [float(c) for c in (cuts or ()) if _finite(c)],
            "width": int(round(float(width))),
            "height": int(round(float(height))),
        },
        "",
        "",
    )


def _source_crop(
    payload: Mapping[str, Any],
    *,
    aspect: str,
    extra_cuts: Sequence[float] = (),
) -> Tuple[Optional[CropPlan], str, str]:
    """Reuse ``autoframe.build_crop_plan``: no second keyframe engine here."""
    from . import autoframe  # noqa: PLC0415

    cuts = sorted(
        {
            round(float(c), 4)
            for c in list(payload.get("scene_cuts") or ()) + list(extra_cuts)
            if _finite(c) and float(c) > 0.0
        }
    )
    tracks = FaceTracks(
        tracks=list(payload["tracks"]),
        framing=str(payload["framing"]),
        span_x_ratio=float(payload["span_x_ratio"]),
    )
    crop = autoframe.build_crop_plan(
        tracks,
        int(payload["width"]),
        int(payload["height"]),
        aspect=aspect,
        scene_cuts=cuts,
    )
    if crop.mode == "fit":
        return None, "reframe_framing_fit", "framing fit: il crop taglierebbe un volto"
    if crop.mode not in ("track", "lock"):
        return None, "reframe_static_subject", f"crop mode {crop.mode}"
    return crop, "", ""


def _source_faces(item: Any, *, aspect: str, scene_cuts: Any = None) -> _FaceSource:
    payload, reason, detail = _face_entry(item)
    if payload is None:
        return _FaceSource(reason=reason, detail=detail)
    extra = [float(c) for c in (scene_cuts or ()) if _finite(c)]
    crop, reason, detail = _source_crop(payload, aspect=aspect, extra_cuts=extra)
    if crop is None:
        return _FaceSource(reason=reason, detail=detail)
    stamps = sorted(
        {
            round(float(row["t"]), 4)
            for track in payload["tracks"]
            for row in (track.get("samples") or ())
            if isinstance(row, Mapping) and _finite(row.get("t"))
        }
    )
    return _FaceSource(crop=crop, samples=tuple(stamps))


def _keyframe_scale(crop: CropPlan, out_w: int, out_h: int) -> float:
    """Crop width as a fraction of the output canvas after the cover scale.

    ``build_crop_plan`` keeps ``crop_w`` fixed for the whole clip (ffmpeg
    evaluates a ``crop`` size once), so one scale per clip is the whole truth
    and the renderer animates the centre only.
    """
    src_w, src_h = float(crop.src_w), float(crop.src_h)
    if src_w <= 0.0 or src_h <= 0.0 or out_w <= 0 or out_h <= 0:
        return 0.0
    cover = max(float(out_w) / src_w, float(out_h) / src_h)
    return _clamp(float(crop.crop_w) * cover / float(out_w), 0.0, 1.0)


def _box_owner(t: float, fragments: Sequence[Tuple[float, float]]) -> int:
    """The kept fragment that owns a source instant, or -1 for a removed gap."""
    for index, (src0, src1) in enumerate(fragments):
        if src0 - _EPS <= t <= src1 + _EPS:
            return index
    return -1


def _fragment_keyframes(
    crop: CropPlan,
    fragment: Tuple[float, float, float, float],
    scale: float,
) -> List[Dict[str, float]]:
    """Source-time boxes of one fragment, shifted to destination time.

    A box outside the fragment produces nothing: the destination instants are
    the remapped ones, never the source ones, and a sample in a removed silence
    has no destination at all.
    """
    src0, src1, dst0, dst1 = fragment
    src_w, src_h = float(crop.src_w), float(crop.src_h)
    half_w, half_h = float(crop.crop_w) / 2.0, float(crop.crop_h) / 2.0
    by_t: Dict[float, Dict[str, float]] = {}
    for box in crop.boxes or ():
        t = float(box["t"])
        if t < src0 - _EPS or t > src1 + _EPS:
            continue
        dst_t = round(_clamp(dst0 + (t - src0), dst0, dst1), 3)
        by_t[dst_t] = {
            "dst_t": dst_t,
            "x": round(_clamp((float(box["x"]) + half_w) / src_w, 0.0, 1.0), 4),
            "y": round(_clamp((float(box["y"]) + half_h) / src_h, 0.0, 1.0), 4),
            "scale": round(scale, 4),
        }
    return [by_t[key] for key in sorted(by_t)]


def _declare_reframe(
    report: BridgeReport,
    clip: Any,
    created: Sequence[Any],
    state: _FaceSource,
    fragments: Sequence[Tuple[float, float]],
    *,
    out_w: int,
    out_h: int,
) -> None:
    """One ``reframe`` entry per kept fragment, plus every skip declared."""
    crop = state.crop
    if crop is None:
        for new_clip in created:
            report.reframe.append(
                {
                    "clip_id": new_clip.clip_id,
                    "source_id": clip.source_id,
                    "frames": 0,
                    "keyframes": [],
                    "declared": f"reframe non applicato: {state.reason}",
                }
            )
            entry: Dict[str, Any] = {
                "anchor_id": None,
                "clip_id": new_clip.clip_id,
                "source_id": clip.source_id,
                "reason": state.reason,
            }
            if state.detail:
                entry["detail"] = state.detail
            report.not_applied.append(entry)
        return

    scale = _keyframe_scale(crop, out_w, out_h)
    owners: Dict[int, int] = {}
    orphans = 0
    for box in crop.boxes or ():
        owner = _box_owner(float(box["t"]), fragments)
        if owner >= 0:
            owners[owner] = owners.get(owner, 0) + 1
    clip_in, clip_out = float(clip.src_in), float(clip.src_out)
    for stamp in state.samples:
        if not (clip_in - _EPS <= stamp <= clip_out + _EPS):
            continue
        if _box_owner(stamp, fragments) < 0:
            orphans += 1
    if orphans:
        report.not_applied.append(
            {
                "anchor_id": None,
                "clip_id": clip.clip_id,
                "source_id": clip.source_id,
                "reason": "reframe_keyframes_in_silence",
                "count": orphans,
                "declared": "campioni dentro un silenzio tagliato: nessun keyframe",
            }
        )

    for index, new_clip in enumerate(created):
        fragment = (
            float(new_clip.src_in),
            float(new_clip.src_out),
            float(new_clip.dst_in),
            float(new_clip.dst_out),
        )
        keyframes = _fragment_keyframes(crop, fragment, scale)
        reason, detail = "", ""
        if scale <= 0.0:
            reason, detail = "reframe_degenerate_keyframes", "scala nulla"
        elif len(keyframes) < 2:
            reason = "reframe_degenerate_keyframes"
            detail = f"{len(keyframes)} keyframe: serve una animazione, non un fermo"
        elif all(
            abs(item["x"] - keyframes[0]["x"]) < 1e-6
            and abs(item["y"] - keyframes[0]["y"]) < 1e-6
            for item in keyframes
        ):
            reason = "reframe_degenerate_keyframes"
            detail = "centro identico su ogni keyframe"
        if reason:
            keyframes = []
        frames = owners.get(index, 0)
        report.reframe.append(
            {
                "clip_id": new_clip.clip_id,
                "source_id": clip.source_id,
                "frames": frames,
                "keyframes": keyframes,
                "declared": (
                    f"reframe: {len(keyframes)} keyframe da {frames} campioni, "
                    f"scala {scale:.3f}, crop {int(crop.crop_w)}x{int(crop.crop_h)}"
                    if keyframes
                    else f"reframe non applicato: {detail}"
                ),
            }
        )
        if reason:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": new_clip.clip_id,
                    "source_id": clip.source_id,
                    "reason": reason,
                    "keyframes": len(keyframes),
                    "detail": detail,
                }
            )


# --------------------------------------------------------------------------- #
# A7: denoise and stabilisation, single pass, only on a finding
# --------------------------------------------------------------------------- #


def _motion_field(item: Any, *names: str) -> Any:
    """Read a nested field from a ``VideoAnalysis`` or from its ``to_dict()``."""
    if item is None or isinstance(item, BaseException):
        return None
    value: Any = item
    for name in names:
        if value is None:
            return None
        if isinstance(value, Mapping):
            value = value.get(name)
        else:
            value = getattr(value, name, None)
    return value


def _denoise_filter(noise_dbfs: Optional[float]) -> str:
    """The single-pass denoise of the EDL path, driven by the measurement."""
    low, high = AFFTDN_NF_LIMITS_DB
    if noise_dbfs is not None and low <= noise_dbfs <= high:
        return f"afftdn=nf={noise_dbfs:.1f}"
    return "highpass=f=80"


def _restore_sentence(
    denoise: bool,
    stabilize: bool,
    applied: bool,
    snr: Optional[float],
    floor: Optional[float],
    jitter: Optional[float],
    snr_thr: float,
    jitter_thr: float,
) -> str:
    parts: List[str] = []
    if denoise:
        parts.append(
            f"denoise afftdn nf={floor:.1f} (snr {snr:.1f} < {snr_thr:.1f} dB)"
        )
    else:
        parts.append(
            f"denoise assente (snr {snr:.1f} >= {snr_thr:.1f} dB)"
            if snr is not None
            else "denoise assente (snr non misurato)"
        )
    if applied:
        parts.append(f"stabilize deshake applicato (jitter {jitter:.2f} >= {jitter_thr:.2f})")
    elif stabilize:
        # Il render sa applicare deshake; qui non parte perche' manca la
        # dichiarazione d'intento. Il movimento puo' essere voluto.
        parts.append(
            f"stabilize proposto (jitter {jitter:.2f} >= {jitter_thr:.2f}); "
            "non applicato senza intento dichiarato"
        )
    else:
        parts.append(
            f"stabilize assente (jitter {jitter:.2f} < {jitter_thr:.2f})"
            if jitter is not None
            else "stabilize assente (jitter non misurato)"
        )
    return "; ".join(parts)


def _declare_restore(
    report: BridgeReport,
    clip: Any,
    created: Sequence[Any],
    *,
    analysis: Any,
    motion: Any,
    intent: str = "propose",
) -> None:
    """Gate denoise on ``snr_db`` and stabilisation on ``jitter.score``.

    Both are the same thresholds ``plan.py`` uses for the EditPlan path:
    ``noise_snr_low`` (apply below) and ``jitter_shaky`` (apply at or above).
    A missing measurement is a declared skip with the value it should have had,
    never a silent filter.
    """
    snr_raw = _analysis_field(analysis, "snr_db")
    floor_raw = _analysis_field(analysis, "noise_floor_dbfs")
    jitter_raw = _motion_field(motion, "jitter", "score")
    snr = float(snr_raw) if _finite(snr_raw) else None
    floor = float(floor_raw) if _finite(floor_raw) else None
    jitter = float(jitter_raw) if _finite(jitter_raw) else None
    snr_thr = float(THRESHOLDS["noise_snr_low"])
    jitter_thr = float(THRESHOLDS["jitter_shaky"])
    denoise = snr is not None and snr < snr_thr
    stabilize = jitter is not None and jitter >= jitter_thr
    # D-24: un pan a mano libera e un tremore misurano uguale. Il render sa
    # applicare deshake, ma applicarlo senza sapere se il movimento era voluto
    # significa bloccare una panoramica. L'intento si dichiara.
    applied = stabilize and str(intent) == "apply"
    filter_text = _denoise_filter(floor) if denoise else None
    declared = _restore_sentence(
        denoise, stabilize, applied, snr, floor, jitter, snr_thr, jitter_thr
    )

    for new_clip in created:
        report.restore.append(
            {
                "clip_id": new_clip.clip_id,
                "source_id": clip.source_id,
                "denoise": denoise,
                "stabilize": applied,
                "stabilize_proposed": stabilize,
                "stabilize_applied": applied,
                "stabilize_intent": str(intent),
                "noise_dbfs": round(floor, 2) if floor is not None else None,
                "jitter": round(jitter, 3) if jitter is not None else None,
                "snr_db": round(snr, 2) if snr is not None else None,
                "noise_threshold_db": snr_thr,
                "jitter_threshold": jitter_thr,
                "denoise_filter": filter_text,
                "declared": declared,
            }
        )
        if not denoise:
            entry: Dict[str, Any] = {
                "anchor_id": None,
                "clip_id": new_clip.clip_id,
                "source_id": clip.source_id,
                "reason": "denoise_not_justified",
                "snr_db": round(snr, 2) if snr is not None else None,
                "noise_dbfs": round(floor, 2) if floor is not None else None,
                "threshold_db": snr_thr,
            }
            if isinstance(analysis, BaseException):
                entry["detail"] = f"analysis_failed: {analysis}"
            report.not_applied.append(entry)
        if not applied:
            entry = {
                "anchor_id": None,
                "clip_id": new_clip.clip_id,
                "source_id": clip.source_id,
                "reason": (
                    "stabilize_not_declared" if stabilize else "stabilize_not_justified"
                ),
                "jitter": round(jitter, 3) if jitter is not None else None,
                "threshold": jitter_thr,
            }
            if isinstance(motion, BaseException):
                entry["detail"] = f"analysis_failed: {motion}"
            report.not_applied.append(entry)


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #


def build(
    plan: edl_plan.EDLPlan,
    analyses: Mapping[str, Any],
    *,
    target_lufs: float,
    loudness_spans: Callable[[str, Sequence[Sequence[float]]], Mapping[str, Any]],
    transcripts: Optional[Mapping[str, Any]] = None,
    caption_style: Optional[Mapping[str, Any]] = None,
    faces: Optional[Mapping[str, Any]] = None,
    motion: Optional[Mapping[str, Any]] = None,
    stabilize_intent: Optional[str] = None,
) -> Tuple[edl_plan.EDLPlan, BridgeReport]:
    """Cut the a_roll chain on measured silence and gain it per used segment.

    ``analyses`` maps ``source_id`` to an audio analysis: either a
    ``contract.AudioAnalysis``, its ``to_dict()`` form, or the exception that
    prevented the analysis. A failed analysis never crashes the bridge: the clip
    stays whole, un-gained, and the failure is declared in ``not_applied``.

    ``transcripts`` maps ``source_id`` to a ``contract.Transcript`` or to the
    exception that prevented the transcription; ``None`` means captions are off
    and the report says so. ``caption_style`` overrides the live
    ``bridge_caption_*`` thresholds for this run only.

    ``faces`` maps ``source_id`` to a ``FaceTracks`` (or its ``to_dict()`` form,
    or a mapping that adds ``width``/``height``/``scene_cuts``) or to the
    exception that prevented detection; ``None`` means reframe is off. Every
    a_roll fragment gets a ``reframe`` entry: keyframes only where a kept
    fragment carries a track sample, a declared ``not_applied`` when it does not.

    ``motion`` maps ``source_id`` to a ``VideoAnalysis`` (or its ``to_dict()``
    form) or to the exception; ``None`` means the restore gate is off. With it,
    every a_roll fragment declares ``denoise`` and ``stabilize`` and the finding
    that justified them. Both default off so a caller that knows nothing about
    faces or motion gets exactly the plan and the report it got before.
    """
    if not isinstance(plan, edl_plan.EDLPlan):
        raise VxError("bridge.build expects an EDLPlan")
    if not _finite(target_lufs):
        raise VxError("bridge.build: target_lufs must be finite")
    style = _caption_style(caption_style)
    min_fragment = float(THRESHOLDS["bridge_min_fragment_ms"]) / 1000.0
    if min_fragment <= 0.0:
        raise VxError("bridge: bridge_min_fragment_ms must be positive")
    pad = float(THRESHOLDS["silence_pad"])
    silence_min = float(THRESHOLDS["silence_min"])

    report = BridgeReport()
    out_w, out_h = (int(value) for value in plan.resolution)
    face_cache: Dict[str, _FaceSource] = {}
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
    fragments_by_source: Dict[str, List[Tuple[float, float, float, float]]] = {}
    anchor_dst: Dict[str, List[Tuple[float, float]]] = {}
    source_order: List[str] = []
    anchor_seq = 0
    cursor = 0.0

    for track_index, clip in chain:
        analysis = analyses.get(clip.source_id) if analyses else None
        fragments: List[Tuple[float, float]]
        clip_removals: List[Dict[str, Any]] = []
        clip_applied: set = set()
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
            clip_removals = all_removals
            clip_applied = applied
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
        created: List[edl_plan.ClipSegment] = []
        clip_fragments: List[Tuple[float, float, float, float]] = []
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
            created.append(new_clip)
            clip_fragments.append(
                (
                    float(first),
                    float(last),
                    float(new_clip.dst_in),
                    float(new_clip.dst_out),
                )
            )
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
        fragments_by_source.setdefault(clip.source_id, []).extend(clip_fragments)
        if clip.source_id not in source_order:
            source_order.append(clip.source_id)
        for removal in clip_removals:
            anchor_dst[removal["anchor_id"]] = (
                []
                if removal["anchor_id"] in clip_applied
                else [
                    (piece[2], piece[3])
                    for piece in _remap_span(
                        removal["t0"], removal["t1"], clip_fragments
                    )
                ]
            )

        # -- A6/A7: this source's measurements, declared per kept fragment --- #
        if faces is not None:
            if clip.source_id not in face_cache:
                face_cache[clip.source_id] = _source_faces(
                    faces.get(clip.source_id),
                    aspect=str(plan.aspect_ratio),
                    scene_cuts=_motion_field(
                        motion.get(clip.source_id) if motion else None,
                        "scene_cuts",
                    ),
                )
            _declare_reframe(
                report,
                clip,
                created,
                face_cache[clip.source_id],
                fragments,
                out_w=out_w,
                out_h=out_h,
            )
        if motion is not None:
            _declare_restore(
                report,
                clip,
                created,
                analysis=analysis,
                motion=motion.get(clip.source_id),
                intent=str(stabilize_intent or "propose"),
            )

    duration = cursor
    if duration <= 1e-6:
        raise VxError("bridge: the a_roll chain is empty after the cuts")

    # -- A4: word anchors and captions, remapped on the kept fragments ------ #
    if transcripts is None:
        report.not_applied.append({"anchor_id": None, "reason": "captions_disabled"})
    else:
        _caption_anchors(
            report, transcripts, source_order, fragments_by_source, style, anchor_dst
        )

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
    music_specs = {
        str(getattr(spec, "target_role", "")): spec
        for spec in getattr(plan, "ducking_tracks", ()) or ()
    }
    default_spec = getattr(plan, "ducking", None)
    retimed_tracks: List[edl_plan.Track] = []
    for track in audio_tracks:
        is_music = track.role == "music" or str(track.role).startswith("music_")
        if is_music:
            rebuilt = _retime_music(
                plan,
                track,
                duration,
                report,
                music_specs.get(track.role, default_spec),
            )
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
        overlaid = _rebuild_broll(plan, track, duration, report, anchor_dst=anchor_dst)
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

    # -- A10: camera ops follow their footage, or are declared gone ---------- #
    _retime_camera(plan, video_tracks, report)

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
    """Measure the used span only, then apply the loudness gain within its peak headroom."""
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
    input_tp_raw = measured.get("input_tp") if isinstance(measured, Mapping) else None
    input_tp = float(input_tp_raw) if _finite(input_tp_raw) else None
    ceiling_tp = float(THRESHOLDS["bridge_gain_tp_ceiling_dbtp"])
    peak_limited = False
    gain_max_by_peak: Optional[float] = None
    gain_pre_clamp = gain_unclamped
    if input_tp is None:
        report.not_applied.append(
            {
                "anchor_id": None,
                "clip_id": clip.clip_id,
                "reason": "peak_headroom_unknown",
            }
        )
    else:
        gain_max_by_peak = round(ceiling_tp - input_tp, 2)
        if gain_max_by_peak < gain_unclamped:
            peak_limited = True
            gain_pre_clamp = gain_max_by_peak
    gain_db = round(_clamp(gain_pre_clamp, GAIN_LIMITS_DB[0], GAIN_LIMITS_DB[1]), 2)
    shortfall = round(abs(gain_pre_clamp - gain_db), 2)
    lra_raw = measured.get("input_lra") if isinstance(measured, Mapping) else None
    input_lra = float(lra_raw) if _finite(lra_raw) else None
    lra_threshold = float(THRESHOLDS["lra_wide"])
    compress = input_lra is not None and input_lra > lra_threshold
    entry: Dict[str, Any] = {
        "clip_id": clip.clip_id,
        "measured_i": round(float(input_i), 3),
        "gain_db": gain_db,
        "target_i": float(target_lufs),
        "declared": f"target {float(target_lufs):g}",
        "shortfall_lu": shortfall,
        "input_lra": round(input_lra, 2) if input_lra is not None else None,
        "compress": compress,
    }
    loudness_shortfall = 0.0
    if peak_limited:
        loudness_shortfall = round(float(target_lufs) - (float(input_i) + gain_db), 2)
        entry["peak_limited"] = True
        entry["input_tp"] = round(float(input_tp), 3)
        entry["loudness_shortfall_lu"] = loudness_shortfall
    report.audio.append(entry)
    if peak_limited:
        report.not_applied.append(
            {
                "anchor_id": None,
                "clip_id": clip.clip_id,
                "reason": "gain_limited_by_peak",
                "required_gain_db": gain_unclamped,
                "gain_db": gain_db,
                "gain_max_by_peak_db": gain_max_by_peak,
                "input_tp": round(float(input_tp), 3),
                "ceiling_dbtp": ceiling_tp,
                "loudness_shortfall_lu": loudness_shortfall,
            }
        )
    if shortfall > 1e-9:
        report.not_applied.append(
            {
                "anchor_id": None,
                "clip_id": clip.clip_id,
                "reason": "gain_beyond_limit",
                "required_gain_db": gain_pre_clamp,
                "gain_db": gain_db,
                "shortfall_lu": shortfall,
            }
        )
    if compress:
        entry["declared_compress"] = (
            f"compress applicato: LRA {input_lra:.1f} LU > {lra_threshold:g} LU"
        )
    elif input_lra is None:
        report.not_applied.append(
            {
                "anchor_id": None,
                "clip_id": clip.clip_id,
                "reason": "compress_unmeasured",
                "input_lra": None,
                "threshold_lra": lra_threshold,
                "declared": (
                    f"compress assente: LRA non misurato (soglia {lra_threshold:g} LU)"
                ),
            }
        )
    else:
        report.not_applied.append(
            {
                "anchor_id": None,
                "clip_id": clip.clip_id,
                "reason": "compress_declined",
                "input_lra": round(input_lra, 2),
                "threshold_lra": lra_threshold,
                "declared": (
                    f"compress assente: LRA {input_lra:.1f} LU <= {lra_threshold:g} LU"
                ),
            }
        )
    return replace(clip, gain_db=gain_db, compress=compress)


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
        ducking_tracks=plan.ducking_tracks,
    )


def _retime_music(
    plan: edl_plan.EDLPlan,
    track: edl_plan.Track,
    duration: float,
    report: BridgeReport,
    spec: Any = None,
) -> Optional[edl_plan.Track]:
    is_music = track.role == "music" or str(track.role).startswith("music_")
    if not is_music or not track.clips:
        return track
    clips: List[edl_plan.ClipSegment] = []
    for index, clip in enumerate(track.clips):
        media = _media_duration(plan, clip.source_id, float(clip.src_out))
        # The bed keeps its media duration: a track shorter than the timeline is
        # looped by the render graph, never stretched or cut down to it. Only a
        # media longer than the timeline is clamped, because `dst_out` cannot
        # exceed `duration_sec` and `validate` keeps src and dst durations equal.
        length = min(media, duration)
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
        loop = plan_music_loop(
            length,
            float(duration),
            float(THRESHOLDS["music_loop_crossfade_sec"]),
        )
        if loop.looped:
            declared = (
                f"musica in loop: {loop.copies} copie, {loop.copies - 1} giunzioni "
                f"in acrossfade da {loop.crossfade_sec:.3f}s"
            )
        elif loop.refused:
            declared = f"loop rifiutato ({loop.refused}), passata singola"
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": clip.clip_id,
                    "reason": "music_loop_refused",
                    "detail": loop.refused,
                }
            )
        else:
            declared = f"musica singola: {length:.3f}s su {float(duration):.3f}s"
        report.audio.append(
            {
                "clip_id": clip.clip_id,
                "track_id": track.track_id,
                "role": track.role,
                "looped": loop.looped,
                "loops": loop.copies,
                "crossfade_sec": loop.crossfade_sec,
                "declared": declared,
                "ducking_threshold": (
                    float(spec.threshold) if spec is not None else None
                ),
                "ducking_ratio": float(spec.ratio) if spec is not None else None,
            }
        )
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
    *,
    anchor_dst: Optional[Mapping[str, Sequence[Tuple[float, float]]]] = None,
) -> List[edl_plan.ClipSegment]:
    """Keep or re-place b_roll, declaring every drop, every score and every mutation.

    Placement: an existing ``dst_in`` that still fits after the cut is the anchor
    the payload bridge already honoured, so it is kept; only a clip that no
    longer fits falls back to the uniform placement of ``payload_to_edl_plan``,
    clamped to ``[0, duration - length]``.

    Score (A8): every candidate is scored against the word anchors its
    destination window covers, ``coverage * confidence`` from
    :func:`_broll_score`. Below ``broll_min_score`` the clip is dropped as
    ``broll_below_threshold`` carrying the score and the anchor ids. A window
    with no word anchor to score is kept and declared
    ``broll_score_unavailable``: a score is never invented.

    Overlap (A11), deterministic: candidates are processed in
    ``(dst_in, declaration index)`` order and each is compared against the whole
    kept set, never against the last processed clip. When two windows overlap
    the higher score survives; the other is dropped as
    ``broll_overlap_after_cut`` naming both clip ids and both scores. Equal
    scores — and unmeasurable pairs, where one score is ``None`` — keep the
    earlier window. An evicted candidate leaves the kept set for good, so it
    cannot reappear, and the survivor is checked again against what remains.

    Every entry carries ``anchor_ids``: the word and silence anchors whose
    destination span overlaps the clip window. An accepted clip that covers no
    anchor is declared as ``broll_without_anchor``.
    """
    spans = anchor_dst or {}
    anchors_by_id: Dict[str, Anchor] = {
        anchor.anchor_id: anchor
        for anchor in report.anchors
        if isinstance(anchor, Anchor)
    }
    min_score = float(THRESHOLDS["broll_min_score"])
    sources = [clip for clip in track.clips if int(getattr(clip, "layer", 0)) >= 1]
    if not sources:
        return []
    count = len(sources)
    slots: List[_BrollSlot] = []
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
        anchor_ids = _anchor_ids_for(dst_in, dst_in + length, spans)
        score, coverage, confidence = _broll_score(
            dst_in, dst_in + length, spans, anchors_by_id
        )
        fade = clip.transition_in
        fade_cap = _transition_cap(length)
        fade_clamped = fade is not None and float(fade.duration_sec) > fade_cap + 1e-6
        if fade_clamped:
            fade = edl_plan.Transition(fade.type, fade_cap)
        slots.append(
            _BrollSlot(
                clip=clip,
                index=index,
                length=length,
                dst_in=dst_in,
                anchor_ids=tuple(anchor_ids),
                score=score,
                coverage=coverage,
                confidence=confidence,
                fade=fade,
                fade_clamped=fade_clamped,
            )
        )

    below = [
        slot
        for slot in slots
        if slot.score is not None and slot.score < min_score - _EPS
    ]
    candidates = [
        slot for slot in slots if slot.score is None or slot.score >= min_score - _EPS
    ]
    for slot in sorted(below, key=lambda item: (item.dst_in, item.index)):
        report.broll.append(
            {
                "asset_id": slot.clip.source_id,
                "at": round(slot.dst_in, 3),
                "anchor_id": slot.anchor_ids[0] if slot.anchor_ids else None,
                "anchor_ids": list(slot.anchor_ids),
                "score": slot.score,
                "accepted": False,
            }
        )
        report.not_applied.append(
            {
                "anchor_id": slot.anchor_ids[0] if slot.anchor_ids else None,
                "clip_id": slot.clip.clip_id,
                "asset_id": slot.clip.source_id,
                "reason": "broll_below_threshold",
                "score": slot.score,
                "threshold": min_score,
                "anchor_ids": list(slot.anchor_ids),
                "at": round(slot.dst_in, 3),
            }
        )

    kept: List[_BrollSlot] = []
    evicted: List[Tuple[_BrollSlot, _BrollSlot]] = []
    for slot in sorted(candidates, key=lambda item: (item.dst_in, item.index)):
        while True:
            clash = next((other for other in kept if slot.overlaps(other)), None)
            if clash is None:
                kept.append(slot)
                break
            if _broll_overlap_winner(slot, clash) is slot:
                kept.remove(clash)
                evicted.append((clash, slot))
                continue
            evicted.append((slot, clash))
            break
    accepted_ids = {id(slot) for slot in kept}

    for slot in sorted(candidates, key=lambda item: (item.dst_in, item.index)):
        accepted = id(slot) in accepted_ids
        entry: Dict[str, Any] = {
            "asset_id": slot.clip.source_id,
            "at": round(slot.dst_in, 3),
            "anchor_id": slot.anchor_ids[0] if slot.anchor_ids else None,
            "anchor_ids": list(slot.anchor_ids),
            "score": slot.score,
            "accepted": accepted,
        }
        if slot.score is not None:
            entry["score_coverage"] = slot.coverage
            entry["score_confidence"] = slot.confidence
        report.broll.append(entry)
        if not accepted:
            continue
        if slot.fade_clamped:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": slot.clip.clip_id,
                    "asset_id": slot.clip.source_id,
                    "reason": "broll_fade_clamped",
                    "duration_sec": slot.clip.transition_in.duration_sec,
                    "clamped_sec": round(_transition_cap(slot.length), 3),
                }
            )
        if slot.clip.transition_out is not None:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": slot.clip.clip_id,
                    "asset_id": slot.clip.source_id,
                    "reason": "transition_out_dropped",
                    "transition": slot.clip.transition_out.type,
                    "duration_sec": slot.clip.transition_out.duration_sec,
                }
            )
        if not slot.anchor_ids:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": slot.clip.clip_id,
                    "asset_id": slot.clip.source_id,
                    "reason": "broll_without_anchor",
                    "at": round(slot.dst_in, 3),
                }
            )
        if slot.score is None:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": slot.clip.clip_id,
                    "asset_id": slot.clip.source_id,
                    "reason": "broll_score_unavailable",
                    "score": None,
                    "anchor_ids": list(slot.anchor_ids),
                    "at": round(slot.dst_in, 3),
                }
            )

    for loser, winner in evicted:
        report.not_applied.append(
            {
                "anchor_id": None,
                "clip_id": loser.clip.clip_id,
                "other_clip_id": winner.clip.clip_id,
                "asset_id": loser.clip.source_id,
                "other_asset_id": winner.clip.source_id,
                "reason": "broll_overlap_after_cut",
                "at": round(loser.dst_in, 3),
                "other_at": round(winner.dst_in, 3),
                "score": loser.score,
                "other_score": winner.score,
                "policy": "higher_score_then_earlier",
            }
        )

    kept.sort(key=lambda item: item.dst_in)
    return [
        edl_plan.ClipSegment(
            clip_id=slot.clip.clip_id,
            source_id=slot.clip.source_id,
            src_in=float(slot.clip.src_in),
            src_out=float(slot.clip.src_in) + slot.length,
            dst_in=slot.dst_in,
            dst_out=slot.dst_in + slot.length,
            layer=slot.clip.layer,
            role=slot.clip.role,
            transition_in=slot.fade,
            gain_db=slot.clip.gain_db,
        )
        for slot in kept
    ]


def _camera_declared(ref: Any) -> bool:
    """A ``CameraSpec`` counts as declared when at least one field is set."""
    camera = getattr(ref, "camera", None)
    if camera is None:
        return False
    return any(
        getattr(camera, name, None) is not None
        for name in ("angle_deg", "focal_length_mm")
    )


def _surviving_window(
    clip_id: str, clips_by_id: Mapping[str, Any]
) -> Optional[Tuple[float, float]]:
    """The destination window a pre-bridge clip holds after the cut, if any.

    A b-roll keeps its ``clip_id``; a layer-0 clip becomes ``{clip_id}_{n}``
    fragments, so the window is the span from the first fragment's ``dst_in``
    to the last fragment's ``dst_out``.
    """
    direct = clips_by_id.get(clip_id)
    if direct is not None:
        return (float(direct.dst_in), float(direct.dst_out))
    prefix = clip_id + "_"
    windows = [
        (float(clip.dst_in), float(clip.dst_out))
        for new_id, clip in clips_by_id.items()
        if new_id.startswith(prefix) and new_id[len(prefix) :].isdigit()
    ]
    if not windows:
        return None
    return (min(start for start, _ in windows), max(end for _, end in windows))


def _retime_camera(
    plan: edl_plan.EDLPlan,
    video_tracks: Sequence[edl_plan.Track],
    report: BridgeReport,
) -> None:
    """Follow every declared camera onto the cut timeline, or declare it gone.

    ``CameraSpec`` is declared on the source, so the operation it authorises is
    the destination window of each video clip that references it: those windows
    are what the 30/180 rules compare at the cuts. The bridge rebuilds them, so
    an op is re-timed onto the surviving pieces of its clip. A window that
    shrank is clamped and declared with both spans; a clip the bridge removed
    takes its op with it and is declared ``camera_op_dropped`` at its original
    time. A plan with no camera declared anywhere gets exactly one
    ``camera_absent`` entry, not one per clip.
    """
    clips_by_id = {clip.clip_id: clip for track in video_tracks for clip in track.clips}
    ops = [
        clip
        for track in plan.video_tracks
        for clip in track.clips
        if _camera_declared(plan.sources.get(clip.source_id))
    ]
    if not ops:
        report.not_applied.append(
            {
                "anchor_id": None,
                "reason": "camera_absent",
                "sources": sorted(
                    {
                        clip.source_id
                        for track in plan.video_tracks
                        for clip in track.clips
                    }
                ),
                "clips": sum(len(track.clips) for track in plan.video_tracks),
            }
        )
        return
    for clip in ops:
        original_in = float(clip.dst_in)
        original_out = float(clip.dst_out)
        window = _surviving_window(clip.clip_id, clips_by_id)
        if window is None:
            report.not_applied.append(
                {
                    "anchor_id": None,
                    "clip_id": clip.clip_id,
                    "source_id": clip.source_id,
                    "reason": "camera_op_dropped",
                    "original_dst_in": round(original_in, 3),
                    "original_dst_out": round(original_out, 3),
                }
            )
            continue
        if (
            abs(window[0] - original_in) <= 1e-6
            and abs(window[1] - original_out) <= 1e-6
        ):
            continue
        shrank = (window[1] - window[0]) < (original_out - original_in) - 1e-6
        report.not_applied.append(
            {
                "anchor_id": None,
                "clip_id": clip.clip_id,
                "source_id": clip.source_id,
                "reason": "camera_op_clamped" if shrank else "camera_op_retimed",
                "original_dst_in": round(original_in, 3),
                "original_dst_out": round(original_out, 3),
                "dst_in": round(window[0], 3),
                "dst_out": round(window[1], 3),
            }
        )
