"""La policy dei giudizi, quando esiste, e' un contratto: le sue soglie decidono.

    python tests/test_policy_in_plan.py

Senza policy (version 0) non cambia niente: e' la condizione per non rompere chi
non ha mai votato. Da v1 in poi `cut_rate`, `effects_density` e `variety_min`
entrano nel piano e nella verifica.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def report(clips: float = 25.0, duration: float = 10.0, effects: float = 4.0) -> dict:
    return {
        "measured": {"clips": clips, "duration_sec": duration},
        "effects": {"count": effects},
    }


POLICY = {
    "version": 1,
    "provisional": True,
    "thresholds": {
        "cut_rate_min": 0.18,
        "cut_rate_max": 0.75,
        "effects_density_max": 0.35,
        "variety_min": 0.55,
    },
}


def test_no_policy_changes_nothing() -> None:
    print("\n1. senza policy non si applica niente")
    block = auto.policy_checks(report(), None)
    check("nessun check", block["checks"] == [], block["checks"])
    check("la nota lo dice", "nessuna policy" in block["note"], block["note"])
    check("version 0", block["version"] == 0, block["version"])
    block_zero = auto.policy_checks(report(), {"version": 0, "thresholds": POLICY["thresholds"]})
    check("una v0 con soglie resta inerte", block_zero["checks"] == [], block_zero["checks"])


def test_measured_numbers() -> None:
    print("\n2. le misure sono quelle della regola (tagli/secondo, effetti/inquadratura)")
    block = auto.policy_checks(report(clips=25, duration=10, effects=4), POLICY)
    check("cut_rate = 2.5", block["measured"]["cut_rate"] == 2.5, block["measured"])
    check(
        "effects_density = 0.16",
        block["measured"]["effects_density"] == 0.16,
        block["measured"],
    )


def test_out_of_bounds_blocks() -> None:
    print("\n3. fuori soglia: check fallito, con misura e soglia")
    block = auto.policy_checks(report(clips=25, duration=10, effects=4), POLICY)
    identifiers = {item["id"]: item for item in block["checks"]}
    check("cut_rate_max fallisce a 2.5 contro 0.75", identifiers["policy_cut_rate_max"]["passed"] is False, identifiers["policy_cut_rate_max"])
    check(
        "il perche' porta i due numeri",
        "2.50" in identifiers["policy_cut_rate_max"]["why"]
        and "0.75" in identifiers["policy_cut_rate_max"]["why"],
        identifiers["policy_cut_rate_max"]["why"],
    )
    check("il blocco si dichiara non ok", block["ok"] is False, block["ok"])


def test_within_bounds_passes() -> None:
    print("\n4. dentro le soglie: nessun blocco")
    block = auto.policy_checks(report(clips=6, duration=10, effects=1), POLICY)
    check(
        "cut_rate 0.6 e densita' 0.17 passano",
        block["ok"] is True and all(item["passed"] for item in block["checks"]),
        [(item["id"], item["passed"]) for item in block["checks"]],
    )


def test_boundary_is_inclusive() -> None:
    print("\n5. il confine e' incluso")
    block = auto.policy_checks(report(clips=7.5, duration=10, effects=2.625), POLICY)
    check(
        "cut_rate esattamente al massimo passa",
        {item["id"]: item["passed"] for item in block["checks"]}["policy_cut_rate_max"] is True,
        block["measured"],
    )


def test_missing_measure_does_not_invent() -> None:
    print("\n6. senza misura non si inventa un verdetto")
    block = auto.policy_checks({"measured": {}, "effects": {}}, POLICY)
    check("nessun check su durata zero", block["checks"] == [], block["checks"])
    check("le misure restano None", block["measured"]["cut_rate"] is None, block["measured"])


def test_provisional_is_carried() -> None:
    print("\n7. una policy provvisoria lo dice anche nel report")
    block = auto.policy_checks(report(), POLICY)
    check("provisional true", block["provisional"] is True, block["provisional"])
    check(
        "le soglie applicate sono nel blocco",
        block["thresholds"] == POLICY["thresholds"],
        block["thresholds"],
    )


def main() -> int:
    test_no_policy_changes_nothing()
    test_measured_numbers()
    test_out_of_bounds_blocks()
    test_within_bounds_passes()
    test_boundary_is_inclusive()
    test_missing_measure_does_not_invent()
    test_provisional_is_carried()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
