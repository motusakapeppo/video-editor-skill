"""vx_tasks — a file-backed task board, because V2 has no todo tool.

The ledger is the source of truth (`PLAN/MASTER_LEDGER.md`); this board is its
working copy: one row per task, with the state the ledger declares and the
evidence this session added. Nothing is invented: `sync` only adds tasks that
exist in the ledger, and it never overwrites an evidence note.

    python scripts/vx_tasks.py sync                 # ledger -> board
    python scripts/vx_tasks.py next                 # the first open task, with its acceptance
    python scripts/vx_tasks.py list --open          # everything still open, by phase
    python scripts/vx_tasks.py start F4-T13         # mark doing, print the acceptance
    python scripts/vx_tasks.py done F4-T13 --evidence "test_x 12 PASS" --commit abc1234
    python scripts/vx_tasks.py block F7 --why "needs the MaterialMap"

The board lives in `PLAN/TASKS.json` and is committed, so a session that starts
from scratch reads it instead of re-deriving the backlog.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "PLAN" / "MASTER_LEDGER.md"
BOARD = ROOT / "PLAN" / "TASKS.json"
SCHEMA = "vx.tasks/1"

#: The ledger's own vocabulary, mapped to the board's.
STATES = {
    "DONE": "done",
    "TODO": "todo",
    "WIP": "doing",
    "BLOCKED": "blocked",
}
OPEN = ("todo", "doing", "blocked")
ROW = re.compile(r"^\|\s*(F\d+-T\d+|F\d+)\s*\|")


def _cells(line: str) -> List[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def read_ledger(path: Path = LEDGER) -> List[Dict[str, Any]]:
    """Every task row of the ledger, in document order."""
    tasks: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not ROW.match(line):
            continue
        cells = _cells(line)
        if len(cells) < 4:
            continue
        # The ledger's tables have four or five columns depending on the phase;
        # the state is always the last one, the acceptance everything before it.
        ident, title, state = cells[0], cells[1], cells[-1]
        acceptance = " | ".join(cells[2:-1])
        word = state.split()[0].upper() if state else ""
        tasks.append(
            {
                "id": ident,
                "phase": ident.split("-")[0],
                "title": title,
                "acceptance": acceptance,
                "ledger_state": states(word),
                "ledger_note": state,
            }
        )
    return tasks


def states(word: str) -> str:
    return STATES.get(word, "todo")


def load(path: Path = BOARD) -> Dict[str, Any]:
    if not path.is_file():
        return {"schema": SCHEMA, "tasks": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise SystemExit(f"{path.name}: unexpected schema {data.get('schema')!r}")
    return data


def save(board: Dict[str, Any], path: Path = BOARD) -> None:
    board["tasks"] = sorted(board["tasks"], key=lambda row: row["id"])
    path.write_text(
        json.dumps(board, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def sync(board: Dict[str, Any], ledger: List[Dict[str, Any]]) -> Dict[str, int]:
    """Add the tasks the ledger has and the board lacks; refresh their state."""
    by_id = {row["id"]: row for row in board["tasks"]}
    added = updated = 0
    for task in ledger:
        row = by_id.get(task["id"])
        if row is None:
            board["tasks"].append(
                {
                    **task,
                    "status": task["ledger_state"],
                    # A task the ledger already closed carries the ledger's own
                    # proof line: the board must not look unproven because it was
                    # built after the fact.
                    "evidence": (
                        task["ledger_note"]
                        if task["ledger_state"] == "done"
                        else ""
                    ),
                    "commits": [],
                    "note": "",
                }
            )
            added += 1
            continue
        if row.get("status") == "done":
            continue  # a closed task keeps the evidence that closed it
        if row.get("status") != task["ledger_state"]:
            row["status"] = task["ledger_state"]
            updated += 1
        row["title"] = task["title"]
        row["acceptance"] = task["acceptance"]
        row["ledger_note"] = task["ledger_note"]
    return {"added": added, "updated": updated}


def find(board: Dict[str, Any], ident: str) -> Dict[str, Any]:
    for row in board["tasks"]:
        if row["id"] == ident:
            return row
    raise SystemExit(f"unknown task {ident!r}; run `sync` first")


def show(row: Dict[str, Any]) -> str:
    head = f"{row['id']}  [{row['status']}]  {row['title']}"
    lines = [head, f"  accettazione: {row['acceptance']}"]
    if row.get("evidence"):
        lines.append(f"  prova: {row['evidence']}")
    if row.get("commits"):
        lines.append(f"  commit: {', '.join(row['commits'])}")
    if row.get("note"):
        lines.append(f"  nota: {row['note']}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sync", help="ledger -> board")
    listing = sub.add_parser("list", help="the board")
    listing.add_argument("--open", action="store_true")
    listing.add_argument("--phase")
    listing.add_argument("--status", choices=("todo", "doing", "done", "blocked"))
    sub.add_parser("next", help="the first open task, in ledger order")

    start = sub.add_parser("start", help="mark doing and show the acceptance")
    start.add_argument("id")
    done = sub.add_parser("done", help="mark done with its evidence")
    done.add_argument("id")
    done.add_argument("--evidence", required=True)
    done.add_argument("--commit", action="append", default=[])
    block = sub.add_parser("block", help="mark blocked with the reason")
    block.add_argument("id")
    block.add_argument("--why", required=True)

    sub.add_parser("check", help="the board is consistent, or exit 1")

    args = parser.parse_args(argv)
    board = load()

    if args.cmd == "sync":
        counts = sync(board, read_ledger())
        save(board)
        print(f"board: +{counts['added']} new, {counts['updated']} refreshed, "
              f"{len(board['tasks'])} total")
        return 0

    if args.cmd == "list":
        rows = board["tasks"]
        if args.open:
            rows = [row for row in rows if row["status"] in OPEN]
        if args.phase:
            rows = [row for row in rows if row["phase"] == args.phase]
        if args.status:
            rows = [row for row in rows if row["status"] == args.status]
        for row in rows:
            print(f"{row['id']:9} {row['status']:8} {row['title'][:88]}")
        print(f"\n{len(rows)} task(s)")
        return 0

    if args.cmd == "next":
        open_rows = [row for row in board["tasks"] if row["status"] in OPEN]
        if not open_rows:
            print("nothing open: the board is empty")
            return 0
        # A blocked task is open but not workable: `next` returns something a
        # session can begin, and leaves the blocked ones for `list --open`.
        ordered = sorted(
            open_rows,
            key=lambda row: (row["status"] != "todo", row["status"] != "doing", row["id"]),
        )
        print(show(ordered[0]))
        return 0

    if args.cmd == "start":
        row = find(board, args.id)
        row["status"] = "doing"
        save(board)
        print(show(row))
        return 0

    if args.cmd == "done":
        row = find(board, args.id)
        row["status"] = "done"
        row["evidence"] = args.evidence
        row["commits"] = sorted(set(row.get("commits", []) + args.commit))
        save(board)
        print(show(row))
        return 0

    if args.cmd == "block":
        row = find(board, args.id)
        row["status"] = "blocked"
        row["note"] = args.why
        save(board)
        print(show(row))
        return 0

    if args.cmd == "check":
        problems: List[str] = []
        ids = {row["id"] for row in board["tasks"]}
        missing = [task["id"] for task in read_ledger() if task["id"] not in ids]
        if missing:
            problems.append(f"not on the board: {', '.join(missing[:6])}")
        for row in board["tasks"]:
            if row["status"] not in (*OPEN, "done"):
                problems.append(f"{row['id']}: illegal status {row['status']!r}")
            if row["status"] == "done" and not row.get("evidence"):
                problems.append(f"{row['id']}: done without evidence")
            if row["status"] == "blocked" and not row.get("note"):
                problems.append(f"{row['id']}: blocked without a reason")
        doing = [row["id"] for row in board["tasks"] if row["status"] == "doing"]
        if len(doing) > 2:
            problems.append(f"more than two tasks in flight: {', '.join(doing)}")
        for problem in problems:
            print(f"  [FAIL] {problem}")
        print(f"\n{len(board['tasks'])} task(s), {len(problems)} problem(s)")
        return 0 if not problems else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
