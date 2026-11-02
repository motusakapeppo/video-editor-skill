"""F16-T05: two lossless passes give the same frames as one, and the weight is said.

Scenario:

S1 — a source encoded once to the declared lossless intermediate, then decoded,
     gives frame sha A; encoding it *again* and decoding gives the same frame sha
     A. Lossless means the pixels are not touched.
S2 — the intermediate is heavier than the delivery encode: the ratio is measured
     and printed, so "lossless costs disk" is a number, not a feeling.
S3 — the lossless codec is not in the router's table: `pick` can never choose it
     for a master.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import accel  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:60} {reason}")


def run(args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def frame_sha(video: Path, work: Path, name: str) -> str:
    target = work / f"{name}.png"
    proc = run(["-i", str(video), "-frames:v", "1", str(target)])
    if proc.returncode != 0 or not target.is_file():
        raise RuntimeError(proc.stderr[-200:] or "cannot decode")
    return hashlib.sha256(target.read_bytes()).hexdigest()


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

    check(
        "S3 the lossless arguments are declared",
        list(accel.lossless_args())[:2] == ["-c:v", "ffv1"],
        accel.lossless_args(),
    )
    check(
        "S3 and the router cannot pick it as an encoder",
        "ffv1" not in accel.ENCODERS,
        sorted(accel.ENCODERS),
    )

    with tempfile.TemporaryDirectory(prefix="vx-lossless-") as raw:
        work = Path(raw)
        source = work / "source.mp4"
        proc = run(
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=12:duration=1",
                "-c:v",
                "libx264",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                str(source),
            ]
        )
        if proc.returncode != 0 or not source.is_file():
            check("the fixture builds", False, proc.stderr[-200:])
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 1

        first = work / "lossless-1.mkv"
        second = work / "lossless-2.mkv"
        for src, dst in ((source, first), (first, second)):
            proc = run(["-i", str(src), *accel.lossless_args(), str(dst)])
            if proc.returncode != 0 or not dst.is_file():
                check("the lossless pass runs", False, proc.stderr[-200:])
                print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
                return 1

        one = frame_sha(first, work, "one")
        two = frame_sha(second, work, "two")
        check(
            "S1 the second lossless pass keeps the frame identical",
            one == two,
            f"{one[:16]} vs {two[:16]}",
        )

        delivery = work / "delivery.mp4"
        run(
            [
                "-i",
                str(source),
                "-c:v",
                "libx264",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                str(delivery),
            ]
        )
        ratio = first.stat().st_size / max(delivery.stat().st_size, 1)
        print(
            f"    [INFO] peso dichiarato: lossless {first.stat().st_size} byte, "
            f"delivery {delivery.stat().st_size} byte, x{ratio:.1f}"
        )
        check(
            "S2 the intermediate is heavier, and the number is measured",
            ratio >= 1.0,
            f"x{ratio:.1f}",
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
