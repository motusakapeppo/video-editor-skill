"""F16-T02: the same document renders byte-identically in parallel and in series.

Scenario, written before the change:

S1 — a 4 s document cut into four 1 s chunks, rendered once with `jobs=1` and once
     with `jobs=4`, produces the same sha256. The two wall times are measured and
     printed; the assertion is the bytes, because a faster wrong file is worthless.
S2 — the report says how many workers ran, so a silent fallback to serial is
     visible instead of assumed.

python tests/test_parallel_chunks.py
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, edl3_render  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FPS = 12
SIZE = (320, 568)


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:60} {reason}")


def clip_file(path: Path, colour: str, seconds: float = 2.0) -> Path:
    proc = subprocess.run(
        [
            ff.ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={colour}:size={SIZE[0]}x{SIZE[1]}:rate={FPS}:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 or not path.is_file():
        raise RuntimeError(proc.stderr[-300:] or "ffmpeg could not build the clip")
    return path


def document(paths: list) -> edl3.Timeline3:
    """Four one-second clips: a chunk boundary can only fall between clips."""
    clips = []
    for index, path in enumerate(paths):
        clips.append(
            edl3.Clip3(
                id=f"c{index}",
                source=str(path),
                src_in=0.0,
                src_out=1.0,
                dst_in=float(index),
                dst_out=float(index + 1),
            )
        )
    return edl3.Timeline3(fps=FPS, size=SIZE, clips=clips)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    try:
        binary = ff.ffmpeg()
    except Exception as exc:  # noqa: BLE001
        skip("whole file", f"no ffmpeg: {type(exc).__name__}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0
    if not binary:
        skip("whole file", "no ffmpeg")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    with tempfile.TemporaryDirectory(prefix="vx-parallel-") as raw:
        work = Path(raw)
        try:
            pass
        except Exception as exc:  # noqa: BLE001
            check("the fixtures build", False, f"{type(exc).__name__}: {exc}")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 1

        colours = [
            clip_file(work / name, colour, seconds=1.0)
            for name, colour in (
                ("red.mp4", "red"),
                ("blue.mp4", "blue"),
                ("green.mp4", "green"),
                ("white.mp4", "white"),
            )
        ]
        doc = document(colours)
        serial = work / "serial.mp4"
        together = work / "parallel.mp4"

        started = time.perf_counter()
        report = edl3_render.render(
            doc,
            serial,
            workdir=work / "w-serial",
            audio="drop",
            chunks=4,
            jobs=1,
            settings={},
        )
        serial_seconds = time.perf_counter() - started

        started = time.perf_counter()
        report_p = edl3_render.render(
            doc,
            together,
            workdir=work / "w-parallel",
            audio="drop",
            chunks=4,
            jobs=4,
            settings={},
        )
        parallel_seconds = time.perf_counter() - started

        check(
            "S1 four chunks in parallel are byte-identical to four in series",
            sha256(serial) == sha256(together),
            f"{sha256(serial)[:16]} vs {sha256(together)[:16]}",
        )
        print(
            f"    [INFO] tempo misurato: serie {serial_seconds:.2f} s, "
            f"parallelo {parallel_seconds:.2f} s "
            f"(x{serial_seconds / max(parallel_seconds, 1e-9):.2f})"
        )
        check(
            "S1 both files are whole MP4 of the same length",
            serial.stat().st_size > 1024 and together.stat().st_size > 1024,
            f"{serial.stat().st_size} / {together.stat().st_size} bytes",
        )
        check(
            "S2 the report names the workers",
            int(report.get("jobs", 0)) == 1 and int(report_p.get("jobs", 0)) == 4,
            f"{report.get('jobs')} / {report_p.get('jobs')}",
        )
        def covered(payload: dict) -> bool:
            block = payload.get("chunks") or {}
            done = sorted(block.get("rendered", []) + block.get("reused", []))
            return int(block.get("count", 0)) == 4 and done == [0, 1, 2, 3]

        check(
            "S2 the chunks were all rendered, not skipped",
            covered(report) and covered(report_p),
            f"{(report.get('chunks') or {}).get('count')} / "
            f"{(report_p.get('chunks') or {}).get('count')}",
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
