"""Il piano-scaletta: sezioni compilate con le misure, e nessuna competenza spenta.

    python tests/test_sections.py

S1  ogni sezione compilata porta capability, binding e un perche' con un numero;
S2  una sezione senza misura non risulta compilata (missing), non si inventa;
S3  nessuna competenza citata e' sconosciuta (niente orfani);
S4  le competenze citate sono un sottoinsieme di quelle che il lavoro ha davvero
    usato: la scaletta non puo' chiamare cose che non esistono;
S5  un report vuoto non produce sezioni compilate e non solleva;
S6  le sezioni sono sempre le sei dichiarate, in ordine.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import sections  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def full_report() -> dict:
    return {
        "measured": {"duration_sec": 12.508, "clips": 3},
        "opening": {"chosen": {"asset_id": "a.mp4", "sharpness": 82.15}, "threshold": 50.0},
        "coherence": {"metrics": {"shots": 6}},
        "bridge": {"cuts": 2},
        "typography": {},
        "texts": ["STASERA TUTTO CAMBIA", "SCRIVICI IN DM"],
        "faces": {"speakers": 1},
        "selected": [
            {"asset_id": "a.mp4", "role": "a_roll"},
            {"asset_id": "b.mp4", "role": "b_roll"},
        ],
    }


def test_filled_sections_have_number_and_capability() -> None:
    print("\n1. ogni sezione compilata ha capability, binding e un numero")
    block = sections.compile_sections(full_report())
    filled = [row for row in block["sections"] if row["state"] == "filled"]
    check("S1 ci sono sezioni compilate", len(filled) >= 4, len(filled))
    check(
        "S1 tutte hanno capability, binding e perche'",
        all(
            row["capability"] and row["binding"] not in (None, {}) and row["why"]
            for row in filled
        ),
        [(row["id"], row["capability"]) for row in filled],
    )
    check(
        "S1 il perche' porta un numero",
        all(any(char.isdigit() for char in str(row["why"])) for row in filled),
        [row["why"][:40] for row in filled],
    )


def test_missing_is_declared() -> None:
    print("\n2. senza misura la sezione non risulta compilata")
    block = sections.compile_sections({"measured": {"clips": 2}, "opening": {}})
    hook = next(row for row in block["sections"] if row["id"] == "hook")
    check("S2 hook e' missing, non filled", hook["state"] == "missing", hook["state"])
    check("S2 e dice perche'", "misurata" in hook["why"], hook["why"])
    # Ma se il testo di apertura c'e', l'hook e' compilato: prima un video con
    # l'hook scritto veniva bocciato per "manca l'hook".
    with_text = sections.compile_sections(
        {"measured": {"clips": 2}, "opening": {}, "texts": ["STASERA TUTTO CAMBIA"]}
    )
    hook_text = next(row for row in with_text["sections"] if row["id"] == "hook")
    check(
        "S2 con il testo di apertura l'hook e' compilato (typography)",
        hook_text["state"] == "filled" and hook_text["capability"] == "typography",
        (hook_text["state"], hook_text["capability"]),
    )
    check(
        "S2 e il perche' porta un numero (i caratteri)",
        any(char.isdigit() for char in str(hook_text["why"])),
        hook_text["why"],
    )


def test_no_orphan_capabilities() -> None:
    print("\n3. nessuna competenza sconosciuta")
    block = sections.compile_sections(full_report())
    check("S3 unknown vuoto", block["unknown_capabilities"] == [], block["unknown_capabilities"])
    check(
        "S3 tutte le capability citate esistono",
        set(block["capabilities"]) <= set(sections.CAPABILITIES),
        block["capabilities"],
    )


def test_capabilities_are_the_real_ones() -> None:
    print("\n4. le capability citate sono quelle che il lavoro ha usato")
    block = sections.compile_sections(full_report())
    check(
        "S4 decide, coherence, bridge, typography, faces sono tutte presenti",
        {"decide", "coherence", "bridge", "typography", "faces"} <= set(block["capabilities"]),
        block["capabilities"],
    )
    check("S4 il blocco si dichiara ok", block["ok"] is True, block["ok"])


def test_empty_report_does_not_raise() -> None:
    print("\n5. un report vuoto non solleva e non inventa")
    block = sections.compile_sections({})
    check("S5 nessuna sezione compilata", block["filled"] == 0, block["filled"])
    check("S5 totale resta sei", block["total"] == 6, block["total"])
    check("S5 ok e' falso, non un falso positivo", block["ok"] is False, block["ok"])


def test_sections_are_the_declared_six() -> None:
    print("\n6. le sezioni sono sempre le sei dichiarate, in ordine")
    block = sections.compile_sections(full_report())
    check(
        "S6 ids nell'ordine dichiarato",
        [row["id"] for row in block["sections"]] == list(sections.SECTIONS),
        [row["id"] for row in block["sections"]],
    )


def test_expectations_come_from_the_request() -> None:
    print("\n7. la richiesta diventa requisito, e solo quello che ha scritto")
    wanted = sections.expectations("reel 15s, hook: Stasera tutto cambia, cta: Scrivici")
    check("S7 hook richiesto", wanted.get("hook", {}).get("required") is True, wanted)
    check("S7 cta richiesta", wanted.get("cta", {}).get("required") is True, wanted)
    check(
        "S7 nessun requisito inventato su un prompt neutro",
        sections.expectations("un reel di dieci secondi") == {},
        sections.expectations("un reel di dieci secondi"),
    )


def test_gaps_block_the_delivery() -> None:
    print("\n8. un hook chiesto e non compilato e' un buco bloccante")
    empty = sections.compile_sections({})
    wanted = sections.expectations("reel, hook: prova, cta: scrivici")
    audit = sections.audit(empty, wanted)
    check("S8 due buchi (hook e cta)", len(audit["gaps"]) == 2, audit["gaps"])
    check("S8 diventano altrettanti check falliti", len(audit["checks"]) == 2, audit["checks"])
    check("S8 e il blocco non e' ok", audit["ok"] is False, audit["ok"])

    full = sections.compile_sections(
        {
            "measured": {"duration_sec": 12.0, "clips": 3},
            "opening": {"chosen": {"asset_id": "a", "sharpness": 80.0}, "threshold": 50.0},
            "texts": ["STASERA", "SCRIVICI"],
            "coherence": {"metrics": {"shots": 5}},
        }
    )
    clean = sections.audit(full, wanted)
    check("S8 con le sezioni compilate non ci sono buchi", clean["gaps"] == [], clean["gaps"])
    check("S8 e il blocco e' ok", clean["ok"] is True, clean["ok"])


def main() -> int:
    test_filled_sections_have_number_and_capability()
    test_missing_is_declared()
    test_no_orphan_capabilities()
    test_capabilities_are_the_real_ones()
    test_empty_report_does_not_raise()
    test_sections_are_the_declared_six()
    test_expectations_come_from_the_request()
    test_gaps_block_the_delivery()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
