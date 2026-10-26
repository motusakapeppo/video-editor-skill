"""Portability gate: no machine-specific absolute path or brand name in tracked files.

Run:
    python tests/test_portability.py

The scan covers every file git would commit: tracked plus untracked-not-ignored
(``git ls-files --cached --others --exclude-standard``), so a brand-new file
cannot slip past the gate just because it has not been committed yet. It fails on:

* a drive-letter absolute path (``C:\\...`` / ``D:/...``), with a negative
  lookbehind so ``https://`` is not a false positive;
* the machine-specific user prefix ``c:\\users\\`` (case-insensitive);
* the brand string ``vox populi`` (case-insensitive);
* the brand string ``motus`` inside ``scripts/**/*.py`` and
  ``tests/run_color.py`` (case-insensitive). Prose under ``references/`` is
  intentionally out of scope for the brand check.

Exit code 1 if anything is found, 0 otherwise.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

TEXT_EXT = {".py", ".json", ".md", ".txt", ".yaml", ".yml", ".cfg", ".ini"}
DRIVE_PATH = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]")
USER_PATH = "c:\\users\\"
BRAND = "vox populi"
BRAND_CODE = "motus"

# These two files assert that the forbidden literals are absent, so they must
# contain them. Skipping them is deliberate; any other file is still scanned.
META_ASSERTIONS = {
    "tests/test_docs_consistency.py",
    "tests/test_delivery_gate_optional.py",
}


def scanned_files() -> list[Path]:
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        capture_output=True,
        check=True,
    )
    names = proc.stdout.decode("utf-8", "replace").split("\0")
    return [ROOT / name for name in names if name]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def scan() -> list[str]:
    problems: list[str] = []
    for path in scanned_files():
        if path.resolve() == SELF:
            continue
        if path.suffix.lower() not in TEXT_EXT:
            continue
        text = _read(path)
        if text is None:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel in META_ASSERTIONS:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in DRIVE_PATH.finditer(line):
                problems.append(
                    f"{rel}:{lineno}: drive path {match.group(0)!r} in {line.strip()!r}"
                )
            low = line.lower()
            if USER_PATH in low:
                problems.append(
                    f"{rel}:{lineno}: user path {USER_PATH!r} in {line.strip()!r}"
                )
            if BRAND in low:
                problems.append(f"{rel}:{lineno}: brand {BRAND!r} in {line.strip()!r}")
    return problems


def branding() -> list[str]:
    problems: list[str] = []
    for path in scanned_files():
        if path.resolve() == SELF:
            continue
        rel = path.relative_to(ROOT).as_posix()
        in_scope = (rel.startswith("scripts/") and rel.endswith(".py")) or (
            rel == "tests/run_color.py"
        )
        if not in_scope:
            continue
        text = _read(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if BRAND_CODE in line.lower():
                problems.append(
                    f"{rel}:{lineno}: brand {BRAND_CODE!r} in {line.strip()!r}"
                )
    return problems


def main() -> int:
    problems = scan() + branding()
    if problems:
        for problem in problems:
            print(problem)
        print(f"\n{len(problems)} portability violation(s)")
        return 1
    print("portability: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
