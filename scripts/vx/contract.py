"""vx.contract — FROZEN interface v1.0.0 for the video-editor skill.

stdlib only. Every other module may import this; this module imports nothing local.
Changing anything here is a version bump (see SKILL.md "contratto").
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

SCHEMA = "vx.report/1.0"
TRIAGE_SCHEMA = "vx.triage/1.0"
PLAN_SCHEMA = "vx.plan/1.0"
QC_SCHEMA = "vx.qc/1.0"
VERSION = "1.0.0"

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

THRESHOLDS = {
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
    "clip_pct": 0.01,
    "lra_wide": 12.0,
    # speech / subtitles
    "speech_wpm_fast": 190,
    "sub_max_chars_916": 18,
    "sub_max_chars_169": 42,
    "sub_max_lines": 2,
    "sub_max_cps": 25,
    # editing policy
    "hook_window": 4.0,
    "max_creative_treatments": 3,
    "cut_fade_ms": 12,
}

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
