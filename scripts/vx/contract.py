"""vx.contract — FROZEN interface v1.5.0 for the video-editor skill.

stdlib only. Every other module may import this; this module imports nothing local.
Changing anything here is a version bump (see SKILL.md "contratto").
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "vx.report/1.0"
TRIAGE_SCHEMA = "vx.triage/1.0"
PLAN_SCHEMA = "vx.plan/1.1"
QC_SCHEMA = "vx.qc/1.0"
VERSION = "1.5.0"

PROXY_WIDTH = 640
SAMPLE_FPS_VIDEO = 10.0
SAMPLE_FPS_FACE = 8.0

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NEEDS_INPUT = 3
EXIT_MISSING_DEP = 4
EXIT_QC_FAIL = 5
EXIT_RENDER_FAIL = 6

# Safe rectangles on a 1080x1920 canvas: (x1, x2, y1, y2) in pixels.
SAFE_ZONES = {
    "9:16": (120, 888, 288, 1248),
    "1:1": (60, 1020, 120, 960),
    "4:5": (60, 1020, 120, 1200),
    "16:9": (48, 1872, 36, 1044),
}
# Per-platform (top, bottom, left, right) insets on 1080x1920, ad-guidance values.
PLATFORM_INSETS = {
    "tiktok": (240, 660, 120, 120),
    "reels": (269, 672, 65, 65),
    "shorts": (288, 672, 48, 192),
    "facebook": (269, 672, 65, 65),
    "snapchat": (192, 672, 65, 162),
}
ASPECTS = {
    "9:16": (9, 16),
    "1:1": (1, 1),
    "4:5": (4, 5),
    "16:9": (16, 9),
    "4:3": (4, 3),
    "3:4": (3, 4),
}
LOUDNESS_TARGETS = {
    "social": (-14.0, -1.0),
    "apple": (-16.0, -1.0),
    "broadcast": (-23.0, -2.0),
}

_THRESHOLDS_DATA: Dict[str, Any] = {
    # video stability / image
    "jitter_shaky": 0.35,
    "jitter_bands": (0.35, 0.60, 1.00),
    "stab_min_headroom_pct": 8.0,
    "clip_high_pct": 2.0,
    "clip_high_block_pct": 8.0,
    "luma_under": 45.0,
    "cast_rg": 12.0,
    "cast_by": 12.0,
    "freeze_min": 1.5,
    "scene_cut": 0.30,
    # audio
    "noise_snr_low": 12.0,
    "dead_air_ratio": 0.08,
    "dead_air_max_cut": 0.35,
    "silence_pad": 0.12,
    "silence_min": 0.35,
    "silence_dbfs": -70.0,
    "bridge_min_fragment_ms": 700,
    "clip_pct": 0.01,
    "lra_wide": 12.0,
    # speech / subtitles
    "speech_wpm_fast": 190,
    "sub_max_chars_916": 18,
    "sub_max_chars_169": 42,
    "sub_max_lines": 2,
    "sub_max_cps": 20,
    "cue_dur_min": 0.4,
    "cue_dur_max": 7.0,
    "upscale_warn": 1.25,
    # editing policy
    "hook_window": 4.0,
    "max_creative_treatments": 3,
    "cut_fade_ms": 12,
    # multitrack EDL audio buses
    "dialogue_fade_ms": 12,
    "broll_fade_sec": 0.5,
    # asset triage (Fase 4)
    "triage_max_upscale": 1.5,
    "triage_max_broll": 3,
    "triage_max_music": 1,
    "triage_min_duration_sec": 1.0,
    "triage_broll_sec": 3.0,
    "triage_image_duration_sec": 3.0,
    "triage_music_gain_db": -8.0,
    "triage_music_ratio_low": 0.5,
    "triage_sharpness_low": 50.0,
    "triage_confidence_low": 0.2,
    "triage_confidence_high": 0.8,
}

THRESHOLDS = MappingProxyType(_THRESHOLDS_DATA)

# The default snapshot is separate and never changes. `settings` owns the only
# write path to the live table through `_set_thresholds`; everything else reads a
# read-only mapping and cannot mutate it in place.
DEFAULT_THRESHOLDS = MappingProxyType(dict(_THRESHOLDS_DATA))


def _set_thresholds(values: Dict[str, Any]) -> None:
    _THRESHOLDS_DATA.clear()
    _THRESHOLDS_DATA.update(values)


CREATIVE_TREATMENTS = ("autoframe", "zoom", "hook_overlay", "fit")
TECHNICAL_TREATMENTS = (
    "cut_silence",
    "denoise",
    "loudnorm",
    "stabilize",
    "color_fix",
    "exposure_fix",
    "compress",
)

# The semantic half of the evidence law: a witness must be the measurement that
# justifies the treatment, not merely a finding that exists. The map is frozen;
# a treatment absent from it is rejected rather than granted a silent default.
ALLOWED_EVIDENCE_MAP = MappingProxyType(
    {
        "clipping_fix": frozenset({"audio.clipping"}),
        "denoise": frozenset({"audio.noise.high"}),
        "loudnorm": frozenset({"audio.loudness.off"}),
        "compress": frozenset({"audio.lra.wide"}),
        "audio_restore": frozenset({"audio.silent"}),
        "stabilize": frozenset({"video.jitter.shaky"}),
        "exposure_fix": frozenset({"video.exposure.under", "video.exposure.high_clip"}),
        "color_fix": frozenset({"video.color.cast", "brief.autonomous"}),
        "pacing_fix": frozenset({"video.pacing.drag"}),
        "autoframe": frozenset(
            {
                "faces.none",
                "faces.single",
                "faces.multi_span",
                "faces.multi_concurrent",
                "faces.track_broken",
            }
        ),
        "hook_overlay": frozenset({"video.hook.weak"}),
        "zoom": frozenset({"video.pacing.drag"}),
        "fit": frozenset({"faces.multi_span", "faces.multi_concurrent"}),
        "subtitles": frozenset({"speech.present"}),
        "filler_cut": frozenset({"speech.filler"}),
        "cut_silence": frozenset({"audio.dead_air.excess", "video.freeze.static"}),
    }
)


class VxError(RuntimeError):
    """Base error for the toolkit."""


class MissingDependency(VxError):
    """A binary, python package, or model asset is unavailable."""


class TriageRequired(VxError):
    """The brief is incomplete: the skill must pause and ask the user."""

    def __init__(self, request: Dict[str, Any]):
        super().__init__("needs user input")
        self.request = request


class QCFailure(VxError):
    """Quality-control gate failed."""

    def __init__(self, result: Dict[str, Any]):
        super().__init__("qc failed")
        self.result = result


class RenderFailure(VxError):
    """ffmpeg render failed."""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def even(value: float) -> int:
    """Nearest even integer not greater than value (yuv420p safe)."""
    v = int(round(value))
    if v % 2:
        v -= 1
    return max(2, v)


def target_crop_size(src_w: int, src_h: int, aspect: str) -> tuple[int, int]:
    """Largest even window of `aspect` that fits inside the source."""
    aw, ah = ASPECTS[aspect]
    target_ratio = aw / ah
    src_ratio = src_w / src_h
    if src_ratio > target_ratio:
        h = src_h
        w = src_h * target_ratio
    else:
        w = src_w
        h = src_w / target_ratio
    return even(min(src_w, w)), even(min(src_h, h))


def output_size(
    aspect: str, precision: str = "full", proxy_width: int = PROXY_WIDTH
) -> tuple[int, int]:
    """Render size per aspect; proxy keeps the same aspect at 640 wide."""
    w, h = ASPECTS[aspect]
    if precision == "proxy":
        out_w = proxy_width
        out_h = even(out_w * h / w)
    elif aspect in ("9:16", "1:1", "4:5"):
        out_h = 1920 if aspect != "4:5" else 1920
        out_w = even(out_h * w / h)
    elif aspect == "16:9":
        out_w, out_h = 1920, 1080
    else:
        out_w, out_h = 1920, even(1920 * h / w)
    return even(out_w), even(out_h)


def safe_rect(aspect: str) -> tuple[int, int, int, int]:
    return SAFE_ZONES.get(aspect, SAFE_ZONES["9:16"])


def expected_frames(cuts: List[Dict[str, float]], fps: float) -> int:
    """Frame count expected after applying [(in,out)...] cuts at `fps`."""
    total = 0.0
    for c in cuts:
        start = float(c.get("in", 0.0))
        end = float(c.get("out", 0.0))
        if end > start:
            total += end - start
    return int(round(total * fps))


def resolution_scale(a: tuple[int, int], b: tuple[int, int]) -> float:
    """Scale factor from resolution a to resolution b (width based)."""
    if not a[0] or not b[0]:
        return 1.0
    return b[0] / a[0]


def fmt(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


# --------------------------------------------------------------------------- #
# dataclasses
# --------------------------------------------------------------------------- #


@dataclass
class ProbeResult:
    path: str = ""
    duration: float = 0.0
    fps: float = 0.0
    width: int = 0
    height: int = 0
    nb_frames: Optional[int] = None
    video_codec: str = ""
    audio_codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    pix_fmt: str = ""
    rotation: int = 0
    has_audio: bool = False
    bitrate: int = 0
    container: str = ""
    video_index: int = 0
    audio_index: Optional[int] = None
    color: Dict[str, Any] = field(default_factory=dict)
    size: int = 0
    mtime: float = 0.0
    # "video" | "image" | "audio". Appended last so positional construction
    # keeps working; probe() always reports "video".
    kind: str = "video"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def is_vertical(self) -> bool:
        return self.height > self.width


@dataclass
class VideoAnalysis:
    scene_cuts: List[float] = field(default_factory=list)
    freezes: List[Dict[str, float]] = field(default_factory=list)
    jitter: Dict[str, Any] = field(default_factory=dict)
    exposure: Dict[str, Any] = field(default_factory=dict)
    color: Dict[str, Any] = field(default_factory=dict)
    motion: Dict[str, Any] = field(default_factory=dict)
    pacing: Dict[str, Any] = field(default_factory=dict)
    retention: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AudioAnalysis:
    noise_floor_dbfs: float = -90.0
    speech_level_dbfs: float = -20.0
    snr_db: float = 0.0
    dead_air: List[Dict[str, float]] = field(default_factory=list)
    peaks: List[Dict[str, float]] = field(default_factory=list)
    clipping_pct: float = 0.0
    dc_offset: float = 0.0
    loudness: Dict[str, Any] = field(default_factory=dict)
    music_detected: bool = False
    music_ratio: float = 0.0
    music_confidence: float = 0.0
    speech_regions: List[Dict[str, float]] = field(default_factory=list)
    vad_engine: str = "none"
    snr_method: str = "p10-p90"
    verdict: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FaceTracks:
    detected_ratio: float = 0.0
    tracks: List[Dict[str, Any]] = field(default_factory=list)
    max_concurrent: int = 0
    framing: str = "center"
    detector: str = "none"
    span_x_ratio: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CropPlan:
    mode: str = "center"  # track | lock | fit | center
    aspect: str = "9:16"
    src_w: int = 0
    src_h: int = 0
    crop_w: int = 0
    crop_h: int = 0
    out_w: int = 0
    out_h: int = 0
    boxes: List[Dict[str, float]] = field(default_factory=list)  # {t,x,y}
    headroom_pct: float = 0.0
    safe: bool = True
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Cue:
    start: float = 0.0
    end: float = 0.0
    text: str = ""
    words: List[Dict[str, Any]] = field(default_factory=list)
    style: str = "karaoke"
    line_count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Transcript:
    language: str = ""
    words: List[Dict[str, Any]] = field(default_factory=list)
    segments: List[Dict[str, Any]] = field(default_factory=list)
    text: str = ""
    duration: float = 0.0
    wpm: float = 0.0
    model: str = ""
    device: str = "cpu"
    skipped_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EditPlan:
    output: Dict[str, Any] = field(default_factory=dict)
    treatments: List[Dict[str, Any]] = field(default_factory=list)
    cuts: List[Dict[str, float]] = field(default_factory=list)
    render: Dict[str, Any] = field(default_factory=dict)
    deliverables: List[Dict[str, Any]] = field(default_factory=list)
    brief: Dict[str, Any] = field(default_factory=dict)
    schema: str = PLAN_SCHEMA

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QCResult:
    pass_: bool = False
    checks: List[Dict[str, Any]] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    schema: str = QC_SCHEMA

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["pass"] = d.pop("pass_")
        return d


# --------------------------------------------------------------------------- #
# validators — the "no finding -> no treatment" law lives here
# --------------------------------------------------------------------------- #


def validate_report(report: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if report.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if "source" not in report:
        errors.append("missing source block")
    for key in ("path", "duration", "fps", "width", "height"):
        if key not in (report.get("source") or {}):
            errors.append(f"source.{key} missing")
    if not isinstance(report.get("findings", []), list):
        errors.append("findings must be a list")
    return errors


def _is_number(value: Any) -> bool:
    """True for real numbers; booleans are not numbers here."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finding_span(finding: Dict[str, Any]) -> Optional[tuple[float, float]]:
    span = finding.get("span")
    if not isinstance(span, (list, tuple)) or len(span) != 2:
        return None
    if not (_is_number(span[0]) and _is_number(span[1])):
        return None
    return float(span[0]), float(span[1])


