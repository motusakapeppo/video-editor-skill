"""La modalita' autonoma non chiede: registra i default e prosegue.

python tests/test_autonomy.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import plan  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:44} {detail}")


def main() -> int:
    report = {"source": {"width": 1080, "height": 1920, "duration": 12.0}}
    brief: dict = {}

    check(
        "brief incompleto chiede",
        isinstance(plan.needs_triage(brief, report), dict),
    )
    check(
        "autonomo non chiede",
        plan.needs_triage(brief, report, autonomous=True) is None,
    )

    defaults = plan.autonomous_defaults(report, brief)
    check(
        "formato dal materiale",
        defaults["target_format"]["value"] == "9:16",
        defaults["target_format"]["value"],
    )
    check("hook inazione", defaults["hook"]["value"] == "none")
    check("sottotitoli auto", defaults["subtitles"]["value"] == "auto")
    check(
        "ogni default ha provenienza",
        all(v["provenance"] == "declared:autonomous" for v in defaults.values()),
    )

    built = plan.build_plan(report, brief, defaults)
    check("il piano nasce senza risposte", built.output.get("aspect") == "9:16")

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
