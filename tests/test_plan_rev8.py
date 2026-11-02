"""Revisione 8: the generated plan copy, its gate, and its load-bearing test.

python tests/test_plan_rev8.py

Checks:
(a) the generator is deterministic: two runs give identical bytes;
(b) the committed `PLAN/PLAN_REV8.md` is exactly what the generator emits, and
    `--check` exits 0 on it;
(c) the BODY carries no superseded text (`NON SEGUIRE`, the old signal-9 kill,
    provider instructions for the two social platforms): that text lives only in
    the appendix, verbatim;
(d) the status table has one row per phase F0..F31, the four allowed states, and
    the T09 block line right under the table;
(e) the deviation register has at least five rows and names `1.2` and `D028`;
(f) the header carries the commit and its date, and another commit changes it;
(g) LOAD-BEARING: a ledger copy with one phase's rows removed makes `--check`
    fail and name that phase.

Script-style: prints PASS/FAIL, exits 1 if a check fails.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import gen_plan_rev8 as gen  # noqa: E402

PLAN_FILE = ROOT / "PLAN" / "PLAN_REV8.md"
LEDGER = ROOT / "PLAN" / "MASTER_LEDGER.md"
SCRIPT = ROOT / "scripts" / "gen_plan_rev8.py"

HEADER = re.compile(
    r"^Generato da scripts/gen_plan_rev8\.py dal commit ([0-9a-f]{7,40}) "
    r"del (\d{4}-\d{2}-\d{2})\.$",
    re.MULTILINE,
)
ROW = re.compile(
    r"^\|\s*(F\d+)\s*\|\s*(.*?)\s*\|\s*([A-Z]+)\s*\|\s*(\d+)/(\d+)\s*\|",
    re.MULTILINE,
)
DEVIATION = re.compile(r"^\|\s*(\d+)\s*\|", re.MULTILINE)
HISTORY_HEADINGS = (
    "Fonti, distinte per tipo di segnale",
    "Passi automatici di ogni lavoro",
    "Mappa dei provider noti",
    "Addendum al prompt (da incollare in coda alla sezione 9) - SUPERATO da C",
)
FORBIDDEN = ("NON SEGUIRE", "kill -9")
PROVIDERS = ("Instagram", "TikTok")
STATES = {"DONE", "APERTO", "BLOCCATO", "PARZIALE"}

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def git(fmt: str) -> str:
    done = subprocess.run(
        ["git", "log", "-1", f"--format={fmt}", "--",
         "PLAN/MASTER_PLAN.md", "PLAN/MASTER_LEDGER.md", "PLAN/ANNEX_F.md",
         "PLAN/DECISIONS_PATCH.md", "PLAN/DISCREPANZE.md"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return done.stdout.strip()


def body_of(text: str) -> str:
    return text.partition(gen.APPENDIX_MARKER)[0]


def appendix_of(text: str) -> str:
    return text.partition(gen.APPENDIX_MARKER)[2]


def strip_commit_line(text: str) -> str:
    """The document without the one line the commit changes."""
    return "\n".join(
        line
        for line in text.splitlines()
        if not line.startswith("Generato da scripts/gen_plan_rev8.py")
    )


def phase_rows(text: str) -> Dict[str, Tuple[str, int, int]]:
    rows: Dict[str, Tuple[str, int, int]] = {}
    for ident, _title, state, closed, total in ROW.findall(text):
        rows[ident] = (state, int(closed), int(total))
    return rows


def body_violations(text: str) -> list:
    """The superseded SECTIONS must not be in the body: headings, their bullets, their marker.

    A sigla in prose ("l'Allegato D sostituisce C3") is legitimate and must pass; only the
    section coming back -- its heading, its provider bullets, its instructions -- is a defect.
    """
    problems = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^#{2,4}\s*(B2|B5|B6|B7|C3)\b", stripped):
            problems.append("heading: " + stripped[:48])
        if stripped.startswith(("- TikTok.", "- Instagram.")):
            problems.append("provider bullet: " + stripped[:48])
        if "API Graph" in stripped or "Business Discovery" in stripped:
            problems.append("provider instructions")
        if "NON SEGUIRE" in stripped or "kill -9" in stripped:
            problems.append("superseded marker")
    return problems


def _commit_date(ref: str) -> str:
    done = subprocess.run(
        ["git", "log", "-1", "--format=%cs", ref],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
    )
    return (done.stdout or "").strip()


def main() -> int:
    committed = PLAN_FILE.read_text(encoding="utf-8")
    rendered = gen.render()
    body, appendix = body_of(committed), appendix_of(committed)

    # -- (a) determinism -------------------------------------------------- #
    second = gen.render()
    check("due render identici byte per byte", rendered == second, f"{len(rendered)} byte")
    cited = gen._cited_commit(committed) or "HEAD"
    check(
        "l'unica data nel documento e' quella del commit citato",
        sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", committed))) == [_commit_date(cited)],
        sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", committed))),
    )
    check("il commit citato esiste nel repository", bool(_commit_date(cited)), cited)

    # -- (b) the committed file is the generator's output ----------------- #
    check(
        "PLAN_REV8.md == render(), testata a parte",
        strip_commit_line(committed) == strip_commit_line(rendered),
    )
    current = run("--check")
    check("--check esce 0 sul file committato", current.returncode == 0, current.stdout.strip())
    check("--check stampa 'aggiornata'", "aggiornata" in current.stdout)

    # -- (c) body vs appendix --------------------------------------------- #
    check("corpo senza sezioni superate", body_violations(body) == [], body_violations(body))
    check(
        "una sigla in prosa e' legittima e non fa scattare il controllo",
        body_violations("l'Allegato D sostituisce C3, che nominava Instagram e TikTok.") == [],
    )
    check(
        "il controllo scatta se una sezione rientra nel corpo",
        bool(body_violations("### C3. Mappa dei provider\n- TikTok. Creative Center")),
    )
    for token in FORBIDDEN:
        check(f"corpo senza {token!r}", token not in body)
    check("appendice con 'NON SEGUIRE'", appendix.count("NON SEGUIRE") >= 5,
          appendix.count("NON SEGUIRE"))
    check(
        "appendice con i due provider social",
        all(name in appendix for name in PROVIDERS),
    )
    check(
        "le sezioni storiche stanno solo in appendice",
        all(heading not in body for heading in HISTORY_HEADINGS),
    )
    check(
        "appendice in coda, dopo Esame",
        committed.index(gen.APPENDIX_MARKER) > committed.index("## Esame"),
    )
    check(
        "una sola riga dichiara il testo storico",
        appendix.count(gen.HISTORY_NOTE) == 1,
    )

    # -- (d) the status table --------------------------------------------- #
    rows = phase_rows(committed)
    core = [f"F{number}" for number in range(32)]
    check("32 righe per F0..F31", all(phase in rows for phase in core),
          sum(1 for phase in rows if int(phase[1:]) < 32))
    ordered = [ident for ident, *_ in ROW.findall(committed) if int(ident[1:]) < 32]
    check("le righe delle fasi in ordine F0..F31", ordered == core, ordered[:3])
    check(
        "stati ammessi solo i quattro",
        {state for state, _, _ in rows.values()} <= STATES,
        sorted({state for state, _, _ in rows.values()}),
    )
    t09_line = LEDGER.read_text(encoding="utf-8").splitlines()
    t09 = next(
        (line for line in t09_line if line.startswith("| F0-T09")), ""
    )
    check("T09 e' BLOCKED nel ledger", "BLOCKED" in t09)
    check("la tabella segna F0 come BLOCCATO", rows.get("F0", ("",))[0] == "BLOCCATO")
    lines = committed.splitlines()
    last_core = max(
        index for index, line in enumerate(lines) if re.match(r"^\| F31 \|", line)
    )
    following = next(line for line in lines[last_core + 1 :] if line.strip())
    check(
        "riga del blocco T09 subito sotto la tabella",
        following.startswith("**Blocco T09**"),
        following[:40],
    )
    check(
        "il blocco T09 nomina F5, F6, F9 e l'esame",
        all(name in following for name in ("F5", "F6", "F9", "esame")),
    )

    # -- (e) the deviation register --------------------------------------- #
    register = committed.partition("## Registro delle deviazioni")[2].partition("## Fasi")[0]
    deviated = [number for number in DEVIATION.findall(register)]
    check("registro con almeno 5 righe", len(deviated) >= 5, len(deviated))
    check("registro numerato 1..N", deviated == [str(i) for i in range(1, len(deviated) + 1)])
    check("registro nomina 1.2", "1.2" in register)
    check("registro nomina D028", "D028" in register)
    check(
        "ogni riga del registro ha una fonte",
        all("PLAN/" in line for line in register.splitlines() if DEVIATION.match(line)),
    )

    # -- (f) the header ---------------------------------------------------- #
    match = HEADER.search(committed)
    check("intestazione con commit e data ISO", match is not None)
    if match:
        check("data della riga = data del commit", match.group(2) == git("%cs"), match.group(2))
        # Il commit citato non puo' essere quello che contiene il documento: lo
    # diventerebbe solo dopo il commit, e la testata avrebbe dovuto indovinarlo.
    # Quello che si puo' pretendere e' che sia un ANTENATO reale di HEAD: la
    # testata e' provenienza, il corpo e' la verifica del contenuto.
    cited = match.group(1) if match else ""
    ancestor = bool(cited) and subprocess.run(
        ["git", "merge-base", "--is-ancestor", cited, "HEAD"],
        cwd=str(ROOT), capture_output=True,
    ).returncode == 0
    check("il commit della riga e' un antenato di HEAD", ancestor, cited)
    check("riga di sola lettura presente", gen.READ_ONLY in committed)
    fake = gen.render("deadbeef")
    fake_header = HEADER.search(fake)
    check(
        "un altro commit cambia l'intestazione",
        fake_header is not None and fake_header.group(1) == "deadbeef",
        fake_header.group(1) if fake_header else "",
    )
    check(
        "il resto del documento non cambia col commit",
        strip_commit_line(fake) == strip_commit_line(committed),
    )

    # -- (g) LOAD-BEARING: the gate names the phase that lost its rows ----- #
    source = LEDGER.read_text(encoding="utf-8")
    stripped = "\n".join(
        line for line in source.splitlines() if not line.startswith("| F16-T")
    )
    dropped = len(source.splitlines()) - len(stripped.splitlines())
    check("la copia toglie le righe di F16", dropped >= 5, dropped)
    with tempfile.TemporaryDirectory() as temporary:
        copy = Path(temporary) / "ledger_senza_f16.md"
        copy.write_text(stripped + "\n", encoding="utf-8", newline="\n")
        gated = run("--check", "--ledger", str(copy))
    check("--check fallisce sulla copia", gated.returncode == 1, gated.returncode)
    check("il fallimento nomina F16", "F16" in gated.stdout, gated.stdout.strip())
    check(
        "il fallimento chiede di rigenerare",
        "python scripts/gen_plan_rev8.py" in gated.stdout,
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
