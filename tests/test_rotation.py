"""D-9: a clip that says "rotate 90" reaches the canvas upright.

The probe reports the *display* size (width and height swapped for a 90/270
display matrix) and ffmpeg autorotates on decode, so the proxy and the canvas see
the same portrait frame the phone meant. This test proves both halves on a
synthetic clip whose content is asymmetric: a red band on the left third becomes
a red band on the top third once the file is rotated clockwise.

python tests/test_rotation.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import probe, proxy  # noqa: E402
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


def run(args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def build(path: Path) -> Path:
    """320x180, red band on the left third, one second."""
    proc = run(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=green:size=320x180:rate=24:duration=1",
            "-vf",
            "drawbox=x=0:y=0:w=107:h=180:color=red@1:t=fill",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            str(path),
        ]
    )
    if proc.returncode != 0 or not path.is_file():
        raise RuntimeError(proc.stderr[-300:] or "ffmpeg could not build the clip")
    return path


def rotated(source: Path, target: Path) -> Path:
    """Same pixels, with a 90 degree display matrix (a phone's portrait clip)."""
    # ffmpeg 8 writes the display matrix from an input option; the old
    # ``-metadata rotate=90`` is ignored on stream copy and would test nothing.
    proc = run(
        [
            "-display_rotation",
            "90",
            "-i",
            str(source),
            "-c",
            "copy",
            str(target),
        ]
    )
    if proc.returncode != 0 or not target.is_file():
        raise RuntimeError(proc.stderr[-300:] or "ffmpeg could not rotate the clip")
    return target


def band(video: Path) -> dict:
    """Where the red band sits: mean redness of each third, top/bottom and sides."""
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(video))
    try:
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok or frame is None:
        raise RuntimeError("cannot read the first frame")
    b, g, r = (frame[:, :, i].astype(float) for i in range(3))
    redness = r - np.maximum(g, b)
    h, w = frame.shape[:2]
    v = max(1, h // 3)
    s = max(1, w // 3)
    return {
        "size": (w, h),
        "top": float(redness[:v].mean()),
        "bottom": float(redness[-v:].mean()),
        "left": float(redness[:, :s].mean()),
        "right": float(redness[:, -s:].mean()),
    }


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

    with tempfile.TemporaryDirectory(prefix="vx-rotation-") as raw:
        work = Path(raw)
        try:
            flat = build(work / "flat.mp4")
            phone = rotated(flat, work / "phone.mp4")
        except Exception as exc:  # noqa: BLE001
            check("the fixtures build", False, f"{type(exc).__name__}: {exc}")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 1

        plain = probe.probe(flat)
        turned = probe.probe(phone)
        check(
            "the flat clip is landscape and says nothing",
            (plain.width, plain.height, plain.rotation) == (320, 180, 0),
            f"{plain.width}x{plain.height} rot={plain.rotation}",
        )
        check(
            "the display matrix is read and the size is the display one",
            (turned.width, turned.height) == (180, 320),
            f"{turned.width}x{turned.height} rot={turned.rotation}",
        )
        check(
            "the rotation is declared as 90",
            turned.rotation % 360 == 90,
            turned.rotation,
        )

        made = proxy.make_proxy(str(phone), str(work / "proxy" / "proxy.mp4"), width=180)
        made_info = probe.probe(Path(str(made["path"])))
        check(
            "the proxy keeps the portrait shape",
            made_info.height > made_info.width,
            f"{made_info.width}x{made_info.height}",
        )

        where = band(Path(str(made["path"])))
        ends = max(where["top"], where["bottom"])
        sides = max(where["left"], where["right"])
        check(
            "the left band became a horizontal end: the frame is upright",
            ends > 20.0 and ends > sides + 20.0,
            f"top {where['top']:.1f} bottom {where['bottom']:.1f} "
            f"left {where['left']:.1f} right {where['right']:.1f}",
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
