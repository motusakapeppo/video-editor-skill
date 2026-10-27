"""vx.golden — real frame-level golden tests.

``gates.color`` compares luma distributions against a caller-supplied video and
``prove.renderer_delta`` compares two renderers against each other; neither
answers "did this exact frame change?". This module does: one PNG per requested
timestamp, extracted by ffmpeg itself, hashed, and compared pixel by pixel.

    manifest = golden.capture(video, [0.5, 1.5], workdir / "frames")
    golden.assert_golden(manifest, golden.load(old_manifest_path))

Determinism contract: the same input file and the same timestamps produce
byte-identical PNGs, hence identical frame hashes, across runs and machines
running the same ffmpeg build. Timestamps are stored rounded to six decimals so
the manifest survives a JSON round-trip unchanged; a time that drifts beyond
that is a real difference, not a rounding artefact.

Comparison: hashes equal -> zero difference, no decode. Hashes differ -> both
PNGs are decoded and the mean absolute difference is taken per channel. Pillow
does the decoding when it is importable (installed here); otherwise ffmpeg's
``blend=all_mode=difference`` + ``signalstats`` reports the luma MAD. There is
no third path and no silent skip: a timestamp present in only one manifest, or
a frame file that has gone missing, raises ``GoldenError``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import ffmpeg

MANIFEST_VERSION = 1
PNG_SUFFIX = ".png"

__all__ = [
    "GoldenError",
    "MANIFEST_VERSION",
    "capture",
    "compare",
    "assert_golden",
    "load",
    "_pixel_diff",
]


class GoldenError(Exception):
    """A golden capture or comparison could not be trusted."""


# ---------------------------------------------------------------- utilities


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _norm_times(times: Sequence[float]) -> List[float]:
    values: List[float] = []
    for raw in times:
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise GoldenError(f"timestamp non numerico: {raw!r}") from exc
        if value != value or value < 0:  # NaN or negative
            raise GoldenError(f"timestamp non valido: {raw!r}")
        values.append(round(value, 6))
    if not values:
        raise GoldenError("capture: nessun timestamp richiesto")
    return values


def _time_key(value: float) -> str:
    return f"{float(value):.6f}"


def _frame_name(index: int, value: float) -> str:
    return f"frame_{index:04d}_{float(value):.3f}{PNG_SUFFIX}"


def _png_size(path: Path) -> List[int]:
    """Frame size without assuming Pillow: Pillow first, ffprobe second."""
    try:
        from PIL import Image  # noqa: WPS433 (optional dependency)

        with Image.open(path) as image:
            width, height = image.size
        return [int(width), int(height)]
    except Exception:  # noqa: BLE001 - fall through to ffprobe
        pass
    proc = ffmpeg.run_fp(
        [
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ]
    )
    if proc.ok:
        try:
            stream = json.loads(proc.stdout or "{}")["streams"][0]
            return [int(stream["width"]), int(stream["height"])]
        except (KeyError, IndexError, ValueError, TypeError):
            pass
    raise GoldenError(f"dimensioni del frame illeggibili: {path}")


# ----------------------------------------------------------------- capture


def capture(video: Any, times: Sequence[float], outdir: Any) -> Dict[str, Any]:
    """Extract one PNG per timestamp with ffmpeg and return the manifest.

    ``-ss`` sits before ``-i`` (fast input seek) but ffmpeg still decodes up to
    the exact frame, so the extracted frame does not depend on keyframe
    placement. ``-frames:v 1`` stops the encode after that single frame.
    """
    source = Path(video)
    if not source.exists():
        raise GoldenError(f"video non trovato: {source}")
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    wanted = _norm_times(times)

    frames: List[Dict[str, Any]] = []
    hashes: Dict[str, str] = {}
    size: Optional[List[int]] = None
    for index, value in enumerate(wanted):
        destination = out / _frame_name(index, value)
        if destination.exists():
            destination.unlink()
        proc = ffmpeg.run_ff(
            [
                "-ss",
                f"{value:.3f}",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-an",
                "-sn",
                "-dn",
                "-c:v",
                "png",
                "-pix_fmt",
                "rgb24",
                "-f",
                "image2",
                str(destination),
            ],
            loglevel="error",
        )
        if not proc.ok or not destination.exists():
            raise GoldenError(
                f"estrazione del frame a {value:.3f}s fallita: {proc.tail()}"
            )
        if size is None:
            size = _png_size(destination)
        digest = _sha256_file(destination)
        hashes[_time_key(value)] = digest
        frames.append(
            {
                "time": value,
                "file": destination.name,
                "path": str(destination.resolve()),
                "sha256": digest,
                "size_bytes": destination.stat().st_size,
            }
        )

    manifest: Dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "video": str(source.resolve()),
        "video_sha256": _sha256_file(source),
        "times": wanted,
        "size": size or [0, 0],
        "frame_sha256": hashes,
        "frames": frames,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


# --------------------------------------------------------------- comparison


def load(manifest: Any) -> Dict[str, Any]:
    """Read a manifest from a path, or pass a mapping through untouched."""
    if isinstance(manifest, Mapping):
        return dict(manifest)
    path = Path(str(manifest))
    if not path.exists():
        raise GoldenError(f"manifest non trovato: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GoldenError(f"manifest illeggibile: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GoldenError(f"manifest non e' un oggetto JSON: {path}")
    base = path.resolve().parent
    for frame in data.get("frames") or []:
        if isinstance(frame, dict) and frame.get("path") and not frame.get("file"):
            frame["file"] = Path(str(frame["path"])).name
        if isinstance(frame, dict) and frame.get("file") and not frame.get("path"):
            frame["path"] = str(base / str(frame["file"]))
    return data


def _manifest_frames(manifest: Mapping[str, Any], label: str) -> List[Dict[str, Any]]:
    frames = manifest.get("frames")
    if not isinstance(frames, list) or not frames:
        raise GoldenError(f"manifest {label}: campo 'frames' assente o vuoto")
    cleaned: List[Dict[str, Any]] = []
    for position, frame in enumerate(frames):
        if not isinstance(frame, Mapping):
            raise GoldenError(f"manifest {label}: frame {position} non e' un oggetto")
        if "time" not in frame:
            raise GoldenError(f"manifest {label}: frame {position} senza 'time'")
        sha = frame.get("sha256")
        if not isinstance(sha, str) or not sha:
            raise GoldenError(f"manifest {label}: frame {position} senza sha256")
        cleaned.append(
            {
                "time": round(float(frame["time"]), 6),
                "sha256": sha,
                "path": frame.get("path"),
                "file": frame.get("file"),
            }
        )
    return cleaned


def _resolve_frame_path(
    frame: Mapping[str, Any], manifest: Mapping[str, Any], label: str
) -> Path:
    raw = frame.get("path")
    candidates: List[Path] = []
    if raw:
        candidates.append(Path(str(raw)))
    if frame.get("file"):
        if manifest.get("outdir"):
            candidates.append(Path(str(manifest["outdir"])) / str(frame["file"]))
        if manifest.get("video"):
            candidates.append(Path(str(manifest["video"])).parent / str(frame["file"]))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise GoldenError(
        f"manifest {label}: file del frame a {float(frame['time']):.3f}s mancante"
    )


def _pixel_diff(path_a: Path, path_b: Path) -> Dict[str, Any]:
    """Mean absolute difference between two decoded RGB frames.

    Pillow decodes both images and ``ImageChops.difference`` + ``ImageStat``
    yields the per-band mean of |a-b|, which is the MAD by definition. Without
    Pillow the same quantity is measured on luma only, by ffmpeg.
    """
    try:
        from PIL import Image, ImageChops, ImageStat

        with Image.open(path_a) as image_a, Image.open(path_b) as image_b:
            left = image_a.convert("RGB")
            right = image_b.convert("RGB")
            if left.size != right.size:
                raise GoldenError(
                    f"frame di dimensioni diverse: {left.size} vs {right.size}"
                )
            difference = ImageChops.difference(left, right)
            stat = ImageStat.Stat(difference)
            means = [float(value) for value in stat.mean]
            extrema = stat.extrema
        result: Dict[str, Any] = {
            "method": "pillow",
            "mad": round(sum(means) / len(means), 6),
            "mad_r": round(means[0], 6),
            "mad_g": round(means[1], 6),
            "mad_b": round(means[2], 6),
            "max_abs": int(max(int(pair[1]) for pair in extrema)),
        }
    except GoldenError:
        raise
    except Exception:  # noqa: BLE001 - Pillow unusable: measure with ffmpeg
        result = _ffmpeg_diff(path_a, path_b)
    return result


def _ffmpeg_diff(path_a: Path, path_b: Path) -> Dict[str, Any]:
    """Luma MAD via blend=difference + signalstats (YAVG), no PSNR/SSIM."""
    proc = ffmpeg.run_ff(
        [
            "-i",
            str(path_a),
            "-i",
            str(path_b),
            "-filter_complex",
            "[0:v]format=yuv420p[a];[1:v]format=yuv420p[b];"
            "[a][b]blend=all_mode=difference,signalstats,"
            "metadata=mode=print:file=-",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        loglevel="error",
    )
    matches = re.findall(r"signalstats\.YAVG=([0-9.]+)", proc.stdout or "")
    if not proc.ok or not matches:
        raise GoldenError(f"differenza pixel non calcolabile: {proc.tail()}")
    luma = float(matches[0])
    return {
        "method": "ffmpeg",
        "mad": round(luma, 6),
        "mad_r": None,
        "mad_g": None,
        "mad_b": None,
        "max_abs": None,
    }


def compare(
    manifest_a: Any,
    manifest_b: Any,
    *,
    pixel_tolerance: int = 0,
) -> List[Dict[str, Any]]:
    """Return one entry per frame whose pixels differ beyond ``pixel_tolerance``.

    ``pixel_tolerance`` is the largest mean absolute difference, per channel
    (0-255 scale), that still counts as identical. An empty list means every
    timestamp matched. A timestamp present in one manifest only is an error:
    silently skipping it would turn a shrunk capture into a green test.
    """
    if pixel_tolerance < 0:
        raise GoldenError("pixel_tolerance non puo' essere negativo")
    left = load(manifest_a)
    right = load(manifest_b)
    frames_a = _manifest_frames(left, "A")
    frames_b = _manifest_frames(right, "B")

    times_a = [frame["time"] for frame in frames_a]
    times_b = [frame["time"] for frame in frames_b]
    if times_a != times_b:
        missing_b = [t for t in times_a if t not in times_b]
        missing_a = [t for t in times_b if t not in times_a]
        detail = []
        if missing_b:
            detail.append("assenti in B: " + ", ".join(f"{t:.3f}s" for t in missing_b))
        if missing_a:
            detail.append("assenti in A: " + ", ".join(f"{t:.3f}s" for t in missing_a))
        if not detail:
            detail.append(f"ordine diverso: {times_a} vs {times_b}")
        raise GoldenError("timestamp non allineati (" + "; ".join(detail) + ")")

    differences: List[Dict[str, Any]] = []
    for frame_a, frame_b in zip(frames_a, frames_b):
        value = frame_a["time"]
        if frame_a["sha256"] == frame_b["sha256"]:
            continue
        path_a = _resolve_frame_path(frame_a, left, "A")
        path_b = _resolve_frame_path(frame_b, right, "B")
        measured = _pixel_diff(path_a, path_b)
        if measured["mad"] <= float(pixel_tolerance):
            continue
        differences.append(
            {
                "time": value,
                "mad": measured["mad"],
                "mad_r": measured["mad_r"],
                "mad_g": measured["mad_g"],
                "mad_b": measured["mad_b"],
                "max_abs": measured["max_abs"],
                "method": measured["method"],
                "tolerance": int(pixel_tolerance),
                "sha256_a": frame_a["sha256"],
                "sha256_b": frame_b["sha256"],
            }
        )
    return differences


def assert_golden(
    manifest_a: Any,
    manifest_b: Any,
    *,
    pixel_tolerance: int = 0,
) -> None:
    """Raise ``GoldenError`` with a per-frame, human-readable diff on failure."""
    differences = compare(manifest_a, manifest_b, pixel_tolerance=pixel_tolerance)
    if not differences:
        return
    lines = [
        f"{len(differences)} frame diversi (tolleranza {int(pixel_tolerance)} MAD):"
    ]
    for entry in differences:
        channels = ""
        if entry["method"] == "pillow":
            channels = (
                f" r={entry['mad_r']:.3f} g={entry['mad_g']:.3f}"
                f" b={entry['mad_b']:.3f} max={entry['max_abs']}"
            )
        lines.append(
            f"  {entry['time']:.3f}s: MAD={entry['mad']:.3f} "
            f"({entry['method']}){channels}"
        )
    raise GoldenError("\n".join(lines))
