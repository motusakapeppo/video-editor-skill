"""Gate 5 dinamico su tutto il corpus: le due implementazioni devono combaciare.

Per ogni clip e per ogni nodo cromatico si prendono due istanti, si applica il nodo
con FFmpeg e con Pillow sullo STESSO frame, e si misura lo scarto di luma. Il nodo
non contiene coordinate, quindi il risultato deve essere indipendente da risoluzione
e framerate: se non lo fosse, il test lo mostrerebbe clip per clip.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import contract, gates, grade, motion  # noqa: E402

CORPUS = ROOT / "tests" / "stock_corpus"
WORK = ROOT / "tests" / "_out" / "color"
TOL = 0.025

NODES = {
    "duotone motus": {"type": "duotone", "shadows": "#000000", "highlights": "#ff3333"},
    "caldo pellicola": {
        "type": "duotone",
        "shadows": "#2a1a10",
        "highlights": "#ffe8c8",
    },
    "nessuno": {"type": "none"},
}

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:34} {detail}")


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    clips = [(p.stem, p) for p in sorted(CORPUS.glob("*.mp4"))]
    clips += [(f"real_{p.stem}", p) for p in sorted((CORPUS / "real").glob("*.webm"))]

    for name, path in clips:
        frames, dur, fps = motion.expected_frames(path)
        pr = path
        print(f"  {name}: {frames} frame @{fps:.2f} {dur:.2f}s")
        for node_name, spec in NODES.items():
            node = grade.parse(spec)
            deltas = []
            for at in (dur * 0.2, dur * 0.7):
                r = gates.calibrate(
                    node,
                    pr,
                    at=at,
                    tolerance=TOL,
                    workdir=WORK / name / node.type if False else WORK / name,
                )
                worst = next(c.value for c in r.checks if c.code == "max_dl")
                deltas.append(worst)
            check(f"{node_name}", max(deltas) <= TOL, f"max |dL| {max(deltas):.4f}")
        print()

    print(f"{PASS} PASS / {FAIL} FAIL   (tolleranza |dL| <= {TOL})")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
