"""Facts of the plan, read out of the repository documents.

Every function here takes the text of a document and returns data: no markdown is
composed and nothing is written. A missing heading raises with its own name
instead of yielding an empty section, because an empty section is the drift this
whole exercise exists to catch.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]


PLAN_DIR = ROOT / "PLAN"


MASTER_PLAN = PLAN_DIR / "MASTER_PLAN.md"


MASTER_LEDGER = PLAN_DIR / "MASTER_LEDGER.md"


ANNEX_F = PLAN_DIR / "ANNEX_F.md"


LEDGER_PATCH = PLAN_DIR / "LEDGER_PATCH.md"


DECISIONS_PATCH = PLAN_DIR / "DECISIONS_PATCH.md"


DOC_VS_REPO = PLAN_DIR / "DOC_VS_REPO.md"


PRECEDENCE = PLAN_DIR / "PRECEDENCE.md"


DISCREPANZE = PLAN_DIR / "DISCREPANZE.md"


LEDGER_ENV = "VX_PLAN_LEDGER"


STATES = ("DONE", "TODO", "WIP", "BLOCKED")
STATUS_FOR_TOKEN = {"DONE": "DONE", "WIP": "PARZIALE", "BLOCKED": "BLOCCATO"}


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


LEDGER_ROW_RE = re.compile(r"^\|\s*(F\d+)-T(\d+)\s*\|")


LEDGER_HEAD_RE = re.compile(r"^##\s+(F\d+)\s*[-\u2014]\s+(.*)$")


PLAN_PHASE_RE = re.compile(r"^(F\d+)\.\s+(.*)$")


ANNEX_HEAD_RE = re.compile(r"^###\s+(F\d+)\s+\((?:AF4/)?(F\d+)\)\s+-\s+(.*)$")


ANNEX_ROW_RE = re.compile(r"^\|\s*(F\d+)\s+\(AF4/F\d+\)")


ITEM_RE = re.compile(r"^(\d{1,2})\.\s+(.*)$")


STATE_RE = re.compile(r"^([A-Z]+)")


HASH_RE = re.compile(r"`([0-9a-f]{7,40})`")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def read(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"{rel(path)}: file mancante, il documento non si genera")
    return path.read_text(encoding="utf-8")


def cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def state_cell(line: str) -> str:
    """The state cell: the rightmost cell that starts with a known state token.

    Cells may legally contain a pipe inside a code span (``(antonio|inter)`` in the
    F28-T01 row splits the state cell in two), so neither left-to-right nor
    right-to-left splitting alone finds the column. Searching the cells from the
    right for a leading ``DONE``/``TODO``/``WIP``/``BLOCKED`` does.
    """
    parts = [part.strip() for part in line.strip().strip("|").split("|")]
    for part in reversed(parts):
        match = STATE_RE.match(part.lstrip("`"))
        if match and match.group(1) in STATES:
            return part
    raise SystemExit(f"stato introvabile nella riga: {line[:70]!r}")


def state_token(value: str) -> str:
    match = STATE_RE.match(value.strip().lstrip("`"))
    if not match or match.group(1) not in STATES:
        raise SystemExit(f"stato illeggibile nel ledger: {value!r}")
    return match.group(1)


def span(lines: Sequence[str], predicate, name: str) -> Tuple[int, int, int]:
    """(heading index, end index, level) of the first heading matching predicate."""
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if not match or not predicate(match.group(2)):
            continue
        level = len(match.group(1))
        for stop in range(index + 1, len(lines)):
            nxt = HEADING_RE.match(lines[stop])
            if nxt and len(nxt.group(1)) <= level:
                return index, stop, level
        return index, len(lines), level
    raise SystemExit(f"{name}: intestazione mancante")


def numbered(lines: Sequence[str], start: int, stop: int) -> Dict[int, str]:
    """Numbered list items, joining wrapped continuation lines."""
    items: Dict[int, str] = {}
    current: Optional[int] = None
    for line in lines[start:stop]:
        stripped = line.strip()
        match = ITEM_RE.match(stripped)
        if match:
            current = int(match.group(1))
            items[current] = match.group(2).strip()
        elif not stripped:
            current = None
        elif current is not None and not stripped.lower().startswith("criteri"):
            items[current] += " " + stripped
        else:
            current = None
    return items


class Row(NamedTuple):
    ident: str
    phase: str
    state: str
    hashes: Tuple[str, ...]


class AnnexPhase(NamedTuple):
    ident: str
    label: str
    title: str
    bullets: List[str]
    tasks: List[str]
    state: str


class Anchor(NamedTuple):
    path: Path
    needle: str
    label: str


class Deviation(NamedTuple):
    built: str
    planned: str
    reason: str
    sources: Tuple[Anchor, ...]


class Sources(NamedTuple):
    plan: str
    ledger: str
    annex: str
    patch: str
    decisions: str
    doc: str
    precedence: str
    discrepanze: str


def load(ledger: Optional[Path] = None) -> Sources:
    """The eight documents the copy is built from.

    A ledger named explicitly - by argument or by ``VX_PLAN_LEDGER`` - must exist:
    silently falling back to the real ledger would turn a typo in the gate test
    into a green run.
    """
    named = ledger or (Path(os.environ[LEDGER_ENV]) if os.environ.get(LEDGER_ENV) else None)
    chosen = named or MASTER_LEDGER
    if not chosen.is_file():
        raise SystemExit(f"{rel(chosen)}: ledger indicato ma assente")
    return Sources(
        plan=read(MASTER_PLAN),
        ledger=read(chosen),
        annex=read(ANNEX_F),
        patch=read(LEDGER_PATCH),
        decisions=read(DECISIONS_PATCH),
        doc=read(DOC_VS_REPO),
        precedence=read(PRECEDENCE),
        discrepanze=read(DISCREPANZE),
    )


def ledger_rows(text: str) -> List[Row]:
    rows: Dict[str, Row] = {}
    for line in text.splitlines():
        match = LEDGER_ROW_RE.match(line)
        if not match:
            continue
        ident = f"{match.group(1)}-T{match.group(2)}"
        rows.setdefault(
            ident,
            Row(
                ident=ident,
                phase=match.group(1),
                state=state_token(state_cell(line)),
                hashes=tuple(dict.fromkeys(HASH_RE.findall(line))),
            ),
        )
    return list(rows.values())


def ledger_titles(text: str) -> Dict[str, str]:
    titles: Dict[str, str] = {}
    for line in text.splitlines():
        match = LEDGER_HEAD_RE.match(line)
        if match:
            titles.setdefault(match.group(1), match.group(2).strip())
    return titles


def plan_phases(text: str) -> List[Tuple[str, str, List[str]]]:
    """(id, title, body lines) for every ``### Fx.`` / ``#### Fx.`` of the plan."""
    lines = text.splitlines()
    found: List[Tuple[str, str, List[str]]] = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if not match or len(match.group(1)) not in (3, 4):
            continue
        phase = PLAN_PHASE_RE.match(match.group(2))
        if not phase:
            continue
        _, stop, _ = span(
            lines, lambda title, want=match.group(2): title == want, rel(MASTER_PLAN)
        )
        body = [line for line in lines[index + 1 : stop]]
        while body and not body[-1].strip():
            body.pop()
        while body and not body[0].strip():
            body.pop(0)
        found.append((phase.group(1), phase.group(2).strip(), body))
    ids = [item[0] for item in found]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"{rel(MASTER_PLAN)}: fase duplicata fra {ids}")
    return found


