"""D-21: a dark frame is measured, but its sharpness never becomes a gate.

The T09 corpus is the reason this file exists: c01 (mean luma 38.7) was judged
"mosso, sfuocata" and still scores 2.32 on the raw Laplacian variance, above c03
(88.7, "buona", 1.92). Five contrast-aware candidates were tried on the same
frames and none of them inverts the pair, so the honest fix is a declared floor:
below `SHARPNESS_MIN_LUMA` the number is a description and the triage gate
records `video_sharpness` as not evaluated.

python tests/test_sharpness_lowlight.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import asset_triage, visual  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:58} {reason}")


def clip(target: Path, *, source: str, brightness: float, blur: int) -> Path:
    chain = [f"eq=brightness={brightness}"]
    if blur:
        chain = [f"boxblur={blur}:{blur}"] + chain
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
            f"{source}=size=320x568:rate=24:duration=1",
            "-vf",
            ",".join(chain),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 or not target.is_file():
        raise RuntimeError(proc.stderr[-300:] or "ffmpeg could not build the clip")
    return target


def main() -> int:
    try:
        binary = ff.ffmpeg()
    except Exception as exc:  # noqa: BLE001 - a missing encoder is a declared skip
        skip("whole file", f"no ffmpeg: {type(exc).__name__}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0
    if not binary:
        skip("whole file", "no ffmpeg")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    with tempfile.TemporaryDirectory(prefix="vx-sharp-lowlight-") as raw:
        work = Path(raw)
        try:
            bright = clip(
                work / "bright.mp4", source="testsrc2", brightness=0.02, blur=0
            )
            dark = clip(work / "dark.mp4", source="testsrc2", brightness=-0.75, blur=2)
        except Exception as exc:  # noqa: BLE001
            check("the fixtures build", False, f"{type(exc).__name__}: {exc}")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 1

        bright_m = visual.measure_sharpness(bright)
        dark_m = visual.measure_sharpness(dark)

        check(
            "the bright clip is measured and reliable",
            bright_m["reliable"] is True and bright_m["samples"] >= 1,
            f"luma={bright_m['mean_luma']:.1f} sharp={bright_m['sharpness']:.2f}",
        )
        check(
            "the dark clip is measured but declared unreliable",
            dark_m["reliable"] is False,
            f"luma={dark_m['mean_luma']:.1f} sharp={dark_m['sharpness']:.2f}",
        )
        check(
            "the floor is the declared one",
            visual.SHARPNESS_MIN_LUMA == 60.0,
            visual.SHARPNESS_MIN_LUMA,
        )

        bright_asset = asset_triage.AssetInfo(
            asset_id="bright", kind="video", path=str(bright)
        )
        dark_asset = asset_triage.AssetInfo(
            asset_id="dark", kind="video", path=str(dark)
        )
        bright_metrics = asset_triage.measure_asset(bright_asset)
        dark_metrics = asset_triage.measure_asset(dark_asset)

        check(
            "triage gates on the bright clip's sharpness",
            "sharpness" in bright_metrics and bright_metrics.get("sharpness_reliable") is None,
            sorted(k for k in bright_metrics if k.startswith("sharpness")),
        )
        check(
            "triage keeps the dark number as a description",
            "sharpness" not in dark_metrics
            and dark_metrics.get("sharpness_reliable") is False
            and "sharpness_descriptive" in dark_metrics,
            sorted(k for k in dark_metrics if k.startswith("sharpness")),
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
