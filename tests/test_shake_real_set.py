"""The real shake set: five human judgments, and a metric that cannot use them.

S1 - the set is real material: five excerpts, each with its measured jitter and the
     human judgment, no invented values.
S2 - the human order and the metric order disagree on exactly the pair that matters:
     r2 (a pan) scores higher than everything except r4 (a real shake), and the ratio
     between them is under 3. If this ever passes with a ratio above 3, the metric
     changed: the threshold must then be re-derived from this set, not inherited.
S3 - the candidate metric (velocity residual) was measured and does not separate
     either: the ratio stays in the same band. It is recorded, not adopted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import registry  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def main() -> int:
    data = json.loads((ROOT / "tests" / "fixtures" / "corpus_shake_real.json").read_text(encoding="utf-8"))
    clips = data["clips"]

    check("S1 five real excerpts are recorded", len(clips) == 5, [c["id"] for c in clips])
    check(
        "S1 each carries its measured jitter and its human judgment",
        all(isinstance(c.get("jitter"), (int, float)) and c.get("judgment") for c in clips),
        [(c["id"], c["jitter"], c["judgment"]) for c in clips],
    )
    by_id = {c["id"]: c for c in clips}
    check(
        "S1 the stable excerpts are the lowest numbers, the shake is the highest",
        by_id["r3"]["jitter"] < by_id["r1"]["jitter"] < by_id["r4"]["jitter"]
        and by_id["r5"]["jitter"] < by_id["r1"]["jitter"],
        {c["id"]: c["jitter"] for c in clips},
    )

    ratio = by_id["r4"]["jitter"] / by_id["r2"]["jitter"]
    check(
        "S2 a pan and a shake stay within a factor of 3: the metric cannot separate them",
        ratio < 3.0,
        f"r4/r2 = {ratio:.2f}; se questo fallisce la metrica e' cambiata: rifare la soglia",
    )
    check(
        "S2 the human says the pan is not a tremor",
        by_id["r2"]["judgment"] == "pan",
        by_id["r2"].get("note"),
    )

    candidate = data.get("candidate") or {}
    values = candidate.get("values") or {}
    check(
        "S3 the candidate was measured on the same set",
        set(values) == {c["id"] for c in clips},
        values,
    )
    candidate_ratio = (
        float(values["r4"]) / float(values["r2"]) if values.get("r2") else 0.0
    )
    check(
        "S3 and it does not separate either: same band as the metric in use",
        candidate_ratio < 3.5,
        f"r4/r2 = {candidate_ratio:.2f}",
    )
    check(
        "S3 the threshold stays withdrawn while the metric is blind",
        registry.ladder_threshold("shake") is None,
        registry.ladder_threshold("shake"),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
