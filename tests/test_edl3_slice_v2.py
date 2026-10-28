"""F2 wave-2 vertical slice: a /1.1 document with 2 keyframes and a transition.

One 320x568 synthetic source, one clip, one transform effect with exactly two
opacity keyframes and one ``fade_in``. The document must validate, render to a
non-empty master, and two independent renders must yield a byte-identical
extracted frame (sha256), not merely a similar one.

    python tests/test_edl3_slice_v2.py
"""

from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402
from vx import edl3_render  # noqa: E402
from vx import golden  # noqa: E402
from vx.ffmpeg import run_ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FPS = 12
SIZE = (320, 568)
DURATION = 2.0
FRAME_AT = 1.0
SETTINGS = {"chromium_available": False}


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:64} {reason}")


def make_source(path: Path) -> str:
    proc = run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c=white:s={SIZE[0]}x{SIZE[1]}:r={FPS}:d={DURATION}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        loglevel="error",
    )
    return "" if proc.ok and path.exists() else proc.tail()


def build(source: Path) -> edl3.Timeline3:
    track = edl3.KeyframeTrack(
        [
            edl3.Keyframe(t=0.0, value=1.0),
            edl3.Keyframe(t=DURATION, value=0.4),
        ]
    )
    effect = edl3.Effect(
        kind="transform",
        params={"scale": 1.0, "opacity": 1.0},
        keyframes={"opacity": track},
    )
    clip = edl3.Clip3(
        id="c1",
        source=str(source),
        src_in=0.0,
        src_out=DURATION,
        effects=[effect],
        transition_in=edl3.Transition(kind="fade_in", duration=0.4),
    )
    return edl3.Timeline3(
        fps=FPS,
        size=SIZE,
        clips=[clip],
        version=edl3.EDL3_VERSION,
    )


def main() -> int:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vx_slice_v2_") as tmp:
        work = Path(tmp)
        source = work / "white.mp4"
        problem = make_source(source)
        if problem:
            skip("synthetic source", problem)
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 0

        try:
            doc = build(source)
            print("\n-- (a) the smallest declarative document")
            check(
                "the document declares a version the reader accepts",
                doc.version in edl3.EDL3_VERSIONS,
                doc.version,
            )
            check(
                "the effect carries exactly two keyframes",
                doc.clips[0].transform().keyframe_count() == 2,
                doc.clips[0].transform().keyframe_count(),
            )
            check(
                "the transition is declared",
                doc.clips[0].transition_in is not None
                and doc.clips[0].transition_in.kind == "fade_in",
                doc.clips[0].transition_in,
            )
            problems = doc.validate()
            check("validate() returns no problem", problems == [], problems)
            check(
                "the round trip preserves the document",
                edl3.Timeline3.from_dict(doc.to_dict()).to_dict() == doc.to_dict(),
            )

            print("\n-- (b) two renders, one byte-identical frame")
            first = edl3_render.render(
                doc,
                work / "first.mp4",
                workdir=work / "first",
                settings=SETTINGS,
            )
            second = edl3_render.render(
                doc,
                work / "second.mp4",
                workdir=work / "second",
                settings=SETTINGS,
            )
            first_path = work / "first.mp4"
            second_path = work / "second.mp4"
            check(
                "both masters exist and are non-empty",
                first_path.exists()
                and second_path.exists()
                and first_path.stat().st_size > 0
                and second_path.stat().st_size > 0,
                f"{first_path.stat().st_size} / {second_path.stat().st_size} bytes",
            )
            check(
                "the report names the expr backend and 2 keyframes",
                first["backend"] == edl3_render.BACKEND_EXPR
                and first["clips"][0]["keyframes"] == 2,
                f"{first['backend']} keyframes={first['clips'][0]['keyframes']}",
            )
            check(
                "the transition reached the graph",
                bool(first["clips"][0]["fade_filter"]),
                first["clips"][0]["fade_filter"],
            )
            check(
                "the settings were declared, not probed",
                first["settings_source"] == "declared",
                first["settings_source"],
            )
            frame_first = golden.capture(
                first_path, [FRAME_AT], work / "frames_first"
            )["frames"][0]
            frame_second = golden.capture(
                second_path, [FRAME_AT], work / "frames_second"
            )["frames"][0]
            check(
                "both captures extracted one frame at the declared time",
                frame_first["time"] == FRAME_AT
                and frame_second["time"] == FRAME_AT,
                f"{frame_first['time']} / {frame_second['time']}",
            )
            check(
                "the two extracted frames have a byte-identical sha256",
                frame_first["sha256"] == frame_second["sha256"]
                and len(frame_first["sha256"]) == 64,
                frame_first["sha256"][:16],
            )
        except Exception:  # noqa: BLE001 - a crash is a failure, never a skip
            traceback.print_exc()
            check("test harness completed without an unexpected exception", False)

    elapsed = time.monotonic() - started
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  ({elapsed:.1f}s)")
    check("suite stays under the 90 s ceiling", elapsed < 90.0, f"{elapsed:.1f}s")
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
