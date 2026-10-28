"""vx.deliver — one declared deliverable, produced and proven, or refused.

F26-T07. A profile declares its ``deliverables``; this module turns one of those
targets into a real file with the encoder the target names, then hands the file
to :mod:`vx.deliver_probe` for the ffprobe measurement and to
:mod:`vx.deliver_sidecar` for the artifacts beside it, instead of trusting the
command line it just ran. The target table and the encoder probe live in
:mod:`vx.deliver_targets` and are re-exported here. Refusals are stable codes:

``DELIVERY_UNKNOWN_TARGET``      the profile does not declare the target, or no
                                 recipe exists for a name it does declare;
``DELIVERY_ENCODER_MISSING``     the encoder this target needs is absent from
                                 this ffmpeg build; the message names it;
``DELIVERY_NOT_AS_DECLARED``     the file came out different from its
                                 declaration -- a substitution is never silent;
``DELIVERY_TRANSFER_UNTAGGED``   a non-SDR profile: an HDR stream must carry its
                                 BT.2100 tags, and tagging is the renderer's job.

Import-cheap: only the ffmpeg wrapper is loaded at import time. The loudness
measurement lives in :mod:`vx.qc` and is imported on first use.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import deliver_probe, deliver_sidecar
from . import ffmpeg as ff
from .deliver_targets import (
    PROBED_ENCODERS,
    TARGETS,
    Target,
    encoder_available,
    encoders,
    missing,
    spec,
    targets,
)
from .edl3 import EDLError

__all__ = [
    "Target",
    "TARGETS",
    "PROBED_ENCODERS",
    "targets",
    "spec",
    "encoders",
    "encoder_available",
    "missing",
    "deliver",
]


def _number(value: float) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"


def _audio_args(encoder: str) -> Tuple[str, ...]:
    if encoder == "aac":
        return ("-b:a", "192k", "-ar", "48000", "-ac", "2")
    return ("-ar", "48000", "-ac", "2")


def _encode_args(
    master: Path,
    destination: Path,
    row: Any,
    recipe: Target,
    duration: float,
    audio: bool,
) -> List[str]:
    """The one ffmpeg invocation: fit the master to the profile's canvas and rate.

    The canvas is the profile's, reached by scaling down and padding, never by
    cropping the picture; ``-t`` bounds the tail so a rate conversion cannot add
    frames past the master's own duration (the same rule ``edl3_render`` uses).
    """
    width, height = int(row.width), int(row.height)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,format={recipe.pix_fmt}"
    )
    args = [
        "-i", str(master), "-map", "0:v:0", "-vf", vf,
        "-r", _number(row.fps), "-t", _number(duration),
        "-sn", "-map_chapters", "-1",
        "-c:v", recipe.encoder, *recipe.video_args,
    ]
    if audio:
        args += ["-map", "0:a:0", "-c:a", recipe.audio_encoder, *_audio_args(recipe.audio_encoder)]
    else:
        args.append("-an")
    if recipe.ext == ".mp4":
        args += ["-movflags", "+faststart"]
    return args + [str(destination)]


def deliver(
    master: Path,
    profile: Any,
    target: str,
    out: Path,
    workdir: Path,
    *,
    document: Any = None,
    captions: Any = None,
) -> Dict[str, Any]:
    """Produce ``target`` from ``master`` into ``out`` and report what landed.

    ``out`` is the output directory: the file is named after the target
    (``h264/mp4`` -> ``h264.mp4``), so two targets of one profile never collide.
    A document with captions also writes a sidecar ``.srt`` next to the file --
    soft subtitles are never muxed, and the report states the streams the file
    actually carries. A document that declares scene boundaries writes an
    FFmetadata chapter file; nothing is invented when it declares none.
    """
    source_path = Path(master)
    if not source_path.is_file() or source_path.stat().st_size == 0:
        raise FileNotFoundError(f"master not found or empty: {source_path}")

    row = profile
    if isinstance(row, str):
        from . import profiles  # noqa: PLC0415 - a name is resolved, never assumed

        row = profiles.resolve(row)
    name = str(getattr(row, "id", row))
    declared = targets(row)
    key = str(target).strip()
    if key not in declared:
        raise EDLError(
            "DELIVERY_UNKNOWN_TARGET",
            f"profile {name!r} does not declare {key!r}; it declares {list(declared)}",
            "deliverables",
        )
    if str(getattr(row, "transfer", "sdr")) != "sdr":
        raise EDLError(
            "DELIVERY_TRANSFER_UNTAGGED",
            f"profile {name!r} declares a {getattr(row, 'transfer', '')} transfer; an "
            "HDR delivery must carry its BT.2100 tags, which the renderer writes, "
            "not this producer",
            "deliverables",
        )
    recipe = spec(key)
    for encoder in (recipe.encoder, recipe.audio_encoder):
        if not encoder_available(encoder):
            raise EDLError(
                "DELIVERY_ENCODER_MISSING",
                f"target {key!r} needs encoder {encoder!r}, absent from this ffmpeg "
                f"build ({ff.version()})",
                "deliverables",
            )

    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    Path(workdir).mkdir(parents=True, exist_ok=True)
    stem = key.replace("/", ".").rsplit(".", 1)[0]
    destination = out_dir / f"{stem}{recipe.ext}"

    source = deliver_probe.probe(source_path)
    duration = float(source["duration"])
    if duration <= 0:
        raise EDLError("DELIVERY_MASTER_UNREADABLE", "master reports no duration", str(source_path))

    proc = ff.run_ff(
        _encode_args(source_path, destination, row, recipe, duration, source["audio_streams"] > 0),
        step="render",
    )
    if not proc.ok:
        destination.unlink(missing_ok=True)
        raise EDLError("DELIVERY_RENDER_FAILED", proc.tail(15), str(destination))
    if not destination.is_file() or destination.stat().st_size == 0:
        raise EDLError("DELIVERY_RENDER_FAILED", "ffmpeg reported success but wrote nothing", str(destination))

    measured = deliver_probe.probe(destination)
    deliver_probe.verify(recipe, row, measured, duration, destination)

    report: Dict[str, Any] = {
        "profile": name,
        "target": key,
        "path": str(destination.resolve()),
        "encoder": recipe.encoder,
        "bytes": destination.stat().st_size,
        "container": measured["container"],
        "container_family": measured["family"],
        "video_codec": measured["video_codec"],
        "pix_fmt": measured["pix_fmt"],
        "bit_depth": measured["bit_depth"],
        "width": measured["width"],
        "height": measured["height"],
        "fps": measured["fps"],
        "frames": measured["frames"],
        "duration": measured["duration"],
        "audio_codec": measured["audio_codec"] or None,
        "audio_channels": measured["audio_channels"],
        "loudness_lufs": None,
        "true_peak_db": None,
        "subtitles": measured["subtitle_streams"] > 0,
        "subtitle_streams": measured["subtitle_streams"],
        "chapters": measured["chapters"],
        "sidecar": None,
        "sidecar_cues": 0,
        "chapter_metadata": None,
        "chapter_count": 0,
    }
    if measured["audio_streams"]:
        from . import qc  # noqa: PLC0415 - the measurement pass, only when needed

        lufs = qc.lufs_of(destination)
        report["loudness_lufs"] = lufs.get("input_i")
        report["true_peak_db"] = lufs.get("input_tp")

    rows = deliver_sidecar.cues(captions) if captions is not None else deliver_sidecar.document_cues(document)
    if rows:
        sidecar = out_dir / f"{stem}.srt"
        sidecar.write_text(deliver_sidecar.srt(rows), encoding="utf-8", newline="\n")
        report["sidecar"] = str(sidecar.resolve())
        report["sidecar_cues"] = len(rows)

    marks = deliver_sidecar.chapter_marks(document, duration)
    if marks:
        meta = out_dir / f"{stem}.chapters.txt"
        meta.write_text(deliver_sidecar.ffmetadata(marks), encoding="utf-8", newline="\n")
        report["chapter_metadata"] = str(meta.resolve())
        report["chapter_count"] = len(marks)
    return report
