"""Il profilo per-frame campiona i frame veri, una volta sola.

    python tests/test_frame_profile.py

P1  i campioni stanno ai tempi dichiarati (multipli di ``step``), uno per passo;
P2  il profilo legge il frame di **quel** tempo: la seconda metà del file è
    sfocata e la nitidezza misurata crolla dove il video cambia, non altrove;
P3  le tre misure restano quelle di sempre (nitidezza, luma, diff) e il primo
    campione ha ``diff`` 255;
P4  un file assente non inventa un profilo.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def _ffmpeg() -> str:
    explicit = os.environ.get("VX_FFMPEG")
    if explicit and Path(explicit).is_file():
        return explicit
    found = shutil.which("ffmpeg")
    return found or "ffmpeg"


def _make(path: Path, work: Path) -> bool:
    """Un secondo nitido + un secondo sfocato, 25 fps, 320x568."""
    ff = _ffmpeg()
    sharp = work / "sharp.mp4"
    blur = work / "blur.mp4"
    common = ["-y", "-hide_banner", "-loglevel", "error"]
    first = subprocess.run(
        [
            ff,
            *common,
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x568:rate=25:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(sharp),
        ],
        capture_output=True,
        check=False,
    )
    second = subprocess.run(
        [
            ff,
            *common,
            "-i",
            str(sharp),
            "-vf",
            "gblur=sigma=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(blur),
        ],
        capture_output=True,
        check=False,
    )
    listing = work / "list.txt"
    listing.write_text(
        f"file '{sharp.as_posix()}'\nfile '{blur.as_posix()}'\n", encoding="utf-8"
    )
    joined = subprocess.run(
        [
            ff,
            *common,
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    if first.returncode or second.returncode or joined.returncode or not path.is_file():
        print("  ffmpeg non ha prodotto il file:", first.stderr[-200:], second.stderr[-200:])
        return False
    return True


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-profile-") as raw:
        work = Path(raw)
        video = work / "half.mp4"
        if not _make(video, work):
            print("SKIP: senza ffmpeg il profilo non si prova")
            return 0

        profile = auto.measure_frame_profile(video, step=0.5)
        if not profile:
            check("P0 il profilo esiste", False, profile)
            print(f"\n{PASS} PASS / {FAIL} FAIL")
            return 1

        samples = profile["samples"]
        times = [item["t"] for item in samples]
        check(
            "P1 un campione per passo, ai tempi dichiarati",
            times == [0.0, 0.5, 1.0, 1.5],
            times,
        )
        check(
            "P1 la durata dichiarata e' quella del file",
            abs(float(profile["duration"]) - 2.0) <= 0.1,
            profile["duration"],
        )
        sharp = [samples[0]["sharpness"], samples[1]["sharpness"]]
        blurred = [samples[2]["sharpness"], samples[3]["sharpness"]]
        check(
            "P2 il primo secondo e' nitido, il secondo sfocato",
            min(sharp) > max(blurred) * 3.0,
            (sharp, blurred),
        )
        check(
            "P3 le tre misure ci sono e il primo diff e' 255",
            all(
                set(item) == {"t", "sharpness", "luma", "diff"} for item in samples
            )
            and samples[0]["diff"] == 255.0,
            samples[0],
        )
        check(
            "P4 un file assente non inventa un profilo",
            auto.measure_frame_profile(work / "non-esiste.mp4") is None,
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
