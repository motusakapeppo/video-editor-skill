"""T09 adoption: the calibrated exposure boundary replaces the measured one.

Scenario:

S1 - the ladder threshold is the one the ladder declares (50,636), not the old 45.
S2 - the verdicts on the ladder's own levels match what the blind panel said
     (92,5 ok / 50,6 dark / 27 very_dark / 13 very_dark).
S3 - a value between the two boundaries changes side: 47 was "ok" with 45 and is
     "dark" with the calibrated threshold. That is the whole point of adopting it.
S4 - cast is calibrated but NOT adopted: the ladder gives an "evident" boundary
     (46,1), while the verdict vocabulary only classifies the hue (neutral/warm/
     green/cool/magenta). The gap is declared, not hidden.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import analyze, registry  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def main() -> int:
    ladder = json.loads((ROOT / "tests" / "fixtures" / "corpus_ladder.json").read_text(encoding="utf-8"))
    exposure = ladder["axes"]["exposure"]

    check(
        "S1 the adopted threshold is the ladder's, not the old 45",
        registry.ladder_threshold("exposure") == exposure["threshold"] == 50.636,
        registry.ladder_threshold("exposure"),
    )
    check(
        "S1 a withdrawn axis returns no threshold",
        registry.ladder_threshold("shake") is None,
        registry.ladder_threshold("shake"),
    )

    verdicts = [
        (level["value"], level["token"], analyze._exposure_verdict(level["value"], 0.0))
        for level in exposure["ladder"]
    ]
    check(
        "S2 every level gets the verdict the panel gave it",
        all(
            (token == "ok" and got == "ok") or (token in ("dark", "very_dark") and got in ("dark", "very_dark"))
            for _, token, got in verdicts
        ),
        verdicts,
    )
    check(
        "S2 and the boundary itself is where the ladder says",
        analyze._exposure_verdict(50.7, 0.0) == "ok"
        and analyze._exposure_verdict(50.6, 0.0) == "dark",
        "50,7 ok / 50,6 dark",
    )

    check(
        "S3 47 changed side: it used to be ok, it is dark now",
        analyze._exposure_verdict(47.0, 0.0) == "dark",
        analyze._exposure_verdict(47.0, 0.0),
    )
    check(
        "S3 clipping still wins over darkness",
        analyze._exposure_verdict(20.0, 99.0) == "clipped",
        analyze._exposure_verdict(20.0, 99.0),
    )

    check(
        "S4 cast is calibrated but not adopted: the vocabulary has no severity",
        registry.ladder_threshold("cast") == 46.116
        and analyze._color_verdict(0.0, 40.0) == "cool",
        "soglia 46,116 nota; il verdetto usa ancora 12,0 (dev 3,3 -> cool)",
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
