"""vx.fontstore -- the disk side of vx.fontfetch: catalogue, cache, fetched.json, digests.

Nothing here decides whether a family may be used: the licence gate and the
network gate live in :mod:`vx.fontfetch`. This module answers "which catalogue
row is this family", "what is bundled", "what is recorded" and "does the file
still hash to what the row says".

The digest is :func:`vx.fontlib.sha256_file` and the bundled directory is
:func:`vx.fonts.package_font_dir`, both imported on use so importing this module
stays cheap.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

from . import fonts

__all__ = [
    "CACHE_DIR_NAME", "CATALOGUE_NAME", "ENV_CATALOGUE", "FETCHED_NAME",
    "FONT_SUFFIXES", "MAGIC", "MATRIX_NAME", "MATRIX_SECTION", "SCHEMA",
    "audit", "bundled_files", "cache_dir", "cached_row", "catalogue",
    "catalogue_file", "family_of_stem", "fetched_path", "fetched_rows",
    "files_metadata", "find", "licence_known", "magic_ok",
    "matrix_font_rows", "norm",
    "record", "recorded_paths", "refuse", "register", "sha256",
    "store_root", "verify", "write_bytes", "write_rows",
]

SCHEMA = "vx.fontfetch/1"
CATALOGUE_NAME = "catalogue.json"
ENV_CATALOGUE = "VX_FONT_CATALOGUE"
CACHE_DIR_NAME = "cache"
FETCHED_NAME = "fetched.json"
FONT_SUFFIXES = (".ttf", ".otf")

MATRIX_NAME = "LICENSES_MATRIX.md"
MATRIX_SECTION = "## Font scaricati dal catalogo"
MATRIX_COLUMNS = ("Famiglia", "File", "Licenza", "Fonte", "sha256", "Verificata il")

#: The four prefixes a font file may start with. Anything else is not a font.
MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")

_UNKNOWN_LICENCES = {"", "unknown", "n/d", "nd", "da confermare", "daconfermare"}
_DOC_DATE = re.compile(r"(Data della verifica\s*:\s*)(\d{2}/\d{2}/\d{4})")

_DOCUMENT: Dict[str, Dict[str, Any]] = {}


def refuse(code: str, detail: str, where: str = "") -> Exception:
    """The project's refusal, built with ``edl3`` imported on demand."""
    from .edl3 import EDLError  # noqa: PLC0415 -- keeps the font modules import-cheap

    return EDLError(code, detail, where)


def norm(text: Any) -> str:
    """The comparison key for a family name: case, spaces, underscores ignored."""
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def licence_known(licence: Any) -> bool:
    """False for an absent, ``unknown`` or still-to-confirm licence."""
    return norm(licence) not in _UNKNOWN_LICENCES


def family_of_stem(stem: str) -> str:
    """``Antonio-Regular`` -> ``Antonio``; ``Inter[opsz,wght]`` -> ``Inter``."""
    base = str(stem or "").split("[", 1)[0]
    return base.split("-")[0].strip() or str(stem or "")


def sha256(path: Union[str, Path]) -> str:
    from .fontlib import sha256_file  # noqa: PLC0415 -- one digest implementation

    return sha256_file(path)


def store_root(dest: Optional[Union[str, Path]] = None) -> Path:
    return Path(dest).expanduser() if dest is not None else fonts.package_font_dir()


