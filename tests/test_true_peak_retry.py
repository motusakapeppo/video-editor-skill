"""Il picco dopo la codifica: se sfora, si abbassa il limite e si rifà.

S1 - `retry_ceiling` abbassa il tetto dell'eccedenza misurata piu' 0,1 dB di
     sicurezza, e non lo alza mai quando il picco e' gia' sotto.
S2 - i due numeri del caso reale: picco -0,54 con tetto -0,9 -> nuovo tetto -1,36.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import render  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def main() -> int:
    check(
        "S1 an overshoot lowers the ceiling by the excess plus safety",
        render.retry_ceiling(-0.54, -0.9) == -1.36,
        render.retry_ceiling(-0.54, -0.9),
    )
    check(
        "S1 a big overshoot lowers it by the whole excess",
        render.retry_ceiling(0.3, -0.9) == -2.2,
        render.retry_ceiling(0.3, -0.9),
    )
    check(
        "S1 a peak already under the ceiling still gets the safety margin",
        render.retry_ceiling(-2.5, -0.9) == -1.0,
        render.retry_ceiling(-2.5, -0.9),
    )
    check(
        "S1 the retry never raises the ceiling",
        render.retry_ceiling(-0.1, -1.2) <= -1.2,
        render.retry_ceiling(-0.1, -1.2),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