def annex_states(ledger_text: str) -> Dict[str, str]:
    """Phase-level state of F32..F43, from their summary rows in the ledger."""
    return {
        ANNEX_ROW_RE.match(line).group(1): state_token(state_cell(line))
        for line in ledger_text.splitlines()
        if ANNEX_ROW_RE.match(line)
    }


def annex_phases(text: str, states: Dict[str, str]) -> List[AnnexPhase]:
    """(id, label, title, bullet lines, task ids, ledger state) for F32..F43."""
    lines = text.splitlines()
    found: List[AnnexPhase] = []
    for index, line in enumerate(lines):
        match = ANNEX_HEAD_RE.match(line)
        if not match:
            continue
        _, stop, _ = span(
            lines,
            lambda title, want=line.strip(): want == "### " + title,
            rel(ANNEX_F),
        )
        body = [entry.rstrip() for entry in lines[index + 1 : stop] if entry.strip()]
        bullets = [entry for entry in body if entry.lstrip().startswith("- ")]
        tasks = sorted(set(re.findall(r"\bT(\d{2})\b", "\n".join(body))))
        if not bullets or not tasks:
            raise SystemExit(
                f"{rel(ANNEX_F)}: la fase {match.group(1)} non ha task, sezione vuota"
            )
        found.append(
            AnnexPhase(
                ident=match.group(1),
                label=f"AF4/{match.group(2)}",
                title=match.group(3).strip(),
                bullets=bullets,
                tasks=tasks,
                state=states.get(match.group(1), ""),
            )
        )
    if len(found) != 12:
        raise SystemExit(f"{rel(ANNEX_F)}: attese 12 fasi F32..F43, trovate {len(found)}")
    declared = re.search(r"(\d+)\s+task in AF4 \(([\d+\s]+)\)", text)
    if not declared:
        raise SystemExit(f"{rel(ANNEX_F)}: il conteggio dichiarato dei task di AF4 manca")
    parts = [int(part) for part in declared.group(2).split("+")]
    counted = [len(phase.tasks) for phase in found]
    if parts != counted or int(declared.group(1)) != sum(counted):
        raise SystemExit(
            f"{rel(ANNEX_F)}: conteggi AF4 incoerenti, dichiarato {parts} "
            f"({declared.group(1)}), contato {counted} ({sum(counted)})"
        )
    return found


