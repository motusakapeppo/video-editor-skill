"""vx.ingest — stage 00: VFR -> CFR, HDR -> BT.709 SDR, SAR -> 1:1, autorotate.

Every measurement downstream (luma percentiles, face boxes, cut density) assumes
a constant frame rate and an SDR BT.709 timeline. This module produces that
timeline once, or returns the original path when it already conforms.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import ffmpeg as ff
from . import probe as probe_mod


HDR_TRANSFER = {"smpte2084", "arib-std-b67"}
HDR_SPACES = {"bt2020nc", "bt2020c", "bt2020"}
DEFAULT_TARGET_FPS = 25.0
VFR_DELTA_TOLERANCE = 0.02
VFR_DELTA_FRACTION = 0.05


class IngestError(RuntimeError):
    """Staging failed; the source is untouched."""


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


def build_ingest_filtergraph(
    *,
    was_vfr: bool,
    was_hdr: bool,
    was_sar: bool,
    target_fps: float,
) -> Tuple[str, Tuple[str, ...]]:
    parts = []
    applied = []
    if was_sar:
        parts.extend(["scale=trunc(iw*sar/2)*2:ih", "setsar=1"])
        applied.append("scale_sar_to_square_pixels")
    else:
        parts.append("setsar=1")
        applied.append("setsar=1")
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

    if not (was_vfr or was_hdr or was_sar or rotation % 360):
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
        )

    out_dir = Path(stage_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    staged = out_dir / f"stage_00_ingest_{src.stem}.mp4"
    graph, applied = build_ingest_filtergraph(
        was_vfr=was_vfr, was_hdr=was_hdr, was_sar=was_sar, target_fps=target_fps
    )
    script = out_dir / f"stage_00_ingest_{src.stem}.txt"
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
    )
