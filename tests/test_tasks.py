"""The board is a working copy of the ledger, and it cannot lie.

python tests/test_tasks.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import vx_tasks  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def main() -> int:
    ledger = vx_tasks.read_ledger()
    check(
        "the ledger yields task rows",
        len(ledger) >= 90,
        f"{len(ledger)} row(s)",
    )
    board = vx_tasks.load()
    check(
        "the board carries the same schema",
        board.get("schema") == vx_tasks.SCHEMA,
        board.get("schema"),
    )
    ids = {row["id"] for row in board["tasks"]}
    missing = [task["id"] for task in ledger if task["id"] not in ids]
    check("every ledger task is on the board", not missing, missing[:5])

    # sync is idempotent: a second pass adds nothing.
    before = len(board["tasks"])
    counts = vx_tasks.sync(board, ledger)
    check(
        "sync adds nothing twice",
        counts["added"] == 0 and len(board["tasks"]) == before,
        counts,
    )

    # every state is one of the four, and a closed task carries its proof.
    illegal = [row["id"] for row in board["tasks"] if row["status"] not in (*vx_tasks.OPEN, "done")]
    check("statuses are the declared four", not illegal, illegal[:5])
    unproven = [
        row["id"]
        for row in board["tasks"]
        if row["status"] == "done" and not row.get("evidence")
    ]
    check("a closed task always carries evidence", not unproven, unproven[:5])

    # done without evidence is refused by the command itself.
    with tempfile.TemporaryDirectory(prefix="vx-tasks-") as raw:
        copy = Path(raw) / "TASKS.json"
        copy.write_text(json.dumps(board), encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "vx_tasks.py"),
                "done",
                "F16-T02",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        check(
            "the command refuses `done` without evidence",
            proc.returncode != 0 and "--evidence" in (proc.stderr + proc.stdout),
            proc.returncode,
        )

    # next returns an open task and prints its acceptance.
    open_rows = [row for row in board["tasks"] if row["status"] in vx_tasks.OPEN]
    first = vx_tasks.show(open_rows[0]) if open_rows else ""
    check(
        "next shows the acceptance of the first open task",
        "accettazione:" in first,
        open_rows[0]["id"] if open_rows else "none",
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