def catalogue_file(path: Optional[Union[str, Path]] = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    env = os.environ.get(ENV_CATALOGUE)
    return Path(env).expanduser() if env else store_root() / CATALOGUE_NAME


def catalogue(path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """The catalogue document (``source``, ``checked``, ``families``); read-only.

    Cached per resolved path, so repeated :func:`find` calls parse the 424 KB
    document once.
    """
    target = catalogue_file(path)
    key = str(target.resolve())
    if key in _DOCUMENT:
        return _DOCUMENT[key]
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise refuse("FONT_CATALOGUE_MISSING", str(exc), str(target))
    except json.JSONDecodeError as exc:
        raise refuse("FONT_CATALOGUE_INVALID", str(exc), str(target))
    if not isinstance(data, Mapping) or not isinstance(data.get("families"), list):
        raise refuse(
            "FONT_CATALOGUE_INVALID", "atteso un oggetto con 'families' come lista", str(target)
        )
    _DOCUMENT[key] = dict(data)
    return _DOCUMENT[key]


def find(
    family: Any, *, catalogue_path: Optional[Union[str, Path]] = None
) -> Optional[Dict[str, Any]]:
    """The catalogue row for ``family``, or None. Case/space/underscore blind."""
    wanted = norm(family)
    if not wanted:
        return None
    for row in catalogue(catalogue_path).get("families") or []:
        if not isinstance(row, Mapping):
            continue
        if norm(row.get("family")) == wanted:
            return dict(row)
        if norm(Path(str(row.get("directory") or "")).name) == wanted:
            return dict(row)
    return None


def cache_dir(root: Union[str, Path], family: str) -> Path:
    return Path(root) / CACHE_DIR_NAME / str(family)


def fetched_path(root: Union[str, Path]) -> Path:
    return Path(root) / FETCHED_NAME


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _tmp_for(path: Path) -> Tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    return tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))


def write_json(path: Union[str, Path], payload: Any) -> None:
    target = Path(path)
    fd, tmp = _tmp_for(target)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, target)
    except BaseException:
        _unlink(tmp)
        raise


def write_bytes(path: Union[str, Path], data: bytes) -> None:
    target = Path(path)
    fd, tmp = _tmp_for(target)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, target)
    except BaseException:
        _unlink(tmp)
        raise


def _write_text(path: Union[str, Path], text: str) -> None:
    target = Path(path)
    fd, tmp = _tmp_for(target)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, target)
    except BaseException:
        _unlink(tmp)
        raise


