"""Il ledger e' il contratto: questi test provano che il prover lo difende.

python tests/test_ledger.py
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import brief as brief_mod, prove  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:46} {detail}")


def main() -> int:
    valid = brief_mod._valid_brief()
    check(
        "brief valido -> []",
        prove.check_ledger(valid) == [],
        str(prove.check_ledger(valid)[:2]),
    )

    missing = copy.deepcopy(valid)
    del missing["choices"]["palette"]
    check(
        "asse mancante rifiutato",
        prove.check_ledger(missing) != [],
        str(prove.check_ledger(missing)[:1]),
    )

    sentence = copy.deepcopy(valid)
    sentence["choices"]["grade"]["inaction"] = (
        "confidenza 0.489 in [0.2, 0.8]: inazione, nessun nodo cromatico."
    )
    errors = prove.check_ledger(sentence)
    check(
        "inaction frase (non bool) rifiutata",
        any("inaction non booleano" in e for e in errors),
        str(errors[:1]),
    )

    real_effect = copy.deepcopy(valid)
    real_effect["choices"]["palette"]["applied"] = False
    real_effect["choices"]["palette"]["inaction"] = True
    real_effect["choices"]["palette"]["confidence"] = 0.5
    check(
        "applied false con effetto reale rifiutato",
        prove.check_ledger(real_effect) != [],
        str(prove.check_ledger(real_effect)[:1]),
    )

    uncertain = copy.deepcopy(valid)
    uncertain["choices"]["grade"]["confidence"] = 0.5
    uncertain["choices"]["grade"]["applied"] = True
    check(
        "confidence 0.5 con applied true rifiutata",
        prove.check_ledger(uncertain) != [],
        str(prove.check_ledger(uncertain)[:1]),
    )

    confident = copy.deepcopy(valid)
    confident["choices"]["grade"]["confidence"] = 0.9
    confident["choices"]["grade"]["applied"] = False
    confident["choices"]["grade"]["inaction"] = True
    check(
        "confidence 0.9 con applied false rifiutata",
        prove.check_ledger(confident) != [],
        str(prove.check_ledger(confident)[:1]),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
