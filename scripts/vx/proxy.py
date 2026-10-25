"""vx.proxy — the low-res proxy pipeline (Fase 1).

Analysis and preview run on a 640px proxy; the full-resolution source is only
touched by the final render. The source is opened read-only and its fingerprint
is compared before/after every encode: ``source_unchanged`` is measured, not
assumed.

Everything here goes through :mod:`vx.ffmpeg` as an argument list — no shell,
no string-joined commands. ``PIL`` is imported lazily and only as a fallback
for the contact sheet.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import ffmpeg as ff
from .contract import EXIT_RENDER_FAIL, EXIT_USAGE, PROXY_WIDTH, VxError, even
from .probe import probe
from .winpath import clear_lock, make_workdir, same_fingerprint, source_fingerprint

__all__ = ["make_proxy", "thumbs", "contact_sheet", "extract_frame", "main"]

HOOK_T = 1.0  # the hook window: a frame at 1.0s is what the viewer sees first
TAIL_GUARD = 0.2  # never sample the last 0.2s: decoders return black there
SHEET_PAD = 4
FASTSTART_SUFFIXES = (".mp4", ".m4v", ".mov")

# Proxy tuning on top of `-preset veryfast`: dropping B-frames and the
# rate-control lookahead keeps CRF 28 quality but removes the two stages that
# dominate wall time at 640px (measured 0.61s -> 0.47s on a 10s 720p clip).
X264_PROXY_PARAMS = "rc-lookahead=0:bframes=0"
SCALE_FLAGS = "bicubic"  # 3x downscale: bicubic avoids the aliasing bilinear adds
# x264's auto thread count overshoots at 640px and pays the sync cost: 8 threads
# measured 22x realtime vs 18x on auto (16 logical cores, 10s 720p clip).
PROXY_THREADS = min(8, max(2, (os.cpu_count() or 4) // 2))


# --------------------------------------------------------------------------- #
# proxy encode
# --------------------------------------------------------------------------- #


def _proxy_encoder(crf: int, preset: str) -> Dict[str, Any]:
    """The proxy is scratch work: hardware wins, size does not matter."""
    try:
        from . import accel

        return accel.pick("scratch", quality=crf, preset=preset)
    except Exception:  # noqa: BLE001 - a probe failure must never block the proxy
        return {
            "name": "libx264",
            "software": True,
            "args": ["-c:v", "libx264", "-crf", str(crf), "-preset", preset],
            "reason": "probe unavailable",
        }


def make_proxy(
    src,
    dst=None,
    *,
    width: int = PROXY_WIDTH,
    workdir=None,
    crf: int = 28,
    preset: str = "veryfast",
) -> dict:
    """Encode a low-res proxy of ``src``; never upscale, never touch the source.

    Aspect ratio and fps are preserved exactly (no ``-r``, no ``fps`` filter).
    Returns the measured facts, including ``source_unchanged``.
    """
    src_p = Path(src)
    if not src_p.is_file():
        raise VxError(f"source not found: {src_p}")

    info = probe(src_p)
    if not info.width or not info.height:
        raise VxError(f"source has no usable video stream: {src_p.name}")

    out_w = even(min(int(width), info.width))
    out_h = even(info.height * out_w / info.width)
    if out_w < 2 or out_h < 2:
        raise VxError(f"proxy size collapsed to {out_w}x{out_h}")

    own_workdir = workdir is None and dst is None
    if workdir is not None:
        wd: Optional[Path] = Path(workdir)
    elif own_workdir:
        wd = make_workdir(src_p)
    else:
        wd = None
    try:
        target = Path(dst) if dst is not None else wd / "proxy" / f"proxy_{out_w}.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)

        before = source_fingerprint(src_p)

        choice = _proxy_encoder(int(crf), str(preset))
        args: List[Any] = [
            "-i",
            str(src_p),
            "-vf",
            f"scale={out_w}:{out_h}:flags={SCALE_FLAGS}",
            *choice["args"],
            "-pix_fmt",
            "yuv420p",
        ]
        if choice["software"]:
            args[1:1] = ["-threads", str(PROXY_THREADS)]
            args += ["-x264-params", X264_PROXY_PARAMS]
        if info.has_audio:
            args += ["-c:a", "aac", "-b:a", "96k", "-ac", "2"]
        else:
            args += ["-an"]
        if target.suffix.lower() in FASTSTART_SUFFIXES:
            args += ["-movflags", "+faststart"]
        args += [str(target)]

        started = time.perf_counter()
        proc = ff.run_ff(args, loglevel="error")
        encode_seconds = time.perf_counter() - started
        if not proc.ok:
            raise VxError(f"proxy encode failed: {proc.tail(8)}")

        after = source_fingerprint(src_p)
        out = probe(target)

        frames = out.nb_frames
        if frames is None:
            frames = int(round(out.duration * out.fps)) if out.fps else 0

        return {
            "path": str(target),
            "width": out.width,
            "height": out.height,
            "fps": out.fps,
            "duration": out.duration,
            "frames": int(frames),
            "encode_seconds": round(encode_seconds, 3),
            "realtime_factor": round(
                info.duration / encode_seconds if encode_seconds > 0 else 0.0, 2
            ),
            "source_unchanged": same_fingerprint(before, after),
        }
    finally:
        if own_workdir:
            clear_lock(wd)


# --------------------------------------------------------------------------- #
# frames
# --------------------------------------------------------------------------- #


def extract_frame(src, t: float, dst, *, width: int | None = None) -> str:
    """Write one frame at ``t`` seconds as PNG/JPG.

    ``-ss`` sits before ``-i`` (fast seek) and the frame is re-encoded, so the
    timestamp is accurate to the frame rather than to the previous keyframe.
    """
    src_p = Path(src)
    if not src_p.is_file():
        raise VxError(f"source not found: {src_p}")
    dst_p = Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)

    args: List[Any] = [
        "-ss",
        f"{max(0.0, float(t)):.3f}",
        "-i",
        str(src_p),
        "-frames:v",
        "1",
    ]
    if width:
        args += ["-vf", f"scale={even(int(width))}:-2:flags=lanczos"]
    if dst_p.suffix.lower() in (".jpg", ".jpeg"):
        args += ["-q:v", "2"]
    args += [str(dst_p)]

    proc = ff.run_ff(args)
    if not proc.ok:
        raise VxError(f"frame extract failed at {t:.3f}s: {proc.tail(6)}")
    return str(dst_p)


def _positions(duration: float, count: int) -> List[float]:
    """Evenly spaced samples over [0, duration - TAIL_GUARD], hook frame included.

    The hook frame at 1.0s takes one of the ``count`` slots (not an extra one),
    so ``len(thumbs(...)) == count`` always holds.
    """
    n = max(1, int(count))
    limit = max(0.0, duration - TAIL_GUARD)
    if duration > 2.0 and n >= 2:
        even_n = n - 1
        spots = [limit * (i + 0.5) / even_n for i in range(even_n)]
        spots.append(min(HOOK_T, limit))
    else:
        spots = [limit * (i + 0.5) / n for i in range(n)]
    return sorted(spots)


def thumbs(
    src,
    dst_dir,
    *,
    count: int = 8,
    at: list[float] | None = None,
    width: int = 480,
) -> list[str]:
    """Write ``count`` preview frames into ``dst_dir`` and return their paths.

    Positions are scene-cut agnostic: evenly spaced, never in the last 0.2s,
    plus a hook-window frame at 1.0s when the clip is longer than 2s. When
    ``at`` is given those exact timestamps are used instead.
    """
    info = probe(src)
    if info.duration <= 0:
        raise VxError(f"cannot sample a zero-length clip: {Path(src).name}")

    out_dir = Path(dst_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if at:
        ceiling = max(0.0, info.duration - 0.05)
        spots = [min(max(0.0, float(t)), ceiling) for t in at]
    else:
        spots = _positions(info.duration, count)

    paths: List[str] = []
    for index, t in enumerate(spots):
        target = out_dir / f"thumb_{index:02d}_{t:06.3f}.jpg"
        paths.append(extract_frame(src, t, target, width=width))
    return paths


# --------------------------------------------------------------------------- #
# contact sheet
# --------------------------------------------------------------------------- #


def _sheet_ffmpeg(
    items: List[Path], dst: Path, cols: int, rows: int, tw: int, th: int
) -> str:
    """One ffmpeg pass: normalise every still, concat them into a single stream,
    then let ``tile`` lay that stream out. ``tile`` accepts one input, not N."""
    args: List[Any] = []
    for item in items:
        args += ["-i", str(item)]

    chains = [
        f"[{i}:v]scale={tw}:{th}:force_original_aspect_ratio=decrease,"
        f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[v{i}]"
        for i in range(len(items))
    ]
    labels = "".join(f"[v{i}]" for i in range(len(items)))
    graph = (
        ";".join(chains)
        + f";{labels}concat=n={len(items)}:v=1:a=0[cat]"
        + f";[cat]tile={cols}x{rows}:padding={SHEET_PAD}:margin={SHEET_PAD}[out]"
    )
    args += [
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(dst),
    ]
    proc = ff.run_ff(args)
    if not proc.ok:
        raise VxError(f"tile sheet failed: {proc.tail(6)}")
    return str(dst)


def _sheet_pil(
    items: List[Path], dst: Path, cols: int, rows: int, tw: int, th: int
) -> str:
    try:
        from PIL import Image  # lazy: only needed when ffmpeg tile is unavailable
    except ImportError as exc:  # pragma: no cover - PIL is in the venv
        raise VxError("PIL not available for the contact sheet fallback") from exc

    sheet = Image.new(
        "RGB",
        (cols * tw + SHEET_PAD * (cols + 1), rows * th + SHEET_PAD * (rows + 1)),
        (16, 16, 16),
    )
    for index, item in enumerate(items):
        with Image.open(item) as raw:
            frame = raw.convert("RGB")
            frame.thumbnail((tw, th))
            x = SHEET_PAD + (index % cols) * (tw + SHEET_PAD) + (tw - frame.width) // 2
            y = (
                SHEET_PAD
                + (index // cols) * (th + SHEET_PAD)
                + (th - frame.height) // 2
            )
            sheet.paste(frame, (x, y))
    sheet.save(dst, quality=90)
    return str(dst)


def contact_sheet(
    paths: list[str], dst, *, cols: int = 4, thumb_width: int = 320
) -> str:
    """Tile ``paths`` into one image. ffmpeg ``tile`` first, PIL as fallback."""
    items = [Path(p) for p in paths]
    if not items:
        raise VxError("contact sheet needs at least one frame")
    missing = [str(p) for p in items if not p.is_file()]
    if missing:
        raise VxError(f"contact sheet input missing: {missing[0]}")

    dst_p = Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)

    cols = max(1, int(cols))
    rows = max(1, math.ceil(len(items) / cols))
    tw = even(int(thumb_width))
    th = even(tw * 9 / 16)

    try:
        return _sheet_ffmpeg(items, dst_p, cols, rows, tw, th)
    except VxError:
        return _sheet_pil(items, dst_p, cols, rows, tw, th)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vx.proxy",
        description="Low-res proxy, preview frames and contact sheet (Fase 1).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_make = sub.add_parser("make", help="encode the 640px proxy")
    p_make.add_argument("--input", required=True)
    p_make.add_argument("--out", default=None)
    p_make.add_argument("--width", type=int, default=PROXY_WIDTH)
    p_make.add_argument("--crf", type=int, default=28)
    p_make.add_argument("--preset", default="veryfast")
    p_make.add_argument("--json", action="store_true")

    p_thumbs = sub.add_parser("thumbs", help="write preview frames")
    p_thumbs.add_argument("--input", required=True)
    p_thumbs.add_argument("--out-dir", required=True)
    p_thumbs.add_argument("--count", type=int, default=8)
    p_thumbs.add_argument("--width", type=int, default=480)
    p_thumbs.add_argument("--json", action="store_true")

    p_sheet = sub.add_parser("sheet", help="tile frames into a contact sheet")
    p_sheet.add_argument("--inputs", nargs="+", required=True)
    p_sheet.add_argument("--out", required=True)
    p_sheet.add_argument("--cols", type=int, default=4)
    p_sheet.add_argument("--thumb-width", type=int, default=320)

    p_frame = sub.add_parser("frame", help="extract one frame")
    p_frame.add_argument("--input", required=True)
    p_frame.add_argument("--t", type=float, required=True)
    p_frame.add_argument("--out", required=True)
    p_frame.add_argument("--width", type=int, default=None)

    ns = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if ns.cmd == "make":
            data = make_proxy(
                ns.input,
                ns.out,
                width=ns.width,
                crf=ns.crf,
                preset=ns.preset,
            )
            if ns.json:
                print(json.dumps(data, indent=2, ensure_ascii=False))
            else:
                print(f"proxy        {data['path']}")
                print(f"size         {data['width']}x{data['height']}")
                print(f"fps          {data['fps']}")
                print(f"duration     {data['duration']}s ({data['frames']} frames)")
                print(f"encode       {data['encode_seconds']}s")
                print(f"realtime     {data['realtime_factor']}x")
                print(f"source ok    {data['source_unchanged']}")
            return 0

        if ns.cmd == "thumbs":
            paths = thumbs(ns.input, ns.out_dir, count=ns.count, width=ns.width)
            if ns.json:
                print(json.dumps(paths, indent=2, ensure_ascii=False))
            else:
                for path in paths:
                    print(path)
            return 0

        if ns.cmd == "sheet":
            path = contact_sheet(
                ns.inputs, ns.out, cols=ns.cols, thumb_width=ns.thumb_width
            )
            print(path)
            return 0

        if ns.cmd == "frame":
            print(extract_frame(ns.input, ns.t, ns.out, width=ns.width))
            return 0
    except VxError as exc:
        print(f"vx.proxy: {exc}", file=sys.stderr)
        return EXIT_RENDER_FAIL

    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
