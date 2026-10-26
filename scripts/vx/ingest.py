"""vx.ingest — stage 00: VFR -> CFR, HDR -> BT.709 SDR, SAR -> 1:1, autorotate.

Every measurement downstream (luma percentiles, face boxes, cut density) assumes
a constant frame rate and an SDR BT.709 timeline. This module produces that
timeline once, or returns the original path when it already conforms.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from . import ffmpeg as ff
from . import probe as probe_mod
from .contract import even


HDR_TRANSFER = {"smpte2084", "arib-std-b67"}
HDR_SPACES = {"bt2020nc", "bt2020c", "bt2020"}
DEFAULT_TARGET_FPS = 25.0
VFR_DELTA_TOLERANCE = 0.02
VFR_DELTA_FRACTION = 0.05

# Stage 00 keeps the analysis/render source small enough to decode cheaply while
# still feeding a 1080x1920 crop without upscaling. 1.5x the 1080p area is the
# ceiling: above it the source is downscaled, below it the source is left alone.
MAX_INGEST_PIXELS = 1920 * 1080 * 1.5
MIN_DELIVERY_W = 1080
MIN_DELIVERY_H = 1920


class IngestError(RuntimeError):
    """Staging failed; the source is untouched."""


def _safe_label(label: Optional[str], fallback: str) -> str:
    raw = f"{label}_{fallback}" if label else fallback
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw)


@dataclass(frozen=True)
class IngestResult:
    source_path: str
    staged_path: str
    is_staged: bool
    was_vfr: bool
    was_hdr: bool
    was_sar: bool
    rotation: int
    target_fps: float
    filters_applied: Tuple[str, ...]
    vfr_fraction: Optional[float] = None
    was_downscaled: bool = False
    downscale: Optional[Dict[str, int]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _parse_rate(value: object) -> float:
    if not value or str(value) in ("0/0", "N/A"):
        return 0.0
    text = str(value)
    if "/" in text:
        num, _, den = text.partition("/")
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _aspect(value: object) -> float:
    if not value:
        return 1.0
    text = str(value)
    if ":" in text:
        num, _, den = text.partition(":")
        try:
            return float(num) / float(den) if float(den) else 1.0
        except ValueError:
            return 1.0
    try:
        return float(text)
    except ValueError:
        return 1.0


def _video_stream(probe_info: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(probe_info.get("streams"), list):
        video = next(
            (s for s in probe_info["streams"] if s.get("codec_type") == "video"),
            None,
        )
        if video:
            return video
    if probe_info.get("codec_type") == "video":
        return probe_info
    color = probe_info.get("color") or {}
    fps = float(probe_info.get("fps") or 0.0)
    return {
        "codec_type": "video",
        "avg_frame_rate": f"{fps}/1",
        "r_frame_rate": f"{fps}/1",
        "pix_fmt": color.get("pix_fmt") or probe_info.get("pix_fmt"),
        "color_transfer": color.get("color_transfer"),
        "color_space": color.get("color_space"),
        "rotation": probe_info.get("rotation") or 0,
        "sample_aspect_ratio": "1:1",
    }


def measure_frame_deltas(
    path: str | Path, seconds: int = 10
) -> Tuple[Optional[float], Optional[float]]:
    """Fraction of PTS deltas that deviate from the median, and the median itself."""
    proc = ff.run_fp(
        [
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "csv=p=0",
            "-read_intervals",
            f"%+{seconds}",
            str(path),
        ]
    )
    if not proc.ok:
        return None, None
    times = []
    for line in proc.stdout.splitlines():
        text = line.strip().rstrip(",")
        try:
            times.append(float(text))
        except ValueError:
            continue
    deltas = [b - a for a, b in zip(times, times[1:]) if b > a]
    if len(deltas) < 10:
        return None, None
    median = statistics.median(deltas)
    if median <= 0:
        return None, None
    off = sum(1 for d in deltas if abs(d - median) / median > VFR_DELTA_TOLERANCE)
    return off / len(deltas), median


def detect_vfr(
    video_stream: Dict[str, Any], path: str | Path | None = None
) -> Tuple[bool, float]:
    r_fps = _parse_rate(video_stream.get("r_frame_rate"))
    avg_fps = _parse_rate(video_stream.get("avg_frame_rate"))
    target = avg_fps or r_fps or DEFAULT_TARGET_FPS
    fraction, median = measure_frame_deltas(path) if path else (None, None)
    if median:
        target = 1.0 / median
    if fraction is not None:
        return fraction > VFR_DELTA_FRACTION, round(target, 3)
    if avg_fps > 0 and r_fps > 0:
        return abs(r_fps - avg_fps) / avg_fps > 0.01, round(target, 3)
    return True, round(target, 3)


def detect_hdr(video_stream: Dict[str, Any]) -> bool:
    transfer = str(video_stream.get("color_transfer") or "").lower()
    space = str(video_stream.get("color_space") or "").lower()
    pix_fmt = str(video_stream.get("pix_fmt") or "").lower()
    if transfer in HDR_TRANSFER:
        return True
    if space in HDR_SPACES:
        return True
    if ("10le" in pix_fmt or "10be" in pix_fmt) and transfer not in ("bt709", ""):
        return space not in ("bt709", "")
    return False


def detect_sar(video_stream: Dict[str, Any]) -> bool:
    return abs(_aspect(video_stream.get("sample_aspect_ratio")) - 1.0) > 1e-3


def detect_rotation(video_stream: Dict[str, Any]) -> int:
    for side in video_stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(float(side["rotation"]))
            except (TypeError, ValueError):
                continue
    try:
        return int(float((video_stream.get("tags") or {}).get("rotate", 0) or 0))
    except (TypeError, ValueError):
        return 0


def effective_dimensions(
    width: int, height: int, *, sar: float = 1.0, rotation: int = 0
) -> Tuple[int, int]:
    """Display-oriented, even dimensions: de-squeeze, rotate, round to even."""
    w = even(int(width) * float(sar))
    h = even(int(height))
    if abs(int(rotation)) % 180 == 90:
        w, h = h, w
    return w, h


def plan_downscale(
    width: int,
    height: int,
    *,
    sar: float = 1.0,
    rotation: int = 0,
    min_w: int = MIN_DELIVERY_W,
    min_h: int = MIN_DELIVERY_H,
    area_guard: float = MAX_INGEST_PIXELS,
) -> Optional[Tuple[int, int]]:
    """Target size for stage 00, or None when the source already fits.

    The target keeps the source aspect ratio and is the smallest scale that
    still lets a 1080x1920 crop be taken without upscaling. A source whose
    quality floor would require an upscale is left alone.
    """
    dw, dh = effective_dimensions(width, height, sar=sar, rotation=rotation)
    if dw <= 0 or dh <= 0:
        return None
    if dw * dh <= area_guard:
        return None
    crop_w0 = min(dw, dh * 9.0 / 16.0)
    crop_h0 = min(dh, dw * 16.0 / 9.0)
    if crop_w0 <= 0 or crop_h0 <= 0:
        return None
    s = max(min_w / crop_w0, min_h / crop_h0)
    if s >= 1.0:
        return None
    return (even(dw * s), even(dh * s))


def detect_downscale(video_stream: Dict[str, Any]) -> Tuple[bool, int, int]:
    """(needs_downscale, display_w, display_h) for a raw ffprobe video stream."""
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    sar = _aspect(video_stream.get("sample_aspect_ratio"))
    rotation = detect_rotation(video_stream)
    dw, dh = effective_dimensions(width, height, sar=sar, rotation=rotation)
    plan = plan_downscale(width, height, sar=sar, rotation=rotation)
    return plan is not None, dw, dh


def build_downscale_filter(width: int, height: int) -> Tuple[str, str]:
    """(filter, label) for the stage-00 downscale, or ("", "") when not needed."""
    plan = plan_downscale(width, height)
    if plan is None:
        return "", ""
    w, h = plan
    return (
        f"scale={w}:{h}:flags=lanczos",
        f"downscale_4k_to_1080p ({width}x{height} -> {w}x{h})",
    )


def build_ingest_filtergraph(
    *,
    was_vfr: bool,
    was_hdr: bool,
    was_sar: bool,
    target_fps: float,
    downscale: Optional[Tuple[int, int]] = None,
) -> Tuple[str, Tuple[str, ...]]:
    parts = []
    applied = []
    if was_sar:
        if downscale is None:
            parts.extend(["scale=trunc(iw*sar/2)*2:ih", "setsar=1"])
        else:
            # The explicit target already carries the de-squeezed width, so the
            # intermediate SAR scale would apply the stretch twice.
            parts.append("setsar=1")
        applied.append("scale_sar_to_square_pixels")
    else:
        parts.append("setsar=1")
        applied.append("setsar=1")
    if downscale is not None:
        w, h = downscale
        parts.append(f"scale={w}:{h}:flags=lanczos")
        # scale recomputes SAR to preserve DAR; force square pixels again so the
        # staged timeline stays 1:1 like every other stage-00 output.
        parts.append("setsar=1")
        applied.append(f"downscale={w}x{h}")
    if was_hdr:
        ff.require("zscale")
        ff.require("tonemap")
        parts.extend(
            [
                "zscale=t=linear:npl=100",
                "format=gbrpf32le",
                "zscale=p=bt709",
                "tonemap=hable:desat=0.0",
                "zscale=t=bt709:m=bt709:r=limited",
            ]
        )
        applied.append("tonemap_hdr_to_bt709")
    if was_vfr:
        parts.append(f"fps=fps={target_fps:g}")
        applied.append(f"fps={target_fps:g}")
    parts.append("format=yuv420p")
    return "[0:v]" + ",".join(parts) + "[v]", tuple(applied)


def normalize_source(
    source_path: str | Path,
    probe_info: Dict[str, Any],
    stage_dir: str | Path,
    *,
    label: Optional[str] = None,
) -> IngestResult:
    src = Path(source_path)
    if not src.exists():
        raise FileNotFoundError(f"sorgente non trovata: {src}")
    stream = _video_stream(probe_info)
    fraction, median = measure_frame_deltas(src)
    was_vfr, target_fps = detect_vfr(stream)
    if fraction is not None:
        was_vfr = fraction > VFR_DELTA_FRACTION
    if median:
        target_fps = 1.0 / median
    was_hdr = detect_hdr(stream)
    was_sar = detect_sar(stream)
    rotation = detect_rotation(stream)
    target_fps = round(target_fps, 3)

    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    sar = _aspect(stream.get("sample_aspect_ratio"))
    downscale = plan_downscale(width, height, sar=sar, rotation=rotation)
    was_downscaled = downscale is not None

    if not (was_vfr or was_hdr or was_sar or rotation % 360 or was_downscaled):
        return IngestResult(
            source_path=str(src.resolve()),
            staged_path=str(src.resolve()),
            is_staged=False,
            was_vfr=False,
            was_hdr=False,
            was_sar=False,
            rotation=rotation,
            target_fps=target_fps,
            filters_applied=(),
            vfr_fraction=fraction,
            was_downscaled=False,
            downscale=None,
        )

    out_dir = Path(stage_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_label(label, src.stem)
    staged = out_dir / f"stage_00_ingest_{stem}.mp4"
    graph, applied = build_ingest_filtergraph(
        was_vfr=was_vfr,
        was_hdr=was_hdr,
        was_sar=was_sar,
        target_fps=target_fps,
        downscale=downscale,
    )
    script = out_dir / f"stage_00_ingest_{stem}.txt"
    script.write_text(graph, encoding="utf-8")
    proc = ff.run_ff(
        [
            "-i",
            str(src),
            "-filter_complex_script",
            str(script),
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-crf",
            "17",
            "-preset",
            "fast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(staged),
        ],
        loglevel="error",
    )
    if not proc.ok:
        raise IngestError(f"staging fallito: {proc.tail(12)}")
    if not staged.is_file() or staged.stat().st_size == 0:
        raise IngestError("staging non ha prodotto un file")
    probe_mod.probe(str(staged))
    downscale_info = None
    if downscale is not None:
        display_w, display_h = effective_dimensions(
            width, height, sar=sar, rotation=rotation
        )
        downscale_info = {
            "src_w": display_w,
            "src_h": display_h,
            "dst_w": downscale[0],
            "dst_h": downscale[1],
        }
    return IngestResult(
        source_path=str(src.resolve()),
        staged_path=str(staged.resolve()),
        is_staged=True,
        was_vfr=was_vfr,
        was_hdr=was_hdr,
        was_sar=was_sar,
        rotation=rotation,
        target_fps=target_fps,
        filters_applied=applied,
        vfr_fraction=fraction,
        was_downscaled=was_downscaled,
        downscale=downscale_info,
    )


def ingest_sources(
    sources_map: Mapping[str, str | Path],
    stage_dir: str | Path,
    *,
    progress=None,
) -> Dict[str, IngestResult]:
    """Stage every declared source through stage 00, keyed by ``source_id``.

    Each source is probed and normalized independently (VFR, HDR, SAR,
    rotation, 4K guard), so a multi-source plan gets one consistent timeline
    per source. The staged filename carries the ``source_id``: two different
    files with the same stem must not overwrite each other.
    """
    if not sources_map:
        raise IngestError("sources_map is empty")
    results: Dict[str, IngestResult] = {}
    for source_id, path in sources_map.items():
        if not isinstance(source_id, str) or not source_id:
            raise IngestError(f"source_id non valido: {source_id!r}")
        info = probe_mod.probe(str(path)).to_dict()
        if progress is not None:
            progress(source_id, str(path))
        results[source_id] = normalize_source(
            str(path), info, stage_dir, label=source_id
        )
    return results
