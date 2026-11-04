"""Il punto di presa si sceglie per misura, con la soglia della clip.

La prova a freddo aveva due difetti misurati: una clip a meta' timeline con un
fotogramma inutilizzabile e il primo fotogramma di entrambe le clip che era uno
zoom sfocato. Il primo correttivo usava la soglia **assoluta** del triage
(``triage_sharpness_low`` = 50) e ha svuotato il montaggio: su un 360x640 da
WhatsApp la varianza del Laplaciano sta sotto 50 quasi ovunque, e 15.2 s sono
diventati 1.87 s.

La regola giusta e' relativa alla clip:

* ``preferred`` = 35% del 75° percentile della curva (dichiarato per clip);
* ``absolute`` = pavimento nero/vetro (:data:`auto.IN_POINT_ABSOLUTE_FLOOR`);
* inutilizzabile solo cio' che sta sotto il pavimento (o e' nero/freeze);
* il filtro non toglie mai piu' del 25% di una clip: oltre, il tratto rientra
  dichiarato ``below_preferred``, e la timeline resta pubblicabile (>= 85%);
* il punto di presa resta il **picco di nitidezza** della parte preferita, la
  durata dei segmenti resta quella della griglia (dst invariati).

Senza profilo per-frame non si inventa niente: ``in_point: not_measured``.

python tests/test_frame_choice.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import types
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


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:62} {reason}")


def profile(
    values,
    *,
    step: float = STEP,
    luma: float = 0.5,
    diff: float = 5.0,
    duration: float | None = None,
):
    """Un profilo sintetico nella forma che ``measure_frame_profile`` produce."""
    samples = [
        {
            "t": round(index * step, 4),
            "sharpness": float(value),
            "luma": float(luma),
            "diff": float(diff),
        }
        for index, value in enumerate(values)
    ]
    total = float(duration) if duration is not None else len(values) * step
    return {
        "samples": samples,
        "step_sec": step,
        "duration": total,
        "method": "laplacian_variance",
    }


def choose(rows, **kwargs):
    params = {"black_luma": BLACK, "freeze_seconds": 1.5}
    params.update(kwargs)
    return auto.choose_in_point(rows["samples"], **params, duration=rows["duration"])


def payload(entries, *, bpm: float = 100.0, beat: bool = True):
    selected = []
    for asset_id, role, target in entries:
        entry = {
            "asset_id": asset_id,
            "role": role,
            "target_duration_sec": float(target),
        }
        if role == "a_roll":
            entry["type"] = "video"
        selected.append(entry)
    return {
        "contract_version": "1.5.0",
        "asset_triage": {"selected_assets": selected, "rejected_assets": []},
        "scene": {"aspect_ratio": "9:16", "resolution": [1080, 1920]},
        "beat_grid": {
            "enabled": bool(beat),
            "mode": "on" if beat else "off",
            "bpm": float(bpm),
            "subdivision": 1,
            "source": "test",
        },
    }


def build(entries, durations, *, quality=None, fps: float = 30.0, bpm: float = 100.0):
    data = payload(entries, bpm=bpm)
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


def visual_clips(plan):
    return [clip for track in plan.video_tracks for clip in track.clips]


def plan_sha(plan) -> str:
    blob = json.dumps(plan.to_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def main() -> int:
    print("A. soglia relativa alla clip")

    # Bassa nitidezza ma uniforme (360x640 da telefono): sotto 50 quasi tutto,
    # ma e' materiale buono per occhio: la soglia della clip lo accetta.
    uniform = profile([20, 22, 24, 26, 28, 30, 28, 26, 30, 32, 28, 24])
    uniform_choice = choose(uniform)
    check(
        "clip uniforme e bassa: quasi tutto utilizzabile, non scartata",
        uniform_choice is not None
        and float(uniform_choice["usable_fraction"]) >= 0.99,
        uniform_choice and uniform_choice["usable_fraction"],
    )
    check(
        "clip uniforme: la soglia preferita scende con la distribuzione",
        uniform_choice["thresholds"]["relative"] < 50.0
        and uniform_choice["thresholds"]["absolute"] == FLOOR,
        uniform_choice["thresholds"],
    )
    check(
        "clip uniforme e gia' buona in testa: punto invariato (piano identico)",
        uniform_choice["src_in"] == 0.0 and uniform_choice["moved"] is False,
        uniform_choice["src_in"],
    )

    # Zoom sfocato in testa, poi fuoco: il pavimento toglie solo la testa.
    blurry = profile(
        [1, 2, 3, 5, 8, 12, 20, 30, 45, 62, 85, 120, 160, 200, 180, 170, 160, 150,
         140, 130, 120, 110, 100, 90]
    )
    moved = choose(blurry)
    check(
        "inizio inutilizzabile: il punto va sul picco di nitidezza",
        moved["moved"] is True
        and abs(float(moved["sharpness"]) - 200.0) < 1e-6,
        (moved["src_in"], moved["sharpness"]),
    )
    check(
        "la testa tolta e' sotto il pavimento, non sotto la soglia del triage",
        abs(float(moved["usable_start"]) - 0.45) < 1e-6
        and float(moved["usable_start"]) < 2.1,
        moved["usable_start"],
    )
    check(
        "il pavimento assoluto e' molto basso (nero/vetro, non 'sfocato')",
        FLOOR <= 12.0,
        FLOOR,
    )

    print("\nB. mai svuotare: quota di scarto e below_preferred")

    # Il tratto inutilizzabile e' il 40% della clip: il filtro non puo' toglierlo
    # tutto, rientra dichiarato.
    head_heavy = profile([2, 3, 4] * 8 + [120, 130, 140, 150] * 3, duration=4.8)
    kept = choose(head_heavy)
    check(
        "scarto oltre il 25%: il materiale scartato rientra (finestra intera)",
        kept["usable_start"] == 0.0
        and abs(float(kept["usable_end"]) - 4.8) < 1e-6,
        (kept["usable_start"], kept["usable_end"]),
    )
    check(
        "lo scarto rientrato e' dichiarato, non nascosto",
        kept["kept_below_preferred"] is True and kept["src_in"] > 0.0,
        (kept["kept_below_preferred"], kept["src_in"]),
    )
    check(
        "la quota massima di scarto e' quella dichiarata",
        auto.IN_POINT_MAX_REMOVAL == 0.25,
        auto.IN_POINT_MAX_REMOVAL,
    )

    # Clip davvero inutilizzabile: tutto sotto il pavimento.
    check(
        "clip tutta sfocata (sotto il pavimento): esclusa",
        choose(profile([1, 2, 3, 4, 2, 3, 4])) is None,
    )
    check(
        "clip nera: esclusa anche se il Laplaciano e' alto",
        choose(profile([200, 200, 200, 200], luma=0.0)) is None,
    )
    frozen_rows = [
        {"t": index * STEP, "sharpness": 200.0, "luma": 0.5, "diff": 0.0}
        for index in range(16)
    ]
    check(
        "freeze lungo: escluso, non e' materiale",
        auto.choose_in_point(
            frozen_rows,
            black_luma=BLACK,
            freeze_seconds=1.5,
            duration=16 * STEP,
        )
        is None,
    )

    print("\nC. durata: il piano non si svuota")

    entries = [("clip_long", "a_roll", 10.0)]
    durations = {"clip_long": 10.0}
    base = build(entries, durations)
    measured = {
        "clip_long": {
            "quality": "measured",
            "src_in": 9.25,
            "usable_start": 1.2,
            "usable_end": 9.97,
            "sharpness": 75.0,
            "moved": True,
            "thresholds": {"absolute": FLOOR, "relative": 13.0, "preferred": 13.0},
            "why": "picco",
        }
    }
    windowed = build(entries, durations, quality=measured)
    check(
        "durata utile >= 85% del materiale ottenibile",
        float(windowed.duration_sec) >= 0.85 * float(base.duration_sec),
        (round(float(windowed.duration_sec), 3), round(float(base.duration_sec), 3)),
    )
    clips = visual_clips(windowed)
    check(
        "il primo segmento parte dal punto misurato",
        abs(float(clips[0].src_in) - 9.25) < 1e-6,
        clips[0].src_in,
    )
    check(
        "ogni segmento resta dentro la finestra utile",
        all(
            float(clip.src_in) >= 1.2 - 1e-6 and float(clip.src_out) <= 9.97 + 1e-6
            for clip in clips
        ),
        [(c.src_in, c.src_out) for c in clips[:3]],
    )
    check(
        "la durata dei segmenti resta quella pianificata (0.60 s)",
        all(abs(clip.duration - 0.6) < 1e-6 for clip in clips[:-1]),
        [round(c.duration, 3) for c in clips[:3]],
    )
    check(
        "i tagli non si muovono: dst identici al piano senza filtro",
        [(c.dst_in, c.dst_out) for c in clips[:-1]]
        == [(c.dst_in, c.dst_out) for c in visual_clips(base)[: len(clips) - 1]],
    )
    check(
        "finita la finestra si riparte dalla testa utile, non dallo scarto",
        any(abs(float(clip.src_in) - 1.2) < 1e-6 for clip in clips),
        [round(float(c.src_in), 2) for c in clips[:4]],
    )

    print("\nD. dichiarazioni: below_preferred, unusable, esclusi")

    segments = auto.segment_quality(windowed, measured)
    low = {
        "clip_long": {
            **measured["clip_long"],
            "samples": [
                {"t": 1.2, "sharpness": 11.0, "luma": 0.5, "diff": 5.0},
                {"t": 5.0, "sharpness": 11.0, "luma": 0.5, "diff": 5.0},
                {"t": 9.97, "sharpness": 75.0, "luma": 0.5, "diff": 5.0},
            ],
        }
    }
    low_segments = auto.segment_quality(windowed, low)
    check(
        "i segmenti che partono sotto la soglia preferita sono below_preferred",
        len(low_segments["below_preferred"]) > 0
        and len(low_segments["unusable"]) == 0,
        (len(low_segments["below_preferred"]), len(low_segments["unusable"])),
    )
    check(
        "nessun segmento parte sotto il pavimento",
        segments["unusable"] == [],
    )

    excluded_choice = {
        "clip_long": {
            "quality": "measured",
            "in_point": 0.0,
            "src_in": 0.0,
            "usable_start": 0.0,
            "usable_end": 10.0,
            "usable_fraction": 1.0,
            "sharpness": 120.0,
            "thresholds": {"absolute": FLOOR, "relative": 42.0, "preferred": 42.0},
        },
        "clip_dark": {
            "quality": "unusable",
            "src_in": 0.0,
            "usable_start": 0.0,
            "usable_end": 0.0,
            "sharpness": 6.0,
            "excluded": True,
            "thresholds": {"absolute": FLOOR, "relative": 5.0, "p75": 8.0},
            "why": "nessun istante sopra il pavimento",
        },
    }
    both = build(
        [("clip_long", "a_roll", 10.0), ("clip_dark", "a_roll", 4.0)],
        {"clip_long": 10.0, "clip_dark": 4.0},
        quality=excluded_choice,
    )
    check(
        "clip davvero inutilizzabile: esclusa, non 'recuperata' per fare durata",
        {clip.source_id for clip in visual_clips(both)} == {"clip_long"}
        and any("esclusa" in note and "clip_dark" in note for note in both.notes),
        [note for note in both.notes if "esclus" in note],
    )
    declared = auto.plan_declaration(
        excluded_choice,
        segments=low_segments,
        plan=windowed,
        material_sec=10.0,
    )
    check(
        "plan.excluded[] porta motivo e misura (pavimento + relativa)",
        declared["excluded_count"] == 1
        and declared["excluded"][0]["asset_id"] == "clip_dark"
        and declared["excluded"][0]["measure"]["threshold_absolute"] == FLOOR,
        declared["excluded"],
    )
    check(
        "plan.in_points[] porta soglia assoluta, relativa e frazione utile",
        declared["in_points"]
        and declared["in_points"][0]["threshold_relative"] == 42.0
        and declared["in_points"][0]["threshold_absolute"] == FLOOR
        and declared["in_points"][0]["usable_fraction"] == 1.0,
        declared["in_points"][0],
    )
    check(
        "plan.below_preferred[] e' contato",
        declared["below_preferred_count"] == len(low_segments["below_preferred"])
        and declared["below_preferred_count"] > 0,
        declared["below_preferred_count"],
    )
    check(
        "plan.duration_retained dice quanta durata resta",
        abs(float(declared["duration_retained"]) - 0.877) < 0.01,
        declared.get("duration_retained"),
    )

    print("\nE. nessuna regressione quando il materiale e' gia' buono")

    good = {
        "clip_long": {
            "quality": "measured",
            "src_in": 0.0,
            "usable_start": 0.0,
            "usable_end": 10.0,
            "sharpness": 150.0,
            "moved": False,
            "thresholds": {"absolute": FLOOR, "relative": 52.0, "preferred": 52.0},
        }
    }
    check(
        "materiale buono: sha del piano identico a prima",
        plan_sha(build(entries, durations, quality=good)) == plan_sha(base),
    )
    check(
        "nessuna qualita' passata: sha identico (comportamento di sempre)",
        plan_sha(build(entries, durations, quality=None)) == plan_sha(base),
    )
    absent = {
        "clip_long": {
            "quality": "not_measured",
            "src_in": 0.0,
            "usable_start": None,
            "usable_end": None,
            "sharpness": None,
            "excluded": False,
        }
    }
    check(
        "profilo assente: piano di sempre, dichiarato not_measured",
        plan_sha(build(entries, durations, quality=absent)) == plan_sha(base),
    )

    print("\nF. il probe dichiara, non inventa")

    staged = {
        "clip_a": types.SimpleNamespace(path="/tmp/non-esiste-a.mp4"),
        "clip_b": types.SimpleNamespace(path="/tmp/non-esiste-b.mp4"),
    }
    selection = [
        {"asset_id": "clip_a", "role": "a_roll"},
        {"asset_id": "clip_b", "role": "a_roll"},
    ]
    fake = {
        "/tmp/non-esiste-a.mp4": profile([1, 2, 3, 60, 90, 120, 140, 120, 100]),
        "/tmp/non-esiste-b.mp4": profile([1, 2, 3, 4, 2, 3, 4]),
    }
    choice = auto.frame_choice(
        selection,
        {},
        staged,
        black_luma=BLACK,
        freeze_seconds=1.5,
        measure=lambda path: fake.get(str(path).replace("\\", "/")),
    )
    check(
        "la clip misurata porta il punto scelto e la soglia relativa",
        choice["clip_a"]["quality"] == "measured"
        and choice["clip_a"]["src_in"] > 0
        and choice["clip_a"]["thresholds"]["relative"] > 0,
        choice["clip_a"]["src_in"],
    )
    check(
        "la clip senza istanti utili esce dal piano",
        choice["clip_b"]["excluded"] is True,
        choice["clip_b"]["quality"],
    )
    missing = auto.frame_choice(
        selection[:1],
        {},
        {"clip_a": types.SimpleNamespace(path="/tmp/non-esiste-c.mp4")},
        black_luma=BLACK,
        freeze_seconds=1.5,
        measure=lambda path: None,
    )
    check(
        "senza metriche non si inventa: not_measured, punto 0",
        missing["clip_a"]["quality"] == "not_measured"
        and missing["clip_a"]["src_in"] == 0.0
        and missing["clip_a"]["excluded"] is False,
        missing["clip_a"]["in_point"],
    )
    check(
        "measure_frame_profile su un file assente non inventa un profilo",
        auto.measure_frame_profile("/tmp/definitivamente-assente.mp4") is None,
    )
    opening = auto.opening_in_point(choice, "clip_a")
    check(
        "opening.in_point porta src_in, nitidezza, perche' e le due soglie",
        set(opening) >= {"src_in", "sharpness", "why"}
        and opening["sharpness"] is not None
        and opening["threshold_relative"] > 0
        and opening["threshold_absolute"] == FLOOR,
        {key: opening.get(key) for key in ("src_in", "sharpness")},
    )
    opening_missing = auto.opening_in_point(missing, "clip_a")
    check(
        "opening.in_point dichiara not_measured senza metriche",
        opening_missing["in_point"] == "not_measured"
        and "not_measured" in opening_missing["why"],
        opening_missing["in_point"],
    )
    check(
        "percentile: 75° della curva e' quello dichiarato",
        abs(auto.percentile([0, 10, 20, 30, 40], 0.75) - 30.0) < 1e-9,
        auto.percentile([0, 10, 20, 30, 40], 0.75),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
