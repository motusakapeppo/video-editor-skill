"""vx.probe — ffprobe -> ProbeResult. Read-only, never touches the source."""

from __future__ import annotations

import json
from pathlib import Path

from . import ffmpeg as ff
from .contract import MissingDependency, ProbeResult


def _fraction(value: str) -> float:
    if not value or value in ("0/0", "N/A"):
        return 0.0
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def probe(path: str | Path, *, count_frames: bool = False) -> ProbeResult:
    p = Path(path)
    if not p.exists():
        raise MissingDependency(f"input not found: {p}")
    stat = p.stat()
    args = [
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(p),
    ]
    if count_frames:
        args.insert(2, "-count_frames")
    proc = ff.run_fp(args)
    if not proc.ok:
        raise MissingDependency(f"ffprobe failed on {p.name}: {proc.tail(5)}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MissingDependency(f"no video stream in {p.name}")

    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(float(side["rotation"]))
    if not rotation:
        rotation = int(float((video.get("tags") or {}).get("rotate", 0) or 0))

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if abs(rotation) in (90, 270):
        width, height = height, width

    fps = _fraction(video.get("avg_frame_rate", "")) or _fraction(
        video.get("r_frame_rate", "")
    )

    nb_frames = None
    if video.get("nb_frames") not in (None, "N/A"):
        try:
            nb_frames = int(video["nb_frames"])
        except ValueError:
            nb_frames = None
    if (
        nb_frames is None
        and count_frames
        and video.get("nb_read_frames") not in (None, "N/A")
    ):
        try:
            nb_frames = int(video["nb_read_frames"])
        except ValueError:
            nb_frames = None

    color = {
        k: video.get(k)
        for k in (
            "color_range",
            "color_space",
            "color_transfer",
            "color_primaries",
            "pix_fmt",
            "profile",
            "level",
            "field_order",
        )
        if video.get(k)
    }

    duration = 0.0
    try:
        duration = float(video.get("duration") or fmt.get("duration") or 0.0)
    except ValueError:
        duration = 0.0
    if not duration and fps and nb_frames:
        duration = nb_frames / fps

    return ProbeResult(
        path=str(p.resolve()),
        duration=round(duration, 6),
        fps=round(fps, 6),
        width=width,
        height=height,
        nb_frames=nb_frames,
        video_codec=video.get("codec_name", ""),
        audio_codec=(audio or {}).get("codec_name", ""),
        sample_rate=int((audio or {}).get("sample_rate") or 0),
        channels=int((audio or {}).get("channels") or 0),
        pix_fmt=video.get("pix_fmt", ""),
        rotation=rotation,
        has_audio=audio is not None,
        bitrate=int(float(fmt.get("bit_rate") or 0)),
        container=fmt.get("format_name", ""),
        video_index=int(video.get("index", 0)),
        audio_index=int(audio["index"]) if audio else None,
        color=color,
        size=int(stat.st_size),
        mtime=round(stat.st_mtime, 3),
    )
