"""La risposta ha una conseguenza: chi decide, e cosa cambia davvero.

    python tests/test_answers_consequence.py

Tre domande a cui il test risponde con i fatti, non con le intenzioni:

A. la prima domanda (presente/automatico) si legge da flag, dalla richiesta o
   dalla vista, e in automatico nessuna delle tre domande viene posta;
B. il formato scelto nella vista cambia il canvas, e in "fai tutto tu" il canvas
   segue il materiale misurato invece di un default;
C. la risposta sull'hook scrive o non scrive la card, e la risposta sui
   sottotitoli sceglie anche lo stile (karaoke/clean), non solo acceso/spento.

Le funzioni sono quelle vere: se domani qualcuno le cambia, questo file si
accorge. Il giro completo (master e pixel) lo coprono i test end-to-end.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, decide  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def landscape() -> list:
    return [NS(width=1920, height=1080), NS(width=1280, height=720)]


def portrait() -> list:
    return [NS(width=1080, height=1920)]


def main() -> int:
    print("\n-- A: chi decide")
    check(
        "«fai tutto tu» = automatico",
        auto.presence_from_prompt("un reel di 10 secondi, fai tutto tu")
        == ("automatico", "fai tutto tu"),
        auto.presence_from_prompt("fai tutto tu"),
    )
    check(
        "«voglio scegliere io» = presente",
        auto.presence_from_prompt("voglio scegliere io i dettagli")[0] == "presente",
        auto.presence_from_prompt("voglio scegliere io"),
    )
    check(
        "una richiesta neutra non decide niente",
        auto.presence_from_prompt("un reel di 10 secondi") == ("", ""),
        auto.presence_from_prompt("un reel di 10 secondi"),
    )

    base = dict(prompt="un video di 10 secondi", presence=None)
    by_prompt = auto.resolve_presence(
        NS(**{**base, "prompt": "fai tutto tu, un video di 10 secondi"}),
        ROOT,
        watched=False,
    )
    check(
        "la richiesta vince senza chiedere",
        by_prompt["value"] == "automatico" and by_prompt["source"] == "prompt",
        (by_prompt["value"], by_prompt["source"]),
    )
    by_flag = auto.resolve_presence(
        NS(**{**base, "presence": "presente"}), ROOT, watched=False
    )
    check(
        "il flag vince sulla richiesta",
        by_flag["value"] == "presente" and by_flag["answered_by"] == "flag",
        (by_flag["value"], by_flag["answered_by"]),
    )
    by_default = auto.resolve_presence(NS(**base), ROOT, watched=False)
    check(
        "senza nessuno che guarda: automatico, dichiarato",
        by_default["value"] == "automatico"
        and by_default["source"] == "default"
        and by_default["asked"] is False,
        (by_default["value"], by_default["source"]),
    )
    check(
        "le tre domande non poste sono dichiarate, non inventate",
        all(item["asked"] is False for item in auto._auto_question_entries("x")),
        [item["asked"] for item in auto._auto_question_entries("x")],
    )

    print("\n-- B: il formato cambia il canvas")
    answered = [
        {"id": "target_format", "value": "16:9", "answered_by": "user"},
        {"id": "hook", "value": "auto", "answered_by": "user"},
        {"id": "subtitles", "value": "clean", "answered_by": "user"},
    ]
    choice = auto._target_format_choice(answered, NS(presence_mode="presente"), [])
    check(
        "una scelta umana vince e dice la fonte",
        (choice or {}).get("aspect") == "16:9" and choice.get("source") == "vista",
        choice,
    )
    auto_mode = NS(presence_mode="automatico", presence_source="flag", prompt="un video")
    derived = auto._target_format_choice(
        auto._auto_question_entries("x"), auto_mode, landscape()
    )
    check(
        "in «fai tutto tu» il canvas segue il materiale",
        (derived or {}).get("aspect") == "16:9" and "orizzontali" in derived["why"],
        derived,
    )
    vertical = auto._target_format_choice(
        auto._auto_question_entries("x"), auto_mode, portrait()
    )
    check(
        "materiale verticale: 9:16",
        (vertical or {}).get("aspect") == "9:16",
        vertical,
    )
    implicit = NS(presence_mode="automatico", presence_source="default", prompt="un video")
    check(
        "il default implicito non cambia il canvas",
        auto._target_format_choice(auto._auto_question_entries("x"), implicit, landscape())
        is None,
        "",
    )
    named = NS(presence_mode="automatico", presence_source="flag", prompt="un video per youtube")
    check(
        "se la richiesta nomina la piattaforma, non si sovrascrive",
        auto._target_format_choice(auto._auto_question_entries("x"), named, landscape())
        is None,
        "",
    )

    payload = decide.parse_and_decide("un video di 6 secondi", aspect="16:9")
    check(
        "decide nasce col canvas scelto",
        payload["scene"]["aspect_ratio"] == "16:9"
        and list(payload["scene"]["resolution"]) == [1920, 1080],
        (payload["scene"]["aspect_ratio"], payload["scene"]["resolution"]),
    )
    square = decide.parse_and_decide("un video di 6 secondi", aspect="1:1")
    check(
        "e con 1:1 i due lati sono uguali",
        list(square["scene"]["resolution"]) == [1080, 1080],
        square["scene"]["resolution"],
    )
    try:
        decide.parse_and_decide("un video di 6 secondi", aspect="21:9")
    except Exception as exc:  # noqa: BLE001 - un aspetto inventato si rifiuta
        refused = type(exc).__name__
    else:
        refused = ""
    check("un aspetto che non esiste si rifiuta", bool(refused), refused)

    print("\n-- C: hook e sottotitoli")
    brief = auto.textbrief.parse_brief_text("un video di 6 secondi")
    text_brief, _ = auto._apply_hook_answer(brief, {}, "nessuno", None)
    check(
        "«nessuno» non scrive la card e non la dichiara mancante",
        text_brief.asks_hook is False and text_brief.hook_text == "",
        (text_brief.asks_hook, text_brief.hook_text),
    )
    forced, sources = auto._apply_hook_answer(
        brief,
        {},
        "testo",
        NS(captions=[{"text": "questa è la prima frase del parlato vero"}]),
    )
    check(
        "«testo» usa le parole vere, non una frase inventata",
        forced.asks_hook is True
        and forced.hook_text.startswith("questa è la prima frase"),
        (forced.asks_hook, forced.hook_text, sources.get("hook_answer")),
    )
    empty, empty_sources = auto._apply_hook_answer(brief, {}, "testo", None)
    check(
        "«testo» senza frasi: lo dichiara, non inventa",
        empty.asks_hook is True
        and empty.hook_text == ""
        and "nessuna frase" in str(empty_sources.get("hook_answer")),
        empty_sources.get("hook_answer"),
    )
    untouched, _ = auto._apply_hook_answer(brief, {}, "auto", None)
    check("«auto» non tocca il brief", untouched == brief)

    print("\n-- D: la dichiarazione non si autoincorona")
    entries = [
        {"id": "target_format", "answered_by": "user", "asked": True},
        {"id": "hook", "answered_by": "user", "asked": True},
        {"id": "subtitles", "answered_by": "auto", "asked": False},
    ]
    auto._mark_applied(entries, "target_format", "canvas 16:9 dalla vista", value="16:9")
    unapplied = auto.declare_question_answers(entries)
    check(
        "chi ha applicato lo dice il report, non la dichiarazione",
        entries[0]["applied"] is True and entries[0]["why"] == "canvas 16:9 dalla vista",
        entries[0],
    )
    check(
        "l'hook non applicato resta senza effetto dichiarato",
        unapplied == ["hook"] and entries[1]["applied"] is False,
        unapplied,
    )
    check(
        "una domanda non posta non e' un fallimento",
        entries[2]["applied"] is False
        and "nessuno ha chiesto" in str(entries[2]["why"]),
        entries[2]["why"],
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
