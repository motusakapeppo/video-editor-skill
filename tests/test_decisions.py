"""Test delle decisioni: il perimetro (quando dedurre) e il registro (con che prova).

Nessun modello linguistico qui: regole deterministiche, cosi' il comportamento e'
riproducibile e una regressione si vede subito. Le asserzioni sono sul contratto,
non sulle parole. La seconda parte prova ``vx.decisions`` e le cinque voci che
``auto`` costruisce senza rete e senza render.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import auto, decide, decisions, estimate, serve  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:42} {detail}")


def codes(decision) -> list[str]:
    return [b.code for b in decision.blockers]


def _raises(label: str, call, needle: str = "") -> None:
    try:
        call()
    except decisions.DecisionError as exc:
        ok = needle in str(exc) if needle else True
        check(label, ok, str(exc))
    else:
        check(label, False, "nessun errore")


def _fake_plan():
    """Un piano minimo ma vero: tre clip, 30 s, 1080p30."""

    def clip(name: str, dst_in: float, dst_out: float, layer: int = 0):
        return SimpleNamespace(
            clip_id=name,
            source_id="s1",
            src_in=dst_in,
            src_out=dst_out,
            dst_in=dst_in,
            dst_out=dst_out,
            layer=layer,
            effects=(),
        )

    track = SimpleNamespace(
        clips=(clip("c1", 0.0, 10.0), clip("c2", 10.0, 20.0), clip("c3", 20.0, 30.0))
    )
    return SimpleNamespace(
        duration_sec=30.0,
        resolution=(1920, 1080),
        fps=30.0,
        aspect_ratio="16:9",
        video_tracks=(track,),
        sources={},
        notes=(),
    )


def registry_checks() -> None:
    print("\nregistro: una voce valida")
    entry = decisions.record(
        id="font",
        question="quale famiglia per un horror condensato?",
        chosen="Archivo Black",
        confidence="high",
        evidence=["larghezza condensed misurata 0.422 em", "energia 0.82"],
        alternatives=[{"option": "Bebas Neue", "why_not": "score 2.0 contro 4.0"}],
        falsified_if="il titolo risulta illeggibile a 1080p o fuori tono",
        by="auto",
    )
    check("voce valida conservata", entry["id"] == "font" and entry["chosen"] == "Archivo Black")
    check("evidenza intatta", len(entry["evidence"]) == 2)
    check("alternativa motivata", entry["alternatives"][0]["option"] == "Bebas Neue")
    check("fiducia alta ammessa su prova forte", entry["confidence"] == "high")
    check("forma completa", set(entry) == {
        "id", "question", "chosen", "confidence", "evidence",
        "alternatives", "alternatives_note", "falsified_if", "by",
    })

    print("\nregistro: i divieti")
    _raises(
        "senza evidence -> errore",
        lambda: decisions.record(
            id="x", question="q", chosen="c", evidence=[],
            falsified_if="f", confidence="low",
        ),
        "evidence",
    )
    _raises(
        "riga di evidence vuota -> errore",
        lambda: decisions.record(
            id="x", question="q", chosen="c", evidence=["ok 1", " "],
            falsified_if="f", confidence="low",
        ),
        "vuota",
    )
    _raises(
        "senza falsified_if -> errore",
        lambda: decisions.record(
            id="x", question="q", chosen="c", evidence=["score 4.0"],
            falsified_if="", alternatives_note="un solo candidato",
        ),
        "falsified_if",
    )
    _raises(
        "alternatives vuota senza nota -> errore",
        lambda: decisions.record(
            id="x", question="q", chosen="c", evidence=["score 4.0"],
            falsified_if="f", alternatives=[],
        ),
        "alternatives_note",
    )
    _raises(
        "alternativa senza why_not -> errore",
        lambda: decisions.record(
            id="x", question="q", chosen="c", evidence=["score 4.0"],
            falsified_if="f",
            alternatives=[{"option": "Bebas Neue"}],
        ),
        "why_not",
    )
    _raises(
        "by sconosciuto -> errore",
        lambda: decisions.record(
            id="x", question="q", chosen="c", evidence=["score 4.0"],
            falsified_if="f", by="robot", alternatives_note="un solo candidato",
        ),
        "by",
    )

    print("\nregistro: la fiducia segue l'evidenza")
    weak = decisions.record(
        id="x", question="q", chosen="c", evidence=["sembra adatto"],
        falsified_if="f", alternatives_note="un solo candidato",
    )
    check("evidenza debole senza fiducia -> low", weak["confidence"] == "low")
    _raises(
        "evidenza debole con high -> errore",
        lambda: decisions.record(
            id="x", question="q", chosen="c", evidence=["sembra adatto"],
            falsified_if="f", confidence="high",
            alternatives_note="un solo candidato",
        ),
        "low",
    )
    no_alternatives = decisions.record(
        id="x", question="q", chosen="c", evidence=["score 4.0"],
        falsified_if="f", alternatives=[],
        alternatives_note="un solo candidato utilizzabile",
    )
    check("alternatives vuota con nota -> valida", no_alternatives["alternatives"] == [])

    print("\nregistro: id unici")
    registry = decisions.Registry()
    registry.add(entry)
    _raises(
        "id ripetuto -> errore",
        lambda: registry.add(entry),
        "gia'",
    )
    check("registro con una voce", len(registry) == 1)

    print("\nun giro finto di auto: i cinque punti")
    with tempfile.TemporaryDirectory() as temporary:
        workdir = Path(temporary)
        plan = _fake_plan()

        def fake_font(root, requirement, energy, allow_network):
            return {
                "chosen": "Archivo Black",
                "pool": "locale",
                "candidates": [
                    {"family": "Archivo Black", "score": 4.0, "licence": "commercial"},
                    {"family": "Bebas Neue", "score": 2.0, "licence": "commercial"},
                ],
            }

        font_section = auto.font_gate(
            workdir,
            requirement="horror condensato",
            energy=0.82,
            wait=0.0,
            gate=False,
            propose=fake_font,
            notify=lambda _message: None,
        )

        def fake_keyframes(root, current_plan):
            return {
                "digest": "abcdef0123456789",
                "timeline_digest": "timeline-1",
                "count": 3,
                "frames": [
                    {"clip": "c1", "index": 0, "at": 7.5, "sha256": f"sha-{i}"}
                    for i in range(3)
                ],
            }

        key_section = auto.keyframe_gate(
            workdir,
            plan=plan,
            wait=0.0,
            gate=False,
            propose=fake_keyframes,
            notify=lambda _message: None,
        )

        # Lo stesso cancello del piano, con un tetto impossibile: scade e prende fast.
        plan_result = estimate.plan_gate(
            workdir,
            plan,
            budget=0.001,
            wait=0.0,
            gate=True,
            notify=lambda _message: None,
        )
        estimate_section = plan_result["estimate"]
        coherence_section = {
            "ok": False,
            "violations": [
                {"code": "variety_low", "evidence": "varieta' 0.33 sotto soglia 0.50"}
            ],
            "metrics": {
                "shots": 6,
                "variety": 0.33,
                "justified_cuts": 2,
                "cuts": 5,
                "unknown": [],
            },
        }
        bridge_summary = {
            "enabled": True,
            "cuts": 5,
            "reframe": 3,
            "restore": 1,
            "captions": 0,
        }
        payload = {
            "execution_context": {
                "creative_rationale": {
                    "genre_detected": "horror_promo",
                    "pacing": {"bpm_target": 120, "cut_policy": "beat_synced"},
                    "color_palette": {"type": "duotone"},
                }
            }
        }
        log = auto.build_decisions(
            font_section=font_section,
            key_section=key_section,
            plan_choice=plan_result["plan_choice"],
            estimate_section=estimate_section,
            coherence_section=coherence_section,
            bridge_summary=bridge_summary,
            plan=plan_result["plan"],
            payload=payload,
            requirement="horror condensato",
            energy=0.82,
        )
        by_id = {item["id"]: item for item in log}

        check("cinque voci dopo il giro", len(log) == 5, str(list(by_id)))
        check(
            "gli id richiesti",
            list(by_id) == ["font", "keyframes", "plan", "coherence", "effects"],
            str(list(by_id)),
        )
        check(
            "ogni voce passa validate",
            all(decisions.validate(item) == item for item in log),
        )
        check(
            "ogni voce ha prova e falsificazione",
            all(item["evidence"] and item["falsified_if"] for item in log),
        )
        check(
            "by e' sempre auto/user/timeout",
            all(item["by"] in decisions.DECIDERS for item in log),
        )
        check(
            "font con punteggio e licenza",
            by_id["font"]["chosen"] == "Archivo Black"
            and any("score" in row for row in by_id["font"]["evidence"])
            and any("licenza commercial" in row for row in by_id["font"]["evidence"]),
        )
        check(
            "font con l'alternativa e il suo perche'",
            by_id["font"]["alternatives"][0]["option"] == "Bebas Neue",
        )
        check(
            "keyframes con i tre quartili",
            "3 fotogrammi" in by_id["keyframes"]["chosen"]
            and any("25/50/75" in row for row in by_id["keyframes"]["evidence"]),
        )
        check(
            "plan fast per timeout",
            by_id["plan"]["chosen"].startswith("fast")
            and by_id["plan"]["by"] == "timeout",
        )
        check("plan low quando scade", by_id["plan"]["confidence"] == "low")
        check(
            "coerenza: accettare e dichiarare",
            "accettare" in by_id["coherence"]["chosen"]
            and any("variety_low" in row for row in by_id["coherence"]["evidence"]),
        )
        check(
            "effetti: composizione e costo",
            "reframe 3" in by_id["effects"]["chosen"]
            and any("costo effetti" in row for row in by_id["effects"]["evidence"]),
        )
        check(
            "effetti: la tesi entra nella prova",
            any("120 bpm" in row for row in by_id["effects"]["evidence"]),
        )

        print("\nla forma non cambia con chi decide")
        shapes = {
            tuple(sorted(decisions.record(
                id=f"x-{by}", question="q", chosen="c", evidence=["score 4.0"],
                falsified_if="f", by=by, alternatives_note="un solo candidato",
            )))
            for by in decisions.DECIDERS
        }
        check("stessa forma per auto/user/timeout", len(shapes) == 1, str(shapes))

        print("\nserve espone il registro")
        job = workdir / "job"
        job.mkdir()
        (job / "report.json").write_text(
            json.dumps({"decisions": log}, ensure_ascii=False), encoding="utf-8"
        )
        served = serve.state(workdir)
        check("decisions nella lista di stato", isinstance(served["decisions"], list))
        check("la lista non e' vuota", len(served["decisions"]) == 5)
        check("il primo id e' font", served["decisions"][0]["id"] == "font")
        empty = workdir / "vuoto"
        empty.mkdir()
        check("senza lavoro: lista vuota", serve.state(empty)["decisions"] == [])
        bare = workdir / "bare"
        bare.mkdir()
        (bare / "report.json").write_text("{}", encoding="utf-8")
        check(
            "report senza registro: lista vuota",
            serve._job_state(bare)["decisions"] == [],
        )


def main() -> int:
    print("richiesta ambigua: reel vintage con transizioni dinamiche")
    d = decide.interpret(
        "un reel con stile vintage e transizioni dinamiche", assets=["Antonio", "Inter"]
    )
    check("contratto NON chiuso", not d.ok)
    check("blocca vintage", "vague_vintage" in codes(d), str(codes(d)))
    check("blocca dinamiche (plurale)", "vague_dinamic" in codes(d), str(codes(d)))
    check(
        "deduce 9:16 da 'reel'",
        d.deduced.get("aspect") == "9:16",
        str(d.deduced.get("aspect")),
    )
    check(
        "deduce la sovrapposizione",
        (d.deduced.get("transitions") or {}).get("model") == "overlap",
    )
    check("ogni blocco ha opzioni", all(b.options for b in d.blockers))

    print("\nrichiesta chiusa: vlog con ombra gaussiana e font dichiarato")
    c = decide.interpret(
        "un vlog con ombra gaussiana, transizioni, font Antonio",
        assets=["Antonio", "Inter"],
    )
    check("contratto chiuso", c.ok, str(codes(c)))
    check("deduce 16:9 da 'vlog'", c.deduced.get("aspect") == "16:9")
    check(
        "engine scelto senza chiedere",
        c.deduced.get("engine") == "pillow",
        str(c.deduced.get("engine")),
    )
    check(
        "grade neutro di default",
        c.contract.get("grade") == {"type": "none"},
        str(c.contract.get("grade")),
    )

    print("\nasset non in registro")
    a = decide.interpret("reel con font BebasNeue e placca", assets=["Antonio"])
    check("blocca il font mancante", "asset_missing" in codes(a), str(codes(a)))
    check(
        "propone un fallback tracciato",
        any("sostituzione" in o for b in a.blockers for o in b.options),
    )

    print("\nrichiesta senza formato")
    f = decide.interpret("montaggio con duotone")
    check("blocca il formato mancante", "format_missing" in codes(f), str(codes(f)))

    print("\ntransizioni senza handle")
    h = decide.interpret("reel con transizioni", has_handles=False)
    check(
        "blocca la sovrapposizione impossibile", "no_handles" in codes(h), str(codes(h))
    )

    print("\ncapacita' che impone il motore")
    e = decide.interpret("reel con backdrop-filter")
    check(
        "CSS impone chromium",
        e.deduced.get("engine") == "chromium",
        str(e.deduced.get("engine")),
    )

    registry_checks()

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
