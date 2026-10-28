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
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

from . import fonts

__all__ = [
    "CACHE_DIR_NAME", "CATALOGUE_NAME", "ENV_CATALOGUE", "FETCHED_NAME",
    "FONT_SUFFIXES", "MAGIC", "SCHEMA", "bundled_files",
    "cache_dir", "cached_row", "catalogue", "catalogue_file",
    "family_of_stem", "fetched_path", "fetched_rows", "files_metadata",
    "find", "magic_ok", "norm", "record",
    "recorded_paths", "refuse", "sha256", "store_root",
    "verify", "write_bytes", "write_rows",
]

SCHEMA = "vx.fontfetch/1"
CATALOGUE_NAME = "catalogue.json"
ENV_CATALOGUE = "VX_FONT_CATALOGUE"
CACHE_DIR_NAME = "cache"
FETCHED_NAME = "fetched.json"
FONT_SUFFIXES = (".ttf", ".otf")

#: The four prefixes a font file may start with. Anything else is not a font.
MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")

_DOCUMENT: Dict[str, Dict[str, Any]] = {}


def refuse(code: str, detail: str, where: str = "") -> Exception:
    """The project's refusal, built with ``edl3`` imported on demand."""
    from .edl3 import EDLError  # noqa: PLC0415 -- keeps the font modules import-cheap

    return EDLError(code, detail, where)


def norm(text: Any) -> str:
    """The comparison key for a family name: case, spaces, underscores ignored."""
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


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
