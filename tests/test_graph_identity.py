"""Il grafo senza visual deve restare identico, byte per byte.

Il cablaggio visivo inserisce filtri dentro ogni segmento. Se il percorso
``visual=None`` cambiasse anche di un carattere, il render approvato non
sarebbe piu' riproducibile e il Gate 2 potrebbe passare o fallire per caso.
Questo test fissa il grafo con un hash dorato e prova che un ``visual`` di soli
no-op produce lo stesso testo.

python tests/test_graph_identity.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import animplan, contract, gates, gradeplan, render  # noqa: E402

PASS = 0
FAIL = 0

# Grafo dorato: due tagli contigui 0..2.5..5.0, 4:5, proxy, media sintetico.
GOLDEN_SHA256 = "7e5ace15d4743e2208f64f25258cd4d86f386c46a2b5aae9f1fdd9b4530be75d"


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:52} {detail}")


def _media() -> dict:
    return {
        "path": str(ROOT / "tests" / "stock_corpus" / "bright_45.mp4"),
        "width": 1080,
        "height": 1350,
        "fps": 25.0,
        "has_audio": True,
        "duration_s": 10.0,
    }


def _plan() -> contract.EditPlan:
    return contract.EditPlan(
        output={"aspect": "4:5", "loudness": "social"},
        cuts=[{"in": 0.0, "out": 2.5}, {"in": 2.5, "out": 5.0}],
        render={"precision": "proxy"},
    )


def main() -> int:
    plan, media = _plan(), _media()
    base = render.build_filtergraph(plan, media)
    explicit = render.build_filtergraph(plan, media, visual=None)
    empty = render.build_filtergraph(plan, media, visual={})

    check("visual omesso == visual=None", base == explicit)
    check("visual={} e' un no-op", base == empty)
    check(
        "hash dorato del grafo",
        hashlib.sha256(base.encode("utf-8")).hexdigest() == GOLDEN_SHA256,
        hashlib.sha256(base.encode("utf-8")).hexdigest()[:16],
    )
    check("Gate 2 sul grafo senza visual", gates.graph(base).ok)

    node = gradeplan.decide({"luma_p50": 0.1})["node"]
    grade = gradeplan.chain_for_shot(node)
    shot = {"duration": 2.48, "width": 1080, "height": 1350, "fps": 25.0}
    anim = animplan.chain_for_shot({"name": "fade", "count": 1}, shot)
    check("il nodo produce un frammento", bool(grade), grade[:24])
    check("l'animazione produce un frammento", bool(anim), anim[:24])

    visual = {
        "grade_nodes": [node],
        "animations": [{"name": "fade", "count": 1}],
        "width": 1080,
        "height": 1350,
    }
    wired = render.build_filtergraph(plan, media, visual=visual)
    check("il percorso visuale cambia il grafo", wired != base)
    check("il grade entra in ogni segmento", wired.count("curves=m=") == 2)
    check("l'animazione entra in ogni segmento", wired.count("fade=t=in") == 2)
    check("Gate 2 regge anche con il visual", gates.graph(wired).ok)
    check(
        "i tagli video restano due",
        wired.count("concat=n=2:v=1:a=1") == 1 and "[v2]" not in wired,
    )
    check("nessun frammento vuoto lascia virgole doppie", ",," not in wired)

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
