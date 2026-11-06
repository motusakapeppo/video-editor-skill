"""Lo scarto di durata da silenzi tolti: tollerato entro 20% (max 3 s).

    python tests/test_silence_allowance.py

Difetto del 2026-11-06: un reel da 15 s usciva a 12.508 s perche' aveva tolto i
silenzi, e veniva bocciato come `NON-PUBBLICABILE` con tolleranza un frame. La
regola ora e' una sola, in `verify.silence_allowance`, usata dal QC e da `reel`.

Il secondo giro ha scoperto che nel `auto_report` il riepilogo del ponte porta
`cuts` come **numero**, non come lista: la regola deve leggere entrambe le forme.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import verify  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def main() -> int:
    numeric = {"bridge": {"cuts": 2}}
    listed = {"bridge": {"cuts": [{"reason": "dead_air"}, {"reason": "other"}]}}
    clean = {"bridge": {"cuts": 0}}

    allowance, note = verify.silence_allowance(numeric, 15.0)
    check("S1 riepilogo numerico: 2 tagli contano", allowance == 3.0, (allowance, note))
    check("S1 la frase porta i secondi e i tagli", "3.00s" in note and "2 tagli" in note, note)

    listed_allowance, _ = verify.silence_allowance(listed, 15.0)
    check("S2 lista di tagli: vale lo stesso", listed_allowance == 3.0, listed_allowance)

    zero, _ = verify.silence_allowance(clean, 15.0)
    check("S3 nessun silenzio tolto: nessuna agevolazione", zero == 0.0, zero)

    capped, _ = verify.silence_allowance(numeric, 60.0)
    check("S4 richiesta lunga: il tetto e' 3 s, non il 20%", capped == 3.0, capped)

    small, _ = verify.silence_allowance(numeric, 5.0)
    check("S5 richiesta corta: il 20% scende sotto i 3 s", small == 1.0, small)

    empty, _ = verify.silence_allowance({}, 15.0)
    check("S6 report senza ponte: nessuna agevolazione", empty == 0.0, empty)
    broken, _ = verify.silence_allowance({"bridge": {"cuts": "due"}}, 15.0)
    check("S7 un valore assurdo non inventa secondi", broken == 0.0, broken)

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
