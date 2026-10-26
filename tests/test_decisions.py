"""Test del perimetro decisionale: quando la skill deduce e quando alza la mano.

Nessun modello linguistico qui: regole deterministiche, cosi' il comportamento e'
riproducibile e una regressione si vede subito. Le asserzioni sono sul contratto,
non sulle parole.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import decide  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:42} {detail}")


def codes(decision) -> list[str]:
    return [b.code for b in decision.blockers]


def main() -> int:
    print("richiesta ambigua: reel vintage con transizioni dinamiche")
    d = decide.interpret(
        "un reel con stile vintage e transizioni dinamiche", assets=["Antonio", "Inter"]
    )
    check("contratto NON chiuso", not d.ok)
    check("blocca vintage", "vague_vintage" in codes(d), str(codes(d)))
    check("blocca dinamiche (plurale)", "vague_dinamic" in codes(d), str(codes(d)))
    check(
        "deduce 9:16 da 'reel'",
        d.deduced.get("aspect") == "9:16",
        str(d.deduced.get("aspect")),
    )
    check(
        "deduce la sovrapposizione",
        (d.deduced.get("transitions") or {}).get("model") == "overlap",
    )
    check("ogni blocco ha opzioni", all(b.options for b in d.blockers))

    print("\nrichiesta chiusa: vlog con ombra gaussiana e font dichiarato")
    c = decide.interpret(
        "un vlog con ombra gaussiana, transizioni, font Antonio",
        assets=["Antonio", "Inter"],
    )
    check("contratto chiuso", c.ok, str(codes(c)))
    check("deduce 16:9 da 'vlog'", c.deduced.get("aspect") == "16:9")
    check(
        "engine scelto senza chiedere",
        c.deduced.get("engine") == "pillow",
        str(c.deduced.get("engine")),
    )
    check(
        "grade neutro di default",
        c.contract.get("grade") == {"type": "none"},
        str(c.contract.get("grade")),
    )

    print("\nasset non in registro")
    a = decide.interpret("reel con font BebasNeue e placca", assets=["Antonio"])
    check("blocca il font mancante", "asset_missing" in codes(a), str(codes(a)))
    check(
        "propone un fallback tracciato",
        any("sostituzione" in o for b in a.blockers for o in b.options),
    )

    print("\nrichiesta senza formato")
    f = decide.interpret("montaggio con duotone")
    check("blocca il formato mancante", "format_missing" in codes(f), str(codes(f)))

    print("\ntransizioni senza handle")
    h = decide.interpret("reel con transizioni", has_handles=False)
    check(
        "blocca la sovrapposizione impossibile", "no_handles" in codes(h), str(codes(h))
    )

    print("\ncapacita' che impone il motore")
    e = decide.interpret("reel con backdrop-filter")
    check(
        "CSS impone chromium",
        e.deduced.get("engine") == "chromium",
        str(e.deduced.get("engine")),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
