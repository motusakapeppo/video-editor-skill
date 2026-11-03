"""Render-time estimator: medians over history, defaults declared, no sklearn.

python tests/test_estimate.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import estimate  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def row(
    duration: float,
    seconds: float,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
    preset: str = "medium",
    x264_vs_hw: str = "x264",
    crf: int = 18,
) -> dict:
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "duration_sec": duration,
        "crf": crf,
        "preset": preset,
        "x264_vs_hw": x264_vs_hw,
        "seconds": seconds,
    }


def job(
    duration: float,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
    preset: str = "medium",
    x264_vs_hw: str = "x264",
) -> dict:
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "duration_sec": duration,
        "crf": 18,
        "preset": preset,
        "x264_vs_hw": x264_vs_hw,
    }


def relative_error(predicted: float, truth: float) -> float:
    return abs(predicted - truth) / truth


# ---------------------------------------------------------------------------
# piani finti: nessun render, nessuna rete. Solo struttura e durate.
# ---------------------------------------------------------------------------


class Clip:
    def __init__(
        self,
        index: int,
        dst_in: float,
        dst_out: float,
        src_in: float,
        src_out: float,
        *,
        layer: int = 0,
        effects=(),
        clip_id: str = "",
    ) -> None:
        self.clip_id = clip_id or f"v{layer}_{index}_0"
        self.source_id = f"s{index}"
        self.src_in = src_in
        self.src_out = src_out
        self.dst_in = dst_in
        self.dst_out = dst_out
        self.layer = layer
        self.effects = tuple(effects)


class Track:
    def __init__(self, clips) -> None:
        self.clips = list(clips)
        self.track_id = 0
        self.role = "a_roll"


class Plan:
    def __init__(self, *, seconds: float = 9.0, resolution=(1080, 1920), extra=True) -> None:
        self.fps = 30.0
        self.duration_sec = seconds
        self.aspect_ratio = "9:16"
        self.resolution = tuple(resolution)
        self.sources = {}
        per = seconds / 3.0
        clips = [
            Clip(0, 0.0, per, 10.0, 10.0 + per),
            Clip(1, per, 2 * per, 20.0, 20.0 + per),
            Clip(2, 2 * per, 3 * per, 30.0, 30.0 + per),
        ]
        if extra:
            # overlay e effetti: la parte che la versione rapida taglia
            clips[0].effects = (
                {"primitives": [{"kind": "blur", "params": {"sigma": 12}}]},
                {"primitives": [{"kind": "grade", "params": {"contrast": 1.4}}]},
                {"kind": "grain", "params": {"strength": 0.2}},
            )
            clips[1].effects = ({"kind": "crop", "params": {}},)
            clips.append(
                Clip(
                    3,
                    0.0,
                    seconds,
                    40.0,
                    40.0 + seconds,
                    layer=1,
                    effects=(
                        {"kind": "text", "params": {"content": "hook"}},
                        {"kind": "blend", "params": {"mix": 0.4}},
                    ),
                )
            )
            clips.append(
                Clip(
                    4,
                    seconds / 2.0,
                    seconds,
                    50.0,
                    50.0 + seconds / 2.0,
                    layer=1,
                    effects=({"kind": "grain", "params": {}},),
                )
            )
        self.video_tracks = (Track(clips),)


class FakeClock:
    """Avanza solo quando qualcuno dorme: la prova dell'attesa non aspetta."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))


def timing_row(seconds: float, timings: dict, *, resolution=(1080, 1920), preset: str = "medium") -> dict:
    return {
        "timeline_sec": seconds,
        "material_sec": seconds,
        "width": resolution[0],
        "height": resolution[1],
        "fps": 30.0,
        "preset": preset,
        "x264_vs_hw": "x264",
        "timings": dict(timings),
    }


