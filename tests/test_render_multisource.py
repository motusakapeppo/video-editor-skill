"""Render multi-source: dynamic [idx:v]/[idx:a] pads, multiple -i, real output."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import probe, render  # noqa: E402
from vx.edl_plan import (  # noqa: E402
    ClipSegment,
    EDLPlan,
    SourceRef,
    Track,
    Transition,
)

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


def _plan(*, resolution=(1080, 1920), duration=20.0) -> EDLPlan:
    sources = {
        "src_A": SourceRef("a.mp4", "a" * 64, 20.0),
        "src_B": SourceRef("b.mp4", "b" * 64, 20.0),
    }
    c1 = ClipSegment("c1", "src_A", 2.0, 12.0, 0.0, 10.0, 0, "main_talking_head")
    c2 = ClipSegment("c2", "src_B", 4.0, 14.0, 10.0, 20.0, 0, "main_talking_head")
    a1 = ClipSegment("a1", "src_A", 2.0, 12.0, 0.0, 10.0, 0, "dialogue")
    a2 = ClipSegment("a2", "src_B", 4.0, 14.0, 10.0, 20.0, 0, "dialogue")
    b1 = ClipSegment(
        "b1",
        "src_B",
        0.0,
        2.0,
        3.0,
        5.0,
        1,
        "broll_overlay",
        Transition("fade", 0.3),
        Transition("fade", 0.3),
    )
    return EDLPlan(
        fps=25.0,
        duration_sec=duration,
        aspect_ratio="9:16",
        resolution=resolution,
        sources=sources,
        video_tracks=(Track(0, (c1, c2), "main"), Track(1, (b1,), "broll")),
        audio_tracks=(Track(0, (a1, a2), "dialogue"),),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _clip(path: Path, color: str) -> bool:
    proc = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=320x180:r=25:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(path),
        ],
        capture_output=True,
    )
    return proc.returncode == 0 and path.is_file()


def main() -> int:
    plan = _plan()
    errors = plan.validate()
    check("fixture valida", errors == [], errors)

    source_to_index = {"src_A": 0, "src_B": 1}
    graph = render.build_edl_filtergraph(plan, source_to_index)
    check(
        "clip A usa [0:v]",
        "[0:v]trim=start=2.000000:end=12.000000" in graph,
        graph[:120],
    )
    check(
        "clip B usa [1:v]",
        "[1:v]trim=start=4.000000:end=14.000000" in graph,
    )
    check(
        "audio A usa [0:a]",
        "[0:a]atrim=start=2.000000:end=12.000000" in graph,
    )
    check(
        "audio B usa [1:a]",
        "[1:a]atrim=start=4.000000:end=14.000000" in graph,
    )
    check("concat A/V unico", "concat=n=2:v=1:a=1" in graph)
    check(
        "B-roll dallo stesso indice 1",
        "[1:v]trim=start=0.000000:end=2.000000" in graph,
    )
    check(
        "overlay alla dst_in",
        "overlay=enable='between(t,3.000000,5.000000)'" in graph,
    )
    check("[vout] presente", "[vout]" in graph)
    check("[aout] presente", "[aout]" in graph)
    check(
        "nessun pad incrociato",
        "[0:v]trim=start=4.000000" not in graph,
    )

    args = render.build_edl_command(
        plan,
        {"src_A": "A.mp4", "src_B": "B.mp4"},
        "out.mp4",
        graph_path="g.txt",
    )
    check("due -i", args.count("-i") == 2, args.count("-i"))
    first = args.index("-i")
    second = args.index("-i", first + 1)
    check("primo input A", args[first + 1] == "A.mp4", args[first + 1])
    check("secondo input B", args[second + 1] == "B.mp4", args[second + 1])
    check("graph via script", "-filter_complex_script" in args and "g.txt" in args)
    check("mappa video", "[vout]" in args)
    check("mappa audio", "[aout]" in args)

    with tempfile.TemporaryDirectory(prefix="vx-edl-render-") as raw:
        tmp = Path(raw)
        a, b = tmp / "a.mp4", tmp / "b.mp4"
        if not _clip(a, "blue") or not _clip(b, "red"):
            skip("render reale", "ffmpeg non disponibile")
        else:
            sources = {
                "src_A": SourceRef(str(a), _sha256(a), 2.0),
                "src_B": SourceRef(str(b), _sha256(b), 2.0),
            }
            c1 = ClipSegment("c1", "src_A", 0.0, 1.0, 0.0, 1.0, 0, "main_talking_head")
            c2 = ClipSegment("c2", "src_B", 1.0, 2.0, 1.0, 2.0, 0, "main_talking_head")
            a1 = ClipSegment("a1", "src_A", 0.0, 1.0, 0.0, 1.0, 0, "dialogue")
            a2 = ClipSegment("a2", "src_B", 1.0, 2.0, 1.0, 2.0, 0, "dialogue")
            b1 = ClipSegment(
                "b1",
                "src_B",
                0.0,
                0.5,
                0.5,
                1.0,
                1,
                "broll_overlay",
                Transition("fade", 0.3),
                Transition("fade", 0.3),
            )
            real = EDLPlan(
                fps=25.0,
                duration_sec=2.0,
                aspect_ratio="9:16",
                resolution=(320, 180),
                sources=sources,
                video_tracks=(Track(0, (c1, c2), "main"), Track(1, (b1,), "broll")),
                audio_tracks=(Track(0, (a1, a2), "dialogue"),),
            )
            check("fixture reale valida", real.validate() == [], real.validate())
            out = tmp / "out.mp4"
            try:
                render.render_edl(
                    real,
                    {"src_A": str(a), "src_B": str(b)},
                    out,
                    workdir=tmp / "work",
                    crf=30,
                    preset="ultrafast",
                )
            except Exception as exc:  # noqa: BLE001
                check("render reale", False, f"{type(exc).__name__}: {exc}")
            else:
                info = probe.probe(str(out))
                check("render reale: file", out.is_file() and out.stat().st_size > 0)
                check(
                    "render reale: geometria",
                    (info.width, info.height) == (320, 180),
                    (info.width, info.height),
                )
                check(
                    "render reale: durata",
                    abs(info.duration - 2.0) <= 0.15,
                    info.duration,
                )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
