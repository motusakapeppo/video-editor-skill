"""Licences coherence gate: no dependency and no model without a matrix row.

Run:
    python tests/test_licenses_matrix.py
    python tests/test_licenses_matrix.py --matrix <path> --pyproject <path>
    python tests/test_licenses_matrix.py --today 2026-10-28

Three sources are cross-checked:

1. ``pyproject.toml`` -> the ``dependencies`` array of the ``[project]`` table;
2. ``LICENSES_MATRIX.md`` -> the "Verificate su questa macchina" table
   (Componente | Versione | Licenza | Verificata da | Uso) and the document
   level line ``Data della verifica: gg/mm/aaaa``;
3. the models the code can load: the pinned records of ``scripts/vx/models.py``
   (licence, source URL, sha256) plus every file under ``assets/models/``; the
   registry is cross-checked against ``scripts/vx``, so a record no code
   mentions is caught too.

Failure conditions, one check each, every detail naming the component: a
declared dependency without a row; a model without a row and without a pinned
record; a row missing one of the five cells (an empty version is one of them);
the verification date absent, not exactly ``gg/mm/aaaa``, or older than twelve
months from ``today``; ``Uso`` outside the three admitted values. A row whose
licence says "da confermare" is a declared risk, not an omission: it never
fails a check, it is counted and printed.

Why a model may be covered by a pinned record instead of a row: a bundled
artifact whose bytes are pinned in ``scripts/vx/models.py`` carries its licence
and upstream URL in the same machine-readable record, and that record is
verified here against the sha256 of the file on disk. Those models are printed
under COVERAGE GAP so the missing documentation line stays visible; a model
that is neither in the matrix nor pinned fails. A row whose ``Uso`` is
``libreria`` never covers a model: the matrix's rule 4 says the engine licence
does not cover the weights, so only ``processo esterno`` and ``opzionale`` rows
are eligible.

Python 3.10 has no ``tomllib``, so the dependency array is read by a small
explicit parser: a regex anchors ``dependencies = [``, a quote-safe scan finds
the closing bracket, a quote-safe split cuts the items. Limitation: only that
array is read, not the TOML grammar -- a dependency expressed through a table,
an include or a variable is invisible, and a comment inside the array is not
stripped, so a commented-out requirement still counts as declared. Model scope
limitation: a bundle neither registered nor present on disk (the MediaPipe
``face_landmarker.task`` fallback) cannot load weights and is out of scope;
drop the file into ``assets/models/`` and the gate demands a row for it.

stdlib only, no network, no new dependency.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import re
import sys
from pathlib import Path

# allow: SIZE_OK - one standalone gate: three parsers (pyproject array, matrix
# tables, model registry) plus one named check per failure condition; splitting
# it would break the repo's one-file-per-test contract.

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = ROOT / "LICENSES_MATRIX.md"
DEFAULT_PYPROJECT = ROOT / "pyproject.toml"
SCRIPTS = ROOT / "scripts"
VX_DIR = SCRIPTS / "vx"
ASSETS = ROOT / "assets" / "models"

COLUMNS = ("Componente", "Versione", "Licenza", "Verificata da", "Uso")
USES = ("libreria", "processo esterno", "opzionale")
DATE_TEXT = re.compile(r"^\d{2}/\d{2}/\d{4}$")
# Any triple that reads as a date, in any separator, so a wrong format is refused
# instead of silently falling back to the document date. Version cells such as
# ``2.2.6`` or ``5.0.0.93`` never carry a four-digit group and cannot match.
DATE_ISH = re.compile(r"\d{1,4}/\d{1,2}/\d{1,4}|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\.\d{1,2}\.\d{4}\b")
DOC_DATE = re.compile(r"Data della verifica\s*:\s*(\S+)")
GENERIC_MODEL_TOKENS = {"faster", "small", "model", "models", "weights", "pesi"}

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


# -- pyproject: the dependency array, without tomllib ------------------------ #


def project_block(text: str) -> str | None:
    start = re.search(r"^\[project\]\s*$", text, flags=re.MULTILINE)
    if start is None:
        return None
    tail = text[start.end() :]
    following = re.search(r"^\[", tail, flags=re.MULTILINE)
    return tail[: following.start()] if following else tail


def dependencies_array(block: str) -> str | None:
    opening = re.search(r"^dependencies\s*=\s*\[", block, flags=re.MULTILINE)
    if opening is None:
        return None
    index, depth, quote = opening.end(), 1, ""
    while index < len(block):
        char = block[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return block[opening.end() : index]
        index += 1
    return None


def requirement_items(raw: str) -> list[str]:
    items, buffer, quote = [], [], ""
    for char in raw:
        if quote:
            buffer.append(char)
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
            buffer.append(char)
        elif char == ",":
            items.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    items.append("".join(buffer))
    cleaned = (item.strip().strip("\"'").strip() for item in items)
    return [item for item in cleaned if item]


def requirement_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    return match.group(1) if match else ""


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


# -- matrix ---------------------------------------------------------------- #


def matrix_tables(text: str) -> list[list[list[str]]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            current.append(stripped)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    tables: list[list[list[str]]] = []
    for block in blocks:
        rows: list[list[str]] = []
        for raw in block:
            cells = [c.strip().replace("**", "") for c in raw.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            rows.append(cells)
        tables.append(rows)
    return tables


def find_table(tables: list[list[list[str]]], *required: str) -> list[list[str]] | None:
    wanted = [name.lower() for name in required]
    for table in tables:
        if not table:
            continue
        header = [cell.lower() for cell in table[0]]
        if all(name in header for name in wanted):
            return table[1:]
    return None


def parse_date(raw: str) -> dt.date | None:
    if not DATE_TEXT.match(raw):
        return None
    try:
        return dt.datetime.strptime(raw, "%d/%m/%Y").date()
    except ValueError:
        return None


def twelve_months_before(today: dt.date) -> dt.date:
    try:
        return today.replace(year=today.year - 1)
    except ValueError:  # 29 February
        return today.replace(year=today.year - 1, day=28)


def row_for_dependency(name: str, rows: list[list[str]]) -> list[str] | None:
    target = normalise(name)
    for row in rows:
        component = normalise(row[0]) if row else ""
        if component == target or component.startswith((target + " ", target + "(")):
            return row
    return None


# -- models ---------------------------------------------------------------- #


def registry() -> dict:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from vx import models  # noqa: E402 - repo package, stdlib-only entry

    return models.RECORDS


def code_references() -> str:
    texts = []
    launcher = SCRIPTS / "vx.py"
    for path in [launcher, *sorted(VX_DIR.glob("*.py"))]:
        if path.name == "models.py" or not path.is_file():
            continue
        texts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(texts)


def model_tokens(*names: str) -> list[str]:
    tokens = set()
    for name in names:
        for token in re.split(r"[^a-z0-9]+", name.lower()):
            if len(token) >= 5 and not token.isdigit() and token not in GENERIC_MODEL_TOKENS:
                tokens.add(token)
    return sorted(tokens, key=len, reverse=True)


def row_for_model(tokens: list[str], rows: list[list[str]]) -> tuple[list[str] | None, str]:
    for row in rows:
        component = re.sub(r"[^a-z0-9]+", " ", (row[0] if row else "").lower())
        for token in tokens:
            if re.search(rf"\b{re.escape(token)}\b", component):
                return row, token
    return None, ""


def pinned_record(record: dict) -> tuple[bool, str]:
    hint = str(record.get("path_hint") or "")
    if not record.get("licence") or not record.get("source_url"):
        return False, "record senza licenza o source_url"
    path = ROOT / hint
    if not path.is_file():
        return False, f"artefatto assente: {Path(hint).name}"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != str(record.get("sha256") or ""):
        return False, f"sha256 diverso: {Path(hint).name}"
    return True, f"{Path(hint).name} sha256 ok"


# -- main ------------------------------------------------------------------ #


IMPORT_ALIASES = {
    "PIL": "Pillow",
    "cv2": "opencv-contrib-python",
    "faster_whisper": "faster-whisper",
}


def imported_modules() -> list[str]:
    """Top-level third-party modules the engine actually imports."""
    local = {path.stem for path in VX_DIR.glob("*.py")}
    stdlib = set(sys.stdlib_module_names)
    found: set[str] = set()
    for path in [SCRIPTS / "vx.py", *sorted(VX_DIR.glob("*.py"))]:
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return sorted(
        name for name in found if name not in stdlib and name not in local and name != "vx"
    )


def check_imports(rows: list[list[str]], imports: list[str]) -> None:
    print("\n-- every imported third-party module is documented")
    for module in imports:
        package = IMPORT_ALIASES.get(module, module)
        row = row_for_dependency(package, rows)
        check(
            f"import {module}: covered by a matrix row",
            row is not None,
            "" if row is not None else f"no row for {package}",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Licences coherence gate.")
    parser.add_argument("--matrix", help="matrix to check (default: repo root)")
    parser.add_argument("--pyproject", help="manifest to check (default: repo root)")
    parser.add_argument("--today", help="ISO date for the 12-month window")
    args = parser.parse_args(argv)

    matrix_path = Path(args.matrix) if args.matrix else DEFAULT_MATRIX
    pyproject_path = Path(args.pyproject) if args.pyproject else DEFAULT_PYPROJECT
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    cutoff = twelve_months_before(today)
    print(f"today: {today:%d/%m/%Y} — cutoff twelve months: {cutoff:%d/%m/%Y}")
    print(f"pyproject: {pyproject_path}")
    print(f"matrix:    {matrix_path}")

    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    matrix_text = matrix_path.read_text(encoding="utf-8")
    block = project_block(pyproject_text)
    check("[project] table present", block is not None, pyproject_path.name)
    raw_dependencies = dependencies_array(block or "")
    check(
        "[project] dependencies array parsed",
        raw_dependencies is not None,
        f"{len(requirement_items(raw_dependencies or ''))} declared",
    )
    declared = [
        requirement_name(item)
        for item in requirement_items(raw_dependencies or "")
        if requirement_name(item)
    ]

    tables = matrix_tables(matrix_text)
    rows = find_table(tables, *COLUMNS)
    check(
        "matrix: 'Verificate' table with the five columns",
        rows is not None,
        f"{len(rows or [])} rows",
    )
    rows = rows or []
    declared_only = find_table(tables, "Componente", "Licenza attesa")
    print(f"matrix: {len(rows)} verified rows, {len(declared_only or [])} declared-not-verified rows")

    found_date = DOC_DATE.search(matrix_text)
    raw_document_date = found_date.group(1).rstrip(".,;:") if found_date else ""
    check("matrix: 'Data della verifica' present", bool(raw_document_date), raw_document_date or "missing")
    document_date = parse_date(raw_document_date)
    check(
        "matrix: document date is exactly gg/mm/aaaa",
        document_date is not None,
        raw_document_date or "missing",
    )
    check(
        "matrix: document date not older than twelve months",
        document_date is not None and document_date >= cutoff,
        raw_document_date or "missing",
    )
    malformed = [
        f"{row[0]}: {cell}"
        for row in rows
        for cell in row
        if DATE_ISH.search(cell) and parse_date(cell.strip()) is None
    ]
    check("matrix: no row carries a malformed date", not malformed, ", ".join(malformed) or "none")

    confirmed: list[str] = []
    for row in rows:
        component = (row[0] if row else "") or "(empty)"
        gaps = [COLUMNS[i] for i in range(len(COLUMNS)) if i >= len(row) or not row[i].strip()]
        check(
            f"row {component}: five columns filled",
            not gaps,
            ("missing: " + ",".join(gaps)) if gaps else "5/5",
        )
        use = (row[4].strip().lower() if len(row) > 4 else "") or "(empty)"
        check(f"row {component}: use allowed", use in USES, use)
        own_date = next((cell.strip() for cell in row if DATE_TEXT.match(cell.strip())), "")
        effective = parse_date(own_date) if own_date else document_date
        raw_effective = own_date or raw_document_date or "missing"
        check(
            f"row {component}: verification date within twelve months",
            effective is not None and effective >= cutoff,
            raw_effective,
        )
        if len(row) > 2 and "da confermare" in row[2].lower():
            confirmed.append(component)

    for name in declared:
        matched = row_for_dependency(name, rows)
        check(
            f"dependency {name}: matrix row",
            matched is not None,
            f"-> {matched[0]}" if matched else "nessuna riga",
        )
    missing_dependencies = [name for name in declared if row_for_dependency(name, rows) is None]
    check(
        "dependencies: every declared dependency has a row",
        not missing_dependencies,
        ", ".join(missing_dependencies) or f"{len(declared)} declared",
    )

    records = registry()
    references = code_references()
    assets = sorted(p.name for p in ASSETS.iterdir() if p.is_file()) if ASSETS.is_dir() else []
    coverage_gaps: list[str] = []
    weight_rows = [
        row for row in rows if len(row) > 4 and row[4].strip().lower() != "libreria"
    ]
    for key, record in sorted(records.items()):
        hint = str(record.get("path_hint") or "")
        tokens = model_tokens(key, Path(hint).stem)
        row, token = row_for_model(tokens, weight_rows)
        pinned, pinned_detail = pinned_record(record)
        detail = (
            f"matrix row '{row[0]}' via '{token}'"
            if row is not None
            else f"pinned record: {pinned_detail}; no weight row"
        )
        check(f"model {key}: matrix row or pinned record", row is not None or pinned, detail)
        licence = (row[2] if row is not None else str(record.get("licence") or "")).strip()
        check(f"model {key}: licence not empty", bool(licence), licence or "(empty)")
        mentioned = [t for t in tokens if re.search(rf"\b{re.escape(t)}\b", references, re.IGNORECASE)]
        check(
            f"model {key}: referenced by scripts/vx",
            bool(mentioned),
            ",".join(mentioned) or "(no reference)",
        )
        if row is None and pinned:
            check(
                f"model {key}: documented by a matrix row",
                False,
                f"pinned record only ({Path(hint).name}); the matrix must carry a row too",
            )

    for name in assets:
        registered = any(
            Path(str(record.get("path_hint") or "")).name.lower() == name.lower()
            for record in records.values()
        )
        check(f"asset {name}: registered in scripts/vx/models.py", registered, name)

    print(f"\nda confermare: {len(confirmed)} row(s)")
    for component in confirmed:
        print(f"  - {component}")
    if coverage_gaps:
        print(f"\nCOVERAGE GAP: {len(coverage_gaps)} model(s) without a matrix row")
        for gap in coverage_gaps:
            print(f"  - {gap}")
    imports = imported_modules()
    check_imports(rows, imports)
    print(
        f"\n{PASS} PASS / {FAIL} FAIL — {len(declared)} declared, {len(imports)} imported, "
        f"{len(records)} models, {len(rows)} matrix rows"
    )
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
