"""The deviation register: where the built thing differs from the plan.

Each row carries the anchors it is derived from - file plus exact string - and the
generator verifies every anchor before emitting the table. A renamed decision or a
deleted ledger row therefore fails the generation with the anchor's name instead
of leaving a silent, stale row.
"""

from __future__ import annotations

from typing import List, Sequence

from plan_facts import (
    DECISIONS_PATCH,
    DISCREPANZE,
    DOC_VS_REPO,
    ANNEX_F,
    LEDGER_PATCH,
    MASTER_LEDGER,
    MASTER_PLAN,
    PRECEDENCE,
    Anchor,
    Deviation,
    Sources,
    cell,
    rel,
)


def deviations(src: Sources) -> List[Deviation]:
    d035 = d035_superseded(src.decisions)
    return [
        Deviation(
            built="Schema EDL 3 additivo: `vx.edl3/1.1`, poi `vx.edl3/1.2`",
            planned="`vx.edl3/2`, con un migratore 3.0 -> 3.1",
            reason=(
                "D028: un campo opzionale con default e' un aumento minore, non una "
                "migrazione. Nessun migratore; il lettore accetta `/1`, `/1.1` e `/1.2`, "
                "e i campi `x-` si conservano al round-trip."
            ),
            sources=(
                Anchor(DISCREPANZE, "| D-6 |", "D-6"),
                Anchor(DECISIONS_PATCH, "Lo schema EDL 3 evolve solo in modo additivo", "D028"),
                Anchor(MASTER_LEDGER, "`vx.edl3/1.2`", "F26-T01"),
            ),
        ),
        Deviation(
            built="`F0-T11`: matrice delle licenze con gate vincolante",
            planned="`F0C`, il task che crea `LICENSES_MATRIX.md`",
            reason=(
                "`F0C` non esiste nel ledger: lo stesso lavoro e' `F0-T11`, protetto da "
                "`tests/test_licenses_matrix.py` (una riga mancante o una verifica "
                "scaduta fanno fallire il test)."
            ),
            sources=(
                Anchor(DOC_VS_REPO, "(F0C)", "riga 4"),
                Anchor(MASTER_LEDGER, "F0-T11", "F0-T11"),
                Anchor(MASTER_LEDGER, "tests/test_licenses_matrix.py", "F0-T11"),
            ),
        ),
        Deviation(
            built="E1 (Look Deck) ed E5 (clonazione) nell'Allegato E e in `F25-T01`..`F25-T15`",
            planned="`E1`, `E5`, `F2B` e `F17B` citati come se fossero nel documento",
            reason=(
                "E1 ed E5 vivono nell'Allegato E, decomposte in F25; `F2B` e' diventato lo "
                "schema 1.1 e poi 1.2; `F17B` sono `F17-T08` e `F17-T09` (uccisioni reali "
                "e guardia di sistema sugli originali)."
            ),
            sources=(
                Anchor(DOC_VS_REPO, "E1 (Look Deck)", "riga 1"),
                Anchor(MASTER_LEDGER, "F25-T01", "F25"),
                Anchor(MASTER_LEDGER, "F17-T08", "F17-T08"),
            ),
        ),
        Deviation(
            built="Terminazione reale: `taskkill /F /T`, `SIGKILL`, albero dei figli",
            planned="Il segnale 9 mandato a mano (`kill` con `-9`)",
            reason=(
                "La prova deve uccidere davvero processo e figli, su Windows e su POSIX, e "
                "poi riprendere dal journal: 50 uccisioni reali in `F17-T08`."
            ),
            sources=(
                Anchor(DOC_VS_REPO, "`taskkill /F /T`", "riga 3"),
                Anchor(DISCREPANZE, "| D-16 |", "D-16"),
                Anchor(MASTER_LEDGER, "taskkill /F /T", "F17-T08"),
            ),
        ),
        Deviation(
            built="Nessun tetto al numero di trattamenti: resta il tetto di cio' che si vede",
            planned=d035,
            reason=(
                "D035: immaginare e' gratis, riempire lo schermo no. Il limite misura il "
                "montaggio, non le varianti che il piano ha immaginato."
            ),
            sources=(
                Anchor(DECISIONS_PATCH, "Il numero di trattamenti non e' piu' un limite creativo", "D035"),
                Anchor(DECISIONS_PATCH, "**Supera:**", "D035"),
            ),
        ),
        Deviation(
            built="`EFFECT_DENSITY`: 3 look che iniziano in una finestra di 10 s, codice duro",
            planned="Un tetto di effetti visibili ogni 10 s, senza un numero",
            reason=(
                "D031: il tetto vive nel compilatore e non ha deroghe, nemmeno per un EDL "
                "esplicito; i primitivi di contratto non sono look."
            ),
            sources=(
                Anchor(DECISIONS_PATCH, "Il tetto anti-arlecchino vive nel compilatore", "D031"),
                Anchor(MASTER_LEDGER, "3 look", "F4-T08"),
                Anchor(MASTER_PLAN, "un tetto di effetti visibili ogni 10 s", "F4"),
            ),
        ),
        Deviation(
            built="`F27` preset e `F28` font",
            planned="Il piano non le aveva: sono nate leggendo il codice",
            reason=(
                "Due misure: i preset erano trasportati e mai risolti; 53 template su 54 "
                "usavano un font di sistema mentre il font incluso non era usato da "
                "nessuno."
            ),
            sources=(
                Anchor(MASTER_LEDGER, "## F27 -", "F27"),
                Anchor(MASTER_LEDGER, "## F28 -", "F28"),
                Anchor(MASTER_LEDGER, "53 template su 54", "F28"),
            ),
        ),
        Deviation(
            built="Le fasi dell'Allegato F come `F32`..`F43`",
            planned="L'allegato le numera `F20`..`F31`",
            reason=(
                "F25..F28 sono occupate: l'ID slitta di dodici posizioni e l'etichetta "
                "originale resta accanto (`F32 (AF4/F20)`), cosi' il documento "
                "dell'utente resta citabile."
            ),
            sources=(
                Anchor(ANNEX_F, "## Conflitto di numerazione, risolto", "conflitto"),
                Anchor(ANNEX_F, "| F32 | AF4/F20 |", "tabella ID"),
            ),
        ),
        Deviation(
            built="Esame a 27 incarichi: 1..19 e 26..33, con 20..25 definiti nel ledger",
            planned="Il documento si ferma a 19 e salta a 26",
            reason=(
                "20..25 (Look Deck, Brand Connect, clonazione della struttura, ispettore, "
                "montaggio a parole, varianti) sono definiti in F25; 26..33 in `AF5`."
            ),
            sources=(
                Anchor(DOC_VS_REPO, "L'esame salta da 19 a 26", "riga 2"),
                Anchor(ANNEX_F, "27 incarichi d'esame", "conteggi"),
                Anchor(MASTER_LEDGER, "L'esame passa a 25 incarichi", "F25"),
            ),
        ),
        Deviation(
            built="32 fasi, 27 incarichi, sei addendum",
            planned='"Versione 1", sezione 4 "F0-F12", sezione 6 "12 incarichi"',
            reason=(
                "Le intestazioni del documento sorgente sono superate dal repository: "
                "questo file le cita solo per dire dove diverge."
            ),
            sources=(
                Anchor(DOC_VS_REPO, "32 fasi (F0-F31)", "riga 5"),
                Anchor(ANNEX_F, "59 task in AF4", "conteggi"),
            ),
        ),
    ]