def _merge_intervals(
    intervals: List[tuple[float, float]],
) -> List[tuple[float, float]]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        if start <= merged[-1][1] + 1e-9:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _removed_intervals(
    cuts: List[Dict[str, Any]], duration: Optional[float]
) -> List[tuple[float, float]]:
    """Complement of the keep-ranges: the source intervals a cut removes."""
    keep: List[tuple[float, float]] = []
    for cut in cuts:
        if not isinstance(cut, dict):
            continue
        start = cut.get("in")
        end = cut.get("out")
        if _is_number(start) and _is_number(end) and float(end) > float(start):
            keep.append((float(start), float(end)))
    if not keep:
        return []
    merged = _merge_intervals(keep)
    removed: List[tuple[float, float]] = []
    cursor = 0.0
    for start, end in merged:
        if start > cursor + 1e-9:
            removed.append((cursor, start))
        cursor = max(cursor, end)
    if duration is not None and cursor < float(duration) - 1e-9:
        removed.append((cursor, float(duration)))
    return removed


def _covered(
    start: float,
    end: float,
    merged: List[tuple[float, float]],
    tolerance: float,
) -> bool:
    for low, high in merged:
        if start >= low - tolerance and end <= high + tolerance:
            return True
    return False


def validate_evidence_shape(plan: Dict[str, Any]) -> List[str]:
    """Shape-only check: enabled treatments carry a non-empty witness list.

    A witness is ``{finding_id, t0, t1}`` with numeric ``t0 <= t1`` and the
    first witness must cite the treatment's own ``reason.finding_id``. Disabled
    treatments are exempt: they may carry null witnesses.
    """
    errors: List[str] = []
    for treatment in plan.get("treatments") or []:
        if not isinstance(treatment, dict) or not treatment.get("enabled"):
            continue
        name = treatment.get("name")
        evidence = treatment.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"treatment {name} has no evidence")
            continue
        reason_id = (treatment.get("reason") or {}).get("finding_id")
        first = evidence[0]
        if not isinstance(first, dict):
            errors.append(f"treatment {name} evidence[0] is not an object")
            continue
        if first.get("finding_id") != reason_id:
            errors.append(
                f"treatment {name} evidence[0] does not match reason.finding_id"
            )
        for index, witness in enumerate(evidence):
            if not isinstance(witness, dict):
                errors.append(f"treatment {name} evidence[{index}] is not an object")
                continue
            finding_id = witness.get("finding_id")
            if not isinstance(finding_id, str) or not finding_id:
                errors.append(f"treatment {name} evidence[{index}] has no finding_id")
            if "source_id" in witness:
                source_id = witness.get("source_id")
                if not isinstance(source_id, str) or not source_id:
                    errors.append(
                        f"treatment {name} evidence[{index}] has an invalid source_id"
                    )
            t0 = witness.get("t0")
            t1 = witness.get("t1")
            if not (_is_number(t0) and _is_number(t1)):
                errors.append(
                    f"treatment {name} evidence[{index}] has a non-numeric span"
                )
            elif float(t0) > float(t1):
                errors.append(f"treatment {name} evidence[{index}] has t0 > t1")
    return errors


