"""Il tempo che la stima prezza e' quello della macchina, e i quattro stadi muti.

Due difetti misurati il 2026-11-06 su un lavoro vero
(``P_05-11-26/vx/job/auto_report.json``):

* la stima diceva **20.525 s** e la macchina ne ha impiegati **265.8**
  (706.9 di parete meno 441.1 di attese umane): il modello prezzava solo
  ``probe, ingest, audio, analysis, faces, render, qc``, mentre il costo stava
  in ``in_point`` 141.8 s, ``measure_wall`` 43.8 s e nelle parti di macchina di
  ``font`` (183.1 s, di cui 180.0 di cancello) e ``keyframes`` (81.7 s, di cui
  81.1 di cancello);
* ``record_timings`` scriveva ``seconds_total`` = ``timings["total"]``, che
  include le attese ai cancelli: la calibrazione imparava il tempo in cui la
  persona pensava.

Le due parti che questo file prova:

S3 -- la riga di storia porta ``seconds_machine`` e ``seconds_waiting`` accanto
a ``seconds_total``, con ``source`` che dice da dove viene la separazione, e
``timings_machine`` che la porta per stadio. Un report vecchio senza
``machine_total`` vale ``machine == total``, ``waiting == 0``, ``source=total``.

S4 -- ``plan_estimate`` prezza anche ``font``, ``in_point``, ``keyframes``,
``measure_wall``: costante dichiarata per secondo di materiale finche' non c'e'
storia, mediana misurata quando c'e'. ``calibrated`` resta ``false`` senza
storia e una nota elenca le parti da costante. La stima non legge mai i tempi
del lavoro che sta stimando: un mapping di ``timings`` viene rifiutato.

python tests/test_estimate_time.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import estimate  # noqa: E402

PASS = 0
FAIL = 0

#: Il lavoro vero del 2026-11-06: 15 s di materiale e di master, 1080x1920 a
#: 25 fps, nessun effetto. I numeri stanno qui perche' la prova non deve
#: dipendere dal quel percorso su questo disco.
REAL_MATERIAL_SEC = 15.0
REAL_MACHINE_MATERIAL_SEC = 32.56
REAL_RESOLUTION = (1080, 1920)
REAL_FPS = 25.0
REAL_MACHINE_SEC = 265.813
REAL_WAIT_SEC = 441.125
REAL_TOTAL_SEC = 706.938
REAL_ESTIMATE_BEFORE = 20.525
REAL_STAGES = {
    "probe": 0.953,
    "ingest": 10.875,
    "audio": 6.711,
    "analysis": 15.022,
    "faces": 22.109,
    "measure_wall": 43.843,
    "font": 183.078,
    "keyframes": 81.703,
    "render": 20.359,
    "qc": 1.141,
    "in_point": 141.812,
    "deliverables": 1.39,
}
REAL_GATES = {
    "triage": 180.016,
    "font": 180.0,
    "keyframes": 81.109,
}
REP_TRIAGE_WAIT = REAL_GATES["triage"]


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


class Clip:
    def __init__(self, index: int, dst_in: float, dst_out: float, src_in: float, src_out: float) -> None:
        self.clip_id = f"v{index}_0"
        self.source_id = f"s{index}"
        self.src_in = src_in
        self.src_out = src_out
        self.dst_in = dst_in
        self.dst_out = dst_out
        self.layer = 0
        self.effects = ()


class Track:
    def __init__(self, clips) -> None:
        self.clips = list(clips)


class Plan:
    """Un piano con master di ``seconds`` secondi e materiale dichiarato.

    ``material_sec`` serve al lavoro vero: 32.56 s di girato per 15 s di master.
    """

    def __init__(self, *, seconds: float = REAL_MATERIAL_SEC, material_sec: float = None) -> None:
        self.fps = REAL_FPS
        self.duration_sec = seconds
        self.resolution = REAL_RESOLUTION
        source = float(seconds if material_sec is None else material_sec)
        half = source / 2.0
        self.video_tracks = (
            Track(
                [
                    Clip(0, 0.0, seconds / 2.0, 0.0, half),
                    Clip(1, seconds / 2.0, seconds, 10.0, 10.0 + half),
                ]
            ),
        )


def real_report(*, new: bool) -> dict:
    """Il report del lavoro vero: con i campi nuovi (auto di oggi) o senza."""
    timings = dict(REAL_STAGES)
    timings["total"] = REAL_TOTAL_SEC
    timings["triage"] = 5.0
    timings["questions"] = REP_TRIAGE_WAIT
    if new:
        timings["waiting_total"] = REAL_WAIT_SEC
        timings["machine_total"] = REAL_MACHINE_SEC
    report = {
        "fps": REAL_FPS,
        "timeline_sec": REAL_MATERIAL_SEC,
        "estimate": {
            "material_sec": REAL_MATERIAL_SEC,
            "resolution": list(REAL_RESOLUTION),
            "preset": "medium",
            "x264_vs_hw": "x264",
            "effects": {"cost": 0.0},
        },
        "timings": timings,
    }
    if new:
        report["gates"] = {
            "total_waited_sec": REAL_WAIT_SEC,
            "items": {
                name: {"asked": True, "waited_sec": seconds}
                for name, seconds in REAL_GATES.items()
            },
        }
    return report


def test_s3_row_from_a_new_report() -> None:
    print("\n-- S3a: la riga scritta da un report nuovo separa macchina e attesa")
    with tempfile.TemporaryDirectory(prefix="vx_time_s3a_") as tmp:
        row = estimate.record_timings(tmp, real_report(new=True))
        check("la riga si scrive", isinstance(row, dict), row and sorted(row))
        check(
            "source dice machine_total",
            row.get("source") == "machine_total",
            row.get("source"),
        )
        check(
            "seconds_machine e' il tempo di macchina dichiarato",
            abs(float(row["seconds_machine"]) - REAL_MACHINE_SEC) < 0.001,
            row["seconds_machine"],
        )
        check(
            "seconds_machine + seconds_waiting = seconds_total (entro 0.01)",
            abs(
                float(row["seconds_machine"])
                + float(row["seconds_waiting"])
                - float(row["seconds_total"])
            )
            < 0.01,
            (
                row["seconds_machine"],
                row["seconds_waiting"],
                row["seconds_total"],
            ),
        )
        check(
            "seconds_total resta il totale di parete (compatibilita')",
            abs(float(row["seconds_total"]) - REAL_TOTAL_SEC) < 0.001,
            row["seconds_total"],
        )
        rows = estimate.load_timings(tmp)
        check(
            "la storia su disco porta gli stessi tre numeri",
            len(rows) == 1
            and abs(float(rows[0]["seconds_machine"]) - REAL_MACHINE_SEC) < 0.001
            and rows[0]["source"] == "machine_total",
            rows and (rows[0]["seconds_machine"], rows[0]["source"]),
        )
        machine = row.get("timings_machine") or {}
        check(
            "timings_machine toglie dal font l'attesa al cancello (183.078 -> 3.078)",
            abs(float(machine.get("font", -1)) - 3.078) < 0.002,
            machine.get("font"),
        )
        check(
            "e da keyframes (81.703 -> 0.594)",
            abs(float(machine.get("keyframes", -1)) - 0.594) < 0.002,
            machine.get("keyframes"),
        )
        check(
            "in_point resta tutto di macchina",
            abs(float(machine.get("in_point", -1)) - REAL_STAGES["in_point"]) < 0.002,
            machine.get("in_point"),
        )


def test_s3_row_from_an_old_report() -> None:
    print("\n-- S3b: un report vecchio non inventa la separazione che non ha")
    with tempfile.TemporaryDirectory(prefix="vx_time_s3b_") as tmp:
        row = estimate.record_timings(tmp, real_report(new=False))
        check(
            "seconds_machine e' il totale dichiarato",
            abs(float(row["seconds_machine"]) - REAL_TOTAL_SEC) < 0.001,
            row["seconds_machine"],
        )
        check("source dice total", row["source"] == "total", row["source"])
        check(
            "seconds_waiting e' zero, non indovinato",
            float(row["seconds_waiting"]) == 0.0,
            row["seconds_waiting"],
        )
        check(
            "i timings grezzi restano scritti come erano",
            abs(float(row["timings"]["font"]) - REAL_STAGES["font"]) < 0.001,
            row["timings"]["font"],
        )
        check(
            "senza timings non si scrive nessuna riga",
            estimate.record_timings(tmp, {"timeline_sec": 1.0}) is None,
            len(estimate.load_timings(tmp)),
        )


def test_s4_parts_and_totals() -> None:
    print("\n-- S4a: i quattro stadi muti entrano in parts, e la somma e' seconds")
    result = estimate.plan_estimate(Plan())
    parts = result["parts"]
    check(
        "parts contiene font, in_point, keyframes, measure_wall",
        {"font", "in_point", "keyframes", "measure_wall"} <= set(parts),
        sorted(parts),
    )
    check(
        "la somma di parts e' seconds (entro 0.01)",
        abs(sum(parts.values()) - result["seconds"]) < 0.01,
        (round(sum(parts.values()), 3), result["seconds"]),
    )
    check(
        "low e high si ricalcolano su tutte le parti",
        abs(result["low"] - result["seconds"] * 0.65) < 0.05
        and abs(result["high"] - result["seconds"] * 1.35) < 0.05,
        (result["low"], result["high"]),
    )
    check(
        "l'intervallo contiene la stima",
        result["low"] <= result["seconds"] <= result["high"],
        (result["low"], result["seconds"], result["high"]),
    )
    check(
        "i quattro stadi costano per secondo di materiale",
        abs(parts["in_point"] - 0.217 * REAL_MATERIAL_SEC) < 0.005
        and abs(parts["measure_wall"] - 0.407 * REAL_MATERIAL_SEC) < 0.005
        and abs(parts["font"] - 0.014 * REAL_MATERIAL_SEC) < 0.005
        and abs(parts["keyframes"] - 0.039 * REAL_MATERIAL_SEC) < 0.005,
        {stage: parts[stage] for stage in ("font", "in_point", "keyframes", "measure_wall")},
    )
    doubled = estimate.plan_estimate(Plan(seconds=2 * REAL_MATERIAL_SEC))
    check(
        "il doppio di materiale raddoppia in_point",
        abs(doubled["parts"]["in_point"] - 2 * parts["in_point"]) < 0.02,
        (parts["in_point"], doubled["parts"]["in_point"]),
    )


def test_s4_the_real_job() -> None:
    print("\n-- S4b: il lavoro vero — stima a macchina libera e banda con la corsa lenta")
    # Il lavoro del 4 novembre: 32.56 s di materiale 1080x1920 da telefono,
    # 265.8 s di macchina, e in_point 141.8 s. Quella corsa e' un **outlier**: e'
    # girata con la macchina occupata. La stima non deve tararsi su di lei (la
    # prima versione lo faceva e diceva 220 s su un lavoro da 25), ma la banda
    # deve contenerla.
    result = estimate.plan_estimate(Plan(seconds=REAL_MATERIAL_SEC, material_sec=REAL_MACHINE_MATERIAL_SEC))
    print(
        f"       stima a macchina libera {result['seconds']:.1f} s | banda "
        f"{result['low']:.1f}-{result['high']:.1f} s | corsa lenta {REAL_MACHINE_SEC:.1f} s"
    )
    check(
        "senza storia la stima sta nella banda della macchina libera",
        40.0 <= result["seconds"] <= 70.0,
        result["seconds"],
    )
    check(
        "la stima non e' tarata sulla corsa lenta",
        result["seconds"] < REAL_MACHINE_SEC / 2,
        (result["seconds"], REAL_MACHINE_SEC),
    )
    check(
        "le assunzioni dicono a cosa si riferisce il numero",
        any("macchina libera" in item for item in result.get("assumptions") or ()),
        result.get("assumptions"),
    )

    slow = {
        "material_sec": REAL_MACHINE_MATERIAL_SEC,
        "timeline_sec": REAL_MATERIAL_SEC,
        "width": 1080,
        "height": 1920,
        "fps": 25.0,
        "timings": {
            "in_point": 141.812,
            "measure_wall": 43.843,
            "keyframes": 81.703,
            "font": 183.078,
        },
        "timings_machine": {
            "in_point": 141.812,
            "measure_wall": 43.843,
            "keyframes": 0.594,
            "font": 3.078,
        },
    }
    with_slow = estimate.plan_estimate(
        Plan(seconds=REAL_MATERIAL_SEC, material_sec=REAL_MACHINE_MATERIAL_SEC), timings=[slow]
    )
    print(
        f"       con la corsa lenta come storia: stima {with_slow['seconds']:.1f} s, "
        f"alto {with_slow['high']:.1f} s"
    )
    check(
        "una sola misura non sposta la stima puntuale",
        abs(with_slow["seconds"] - result["seconds"]) < 5.0,
        (result["seconds"], with_slow["seconds"]),
    )
    check(
        "ma allarga il limite alto fino a coprire la corsa lenta",
        with_slow["high"] > result["high"] * 1.8,
        (result["high"], with_slow["high"]),
    )
    check(
        "e lo dichiara: una sola misura non basta",
        any("una sola misura" in item for item in with_slow["notes"]),
        [item for item in with_slow["notes"] if "una sola" in item],
    )


def test_s4_without_history() -> None:
    print("\n-- S4c: senza storia calibrated false e la nota dice cosa e' costante")
    result = estimate.plan_estimate(Plan())
    note = next(
        (item for item in result["notes"] if item.startswith("parti da costante")),
        "",
    )
    check("calibrated resta false", result["calibrated"] is False, result["calibrated"])
    check("la nota delle parti da costante c'e'", bool(note), note)
    check(
        "la nota elenca le quattro parti nuove",
        all(stage in note for stage in ("font", "in_point", "keyframes", "measure_wall")),
        note,
    )
    check(
        "constant_parts le contiene tutte, measured_parts e' vuoto",
        {"font", "in_point", "keyframes", "measure_wall"} <= set(result["constant_parts"])
        and result["measured_parts"] == [],
        (result["constant_parts"], result["measured_parts"]),
    )
    check(
        "basis dice declared per ognuna delle quattro",
        all(result["basis"][stage] == "declared" for stage in estimate.LATENT_STAGES),
        {stage: result["basis"][stage] for stage in estimate.LATENT_STAGES},
    )


def history_row(*, machine_split: bool) -> dict:
    """Un giro passato: 10 s, con lo split macchina/attesa o senza."""
    timings = {
        "font": 5.0,
        "in_point": 90.0,
        "keyframes": 1.0,
        "measure_wall": 30.0,
        "render": 20.0,
        "probe": 0.2,
    }
    row = {
        "timeline_sec": 10.0,
        "material_sec": 10.0,
        "width": REAL_RESOLUTION[0],
        "height": REAL_RESOLUTION[1],
        "fps": REAL_FPS,
        "preset": "medium",
        "x264_vs_hw": "x264",
        "timings": dict(timings),
    }
    if machine_split:
        # il font vero del giro erano 4.0 s di macchina e 1.0 di cancello
        row["timings_machine"] = dict(timings, font=4.0, keyframes=0.5)
    return row


def test_s4_from_history() -> None:
    print("\n-- S4: con la storia la costante lascia il posto alla misura")
    measured = estimate.plan_estimate(Plan(), timings=[history_row(machine_split=True)])
    check(
        "font misurato: 4.0 s / 10 s * 15 s = 6.0 s",
        abs(measured["parts"]["font"] - 6.0) < 0.01,
        measured["parts"]["font"],
    )
    check(
        "keyframes misurato: 0.5 s / 10 s * 15 s = 0.75 s",
        abs(measured["parts"]["keyframes"] - 0.75) < 0.01,
        measured["parts"]["keyframes"],
    )
    # in_point e measure_wall chiedono DUE giri: una corsa sola puo' essere una
    # macchina occupata (141.8 s contro 2.9 di macchina libera). Restano costanti,
    # e la riga singola allarga solo il limite alto.
    check(
        "una riga sola non sostituisce la costante di in_point",
        measured["basis"]["in_point"] == "declared"
        and measured["basis"]["measure_wall"] == "declared",
        (measured["basis"]["in_point"], measured["basis"]["measure_wall"]),
    )
    check(
        "e la nota lo dice invece di far finta",
        any("una sola misura" in item for item in measured["notes"]),
        [item for item in measured["notes"] if "una sola" in item],
    )
    check(
        "il limite alto di in_point arriva alla misura vista",
        measured["parts"]["in_point"] < 90.0 / 10.0 * REAL_MATERIAL_SEC
        and measured["high"] > measured["seconds"] * 1.2,
        (measured["parts"]["in_point"], measured["high"]),
    )
    two = estimate.plan_estimate(
        Plan(),
        timings=[history_row(machine_split=True), history_row(machine_split=True)],
    )
    check(
        "due giri bastano: in_point passa a misurato",
        two["basis"]["in_point"].startswith("measured"),
        two["basis"]["in_point"],
    )
    unseparated = estimate.plan_estimate(Plan(), timings=[history_row(machine_split=False)])
    check(
        "senza split la storia non fa imparare 5 s di font (attesa inclusa)",
        abs(unseparated["parts"]["font"] - 0.014 * REAL_MATERIAL_SEC) < 0.01
        and unseparated["basis"]["font"] == "declared",
        (unseparated["parts"]["font"], unseparated["basis"]["font"]),
    )
    check(
        "e il referto dichiara i valori di stadio non separati",
        any("senza separazione" in item for item in unseparated["notes"]),
        [item for item in unseparated["notes"] if "separazione" in item],
    )


def test_never_reads_the_job_being_estimated() -> None:
    print("\n-- divieto: la stima non legge i tempi del lavoro che sta stimando")
    report = real_report(new=True)
    raised = None
    try:
        estimate.plan_estimate(Plan(), timings=report["timings"])
    except TypeError as exc:
        raised = str(exc)
    check(
        "passare i timings del report (un mapping) e' rifiutato",
        raised is not None and "storia" in raised,
        raised,
    )
    check(
        "la stima senza storia non dipende da nessun file",
        estimate.plan_estimate(Plan())["seconds"]
        == estimate.plan_estimate(Plan())["seconds"],
        estimate.plan_estimate(Plan())["seconds"],
    )
    with tempfile.TemporaryDirectory(prefix="vx_time_json_") as tmp:
        row = estimate.record_timings(tmp, report)
        check(
            "la riga resta JSON puro",
            json.loads(json.dumps(row)) == row,
            row["source"],
        )


def main() -> int:
    test_s3_row_from_a_new_report()
    test_s3_row_from_an_old_report()
    test_s4_parts_and_totals()
    test_s4_the_real_job()
    test_s4_without_history()
    test_s4_from_history()
    test_never_reads_the_job_being_estimated()

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
