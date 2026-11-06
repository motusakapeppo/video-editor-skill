"""Il tempo: macchina e attesa sono due numeri, non uno gonfiato.

    python tests/test_time_accounting.py

Il difetto che questo file fissa (2026-11-06, lavoro reale): il registro dei
giudizi sommava **tutte** le chiavi di ``report["timings"]``, quindi contava due
volte (``total`` e' gia' il totale) e ci infilava dentro le attese ai cancelli.
Misurato: 1457.7 s registrati contro 706.9 s dichiarati e 526.9 s di macchina
vera. Da quel numero dipendono la MAPE e la calibrazione della stima.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, feedback  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def test_clock_split() -> None:
    print("\n1. il cronometro separa macchina e attesa")
    clock = auto._Clock()
    clock.started = time.monotonic() - 20.0
    clock.add("render", 12.0)
    clock.waited(6.0)
    data = clock.as_dict()
    check(
        "S1 total resta il tempo di parete",
        abs(data["total"] - 20.0) <= 1.0,
        data["total"],
    )
    check("S1 waiting_total e' l'attesa dichiarata", data["waiting_total"] == 6.0, data["waiting_total"])
    check(
        "S1 machine_total = total - attesa",
        abs(data["machine_total"] - (data["total"] - 6.0)) <= 0.05,
        data["machine_total"],
    )
    check(
        "S1 macchina + attesa = totale",
        abs(data["machine_total"] + data["waiting_total"] - data["total"]) <= 1.0,
        (data["machine_total"], data["waiting_total"], data["total"]),
    )
    check(
        "S1 lo stadio non e' stato riscritto",
        data["render"] == 12.0,
        data["render"],
    )


def test_clock_waits_clamped() -> None:
    print("\n2. un'attesa piu' lunga del lavoro non produce numeri negativi")
    clock = auto._Clock()
    clock.started = time.monotonic() - 3.0
    clock.waited(999.0)
    data = clock.as_dict()
    check("S1 l'attesa non supera il totale", data["waiting_total"] <= data["total"] + 0.05, (data["waiting_total"], data["total"]))
    check("S1 la macchina non va sotto zero", data["machine_total"] >= 0.0, data["machine_total"])


def test_clock_wait_ignores_junk() -> None:
    print("\n3. un'attesa non numerica non sporca il conto")
    clock = auto._Clock()
    clock.started = time.monotonic() - 2.0
    for junk in (None, "180", True, -5, float("nan")):
        clock.waited(junk)
    data = clock.as_dict()
    check("S1 attese non numeriche valgono zero", data["waiting_total"] == 0.0, data["waiting_total"])


def test_context_uses_machine_time() -> None:
    print("\n4. il registro dei giudizi scrive il tempo macchina")
    report = {
        "timeline_sec": 15.0,
        "timings": {
            "render": 10.0,
            "questions": 180.0,
            "total": 706.938,
            "unaccounted": 3.204,
            "waiting_total": 441.125,
            "machine_total": 265.813,
        },
        "estimate": {"seconds": 20.525},
    }
    context = feedback.context_from_report(report)
    check(
        "S2 actual_seconds e' il tempo macchina",
        context["actual_seconds"] == 265.813,
        context["actual_seconds"],
    )
    check(
        "S2 l'attesa e' dichiarata a parte",
        context["waiting_seconds"] == 441.125,
        context["waiting_seconds"],
    )
    check(
        "S2 il numero NON e' la somma di tutto il blocco",
        context["actual_seconds"] != 1457.7,
        context["actual_seconds"],
    )
    check(
        "S2 la fonte del numero e' dichiarata",
        context["actual_source"] == "machine_total",
        context["actual_source"],
    )


def test_context_ignores_added_wait() -> None:
    print("\n5. aggiungere un'attesa non cambia il tempo della macchina")
    base = {
        "timings": {"render": 10.0, "total": 100.0, "waiting_total": 0.0, "machine_total": 100.0},
    }
    waited = {
        "timings": {"render": 10.0, "total": 160.0, "waiting_total": 60.0, "machine_total": 100.0},
    }
    first = feedback.context_from_report(base)
    second = feedback.context_from_report(waited)
    check(
        "S2 stesso lavoro, attese diverse, stesso actual",
        first["actual_seconds"] == second["actual_seconds"] == 100.0,
        (first["actual_seconds"], second["actual_seconds"]),
    )
    check(
        "S2 l'attesa si vede solo nel suo campo",
        second["waiting_seconds"] == 60.0 and first["waiting_seconds"] == 0.0,
        (first["waiting_seconds"], second["waiting_seconds"]),
    )


def test_old_report_still_reads() -> None:
    print("\n6. un report vecchio resta leggibile e dichiara la fonte")
    old = {"timings": {"render": 10.0, "total": 100.0}}
    context = feedback.context_from_report(old)
    check("S5 nessuna eccezione sul report vecchio", True)
    check("S5 actual ricavato dal totale dichiarato", context["actual_seconds"] == 100.0, context["actual_seconds"])
    check(
        "S5 la fonte dice che e' ricavato, non misurato",
        context["actual_source"] == "total_minus_waiting",
        context["actual_source"],
    )
    legacy = {"timings": {"render": 10.0, "audio": 2.0}}
    legacy_context = feedback.context_from_report(legacy)
    check(
        "S5 senza totale si ricade sulla somma degli stadi",
        legacy_context["actual_seconds"] == 12.0 and legacy_context["actual_source"] == "stage_sum",
        (legacy_context["actual_seconds"], legacy_context["actual_source"]),
    )
    empty = feedback.context_from_report({})
    check("S5 report vuoto: nessun numero inventato", empty["actual_seconds"] is None, empty["actual_seconds"])


def test_mape_uses_machine_time(tmp_root: Path) -> None:
    print("\n7. la MAPE si calcola sul tempo macchina, non sul gonfiato")
    root = tmp_root / "mape-root"
    root.mkdir(parents=True, exist_ok=True)
    feedback.append(
        {
            "job_id": "job-real",
            "prompt": "un lavoro vero",
            "rating": 2,
            "note": "",
            "context": feedback.context_from_report(
                {
                    "timings": {
                        "render": 10.0,
                        "questions": 180.0,
                        "total": 706.938,
                        "waiting_total": 441.125,
                        "machine_total": 265.813,
                    },
                    "estimate": {"seconds": 20.525},
                }
            ),
        },
        root=root,
    )
    data = feedback.report(root=root)
    mape = data.get("mape") or {}
    expected = round(abs(20.525 - 265.813) / 265.813 * 100.0, 1)
    check(
        "S3 MAPE = |stima - macchina| / macchina",
        mape.get("n") == 1 and abs(float(mape.get("mean_percent") or 0) - expected) <= 0.2,
        mape.get("mean_percent"),
    )
    check(
        "S3 il numero non e' quello del tempo gonfiato",
        abs(float(mape.get("mean_percent") or 0) - 98.5) > 3.0,
        mape.get("mean_percent"),
    )


def main() -> int:
    test_clock_split()
    test_clock_waits_clamped()
    test_clock_wait_ignores_junk()
    test_context_uses_machine_time()
    test_context_ignores_added_wait()
    test_old_report_still_reads()
    with tempfile.TemporaryDirectory(prefix="vx-time-") as tmp:
        test_mape_uses_machine_time(Path(tmp))
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