def test_plan_cost() -> None:
    print("\n-- costo del piano: somma degli effetti per shot, per i secondi")
    plan = Plan()
    cost = estimate.plan_cost(plan)
    check("cinque clip video contate", cost["shots"] == 5, cost["shots"])
    check("sette effetti contati", cost["effects"] == 7, cost["effects"])
    check("il costo non e' zero", cost["cost"] > 0, cost["cost"])
    expected = sum(item["cost_seconds"] for item in cost["per_shot"])
    check(
        "cost_seconds e' la somma dei shot",
        abs(cost["cost_seconds"] - expected) < 0.01,
        (cost["cost_seconds"], round(expected, 3)),
    )
    check(
        "ogni shot dichiara clip, secondi ed effetti",
        all(
            {"clip", "seconds", "effects", "cost", "cost_seconds"} <= set(item)
            for item in cost["per_shot"]
        ),
    )
    check(
        "un effetto ignoto vale la costante dichiarata",
        estimate.effect_cost({"kind": "grain"}) == estimate.DEFAULT_EFFECT_COST,
    )


def test_plan_estimate_declared() -> None:
    print("\n-- stima senza misure: costanti dichiarate e calibrated false")
    plan = Plan()
    result = estimate.plan_estimate(plan)
    check(
        "la forma e' quella richiesta",
        {"seconds", "low", "high", "calibrated", "parts"} <= set(result),
        sorted(result)[:6],
    )
    check("calibrated false senza timings", result["calibrated"] is False)
    check(
        "una nota dice no_timings",
        any("no_timings" in note for note in result["notes"]),
        result["notes"][0],
    )
    check(
        "l'intervallo contiene la stima",
        result["low"] <= result["seconds"] <= result["high"],
        (result["low"], result["seconds"], result["high"]),
    )
    check(
        "parts dichiara tutti gli stadi prezzati",
        set(result["parts"]) == set(estimate.ESTIMATE_STAGES),
        sorted(result["parts"]),
    )
    check(
        "ogni parte e' positiva",
        all(value > 0 for value in result["parts"].values()),
        result["parts"],
    )
    check("il render e' la parte piu' cara", result["parts"]["render"] == max(result["parts"].values()), result["parts"]["render"])

    uhd = estimate.plan_estimate(Plan(resolution=(2160, 3840)))
    check(
        "piu' pixel, piu' secondi",
        uhd["seconds"] > result["seconds"],
        (result["seconds"], uhd["seconds"]),
    )
    slow = estimate.plan_estimate(Plan(), preset="veryslow")
    fast = estimate.plan_estimate(Plan(), preset="ultrafast")
    check(
        "il preset veloce costa meno (costante dichiarata)",
        fast["seconds"] < result["seconds"] < slow["seconds"],
        (fast["seconds"], result["seconds"], slow["seconds"]),
    )
    plain = estimate.plan_estimate(Plan(extra=False))
    check(
        "gli effetti alzano la parte render",
        result["parts"]["render"] > plain["parts"]["render"],
        (plain["parts"]["render"], result["parts"]["render"]),
    )
    check(
        "il piano con effetti dichiara il proprio costo",
        result["effects"]["count"] == 7 and result["effects"]["factor"] > 1.0,
        result["effects"],
    )


