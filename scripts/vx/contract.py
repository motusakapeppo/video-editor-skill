"""vx.contract — FROZEN interface v1.5.0 for the video-editor skill.

stdlib only. Every other module may import this; this module imports nothing local.
Changing anything here is a version bump (see SKILL.md "contratto").
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

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
    # A3: the per-clip gain is peak-aware. A quiet clip boosted to the loudness
    # target must not push its own true peak past this dialogue ceiling; when
    # the peak is what limits the gain, the missed loudness is declared in LU.
    # FX: typographic classes are measured on the font binary, not guessed from
    # the file name. Advance ratio = advance/H per em; stem ratio = stem/cap.
    "font_advance_condensed_max": 0.45,
    "font_advance_extended_min": 0.62,
    "font_stem_light_max": 0.09,
    "font_stem_regular_max": 0.13,
    "font_stem_medium_max": 0.145,
    "font_stem_bold_max": 0.21,
    "bridge_gain_tp_ceiling_dbtp": -1.5,
    # Delivery limiter headroom: alimiter's own tolerance at 4x oversampling
    # plus the measured AAC overshoot after the limiter. Their sum is the margin
    # the limiter keeps below the QC true-peak ceiling.
    "limiter_tolerance_db": 0.1,
    "limiter_encode_overshoot_db": 0.2,
    # A4: word anchors -> captions. A word below the confidence floor never
    # becomes an anchor; it is counted and declared, not dropped silently.
    "bridge_caption_min_confidence": 0.45,
    "bridge_caption_gap_ms": 420,
    "bridge_caption_max_chars": 42,
    "bridge_caption_bottom_inset_ratio": 0.08,
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
    # La composizione della scaletta (sections.compose): quanto dura il gancio e
    # in che ordine entrano i b-roll. Sono le uniche tre leve misurate prima del
    # piano; i numeri stanno qui come ogni altra soglia, non sparsi nei moduli.
    # ``hook_share_max`` impedisce che un hook da 2,4 s su un reel da 6 s sia
    # metà video; ``hook_tail_sec`` è la coda che deve restare nella corsa
    # dell'apertura perché il corpo abbia materiale.
    "composition_hook_min_sec": 1.0,
    "composition_hook_base_sec": 2.0,
    "composition_hook_strong_sec": 2.4,
    "composition_hook_weak_sec": 1.2,
    "composition_hook_max_sec": 3.0,
    "composition_hook_share_max": 0.35,
    "composition_hook_tail_sec": 0.5,
    "composition_hook_strong_ratio": 1.5,
    "composition_hook_weak_ratio": 1.0,
    # I b-roll entrano sul taglio più vicino allo slot uniforme, ma solo se il
    # taglio è dentro questa tolleranza: su un piano con inquadrature lunghe non
    # si sposta niente (un overlay a metà di un shot da 6 s è normale), su un
    # piano fitto il b-roll comincia dove il video taglia già.
    "composition_broll_snap_sec": 0.75,
    # La coda che resta libera quando la richiesta chiede una CTA o una
    # testimonianza: un b-roll che sfuma sopra il messaggio finale lo copre.
    "composition_tail_sec": 1.5,
    # C8 -- legge A: nessun effetto senza prova, e mai piu' di
    # ``max_creative_treatments`` trattamenti creativi distinti per reel. Le
    # soglie sotto sono le misure che rendono una spec ammissibile: movimento
    # (jitter), cast colore, bordo piu' chiaro del centro (vignetta) e
    # saturazione bassa (noir/sepia). Sono politiche dichiarate, non numeri
    # calibrati: il test le legge da qui, nessun modulo le reinventa.
    "effects_shake_jitter_min": 0.35,
    "effects_color_cast_min": 12.0,
    "effects_vignette_border_ratio": 1.06,
    "effects_saturation_max": 0.22,
    # C8 -- legge B: meglio corto che ripetuto. Una corsa di inquadrature quasi
    # identiche piu' lunga di questo numero si accorcia; sotto la durata
    # dichiarabile il lavoro non si consegna.
    "variety_repeat_max": 2,
    "variety_min_duration_sec": 3.0,
    # multitrack EDL audio buses
    "dialogue_fade_ms": 12,
    "broll_fade_sec": 0.5,
    # A12: music shorter than the timeline is looped in the graph; the seam is
    # an `acrossfade` of this length, so no loop can click or drop.
    "music_loop_crossfade_sec": 0.4,
    # A8: the semantic score of a B-roll is the share of its destination window
    # actually covered by transcribed word anchors, times the mean confidence of
    # those anchors (time-weighted). Both factors live in [0, 1], so the score is
    # already normalised: no rescaling is applied. 0.35 reads as "at least a third
    # of the shot illustrates speech we recognised with confidence": lower than
    # that the picture is decoration, not evidence.
    "broll_min_score": 0.35,
    # asset triage (Fase 4)
    "triage_max_upscale": 1.5,
    # Adaptation, declared. The triage refuses a canvas that would need more than
    # `triage_max_upscale` of growth; when *every* refusal is that one, the limit
    # is raised up to `triage_adapt_max_upscale` (4x keeps a 360x640 phone clip
    # legible on a 1080x1920 canvas). Past the ceiling the canvas is lowered
    # instead, and the ladder aims at `triage_adapt_target_upscale`. Both numbers
    # are written in `triage.adapted` and can be overridden like every threshold.
    "triage_adapt_max_upscale": 4.0,
    "triage_adapt_target_upscale": 2.0,
    "triage_max_broll": 3,
    "triage_max_music": 2,
    "triage_min_duration_sec": 1.0,
    "triage_broll_sec": 3.0,
    "triage_image_duration_sec": 3.0,
    "triage_music_gain_db": -8.0,
    "triage_music_ratio_low": 0.5,
    "triage_sharpness_low": 50.0,
    "triage_confidence_low": 0.2,
    "triage_confidence_high": 0.8,
    # A9: perceptual duplicate detection. Two encodes of the same take decode to
    # near-identical frames, so their sampled dHash agrees; a different take on
    # the same set does not. The distance is a normalised Hamming distance over
    # the concatenated sampled hashes (3 frames x 64 bits = 192 bits), so 0.06
    # allows ~11 differing bits and nothing more.
    "triage_phash_max_distance": 0.06,
    # Two encodes of one take may differ by a few frames of container duration;
    # a different take almost never lands inside half a second of the same length.
    "triage_phash_duration_tolerance_sec": 0.5,
}

THRESHOLDS = MappingProxyType(_THRESHOLDS_DATA)

# The default snapshot is separate and never changes. `settings` owns the only
# write path to the live table through `_set_thresholds`; everything else reads a
# read-only mapping and cannot mutate it in place.
DEFAULT_THRESHOLDS = MappingProxyType(dict(_THRESHOLDS_DATA))


def _set_thresholds(values: Dict[str, Any]) -> None:
    _THRESHOLDS_DATA.clear()
    _THRESHOLDS_DATA.update(values)


# A12: the graph repeats the music bed instead of leaving a silent tail, so the
# copy count is a branch count. 256 copies is ~10 minutes of bed from a 5 s
# loop; beyond it the loop is refused and declared, never emitted as a graph
# nobody can debug.
MAX_MUSIC_LOOP_COPIES = 256


@dataclass(frozen=True)
class MusicLoopPlan:
    """How many copies of the music bed cover the timeline, and with what seam."""

    looped: bool
    copies: int
    crossfade_sec: float
    refused: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "looped": self.looped,
            "copies": self.copies,
            "crossfade_sec": self.crossfade_sec,
            "refused": self.refused,
        }


def plan_music_loop(
    media_sec: float,
    timeline_sec: float,
    crossfade_sec: float,
    *,
    max_copies: int = MAX_MUSIC_LOOP_COPIES,
) -> MusicLoopPlan:
    """Smallest copy count whose crossfaded joints still reach the timeline.

    Each joint costs one crossfade, so ``N`` copies yield
    ``N * (M - c) + c`` seconds: ``N = ceil((T - c) / (M - c))``, which reduces
    to ``ceil(T / M)`` when the crossfade is zero. ``c`` is capped at half the
    track, so every ``acrossfade`` is strictly shorter than both its inputs, as
    ffmpeg requires.
    """
    if not (math.isfinite(media_sec) and math.isfinite(timeline_sec)):
        return MusicLoopPlan(False, 1, 0.0, "durata non finita")
    if media_sec <= 0 or timeline_sec <= media_sec:
        return MusicLoopPlan(False, 1, 0.0)
    if crossfade_sec <= 0:
        seam = 0.0
    else:
        seam = min(float(crossfade_sec), media_sec / 2.0)
    step = media_sec - seam
    if step <= 0:
        return MusicLoopPlan(False, 1, 0.0, "traccia troppo corta per una giunzione")
    copies = int(math.ceil((timeline_sec - seam) / step))
    if copies < 2:
        return MusicLoopPlan(False, 1, 0.0)
    if copies > max_copies:
        return MusicLoopPlan(
            False,
            1,
            0.0,
            f"{copies} copie oltre il limite di {max_copies}",
        )
    return MusicLoopPlan(True, copies, round(seam, 6))


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
        # One horizon for every stage: the timeline the analysis measured, which
        # is the staging file when the source needed normalising. `plan.py`
        # clamps findings and cuts to the same number (`_timeline_duration`), so
        # the gate and the plan can never disagree about where the material ends.
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


_SOURCE_KEY_RUN = re.compile(r"[^A-Za-z0-9_.-]+")
_SOURCE_KEY_REPEAT = re.compile(r"_+")


def source_key(asset_id: str, taken: Optional[Set[str]] = None) -> str:
    """Project a human-readable asset id onto a legal EDL ``source_id``.

    The EDL key is an internal identifier: it only has to match
    ``edl_plan._SOURCE_KEY`` (``[A-Za-z0-9_.-]+``) and to be unique inside one
    document. Real-world names ("WhatsApp Video 2026-10-26 at 22.59.10.mp4",
    "clip (1).mp4", "perché è così.mp4") are not, so the EDL carries the slug
    while the triage report and ``payload.asset_triage`` keep the original
    asset id the user can point at.

    Every run of characters outside ``[A-Za-z0-9_.-]`` collapses to one ``_``;
    repeated ``_`` are collapsed and leading/trailing ``_``/``.`` are stripped.
    An empty result becomes ``"asset"``. When ``taken`` already holds the slug
    the suffix ``_2``, ``_3``, ... is appended until free: the caller owns
    ``taken`` and feeds the assets in document order, so the result is
    deterministic and never depends on dict or set ordering.
    """
    if not isinstance(asset_id, str) or not asset_id:
        raise VxError("source_key: asset_id must be a non-empty string")
    slug = _SOURCE_KEY_REPEAT.sub("_", _SOURCE_KEY_RUN.sub("_", asset_id))
    slug = slug.strip("_.")
    if not slug:
        slug = "asset"
    if taken is None:
        return slug
    candidate = slug
    suffix = 2
    while candidate in taken:
        candidate = f"{slug}_{suffix}"
        suffix += 1
    return candidate


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
    quality: Optional[Mapping[str, Any]] = None,
) -> Any:  # edl_plan.EDLPlan
    """Project a scene payload v1.5.0 (with ``asset_triage``) into an EDLPlan.

    The bridge consumes the triage verdict instead of guessing: only
    ``selected_assets`` reach the timeline, every rejected asset is counted in
    the notes and never routed. A-roll lands on video layer 0, b-roll on layer
    1, the dialogue bus mirrors A-roll, and at most one music bed is placed with
    its declared gain. ``sources`` carries only the refs actually referenced.

    ``quality`` is the per-asset verdict of the frame probe (``auto`` measures
    it, a test can pass it): ``{"src_in": s, "usable_end": e, "excluded": bool,
    "quality": "measured"|"not_measured"|"unusable", "why": ...}``. A measured
    window moves the take point and bounds the usable material — the segment
    durations are still the beat grid's, so rhythm does not move. A clip with
    no usable instant is dropped from the plan and declared in the notes; if it
    is the only a_roll the plan keeps it (a timeline cannot be empty) and says
    so. No verdict means no change: the EDL is byte-identical to before.
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

    # -- composizione della scaletta (vx.composition/1) ---------------------- #
    # Il payload porta una proposta, non un ordine: ``sections.compose`` ha
    # letto solo misure già prese (nitidezza dell'apertura, corse utili, BPM) e
    # qui si applica quello che il materiale permette. Senza blocco il piano è
    # byte-identico a prima: la composizione non cambia il default di nessuno.
    composition = payload.get("composition")
    composition = composition if isinstance(composition, Mapping) else {}
    proposed = composition.get("proposed")
    proposed = proposed if isinstance(proposed, Mapping) else {}
    hook_seconds: Optional[float] = None
    raw_hook = proposed.get("hook_seconds")
    if isinstance(raw_hook, (int, float)) and not isinstance(raw_hook, bool):
        if math.isfinite(float(raw_hook)) and float(raw_hook) > 0:
            hook_seconds = float(raw_hook)
    broll_order = proposed.get("broll_order")
    if isinstance(broll_order, (list, tuple)) and b_entries:
        rank = {str(asset): position for position, asset in enumerate(broll_order)}
        payload_order = {id(entry): position for position, entry in enumerate(b_entries)}
        if rank:
            b_entries = sorted(
                b_entries,
                key=lambda entry: (
                    rank.get(str(entry.get("asset_id")), len(rank)),
                    payload_order.get(id(entry), 0),
                ),
            )

    # -- punto di presa misurato -------------------------------------------- #
    # ``quality`` arriva dal probe dei fotogrammi. Non e' un permesso: e' una
    # misura. Una clip senza istanti sopra il pavimento non entra nel piano (se
    # resta qualcosa da montare) e le sue note dicono la misura che l'ha
    # esclusa. Una clip con materiale buono si monta dalla finestra utile, con
    # il punto di presa sul picco: la durata pianificata non cambia.
    verdicts: Dict[str, Mapping[str, Any]] = {}
    if isinstance(quality, Mapping):
        for key, value in quality.items():
            if isinstance(value, Mapping):
                verdicts[str(key)] = value
    quality_notes: List[str] = []
    excluded_ids: List[str] = []
    for entry in a_entries:
        asset_id = entry.get("asset_id")
        row = verdicts.get(str(asset_id or "")) or {}
        if row.get("excluded"):
            excluded_ids.append(str(asset_id))
    original_total = len(a_entries)
    if excluded_ids and len(excluded_ids) < original_total:
        kept = [
            entry
            for entry in a_entries
            if str(entry.get("asset_id")) not in excluded_ids
        ]
        for asset_id in excluded_ids:
            row = verdicts.get(asset_id) or {}
            quality_notes.append(
                "clip esclusa dal piano: {asset} ({why}); {count} su {total} "
                "a_roll, la timeline usa il resto".format(
                    asset=asset_id,
                    why=str(row.get("why") or "nessun istante utilizzabile"),
                    count=len(excluded_ids),
                    total=original_total,
                )
            )
        a_entries = kept
    elif excluded_ids:
        for asset_id in excluded_ids:
            row = verdicts.get(asset_id) or {}
            quality_notes.append(
                "nessuna clip utilizzabile, si monta lo stesso {asset}: {why}".format(
                    asset=asset_id,
                    why=str(row.get("why") or "nessun istante utilizzabile"),
                )
            )

    def _take_window(
        asset_id: str,
    ) -> Tuple[float, float, float, Mapping[str, Any]]:
        """``(usable_start, usable_end, in_point, verdict)`` per un a-roll."""
        row = verdicts.get(str(asset_id or "")) or {}
        total = _media_duration(asset_id)
        if not row or row.get("excluded"):
            return 0.0, total, 0.0, {}
        try:
            start = float(row.get("usable_start") or 0.0)
        except (TypeError, ValueError):
            start = 0.0
        try:
            point = float(row.get("src_in") or 0.0)
        except (TypeError, ValueError):
            point = start
        raw_end = row.get("usable_end")
        try:
            end = float(raw_end) if raw_end is not None else total
        except (TypeError, ValueError):
            end = total
        start = max(0.0, min(start, total))
        end = max(start, min(end, total))
        point = max(start, min(point, end))
        if end - start <= 0.0:
            return 0.0, total, 0.0, row
        return start, end, point, row

    def _take_runs(asset_id: str) -> List[Tuple[float, float]]:
        """Le corse utilizzabili di un a-roll, in ordine, dentro la sorgente.

        ``usable_runs`` e' la misura del probe (tutte le corse, non solo la
        migliore). Un verdetto vecchio senza corse ricade sulla finestra unica
        ``[usable_start, usable_end]``: stesso piano di prima, nessuna
        invenzione.
        """
        row = verdicts.get(str(asset_id or "")) or {}
        total = _media_duration(asset_id)
        if row.get("excluded"):
            return []
        raw = row.get("usable_runs")
        runs: List[Tuple[float, float]] = []
        if isinstance(raw, (list, tuple)) and raw:
            for item in raw:
                if not isinstance(item, Mapping):
                    continue
                try:
                    run_start = float(item.get("start"))
                    run_end = float(item.get("end"))
                except (TypeError, ValueError):
                    continue
                run_start = max(0.0, min(run_start, total))
                run_end = max(run_start, min(run_end, total))
                if run_end - run_start > 1e-9:
                    runs.append((run_start, run_end))
        if not runs:
            start, end, _point, _row = _take_window(asset_id)
            if end - start > 1e-9:
                runs.append((start, end))
        return runs

    def _ordered_pieces(
        runs: Sequence[Tuple[float, float]], point: float, first: bool
    ) -> List[Tuple[float, float]]:
        """Le corse nell'ordine in cui consumarle.

        La prima clip entra dal punto di presa preferito (l'hook) e poi usa il
        resto della sua corsa e le altre corse: nessun secondo della clip va
        sprecato. Le clip successive partono dalla prima corsa e proseguono in
        ordine, senza tornare indietro.
        """
        if not runs:
            return []
        if not first:
            return list(runs)
        index = 0
        for position, (run_start, run_end) in enumerate(runs):
            if run_start - 1e-9 <= point <= run_end + 1e-9:
                index = position
                break
        run_start, run_end = runs[index]
        start = max(run_start, min(point, run_end))
        pieces: List[Tuple[float, float]] = []
        if run_end - start > 1e-9:
            pieces.append((start, run_end))
        pieces.extend(runs[index + 1 :])
        pieces.extend(runs[:index])
        if start - run_start > 1e-9:
            pieces.append((run_start, start))
        return pieces

    if not a_entries:
        raise VxError("payload.asset_triage has no a_roll: cannot build a timeline")

    # ONE projection asset_id -> EDL key, in the payload's own order. The asset
    # id is never rewritten: the report, the triage block and dedup keep the
    # human-readable name, only the EDL carries a slug.
    source_ids: Dict[str, str] = {}
    taken_keys: Set[str] = set()
    for entry in selected:
        asset_id = entry.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id or asset_id in source_ids:
            continue
        key = source_key(asset_id, taken_keys)
        source_ids[asset_id] = key
        taken_keys.add(key)

    def _source_id(asset_id: str) -> str:
        key = source_ids.get(asset_id)
        if key is None:
            raise VxError(f"asset {asset_id!r} has no EDL source_id")
        return key

    referenced: Dict[str, Any] = {}
    a_clips: List[Any] = []
    dialogue_clips: List[Any] = []
    cursor = 0.0
    scene_block = payload.get("scene")
    requested = (
        float(scene_block.get("duration_sec"))
        if isinstance(scene_block, Mapping)
        and isinstance(scene_block.get("duration_sec"), (int, float))
        and float(scene_block.get("duration_sec")) > 0
        else 0.0
    )

    # Per-entry usable length, in payload order. A requested duration truncates
    # the last one; the beat grid then works on exactly this material. The
    # usable window is the material the probe accepts (only truly unusable
    # stretches are gone, never more than 25%): the take point inside it moves,
    # the planned shot lengths do not.
    lengths: List[float] = []
    starts: List[float] = []
    ends: List[float] = []
    points: List[float] = []
    totals: List[float] = []
    remaining = requested
    for index, entry in enumerate(a_entries):
        asset_id = entry.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise VxError(f"selected a_roll[{index}] has no asset_id")
        start, end, point, _row = _take_window(asset_id)
        length = min(_target(entry, asset_id), end - start)
        if requested > 0.0:
            if remaining <= 1e-3:
                break
            length = min(length, remaining)
        if length <= 0:
            raise VxError(f"asset {asset_id!r} has no usable duration")
        lengths.append(length)
        starts.append(start)
        ends.append(end)
        points.append(point)
        totals.append(_media_duration(asset_id))
        if requested > 0.0:
            remaining -= length
    if not lengths:
        raise VxError("a_roll total duration is zero")
    plain_total = sum(lengths)

    # -- the timeline lands on a whole frame -------------------------------- #
    # A request of N seconds is N seconds only if it is a whole number of frames:
    # N at 25 fps is 375 frames, N at 30 fps is 450. The material is extended or
    # trimmed at the tail so the timeline ends exactly on that frame, on the same
    # beat grid: the last shot is allowed to be shorter (or longer) than its
    # neighbour, and the caller declares the difference. When the material cannot
    # reach the frame, the budget stops at the last whole frame the material can
    # fill: the maximum reachable is declared, never invented.
    if requested > 0.0 and float(fps) > 0:
        rate = float(fps)
        frame_target = int(round(requested * rate)) / rate
        capacity = sum(max(0.0, totals[i]) for i in range(len(lengths)))
        if capacity + 1e-9 < frame_target:
            reachable_frames = int(math.floor(capacity * rate + 1e-9))
            frame_target = reachable_frames / rate if reachable_frames > 0 else frame_target
        deficit = frame_target - plain_total
        if deficit > 1e-9:
            # Grow from the tail, on the source (the quality window is a
            # preference for the take point, not a wall: the extra second the
            # prompt asks for comes from the same clip, even outside it).
            for index in range(len(lengths) - 1, -1, -1):
                room = max(0.0, totals[index] - lengths[index])
                add = min(room, deficit)
                if add > 0.0:
                    lengths[index] += add
                    deficit -= add
                if deficit <= 1e-9:
                    break
            for index in range(len(lengths)):
                if lengths[index] > (ends[index] - starts[index]) + 1e-9:
                    starts[index] = 0.0
                    ends[index] = max(lengths[index], min(totals[index], ends[index]))
                if points[index] + lengths[index] > ends[index] + 1e-9:
                    points[index] = max(starts[index], ends[index] - lengths[index])
            plain_total = sum(lengths)
        elif deficit < -1e-9:
            # The tail is shortened from the last clip backwards; each clip keeps
            # at least one frame. It is the last beat that gets trimmed, and the
            # report's `timeline.tail` declares the difference.
            to_cut = -deficit
            floor = 1.0 / rate
            for index in range(len(lengths) - 1, -1, -1):
                room = max(0.0, lengths[index] - floor)
                take = min(room, to_cut)
                if take > 0.0:
                    lengths[index] -= take
                    to_cut -= take
                if to_cut <= 1e-9:
                    break
            plain_total = sum(lengths)

    # -- beat grid ---------------------------------------------------------- #
    # The BPM used to sit in the brief while the planner declared it could not
    # cut on it. Now it decides the boundaries: the same grid grammar.snap_cuts
    # uses, with provenance written onto every clip.
    beat_block = payload.get("beat_grid")
    beat_block = beat_block if isinstance(beat_block, Mapping) else {}
    pacing_block = {}
    context_block = payload.get("execution_context")
    if isinstance(context_block, Mapping):
        creative = context_block.get("creative_rationale")
        if isinstance(creative, Mapping):
            pacing_block = creative.get("pacing") or {}
    try:
        beat_bpm = float(beat_block.get("bpm") or 0.0)
    except (TypeError, ValueError):
        beat_bpm = 0.0
    if beat_bpm <= 0:
        raw_bpm = pacing_block.get("bpm_target")
        if isinstance(raw_bpm, (int, float)) and not isinstance(raw_bpm, bool):
            beat_bpm = float(raw_bpm)
    beat_mode = str(beat_block.get("mode") or "auto")
    beat_enabled = bool(beat_block.get("enabled"))
    if "enabled" not in beat_block:
        beat_enabled = beat_mode != "off" and beat_bpm > 0 and str(
            pacing_block.get("cut_policy") or ""
        ) in ("beat_synced", "onset_synced")
    if beat_mode == "off":
        beat_enabled = False

    beat_plan = None
    if beat_enabled and beat_bpm > 0:
        from . import grammar

        materials = []
        for index, entry in enumerate(a_entries[: len(lengths)]):
            asset_id = entry.get("asset_id")
            materials.append(
                {
                    "label": str(asset_id),
                    "asset_id": asset_id,
                    "duration": float(lengths[index]),
                }
            )
        try:
            subdivision = int(beat_block.get("subdivision") or 1)
        except (TypeError, ValueError):
            subdivision = 1
        if subdivision not in (1, 2, 4):
            subdivision = 1
        try:
            offset = float(beat_block.get("offset") or 0.0)
        except (TypeError, ValueError):
            offset = 0.0
        beat_plan = grammar.plan_beat_shots(
            materials,
            bpm=beat_bpm,
            fps=float(fps),
            subdivision=subdivision,
            offset=offset,
            energy=beat_block.get("energy"),
            total=plain_total,
            method=f"beat-grid:{beat_block.get('source') or 'auto'}",
        )

    hook_split = False
    if beat_plan is not None and beat_plan.shots:
        cursors: Dict[int, float] = {}
        for index, shot in enumerate(beat_plan.shots):
            asset_id = shot.get("asset_id")
            entry_index = int(shot.get("source_index", 0))
            if not isinstance(asset_id, str) or not asset_id:
                asset_id = a_entries[entry_index].get("asset_id")
            if not isinstance(asset_id, str) or not asset_id:
                raise VxError("beat plan shot has no asset_id")
            begin = float(starts[entry_index]) if entry_index < len(starts) else 0.0
            stop = float(ends[entry_index]) if entry_index < len(ends) else begin
            row = verdicts.get(asset_id) or {}
            wrap = float(row.get("wrap_start") or begin)
            wrap = max(begin, min(wrap, stop))
            if entry_index not in cursors:
                cursors[entry_index] = (
                    float(points[entry_index])
                    if entry_index < len(points)
                    else begin
                )
            cursor = cursors[entry_index]
            take = float(shot["src_out"]) - float(shot["src_in"])
            if cursor + take > stop + 1e-9:
                # Il materiale utile e' finito: si riparte dalla testa
                # preferita della finestra (mai dallo scarto sotto il
                # pavimento). La durata dello shot non cambia.
                cursor = wrap
            if cursor + take > stop + 1e-9:
                take = max(0.0, stop - cursor)
            src_in = round(cursor, 6)
            src_out = round(cursor + take, 6)
            cursors[entry_index] = cursor + take
            ref = _source_ref(asset_id)
            referenced[_source_id(asset_id)] = ref
            provenance = (dict(shot.get("provenance") or {"kind": "beat"}),)
            a_clips.append(
                edl_plan.ClipSegment(
                    clip_id=f"v0_{index}",
                    source_id=_source_id(asset_id),
                    src_in=src_in,
                    src_out=src_out,
                    dst_in=float(shot["dst_in"]),
                    dst_out=float(shot["dst_out"]),
                    layer=0,
                    role="main_talking_head",
                    provenance=provenance,
                )
            )
            dialogue_clips.append(
                edl_plan.ClipSegment(
                    clip_id=f"a0_{index}",
                    source_id=_source_id(asset_id),
                    src_in=src_in,
                    src_out=src_out,
                    dst_in=float(shot["dst_in"]),
                    dst_out=float(shot["dst_out"]),
                    layer=0,
                    role="dialogue",
                    gain_db=0.0,
                    provenance=provenance,
                )
            )
        cursor = beat_plan.total
    else:
        # Nessuna griglia: il piano riempie la durata chiesta consumando le
        # corse utilizzabili, non la sola corsa del picco. La prima clip entra
        # dal punto di presa preferito (l'hook), le successive proseguono in
        # ordine. Ogni ``src_in`` cade dentro una corsa dichiarata: mai nel
        # nero, nel freeze o sotto il pavimento.
        #
        # La composizione aggiunge **un** taglio, e solo se è stata misurata:
        # il primo pezzo dell'apertura si spezza in hook + corpo. Con la griglia
        # BPM accesa non si tocca niente: lì i tagli li decide il tempo.
        hook_left = hook_seconds
        hook_split = False
        min_tail = max(
            float(THRESHOLDS.get("composition_hook_tail_sec") or 0.5),
            2.0 / float(fps),
        )
        for index, entry in enumerate(a_entries[: len(lengths)]):
            asset_id = entry.get("asset_id")
            if not isinstance(asset_id, str) or not asset_id:
                raise VxError(f"selected a_roll[{index}] has no asset_id")
            ref = _source_ref(asset_id)
            budget = float(lengths[index])
            point = float(points[index]) if index < len(points) else 0.0
            pieces = _ordered_pieces(_take_runs(asset_id), point, index == 0)
            remaining = budget
            sub = 0
            for run_start, run_end in pieces:
                if remaining <= 1e-9:
                    break
                take = min(run_end - run_start, remaining)
                if take <= 1e-9:
                    continue
                chunks = [take]
                if hook_left is not None and take > hook_left + min_tail:
                    chunks = [hook_left, take - hook_left]
                    hook_split = True
                hook_left = None
                offset = run_start
                for chunk in chunks:
                    src_in = round(offset, 6)
                    src_out = round(offset + chunk, 6)
                    referenced[_source_id(asset_id)] = ref
                    a_clips.append(
                        edl_plan.ClipSegment(
                            clip_id=f"v0_{index}_{sub}",
                            source_id=_source_id(asset_id),
                            src_in=src_in,
                            src_out=src_out,
                            dst_in=cursor,
                            dst_out=cursor + chunk,
                            layer=0,
                            role="main_talking_head",
                        )
                    )
                    dialogue_clips.append(
                        edl_plan.ClipSegment(
                            clip_id=f"a0_{index}_{sub}",
                            source_id=_source_id(asset_id),
                            src_in=src_in,
                            src_out=src_out,
                            dst_in=cursor,
                            dst_out=cursor + chunk,
                            layer=0,
                            role="dialogue",
                            gain_db=0.0,
                        )
                    )
                    cursor += chunk
                    remaining -= chunk
                    sub += 1
                    offset = src_out
    duration = cursor
    if duration <= 0:
        raise VxError("a_roll total duration is zero")
    beat_notes: List[str] = []
    if beat_plan is not None:
        beat_notes.append(
            f"beat grid {beat_plan.bpm:.2f} BPM: {len(beat_plan.cuts)} tagli, "
            f"{beat_plan.off_grid} fuori griglia, densita' {beat_plan.density:.2f}/s"
        )

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
    # La coda protetta e la politica degli slot vengono dalla composizione, se
    # c'è: un b-roll che entra su un taglio misurabile non sembra un errore di
    # montaggio, e la coda di una CTA non si copre. Senza blocco, tutto come
    # prima (slot uniformi, nessuna coda).
    placement = str(proposed.get("broll_placement") or "")
    tail_sec = 0.0
    raw_tail = proposed.get("tail_seconds")
    if isinstance(raw_tail, (int, float)) and not isinstance(raw_tail, bool):
        if math.isfinite(float(raw_tail)) and float(raw_tail) > 0:
            tail_sec = float(raw_tail)
    usable_end = max(0.0, duration - tail_sec)
    # One b-roll per slot: the pool cannot overlap on layer 1, so an overlay is
    # never longer than the gap between two placements. Con una coda protetta la
    # distribuzione usa lo spazio che resta: due b-roll su 6 s con 1,5 s di coda
    # stanno in 4,5 s, non in 6.
    b_slot = (usable_end / float(n_broll + 1)) if n_broll else duration
    snap_tolerance = float(THRESHOLDS.get("composition_broll_snap_sec") or 0.75)
    cuts = sorted(
        {
            round(float(clip.dst_in), 6)
            for clip in a_clips
            if float(clip.dst_in) > 1e-6
        }
    )
    previous_end = 0.0
    raw_slots = [
        usable_end * (index + 1) / (n_broll + 1) for index in range(n_broll)
    ]
    for index, entry in enumerate(b_entries):
        asset_id = entry.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise VxError(f"selected b_roll[{index}] has no asset_id")
        ref = _source_ref(asset_id)
        media_duration = _media_duration(asset_id)
        length = min(_target(entry, asset_id), media_duration, duration, b_slot)
        if length <= 0:
            raise VxError(f"asset {asset_id!r} has no usable duration")
        computed = False
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
            at = raw_slots[index]
            computed = True
        if computed and placement == "nearest_cut" and cuts:
            room = max(0.0, duration - length - tail_sec)
            # Lo slot successivo resta dove sarebbe stato: uno snap che gli
            # ruba lo spazio creerebbe una sovrapposizione su layer 1.
            next_start = (
                raw_slots[index + 1] if index + 1 < n_broll else usable_end
            )
            near = [
                cut
                for cut in cuts
                if cut <= room + 1e-9
                and cut >= previous_end - 1e-9
                and cut + length <= next_start + 1e-9
            ]
            if near:
                candidate = min(near, key=lambda cut: abs(cut - at))
                if abs(candidate - at) <= snap_tolerance + 1e-9:
                    at = candidate
        max_start = max(0.0, duration - length - tail_sec)
        dst_in = clamp(at, 0.0, max_start)
        previous_end = dst_in + length
        fade = float(THRESHOLDS["broll_fade_sec"])
        if fade > length:
            fade = length
        referenced[_source_id(asset_id)] = ref
        b_clips.append(
            edl_plan.ClipSegment(
                clip_id=f"v1_{index}",
                source_id=_source_id(asset_id),
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

    max_music = int(THRESHOLDS["triage_max_music"])
    if len(music_entries) > max_music:
        raise VxError(
            f"payload.asset_triage selected more than {max_music} music assets"
        )
    music_clips: List[edl_plan.ClipSegment] = []
    for index, entry in enumerate(music_entries):
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
        referenced[_source_id(asset_id)] = ref
        role = "music" if len(music_entries) == 1 else f"music_{chr(97 + index)}"
        music_clips.append(
            edl_plan.ClipSegment(
                clip_id=f"a{index + 1}_0",
                source_id=_source_id(asset_id),
                src_in=0.0,
                src_out=music_length,
                dst_in=0.0,
                dst_out=music_length,
                layer=0,
                role=role,
                gain_db=gain,
            )
        )

    video_tracks = [edl_plan.Track(track_id=0, clips=tuple(a_clips), role="a_roll")]
    if b_clips:
        video_tracks.append(
            edl_plan.Track(track_id=1, clips=tuple(b_clips), role="b_roll")
        )
    audio_tracks = [
        edl_plan.Track(track_id=0, clips=tuple(dialogue_clips), role="dialogue")
    ]
    for index, clip in enumerate(music_clips):
        audio_tracks.append(
            edl_plan.Track(track_id=index + 1, clips=(clip,), role=clip.role)
        )

    notes: List[str] = []
    payload_version = payload.get("contract_version") or "?"
    notes.append(f"payload v{payload_version} -> EDL v{edl_plan.EDL_CONTRACT_VERSION}")
    if hook_split and hook_seconds is not None:
        notes.append(
            "composizione: hook di {seconds:.3f}s sull'apertura {asset}"
            " (spezzato dal corpo, stesso punto di presa)".format(
                seconds=hook_seconds,
                asset=str(proposed.get("hook_asset") or "?"),
            )
        )
    if proposed.get("broll_order"):
        notes.append(
            "composizione: b-roll in ordine "
            + ", ".join(str(item) for item in proposed["broll_order"])
        )
    notes.extend(beat_notes)
    notes.extend(quality_notes)
    for index, entry in enumerate(a_entries[: len(lengths)]):
        asset_id = str(entry.get("asset_id") or "")
        start = float(starts[index]) if index < len(starts) else 0.0
        point = float(points[index]) if index < len(points) else start
        row = verdicts.get(asset_id) or {}
        if row.get("quality") != "measured":
            continue
        if start > 1e-9:
            notes.append(
                "clip {asset}: tolti {cut:.3f}s sotto il pavimento assoluto "
                "(niente nero/vetro); il materiale utile parte da {start:.3f}s".format(
                    asset=asset_id,
                    cut=start,
                    start=start,
                )
            )
        if point > start + 1e-9:
            notes.append(
                "punto di presa misurato: {asset} da {point:.3f}s "
                "(nitidezza {sharp:.1f}, soglia preferita {pref:.1f}, "
                "finestra utile fino a {end:.3f}s)".format(
                    asset=asset_id,
                    point=point,
                    sharp=float(row.get("sharpness") or 0.0),
                    pref=float((row.get("thresholds") or {}).get("preferred") or 0.0),
                    end=float(row.get("usable_end") or point),
                )
            )
        if row.get("short_window"):
            notes.append(
                "finestra utile piu' corta della battuta: la coda e' off_grid, "
                "dichiarata nel piano di battuta"
            )
    if b_clips and not any_anchor:
        notes.append("b-roll distribuiti uniformemente: nessun ancoraggio dichiarato")
    if not music_clips:
        notes.append("nessuna traccia musicale")
    else:
        for index, clip in enumerate(music_clips):
            length = float(clip.src_out)
            if length < duration - 1e-9:
                notes.append(
                    f"musica {index + 1}/{len(music_clips)} più corta della timeline: "
                    f"{length:.3f}s su {duration:.3f}s"
                )
    if not dialogue_clips:
        notes.append("nessun bus dialogo: ducking non dichiarato")
    notes.append(f"asset scartati ignorati: {rejected_count}")

    if music_clips and dialogue_clips and len(music_clips) == 1:
        ducking = edl_plan.DuckingSpec()
        ducking_tracks: tuple = ()
    elif music_clips and dialogue_clips:
        ducking = None
        primary = edl_plan.DuckingSpec()
        beds = []
        for index, clip in enumerate(music_clips):
            if index == 0:
                beds.append(
                    edl_plan.DuckingSpec(
                        target_role=clip.role,
                        threshold=primary.threshold,
                        ratio=primary.ratio,
                        attack_ms=primary.attack_ms,
                        release_ms=primary.release_ms,
                        makeup_db=primary.makeup_db,
                    )
                )
            else:
                # The secondary bed steps back further than the lead bed: it
                # triggers earlier and ducks deeper, so two beds never fight for
                # the same pocket. A policy, not a measurement, and it is declared
                # per track in the report.
                beds.append(
                    edl_plan.DuckingSpec(
                        target_role=clip.role,
                        threshold=min(1.0, primary.threshold * 1.6),
                        ratio=min(20.0, primary.ratio * 1.5),
                        attack_ms=primary.attack_ms,
                        release_ms=primary.release_ms,
                        makeup_db=primary.makeup_db,
                    )
                )
        ducking_tracks = tuple(beds)
    else:
        ducking = None
        ducking_tracks = ()

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
        ducking_tracks=ducking_tracks,
        notes=tuple(notes),
    )
