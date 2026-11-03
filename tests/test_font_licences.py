"""Licence ledger gate for fetched fonts -- register, idempotency, audit.

Run offline (no socket is opened)::

    python tests/test_font_licences.py

Exercises, against temporary stores and matrices:
  a. a new record creates exactly one matrix row and one ``fetched.json`` row;
  b. the same record twice is a no-op: one row, identical sha256 both files;
  c. a licence that is absent or ``unknown`` is refused before any write;
  d. ``vx fonts audit`` returns exit 0 when coherent and exit 2 on each
     incoherence: cache without a matrix row, row without a file, unknown
     licence; the CLI path is exercised too.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import cli, fontstore  # noqa: E402
from vx.edl3 import EDLError  # noqa: E402

PASS = 0
FAIL = 0

TTF = b"\x00\x01\x00\x00" + b"\x00" * 256
DIGEST = hashlib.sha256(TTF).hexdigest()
TODAY = date.today().strftime("%d/%m/%Y")


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:70} {detail}")


def code_of(call) -> str:
    try:
        call()
    except EDLError as exc:
        return str(exc.code)
    except Exception as exc:  # a wrong exception type is a bug and must be visible
        return f"{type(exc).__name__}: {exc}"
    return ""


def fixture(work: Path) -> Path:
    """A minimal matrix with the font section already present."""
    matrix = work / "LICENSES_MATRIX.md"
    matrix.write_text(
        "# Matrice delle licenze\n\n"
        "Data della verifica: 01/01/2026.\n\n"
        "## Font scaricati dal catalogo\n\n"
        "| Famiglia | File | Licenza | Fonte | sha256 | Verificata il |\n"
        "|---|---|---|---|---|---|\n\n"
        "## Da verificare prima dell'integrazione\n\n"
        "| Componente | Licenza attesa |\n"
        "|---|---|\n",
        encoding="utf-8",
    )
    return matrix


def new_store(work: Path) -> tuple[Path, Path]:
    root = work / "fonts"
    (root / "cache").mkdir(parents=True)
    return root, fixture(work)


def put_cache(root: Path, family: str, name: str) -> Path:
    folder = root / "cache" / family
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(TTF)
    return path


def record(family: str = "TestFamily", **overrides) -> dict:
    payload = {
        "family": family,
        "licence": "OFL-1.1",
        "licence_url": "https://github.com/google/fonts/tree/main/ofl/testfamily",
        "source": "https://raw.githubusercontent.com/google/fonts/main",
        "sha256": DIGEST,
        "files": [
            {"name": f"{family}-Regular.ttf", "sha256": DIGEST, "bytes": len(TTF)}
        ],
    }
    payload.update(overrides)
    return payload


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows_for(matrix: Path, family: str) -> list[dict]:
    return [
        row
        for row in fontstore.matrix_font_rows(matrix)
        if fontstore.norm(row.get("family")) == fontstore.norm(family)
    ]


def append_row(matrix: Path, cells: tuple[str, ...]) -> None:
    text = matrix.read_text(encoding="utf-8")
    line = "| " + " | ".join(cells) + " |\n"
    marker = "\n## Da verificare"
    matrix.write_text(text.replace(marker, "\n" + line + marker, 1), encoding="utf-8")


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="vx_font_licences_"))
    root, matrix = new_store(work)
    put_cache(root, "testfamily", "TestFamily-Regular.ttf")

    # a. a new record registers in both files
    result = fontstore.register(record(), root=root, matrix_path=matrix)
    rows = rows_for(matrix, "TestFamily")
    fetched = fontstore.fetched_rows(root)
    check("register: one matrix row", len(rows) == 1, len(rows))
    check("register: row carries the licence", rows and rows[0]["licence"] == "OFL-1.1", rows[0]["licence"] if rows else "")
    check("register: row carries a digest", rows and rows[0]["sha256"].startswith(DIGEST[:16]), rows[0]["sha256"] if rows else "")
    check("register: fetched.json row written", len(fetched) == 1, len(fetched))
    check("register: fetched row licence", fetched and fetched[0]["licence"] == "OFL-1.1", fetched[0]["licence"] if fetched else "")
    check("register: matrix row carries the licence URL", rows and "ofl/testfamily" in rows[0]["source"], rows[0]["source"] if rows else "")
    check("register: document date refreshed on change", f"Data della verifica: {TODAY}." in matrix.read_text(encoding="utf-8"), TODAY)
    check("register: reports the change", result["changed"] is True, result)

    # b. the same record twice is idempotent
    matrix_before, fetched_before = sha(matrix), sha(fontstore.fetched_path(root))
    again = fontstore.register(record(), root=root, matrix_path=matrix)
    check("idempotency: second call reports no change", again["changed"] is False, again)
    check("idempotency: matrix sha256 unchanged", sha(matrix) == matrix_before, sha(matrix))
    check("idempotency: fetched sha256 unchanged", sha(fontstore.fetched_path(root)) == fetched_before, sha(fontstore.fetched_path(root)))
    check("idempotency: still one matrix row", len(rows_for(matrix, "TestFamily")) == 1, len(rows_for(matrix, "TestFamily")))
    check("idempotency: still one fetched row", len(fontstore.fetched_rows(root)) == 1, len(fontstore.fetched_rows(root)))

    # c. unknown or absent licence is refused, nothing written
    refused = code_of(
        lambda: fontstore.register(
            record("BadLicence", licence="unknown"), root=root, matrix_path=matrix
        )
    )
    check("licence unknown: refused", refused == "FONT_LICENCE_MISSING", refused)
    check("licence unknown: no matrix row", rows_for(matrix, "BadLicence") == [], rows_for(matrix, "BadLicence"))
    refused = code_of(
        lambda: fontstore.register(
            record("NoLicence", licence=""), root=root, matrix_path=matrix
        )
    )
    check("licence empty: refused", refused == "FONT_LICENCE_MISSING", refused)

    # d. audit: coherent fixture
    clean = fontstore.audit(root=root, matrix_path=matrix)
    check("audit clean: ok", clean["ok"] is True, clean["problems"])
    check("audit clean: exit 0", clean["exit_code"] == 0, clean["exit_code"])
    check("audit clean: one matrix row counted", clean["matrix_rows"] == 1, clean["matrix_rows"])

    # d1. cache without a matrix row
    put_cache(root, "orphanfamily", "OrphanFamily-Regular.ttf")
    report = fontstore.audit(root=root, matrix_path=matrix)
    check("audit orphan: not ok", report["ok"] is False, report["problems"])
    check("audit orphan: names the family", "orphanfamily" in report["missing_rows"], report["missing_rows"])
    check("audit orphan: exit 2", report["exit_code"] == 2, report["exit_code"])

    # d2. matrix row without a file
    fontstore.register(record("NoFileFamily"), root=root, matrix_path=matrix)
    report = fontstore.audit(root=root, matrix_path=matrix)
    check("audit missing file: names the family", "NoFileFamily" in report["rows_without_file"], report["rows_without_file"])

    # d3. unknown licence already written in the matrix
    put_cache(root, "mystery", "Mystery-Regular.ttf")
    append_row(
        matrix,
        ("Mystery", "Mystery-Regular.ttf", "unknown", "n/d", "n/d", TODAY),
    )
    report = fontstore.audit(root=root, matrix_path=matrix)
    check("audit unknown licence: flagged", any("Mystery" in item for item in report["unknown_licence"]), report["unknown_licence"])
    check("audit unknown licence: exit 2", report["exit_code"] == 2, report["exit_code"])

    # d4. the CLI path returns exit 2, not only the library call
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(["fonts", "audit", "--root", str(root), "--matrix", str(matrix)])
    check("cli fonts audit: exit 2 on incoherence", code == 2, code)
    check("cli fonts audit: prints the problems", "incoerenze" in buffer.getvalue(), buffer.getvalue().splitlines()[-1] if buffer.getvalue() else "")

    # d5. a coherent matrix through the CLI returns 0
    root2, matrix2 = new_store(work / "clean")
    put_cache(root2, "cleanfamily", "CleanFamily-Regular.ttf")
    fontstore.register(record("CleanFamily"), root=root2, matrix_path=matrix2)
    with contextlib.redirect_stdout(io.StringIO()):
        code = cli.main(["fonts", "audit", "--root", str(root2), "--matrix", str(matrix2)])
    check("cli fonts audit: exit 0 when coherent", code == 0, code)

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
