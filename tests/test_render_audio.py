"""Il suono segue il piano: fade ai giunti, compressione, denoise, stabilita'.

Un trattamento dichiarato deve comparire nel grafo. Questo test fissa il grafo
audio del concat principale (non quello di build_trim_graph, che lo stage 01
chiama con with_audio=False) e la catena di stabilizzazione.

python tests/test_render_audio.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import contract, render  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def _media() -> dict:
    return {
        "path": str(ROOT / "tests" / "stock_corpus" / "bright_45.mp4"),
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "has_audio": True,
        "duration": 10.0,
    }


def _cuts() -> list[dict]:
    return [
        {"in": 0.0, "out": 2.0},
        {"in": 3.0, "out": 5.0},
        {"in": 6.0, "out": 8.0},
    ]


def _plan(treatments: list[dict]) -> contract.EditPlan:
    return contract.EditPlan(
        output={"aspect": "9:16", "loudness": "social"},
        cuts=_cuts(),
        render={"precision": "proxy"},
        treatments=treatments,
    )


def _audio_segment(graph: str, index: int) -> str:
    match = re.search(r"\[0:a\]atrim=[^\[]*\[a%d\]" % index, graph)
    return match.group(0) if match else ""


def _atrim_spans(graph: str) -> list[tuple[float, float]]:
    spans = []
    for start, end in re.findall(r"\[0:a\]atrim=start=([0-9.]+):end=([0-9.]+)", graph):
        spans.append((float(start), float(end)))
    return spans


def main() -> int:
    media = _media()
    fade = render.FADE_MS / 1000.0

    plan = _plan(
        [
            {
                "name": "denoise",
                "enabled": True,
                "params": {"fallback": "highpass=f=80"},
            },
            {
                "name": "compress",
                "enabled": True,
                "params": {
                    "ratio": 2.0,
                    "threshold_db": -18.0,
                    "attack_ms": 10,
                    "release_ms": 250,
                },
            },
            {
                "name": "stabilize",
                "enabled": True,
                "params": {"shakiness": 8, "accuracy": 9, "smoothing": 10, "zoom": 0},
            },
        ]
    )
    graph = render.build_filtergraph(plan, media)

    subs_graph = render.build_filtergraph(plan, media, subs_path="subs.ass")
    check(
        "il grafo di render passa fontsdir a libass",
        "fontsdir=" in subs_graph and "assets" in subs_graph,
        subs_graph[subs_graph.find("subtitles=") :][:90],
    )

    check("tre segmenti nel concat", "concat=n=3:v=1:a=1" in graph)
    check(
        "esattamente 4 afade ai giunti",
        graph.count("afade=") == 4,
        str(graph.count("afade=")),
    )
    check("due fade in", graph.count("afade=t=in") == 2)
    check("due fade out", graph.count("afade=t=out") == 2)

    first, middle, last = (_audio_segment(graph, i) for i in range(3))
    check("nessun fade in sul primo segmento", "afade=t=in" not in first, first[-40:])
    check("nessun fade out sull'ultimo segmento", "afade=t=out" not in last, last[-40:])
    check(
        "il segmento interno ha fade in e out",
        "afade=t=in" in middle and "afade=t=out" in middle,
    )
    check(
        "il fade out parte a durata - fade",
        f"afade=t=out:st={2.0 - fade:.6f}:d={fade:.3f}" in first,
        f"st={2.0 - fade:.6f}",
    )
    check("nessun acrossfade", "acrossfade" not in graph)

    spans = _atrim_spans(graph)
    total = sum(end - start for start, end in spans)
    check("tre atrim", len(spans) == 3, str(len(spans)))
    check("durata totale invariata", abs(total - 6.0) < 1e-9, f"{total:.6f}")
    check(
        "expected_frames invariato",
        contract.expected_frames(_cuts(), 30.0) == 180,
        str(contract.expected_frames(_cuts(), 30.0)),
    )

    check("acompressor presente", "acompressor=" in graph)
    check(
        "acompressor converte i dB in ampiezza lineare",
        "threshold=0.125893" in graph and "makeup=1.258925" in graph,
        graph[graph.find("acompressor=") :][:96],
    )
    check(
        "acompressor legge i nomi di plan.py",
        "ratio=2.00" in graph and "release=250.0" in graph,
    )
    check("highpass dal fallback", "highpass=f=80" in graph)
    check("afftdn non emesso quando c'e' il fallback", "afftdn" not in graph)

    detect = render._detect_graph(plan)
    check("shakiness=8 nel detect", "shakiness=8" in detect, detect)
    check("accuracy=9 nel detect", "accuracy=9" in detect)
    check(
        "vidstabtransform legge smoothing/zoom",
        "vidstabtransform=input=transforms.trf:smoothing=10:zoom=0" in graph,
    )

    numeric = _plan(
        [
            {
                "name": "denoise",
                "enabled": True,
                "params": {"noise_floor": -25.0, "fallback": "highpass=f=80"},
            }
        ]
    )
    numeric_graph = render.build_filtergraph(numeric, media)
    check("noise_floor numerico -> afftdn", "afftdn=nf=-25.0" in numeric_graph)
    check("fallback ignorato col noise_floor", "highpass" not in numeric_graph)
    check("compress spento -> nessun acompressor", "acompressor" not in numeric_graph)

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
