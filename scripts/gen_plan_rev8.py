"""Generate ``PLAN/PLAN_REV8.md`` - the read-only copy of the plan - from the repo.

The user's source document is a snapshot and it has drifted: it cites phases that
only exist in the repository, it jumps the exam numbering from 19 to 26, it still
carries the old signal-9 kill, and it keeps superseded annexes whose text a fast
reader can follow. The repository is the single source of truth, so the document
becomes a copy generated from it, with a gate that fails when it drifts.

    python scripts/gen_plan_rev8.py                      # rewrite the document
    python scripts/gen_plan_rev8.py --check              # exit 1 when stale
    python scripts/gen_plan_rev8.py --ledger COPIA.md    # read another ledger
    python scripts/gen_plan_rev8.py --commit ab12cd3     # header on another commit

Nothing here is hand-written prose that also lives in the repository: every section
is extracted by heading from ``PLAN/MASTER_PLAN.md``, ``PLAN/MASTER_LEDGER.md``,
``PLAN/ANNEX_F.md`` and friends, and a missing heading stops the run with its name
instead of emitting an empty section.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from plan_copy import (
    APPENDIX_MARKER,
    FORBIDDEN_IN_BODY,
    HISTORY_NOTE,
    OUT,
    READ_ONLY,
    changed_phases,
    render,
)
from plan_facts import read, rel

__all__ = [
    "APPENDIX_MARKER",
    "FORBIDDEN_IN_BODY",
    "HISTORY_NOTE",
    "OUT",
    "READ_ONLY",
    "changed_phases",
    "main",
    "render",
]


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description="Genera PLAN/PLAN_REV8.md")
    parser.add_argument("--check", action="store_true", help="exit 1 se il file e' stantio")
    parser.add_argument("--commit", help="hash o ref da citare nell'intestazione")
    parser.add_argument("--ledger", help="ledger alternativo (prova del gate)")
    args = parser.parse_args(list(argv))

    ledger = Path(args.ledger) if args.ledger else None
    expected = render(args.commit, ledger)
    name = rel(OUT)
    lines = len(expected.splitlines())

    if args.check:
        current = read(OUT) if OUT.is_file() else None
        if current is None:
            print(f"{name}: manca, eseguire python scripts/gen_plan_rev8.py")
            return 1
        if current == expected:
            print(f"{name}: Revisione 8 aggiornata ({lines} righe)")
            return 0
        changed = changed_phases(current, expected)
        detail = f"; righe cambiate: {', '.join(changed)}" if changed else ""
        print(
            f"{name}: Revisione 8 stantia{detail}. "
            "Eseguire python scripts/gen_plan_rev8.py"
        )
        return 1

    if OUT.is_file() and read(OUT) == expected:
        print(f"{name}: invariato ({lines} righe)")
        return 0
    OUT.write_text(expected, encoding="utf-8", newline="\n")
    print(f"{name}: scritto ({lines} righe)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