def d035_superseded(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("**Supera:**"):
            return line[len("**Supera:**") :].strip().rstrip(".")
    raise SystemExit(f"{rel(DECISIONS_PATCH)}: la riga '**Supera:**' di D035 manca")


def check_anchors(rows: Sequence[Deviation], src: Sources) -> None:
    texts = {
        MASTER_PLAN: src.plan,
        MASTER_LEDGER: src.ledger,
        ANNEX_F: src.annex,
        LEDGER_PATCH: src.patch,
        DECISIONS_PATCH: src.decisions,
        DOC_VS_REPO: src.doc,
        PRECEDENCE: src.precedence,
        DISCREPANZE: src.discrepanze,
    }
    for row in rows:
        for anchor in row.sources:
            text = texts.get(anchor.path)
            if text is None:
                raise SystemExit(f"{rel(anchor.path)}: fonte non caricata")
            if anchor.needle not in text:
                raise SystemExit(
                    f"{rel(anchor.path)}: ancora mancante {anchor.needle!r} "
                    "(registro delle deviazioni)"
                )


def deviation_section(src: Sources) -> List[str]:
    rows = deviations(src)
    check_anchors(rows, src)
    out = [
        "## Registro delle deviazioni",
        "",
        "Ogni riga e' un punto in cui il costruito non coincide con il piano originale, "
        "con il motivo. La colonna `fonte` cita il file e l'ancora esatta: il generatore "
        "verifica che l'ancora esista ancora, quindi un documento rinominato fa fallire "
        "la generazione invece di lasciare una riga muta.",
        "",
        "| # | cosa e' stato costruito | cosa diceva il piano | perche' | fonte |",
        "|---|---|---|---|---|",
    ]
    for index, row in enumerate(rows, 1):
        fonte = "; ".join(
            dict.fromkeys(f"{rel(anchor.path)} {anchor.label}" for anchor in row.sources)
        )
        out.append(
            "| "
            + " | ".join(
                (str(index), cell(row.built), cell(row.planned), cell(row.reason), cell(fonte))
            )
            + " |"
        )
    return out
