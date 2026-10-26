"""Asset-triage metrics: raw Laplacian sharpness and bounded exposure.

Run:
    python tests/test_asset_metrics.py
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import analyze, visual  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:56} {reason}")


def _ffmpeg(args: list[str]) -> bool:
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"] + args,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _CountingCapture:
    def __init__(self, cap, counter):
        self._cap = cap
        self._counter = counter

    def read(self):
        self._counter["reads"] += 1
        return self._cap.read()

    def __getattr__(self, name):
        return getattr(self._cap, name)


def _reads_for(path: Path, **kwargs):
    real = analyze.cv2.VideoCapture
    counter = {"reads": 0}

    def factory(*args, **kw):
        return _CountingCapture(real(*args, **kw), counter)

    analyze.cv2.VideoCapture = factory
    try:
        result = analyze.measure_exposure(str(path), **kwargs)
    finally:
        analyze.cv2.VideoCapture = real
    return result, counter["reads"]


def main() -> int:
    if not analyze._CV_OK:
        skip("OpenCV", analyze._CV_WHY or "non disponibile")
        print(f"\n{PASS} PASS / {FAIL} FAIL")
        return 0

    with tempfile.TemporaryDirectory(prefix="vx-metrics-") as raw:
        work = Path(raw)
        sharp = work / "sharp.mp4"
        blur = work / "blur.mp4"
        still = work / "sharp.png"
        dark = work / "dark.mp4"
        long = work / "long.mp4"

        ok_sharp = _ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=25",
                "-t",
                "1",
                "-pix_fmt",
                "yuv420p",
                str(sharp),
            ]
        )
        ok_blur = _ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=25",
                "-t",
                "1",
                "-vf",
                "gblur=sigma=8",
                "-pix_fmt",
                "yuv420p",
                str(blur),
            ]
        )
        ok_still = _ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=25",
                "-frames:v",
                "1",
                str(still),
            ]
        )
        ok_dark = _ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x180:rate=25",
                "-t",
                "1",
                "-pix_fmt",
                "yuv420p",
                str(dark),
            ]
        )
        ok_long = _ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=25",
                "-t",
                "2",
                "-pix_fmt",
                "yuv420p",
                str(long),
            ]
        )

        before = _sha256(sharp) if ok_sharp else ""

        if ok_sharp and ok_blur:
            sharp_value = visual.measure_sharpness(sharp)
            blur_value = visual.measure_sharpness(blur)
            margin = sharp_value["sharpness"] - blur_value["sharpness"]
            check(
                "sharpness: sharp > blurred",
                sharp_value["sharpness"] > blur_value["sharpness"],
                f"sharp={sharp_value['sharpness']:.2f} "
                f"blur={blur_value['sharpness']:.2f} margin={margin:.2f}",
            )
        else:
            skip("sharpness: sharp > blurred", "fixture ffmpeg non disponibile")

        if ok_still:
            still_value = visual.measure_sharpness(still)
            check(
                "sharpness: still image",
                still_value["samples"] >= 1 and still_value["sharpness"] > 0.0,
                f"samples={still_value['samples']} "
                f"sharpness={still_value['sharpness']:.2f}",
            )
        else:
            skip("sharpness: still image", "fixture ffmpeg non disponibile")

        bad = work / "truncated.mp4"
        bad.write_bytes(b"not a media file\n" * 8)
        bad_value = visual.measure_sharpness(bad)
        check(
            "sharpness: decode failure is unavailable",
            bad_value["method"].startswith("unavailable")
            and bad_value["sharpness"] == 0.0,
            bad_value["method"],
        )

        if ok_dark:
            exposure = analyze.measure_exposure(dark)
            check(
                "exposure: black clip is dark",
                exposure.get("verdict") == "dark",
                exposure,
            )
        else:
            skip("exposure: black clip is dark", "fixture ffmpeg non disponibile")

        if ok_long:
            full, full_reads = _reads_for(long)
            bounded, bounded_reads = _reads_for(long, max_seconds=0.2)
            check(
                "exposure: max_seconds bounds the decode",
                bounded_reads < full_reads,
                f"full={full_reads} bounded={bounded_reads}",
            )
            check(
                "exposure: same key set",
                set(bounded) == set(full),
                sorted(bounded),
            )
        else:
            skip(
                "exposure: max_seconds bounds the decode",
                "fixture ffmpeg non disponibile",
            )
            skip("exposure: same key set", "fixture ffmpeg non disponibile")

        if ok_sharp:
            after = _sha256(sharp)
            check("source sha256 unchanged", before == after, before[:12])
        else:
            skip("source sha256 unchanged", "fixture ffmpeg non disponibile")

    if SKIP:
        print(f"\n{SKIP} SKIP")
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
