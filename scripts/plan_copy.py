"""Compose ``PLAN/PLAN_REV8.md``: header, sections, appendix, body guard.

``render()`` is the only entry point: it reads the documents through
``plan_facts``, builds every section, and refuses to emit a body that still
carries superseded text. The header date is the commit date, never ``now``, so two
runs give identical bytes.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from plan_facts import (
    ROOT,
    PLAN_DIR,
    ANNEX_F,
    MASTER_LEDGER,
    MASTER_PLAN,
    Sources,
    annex_phases,
    annex_states,
    cell,
    ledger_rows,
    ledger_titles,
    ledger_exam_items,
    load,
    numbered,
    patch_counts,
    phase_proof,
    phase_state,
    plan_phases,
    plan_titles,
    precedence_note,
    rel,
    STATUS_FOR_TOKEN,
    span,
)
from plan_deviations import deviation_section


OUT = PLAN_DIR / "PLAN_REV8.md"


APPENDIX_MARKER = "<!-- APPENDICE: TESTO STORICO (non operativo) -->"


HISTORY_NOTE = "Testo storico, non operativo: superato dall'Allegato D."


READ_ONLY = (
    "Copia in sola lettura: la fonte di verita' e' il repository "
    "(PLAN/MASTER_PLAN.md, PLAN/MASTER_LEDGER.md, PLAN/ANNEX_F.md). "
    "Non modificare questo file a mano."
)


FORBIDDEN_IN_BODY = ("NON SEGUIRE", "kill -9", "Instagram", "TikTok")


HISTORY_IDS = ("B2", "B5", "B6", "B7", "C3")


PHASE_IDS = [f"F{number}" for number in range(32)]


def header(commit: Optional[str]) -> List[str]:
    head_hash = git_show("%h")
    head_date = git_show("%cs")
    if not head_hash or not head_date:
        raise SystemExit(
            "git: commit o data illeggibili; senza commit il documento non si genera"
        )
    if commit:
        commit_hash = git_show("%h", commit) or commit.strip()
        commit_date = git_show("%cs", commit) or head_date
    else:
        commit_hash, commit_date = head_hash, head_date
    if not re.fullmatch(r"[0-9a-f]{7,40}", commit_hash):
        raise SystemExit(f"git: hash non valido {commit_hash!r}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", commit_date):
        raise SystemExit(f"git: data non ISO {commit_date!r}")
    return [
        "# Piano - Revisione 8",
        "",
        f"Generato da scripts/gen_plan_rev8.py dal commit {commit_hash} del {commit_date}.",
        "",
        READ_ONLY,
        "",
    ]


def git_show(fmt: str, commit: Optional[str] = None) -> str:
    command = ["git", "show", "-s", f"--format={fmt}", commit or "HEAD"]
    try:
        done = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def where_we_are(src: Sources) -> List[str]:
    rows = ledger_rows(src.ledger)
    grouped: Dict[str, List[Row]] = {}
    for row in rows:
        grouped.setdefault(row.phase, []).append(row)
    titles = ledger_titles(src.patch)
    titles.update({key: value for key, value in plan_titles(src.plan).items()})
    titles.update(ledger_titles(src.ledger))
    patches = patch_counts(src.patch)

    out = [
        "## Dove siamo",
        "",
        "Una riga per fase, F0..F31. Lo stato viene dalle righe di task di "
        "`PLAN/MASTER_LEDGER.md`: `DONE` se tutte le righe sono `DONE`, `PARZIALE` se "
        "qualcuna lo e', `APERTO` se nessuna, `BLOCCATO` se una riga dice `BLOCKED` "
        "(il blocco vince, perche' e' cio' che impedisce di chiudere la fase).",
        "",
        "Le fasi F20..F24 non hanno ancora righe nel ledger: i loro task sono pronti in "
        "`PLAN/LEDGER_PATCH.md` e restano aperti. F29..F31 non hanno righe ne' sezione "
        "in nessun documento del repository: il loro titolo non si inventa.",
        "",
        "| fase | titolo | stato | task chiusi/totali | prova |",
        "|---|---|---|---|---|",
    ]
    for phase in PHASE_IDS:
        phase_rows = grouped.get(phase, [])
        closed = sum(1 for row in phase_rows if row.state == "DONE")
        title = titles.get(phase, "non definita nei documenti")
        out.append(
            "| "
            + " | ".join(
                (
                    phase,
                    cell(title),
                    phase_state(phase_rows),
                    f"{closed}/{len(phase_rows)}",
                    cell(phase_proof(phase_rows, patches.get(phase, 0))),
                )
            )
            + " |"
        )

    t09 = next((row for row in rows if row.ident == "F0-T09"), None)
    out.append("")
    if t09 is None:
        raise SystemExit("PLAN/MASTER_LEDGER.md: la riga F0-T09 non esiste piu'")
    if t09.state == "BLOCKED":
        out.append(
            "**Blocco T09** - `F0-T09` (corpus annotato) e' **BLOCCATO** nel ledger e "
            "tiene provvisorie le soglie di **F5**, **F6**, **F9** e dell'esame: finche' "
            "manca l'annotazione umana, nessuna fase che misura su quelle soglie si "
            "chiude (D010)."
        )
    else:
        out.append(
            f"**Blocco T09** - `F0-T09` ora e' **{t09.state}** nel ledger: il blocco che "
            "teneva provvisorie le soglie di F5, F6, F9 e dell'esame non e' piu' aperto."
        )

    out.extend(["", "### Fasi dell'Allegato F (F32..F43)", ""])
    out.append(
        "Gli ID sono F32..F43 perche' F25..F28 sono gia' occupate; l'etichetta originale "
        "dell'allegato resta accanto. Lo stato viene dalla riga di sintesi in "
        "`PLAN/MASTER_LEDGER.md`, i task atomici da `PLAN/ANNEX_F.md`."
    )
    out.extend(
        [
            "",
            "| fase | titolo | stato | task chiusi/totali | prova |",
            "|---|---|---|---|---|",
        ]
    )
    for phase in annex_phases(src.annex, annex_states(src.ledger)):
        total = len(phase.tasks)
        closed = total if phase.state == "DONE" else 0
        proof = (
            "nessuna riga in MASTER_LEDGER.md; task in PLAN/ANNEX_F.md"
            if not phase.state
            else f"ledger: riga `{phase.ident}` {phase.state}, task in PLAN/ANNEX_F.md"
        )
        out.append(
            "| "
            + " | ".join(
                (
                    phase.ident,
                    cell(f"{phase.label} - {phase.title}"),
                    STATUS_FOR_TOKEN.get(phase.state, "APERTO"),
                    f"{closed}/{total}",
                    cell(proof),
                )
            )
            + " |"
        )
    return out


def phases_section(src: Sources) -> List[str]:
    rows = ledger_rows(src.ledger)
    grouped: Dict[str, List[Row]] = {}
    for row in rows:
        grouped.setdefault(row.phase, []).append(row)
    out = [
        "## Fasi e task",
        "",
        "Le fasi come le elenca `PLAN/MASTER_PLAN.md` (sezione 4 per F0..F12, Allegato A4 "
        "per F13..F19): per ognuna la consegna e l'accettazione del piano, e i task "
        "atomici che il ledger le attribuisce oggi. Una fase senza task nel ledger resta "
        "al livello della consegna, come vuole D003.",
        "",
    ]
    for ident, title, body in plan_phases(src.plan):
        out.extend([f"### {ident} - {title}", ""])
        out.extend(line for line in body)
        out.append("")
        tasks = sorted(grouped.get(ident, []), key=lambda row: int(row.ident.split("-T")[1]))
        if tasks:
            out.extend(["| ID | stato |", "|---|---|"])
            out.extend(f"| {row.ident} | {row.state} |" for row in tasks)
        else:
            out.append("Nessun task atomico nel ledger: la fase resta al livello della consegna.")
        out.append("")

    out.extend(
        [
            "### Fasi presenti solo nel ledger (F25..F28)",
            "",
            "Il piano non le elenca: sono nate dopo, e il ledger e' la sola fonte.",
            "",
            "| fase | titolo | task chiusi/totali |",
            "|---|---|---|",
        ]
    )
    ledger_titles_map = ledger_titles(src.ledger)
    for ident in ("F25", "F26", "F27", "F28"):
        tasks = grouped.get(ident, [])
        closed = sum(1 for row in tasks if row.state == "DONE")
        title = ledger_titles_map.get(ident)
        if title is None:
            raise SystemExit(f"{rel(MASTER_LEDGER)}: sezione {ident} mancante")
        out.append(f"| {ident} | {cell(title)} | {closed}/{len(tasks)} |")

    out.extend(
        [
            "",
            "### Allegato F: F32..F43 (da PLAN/ANNEX_F.md)",
            "",
            "I task come li trascrive l'allegato, con l'etichetta originale accanto.",
            "",
        ]
    )
    for phase in annex_phases(src.annex, annex_states(src.ledger)):
        out.extend([f"#### {phase.ident} ({phase.label}) - {phase.title}", ""])
        out.extend(phase.bullets)
        out.extend(
            ["", f"Stato nel ledger: **{phase.state or 'nessuna riga'}**.", ""]
        )
    return out


def exam_section(src: Sources) -> List[str]:
    plan_lines = src.plan.splitlines()
    start, stop, _ = span(plan_lines, lambda t: t.startswith("6. "), rel(MASTER_PLAN))
    core = numbered(plan_lines, start, stop)
    a5_start, a5_stop, _ = span(
        plan_lines, lambda t: t.startswith("A5. "), rel(MASTER_PLAN)
    )
    extended = numbered(plan_lines, a5_start, a5_stop)
    annex_lines = src.annex.splitlines()
    af5_start, af5_stop, _ = span(
        annex_lines, lambda t: t.startswith("AF5. "), rel(ANNEX_F)
    )
    creative = numbered(annex_lines, af5_start, af5_stop)

    items: List[Tuple[int, str, str]] = []
    for number in range(1, 20):
        text = core.get(number) or extended.get(number)
        if not text:
            raise SystemExit(f"{rel(MASTER_PLAN)}: incarico {number} mancante")
        items.append((number, text, "sezione 6" if number in core else "A5"))
    for number in range(26, 34):
        text = creative.get(number)
        if not text:
            raise SystemExit(f"{rel(ANNEX_F)}: incarico {number} mancante")
        items.append((number, text, "AF5"))
    if len(items) != 27:
        raise SystemExit(f"esame: attesi 27 incarichi, trovati {len(items)}")

    ledger_items = ledger_exam_items(src.ledger)
    out = [
        "## Esame",
        "",
        "27 incarichi: 1..19 (sezione 6 del piano piu' A5) e 26..33 (`AF5` "
        "dell'Allegato F). La numerazione salta 20..25 perche' e' quella del documento "
        "sorgente; quegli incarichi esistono e sono definiti nel ledger (F25), non nel "
        "piano: sono elencati sotto la tabella.",
        "",
        "| # | incarico | fonte |",
        "|---|---|---|",
    ]
    for number, text, source in items:
        out.append(f"| {number} | {cell(text)} | {source} |")
    out.extend(
        [
            "",
            "**20..25, definiti nel ledger (`PLAN/MASTER_LEDGER.md`, F25 \"Esame e "
            "perimetro di F25\"):**",
            "",
        ]
    )
    for number, name in ledger_items:
        out.append(f"- `{number}` - {name}")
    if [number for number, _ in ledger_items] != list(range(20, 26)):
        raise SystemExit("ledger: gli incarichi 20..25 non sono sei, la fonte e' cambiata")
    return out


def history_section(src: Sources) -> List[str]:
    lines = src.plan.splitlines()
    out = [
        APPENDIX_MARKER,
        "",
        "## Storico",
        "",
        HISTORY_NOTE,
        "",
    ]
    precedence_rows = precedence_note(src.precedence)
    out.extend([precedence_rows, ""])
    for ident in HISTORY_IDS:
        start, stop, _ = span(
            lines,
            lambda title, want=ident: title.startswith(f"{want}."),
            f"{rel(MASTER_PLAN)} {ident}",
        )
        body = list(lines[start:stop])
        while body and not body[-1].strip():
            body.pop()
        out.extend(body)
        out.append("")
    return out


def render(commit: Optional[str] = None, ledger: Optional[Path] = None) -> str:
    src = load(ledger)
    lines: List[str] = []
    lines.extend(header(commit))
    for section in (
        where_we_are(src),
        deviation_section(src),
        phases_section(src),
        exam_section(src),
    ):
        lines.extend(section)
        lines.append("")
    lines.extend(history_section(src))

    document = "\n".join(lines).rstrip("\n") + "\n"
    body, _, _ = document.partition(APPENDIX_MARKER)
    for token in FORBIDDEN_IN_BODY:
        if token in body:
            raise SystemExit(
                f"PLAN_REV8.md: {token!r} compare nel corpo; quel testo deve restare "
                "solo in appendice"
            )
    return document


def status_rows(text: str) -> Dict[str, str]:
    rows: Dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\|\s*(F\d+)\s*\|", line)
        if match:
            rows[match.group(1)] = line
    return rows


def changed_phases(current: str, expected: str) -> List[str]:
    before, after = status_rows(current), status_rows(expected)
    keys = {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
    return sorted(keys, key=lambda key: int(key[1:]))
