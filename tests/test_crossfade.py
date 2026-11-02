"""F4-T12: a crossfade is an overlap, and the timeline gets shorter by exactly that.

The document's own worked example: two 2 s clips with a 0.5 s dissolve are 3.5 s
long. The test checks the arithmetic, the graph the compiler writes, and the file
that comes out: duration, and a frame inside the dissolve that is a blend of both
shots rather than either one.

python tests/test_crossfade.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, edl3_render, probe  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0

FPS = 24
SIZE = (320, 568)
DISSOLVE = 0.5


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:60} {reason}")


def solid(path: Path, colour: str, seconds: float = 2.0) -> Path:
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
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
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


def document(first: Path, second: Path) -> edl3.Timeline3:
    return edl3.Timeline3(
        fps=FPS,
        size=SIZE,
        clips=[
            edl3.Clip3(id="red", source=str(first), src_in=0.0, src_out=2.0),
            edl3.Clip3(
                id="blue",
                source=str(second),
                src_in=0.0,
                src_out=2.0,
                transition_in=edl3.Transition("crossfade", DISSOLVE),
            ),
        ],
        audio=[
            edl3.AudioTrack3(
                role="dialogue",
                clips=[
                    edl3.AudioClip3(
                        source=str(first), src_in=0.0, src_out=2.0, dst_in=0.0, dst_out=2.0
                    ),
                    edl3.AudioClip3(
                        source=str(second), src_in=0.0, src_out=2.0, dst_in=2.0, dst_out=4.0
                    ),
                ],
            )
        ],
    )


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

    with tempfile.TemporaryDirectory(prefix="vx-crossfade-") as raw:
        work = Path(raw)
        try:
            red = solid(work / "red.mp4", "red")
            blue = solid(work / "blue.mp4", "blue")
        except Exception as exc:  # noqa: BLE001
            check("the fixtures build", False, f"{type(exc).__name__}: {exc}")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 1

        doc = document(red, blue)
        check(
            "two 2 s clips with a 0.5 s dissolve last exactly 3.5 s",
            abs(doc.duration - 3.5) < 1e-9,
            doc.duration,
        )

        problems = [p for p in doc.validate() if p["code"] in edl3_render.HARD_CODES]
        check("the document validates", not problems, problems)

        plan = edl3_render.compile_timeline(doc, audio="mix")
        graph = plan["filter_script"]
        check(
            "the graph dissolves at 1.5 s for 0.5 s",
            "xfade=transition=fade:duration=0.500000:offset=1.500000" in graph,
            [line for line in graph.splitlines() if "xfade" in line][:1],
        )
        check(
            "the dialogue dissolves on the same join",
            "acrossfade=d=0.5" in graph,
            [line for line in graph.splitlines() if "acrossfade" in line][:1],
        )
        check(
            "no bare concat of the two shots is left",
            "concat=n=2:v=1:a=0[out]" not in graph,
            "",
        )

        out = work / "crossfade.mp4"
        edl3_render.render(
            doc, out, workdir=work / "render", audio="mix", settings={}
        )
        info = probe.probe(out)
        check(
            "the rendered file is 3.5 s, not 4",
            abs(info.duration - 3.5) <= 1.0 / FPS + 1e-6,
            f"{info.duration:.4f} s",
        )

        import cv2

        cap = cv2.VideoCapture(str(out))
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, 1750.0)
            ok, frame = cap.read()
        finally:
            cap.release()
        if not ok or frame is None:
            check("a frame inside the dissolve", False, "cannot decode")
        else:
            b, _g, r = (float(frame[:, :, i].mean()) for i in range(3))
            check(
                "a frame inside the dissolve is a blend of both shots",
                r > 40.0 and b > 40.0,
                f"red {r:.0f} blue {b:.0f}",
            )

        chunked = None
        try:
            from vx import edl3_chunks as chunks_mod

            chunks_mod.render_chunked(
                doc, work / "chunked.mp4", workdir=work / "chunks", chunks=2
            )
        except Exception as exc:  # noqa: BLE001 - the refusal is the point
            chunked = getattr(exc, "code", type(exc).__name__)
        check(
            "a chunked render refuses to cut the dissolve",
            chunked == "CHUNK_UNSUPPORTED",
            chunked,
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
