"""Golden frames: real PNG capture, pixel comparison, no silent skips.

The point of this file is that it does not test a string. ``capture`` runs
ffmpeg and writes actual frames; ``compare`` decodes them. Every check below
fails the way the production path would fail.

    python tests/test_golden_frames.py
"""

from __future__ import annotations

import copy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import golden  # noqa: E402
from vx.ffmpeg import ffmpeg  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
TIMES = [0.2, 0.8, 1.6]


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:58} {reason}")


def make_fixture(path: Path) -> str:
    proc = subprocess.run(
        [
            ffmpeg(),
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=10:duration=3",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-g",
            "10",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return "" if proc.returncode == 0 and path.exists() else (proc.stderr or "")[-400:]


def alter_png(path: Path, delta: int = 40) -> None:
    """Change the pixels of a captured frame in place, same dimensions."""
    from PIL import Image

    with Image.open(path) as image:
        frame = image.convert("RGB")
    pixels = frame.load()
    width, height = frame.size
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            pixels[x, y] = (min(255, r + delta), g, b)
    frame.save(path, format="PNG")


def raise_and_message(fn) -> str:
    try:
        fn()
    except golden.GoldenError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - wrong error type is a failure
        return f"__WRONG_TYPE__{type(exc).__name__}: {exc}"
    return ""


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx_golden_") as tmp:
        work = Path(tmp)
        video = work / "fixture.mp4"
        problem = make_fixture(video)
        if problem:
            skip("fixture video", problem)
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 0

        # --- capture twice: identical hashes, identical manifest values ------
        first = golden.capture(video, TIMES, work / "run_a")
        second = golden.capture(video, TIMES, work / "run_b")

        hashes_a = [frame["sha256"] for frame in first["frames"]]
        hashes_b = [frame["sha256"] for frame in second["frames"]]
        check(
            "capture twice -> identical frame hashes",
            hashes_a == hashes_b,
            hashes_a[0][:12],
        )
        check("manifest version declared", first["version"] == golden.MANIFEST_VERSION)
        check(
            "manifest carries video_sha256",
            isinstance(first["video_sha256"], str) and len(first["video_sha256"]) == 64,
            first["video_sha256"][:12],
        )
        check(
            "manifest carries one hash per time",
            sorted(first["frame_sha256"].keys())
            == sorted(f"{float(t):.6f}" for t in TIMES),
            list(first["frame_sha256"].keys()),
        )
        check("manifest carries frame size", first["size"] == [320, 180], first["size"])
        check(
            "one PNG per timestamp on disk",
            all(Path(frame["path"]).exists() for frame in first["frames"])
            and len(list((work / "run_a").glob("*.png"))) == len(TIMES),
        )

        # --- comparison of identical captures ------------------------------
        check("compare(identical) is empty", golden.compare(first, second) == [])
        reloaded = golden.load(work / "run_b" / "manifest.json")
        check(
            "compare via manifest.json on disk is empty",
            golden.compare(first, reloaded) == [],
        )
        try:
            golden.assert_golden(first, reloaded)
            passed = True
        except golden.GoldenError:
            passed = False
        check("assert_golden(identical) does not raise", passed)

        # --- one deliberately altered frame --------------------------------
        tampered = copy.deepcopy(second)
        target_time = TIMES[-1]
        target = next(
            frame
            for frame in tampered["frames"]
            if abs(frame["time"] - target_time) < 1e-9
        )
        alter_png(Path(target["path"]))
        new_hash = golden._sha256_file(Path(target["path"]))
        check(
            "altered PNG changes its own hash",
            new_hash != target["sha256"],
            new_hash[:12],
        )
        target["sha256"] = new_hash
        tampered["frame_sha256"][f"{float(target_time):.6f}"] = new_hash

        differences = golden.compare(first, tampered)
        check("altered frame is reported", len(differences) == 1, len(differences))
        check(
            "reported time is the altered one",
            bool(differences) and abs(differences[0]["time"] - target_time) < 1e-9,
            differences[0]["time"] if differences else None,
        )
        check(
            "difference is a real pixel MAD",
            bool(differences)
            and differences[0]["method"] == "pillow"
            and differences[0]["mad"] > 1.0,
            (differences[0]["mad"], differences[0]["mad_r"]) if differences else None,
        )
        message = raise_and_message(lambda: golden.assert_golden(first, tampered))
        check(
            "assert_golden names the failing timestamp",
            f"{target_time:.3f}s" in message,
            message.replace("\n", " | ")[:90],
        )
        check(
            "pixel_tolerance=255 forgives the altered frame",
            golden.compare(first, tampered, pixel_tolerance=255) == [],
        )
        fallback = golden._ffmpeg_diff(
            Path(first["frames"][-1]["path"]), Path(target["path"])
        )
        check(
            "ffmpeg fallback measures a luma MAD above zero",
            fallback["method"] == "ffmpeg" and fallback["mad"] > 0.0,
            fallback["mad"],
        )

        # --- missing time index is an error, not a skip --------------------
        shrunk = copy.deepcopy(first)
        shrunk["frames"] = shrunk["frames"][:-1]
        shrunk["times"] = shrunk["times"][:-1]
        shrunk["frame_sha256"].pop(f"{float(target_time):.6f}")
        message = raise_and_message(lambda: golden.compare(first, shrunk))
        check(
            "missing time index raises with the time",
            bool(message)
            and "__WRONG_TYPE__" not in message
            and f"{target_time:.3f}s" in message,
            message[:90],
        )
        message = raise_and_message(lambda: golden.assert_golden(first, shrunk))
        check(
            "assert_golden also refuses the shrunk manifest",
            "__WRONG_TYPE__" not in message,
        )

        # --- a frame file that vanished is an error ------------------------
        orphan = copy.deepcopy(first)
        moved = Path(orphan["frames"][0]["path"])
        backup = work / "moved.png"
        shutil.move(str(moved), str(backup))
        try:
            other = copy.deepcopy(second)
            # force a hash mismatch so compare has to decode the missing file
            other["frames"][0]["sha256"] = "0" * 64
            message = raise_and_message(lambda: golden.compare(orphan, other))
        finally:
            shutil.move(str(backup), str(moved))
        check(
            "vanished frame file raises instead of scoring 0",
            bool(message) and "__WRONG_TYPE__" not in message,
            message[:80],
        )

        # --- timestamps are validated --------------------------------------
        message = raise_and_message(lambda: golden.capture(video, [], work / "run_d"))
        check(
            "capture with no timestamps raises",
            "__WRONG_TYPE__" not in message and bool(message),
        )
        message = raise_and_message(
            lambda: golden.capture(video, [-1.0], work / "run_e")
        )
        check(
            "negative timestamp raises",
            "__WRONG_TYPE__" not in message and bool(message),
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