def fetched_rows(root: Union[str, Path]) -> List[Dict[str, Any]]:
    """Every recorded fetch. An unreadable file reads as no rows, never a crash."""
    try:
        data = json.loads(fetched_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("families") if isinstance(data, Mapping) else None
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def write_rows(root: Union[str, Path], rows: Iterable[Mapping[str, Any]]) -> None:
    write_json(fetched_path(root), {"schema": SCHEMA, "families": list(rows)})


def cached_row(root: Union[str, Path], family: str) -> Optional[Dict[str, Any]]:
    wanted = norm(family)
    for row in fetched_rows(root):
        if norm(row.get("family")) == wanted:
            return row
    return None


def bundled_files(root: Union[str, Path], family: str) -> List[Path]:
    """Font files already in ``<root>`` (the bundled set), matched by family."""
    base = Path(root)
    wanted = norm(family)
    if not wanted or not base.is_dir():
        return []
    return sorted(
        path
        for path in base.iterdir()
        if path.is_file()
        and path.suffix.lower() in FONT_SUFFIXES
        and norm(family_of_stem(path.stem)) == wanted
    )


def recorded_paths(root: Union[str, Path], family: str, row: Mapping[str, Any]) -> List[Path]:
    base = cache_dir(root, family)
    return [
        base / name
        for name in (str(item.get("name") or "") for item in row.get("files") or [])
        if name
    ]


def files_metadata(paths: Iterable[Union[str, Path]], origin: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in paths:
        candidate = Path(path)
        if not candidate.is_file():
            continue
        out.append(
            {
                "name": candidate.name,
                "path": str(candidate),
                "sha256": sha256(candidate),
                "bytes": candidate.stat().st_size,
                "origin": origin,
            }
        )
    return out


def record(
    family: Any,
    *,
    dest: Optional[Union[str, Path]] = None,
    catalogue_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Bundled + cached + fetched metadata for one family, merged and read-only.

    ``status`` is ``bundled`` when the family ships with the repo, ``cached``
    when ``fetched.json`` records it and its files are on disk, ``absent``
    otherwise. Digests in ``files`` are read from disk, so a tampered cache shows
    its new digest here; :func:`vx.fontfetch.check` is what refuses the drift.
    """
    root = store_root(dest)
    row = find(family, catalogue_path=catalogue_path)
    name = str(row.get("family")) if row is not None else str(family or "").strip()
    if not name:
        raise refuse("FONT_UNKNOWN_FAMILY", "family vuota")
    fetched = cached_row(root, name)
    bundled = bundled_files(root, name)
    if not bundled and row is None and fetched is None:
        raise refuse(
            "FONT_UNKNOWN_FAMILY", f"{name!r} non e' nel catalogo ne' in {root}", str(root)
        )
    files = files_metadata(bundled, "bundled")
    if fetched is not None:
        files += files_metadata(recorded_paths(root, name, fetched), "cache")
    return {
        "family": name,
        "licence": str((row or fetched or {}).get("licence") or "unknown"),
        "directory": str((row or fetched or {}).get("directory") or ""),
        "source": "bundled" if bundled else str((fetched or {}).get("source") or ""),
        "status": "bundled" if bundled else ("cached" if files else "absent"),
        "bundled": bool(bundled),
        "files": files,
        "fetched_utc": str((fetched or {}).get("fetched_utc") or ""),
        "catalogue": str(catalogue_file(catalogue_path)),
    }


def magic_ok(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] in MAGIC


def verify(root: Union[str, Path], family: str) -> List[str]:
    """Problems for one recorded family; empty means every file still matches."""
    row = cached_row(root, family)
    if row is None:
        return []
    problems: List[str] = []
    base = cache_dir(root, family)
    for item in row.get("files") or []:
        name = str(item.get("name") or "")
        expected = str(item.get("sha256") or "").lower()
        path = base / name
        if not path.is_file():
            problems.append(f"{family}/{name}: assente dalla cache ({path})")
            continue
        actual = sha256(path)
        if actual != expected:
            problems.append(f"{family}/{name}: sha256 {actual} != {expected} ({path})")
    return problems


# ---------------------------------------------------------------------------
# licence matrix: registration and audit
# ---------------------------------------------------------------------------


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _default_matrix_path() -> Path:
    return Path(__file__).resolve().parents[2] / MATRIX_NAME


def _matrix_for_root(root: Union[str, Path]) -> Path:
    """The repo matrix for the real cache, a local one for a temp store.

    Tests and dry runs point ``root`` at a temporary directory; writing the
    repository's matrix from there would pollute a tracked file, so that root
    gets its own matrix next to ``cache/``.
    """
    candidate = Path(root)
    try:
        if candidate.resolve() == fonts.package_font_dir():
            return _default_matrix_path()
    except OSError:
        pass
    return candidate / MATRIX_NAME


def _split_row(line: str) -> List[str]:
    return [cell.strip() for cell in str(line).strip().strip("|").split("|")]


def _section_bounds(lines: List[str]) -> Optional[Tuple[int, int]]:
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == MATRIX_SECTION), None
    )
    if start is None:
        return None
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("## ")
        ),
        len(lines),
    )
    return start, end


def matrix_font_rows(matrix_path: Union[str, Path]) -> List[Dict[str, str]]:
    """Rows of the font section of ``LICENSES_MATRIX.md`` (empty when absent).

    A row maps ``family``, ``file``, ``licence``, ``source``, ``sha256`` and
    ``checked`` to its cells. Only the dedicated font table is read: the
    dependency tables keep their own five-column shape.
    """
    try:
        text = Path(matrix_path).read_text(encoding="utf-8")
    except OSError:
        return []
    lines = text.splitlines()
    bounds = _section_bounds(lines)
    if bounds is None:
        return []
    rows: List[Dict[str, str]] = []
    for line in lines[bounds[0] + 1 : bounds[1]]:
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = _split_row(stripped)
        if len(cells) < len(MATRIX_COLUMNS):
            continue
        if cells[0].lower() == MATRIX_COLUMNS[0].lower():
            continue
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
            continue
        if not cells[0].strip():
            continue
        rows.append(
            {
                "family": cells[0],
                "file": cells[1],
                "licence": cells[2],
                "source": cells[3],
                "sha256": cells[4],
                "checked": cells[5],
            }
        )
    return rows


def _normalise_files(record: Mapping[str, Any]) -> List[Dict[str, Any]]:
    digest = str(record.get("sha256") or "").strip().lower()
    out: List[Dict[str, Any]] = []
    for item in record.get("files") or []:
        if isinstance(item, Mapping):
            name = str(item.get("name") or item.get("file") or "").strip()
            sha = str(item.get("sha256") or digest).strip().lower()
            size = item.get("bytes")
        else:
            name, sha, size = str(item or "").strip(), digest, None
        if not name and not sha:
            continue
        entry: Dict[str, Any] = {"name": name, "sha256": sha}
        if size not in (None, ""):
            try:
                entry["bytes"] = int(size)
            except (TypeError, ValueError):
                pass
        out.append(entry)
    if not out:
        name = str(record.get("file") or record.get("name") or "").strip()
        if name or digest:
            out.append({"name": name, "sha256": digest})
    return out


def _fetch_core(row: Mapping[str, Any]) -> Tuple[Any, ...]:
    files = sorted(
        (str(item.get("name") or ""), str(item.get("sha256") or ""))
        for item in row.get("files") or []
        if isinstance(item, Mapping)
    )
    return (
        str(row.get("licence") or ""),
        str(row.get("directory") or ""),
        str(row.get("source") or ""),
        str(row.get("licence_url") or ""),
        tuple(files),
    )


def _same_row(existing: Mapping[str, str], values: Mapping[str, str]) -> bool:
    return all(
        str(existing.get(key) or "").strip() == str(values.get(key) or "").strip()
        for key in ("family", "file", "licence", "source", "sha256")
    )


def _render_section(rows: Iterable[Mapping[str, str]]) -> List[str]:
    out = [MATRIX_SECTION, "", "| " + " | ".join(MATRIX_COLUMNS) + " |"]
    out.append("|" + "|".join(["---"] * len(MATRIX_COLUMNS)) + "|")
    for row in sorted(rows, key=lambda item: str(item.get("family") or "").casefold()):
        cells = [str(row.get(key) or "").replace("|", "\\|") for key in (
            "family", "file", "licence", "source", "sha256", "checked"
        )]
        out.append("| " + " | ".join(cells) + " |")
    return out


def _minimal_matrix(today: str) -> str:
    return (
        "# Matrice delle licenze\n\n"
        f"Data della verifica: {today}.\n\n"
        "Regola: nessun font entra in cache senza una riga qui.\n"
    )


def _register_matrix(
    matrix_path: Union[str, Path],
    values: Mapping[str, str],
    *,
    today: str,
) -> bool:
    """Insert or update one font row; True when the file changed."""
    target = Path(matrix_path)
    text = target.read_text(encoding="utf-8") if target.is_file() else _minimal_matrix(today)
    lines = text.splitlines()
    bounds = _section_bounds(lines)
    rows: List[Dict[str, str]] = []
    if bounds is not None:
        rows = matrix_font_rows(target)
    key = norm(values.get("family"))
    existing = next((row for row in rows if norm(row.get("family")) == key), None)
    if existing is not None and _same_row(existing, values):
        return False
    if existing is not None:
        rows = [row for row in rows if norm(row.get("family")) != key]
    rows.append(dict(values, checked=today))

    block = _render_section(rows)
    if bounds is not None:
        start, end = bounds
        new_lines = lines[:start] + block + [""] + lines[end:]
    else:
        insert_at = next(
            (
                index
                for index, line in enumerate(lines)
                if line.startswith("## Da verificare")
            ),
            len(lines),
        )
        pad = [""] if insert_at and lines[insert_at - 1].strip() else []
        new_lines = lines[:insert_at] + pad + block + [""] + lines[insert_at:]

    new_text = "\n".join(new_lines).rstrip() + "\n"
    new_text = _DOC_DATE.sub(
        lambda match: match.group(1) + today, new_text, count=1
    )
    if new_text != text:
        _write_text(target, new_text)
        return True
    return False


def register(
    record: Mapping[str, Any],
    *,
    root: Optional[Union[str, Path]] = None,
    matrix_path: Optional[Union[str, Path]] = None,
    today: Optional[str] = None,
) -> Dict[str, Any]:
    """Record one downloaded font in ``fetched.json`` and in the licence matrix.

    Idempotent by construction: an existing row for the same family is updated
    in place, never duplicated, and an unchanged record leaves both files byte
    for byte as they were. A record whose ``licence`` is absent or ``unknown``
    is refused before anything is written: a font without a licence is not a
    detail to fill in later.
    """
    if not isinstance(record, Mapping):
        raise refuse("FONT_RECORD_INVALID", "atteso un record (mapping) del font")
    family = str(record.get("family") or "").strip()
    if not family:
        raise refuse("FONT_FAMILY_MISSING", "record senza 'family'")
    licence = str(record.get("licence") or "").strip()
    if norm(licence) in _UNKNOWN_LICENCES:
        raise refuse(
            "FONT_LICENCE_MISSING",
            f"{family}: licenza {licence or 'assente'!r} non registrabile nella matrice",
            str(record.get("source") or ""),
        )

    root_path = store_root(root)
    target = (
        Path(matrix_path)
        if matrix_path is not None
        else _matrix_for_root(root_path)
    )
    stamp = str(today or date.today().strftime("%d/%m/%Y"))
    source = str(record.get("source") or "").strip()
    licence_url = str(record.get("licence_url") or "").strip()
    directory = str(record.get("directory") or "").strip().strip("/")
    files = _normalise_files(record)

    candidate: Dict[str, Any] = {
        "family": family,
        "licence": licence,
        "directory": directory,
        "files": files,
        "source": source,
    }

    rows = fetched_rows(root_path)
    key = norm(family)
    existing = next((row for row in rows if norm(row.get("family")) == key), None)
    fetched_changed = existing is None or _fetch_core(existing) != _fetch_core(candidate)
    if fetched_changed:
        candidate["fetched_utc"] = (
            str(record.get("fetched_utc") or "").strip() or _now_utc()
        )
        kept = [row for row in rows if norm(row.get("family")) != key]
        kept.append({k: v for k, v in candidate.items() if v not in ("", [], None)})
        kept.sort(key=lambda item: str(item.get("family") or "").lower())
        write_rows(root_path, kept)
    else:
        candidate = dict(existing)

    names = [str(item.get("name") or "") for item in files if item.get("name")]
    if not names:
        file_cell = "n/d"
    elif len(names) <= 3:
        file_cell = "<br>".join(names)
    else:
        file_cell = f"{len(names)} file"
    digest = next(
        (str(item.get("sha256") or "") for item in files if item.get("sha256")),
        str(record.get("sha256") or "").strip().lower(),
    )
    values = {
        "family": family,
        "file": file_cell,
        "licence": licence,
        "source": f"[google/fonts]({licence_url})" if licence_url else (source or "n/d"),
        "sha256": f"{digest[:16]}…" if digest else "n/d",
    }
    matrix_changed = _register_matrix(target, values, today=stamp)
    return {
        "family": family,
        "licence": licence,
        "fetched_changed": fetched_changed,
        "matrix_changed": matrix_changed,
        "changed": fetched_changed or matrix_changed,
        "matrix": str(target),
        "fetched": str(fetched_path(root_path)),
    }


def _cache_families(root: Union[str, Path]) -> List[str]:
    base = Path(root) / CACHE_DIR_NAME
    if not base.is_dir():
        return []
    out: List[str] = []
    for child in sorted(base.iterdir()):
        if child.is_dir() and any(
            path.is_file() and path.suffix.lower() in FONT_SUFFIXES
            for path in child.iterdir()
        ):
            out.append(child.name)
    return out


def _cache_family_dir(root: Union[str, Path], family: str) -> Optional[Path]:
    base = Path(root) / CACHE_DIR_NAME
    exact = base / str(family)
    if exact.is_dir():
        return exact
    wanted = norm(family)
    if not base.is_dir():
        return None
    for child in base.iterdir():
        if child.is_dir() and norm(child.name) == wanted:
            return child
    return None


def _row_has_file(root: Union[str, Path], family: str, file_cell: str) -> bool:
    raw = str(file_cell or "").replace("<br>", "\n")
    names = [
        name.strip("` ")
        for name in re.split(r"[\n,]", raw)
        if Path(name.strip("` ")).suffix.lower() in FONT_SUFFIXES
    ]
    folder = _cache_family_dir(root, family)
    if folder is not None and any(
        path.is_file() and path.suffix.lower() in FONT_SUFFIXES
        for path in folder.iterdir()
    ):
        return True
    if names:
        for name in names:
            if (Path(root) / name).is_file():
                return True
    return bool(bundled_files(root, family))


def audit(
    *,
    root: Optional[Union[str, Path]] = None,
    matrix_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Find every incoherence between cache, ``fetched.json`` and the matrix.

    Three conditions, each a legal risk: a font in cache with no matrix row; a
    matrix or fetched row whose file is not on disk; a licence that is unknown
    or still to be confirmed. Empty ``problems`` means coherent.
    """
    root_path = store_root(root)
    target = (
        Path(matrix_path)
        if matrix_path is not None
        else _matrix_for_root(root_path)
    )
    rows = matrix_font_rows(target) if target.is_file() else []
    fetched = fetched_rows(root_path)
    matrix_names = {norm(row.get("family")) for row in rows}

    missing_rows: List[str] = []
    for family in _cache_families(root_path):
        if norm(family) not in matrix_names:
            missing_rows.append(family)
    for row in fetched:
        family = str(row.get("family") or "")
        if family and norm(family) not in matrix_names and not any(
            norm(item) == norm(family) for item in missing_rows
        ):
            missing_rows.append(family)

    rows_without_file: List[str] = []
    for row in rows:
        family = str(row.get("family") or "")
        if family and not _row_has_file(root_path, family, str(row.get("file") or "")):
            rows_without_file.append(family)
    for row in fetched:
        family = str(row.get("family") or "")
        if not family or family in rows_without_file:
            continue
        paths = recorded_paths(root_path, family, row)
        if not any(path.is_file() for path in paths) and not bundled_files(root_path, family):
            rows_without_file.append(family)

    unknown: List[str] = []
    for row in rows:
        licence = str(row.get("licence") or "")
        if norm(licence) in _UNKNOWN_LICENCES:
            unknown.append(f"{row.get('family')}: {licence or 'assente'}")
    for row in fetched:
        licence = str(row.get("licence") or "")
        family = str(row.get("family") or "")
        if norm(licence) in _UNKNOWN_LICENCES and not any(
            item.startswith(f"{family}:") for item in unknown
        ):
            unknown.append(f"{family}: {licence or 'assente'}")

    problems = (
        [f"font in cache senza riga: {family}" for family in missing_rows]
        + [f"riga senza file: {family}" for family in rows_without_file]
        + [f"licenza non dichiarata: {item}" for item in unknown]
    )
    return {
        "ok": not problems,
        "exit_code": 0 if not problems else 2,
        "root": str(root_path),
        "matrix": str(target),
        "cache_families": len(_cache_families(root_path)),
        "matrix_rows": len(rows),
        "fetched_rows": len(fetched),
        "missing_rows": missing_rows,
        "rows_without_file": rows_without_file,
        "unknown_licence": unknown,
        "problems": problems,
    }
