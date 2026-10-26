"""Il contratto della documentazione: una sola verità, allineata al codice.

python tests/test_docs_consistency.py

Script-style: stampa PASS/FAIL, esce 1 se un controllo fallisce.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "SKILL.md"
README = ROOT / "README.md"
CONTRACT = ROOT / "scripts" / "vx" / "contract.py"
REFS = ROOT / "references"

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:52} {detail}")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> int:
    skill = read(SKILL)
    readme = read(README)
    contract = read(CONTRACT)

    # -- version --------------------------------------------------------- #
    check("SKILL.md dichiara 1.4.0", 'version: "1.4.0"' in skill)
    check("README.md dichiara 1.4.0", "1.4.0" in readme)
    check("contract.VERSION e' 1.4.0", 'VERSION = "1.4.0"' in contract)

    # -- witness law ----------------------------------------------------- #
    for name, text in (("SKILL.md", skill), ("README.md", readme)):
        check(
            f"{name}: legge dei testimoni", "evidence" in text and "cut_silence" in text
        )
        check(
            f"{name}: exit 2/5/6",
            all(code in text for code in ("exit 2", "exit 5", "exit 6")),
        )

    # -- size and single protocol --------------------------------------- #
    lines = len(skill.splitlines())
    check("SKILL.md <= 200 righe", lines <= 200, f"{lines} righe")
    headings = re.findall(r"^## Protocollo\b.*$", skill, flags=re.MULTILINE)
    check("esattamente un '## Protocollo'", len(headings) == 1, f"{len(headings)}")

    # -- reference index ------------------------------------------------- #
    listed = re.findall(r"references/(\d{2})-[A-Za-z0-9\-]+\.md", skill)
    numbers = sorted(set(listed))
    expected = [f"{n:02d}" for n in range(1, 20)]
    check("indice elenca 01..19", numbers == expected, ",".join(numbers))
    missing = [f"{n:02d}" for n in range(1, 20) if not list(REFS.glob(f"{n:02d}-*.md"))]
    check("ogni reference elencato esiste", not missing, ",".join(missing))
    check("README non dice '01..08'", "01..08" not in readme)

    # -- no multi-aspect promise ---------------------------------------- #
    for name, text in (("SKILL.md", skill), ("README.md", readme)):
        check(f"{name}: nessun '1:1'", "1:1" not in text)
        check(f"{name}: nessun '4:5'", "4:5" not in text)
        check(
            f"{name}: nessun export per piattaforma",
            not re.search(
                r"tagli per piattaforma|ricropato per|mp4 per aspect|per-platform export",
                text,
                flags=re.IGNORECASE,
            ),
        )

    # -- stale paths and machine literals ------------------------------- #
    for name, text in (("SKILL.md", skill), ("README.md", readme)):
        check(f"{name}: nessun out\\final_full.mp4", "out\\final_full.mp4" not in text)
        check(f"{name}: nessun Arial Black", "Arial Black" not in text)
        check(
            f"{name}: nessun path assoluto C:\\ o D:\\",
            not re.search(r"[A-Za-z]:\\", text),
        )

    # -- one protocol, no second signed-brief block --------------------- #
    check("SKILL.md: nessun 'brief firmato'", skill.count("brief firmato") == 0)

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