def test_calibration() -> None:
    print("\n-- calibrazione: dai timings misurati alle tariffe per stadio")
    rows = [
        timing_row(
            10.0,
            {
                "probe": 0.5,
                "ingest": 2.0,
                "audio": 1.0,
                "analysis": 1.5,
                "faces": 1.0,
                "render": 8.0,
                "qc": 0.4,
            },
        ),
        timing_row(
            20.0,
            {
                "probe": 1.0,
                "ingest": 6.0,
                "audio": 2.0,
                "analysis": 3.0,
                "faces": 2.0,
                "render": 16.0,
                "qc": 0.8,
            },
        ),
    ]
    model = estimate.calibrate(rows)
    check("due giri usati", model["n"] == 2, model["n"])
    check(
        "una tariffa per ogni stadio di materiale",
        all(model["material"][stage]["n"] == 2 for stage in estimate.MATERIAL_STAGES),
        {stage: model["material"][stage]["n"] for stage in estimate.MATERIAL_STAGES},
    )
    check(
        "probe misurato vale 0.05 s/s di materiale",
        abs(model["material"]["probe"]["median"] - 0.05) < 1e-6,
        model["material"]["probe"]["median"],
    )
    check(
        "le righe senza timings si contano, non si ignorano",
        estimate.calibrate([{"timeline_sec": 3.0}, {"timings": {}}])["skipped"] == 2,
    )

    plan = Plan()
    measured = estimate.plan_estimate(plan, timings=rows)
    check("con i timings calibrated e' true", measured["calibrated"] is True)
    check(
        "nessuna nota no_timings",
        not any("no_timings" in note for note in measured["notes"]),
        measured["notes"],
    )
    check(
        "la parte probe usa la tariffa misurata",
        abs(measured["parts"]["probe"] - 0.05 * measured["material_sec"]) < 0.01,
        (measured["parts"]["probe"], measured["material_sec"]),
    )
    check(
        "le parti misurate non sono quelle dichiarate",
        measured["basis"]["probe"].startswith("measured")
        and estimate.plan_estimate(plan)["basis"]["probe"] == "declared",
        (measured["basis"]["probe"], estimate.plan_estimate(plan)["basis"]["probe"]),
    )
    longer = estimate.plan_estimate(Plan(seconds=18.0), timings=rows)
    check(
        "il doppio di materiale costa piu' del singolo",
        longer["seconds"] > measured["seconds"],
        (measured["seconds"], longer["seconds"]),
    )

    history = [
        {
            "width": 1080,
            "height": 1920,
            "fps": 30.0,
            "duration_sec": d,
            "crf": 18,
            "preset": "medium",
            "x264_vs_hw": "x264",
            "seconds": d * 1.2,
        }
        for d in (6.0, 9.0, 12.0)
    ]
    with_history = estimate.plan_estimate(plan, history=history)
    check(
        "la storia dei render prezza la parte render",
        with_history["basis"]["render"] == "measured render_history",
        with_history["basis"]["render"],
    )
    check(
        "e la stima non resta quella dichiarata",
        with_history["render_history_rows"] == 3
        and with_history["parts"]["render"] != estimate.plan_estimate(plan)["parts"]["render"],
        with_history["parts"]["render"],
    )


def test_record_timings() -> None:
    print("\n-- i tempi di un giro diventano la calibrazione del prossimo")
    with tempfile.TemporaryDirectory(prefix="vx_timing_") as tmp:
        work = Path(tmp)
        report = {
            "fps": 30.0,
            "timeline_sec": 10.0,
            "estimate": {
                "material_sec": 10.0,
                "resolution": [1080, 1920],
                "preset": "medium",
                "x264_vs_hw": "x264",
                "effects": {"cost": 1.5},
            },
            "timings": {
                "probe": 0.2,
                "ingest": 2.5,
                "audio": 1.0,
                "analysis": 1.5,
                "faces": 1.0,
                "render": 8.0,
                "qc": 0.4,
                "deliverables": 0.1,
                "total": 15.0,
            },
        }
        row = estimate.record_timings(work, report)
        check("la riga si scrive", isinstance(row, dict) and row is not None, row and row.get("timings"))
        rows = estimate.load_timings(work)
        check("load_timings la rilegge", len(rows) == 1, len(rows))
        check(
            "la riga porta materiale, preset e i tempi",
            rows[0]["material_sec"] == 10.0
            and rows[0]["preset"] == "medium"
            and rows[0]["timings"]["render"] == 8.0,
            rows[0],
        )
        measured = estimate.plan_estimate(Plan(), timings=rows)
        check("e la stima si dichiara calibrata", measured["calibrated"] is True)
        check(
            "record_timings senza timings non scrive niente",
            estimate.record_timings(work, {"timeline_sec": 1.0}) is None,
            len(estimate.load_timings(work)),
        )


