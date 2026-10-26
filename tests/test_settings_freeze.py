"""THRESHOLDS is read-only; settings owns the only write path and never edits the default."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import analyze, asset_triage, contract, qc, settings  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:58} {detail}")


def main() -> int:
    settings.reset()
    try:
        contract.THRESHOLDS["sub_max_cps"] = 1
        check("THRESHOLDS e' read-only", False, "assignment succeeded")
    except TypeError:
        check("THRESHOLDS e' read-only", True, "TypeError")

    check(
        "default intatto a 20",
        contract.DEFAULT_THRESHOLDS["sub_max_cps"] == 20,
        contract.DEFAULT_THRESHOLDS["sub_max_cps"],
    )
    check(
        "analyze vede lo stesso oggetto",
        analyze.THRESHOLDS is contract.THRESHOLDS,
    )

    settings.apply(settings.build(overrides=["thresholds.sub_max_cps=18"]))
    check(
        "override applicato alla tabella viva",
        contract.THRESHOLDS["sub_max_cps"] == 18,
        contract.THRESHOLDS["sub_max_cps"],
    )
    check(
        "default non toccato dall'override",
        contract.DEFAULT_THRESHOLDS["sub_max_cps"] == 20,
        contract.DEFAULT_THRESHOLDS["sub_max_cps"],
    )
    check(
        "qc si riallinea all'override",
        qc.LIMITS["cue_cps_max"] == 18,
        qc.LIMITS["cue_cps_max"],
    )

    settings.apply(settings.build(overrides=["thresholds.triage_max_upscale=2.0"]))
    check(
        "override raggiunge la soglia effettiva del triage",
        contract.THRESHOLDS["triage_max_upscale"] == 2.0
        and asset_triage.triage_assets(
            [
                asset_triage.AssetInfo(
                    asset_id="a.mp4",
                    kind="video",
                    path="a.mp4",
                    width=100,
                    height=100,
                    duration_sec=5.0,
                )
            ],
            (180, 180),
            measure=lambda asset: {},
        ).thresholds["triage_max_upscale"]
        == 2.0,
        contract.THRESHOLDS["triage_max_upscale"],
    )

    settings.reset()
    check(
        "reset riporta la tabella viva a 20",
        contract.THRESHOLDS["sub_max_cps"] == 20,
        contract.THRESHOLDS["sub_max_cps"],
    )
    check(
        "reset riallinea qc",
        qc.LIMITS["cue_cps_max"] == 20,
        qc.LIMITS["cue_cps_max"],
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
