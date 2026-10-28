"""vx.bench - the official measurement bench (F16-T01).

Renders a real ``vx.edl3/1`` document through the public entry point
(``vx.edl3.Timeline3.from_dict`` -> ``vx.edl3_render.render``) and writes a JSON
report plus a Markdown table. Every number is measured (perf_counter, getsize,
sha256, ``ffprobe -count_frames``), never estimated; peak RSS is psutil-sampled
or ``null`` with ``"peak_rss_source": "unavailable"``. A failure is recorded as
``"ok": false`` with its error code and 40 stderr lines; exit 0, or 2 on failure.

    python -m vx.bench --doc doc.json --out report.json [--repeats N] [--keep]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

TEMP_ROOT = Path(tempfile.gettempdir()) / "vx_bench_tmp"
AUDIO_POLICY = "mix"
TAIL_LINES = 40
RSS_INTERVAL = 0.02
FRAME_KEYS = ("frames", "frame_count", "nb_frames", "expected_frames")
_STARTED = time.perf_counter()

try:  # optional: without it the report degrades to null, it never guesses
    import psutil
except Exception:  # noqa: BLE001 - any import failure means "unavailable"
    psutil = None  # type: ignore[assignment]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tail_lines(text: str, count: int = TAIL_LINES) -> List[str]:
    return text.strip().splitlines()[-count:]


def ffmpeg_binary() -> str:
    from vx.ffmpeg import ffmpeg as vx_ffmpeg

    declared = os.environ.get("VX_FFMPEG")
    return str(Path(declared)) if declared and Path(declared).exists() else vx_ffmpeg()


def ffprobe_binary(ffmpeg: str) -> Optional[str]:
    """ffprobe next to ffmpeg, by construction - never a different build."""
    found = next((Path(ffmpeg).parent / name for name in ("ffprobe.exe", "ffprobe")
                  if (Path(ffmpeg).parent / name).exists()), None)
    return None if found is None else str(found)


def first_line(argv: List[str]) -> str:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (proc.stdout or proc.stderr or "").strip()
    return text.splitlines()[0] if text else ""


def frame_count(path: Path, ffprobe: Optional[str]) -> Tuple[Optional[int], str]:
    """Real count: ffprobe first, the render report second, never a guess."""
    if ffprobe is not None:
        proc = subprocess.run(
            [ffprobe, "-hide_banner", "-loglevel", "error", "-select_streams",
             "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,
        )
        values = (proc.stdout or "").replace(",", " ").split()
        if proc.returncode == 0 and values:
            try:
                return int(values[0]), "ffprobe"
            except ValueError:
                pass
    return None, "unavailable"


class RssMonitor:
    """Sample this process plus its children (ffmpeg included) while rendering."""

    def __init__(self, interval: float = RSS_INTERVAL) -> None:
        self.interval = interval
        self.peak: Optional[int] = None
        self._halt = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _sample(self) -> None:
        if psutil is None:
            return
        try:
            root = psutil.Process()
            tree = [root] + root.children(recursive=True)
        except Exception:  # noqa: BLE001 - a vanished tree is not a measurement
            return
        total = 0
        for process in tree:
            try:
                total += int(process.memory_info().rss)
            except Exception:  # noqa: BLE001 - exited between listing and reading
                continue
        if self.peak is None or total > self.peak:
            self.peak = total

    def start(self) -> None:
        if psutil is None:
            return
        self._halt = threading.Event()
        self._sample()

        def loop() -> None:
            while not self._halt.wait(self.interval):
                self._sample()

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> Optional[int]:
        if self._thread is not None:
            self._halt.set()
            self._thread.join(timeout=5)
            self._sample()
        return self.peak


def error_code(exc: BaseException) -> str:
    if isinstance(declared := getattr(exc, "code", None), str) and declared:
        return declared
    if isinstance(exc, json.JSONDecodeError):
        return "DOC_INVALID_JSON"
    if isinstance(exc, (FileNotFoundError, IsADirectoryError, PermissionError)):
        return "DOC_UNREADABLE"
    return type(exc).__name__.upper()


def render_once(document: Mapping[str, Any], out: Path, stage: Path) -> Dict[str, Any]:
    """The real path: parse the document, then render with the public API."""
    from vx import edl3, edl3_render

    return edl3_render.render(
        edl3.Timeline3.from_dict(document), out, workdir=stage, audio=AUDIO_POLICY
    )


def markdown(report: Mapping[str, Any]) -> str:
    env = report["environment"]
    rows = [
        f"| {row['index']} | "
        f"{'-' if row['seconds'] is None else format(float(row['seconds']), '.4f')} | "
        f"{'-' if row['bytes'] is None else row['bytes']} | "
        f"{row['sha256'] or '-'} | "
        f"{'-' if row['frames'] is None else row['frames']} |"
        for row in report["repeats_data"]
    ] or ["| - | - | - | - | - |"]
    lines = [
        f"# vx.bench - {Path(str(report['doc'])).name}",
        "",
        f"- ok: `{str(report['ok']).lower()}`",
        f"- measured: `{report['timestamp_utc']}`",
        f"- wall: `{report['wall_seconds']:.3f} s` over `{report['repeats']}` runs",
        f"- ffmpeg: `{env['ffmpeg_version']}`",
        f"- python: `{env['python_version']}`",
        f"- peak rss: `{report['peak_rss_source']}`",
        "",
        "| repeat | seconds | bytes | sha256 | frames |",
        "|---:|---:|---:|---|---:|",
        *rows,
    ]
    if report["error"] is not None:
        error = report["error"]
        lines += ["", f"error `{error['code']}`: {error['message']}"]
    return "\n".join(lines) + "\n"


def write_report(report: Mapping[str, Any], out_path: Path) -> None:
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
    out_path.with_suffix(".md").write_text(markdown(report), encoding="utf-8",
                                           newline="\n")


def bench(doc_path: Path, out_path: Path, repeats: int, keep: bool) -> Dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    work = TEMP_ROOT / f"run-{os.getpid()}-{time.perf_counter_ns()}"
    work.mkdir(parents=True, exist_ok=False)
    ffmpeg = ffmpeg_binary()
    ffprobe = ffprobe_binary(ffmpeg)
    monitor = RssMonitor()
    report: Dict[str, Any] = {
        "ok": False, "error": None, "command": None,
        "peak_rss_bytes": None, "peak_rss_source": "unavailable",
        "frames_source": "unavailable", "keep": bool(keep),
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "doc": str(doc_path), "doc_sha256": None, "out": str(out_path),
        "repeats": repeats, "wall_seconds": 0.0, "seconds_total": 0.0,
        "repeats_data": [], "workdir": str(work),
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "ffmpeg_version": first_line([ffmpeg, "-version"]),
            "ffprobe": ffprobe, "audio_policy": AUDIO_POLICY},
    }
    try:
        document = json.loads(doc_path.read_text(encoding="utf-8"))
        report["doc_sha256"] = sha256_file(doc_path)
        for index in range(repeats):
            target = work / f"repeat-{index}" / "out.mp4"
            target.parent.mkdir(parents=True, exist_ok=True)
            row: Dict[str, Any] = {"index": index, "seconds": None, "bytes": None,
                                   "sha256": None, "frames": None, "out": str(target)}
            monitor.start()
            try:
                started = time.perf_counter()
                render = render_once(document, target, target.parent / "stage")
                row["seconds"] = time.perf_counter() - started
            finally:
                peak = monitor.stop()
                if peak is not None:
                    report["peak_rss_bytes"] = max(report["peak_rss_bytes"] or 0, peak)
                    report["peak_rss_source"] = "psutil"
            frames, source = frame_count(target, ffprobe)
            if frames is None:
                frames = next(
                    (value for key in FRAME_KEYS
                     if isinstance(value := render.get(key), int)
                     and not isinstance(value, bool)),
                    None,
                )
                source = "report" if frames is not None else "unavailable"
            report["frames_source"] = source
            row["frames"] = frames
            row["bytes"] = os.path.getsize(target)
            row["sha256"] = sha256_file(target)
            report["repeats_data"].append(row)
            report["seconds_total"] = sum(r["seconds"] for r in report["repeats_data"])
            command = render.get("command")
            if report["command"] is None and isinstance(command, (list, tuple)):
                report["command"] = [str(part) for part in command]
        report["ok"] = bool(report["repeats_data"]) and all(
            row["bytes"] and row["sha256"] for row in report["repeats_data"])
    except Exception as exc:  # noqa: BLE001 - a failure is data, not a crash
        report["error"] = {"code": error_code(exc), "message": str(exc),
                           "stderr_tail": tail_lines(traceback.format_exc())}
        report["ok"] = False
    finally:
        report["wall_seconds"] = time.perf_counter() - _STARTED
    write_report(report, out_path)
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vx.bench",
                                     description="Measure a real EDL 3 render.")
    parser.add_argument("--doc", required=True, help="EDL 3 document (JSON)")
    parser.add_argument("--out", required=True, help="JSON report to write")
    parser.add_argument("--repeats", type=int, default=1, help="renders to measure")
    parser.add_argument("--keep", action="store_true", help="keep the bench temp dir")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error(f"--repeats must be >= 1, received {args.repeats}")
    report = bench(Path(args.doc).resolve(), Path(args.out).resolve(), args.repeats,
                   args.keep)
    if report["ok"]:
        return 0
    print(f"bench failed: {report['error']['code']}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
