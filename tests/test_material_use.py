"""Il piano consuma tutte le corse utilizzabili, non solo il picco.

Il difetto riprodotto: senza ricetta, una richiesta di 10 s su 13.27 s di
materiale buono consegnava 1.87 s. La scelta del punto di presa misurava una
sola finestra (il picco), e il ponte EDL montava da li' fino alla fine della
finestra: 1.20 s dalla prima clip, 0.67 s dalla seconda. Le corse successive e
i tratti ``below_preferred`` restavano sul tavolo.

Qui si verifica, senza ffmpeg e senza rete:

* ``choose_in_point`` dichiara **tutte** le corse, non solo la migliore;
* il piano riempie la durata chiesta consumando le corse in ordine;
* ogni ``src_in``/``src_out`` cade dentro una corsa dichiarata;
* i numeri del report sono coerenti: se ``reachable`` e' vero,
  ``duration_retained`` non scende sotto ``1 - max_removal``; se e' falso la
  nota dice quanta parte era davvero utilizzabile;
* il percorso con la griglia di battuta non cambia (sha identico).

python tests/test_material_use.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, contract, edl_plan  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0

STEP = 0.15
BLACK = 0.02
FLOOR = auto.IN_POINT_ABSOLUTE_FLOOR


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def profile(values, *, step: float = STEP, duration: float | None = None):
    samples = [
        {
            "t": round(index * step, 4),
            "sharpness": float(value),
            "luma": 0.5,
            "diff": 5.0,
        }
        for index, value in enumerate(values)
    ]
    return {
        "samples": samples,
        "step_sec": step,
        "duration": float(duration) if duration is not None else len(values) * step,
        "method": "laplacian_variance",
    }


def payload(entries, *, duration: float | None, bpm: float = 0.0):
    selected = [
        {
            "asset_id": asset_id,
            "role": role,
            "target_duration_sec": float(target),
            "type": "video",
        }
        for asset_id, role, target in entries
    ]
    scene = {"aspect_ratio": "9:16", "resolution": [1080, 1920]}
    if duration is not None:
        scene["duration_sec"] = float(duration)
    return {
        "contract_version": "1.5.0",
        "asset_triage": {"selected_assets": selected, "rejected_assets": []},
        "scene": scene,
        "beat_grid": {
            "enabled": bpm > 0.0,
            "mode": "on" if bpm > 0.0 else "off",
            "bpm": float(bpm),
            "subdivision": 1,
            "source": "test",
        },
    }


def build(entries, durations, *, quality=None, requested=None, fps=30.0, bpm=0.0):
    data = payload(entries, duration=requested, bpm=bpm)
    sources = {
        asset_id: edl_plan.SourceRef(
            path=f"/tmp/{asset_id}.mp4",
            sha256="a" * 64,
            duration=float(durations[asset_id]),
        )
        for asset_id, _role, _target in entries
    }
    media = {
        asset_id: {"duration": float(durations[asset_id])}
        for asset_id, _role, _target in entries
    }
    return contract.payload_to_edl_plan(
        data, sources, media, fps=fps, quality=quality
    )


def a_clips(plan):
    return [clip for track in plan.video_tracks for clip in track.clips if clip.layer == 0]


def plan_sha(plan) -> str:
    blob = json.dumps(plan.to_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# Il materiale reale del difetto: 4.65 s buoni nella prima clip (un solo run),
# 0.30 + 8.32 s nella seconda (due run separati da 0.60 s sotto il pavimento).
REAL_RUNS = {
    "clip_a": {
        "quality": "measured",
        "src_in": 4.05,
        "usable_start": 0.6,
        "usable_end": 5.25,
        "usable_runs": [{"start": 0.6, "end": 5.25, "sharpness": 183.0}],
        "sharpness": 183.0,
        "thresholds": {"absolute": FLOOR, "relative": 58.6, "preferred": 58.6},
    },
    "clip_b": {
        "quality": "measured",
        "src_in": 9.3,
        "usable_start": 0.75,
        "usable_end": 9.9702,
        "usable_runs": [
            {"start": 0.75, "end": 1.05, "sharpness": 5.1},
            {"start": 1.65, "end": 9.9702, "sharpness": 73.0},
        ],
        "sharpness": 73.0,
        "thresholds": {"absolute": FLOOR, "relative": 13.1, "preferred": 13.1},
    },
}

ENTRIES = [("clip_a", "a_roll", 5.262167), ("clip_b", "a_roll", 9.970189)]
DURATIONS = {"clip_a": 5.262167, "clip_b": 9.970189}


def within_any_run(start: float, end: float, runs) -> bool:
    return any(
        start >= float(run["start"]) - 1e-6 and end <= float(run["end"]) + 1e-6
        for run in runs
    )


def main() -> int:
    print("A. la scelta del punto di presa dichiara tutte le corse")

    rows = profile([1, 80, 90, 95, 1, 70, 75, 80, 85, 90, 95], duration=1.65)
    chosen = auto.choose_in_point(
        rows["samples"],
        black_luma=BLACK,
        freeze_seconds=1.5,
        duration=rows["duration"],
    )
    check(
        "choose_in_point porta usable_runs, non solo la finestra",
        isinstance(chosen.get("usable_runs"), list) and len(chosen["usable_runs"]) == 2,
        chosen.get("usable_runs"),
    )
    check(
        "le corse sono in ordine e non si sovrappongono",
        [run["start"] for run in chosen["usable_runs"]]
        == sorted(run["start"] for run in chosen["usable_runs"])
        and all(
            left["end"] <= right["start"] + 1e-9
            for left, right in zip(chosen["usable_runs"], chosen["usable_runs"][1:])
        ),
        [(run["start"], run["end"]) for run in chosen["usable_runs"]],
    )
    check(
        "in_point resta il picco della prima corsa preferita",
        abs(float(chosen["src_in"]) - 0.45) < 1e-6,
        chosen["src_in"],
    )

    print("\nB. il piano riempie la durata consumando le corse")

    plan = build(ENTRIES, DURATIONS, quality=REAL_RUNS, requested=10.0)
    clips = a_clips(plan)
    check(
        "richiesta 10 s: il piano consegna almeno 9.9 s",
        float(plan.duration_sec) >= 9.9,
        round(float(plan.duration_sec), 4),
    )
    check(
        "piu' di due segmenti: le corse successive non sono buttate",
        len(clips) >= 4,
        len(clips),
    )
    check(
        "la prima clip entra dal punto di presa preferito",
        abs(float(clips[0].src_in) - 4.05) < 1e-6,
        clips[0].src_in,
    )
    check(
        "il piano usa 3 corse distinte (clip_a + due di clip_b)",
        auto.plan_runs_used(plan, REAL_RUNS, {"clip_a": "clip_a", "clip_b": "clip_b"})
        == 3,
        auto.plan_runs_used(plan, REAL_RUNS, {"clip_a": "clip_a", "clip_b": "clip_b"}),
    )

    print("\nC. nessun src_in fuori dalle corse (S4)")

    inside = all(
        within_any_run(
            float(clip.src_in),
            float(clip.src_out),
            REAL_RUNS[asset_id]["usable_runs"],
        )
        for clip in clips
        for asset_id in [clip.source_id]
        if asset_id in REAL_RUNS
    )
    check(
        "ogni clip a-roll cade dentro una corsa utilizzabile",
        inside,
        [(round(float(c.src_in), 3), round(float(c.src_out), 3)) for c in clips],
    )
    check(
        "nessun src_out supera la durata della sorgente",
        all(float(clip.src_out) <= DURATIONS[clip.source_id] + 1e-6 for clip in clips),
    )

    print("\nD. coerenza dei numeri del report")

    material = auto.usable_material_sec(REAL_RUNS, [
        {"asset_id": "clip_a", "role": "a_roll", "target_duration_sec": 5.262167},
        {"asset_id": "clip_b", "role": "a_roll", "target_duration_sec": 9.970189},
    ], {})
    retained = float(plan.duration_sec) / material
    check(
        "materiale_sec e' la somma delle corse (13.27 s), non dei target (15.23 s)",
        abs(material - 13.2702) < 1e-3,
        round(material, 4),
    )
    check(
        "reachable vero => duration_retained >= 1 - max_removal",
        retained >= 1.0 - float(auto.IN_POINT_MAX_REMOVAL) - 1e-9,
        round(retained, 4),
    )
    tail = auto.timeline_declaration(10.0, plan.duration_sec, 30.0, material_sec=material)
    check(
        "la coda dichiara reachable true e nessuna 'insufficienza'",
        tail["tail"]["reachable"] is True
        and "materiale insufficiente" not in tail["tail"]["note"],
        tail["tail"],
    )
    check(
        "quando reachable e' vero la nota nomina il materiale utilizzabile",
        "materiale utilizzabile 13.270s" in tail["tail"]["note"],
        tail["tail"]["note"],
    )

    print("\nE. materiale davvero insufficiente: dichiarato, non mascherato (S3)")

    short_runs = {
        "short_1": {
            "quality": "measured",
            "src_in": 0.15,
            "usable_start": 0.15,
            "usable_end": 1.5,
            "usable_runs": [{"start": 0.15, "end": 1.5, "sharpness": 60.0}],
            "sharpness": 60.0,
            "thresholds": {"absolute": FLOOR, "relative": 20.0, "preferred": 20.0},
        },
        "short_2": {
            "quality": "measured",
            "src_in": 0.15,
            "usable_start": 0.15,
            "usable_end": 1.5,
            "usable_runs": [{"start": 0.15, "end": 1.5, "sharpness": 55.0}],
            "sharpness": 55.0,
            "thresholds": {"absolute": FLOOR, "relative": 18.0, "preferred": 18.0},
        },
    }
    short_entries = [("short_1", "a_roll", 10.0), ("short_2", "a_roll", 10.0)]
    short_plan = build(
        short_entries,
        {"short_1": 1.5, "short_2": 1.5},
        quality=short_runs,
        requested=10.0,
    )
    short_material = auto.usable_material_sec(
        short_runs,
        [
            {"asset_id": "short_1", "role": "a_roll", "target_duration_sec": 10.0},
            {"asset_id": "short_2", "role": "a_roll", "target_duration_sec": 10.0},
        ],
        {},
    )
    short_tail = auto.timeline_declaration(
        10.0, short_plan.duration_sec, 30.0, material_sec=short_material
    )
    check(
        "due clip da 1.5 s non arrivano a 10 s: reachable false",
        short_tail["tail"]["reachable"] is False,
        short_plan.duration_sec,
    )
    check(
        "la nota dice 'materiale utilizzabile X s, richiesti 10 s'",
        "materiale utilizzabile 2.700s, richiesti 10s" in short_tail["tail"]["note"],
        short_tail["tail"]["note"],
    )
    check(
        "il piano corto resta dentro le corse (nessuna invenzione)",
        all(
            within_any_run(
                float(clip.src_in),
                float(clip.src_out),
                short_runs[clip.source_id]["usable_runs"],
            )
            for clip in a_clips(short_plan)
        ),
        [(c.src_in, c.src_out) for c in a_clips(short_plan)],
    )

    print("\nF. la ricetta (griglia di battuta) non cambia")

    recipe_entries = [("clip_a", "a_roll", 5.262167), ("clip_b", "a_roll", 9.970189)]
    legacy = {
        asset_id: {key: value for key, value in row.items() if key != "usable_runs"}
        for asset_id, row in REAL_RUNS.items()
    }
    grid_new = build(recipe_entries, DURATIONS, quality=REAL_RUNS, bpm=100.0)
    grid_legacy = build(recipe_entries, DURATIONS, quality=legacy, bpm=100.0)
    check(
        "stesso piano della griglia con e senza usable_runs (sha identico)",
        plan_sha(grid_new) == plan_sha(grid_legacy),
        plan_sha(grid_new)[:12],
    )
    check(
        "la griglia resta oltre la richiesta e usa i suoi tagli",
        float(grid_new.duration_sec) > 0.0 and len(a_clips(grid_new)) > len(a_clips(plan)),
        (round(float(grid_new.duration_sec), 3), len(a_clips(grid_new))),
    )
    check(
        "la griglia conserva la finestra intera (13.87 s), non il solo picco",
        abs(float(grid_new.duration_sec) - 13.870189) < 0.01,
        round(float(grid_new.duration_sec), 4),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