def plan_titles(text: str) -> Dict[str, str]:
    return {phase: title for phase, title, _ in plan_phases(text)}


def patch_counts(text: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for line in text.splitlines():
        match = LEDGER_ROW_RE.match(line)
        if match:
            phase = match.group(1)
            counts[phase] = counts.get(phase, 0) + 1
    return counts


def phase_state(rows: Sequence[Row]) -> str:
    if not rows:
        return "APERTO"
    if any(row.state == "BLOCKED" for row in rows):
        return "BLOCCATO"
    if all(row.state == "DONE" for row in rows):
        return "DONE"
    if any(row.state == "DONE" for row in rows):
        return "PARZIALE"
    return "APERTO"


def phase_proof(rows: Sequence[Row], patch_tasks: int) -> str:
    if rows:
        hashes = list(dict.fromkeys(chain for row in rows for chain in row.hashes))
        closed = sum(1 for row in rows if row.state == "DONE")
        if hashes:
            shown = ", ".join(f"`{value}`" for value in hashes[:3])
            extra = f" +{len(hashes) - 3}" if len(hashes) > 3 else ""
            return shown + extra
        if closed:
            plural = "righe" if closed != 1 else "riga"
            return f"ledger: {closed} {plural} DONE senza hash"
        return "nessuna prova: nessun task chiuso"
    if patch_tasks:
        return (
            f"nessuna riga in MASTER_LEDGER.md; {patch_tasks} task pronti in "
            "PLAN/LEDGER_PATCH.md, tutti aperti"
        )
    return "nessuna riga in MASTER_LEDGER.md"


def ledger_exam_items(text: str) -> List[Tuple[int, str]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("L'esame passa a 25 incarichi"):
            paragraph = line
            for extra in lines[index + 1 :]:
                if not extra.strip():
                    break
                paragraph += " " + extra.strip()
            break
    else:
        raise SystemExit(f"{rel(MASTER_LEDGER)}: la riga dell'esame di F25 manca")
    tail = paragraph.split(":", 1)[1].split(".")[0]
    items: List[Tuple[int, str]] = []
    for chunk in tail.split(","):
        match = re.match(r"^\s*(\d{1,2})\s+(.+)$", chunk.strip())
        if not match:
            raise SystemExit(f"{rel(MASTER_LEDGER)}: incarico illeggibile {chunk!r}")
        items.append((int(match.group(1)), match.group(2).strip()))
    return items


def precedence_note(text: str) -> str:
    required = (
        ("| **Allegato D** (Trend Radar gratuito) | Allegato C, punto **C3**", "D > C3"),
        ("| **Allegato C** (Trend Radar v2) | Allegato B, punti **B2, B5, B6, B7**", "C > B"),
    )
    for needle, label in required:
        if needle not in text:
            raise SystemExit(f"{rel(PRECEDENCE)}: manca la riga {label}")
    return (
        "Precedenza (`PLAN/PRECEDENCE.md`): l'Allegato D supera C3 per la parte dei "
        "provider e ogni uso di API di social; l'Allegato C supera B2, B5, B6 e B7."
    )