def test_fast_plan() -> None:
    print("\n-- versione rapida: stesso montaggio, meno costo")
    plan = Plan()
    fast = estimate.fast_plan(plan)
    check(
        "la risoluzione scende e resta pari",
        fast["resolution"][0] < plan.resolution[0]
        and fast["resolution"][1] < plan.resolution[1]
        and all(value % 2 == 0 and value >= 320 for value in fast["resolution"]),
        fast["resolution"],
    )
    check(
        "il piano rapido porta la risoluzione nuova",
        list(fast["plan"].resolution) == fast["resolution"],
        fast["plan"].resolution,
    )
    check("il preset rapido e' dichiarato", fast["preset"] == estimate.FAST_PRESET, fast["preset"])
    check("la struttura di coerenza non cambia", fast["coherence_kept"] is True)
    check("le rinunce sono elencate", bool(fast["traded"]), fast["traded"])
    check(
        "un solo overlay e un solo effetto per clip",
        fast["dropped_overlays"] == 1 and fast["dropped_effects"] == 3,
        (fast["dropped_overlays"], fast["dropped_effects"]),
    )
    check(
        "i clip di layer 0 restano tutti",
        len([c for t in fast["plan"].video_tracks for c in t.clips if c.layer == 0]) == 3,
    )
    full_est = estimate.plan_estimate(plan)
    fast_est = estimate.plan_estimate(fast["plan"], preset=fast["preset"])
    check(
        "la stima rapida costa meno",
        fast_est["seconds"] < full_est["seconds"]
        and fast_est["high"] < full_est["high"],
        (full_est["seconds"], fast_est["seconds"]),
    )

    from vx import coherence  # noqa: PLC0415 - solo per il confronto

    full_report = coherence.evaluate(plan)
    fast_report = coherence.evaluate(fast["plan"])
    check(
        "la coerenza del piano rapido e' identica a quella del completo",
        fast_report["ok"] == full_report["ok"]
        and fast_report["violations"] == full_report["violations"],
        fast_report.get("violations"),
    )
    check(
        "e vede le stesse inquadrature",
        fast_report["metrics"]["shots"] == full_report["metrics"]["shots"],
        (full_report["metrics"]["shots"], fast_report["metrics"]["shots"]),
    )

    mapping = {
        "timeline": {"fps": 30.0, "duration_sec": 6.0, "aspect_ratio": "9:16", "resolution": [1080, 1920]},
        "sources": {},
        "tracks": {
            "video": [
                {
                    "track_id": 0,
                    "role": "a_roll",
                    "clips": [
                        {
                            "clip_id": "a",
                            "source_id": "s0",
                            "src_in": 0.0,
                            "src_out": 6.0,
                            "dst_in": 0.0,
                            "dst_out": 6.0,
                            "layer": 0,
                            "effects": [
                                {"kind": "grain"},
                                {"kind": "crop"},
                            ],
                        }
                    ],
                }
            ],
            "audio": [],
        },
    }
    mapped = estimate.fast_plan(mapping)
    check(
        "anche un plan.json (mapping) si riduce",
        mapped["plan"]["timeline"]["resolution"] == estimate.fast_resolution([1080, 1920])
        and mapped["plan"]["tracks"]["video"][0]["clips"][0]["effects"] == [{"kind": "grain"}],
        mapped["plan"]["timeline"]["resolution"],
    )
    check("e la struttura resta la stessa", mapped["coherence_kept"] is True)


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def wait_for(path: Path, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.is_file() and read_json(path).get("choice") is None:
            return True
        time.sleep(0.02)
    return False


def test_plan_gate_under_budget() -> None:
    print("\n-- sotto il tetto non si chiede niente")
    with tempfile.TemporaryDirectory(prefix="vx_plan_gate_") as tmp:
        work = Path(tmp)
        plan = Plan()
        fake = FakeClock()
        result = estimate.plan_gate(
            work,
            plan,
            budget=10_000.0,
            wait=30.0,
            clock=fake.clock,
            sleep=fake.sleep,
            notify=lambda _msg: None,
        )
        section = result["plan_choice"]
        check("asked false", section["asked"] is False, section["asked"])
        check("chosen full", section["chosen"] == "full", section["chosen"])
        check("selected_by budget", section["selected_by"] == "budget", section["selected_by"])
        check("nessuna attesa", section["waited_sec"] == 0.0, section["waited_sec"])
        check("nessun plan_choice.json", not (work / estimate.PLAN_CHOICE_NAME).exists())
        check("il piano torna intatto", result["plan"] is plan)
        check(
            "la stima accompagna sempre la scelta",
            result["estimate"]["high"] <= 10_000.0
            and section["estimate_full"]["high"] == result["estimate"]["high"],
            section["estimate_full"],
        )


def test_plan_gate_full() -> None:
    print("\n-- sopra il tetto: attesa, e la scelta full lascia il piano com'e'")
    with tempfile.TemporaryDirectory(prefix="vx_plan_gate_") as tmp:
        work = Path(tmp)
        plan = Plan()
        (work / estimate.PLAN_CHOICE_NAME).write_text(
            json.dumps({"choice": "full"}), encoding="utf-8"
        )
        fake = FakeClock()
        result = estimate.plan_gate(
            work,
            plan,
            budget=1.0,
            wait=30.0,
            clock=fake.clock,
            sleep=fake.sleep,
            notify=lambda _msg: None,
        )
        section = result["plan_choice"]
        check("asked true sopra il tetto", section["asked"] is True, section["asked"])
        check("chosen full", section["chosen"] == "full", section["chosen"])
        check("selected_by user", section["selected_by"] == "user", section["selected_by"])
        check("il piano e' lo stesso oggetto", result["plan"] is plan)
        check("nessuna attesa con la scelta gia' presente", section["waited_sec"] == 0.0)
        check(
            "la stima resta quella completa",
            result["estimate"]["seconds"] == section["estimate_full"]["seconds"],
            result["estimate"]["seconds"],
        )
        check(
            "il file di scelta resta come record",
            read_json(work / estimate.PLAN_CHOICE_NAME).get("choice") == "full",
        )


def test_plan_gate_fast() -> None:
    print("\n-- sopra il tetto: la scelta fast ricostruisce e risparmia")
    with tempfile.TemporaryDirectory(prefix="vx_plan_gate_") as tmp:
        work = Path(tmp)
        plan = Plan()
        holder: dict = {}

        def run_gate() -> None:
            holder["result"] = estimate.plan_gate(
                work, plan, budget=1.0, wait=5.0, notify=lambda _msg: None
            )

        worker = threading.Thread(target=run_gate, daemon=True)
        worker.start()
        appeared = wait_for(work / estimate.PLAN_CHOICE_NAME)
        check("il segnale d'attesa appare", appeared)
        waiting = read_json(work / estimate.PLAN_CHOICE_NAME)
        check(
            "il segnale dichiara schema, stima e opzioni",
            waiting.get("schema") == estimate.PLAN_CHOICE_SCHEMA
            and waiting.get("estimate", {}).get("high", 0) > 1.0
            and set(waiting.get("options") or {}) == {"full", "fast"},
            {k: waiting.get(k) for k in ("schema", "choice")},
        )
        (work / estimate.PLAN_CHOICE_NAME).write_text(
            json.dumps({"choice": "fast"}), encoding="utf-8"
        )
        worker.join(timeout=10)
        check("il cancello si sblocca", not worker.is_alive())
        result = holder.get("result") or {}
        section = result.get("plan_choice") or {}
        check("chosen fast", section.get("chosen") == "fast", section.get("chosen"))
        check("selected_by user", section.get("selected_by") == "user", section.get("selected_by"))
        check(
            "saved_seconds_estimate e' positivo",
            section.get("saved_seconds_estimate", 0) > 0,
            section.get("saved_seconds_estimate"),
        )
        check("traded elenca le rinunce", bool(section.get("traded")), section.get("traded"))
        check(
            "il piano eseguito e' quello rapido",
            list(result["plan"].resolution) == section.get("fast_resolution"),
            result["plan"].resolution,
        )
        check(
            "il costo stimato scende sotto il completo",
            result["estimate"]["seconds"] < section["estimate_full"]["seconds"],
            (section["estimate_full"]["seconds"], result["estimate"]["seconds"]),
        )
        check("la coerenza resta garantita", section.get("coherence_kept") is True)


def test_plan_gate_timeout() -> None:
    print("\n-- allo scadere si parte con la rapida e lo si dichiara")
    with tempfile.TemporaryDirectory(prefix="vx_plan_gate_") as tmp:
        work = Path(tmp)
        fake = FakeClock()
        result = estimate.plan_gate(
            work,
            Plan(),
            budget=1.0,
            wait=2.0,
            clock=fake.clock,
            sleep=fake.sleep,
            notify=lambda _msg: None,
        )
        section = result["plan_choice"]
        check("chosen fast", section["chosen"] == "fast", section["chosen"])
        check("selected_by timeout", section["selected_by"] == "timeout", section["selected_by"])
        check("ha aspettato il tempo dichiarato", section["waited_sec"] >= 1.9, section["waited_sec"])
        check("il segnale d'attesa sparisce", not (work / estimate.PLAN_CHOICE_NAME).exists())
        check("la rinuncia e' dichiarata", bool(section["traded"]))
        closed = estimate.plan_gate(
            work,
            Plan(),
            budget=1.0,
            wait=2.0,
            gate=False,
            clock=fake.clock,
            sleep=fake.sleep,
            notify=lambda _msg: None,
        )
        check(
            "cancello chiuso: nessuna domanda anche sopra il tetto",
            closed["plan_choice"]["asked"] is False
            and closed["plan_choice"]["selected_by"] == "auto",
            closed["plan_choice"]["selected_by"],
        )


def test_plan_choice_reader() -> None:
    print("\n-- il file della scelta: solo full/fast sono risposte")
    with tempfile.TemporaryDirectory(prefix="vx_plan_choice_") as tmp:
        work = Path(tmp)
        path = work / estimate.PLAN_CHOICE_NAME
        check("file assente: nessuna scelta", estimate.read_plan_choice(work) == "")
        path.write_text("{rotto", encoding="utf-8")
        check("file rotto: nessuna scelta", estimate.read_plan_choice(work) == "")
        path.write_text(json.dumps({"choice": None}), encoding="utf-8")
        check("il segnale d'attesa non e' una scelta", estimate.read_plan_choice(work) == "")
        path.write_text(json.dumps({"choice": "FAST"}), encoding="utf-8")
        check("la scelta si legge senza maiuscole", estimate.read_plan_choice(work) == "fast")
        path.write_text(json.dumps({"choice": "slow"}), encoding="utf-8")
        check("un valore ignoto non e' una scelta", estimate.read_plan_choice(work) == "")


def main() -> int:
    test_plan_cost()
    test_plan_estimate_declared()
    test_calibration()
    test_record_timings()
    test_fast_plan()
    test_plan_gate_under_budget()
    test_plan_gate_full()
    test_plan_gate_fast()
    test_plan_gate_timeout()
    test_plan_choice_reader()

    with tempfile.TemporaryDirectory(prefix="vx_estimate_") as tmp:
        work = Path(tmp)

        # --- empty history: declared default, explicit note -----------------
        empty_model = estimate.fit([])
        seconds, note = estimate.predict(empty_model, job(40.0))
        check("empty history -> model has no observations", empty_model["n"] == 0)
        check(
            "empty history -> declared default seconds",
            seconds == estimate.DEFAULT_SECONDS,
            seconds,
        )
        check("empty history -> note says no_history", "no_history" in note, note)
        check(
            "default lies inside the declared range",
            estimate.DEFAULT_RANGE_SEC[0] <= seconds <= estimate.DEFAULT_RANGE_SEC[1],
        )
        missing_path = work / "does_not_exist.jsonl"
        seconds_missing, note_missing = estimate.predict(
            estimate.fit(missing_path), job(40.0)
        )
        check(
            "absent history file behaves like empty history",
            seconds_missing == seconds and "no_history" in note_missing,
            note_missing,
        )

        # --- synthetic trend: seconds proportional to duration -------------
        durations = [6.0, 9.0, 12.0, 18.0, 24.0, 30.0, 36.0]
        rate = 0.9
        history = [row(d, d * rate) for d in durations]
        model = estimate.fit(history)
        check(
            "history with 7 renders fits 1 bucket",
            len(model["buckets"]) == 1,
            model["buckets"].keys(),
        )

        held_out = 45.0
        truth = held_out * rate
        predicted, note_held = estimate.predict(model, job(held_out))
        error = relative_error(predicted, truth)
        check(
            "held-out duration within 25% of trend",
            error <= 0.25,
            f"pred={predicted} truth={truth} err={error:.3f}",
        )
        check(
            "held-out note names the bucket",
            "bucket=x264|medium" in note_held,
            note_held,
        )

        # --- monotonic in duration ----------------------------------------
        predictions = [
            estimate.predict(model, job(d))[0] for d in (5.0, 10.0, 20.0, 40.0, 90.0)
        ]
        check(
            "prediction is monotonic in duration",
            all(a < b for a, b in zip(predictions, predictions[1:])),
            predictions,
        )

        # --- resolution scaling -------------------------------------------
        hd = estimate.predict(model, job(20.0, width=1920, height=1080))[0]
        uhd = estimate.predict(model, job(20.0, width=3840, height=2160))[0]
        check(
            "4x pixels -> 4x predicted seconds",
            abs(uhd / hd - 4.0) < 0.01,
            round(uhd / hd, 4),
        )

        # --- codec buckets stay separate ----------------------------------
        mixed = [row(d, d * 1.6) for d in durations] + [
            row(d, d * 0.4, x264_vs_hw="hw") for d in durations
        ]
        mixed_model = estimate.fit(mixed)
        software = estimate.predict(mixed_model, job(20.0))[0]
        hardware = estimate.predict(mixed_model, job(20.0, x264_vs_hw="hw"))[0]
        check(
            "hardware job predicted faster than software job",
            hardware < software,
            (hardware, software),
        )
        check(
            "hardware prediction uses the hw bucket",
            "hw|medium" in estimate.predict(mixed_model, job(20.0, x264_vs_hw="hw"))[1],
        )
        check(
            "unseen preset falls back and says so",
            "fallback"
            in estimate.predict(mixed_model, job(20.0, preset="veryslow"))[1],
        )

        # --- JSONL round-trip through record() ----------------------------
        history_path = work / "render_history.jsonl"
        for d in durations:
            estimate.record(history_path, job(d), d * rate)
        loaded = estimate.load_history(history_path)
        check(
            "record() appends one JSONL row per render",
            len(loaded) == len(durations),
            len(loaded),
        )
        from_disk = estimate.fit(history_path)
        predicted_disk, _ = estimate.predict(from_disk, job(held_out))
        check(
            "history reloaded from disk predicts within 25%",
            relative_error(predicted_disk, truth) <= 0.25,
            predicted_disk,
        )
        check(
            "model survives a JSON round-trip",
            estimate.predict(json.loads(json.dumps(model)), job(held_out))[0]
            == predicted,
        )

        # --- junk rows are counted, not crashed on ------------------------
        dirty = history[:-1] + [
            row(0.0, 10.0),
            {"width": 1920, "height": 1080, "fps": 30, "duration_sec": 10.0},
            {
                "width": "x",
                "height": None,
                "fps": 30,
                "duration_sec": 10.0,
                "seconds": 3.0,
            },
        ]
        dirty_model = estimate.fit(dirty)
        check(
            "invalid rows are skipped and counted",
            dirty_model["n"] == len(history) - 1 and dirty_model["skipped"] == 3,
            (dirty_model["n"], dirty_model["skipped"]),
        )

        # --- begin/finish contract used by auto ---------------------------
        run_dir = work / "auto_run"
        fragment = estimate.begin(
            type(
                "Plan",
                (),
                {"resolution": [1920, 1080], "fps": 30.0, "duration_sec": 20.0},
            )(),
            run_dir,
            crf=18,
            preset="medium",
        )
        check(
            "begin() reports an estimate before the render",
            fragment["predicted_seconds"] > 0
            and fragment["actual_seconds"] is None
            and "_started" in fragment,
            fragment["predicted_seconds"],
        )
        finished = estimate.finish(fragment)
        check(
            "finish() records actual seconds and drops private keys",
            finished["actual_seconds"] is not None
            and "_started" not in finished
            and finished["error_ratio"] is not None,
            (finished["actual_seconds"], finished["error_ratio"]),
        )
        check(
            "finish() appends the render to history",
            len(estimate.load_history(run_dir / estimate.HISTORY_FILENAME)) == 1,
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
