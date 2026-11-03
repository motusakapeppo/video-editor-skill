"""Il profilo di ``vx auto``: la tabella dei tempi e il motore che la produce.

Due cose si provano qui, entrambe senza render:

* ``_Clock`` somma per stadio e dichiara quello che nessuno stadio ha
  rivendicato, cosi' un buco nel profilo si vede invece di sparire nella somma;
* ``_measure_all`` restituisce un risultato per job, nell'ordine dei job, e
  trasforma un errore in un valore (il ponte lo dichiara, non muore).

Il limite dei processi e' ``cpu - 2``: non e' un dettaglio, e' la regola che
impedisce all'analisi di mangiarsi la macchina che il render deve usare.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def test_clock() -> None:
    print("\n-- _Clock")
    clock = auto._Clock()
    for _ in range(2):
        with clock.time("render"):
            time.sleep(0.05)
    profile = clock.as_dict()
    check("due misure sullo stesso stadio si sommano", profile["render"] >= 0.05, profile["render"])
    check("uno stadio mai toccato e' zero, non assente", profile["proxy"] == 0.0)
    check("il totale e' il muro del processo", profile["total"] >= profile["render"], profile["total"])
    check(
        "unaccounted copre quello che nessuno ha rivendicato",
        profile["unaccounted"] >= 0.0
        and abs(profile["total"] - profile["render"] - profile["qc"] - profile["unaccounted"]) < 0.02,
        profile["unaccounted"],
    )
    check(
        "ogni stadio dichiarato compare nella tabella",
        set(auto.TIMING_STAGES) <= set(profile),
        len(profile),
    )


def test_workers() -> None:
    print("\n-- budget dei worker: core, memoria, altri lavori")
    cpus = os.cpu_count() or 2
    budget = auto._measure_workers(1000)
    check(
        "mai piu' di min(6, cpu-2)",
        budget["workers"] == min(6, max(1, cpus - 2)),
        budget["workers"],
    )
    check("mai piu' job che misure", auto._measure_workers(2)["workers"] <= 2)
    check("almeno un worker", auto._measure_workers(1)["workers"] >= 1)

    poor = auto.resources.worker_budget(6, ram_free=900.0, concurrent=0)
    check("con poca RAM i worker scendono", poor["workers"] == 1, poor["workers"])
    check("e il motivo e' scritto", any("ram" in reason for reason in poor["reasons"]), poor["reasons"])
    rich = auto.resources.worker_budget(6, ram_free=64000.0, concurrent=0)
    check("con RAM abbondante non si taglia per memoria", rich["workers"] == min(6, max(1, cpus - 2)), rich["workers"])
    busy = auto.resources.worker_budget(6, ram_free=64000.0, concurrent=1)
    check(
        "un altro lavoro pesante dimezza i worker",
        busy["workers"] == max(1, rich["workers"] // 2),
        busy["workers"],
    )
    check("e lo dichiara", busy["concurrent_jobs"] == 1 and busy["reasons"], busy["reasons"])


def test_resources() -> None:
    print("\n-- la macchina: RAM, GPU, priorita', registro dei lavori")
    free = auto.resources.ram_free_mb()
    check("la RAM libera e' un numero o None, mai zero inventato", free is None or free > 0, free)
    gpu = auto.resources.gpu()
    check("la GPU e' dichiarata con la sua fonte", "available" in gpu and "source" in gpu, gpu.get("source"))
    check(
        "senza GPU il verdetto e' esplicito, non silenzioso",
        gpu["available"] is True or gpu.get("name") is None,
        gpu.get("name"),
    )
    check(
        "i thread ffmpeg sono espliciti solo se dichiarati",
        auto.resources.ffmpeg_threads() is None
        or isinstance(auto.resources.ffmpeg_threads(), int),
        auto.resources.ffmpeg_threads(),
    )
    with auto.resources.register_job("analysis", "qui"):
        others = [item for item in auto.resources.active_jobs() if str(item.get("kind")) == "analysis"]
        check("un lavoro registrato non conta se stesso", all(item["pid"] != os.getpid() for item in others), len(others))
        check("il registro nomina il lavoro in corso", auto.resources.heavy_jobs() >= 0)
    after = [item for item in auto.resources.active_jobs() if item["pid"] == os.getpid()]
    check("alla fine il lavoro esce dal registro", after == [], after)


def test_measure_all_serial() -> None:
    print("\n-- _measure_all in fila: ordine e errori come valori")
    seen = []

    def fake(job):
        seen.append(job)
        time.sleep(0.03)
        if job[0] == "faces":
            return {"kind": job[0], "error": "no opencv", "seconds": 0.1}
        return {"kind": job[0], "value": f"{job[1]}:{job[2]}", "seconds": 0.1}

    original = auto._measure_task
    auto._measure_task = fake
    try:
        jobs = [("audio", "a.mov", 1), ("analysis", "a.mov", 1), ("faces", "a.mov", 1)]
        clock = auto._Clock()
        results = auto._measure_all(jobs, parallel=False, clock=clock)
    finally:
        auto._measure_task = original
    check("un risultato per job, nello stesso ordine", [item["kind"] for item in results] == ["audio", "analysis", "faces"])
    check("un errore e' un valore, non un'eccezione", results[2].get("error") == "no opencv")
    check("il valore misura il job giusto", results[0]["value"] == "a.mov:1")
    check("il tempo finisce nello stadio giusto", clock.as_dict()["faces"] > 0, clock.as_dict()["faces"])
    check("nessun job lasciato indietro", len(seen) == 3)


def test_parallel_order() -> None:
    print("\n-- parallelo: l'ordine e le dichiarazioni attraversano il processo")
    # Percorsi che non esistono: ogni misura fallisce, e il fallimento deve
    # tornare come valore, nello stesso ordine dei job. I processi figli
    # importano il modulo vero: qui non si finge niente.
    jobs = [
        ("audio", "nessuno-a.mov", {}),
        ("analysis", "nessuno-b.mov", {}),
        ("faces", "nessuno-c.mov", {}),
    ]
    clock = auto._Clock()
    results = auto._measure_all(jobs, parallel=True, clock=clock)
    check("un risultato per job", len(results) == 3, len(results))
    check("stesso ordine dei job", [item["kind"] for item in results] == [job[0] for job in jobs])
    check("ogni fallimento e' un valore", all("error" in item for item in results), [bool("error" in item) for item in results])
    check("il muro del blocco e' dichiarato", clock.as_dict()["measure_wall"] > 0, clock.as_dict()["measure_wall"])


def main() -> int:
    test_clock()
    test_workers()
    test_resources()
    test_measure_all_serial()
    test_parallel_order()
    print(f"\n{PASS} PASS, {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