def validate_evidence(
    plan: Dict[str, Any],
    findings: List[Dict[str, Any]],
    duration: Optional[float] = None,
    *,
    sources: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Full evidence law: witnesses must be fresh, spanned and exact.

    Every witness copies its finding's source-time span (no narrowing, no
    expansion). ``cut_silence`` may cite several findings, and every removed
    source interval must lie inside the union of its witnesses (1e-3). Every
    other treatment carries exactly one witness and needs exact span equality
    (1e-6).

    ``sources`` is the multi-source map (source_id -> source info). When it is
    ``None`` the v1 law applies: a witness may omit ``source_id``. When it is
    provided, every witness of an enabled treatment must cite a non-empty
    ``source_id`` that is a key in the map, and must match the finding's own
    ``source_id`` when the finding declares one.
    """
    errors: List[str] = list(validate_evidence_shape(plan))
    by_id: Dict[str, Dict[str, Any]] = {}
    for finding in findings or []:
        if isinstance(finding, dict) and finding.get("id"):
            by_id[finding["id"]] = finding

    strict_sources = sources is not None

    for treatment in plan.get("treatments") or []:
        if not isinstance(treatment, dict) or not treatment.get("enabled"):
            continue
        name = treatment.get("name")
        if name == "cut_silence" and duration is None:
            errors.append(
                "treatment cut_silence requires a duration to verify coverage"
            )
        evidence = treatment.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            continue  # shape already reported it
        if name != "cut_silence" and len(evidence) != 1:
            errors.append(f"treatment {name} must carry exactly one witness")

        witness_intervals: List[tuple[float, float]] = []
        for index, witness in enumerate(evidence):
            if not isinstance(witness, dict):
                continue
            finding_id = witness.get("finding_id")
            if strict_sources:
                source_id = witness.get("source_id")
                if not isinstance(source_id, str) or not source_id:
                    errors.append(
                        f"treatment {name} evidence[{index}] has no source_id"
                    )
                else:
                    declared = (by_id.get(finding_id) or {}).get("source_id")
                    if declared and source_id != declared:
                        errors.append(
                            f"treatment {name} evidence[{index}] source_id "
                            f"{source_id} does not match finding source_id "
                            f"{declared}"
                        )
                    elif source_id not in sources:
                        errors.append(
                            f"treatment {name} evidence[{index}] cites unknown "
                            f"source_id {source_id}"
                        )
            t0 = witness.get("t0")
            t1 = witness.get("t1")
            if not (_is_number(t0) and _is_number(t1)):
                continue  # shape already reported it
            t0 = float(t0)
            t1 = float(t1)
            if t0 > t1:
                continue  # shape already reported it
            if t1 - t0 <= 0:
                errors.append(f"treatment {name} evidence[{index}] is zero-length")
                continue
            finding = by_id.get(finding_id)
            if finding is None:
                errors.append(f"treatment {name} cites unknown finding {finding_id}")
                continue
            if finding.get("stale"):
                errors.append(f"treatment {name} cites stale finding {finding_id}")
                continue
            if finding_id == "brief.autonomous" and not finding.get("declared"):
                errors.append(
                    f"treatment {name} cites brief.autonomous without declared:true"
                )
                continue
            span = _finding_span(finding)
            if span is None:
                errors.append(
                    f"treatment {name} cites finding {finding_id} with no span"
                )
                continue
            if abs(t0 - span[0]) > 1e-6 or abs(t1 - span[1]) > 1e-6:
                errors.append(
                    f"treatment {name} witness {finding_id} does not copy the "
                    "finding span"
                )
                continue
            allowed = ALLOWED_EVIDENCE_MAP.get(str(name))
            if allowed is None:
                errors.append(
                    f"INVALID_EVIDENCE_SEMANTICS: treatment {name} has no "
                    "evidence policy"
                )
                continue
            if finding_id not in allowed:
                errors.append(
                    f"INVALID_EVIDENCE_SEMANTICS: treatment {name} cannot cite "
                    f"finding {finding_id}"
                )
                continue
            if duration is not None and (t0 < -1e-6 or t1 > float(duration) + 1e-6):
                errors.append(
                    f"treatment {name} witness {finding_id} outside 0..{duration}"
                )
                continue
            witness_intervals.append((t0, t1))

        if name == "cut_silence" and witness_intervals:
            removed = _removed_intervals(plan.get("cuts") or [], duration)
            merged = _merge_intervals(witness_intervals)
            for start, end in removed:
                if not _covered(start, end, merged, 1e-3):
                    errors.append(
                        f"cut_silence removes {start:.3f}-{end:.3f} outside the "
                        "witness union"
                    )
    return errors


def validate_plan(
    plan: Dict[str, Any], report: Optional[Dict[str, Any]] = None
) -> List[str]:
    errors: List[str] = []
    treatments = plan.get("treatments") or []
    known = (
        {f.get("id") for f in (report or {}).get("findings", [])} if report else None
    )
    creative = 0
    stab = 0
    for t in treatments:
        if not t.get("enabled"):
            continue
        name = t.get("name")
        if not t.get("reason", {}).get("finding_id"):
            errors.append(f"treatment {name} has no reason.finding_id")
        elif known is not None and t["reason"]["finding_id"] not in known:
            errors.append(
                f"treatment {name} cites unknown finding {t['reason']['finding_id']}"
            )
        if name in CREATIVE_TREATMENTS:
            creative += 1
        if name == "stabilize":
            stab += 1
    if creative > THRESHOLDS["max_creative_treatments"]:
        errors.append(
            f"{creative} creative treatments > {THRESHOLDS['max_creative_treatments']}"
        )
    if stab > 1:
        errors.append("more than one stabilization pass")
    out = plan.get("output") or {}
    if not out.get("aspect"):
        errors.append("output.aspect missing")
    if report is not None:
        findings = report.get("findings") or []
        source = report.get("source") or {}
        duration = source.get("analysis_duration") or source.get("duration")
        sources = plan.get("sources") or (report or {}).get("sources")
        errors.extend(validate_evidence(plan, findings, duration, sources=sources))
    return errors


def validate_triage(req: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if req.get("status") != "needs_user_input":
        errors.append("status must be needs_user_input")
    q = req.get("questions") or []
    if len(q) != 3:
        errors.append("triage must carry exactly 3 questions")
    for item in q:
        if not item.get("id") or not item.get("options"):
            errors.append("question missing id/options")
    return errors


def validate_qc(qc: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if "pass" not in qc and "pass_" not in qc:
        errors.append("qc pass flag missing")
    if not isinstance(qc.get("checks"), list):
        errors.append("qc checks must be a list")
    return errors


def payload_to_edit_plan(
    payload: Dict[str, Any], media_info: Dict[str, Any]
) -> "EditPlan":
    """Project an autonomous scene payload into the executable plan."""
    from . import decide

    return decide.payload_to_edit_plan(payload, media_info)


def payload_to_edl_plan(
    payload: Mapping[str, Any],
    sources: Mapping[str, Any],
    media_info_map: Mapping[str, Any],
    *,
    fps: float,
    anchors: Sequence[Tuple[str, float]] = (),
    aspect_ratio: Optional[str] = None,
    resolution: Optional[Tuple[int, int]] = None,
) -> Any:  # edl_plan.EDLPlan
    """Project a scene payload v1.5.0 (with ``asset_triage``) into an EDLPlan.

    The bridge consumes the triage verdict instead of guessing: only
    ``selected_assets`` reach the timeline, every rejected asset is counted in
    the notes and never routed. A-roll lands on video layer 0, b-roll on layer
    1, the dialogue bus mirrors A-roll, and at most one music bed is placed with
    its declared gain. ``sources`` carries only the refs actually referenced.
    """
    # Local import: contract.py is the frozen leaf and imports nothing local at
    # module level. edl_plan is pure data, so this mirrors the local import of
    # asset_triage in decide.py without creating a cycle.
    from . import edl_plan

    block = payload.get("asset_triage")
    if not isinstance(block, Mapping) or not block:
        raise VxError(
            "payload.asset_triage missing: the EDL bridge needs the triage block"
        )

    selected = block.get("selected_assets")
    if not isinstance(selected, list):
        raise VxError("payload.asset_triage.selected_assets must be a list")
    rejected = block.get("rejected_assets") or []
    if not isinstance(rejected, list):
        raise VxError("payload.asset_triage.rejected_assets must be a list")
    rejected_count = len(rejected)

    if not (
        isinstance(fps, (int, float))
        and not isinstance(fps, bool)
        and math.isfinite(float(fps))
        and float(fps) > 0
    ):
        raise VxError("fps must be a positive number")

    scene = payload.get("scene") or {}
    if not isinstance(scene, Mapping):
        scene = {}
    aspect = aspect_ratio if aspect_ratio is not None else scene.get("aspect_ratio")
    if not isinstance(aspect, str) or not aspect:
        raise VxError("scene.aspect_ratio missing: pass aspect_ratio explicitly")
    raw_resolution = resolution if resolution is not None else scene.get("resolution")
    if raw_resolution is None:
        raise VxError("scene.resolution missing: pass resolution explicitly")
    try:
        res = (int(raw_resolution[0]), int(raw_resolution[1]))
    except (TypeError, ValueError, IndexError) as exc:
        raise VxError(f"resolution must be two integers: {raw_resolution!r}") from exc

    def _media_duration(asset_id: str) -> float:
        info = media_info_map.get(asset_id)
        if info is None:
            raise VxError(f"asset {asset_id!r} has no media_info entry")
        value = getattr(info, "duration", None)
        if value is None and isinstance(info, Mapping):
            value = info.get("duration")
        try:
            duration = float(value)
        except (TypeError, ValueError) as exc:
            raise VxError(f"asset {asset_id!r} has no usable duration") from exc
        if not math.isfinite(duration) or duration <= 0:
            raise VxError(f"asset {asset_id!r} has no usable duration")
        return duration

    def _source_ref(asset_id: str) -> Any:
        ref = sources.get(asset_id)
        if ref is None:
            raise VxError(f"asset {asset_id!r} has no SourceRef")
        return ref

    def _target(entry: Mapping[str, Any], asset_id: str) -> float:
        try:
            target = float(entry.get("target_duration_sec"))
        except (TypeError, ValueError) as exc:
            raise VxError(
                f"asset {asset_id!r} has no usable target_duration_sec"
            ) from exc
        if not math.isfinite(target) or target <= 0:
            raise VxError(f"asset {asset_id!r} has no usable target_duration_sec")
        return target

    for index, entry in enumerate(selected):
        if not isinstance(entry, Mapping):
            raise VxError(f"selected_assets[{index}] is not an object")

    a_entries = [entry for entry in selected if entry.get("role") == "a_roll"]
    b_entries = [entry for entry in selected if entry.get("role") == "b_roll"]
    music_entries = [entry for entry in selected if entry.get("role") == "music"]

    if not a_entries:
        raise VxError("payload.asset_triage has no a_roll: cannot build a timeline")

    referenced: Dict[str, Any] = {}
    a_clips: List[Any] = []
    dialogue_clips: List[Any] = []
    cursor = 0.0
    for index, entry in enumerate(a_entries):
        asset_id = entry.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise VxError(f"selected a_roll[{index}] has no asset_id")
        ref = _source_ref(asset_id)
        media_duration = _media_duration(asset_id)
        length = min(_target(entry, asset_id), media_duration)
        if length <= 0:
            raise VxError(f"asset {asset_id!r} has no usable duration")
        referenced[asset_id] = ref
        a_clips.append(
            edl_plan.ClipSegment(
                clip_id=f"v0_{index}",
                source_id=asset_id,
                src_in=0.0,
                src_out=length,
                dst_in=cursor,
                dst_out=cursor + length,
                layer=0,
                role="main_talking_head",
            )
        )
        dialogue_clips.append(
            edl_plan.ClipSegment(
                clip_id=f"a0_{index}",
                source_id=asset_id,
                src_in=0.0,
                src_out=length,
                dst_in=cursor,
                dst_out=cursor + length,
                layer=0,
                role="dialogue",
                gain_db=0.0,
            )
        )
        cursor += length
    duration = cursor
    if duration <= 0:
        raise VxError("a_roll total duration is zero")

    anchor_map: Dict[str, float] = {}
    for pair in anchors:
        try:
            anchor_id, anchor_at = pair
            anchor_map[str(anchor_id)] = float(anchor_at)
        except (TypeError, ValueError) as exc:
            raise VxError(f"invalid anchor pair: {pair!r}") from exc

    b_clips: List[Any] = []
    any_anchor = False
    n_broll = len(b_entries)
    for index, entry in enumerate(b_entries):
        asset_id = entry.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise VxError(f"selected b_roll[{index}] has no asset_id")
        ref = _source_ref(asset_id)
        media_duration = _media_duration(asset_id)
        length = min(_target(entry, asset_id), media_duration, duration)
        if length <= 0:
            raise VxError(f"asset {asset_id!r} has no usable duration")
        if asset_id in anchor_map:
            at = anchor_map[asset_id]
            any_anchor = True
        elif entry.get("at_sec") is not None:
            try:
                at = float(entry["at_sec"])
            except (TypeError, ValueError) as exc:
                raise VxError(f"asset {asset_id!r} has a non-numeric at_sec") from exc
            any_anchor = True
        else:
            at = duration * (index + 1) / (n_broll + 1)
        max_start = max(0.0, duration - length)
        dst_in = clamp(at, 0.0, max_start)
        fade = float(THRESHOLDS["broll_fade_sec"])
        if fade > length:
            fade = length
        referenced[asset_id] = ref
        b_clips.append(
            edl_plan.ClipSegment(
                clip_id=f"v1_{index}",
                source_id=asset_id,
                src_in=0.0,
                src_out=length,
                dst_in=dst_in,
                dst_out=dst_in + length,
                layer=1,
                role="b_roll",
                transition_in=edl_plan.Transition("fade", fade),
            )
        )

    # Layer 1 cannot hold two overlapping clips. Refuse rather than emit a plan
    # that validate() would reject when the timeline is too short for the pool.
    ordered_b = sorted(b_clips, key=lambda clip: clip.dst_in)
    for previous, current in zip(ordered_b, ordered_b[1:]):
        if current.dst_in < previous.dst_out - 1e-6:
            raise VxError(
                "selected b_roll clips overlap on layer 1: "
                f"{previous.source_id!r} and {current.source_id!r}"
            )

    if len(music_entries) > 1:
        raise VxError("payload.asset_triage selected more than one music asset")
    music_clip = None
    if music_entries:
        entry = music_entries[0]
        asset_id = entry.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise VxError("selected music has no asset_id")
        ref = _source_ref(asset_id)
        media_duration = _media_duration(asset_id)
        music_length = min(media_duration, duration)
        try:
            gain = float(entry.get("ducking_db", 0.0))
        except (TypeError, ValueError) as exc:
            raise VxError(f"asset {asset_id!r} has a non-numeric ducking_db") from exc
        referenced[asset_id] = ref
        music_clip = edl_plan.ClipSegment(
            clip_id="a1_0",
            source_id=asset_id,
            src_in=0.0,
            src_out=music_length,
            dst_in=0.0,
            dst_out=music_length,
            layer=0,
            role="music",
            gain_db=gain,
        )

    video_tracks = [edl_plan.Track(track_id=0, clips=tuple(a_clips), role="a_roll")]
    if b_clips:
        video_tracks.append(
            edl_plan.Track(track_id=1, clips=tuple(b_clips), role="b_roll")
        )
    audio_tracks = [
        edl_plan.Track(track_id=0, clips=tuple(dialogue_clips), role="dialogue")
    ]
    if music_clip is not None:
        audio_tracks.append(
            edl_plan.Track(track_id=1, clips=(music_clip,), role="music")
        )

    notes: List[str] = []
    payload_version = payload.get("contract_version") or "?"
    notes.append(f"payload v{payload_version} -> EDL v{edl_plan.EDL_CONTRACT_VERSION}")
    if b_clips and not any_anchor:
        notes.append("b-roll distribuiti uniformemente: nessun ancoraggio dichiarato")
    if music_clip is None:
        notes.append("nessuna traccia musicale")
    elif music_length < duration - 1e-9:
        notes.append(
            f"musica più corta della timeline: {music_length:.3f}s su {duration:.3f}s"
        )
    if not dialogue_clips:
        notes.append("nessun bus dialogo: ducking non dichiarato")
    notes.append(f"asset scartati ignorati: {rejected_count}")

    ducking = (
        edl_plan.DuckingSpec() if music_clip is not None and dialogue_clips else None
    )

    return edl_plan.EDLPlan(
        contract_version=edl_plan.EDL_CONTRACT_VERSION,
        fps=float(fps),
        duration_sec=duration,
        aspect_ratio=aspect,
        resolution=res,
        sources=referenced,
        video_tracks=tuple(video_tracks),
        audio_tracks=tuple(audio_tracks),
        ducking=ducking,
        notes=tuple(notes),
    )
