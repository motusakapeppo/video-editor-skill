"""F16-T03: an identical span is covered without re-encoding; a cut never is.

Scenario:

S1 — render, then render again on the same workdir: every chunk is `reused`, none
     is rendered, and the file is byte-identical. That is the smart render.
S2 — change one clip's *content*: only the chunk that reads it re-renders, the
     others are reused, and the master changes. The cache key covers the source
     content, not just the document.
S3 — change one clip's *span* (a cut): its chunk re-renders, and no chunk script
     carries `-c copy`. A cut is never a copy.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
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


def colour_file(path: Path, colour: str) -> Path:
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
            f"color=c={colour}:size={SIZE[0]}x{SIZE[1]}:rate={FPS}:duration=1",
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


def document(paths) -> edl3.Timeline3:
    return edl3.Timeline3(
        fps=FPS,
        size=SIZE,
        clips=[
            edl3.Clip3(
                id=f"c{index}",
                source=str(path),
                src_in=0.0,
                src_out=1.0,
                dst_in=float(index),
                dst_out=float(index + 1),
            )
            for index, path in enumerate(paths)
        ],
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def states(report: dict) -> tuple:
    block = report.get("chunks") or {}
    return sorted(block.get("rendered", [])), sorted(block.get("reused", []))


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

    with tempfile.TemporaryDirectory(prefix="vx-smart-") as raw:
        work = Path(raw)
        try:
            palette = [
                colour_file(work / f"{name}.mp4", name)
                for name in ("red", "green", "blue", "white")
            ]
        except Exception as exc:  # noqa: BLE001
            check("the fixtures build", False, f"{type(exc).__name__}: {exc}")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 1

        doc = document(palette)
        out = work / "master.mp4"
        run_dir = work / "w"

        first = edl3_render.render(
            doc, out, workdir=run_dir, audio="drop", chunks=4, jobs=1, settings={}
        )
        digest_one = sha256(out)
        rendered, reused = states(first)
        check(
            "S1 the first run renders every chunk",
            rendered == [0, 1, 2, 3] and not reused,
            f"rendered={rendered} reused={reused}",
        )

        second = edl3_render.render(
            doc, out, workdir=run_dir, audio="drop", chunks=4, jobs=1, settings={}
        )
        rendered, reused = states(second)
        check(
            "S1 the second run reuses every chunk and re-encodes none",
            not rendered and reused == [0, 1, 2, 3],
            f"rendered={rendered} reused={reused}",
        )
        check(
            "S1 and the file is byte-identical",
            sha256(out) == digest_one,
            sha256(out)[:16],
        )

        # S2: new content in clip 2 -> only chunk 2 re-renders.
        colour_file(work / "green.mp4", "yellow")
        third = edl3_render.render(
            doc, out, workdir=run_dir, audio="drop", chunks=4, jobs=1, settings={}
        )
        rendered, reused = states(third)
        check(
            "S2 a changed source re-renders exactly its chunk (clip 1)",
            rendered == [1] and reused == [0, 2, 3],
            f"rendered={rendered} reused={reused}",
        )
        check(
            "S2 and the master changes",
            sha256(out) != digest_one,
            sha256(out)[:16],
        )

        # S3: a different span on clip 0 is a cut -> re-render, never a copy.
        cut = document(palette)
        cut.clips[0].src_in = 0.25
        cut.clips[0].src_out = 1.0
        cut.clips[0].dst_out = 0.75
        cut.clips[1].dst_in = 0.75
        cut.clips[2].dst_in = 1.75
        cut.clips[3].dst_in = 2.75
        for index in (1, 2, 3):
            cut.clips[index].dst_out = cut.clips[index].dst_in + 1.0
        fourth = edl3_render.render(
            cut, out, workdir=run_dir, audio="drop", chunks=3, jobs=1, settings={}
        )
        rendered, reused = states(fourth)
        check(
            "S3 a cut re-renders its chunk instead of reusing it",
            0 in rendered,
            f"rendered={rendered} reused={reused}",
        )

        scripts = sorted((run_dir / "chunks").rglob("*.txt"))
        copied = [
            path.name
            for path in scripts
            if "-c copy" in path.read_text(encoding="utf-8", errors="replace")
        ]
        check(
            "S3 no chunk script carries `-c copy`",
            not copied,
            copied or f"{len(scripts)} script(s) checked",
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
