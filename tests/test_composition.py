"""La composizione della scaletta: tre leve, tutte misurate (schema vx.composition/1).

    python tests/test_composition.py

C1  stesso input, stesso piano: la proposta è deterministica (niente orologio, random).
C2  apertura nitida -> gancio lungo (2,4 s), quantizzato al frame.
C3  apertura sotto soglia -> gancio corto (1,2 s).
C4  BPM con griglia accesa -> il gancio declina e il piano resta identico alla baseline.
C5  spazio insufficiente -> punto di presa proposto, o declino coi numeri.
C6  quota: su un reel da 6 s il gancio non supera il 35% della durata.
C7  b-roll: ordine per nitidezza misurata; una sola misura -> nessun riordino.
C8  applicazione: due clip contigue della stessa sorgente, frame totali invariati,
    `plan.validate()` vuoto, i b-roll nell'ordine proposto.
C9  le cifre del report sono contate sui piani: inquadrature prima/dopo, durata
    reale del primo clip, e coincidono quando la composizione non si applica.
C10 nessuna competenza spenta: le capability della scaletta non cambiano.
C11 latenza: `compose` resta sotto i 5 ms (p50) e il piano composto non aggiunge
    più di 20 ms.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import asset_triage, contract, decide, edl_plan, sections  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def _facts(**overrides) -> dict:
    facts = {
        "fps": 30.0,
        "duration_sec": 15.0,
        "opening": {"asset_id": "a1.mp4", "sharpness": 812.0, "threshold": 300.0},
        "a_roll": [
            {
                "asset_id": "a1.mp4",
                "seconds": 20.0,
                "sharpness": 812.0,
                "src_in": 1.0,
                "usable_runs": [{"start": 0.0, "end": 12.0}],
                "samples": [[0.0, 700.0], [1.0, 812.0], [2.0, 640.0]],
                "preferred": 280.0,
            }
        ],
        "b_roll": [
            {"asset_id": "b1.png", "seconds": 3.0, "sharpness": 512.7},
            {"asset_id": "b2.png", "seconds": 3.0, "sharpness": 640.1},
        ],
        "beat": {"enabled": False, "bpm": 0.0, "source": ""},
    }
    facts.update(overrides)
    return facts


# -- il payload per provare l'applicazione sul piano vero ------------------- #


def _pool():
    return [
        asset_triage.AssetInfo(
            "a.mp4", "video", "a.mp4", 1080, 1920, 6.0, True, role="a_roll"
        ),
        asset_triage.AssetInfo(
            "b1.png", "image", "b1.png", 1080, 1920, 0.0, False, role="b_roll"
        ),
        asset_triage.AssetInfo(
            "b2.png", "image", "b2.png", 1080, 1920, 0.0, False, role="b_roll"
        ),
    ]


def _payload():
    pool = _pool()
    payload = decide.parse_and_decide("un reel", asset_pool=pool)
    payload["asset_triage"] = asset_triage.triage_assets(
        pool, (1080, 1920), measure=lambda asset: {}
    ).payload_block()
    return payload


def _sources():
    return {
        "a.mp4": edl_plan.SourceRef(path="a.mp4", sha256="a" * 64, duration=6.0),
        "b1.png": edl_plan.SourceRef(path="b1.png", sha256="b" * 64, duration=3.0),
        "b2.png": edl_plan.SourceRef(path="b2.png", sha256="c" * 64, duration=3.0),
    }


def _media():
    return {
        "a.mp4": contract.ProbeResult(path="a.mp4", duration=6.0),
        "b1.png": contract.ProbeResult(path="b1.png", duration=3.0),
        "b2.png": contract.ProbeResult(path="b2.png", duration=3.0),
    }


def _build(payload, quality=None):
    return contract.payload_to_edl_plan(
        payload, _sources(), _media(), fps=30.0, quality=quality or {}
    )


def test_deterministic() -> None:
    print("\n1. C1 la proposta e' deterministica")
    first = sections.compose(_facts())
    second = sections.compose(_facts())
    check(
        "C1 due chiamate: stesso JSON",
        json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True),
        first["proposed"],
    )
    check("C1 schema dichiarato", first["schema"] == "vx.composition/1", first["schema"])


def test_hook_strong_and_weak() -> None:
    print("\n2. C2/C3 il gancio segue la nitidezza misurata")
    strong = sections.compose(_facts())
    check(
        "C2 apertura 2,7x la soglia -> 2,4 s",
        strong["proposed"].get("hook_seconds") == 2.4,
        strong["proposed"].get("hook_seconds"),
    )
    check(
        "C2 il perche' porta i numeri",
        "812.0" in strong["rules"]["hook"]["why"]
        and "2.71" in strong["rules"]["hook"]["why"],
        strong["rules"]["hook"]["why"][:80],
    )
    weak = sections.compose(
        _facts(opening={"asset_id": "a1.mp4", "sharpness": 120.0, "threshold": 300.0})
    )
    check(
        "C3 apertura sotto soglia -> 1,2 s",
        weak["proposed"].get("hook_seconds") == 1.2,
        weak["proposed"].get("hook_seconds"),
    )
    check(
        "C3 e lo dichiara",
        "sotto soglia" in weak["rules"]["hook"]["why"],
        weak["rules"]["hook"]["why"][:70],
    )


def test_grid_defers() -> None:
    print("\n3. C4 con la griglia accesa il gancio declina")
    facts = _facts(beat={"enabled": True, "bpm": 120.0, "source": "music"})
    block = sections.compose(facts)
    check(
        "C4 nessun hook proposto",
        "hook_seconds" not in block["proposed"],
        block["proposed"],
    )
    check(
        "C4 il declino nomina il BPM",
        any("120.00" in item["why"] for item in block["declined"]),
        block["declined"],
    )
    payload = _payload()
    baseline = _build(payload)
    payload["composition"] = block
    composed = _build(payload)
    check(
        "C4 il piano non cambia (stesse durate e stesse sorgenti)",
        [(c.source_id, c.src_in, c.src_out) for c in baseline.video_tracks[0].clips]
        == [(c.source_id, c.src_in, c.src_out) for c in composed.video_tracks[0].clips],
        [c.clip_id for c in composed.video_tracks[0].clips],
    )


def test_material_limits() -> None:
    print("\n4. C5 il gancio non inventa materiale")
    short = _facts(
        a_roll=[
            {
                "asset_id": "a1.mp4",
                "seconds": 1.4,
                "sharpness": 812.0,
                "src_in": 0.9,
                "usable_runs": [{"start": 0.0, "end": 1.4}],
                "samples": [[0.0, 300.0]],
                "preferred": 280.0,
            }
        ]
    )
    block = sections.compose(short)
    check(
        "C5 corsa troppo corta -> hook declinato",
        "hook_seconds" not in block["proposed"],
        block["proposed"],
    )
    check(
        "C5 il motivo porta le misure",
        any(
            "1.40" in item["why"] and "2.40" in item["why"] and "0.50" in item["why"]
            for item in block["declined"]
        ),
        block["declined"],
    )
    moved = _facts(
        a_roll=[
            {
                "asset_id": "a1.mp4",
                "seconds": 12.0,
                "sharpness": 812.0,
                "src_in": 10.5,
                "usable_runs": [{"start": 0.0, "end": 12.0}],
                "samples": [[2.0, 600.0], [5.0, 720.0], [10.5, 812.0]],
                "preferred": 280.0,
            }
        ]
    )
    block = sections.compose(moved)
    take = block["rules"]["hook"].get("take") or {}
    check(
        "C5 punto di presa spostato indietro",
        take.get("moved") is True and float(take.get("to") or 0.0) < 10.5,
        take,
    )
    check(
        "C5 e resta sopra la soglia preferita",
        float(take.get("sharpness") or 0.0) >= float(take.get("preferred") or 0.0),
        take,
    )


def test_share_limit() -> None:
    print("\n5. C6 il gancio non si mangia il reel")
    block = sections.compose(_facts(duration_sec=6.0))
    seconds = float(block["proposed"].get("hook_seconds") or 0.0)
    check("C6 su 6 s il gancio e' al massimo 2,1 s", seconds <= 2.1 + 1e-6, seconds)
    check(
        "C6 e il limite e' dichiarato",
        float(block["rules"]["hook"]["limits"]["share"]) == 2.1,
        block["rules"]["hook"]["limits"],
    )


def test_broll_order() -> None:
    print("\n6. C7 i b-roll entrano per nitidezza misurata")
    block = sections.compose(_facts())
    check(
        "C7 ordine decrescente",
        block["proposed"].get("broll_order") == ["b2.png", "b1.png"],
        block["proposed"].get("broll_order"),
    )
    check(
        "C7 il perche' cita le due misure",
        "640.1" in block["rules"]["broll"]["why"]
        and "512.7" in block["rules"]["broll"]["why"],
        block["rules"]["broll"]["why"],
    )
    one = sections.compose(
        _facts(b_roll=[{"asset_id": "b1.png", "seconds": 3.0, "sharpness": 512.7}])
    )
    check(
        "C7 un solo b-roll -> nessun ordine",
        "broll_order" not in one["proposed"]
        and any(item["id"] == "broll" for item in one["declined"]),
        one["declined"],
    )
    unmeasured = sections.compose(
        _facts(
            b_roll=[
                {"asset_id": "b1.png", "seconds": 3.0, "sharpness": None},
                {"asset_id": "b2.png", "seconds": 3.0, "sharpness": 640.1},
            ]
        )
    )
    check(
        "C7 una sola misura -> nessun riordino inventato",
        "broll_order" not in unmeasured["proposed"],
        unmeasured["declined"],
    )
    already = sections.compose(
        _facts(
            b_roll=[
                {"asset_id": "b2.png", "seconds": 3.0, "sharpness": 640.1},
                {"asset_id": "b1.png", "seconds": 3.0, "sharpness": 512.7},
            ]
        )
    )
    check(
        "C7 ordine gia' giusto -> nessuna modifica dichiarata",
        "broll_order" not in already["proposed"],
        already["declined"],
    )


def test_builder_applies() -> None:
    print("\n7. C8 il piano applica: gancio spezzato, b-roll riordinati")
    payload = _payload()
    baseline = _build(payload)
    payload["composition"] = {
        "schema": "vx.composition/1",
        "proposed": {
            "hook_seconds": 2.0,
            "hook_asset": "a.mp4",
            "broll_order": ["b2.png", "b1.png"],
        },
    }
    plan = _build(payload)
    check("C8 il piano composto valida", plan.validate() == [], plan.validate())

    layer0 = plan.video_tracks[0].clips
    check("C8 due clip in testa, contigue", len(layer0) >= 2, len(layer0))
    first, second = layer0[0], layer0[1]
    check(
        "C8 stessa sorgente e nessun buco",
        first.source_id == second.source_id
        and abs(first.src_out - second.src_in) <= 1e-6
        and abs(first.dst_out - second.dst_in) <= 1e-6,
        (first.src_in, first.src_out, second.src_in),
    )
    check(
        "C8 il primo clip dura esattamente il gancio",
        abs(first.duration - 2.0) <= 1e-6,
        first.duration,
    )
    frames_before = round(sum(c.duration for c in baseline.video_tracks[0].clips) * 30)
    frames_after = round(sum(c.duration for c in layer0) * 30)
    check(
        "C8 i frame totali non cambiano",
        frames_before == frames_after,
        (frames_before, frames_after),
    )
    check(
        "C8 la durata del piano non cambia",
        abs(plan.duration_sec - baseline.duration_sec) <= 1e-6,
        (baseline.duration_sec, plan.duration_sec),
    )
    order_before = [c.source_id for c in baseline.video_tracks[1].clips]
    order_after = [c.source_id for c in plan.video_tracks[1].clips]
    check(
        "C8 l'ordine dei b-roll cambia come proposto",
        order_after == list(reversed(order_before)) and order_before,
        (order_before, order_after),
    )
    check(
        "C8 il piano lo dichiara nelle note",
        any("composizione" in note for note in plan.notes),
        [note for note in plan.notes if "composizione" in note],
    )


def test_measurement_not_promise() -> None:
    print("\n8. C9 le cifre vengono dai piani, non dalla proposta")
    payload = _payload()
    baseline = _build(payload)
    proposed = {
        "schema": "vx.composition/1",
        "proposed": {"hook_seconds": 2.0, "hook_asset": "a.mp4"},
        "declined": [],
    }
    payload["composition"] = proposed
    plan = _build(payload)
    block = sections.measure_application(proposed, baseline, plan)
    applied = block["applied"]
    check("C9 inquadrature 1 -> 2", (applied["shots_before"], applied["shots_after"]) == (1, 2), applied)
    check(
        "C9 la durata del gancio e' quella del piano",
        applied["hook_seconds"] == 2.0 and applied["hook_applied"] is True,
        applied,
    )
    check("C9 sopravvive alla scelta creativa", applied["survived"] is True, applied)

    payload = _payload()
    baseline = _build(payload)
    plan = _build(payload)
    block = sections.measure_application(proposed, baseline, plan)
    applied = block["applied"]
    check(
        "C9 senza applicazione i numeri coincidono",
        applied["shots_before"] == applied["shots_after"]
        and applied["delta"] == 0
        and applied["hook_applied"] is False,
        applied,
    )
    check(
        "C9 e la descrizione non promette un gancio",
        "hook proposto" in sections.describe(block),
        sections.describe(block),
    )


def test_capabilities_unchanged() -> None:
    print("\n9. C10 nessuna competenza spenta dalla composizione")
    report = {
        "measured": {"duration_sec": 12.0, "clips": 3},
        "opening": {"chosen": {"asset_id": "a", "sharpness": 80.0}, "threshold": 50.0},
        "coherence": {"metrics": {"shots": 5}},
        "bridge": {"cuts": 2},
        "texts": ["STASERA", "SCRIVICI"],
        "faces": {"speakers": 1},
        "selected": [
            {"asset_id": "a", "role": "a_roll"},
            {"asset_id": "b", "role": "b_roll"},
        ],
    }
    without = sections.compile_sections(report)
    with_composition = sections.compile_sections(
        {
            **report,
            "composition": {
                "schema": "vx.composition/1",
                "proposed": {"hook_seconds": 2.0, "broll_order": ["b"]},
                "applied": {
                    "hook_seconds": 2.0,
                    "hook_baseline_seconds": 3.5,
                    "hook_applied": True,
                    "shots_before": 5,
                    "shots_after": 6,
                },
            },
        }
    )
    check(
        "C10 stesse capability, stesse sezioni compilate",
        without["capabilities"] == with_composition["capabilities"]
        and without["filled"] == with_composition["filled"],
        with_composition["capabilities"],
    )
    hook = next(row for row in with_composition["sections"] if row["id"] == "hook")
    check(
        "C10 la sezione mostra la composizione",
        (hook.get("composed") or {}).get("seconds") == 2.0
        and "composizione" in hook["why"],
        hook.get("composed"),
    )


def test_latency() -> None:
    print("\n10. C11 la composizione non costa latenza")
    facts = _facts(
        a_roll=[
            {
                "asset_id": f"a{index}.mp4",
                "seconds": 10.0,
                "sharpness": 400.0 + index,
                "src_in": 2.0,
                "usable_runs": [{"start": 0.0, "end": 10.0}],
                "samples": [[0.0, 400.0], [2.0, 400.0 + index]],
                "preferred": 280.0,
            }
            for index in range(20)
        ],
        b_roll=[
            {"asset_id": f"b{index}.png", "seconds": 3.0, "sharpness": 300.0 + index}
            for index in range(20)
        ],
    )
    times = []
    for _ in range(50):
        started = time.perf_counter()
        sections.compose(facts)
        times.append((time.perf_counter() - started) * 1000.0)
    p50 = statistics.median(times)
    check("C11 compose p50 sotto 5 ms su 20 asset", p50 < 5.0, f"{p50:.3f} ms")

    payload = _payload()
    baseline_payload = dict(payload)
    payload["composition"] = sections.compose(_facts())
    delays = []
    for _ in range(10):
        started = time.perf_counter()
        _build(baseline_payload)
        plain = time.perf_counter() - started
        started = time.perf_counter()
        _build(payload)
        composed = time.perf_counter() - started
        delays.append((composed - plain) * 1000.0)
    delta = statistics.median(delays)
    check("C11 il piano composto costa meno di 20 ms in piu'", delta < 20.0, f"{delta:.3f} ms")


def main() -> int:
    test_deterministic()
    test_hook_strong_and_weak()
    test_grid_defers()
    test_material_limits()
    test_share_limit()
    test_broll_order()
    test_builder_applies()
    test_measurement_not_promise()
    test_capabilities_unchanged()
    test_latency()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
