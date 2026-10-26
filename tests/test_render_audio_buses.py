"""Multitrack EDL audio: dialogue bus, music bus, per-clip fades/gain, sidechain ducking.

python tests/test_render_audio_buses.py
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

from vx import probe, render  # noqa: E402
from vx.edl_plan import (  # noqa: E402
    ClipSegment,
    DuckingSpec,
    EDLPlan,
    SourceRef,
    Track,
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _sources() -> dict:
    return {
        "src_A": SourceRef("a.mp4", "a" * 64, 2.0),
        "src_B": SourceRef("b.mp4", "b" * 64, 2.0),
        "src_M": SourceRef("m.mp4", "c" * 64, 2.0),
    }


def _video() -> tuple:
    c1 = ClipSegment("c1", "src_A", 0.0, 1.0, 0.0, 1.0, 0, "main_talking_head")
    c2 = ClipSegment("c2", "src_B", 0.0, 1.0, 1.0, 2.0, 0, "main_talking_head")
    return (Track(0, (c1, c2), "main"),)


def _dialogue() -> tuple:
    a1 = ClipSegment("a1", "src_A", 0.0, 1.0, 0.0, 1.0, 0, "dialogue")
    a2 = ClipSegment("a2", "src_B", 0.0, 1.0, 1.0, 2.0, 0, "dialogue", gain_db=-6.0)
    return (Track(0, (a1, a2), "dialogue"),)


def _music() -> tuple:
    m1 = ClipSegment("m1", "src_M", 0.0, 1.0, 0.0, 1.0, 0, "music", gain_db=-8.0)
    return (Track(1, (m1,), "music"),)


def _plan(*, audio_tracks: tuple, ducking=None) -> EDLPlan:
    return EDLPlan(
        fps=25.0,
        duration_sec=2.0,
        aspect_ratio="16:9",
        resolution=(320, 180),
        sources=_sources(),
        video_tracks=_video(),
        audio_tracks=audio_tracks,
        ducking=ducking,
    )


def _make(path: Path, color: str, duration: float) -> bool:
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
            f"color=c={color}:s=320x180:r=25:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
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
    mapping = {"src_A": 0, "src_B": 1, "src_M": 2}

    ducked = _plan(
        audio_tracks=_dialogue() + _music(),
        ducking=DuckingSpec(),
    )
    check("piano con ducking valido", ducked.validate() == [], ducked.validate())
    graph = render.build_edl_filtergraph(ducked, mapping)

    check(
        "quattro afade (2 per clip dialogo)",
        graph.count("afade") == 4,
        graph.count("afade"),
    )
    check("gain dialogo -6.000dB", "volume=-6.000dB" in graph)
    check("gain musica -8.000dB", "volume=-8.000dB" in graph)
    check("asplit=2 sul dialogo", "asplit=2" in graph)
    check("sidechaincompress presente", "sidechaincompress" in graph)
    check("threshold=0.050", "threshold=0.050" in graph)
    check("ratio=8.000", "ratio=8.000" in graph)
    check("makeup=1.000", "makeup=1.000" in graph)
    check(
        "due apad (un bus ciascuno)",
        graph.count("apad=whole_dur=") == 2,
        graph.count("apad=whole_dur="),
    )
    check(
        "aformat stereo su entrambi i bus",
        graph.count(
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        )
        == 2,
    )
    check(
        "amix finale a due ingressi",
        "amix=inputs=2:dropout_transition=0:normalize=0[aout]" in graph,
    )
    check(
        "musica senza fade", "afade" not in graph.split("[mum1]")[0].rsplit(";", 1)[-1]
    )

    args = render.build_edl_command(
        ducked,
        {"src_A": "A.mp4", "src_B": "B.mp4", "src_M": "M.mp4"},
        "out.mp4",
        graph_path="g.txt",
    )
    check("nessun -c copy nel taglio", "copy" not in args, args)

    plain = _plan(audio_tracks=_dialogue() + _music(), ducking=None)
    plain_graph = render.build_edl_filtergraph(plain, mapping)
    check(
        "senza ducking: amix a due bus",
        "[dialog][music]amix=inputs=2:dropout_transition=0:normalize=0[aout]"
        in plain_graph,
    )
    check("senza ducking: niente sidechain", "sidechaincompress" not in plain_graph)

    dialogue_only = _plan(audio_tracks=_dialogue(), ducking=None)
    dialogue_graph = render.build_edl_filtergraph(dialogue_only, mapping)
    check("solo dialogo: anull[aout]", "[dialog]anull[aout]" in dialogue_graph)
    check("solo dialogo: niente bus musica", "[music]" not in dialogue_graph)

    music_only = _plan(audio_tracks=_music(), ducking=None)
    music_graph = render.build_edl_filtergraph(music_only, mapping)
    check("solo musica: anull[aout]", "[music]anull[aout]" in music_graph)
    check("solo musica: niente bus dialogo", "[dialog]" not in music_graph)

    with tempfile.TemporaryDirectory(prefix="vx-buses-") as raw:
        tmp = Path(raw)
        a, m = tmp / "a.mp4", tmp / "m.mp4"
        if not (_make(a, "blue", 2.0) and _make(m, "green", 1.0)):
            skip("render reale con ducking", "ffmpeg non disponibile")
        else:
            sources = {
                "src_A": SourceRef(str(a), _sha256(a), 2.0),
                "src_M": SourceRef(str(m), _sha256(m), 1.0),
            }
            c1 = ClipSegment("c1", "src_A", 0.0, 2.0, 0.0, 2.0, 0, "main_talking_head")
            a1 = ClipSegment("a1", "src_A", 0.0, 2.0, 0.0, 2.0, 0, "dialogue")
            m1 = ClipSegment(
                "m1", "src_M", 0.0, 1.0, 0.0, 1.0, 0, "music", gain_db=-8.0
            )
            real = EDLPlan(
                fps=25.0,
                duration_sec=2.0,
                aspect_ratio="16:9",
                resolution=(320, 180),
                sources=sources,
                video_tracks=(Track(0, (c1,), "main"),),
                audio_tracks=(Track(0, (a1,), "dialogue"), Track(1, (m1,), "music")),
                ducking=DuckingSpec(),
            )
            check("fixture reale valida", real.validate() == [], real.validate())
            out = tmp / "out.mp4"
            work = tmp / "work"
            try:
                render.render_edl(
                    real,
                    {"src_A": str(a), "src_M": str(m)},
                    out,
                    workdir=work,
                    crf=30,
                    preset="ultrafast",
                )
            except Exception as exc:  # noqa: BLE001
                check("render reale con ducking", False, f"{type(exc).__name__}: {exc}")
            else:
                info = probe.probe(str(out))
                check("render reale: file", out.is_file() and out.stat().st_size > 0)
                check("render reale: audio", info.has_audio is True, info.has_audio)
                check(
                    "render reale: durata 2.0 (musica non tronca)",
                    abs(info.duration - 2.0) <= 0.2,
                    info.duration,
                )
                script = work / "edl_graph.txt"
                check(
                    "script eseguito su disco con sidechain",
                    script.is_file()
                    and "sidechaincompress" in script.read_text(encoding="utf-8"),
                )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
