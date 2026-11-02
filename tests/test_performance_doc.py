"""The performance report is generated, and every row says how to repeat it."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def main() -> int:
    doc = ROOT / "PLAN" / "PERFORMANCE.md"
    check("the report exists", doc.is_file(), doc.name)
    if not doc.is_file():
        print(f"\n{PASS} PASS / {FAIL} FAIL")
        return 1

    text = doc.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| ") and "`python" in line]
    check(
        "it lists at least five measures, each with its command",
        len(rows) >= 5,
        f"{len(rows)} row(s)",
    )
    check(
        "the compile measurement is a positive number",
        any(" s**" in row and "0.0" not in row for row in rows),
        rows[0][:90] if rows else "",
    )
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_performance.py"), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    check(
        "the generator's own check passes",
        proc.returncode == 0,
        (proc.stdout or proc.stderr).strip()[-90:],
    )
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
