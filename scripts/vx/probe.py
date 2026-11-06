"""vx.probe — ffprobe -> ProbeResult. Read-only, never touches the source."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from . import ffmpeg as ff
from .contract import MissingDependency, ProbeResult

STILL_CONTAINERS = ("image2", "png_pipe", "jpeg_pipe", "webp_pipe")
STILL_CODECS = frozenset({"png", "mjpeg", "webp", "gif", "bmp", "tiff"})
STILL_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".heic",
        ".avif",
        ".jxl",
        ".ppm",
        ".pgm",
        ".pnm",
        ".tga",
        ".dpx",
        ".exr",
    }
)


def container_duration(path: Any) -> Optional[float]:
    """La durata del **contenitore** (``format=duration``), o ``None``.

    E' l'unica definizione di "quanto dura il file consegnato": ``ProbeResult``
    porta ``stream.duration``, che e' la traccia video e su un master da 10 s
    puo' dire 9.937 mentre il contenitore dice 9.984. Il contratto e il QC
    devono leggere lo stesso numero, o lo stesso master e' buono per uno e fuori
    tolleranza per l'altro (e' successo: ogni reel finiva NON-PUBBLICABILE).
    """
    try:
        done = ff.run_fp(
            ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
        )
    except Exception:  # noqa: BLE001 - una misura mancante non e' un'eccezione
        return None
    if not done.ok:
        return None
    try:
        return float(str(done.stdout).strip().splitlines()[0])
    except (IndexError, TypeError, ValueError):
        return None


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


def _ffprobe_json(p: Path, *, count_frames: bool = False) -> dict:
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
    return json.loads(proc.stdout or "{}")


def _rotation(video: dict) -> int:
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(float(side["rotation"]))
    if not rotation:
        rotation = int(float((video.get("tags") or {}).get("rotate", 0) or 0))
    return rotation


def _fps(video: dict) -> float:
    return _fraction(video.get("avg_frame_rate", "")) or _fraction(
        video.get("r_frame_rate", "")
    )


def _nb_frames(video: dict, count_frames: bool) -> Optional[int]:
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
    return nb_frames


def _color(video: dict) -> dict:
    return {
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


def _duration(stream: dict, fmt: dict, fps: float, nb_frames: Optional[int]) -> float:
    try:
        duration = float(stream.get("duration") or fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if not duration and fps and nb_frames:
        duration = nb_frames / fps
    return duration


def _is_attached_pic(stream: dict) -> bool:
    return int((stream.get("disposition") or {}).get("attached_pic") or 0) == 1


def _is_still_image(stream: dict, fmt: dict, p: Path) -> bool:
    # A single frame is the precondition: an animated gif reports nb_frames>1
    # and must stay a video even though "gif" is a still-image codec.
    single_frame = stream.get("nb_frames") in (None, "N/A", "1")
    container = fmt.get("format_name", "") or ""
    by_container = any(tok in container for tok in STILL_CONTAINERS) or (
        stream.get("codec_name", "") in STILL_CODECS
    )
    return single_frame and (by_container or p.suffix.lower() in STILL_SUFFIXES)


def probe(path: str | Path, *, count_frames: bool = False) -> ProbeResult:
    p = Path(path)
    if not p.exists():
        raise MissingDependency(f"input not found: {p}")
    stat = p.stat()
    data = _ffprobe_json(p, count_frames=count_frames)
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MissingDependency(f"no video stream in {p.name}")

    rotation = _rotation(video)

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if abs(rotation) in (90, 270):
        width, height = height, width

    fps = _fps(video)
    nb_frames = _nb_frames(video, count_frames)
    color = _color(video)
    duration = _duration(video, fmt, fps, nb_frames)

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
        kind="video",
    )


def probe_asset(path: str | Path) -> ProbeResult:
    """Classify any media file as video, image or audio and describe it."""
    p = Path(path)
    if not p.exists():
        raise MissingDependency(f"input not found: {p}")
    stat = p.stat()
    data = _ffprobe_json(p)
    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    videos = [s for s in streams if s.get("codec_type") == "video"]
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    # Cover art is artwork, not a video track: an mp3 with a thumbnail is audio.
    real_video = [s for s in videos if not _is_attached_pic(s)]
    if not real_video and audio is None:
        raise MissingDependency(f"no usable video or audio stream in {p.name}")

    video = real_video[0] if real_video else None
    if video is None:
        kind = "audio"
    elif _is_still_image(video, fmt, p):
        kind = "image"
    else:
        kind = "video"

    rotation = _rotation(video) if video else 0
    width = int(video.get("width") or 0) if video else 0
    height = int(video.get("height") or 0) if video else 0
    if video and abs(rotation) in (90, 270):
        width, height = height, width

    fps = _fps(video) if video else 0.0
    nb_frames = _nb_frames(video, False) if video else None
    if kind == "image":
        # A still has no timeline of its own: only the container may carry one.
        try:
            duration = float(fmt.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
    else:
        duration = _duration(video or audio or {}, fmt, fps, nb_frames)

    return ProbeResult(
        path=str(p.resolve()),
        duration=round(duration, 6),
        fps=round(fps, 6),
        width=width,
        height=height,
        nb_frames=nb_frames,
        video_codec=video.get("codec_name", "") if video else "",
        audio_codec=(audio or {}).get("codec_name", ""),
        sample_rate=int((audio or {}).get("sample_rate") or 0),
        channels=int((audio or {}).get("channels") or 0),
        pix_fmt=video.get("pix_fmt", "") if video else "",
        rotation=rotation,
        has_audio=audio is not None,
        bitrate=int(float(fmt.get("bit_rate") or 0)),
        container=fmt.get("format_name", ""),
        video_index=int(video.get("index", 0)) if video else -1,
        audio_index=int(audio["index"]) if audio else None,
        color=_color(video) if video else {},
        size=int(stat.st_size),
        mtime=round(stat.st_mtime, 3),
        kind=kind,
    )


def raw_video_stream(path: str | Path) -> dict:
    """The first video stream exactly as ffprobe reports it (rates, SAR, side data)."""
    p = Path(path)
    if not p.exists():
        raise MissingDependency(f"input not found: {p}")
    proc = ff.run_fp(
        [
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_streams",
            "-of",
            "json",
            str(p),
        ]
    )
    if not proc.ok:
        raise MissingDependency(f"ffprobe failed on {p.name}: {proc.tail(5)}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise MissingDependency(f"no video stream in {p.name}")
    return streams[0]
