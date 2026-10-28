"""vx.deliver_probe — what the delivered file actually is, and whether that is
what its target promised.

Split out of :mod:`vx.deliver` because measuring is a different job from
encoding: this module only runs ffprobe and compares values, so a reader can
trust the numbers without the encoder table in view. ``recipe`` and ``row`` are
duck-typed on purpose -- importing :class:`vx.deliver.Target` here would be a
cycle, and this module needs four attributes, not the type.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Mapping

from . import ffmpeg as ff
from .edl3 import EDLError

__all__ = ["family", "probe", "verify"]

_DEPTH = re.compile(r"(\d{2})(?:le|be)$")


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def family(format_name: str, brand: str) -> str:
    """The container family a target names, from what ffprobe reports.

    mov and mp4 share one probe name (``mov,mp4,m4a,3gp,3g2,mj2``), so the
    major brand is what actually separates a QuickTime deliverable from an mp4.
    """
    if "mxf" in format_name:
        return "mxf"
    if (brand or "").strip() == "qt":
        return "mov"
    if "mp4" in format_name:
        return "mp4"
    return format_name


def probe(path: Path) -> Dict[str, Any]:
    """ffprobe the file: streams, chapters, exact decoded frame count."""
    proc = ff.run_fp(
        ["-count_frames", "-show_streams", "-show_format", "-show_chapters", "-of", "json", str(path)]
    )
    if not proc.ok:
        raise EDLError("DELIVERY_PROBE_FAILED", proc.tail(5), str(path))
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise EDLError("DELIVERY_PROBE_FAILED", "no video stream", str(path))
    fmt = data.get("format") or {}
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {}) or {}
    num, _, den = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1").partition("/")
    try:
        fps = float(num) / float(den) if float(den or 0) else 0.0
    except ValueError:
        fps = 0.0
    pix_fmt = str(video.get("pix_fmt") or "")
    depth = _DEPTH.search(pix_fmt)
    return {
        "container": str(fmt.get("format_name") or ""),
        "family": family(
            str(fmt.get("format_name") or ""),
            str((fmt.get("tags") or {}).get("major_brand") or ""),
        ),
        "video_codec": str(video.get("codec_name") or ""),
        "pix_fmt": pix_fmt,
        "bit_depth": int(depth.group(1)) if depth else 8,
        "width": _int(video.get("width")),
        "height": _int(video.get("height")),
        "fps": round(fps, 6),
        "frames": _int(video.get("nb_read_frames")) or _int(video.get("nb_frames")),
        "duration": round(_float(fmt.get("duration") or video.get("duration")), 6),
        "audio_codec": str(audio.get("codec_name") or ""),
        "audio_channels": _int(audio.get("channels")),
        "audio_streams": sum(1 for s in streams if s.get("codec_type") == "audio"),
        "subtitle_streams": sum(1 for s in streams if s.get("codec_type") == "subtitle"),
        "chapters": len(data.get("chapters") or []),
    }


def verify(recipe: Any, row: Any, measured: Mapping[str, Any], duration: float, path: Path) -> None:
    """Refuse a file that does not carry what its target name promises."""
    problems = []
    if measured["video_codec"] != recipe.codec:
        problems.append(f"codec {measured['video_codec']} != {recipe.codec}")
    if measured["pix_fmt"] != recipe.pix_fmt:
        problems.append(f"pix_fmt {measured['pix_fmt']} != {recipe.pix_fmt}")
    if measured["family"] != recipe.family:
        problems.append(f"container {measured['family']} != {recipe.family}")
    if (measured["width"], measured["height"]) != (int(row.width), int(row.height)):
        problems.append(f"canvas {measured['width']}x{measured['height']} != {row.width}x{row.height}")
    expected = int(round(duration * float(row.fps)))
    if abs(measured["frames"] - expected) > 1:
        problems.append(f"{measured['frames']} frames != {expected} at {row.fps:g} fps")
    if abs(measured["duration"] - duration) > max(0.08, 2.0 / float(row.fps)):
        problems.append(f"duration {measured['duration']}s != {duration:g}s")
    if problems:
        raise EDLError("DELIVERY_NOT_AS_DECLARED", f"{recipe.name}: " + "; ".join(problems), str(path))
