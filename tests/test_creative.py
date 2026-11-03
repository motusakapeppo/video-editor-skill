"""Punto 3: due piani leciti, un punteggio, una scelta riproducibile.

Cosa si prova, tutto senza rete e senza render:

* ``candidates`` produce varianti diverse ma entrambe valide (``validate()``
  vuoto), con tesi e cambi dichiarati; ``n=1`` non e' un errore;
* ``score`` penalizza le violazioni di coerenza, il costo e lo scostamento di
  durata, e la novita' e' zero quando non c'e' l'altro candidato;
* ``choose`` prende il punteggio piu' alto, dice quali componenti hanno deciso,
  e a parita' vince il primo;
* la stessa entrata produce la stessa uscita, eseguita due volte.

Le funzioni del modulo sono pure: qui non si tocca il filesystem, non si apre
una socket e non si guarda l'orologio.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, creative, edl_plan  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def make_plan(
    durations: Sequence[float],
    *,
    sources: int = 6,
    with_audio: bool = True,
    source_capacity: float = 60.0,
) -> edl_plan.EDLPlan:
    """Un piano minimo e valido: un clip per durata, stessa griglia in audio."""
    refs = {
        f"s{index}": edl_plan.SourceRef(
            path=f"src{index}.mp4",
            sha256=("%02x" % index) * 32,
            duration=source_capacity,
        )
        for index in range(sources)
    }
    clips = []
    audio = []
    cursor = 0.0
    for index, duration in enumerate(durations):
        source_id = f"s{index % sources}"
        clips.append(
            edl_plan.ClipSegment(
                clip_id=f"v0_{index}",
                source_id=source_id,
                src_in=0.0,
                src_out=float(duration),
                dst_in=cursor,
                dst_out=cursor + float(duration),
                layer=0,
                role="main_talking_head",
            )
        )
        audio.append(
            edl_plan.ClipSegment(
                clip_id=f"a0_{index}",
                source_id=source_id,
                src_in=0.0,
                src_out=float(duration),
                dst_in=cursor,
                dst_out=cursor + float(duration),
                layer=0,
                role="dialogue",
            )
        )
        cursor += float(duration)
    audio_tracks = (
        (edl_plan.Track(track_id=0, clips=tuple(audio), role="dialogue"),)
        if with_audio
        else ()
    )
    return edl_plan.EDLPlan(
        fps=30.0,
        duration_sec=cursor,
        aspect_ratio="9:16",
        resolution=(1080, 1920),
        sources=refs,
        video_tracks=(
            edl_plan.Track(track_id=0, clips=tuple(clips), role="a_roll"),
        ),
        audio_tracks=audio_tracks,
    )


def signature(plan: Any) -> List[Sequence[Any]]:
    return [list(row) for row in creative._signature(plan)]


def slot_source(plan: Any, index: int) -> str:
    clips = list(plan.video_tracks[0].clips)
    return str(clips[index].source_id)


def test_candidates() -> None:
    print("\n-- candidates: due varianti lecite e diverse")
    # [3,3,3,6]: c'e' una coppia non consecutiva di pari durata (slot 0 e 2)
    # e una coda piu' lunga della mediana: entrambe le trasformazioni esistono.
    plan = make_plan([3.0, 3.0, 3.0, 6.0])
    variants = creative.candidates(plan)
    ids = [item["id"] for item in variants]
    check("la base e' valida", plan.validate() == [], plan.validate())
    check("due candidati", len(variants) == 2, ids)
    check("il primo e' la base", ids[0] == "base", ids)
    check("la seconda e' una variante", ids[1] in ("swap", "tail"), ids)
    check(
        "ogni variante ha tesi e cambi",
        all(item["thesis"] and isinstance(item["changes"], list) for item in variants),
        [(item["id"], item["thesis"]) for item in variants],
    )
    check(
        "ogni variante e' lecita (validate vuoto)",
        all(item["plan"].validate() == [] for item in variants),
        [item["plan"].validate() for item in variants],
    )
    check(
        "le varianti differiscono davvero",
        signature(variants[0]["plan"]) != signature(variants[1]["plan"]),
    )
    swap = next((item for item in variants if item["id"] == "swap"), None)
    check("lo swap c'e'", swap is not None, ids)
    if swap is not None:
        swapped = swap["plan"]
        check(
            "lo swap non cambia la durata",
            abs(swapped.duration_sec - plan.duration_sec) < 1e-9,
            (plan.duration_sec, swapped.duration_sec),
        )
        check(
            "lo swap cambia il contenuto dello slot",
            slot_source(swapped, 0) != slot_source(plan, 0),
            (slot_source(plan, 0), slot_source(swapped, 0)),
        )
        check(
            "l'audio dialogo segue lo swap (A/V insieme)",
            slot_source(swapped, 0)
            == str(swapped.audio_tracks[0].clips[0].source_id),
        )
        check(
            "i clip video restano quelli dello slot (clip_id fermo)",
            [clip.clip_id for clip in swapped.video_tracks[0].clips]
            == [clip.clip_id for clip in plan.video_tracks[0].clips],
        )

    check(
        "n=1 restituisce un solo candidato",
        len(creative.candidates(plan, n=1)) == 1,
        len(creative.candidates(plan, n=1)),
    )
    check(
        "n=1 non solleva e tiene la base",
        creative.candidates(plan, n=1)[0]["id"] == "base",
    )
    check(
        "n=0 viene trattato come n=1",
        len(creative.candidates(plan, n=0)) == 1,
        len(creative.candidates(plan, n=0)),
    )


def test_tail_bounds() -> None:
    print("\n-- la variante di coda resta nei limiti")
    plan = make_plan([2.0, 4.0, 3.0, 8.0])
    tail = next(
        (item for item in creative.candidates(plan) if item["id"] == "tail"), None
    )
    check("la coda e' una variante disponibile", tail is not None)
    if tail is None:
        return
    variant = tail["plan"]
    last = variant.video_tracks[0].clips[-1]
    check("il piano di coda e' valido", variant.validate() == [], variant.validate())
    check(
        "il cambio di coda non supera mezzo secondo",
        abs(variant.duration_sec - plan.duration_sec) <= creative.MAX_TAIL_DELTA_SEC + 1e-9,
        abs(variant.duration_sec - plan.duration_sec),
    )
    check(
        "nessuno shot sotto il minimo",
        last.duration >= creative.MIN_SHOT_SEC - 1e-9,
        last.duration,
    )
    check(
        "src e dst dell'ultimo clip restano lunghi uguali",
        abs((last.src_out - last.src_in) - (last.dst_out - last.dst_in)) < 1e-9,
    )
    check(
        "lo src_out non esce dalla sorgente",
        last.src_out <= plan.sources[last.source_id].duration + 1e-9,
        (last.src_out, plan.sources[last.source_id].duration),
    )


def test_score() -> None:
    print("\n-- score: coerenza, prompt, costo, novita'")
    plan = make_plan([3.0, 3.0, 3.0, 6.0])
    other = make_plan([3.0, 3.0, 3.0, 5.5])
    estimate = {"seconds": 60.0, "output_sec": plan.duration_sec}
    clean = {"ok": True, "violations": []}
    dirty = {
        "ok": False,
        "violations": [
            {"code": "variety_low"},
            {"code": "unjustified_cut"},
        ],
    }
    requirements = {"duration_sec": plan.duration_sec}

    good = creative.score(plan, clean, estimate, requirements)
    bad = creative.score(plan, dirty, estimate, requirements)
    check(
        "le parti sono tutte dichiarate",
        set(good["parts"]) == {"coherence", "prompt", "cost", "novelty"},
        sorted(good["parts"]),
    )
    check("il totale sta in 0..1", 0.0 <= good["total"] <= 1.0, good["total"])
    check(
        "due violazioni abbassano il punteggio",
        bad["total"] < good["total"],
        (good["total"], bad["total"]),
    )
    check(
        "la coerenza scende di 0.25 per violazione",
        abs(good["parts"]["coherence"] - bad["parts"]["coherence"] - 0.5) < 1e-9,
        (good["parts"]["coherence"], bad["parts"]["coherence"]),
    )
    four = {"ok": False, "violations": [{"code": f"c{index}"} for index in range(4)]}
    check(
        "quattro violazioni azzerano la coerenza",
        creative.score(plan, four, estimate, requirements)["parts"]["coherence"] == 0.0,
    )
    check(
        "novita' zero senza l'altro candidato",
        good["parts"]["novelty"] == 0.0,
        good["parts"]["novelty"],
    )
    with_other = creative.score(plan, clean, estimate, requirements, other=other)
    check(
        "novita' positiva contro un altro piano",
        with_other["parts"]["novelty"] > 0.0,
        with_other["parts"]["novelty"],
    )
    slower = creative.score(plan, clean, {"seconds": 240.0, "output_sec": plan.duration_sec}, requirements)
    check(
        "un costo stimato maggiore abbassa la parte costo",
        slower["parts"]["cost"] < good["parts"]["cost"],
        (good["parts"]["cost"], slower["parts"]["cost"]),
    )
    near = creative.score(plan, clean, estimate, {"duration_sec": plan.duration_sec})
    far = creative.score(plan, clean, estimate, {"duration_sec": plan.duration_sec * 2.0})
    check(
        "la durata chiesta entra nell'aderenza",
        far["parts"]["prompt"] < near["parts"]["prompt"],
        (near["parts"]["prompt"], far["parts"]["prompt"]),
    )
    check(
        "durata esatta -> aderenza piena",
        abs(near["parts"]["prompt"] - 1.0) < 1e-9,
        near["parts"]["prompt"],
    )
    chorus = creative.score(
        plan,
        clean,
        estimate,
        {"duration_sec": plan.duration_sec, "chorus_required": True, "chorus_present": False},
    )
    check(
        "un coro richiesto e assente abbassa l'aderenza",
        chorus["parts"]["prompt"] < near["parts"]["prompt"],
        (near["parts"]["prompt"], chorus["parts"]["prompt"]),
    )
    only_cost = creative.score(
        plan, clean, estimate, requirements, weights={"coherence": 0, "prompt": 0, "cost": 1, "novelty": 0}
    )
    check(
        "i pesi si possono cambiare (uno solo vale 1)",
        abs(only_cost["total"] - only_cost["parts"]["cost"]) < 1e-9,
        (only_cost["total"], only_cost["parts"]["cost"]),
    )
    check(
        "il totale e' la media pesata rinormalizzata",
        abs(good["total"] - sum(
            creative.WEIGHTS[key] * good["parts"][key] for key in creative.WEIGHTS
        ) / sum(creative.WEIGHTS.values())) < 1e-6,
        good["total"],
    )
    disabled = creative.score(plan, {"disabled": True, "violations": []}, estimate, requirements)
    check(
        "un referto spento non e' un via libera (0.5)",
        disabled["parts"]["coherence"] == 0.5,
        disabled["parts"]["coherence"],
    )


def test_choose() -> None:
    print("\n-- choose: il piu' alto, il motivo, la parita'")
    base = {"id": "base", "score": {"total": 0.61, "parts": {"coherence": 1.0, "prompt": 0.4}}}
    tail = {"id": "tail", "score": {"total": 0.72, "parts": {"coherence": 1.0, "prompt": 0.9}}}
    picked = creative.choose([base, tail])
    check("sceglie il punteggio piu' alto", picked["chosen"] == "tail", picked["chosen"])
    check(
        "il motivo nomina la componente che ha deciso",
        "prompt" in picked["why"],
        picked["why"],
    )
    check(
        "i punteggi di tutti ci sono",
        set(picked["scores"]) == {"base", "tail"},
        sorted(picked["scores"]),
    )
    tie = creative.choose([base, dict(base, id="copia")])
    check("a parita' vince il primo", tie["chosen"] == "base", tie["chosen"])
    check("la parita' si dichiara", "parita" in tie["why"], tie["why"])
    only = creative.choose([base])
    check("un solo candidato: vince per definizione", only["chosen"] == "base")
    check("un solo candidato: motivo esplicito", "un solo" in only["why"], only["why"])


def test_end_to_end() -> None:
    print("\n-- dalla generazione alla scelta, due volte")
    check_plan = make_plan([2.0, 4.0, 3.0, 8.0])
    estimate = {"seconds": 80.0, "output_sec": check_plan.duration_sec}
    requirements = {"duration_sec": 16.5}
    clean = {"ok": True, "violations": []}

    def run() -> Dict[str, Any]:
        variants = creative.candidates(check_plan)
        scored = creative.score_variants(variants, clean, estimate, requirements)
        pick = creative.choose(scored)
        return {
            "ids": [item["id"] for item in scored],
            "theses": [item["thesis"] for item in scored],
            "totals": [item["total"] for item in scored],
            "scores": [item["score"] for item in scored],
            "signatures": [signature(item["plan"]) for item in scored],
            "chosen": pick["chosen"],
            "why": pick["why"],
        }

    first = run()
    second = run()
    check(
        "stessa entrata, stessa uscita (JSON identico)",
        json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True),
    )
    check("il giro sceglie un id fra i candidati", first["chosen"] in first["ids"])
    check(
        "il punteggio premia la durata chiesta",
        first["chosen"] == "tail",
        (first["ids"], first["totals"]),
    )
    check(
        "tutti i punteggi sono finiti",
        all(isinstance(total, float) for total in first["totals"]),
        first["totals"],
    )


def test_decision() -> None:
    print("\n-- la voce di registro che accompagna la scelta")
    section = {
        "chosen": "tail",
        "thesis": "accorcia la coda",
        "why": "tail vince su base per prompt",
        "scores": {
            "base": {"total": 0.6, "parts": {"coherence": 1.0, "prompt": 0.5}},
            "tail": {"total": 0.7, "parts": {"coherence": 1.0, "prompt": 0.9}},
        },
        "variants": [
            {"id": "base", "thesis": "invariato", "changes": [], "total": 0.6},
            {"id": "tail", "thesis": "accorcia la coda", "changes": ["durata"], "total": 0.7},
        ],
        "captions": False,
    }
    entry = auto._decision_creative(section)
    check("id creative", entry["id"] == "creative", entry["id"])
    check("passa il contratto del registro", auto.decisions.validate(entry) == entry)
    check(
        "la prova porta i punteggi",
        any("punteggio" in row for row in entry["evidence"]),
        entry["evidence"],
    )
    check(
        "l'alternativa e' l'altro candidato",
        entry["alternatives"][0]["option"] == "base",
        entry["alternatives"],
    )
    single = auto._decision_creative(
        {
            "chosen": "base",
            "thesis": "invariato",
            "scores": {"base": {"total": 0.5, "parts": {}}},
            "variants": [{"id": "base", "thesis": "invariato", "changes": [], "total": 0.5}],
            "captions": True,
        }
    )
    check(
        "un solo candidato: la nota dice perche' (sottotitoli)",
        "sottotitoli" in single["alternatives_note"],
        single["alternatives_note"],
    )


def main() -> int:
    print("==", Path(__file__).name)
    test_candidates()
    test_tail_bounds()
    test_score()
    test_choose()
    test_end_to_end()
    test_decision()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
