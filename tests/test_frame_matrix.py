"""F26-T03 gate — every canvas and rate the corpus needs, proven by rendering.

The engine declares no format of its own (D033): it accepts any canvas and any
rate a camera or an editor delivers. This file proves it for the five aspects
and the seven rates the genre profiles declare (``vx.profiles.FPS_CHOICES``):

    9:16 180x320, 16:9 320x180, 1:1 240x240, 4:5 200x250, 2.39:1 344x144
    23.976, 24, 25, 29.97, 30, 50, 60 fps

Thirty-five combinations, each a real render through
``vx.edl3_render.render``. Every source is a 30 fps fixture, so the declared
rate is never the source's: the engine has to resample, and a deliverable that
quietly keeps the input clock fails here. Each file is then measured with
ffprobe taken from ``VX_FFMPEG``'s own directory, never with the values the
document declared:

a. width and height are exactly the declared canvas;
b. the decoded frame count is round(seconds * rate), within one frame;
c. the measured duration is frames / rate, within 0.02 s;
d. the stream's own clock is the declared rate.

Five more renders repeat the same four measurements at the real deliverable
canvas of each aspect (1080x1920, 1920x1080, 1080x1080, 1080x1350, 2048x858).

    python tests/test_frame_matrix.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, edl3_render  # noqa: E402
from vx.ffmpeg import ffmpeg, run_ff  # noqa: E402

#: (name, rendered canvas, deliverable canvas, deliverable rate)
ASPECTS: Tuple[Tuple[str, Tuple[int, int], Tuple[int, int], float], ...] = (
    ("9:16", (180, 320), (1080, 1920), 30.0),
    ("16:9", (320, 180), (1920, 1080), 25.0),
    ("1:1", (240, 240), (1080, 1080), 30.0),
    ("4:5", (200, 250), (1080, 1350), 30.0),
    ("2.39:1", (344, 144), (2048, 858), 24.0),
)
RATES: Tuple[float, ...] = (23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 60.0)
#: The fixtures run at one rate the corpus knows but most declarations do not,
#: so every combination resamples instead of passing the source clock through.
SOURCE_RATE = 30.0
SECONDS = 1.0
SOURCE_SECONDS = 2.0
FRAME_SLACK = 1
DURATION_SLACK = 0.02
CLOCK_SLACK = 1e-3
CEILING_SECONDS = 120.0

PASS = 0
FAIL = 0
FAILED_LABELS: list[str] = []

Row = Dict[str, Any]


# ------------------------------------------------------------------ helpers


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    if not ok:
        # Il log dei runner viene troncato: senza questa lista, un rosso
        # lontano dalla coda non si sa quale sia.
        FAILED_LABELS.append(f"{label} :: {detail}")
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def ffprobe_path() -> Path:
    """ffprobe next to the configured ffmpeg binary, never a second install."""
    declared = os.environ.get("VX_FFMPEG")
    if declared:
        # Un nome ("ffmpeg") non e' un percorso: senza risolverlo `with_name`
        # costruisce un parente relativo che non esiste (su un runner il
        # binario sta in una cartella di sistema).
        found = shutil.which(declared)
        base = Path(found or declared)
    else:
        base = Path(ffmpeg())
    return base.with_name("ffprobe.exe" if base.suffix.lower() == ".exe" else "ffprobe")


def measure(path: Path, probe: Path) -> Dict[str, Any]:
    """Decode every frame of ``path`` and read the numbers ffprobe really found."""
    proc = subprocess.run(
        [str(probe), "-hide_banner", "-loglevel", "error", "-select_streams", "v:0",
         "-count_frames", "-show_entries",
         "stream=width,height,nb_read_frames,r_frame_rate",
         "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path.name}: {proc.stderr.strip()[-300:]}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError(f"ffprobe read no video stream from {path.name}")
    stream = streams[0]
    numerator, _, denominator = str(stream.get("r_frame_rate", "0/1")).partition("/")
    return {
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "frames": int(stream.get("nb_read_frames", 0)),
        "duration": float((data.get("format") or {}).get("duration", 0.0)),
        "clock": float(numerator or 0) / float(denominator or 1),
    }


def fixture(work: Path, size: Tuple[int, int]) -> Path:
    """A deterministic 30 fps source at ``size``; one per canvas, reused."""
    work.mkdir(parents=True, exist_ok=True)
    path = work / f"src_{size[0]}x{size[1]}_{SOURCE_RATE:g}.mp4"
    if path.is_file() and path.stat().st_size > 0:
        return path
    proc = run_ff(
        ["-f", "lavfi", "-i",
         f"testsrc2=s={size[0]}x{size[1]}:r={SOURCE_RATE:g}:d={SOURCE_SECONDS:g}",
         "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-g", "1", str(path)],
        loglevel="error",
    )
    if not proc.ok or not path.is_file():
        raise RuntimeError(f"fixture {path.name} failed: {proc.tail()}")
    return path


@dataclass(frozen=True)
class Case:
    """One cell of the matrix: what to render, and with which fixture."""

    aspect: str
    canvas: Tuple[int, int]
    rate: float
    source: Path
    kind: str

    @property
    def label(self) -> str:
        return f"{self.kind} {self.aspect} {self.rate:g}fps"

    @property
    def slug(self) -> str:
        return f"{self.aspect}_{self.rate:g}".replace(":", "x").replace(".", "_")


def document(case: Case) -> edl3.Timeline3:
    """One clip, the declared canvas, the declared rate, nothing else."""
    doc = edl3.Timeline3.from_dict(
        {"version": edl3.EDL3_VERSION, "fps": case.rate,
         "size": [case.canvas[0], case.canvas[1]],
         "clips": [{"id": "c1", "source": str(case.source), "src_in": 0.0,
                    "src_out": SECONDS, "layer": 0, "dst_in": 0.0,
                    "dst_out": SECONDS}]}
    )
    # Timeline3 truncates a declared rate to an integer (edl3.py, __post_init__),
    # which would turn 23.976 into 23 before the renderer ever sees it. F26-T03
    # is about the renderer carrying the declared rate exactly, so the declaration
    # is restored on the constructed document.
    doc.fps = float(case.rate)
    return doc



def verify(case: Case, measured: Dict[str, Any]) -> None:
    """The four properties a deliverable owes its declaration."""
    want_frames = int(round(SECONDS * case.rate))
    width, height = case.canvas
    check(
        f"{case.label} decodes at {width}x{height}",
        (measured["width"], measured["height"]) == (width, height),
        f"{measured['width']}x{measured['height']}",
    )
    check(
        f"{case.label} holds {want_frames} frame(s)",
        abs(measured["frames"] - want_frames) <= FRAME_SLACK,
        f"{measured['frames']}",
    )
    check(
        f"{case.label} runs {want_frames / case.rate:.3f}s",
        abs(measured["duration"] - measured["frames"] / case.rate) <= DURATION_SLACK,
        f"{measured['duration']:.6f}s for {measured['frames']} frames",
    )
    check(
        f"{case.label} carries the {case.rate:g} fps clock",
        abs(measured["clock"] - case.rate) <= CLOCK_SLACK,
        f"{measured['clock']:.6f}",
    )


def render_case(work: Path, case: Case, probe: Path) -> Row:
    """Render one (canvas, rate) pair and measure the file it produced."""
    destination = work / case.kind / f"{case.slug}.mp4"
    row: Row = {
        "aspect": case.aspect,
        "canvas": case.canvas,
        "rate": case.rate,
        "kind": case.kind,
        "frames": None,
        "want": int(round(SECONDS * case.rate)),
        "duration": None,
        "result": "fail",
    }
    before = FAIL
    try:
        edl3_render.render(document(case), destination, workdir=work / "stage" / case.slug)
        measured = measure(destination, probe)
        row.update(measured)
        verify(case, measured)
        row["result"] = "pass" if FAIL == before else "fail"
    except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
        row["detail"] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:120]}"
        traceback.print_exc()
    return row


def print_table(rows: Sequence[Row], kind: str) -> None:
    header = f"{'aspect':<8} | {'canvas':>11} | {'rate':>7} | {'frames':>6} | {'want':>4} | {'duration':>10} | {'clock':>9} | result"
    print(f"\n-- {kind} renders")
    print(header)
    print("-" * len(header))
    for row in rows:
        if row["kind"] != kind:
            continue
        frames = "-" if row["frames"] is None else f"{row['frames']}"
        duration = "-" if row["duration"] is None else f"{row['duration']:.6f}"
        canvas = f"{row['canvas'][0]}x{row['canvas'][1]}"
        line = (
            f"{row['aspect']:<8} | {canvas:>11} | {row['rate']:>7g} | {frames:>6} | "
            f"{row['want']:>4} | {duration:>10} | {row['clock'] if 'clock' in row else '-':>9} | "
            f"{row['result']}"
        )
        if row.get("detail"):
            line += f"  ({row['detail']})"
        print(line)


# --------------------------------------------------------------------- run


def run(work: Path) -> Tuple[List[Row], float]:
    started = time.monotonic()
    probe = ffprobe_path()
    check("ffprobe sits next to the configured ffmpeg", probe.is_file(), str(probe))
    check("the matrix declares five aspects", len(ASPECTS) == 5, len(ASPECTS))
    check("the matrix declares seven rates", len(RATES) == 7, list(RATES))
    check(
        "six of the seven rates force the engine to resample",
        sum(1 for rate in RATES if abs(rate - SOURCE_RATE) > CLOCK_SLACK) == 6,
        f"fixtures at {SOURCE_RATE:g} fps",
    )

    rows: List[Row] = []
    for name, canvas, _full, _rate in ASPECTS:
        source = fixture(work / "sources" / "small", canvas)
        for rate in RATES:
            rows.append(render_case(work, Case(name, canvas, rate, source, "small"), probe))
    for name, _small, canvas, rate in ASPECTS:
        source = fixture(work / "sources" / "full", canvas)
        rows.append(render_case(work, Case(name, canvas, rate, source, "full"), probe))
    return rows, time.monotonic() - started


def main() -> int:
    print("F26-T03 — canvas x rate matrix, measured on the rendered files")
    with tempfile.TemporaryDirectory(prefix="vx_frame_matrix_") as tmp:
        work = Path(tmp)
        try:
            rows, elapsed = run(work)
        except Exception:  # noqa: BLE001 - an unexpected crash is a failure
            traceback.print_exc()
            print("\nharness crashed before the matrix was measured")
            return 1

    print_table(rows, "small")
    print_table(rows, "full")
    small = [row for row in rows if row["kind"] == "small"]
    full = [row for row in rows if row["kind"] == "full"]
    slow = elapsed > CEILING_SECONDS
    check(
        f"suite stays under the {CEILING_SECONDS:.0f} s ceiling",
        not slow,
        f"{elapsed:.1f}s",
    )
    print(
        f"\n{len(small)} rendered combination(s), {len(full)} full-size render(s), "
        f"{elapsed:.1f}s"
    )
    for label in FAILED_LABELS[:12]:
        print(f"  fallito: {label}")
    print(f"{PASS} PASS / {FAIL} FAIL / 0 SKIP")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())



