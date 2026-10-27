"""vx.vault -- ownership records, delete guard, quarantine and space guard.

The engine must never delete something it did not create. Every work directory
that the toolkit writes into carries a `.vx-owned` manifest at its root: a JSON
document listing each artifact with its hash and size. Deletion is a two-step
operation -- `remove()` refuses anything that is not recorded, and what passes
is *moved* into `<root>/.quarantine/<YYYY-MM-DD>/...`, never unlinked. `purge()`
is the only function that deletes, and it only deletes inside the quarantine
tree, after an explicit `dry_run=False` and an age threshold.

Ownership rule, in one sentence: a path is owned only when it resolves strictly
inside the manifest root, the manifest itself sits at that root, and the
resolved relative path is present in its `entries` map -- nothing else is
touchable.

stdlib only. No network. Every entry point that takes a root and a path goes
through `resolve_inside()` before touching the filesystem.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from .contract import VxError

SCHEMA = "vx.owned/1.0"
MANIFEST_NAME = ".vx-owned"
QUARANTINE_DIR = ".quarantine"
JOURNAL_NAME = "journal.jsonl"

# A junction loop on Windows would otherwise spin forever inside realpath.
MAX_RESOLVE_STEPS = 64
_CHUNK = 1024 * 1024


# ---------------------------------------------------------------------------
# path safety
# ---------------------------------------------------------------------------
def _is_link_like(path: Union[str, Path]) -> bool:
    """True for symlinks and (on 3.12+) junctions; never raises."""
    p = Path(path)
    try:
        if p.is_symlink():
            return True
    except OSError:
        return False
    is_junction = getattr(p, "is_junction", None)
    if is_junction is not None:
        try:
            return bool(is_junction())
        except OSError:
            return False
    if os.name == "nt":
        # Junctions are reparse points: only readlink succeeds on them.
        try:
            os.readlink(str(p))
        except (OSError, ValueError):
            return False
        return True
    return False


def _canonical(path: Union[str, Path]) -> Path:
    """Resolve links component-wise with a hard step cap (junction loops)."""
    current = Path(os.path.abspath(str(path)))
    seen: set[str] = set()
    for _ in range(MAX_RESOLVE_STEPS):
        key = os.path.normcase(str(current))
        if key in seen:
            raise VxError(f"path resolution loop detected: {path}")
        seen.add(key)
        try:
            nxt = Path(os.path.realpath(str(current)))
        except (OSError, ValueError) as exc:
            raise VxError(f"cannot resolve path {path!r}: {exc}") from exc
        nxt = Path(os.path.abspath(str(nxt)))
        if os.path.normcase(str(nxt)) == key:
            return nxt
        current = nxt
    raise VxError(
        f"path resolution exceeded {MAX_RESOLVE_STEPS} steps (link loop?): {path}"
    )


def resolve_inside(root: Union[str, Path], path: Union[str, Path]) -> Path:
    """Return the canonical path, or raise VxError if it leaves root.

    Rejects `..` components, absolute paths outside the root, and symlinks or
    junctions whose target resolves outside the root. Link cycles are capped.
    """
    root_res = _canonical(root)
    raw = Path(path)
    if ".." in raw.parts:
        raise VxError(f"path traversal rejected: {path}")
    candidate = raw if raw.is_absolute() else root_res / raw
    resolved = _canonical(Path(os.path.abspath(str(candidate))))
    try:
        resolved.relative_to(root_res)
    except ValueError as exc:
        raise VxError(f"path escapes root {root_res}: {path}") from exc
    return resolved


# ---------------------------------------------------------------------------
# hashing / clock helpers
# ---------------------------------------------------------------------------
def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# manifest I/O
# ---------------------------------------------------------------------------
def _manifest_path(root_res: Path) -> Path:
    return root_res / MANIFEST_NAME


def _manifest_is_safe(root_res: Path) -> bool:
    """The manifest must be a regular file directly at root, not a link."""
    manifest = _manifest_path(root_res)
    try:
        return manifest.is_file() and not _is_link_like(manifest)
    except OSError:
        return False


def _read_data(root_res: Path, *, strict: bool) -> Optional[Dict[str, Any]]:
    manifest = _manifest_path(root_res)
    if not _manifest_is_safe(root_res):
        if strict:
            raise VxError(f"no usable ownership manifest at {manifest}")
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        if strict:
            raise VxError(f"corrupt ownership manifest {manifest}: {exc}") from exc
        return None
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        if strict:
            raise VxError(f"corrupt ownership manifest {manifest}: bad shape")
        return None
    stored_root = data.get("root")
    if not isinstance(stored_root, str):
        if strict:
            raise VxError(f"corrupt ownership manifest {manifest}: missing root")
        return None
    if os.path.normcase(os.path.abspath(stored_root)) != os.path.normcase(
        str(root_res)
    ):
        if strict:
            raise VxError(
                f"manifest root mismatch: {stored_root!r} != {str(root_res)!r}"
            )
        return None
    data["entries"] = {
        key: value
        for key, value in data["entries"].items()
        if isinstance(key, str) and isinstance(value, dict)
    }
    return data


def _write_data(root_res: Path, data: Dict[str, Any]) -> None:
    manifest = _manifest_path(root_res)
    if manifest.exists() and _is_link_like(manifest):
        raise VxError(f"refusing to write through symlinked manifest: {manifest}")
    payload = {
        "schema": SCHEMA,
        "root": str(root_res),
        "created_at": data.get("created_at") or _now_iso(),
        "entries": data.get("entries", {}),
    }
    tmp = root_res / (MANIFEST_NAME + ".tmp")
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(str(tmp), str(manifest))
    except OSError as exc:
        raise VxError(f"cannot write ownership manifest {manifest}: {exc}") from exc
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _read_entries(
    root_res: Path, *, strict: bool
) -> Optional[Dict[str, Dict[str, Any]]]:
    data = _read_data(root_res, strict=strict)
    if data is None:
        return None
    return {str(k): dict(v) for k, v in data["entries"].items()}


def _require_root(root: Union[str, Path]) -> Path:
    root_res = resolve_inside(root, ".")
    if not root_res.exists() or not root_res.is_dir():
        raise VxError(f"not a work directory: {root_res}")
    return root_res


# ---------------------------------------------------------------------------
# 1. ownership manifest
# ---------------------------------------------------------------------------
def declare(root: Union[str, Path]) -> Path:
    """Create (or refresh) the ownership manifest at the root of a work dir."""
    root_res = _canonical(root)
    try:
        root_res.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise VxError(f"cannot create work directory {root_res}: {exc}") from exc
    root_res = _canonical(root_res)
    if _manifest_path(root_res).exists():
        data = _read_data(root_res, strict=True)
        if data is None:
            raise VxError(f"no usable ownership manifest at {root_res}")
    else:
        data = {"created_at": _now_iso(), "entries": {}}
    _write_data(root_res, data)
    return root_res


def register(
    root: Union[str, Path], path: Union[str, Path], *, kind: str = "artifact"
) -> Dict[str, Any]:
    """Record (or replace) one file in the manifest, hashing it now."""
    root_res = declare(root)
    target = resolve_inside(root_res, path)
    if target == _manifest_path(root_res) or target == root_res:
        raise VxError(f"refusing to register the manifest or the root itself: {path}")
    if not target.is_file():
        raise VxError(f"can only register an existing regular file: {target}")
    try:
        relative = target.relative_to(root_res).as_posix()
    except ValueError as exc:
        raise VxError(f"path escapes root {root_res}: {path}") from exc
    if relative.split("/")[0] == QUARANTINE_DIR:
        raise VxError("cannot register a quarantine entry as an artifact")
    stat = target.stat()
    previous = owned_entries(root_res).get(relative)
    record = {
        "sha256": _sha256_file(target),
        "size": stat.st_size,
        "created_at": (previous or {}).get("created_at") or _now_iso(),
        "kind": str(kind),
    }
    data = _read_data(root_res, strict=True)
    if data is None:
        raise VxError(f"no usable ownership manifest at {root_res}")
    data["entries"][relative] = record
    _write_data(root_res, data)
    return {"path": relative, **record}


def is_owned(root: Union[str, Path], path: Union[str, Path]) -> bool:
    """True iff the path is strictly inside root and recorded in the manifest."""
    try:
        root_res = _canonical(root)
        if not root_res.is_dir():
            return False
        target = resolve_inside(root_res, path)
        relative = target.relative_to(root_res).as_posix()
        if not relative or relative == ".":
            return False
        entries = _read_entries(root_res, strict=False)
    except VxError:
        return False
    if entries is None:
        return False
    return relative in entries


def owned_entries(root: Union[str, Path]) -> Dict[str, Dict[str, Any]]:
    """Copy of the manifest entries map (relative posix path -> record)."""
    root_res = _require_root(root)
    entries = _read_entries(root_res, strict=True)
    if entries is None:
        raise VxError(f"no usable ownership manifest at {root_res}")
    return entries


# ---------------------------------------------------------------------------
# 2. delete guard
# ---------------------------------------------------------------------------
def _iter_files(base: Path) -> List[Path]:
    """Files under `base`, links treated as leaves, never followed."""
    out: List[Path] = []
    stack: List[Path] = [base]
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        key = os.path.normcase(str(_canonical(current)))
        if key in seen:
            raise VxError(f"directory loop detected: {current}")
        seen.add(key)
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    child = Path(entry.path)
                    if _is_link_like(child) or entry.is_file(follow_symlinks=False):
                        out.append(child)
                    elif entry.is_dir(follow_symlinks=False):
                        stack.append(child)
        except OSError as exc:
            raise VxError(f"cannot list {current}: {exc}") from exc
    return out


def _resolve_quarantine_root(
    root_res: Path, quarantine_dir: Optional[Union[str, Path]]
) -> Path:
    if quarantine_dir is None:
        return root_res / QUARANTINE_DIR
    qroot = resolve_inside(root_res, quarantine_dir)
    if qroot == root_res:
        raise VxError("quarantine directory cannot be the work root itself")
    return qroot


def _free_name(destination: Path) -> Path:
    if not destination.exists():
        return destination
    stem, suffix = destination.stem, destination.suffix
    for index in range(1, 1000):
        candidate = destination.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise VxError(f"cannot find a free quarantine name for {destination}")


def _append_journal(qroot: Path, line: Dict[str, Any]) -> None:
    try:
        qroot.mkdir(parents=True, exist_ok=True)
        with (qroot / JOURNAL_NAME).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, sort_keys=True) + "\n")
    except OSError as exc:
        raise VxError(f"cannot record quarantine move in {qroot}: {exc}") from exc


def remove(
    root: Union[str, Path],
    path: Union[str, Path],
    *,
    quarantine_dir: Optional[Union[str, Path]] = None,
) -> Path:
    """Move an owned path into quarantine. Never unlinks. Returns the new path."""
    root_res = _require_root(root)
    raw = Path(path)
    if ".." in raw.parts:
        raise VxError(f"path traversal rejected: {path}")
    candidate = raw if raw.is_absolute() else root_res / raw
    target = resolve_inside(root_res, candidate)
    if target == root_res:
        raise VxError("refusing to quarantine the work root itself")
    if _is_link_like(candidate) or _is_link_like(target):
        raise VxError(f"refusing to quarantine a symlink or junction: {path}")
    if not target.exists():
        raise VxError(f"cannot quarantine a missing path: {target}")
    try:
        relative = target.relative_to(root_res).as_posix()
    except ValueError as exc:
        raise VxError(f"path escapes root {root_res}: {path}") from exc
    if relative.split("/")[0] == QUARANTINE_DIR:
        raise VxError("refusing to quarantine the quarantine directory")

    entries = _read_entries(root_res, strict=True)
    if entries is None:
        raise VxError(f"no usable ownership manifest at {root_res}")

    if target.is_dir():
        moved_files: List[str] = []
        for file_path in _iter_files(target):
            try:
                file_rel = file_path.relative_to(root_res).as_posix()
            except ValueError as exc:
                raise VxError(f"path escapes root {root_res}: {file_path}") from exc
            if file_rel not in entries:
                raise VxError(
                    f"directory contains an unowned file, refusing: {file_rel}"
                )
            moved_files.append(file_rel)
        if not moved_files:
            raise VxError(
                f"directory holds no owned files, refusing to quarantine: {relative}"
            )
    else:
        if relative not in entries:
            raise VxError(f"refusing to remove an unowned file: {relative}")
        moved_files = [relative]

    qroot = _resolve_quarantine_root(root_res, quarantine_dir)
    if qroot == target or qroot.is_relative_to(target):
        raise VxError("quarantine directory cannot live inside the removed path")
    if target.is_relative_to(qroot):
        raise VxError("cannot quarantine a path that contains the quarantine tree")
    destination = _free_name(qroot / _today() / relative)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(destination))
    except OSError as exc:
        raise VxError(f"cannot move {target} into quarantine: {exc}") from exc

    data = _read_data(root_res, strict=True)
    if data is None:
        raise VxError(f"no usable ownership manifest at {root_res}")
    for file_rel in moved_files:
        data["entries"].pop(file_rel, None)
    _write_data(root_res, data)
    _append_journal(
        qroot,
        {
            "moved_at": _now_iso(),
            "from": relative,
            "to": str(destination),
            "files": moved_files,
            "kind": "directory" if target.is_dir() else "file",
        },
    )
    return destination


# ---------------------------------------------------------------------------
# 3. quarantine purge
# ---------------------------------------------------------------------------
def _as_timestamp(now: Optional[Union[float, int, datetime]]) -> float:
    if now is None:
        return time.time()
    if isinstance(now, datetime):
        return now.timestamp()
    return float(now)


def purge(
    root: Union[str, Path],
    *,
    days: float = 7,
    now: Optional[Union[float, int, datetime]] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """List (dry_run) or delete quarantine files older than `days`.

    Only the quarantine tree is ever touched; the manifest and the work files
    are out of scope by construction. Default is dry_run=True on purpose.
    """
    if days < 0:
        raise VxError("days must be >= 0")
    root_res = _require_root(root)
    qroot = root_res / QUARANTINE_DIR
    cutoff = _as_timestamp(now) - float(days) * 86400.0
    result: Dict[str, Any] = {
        "dry_run": bool(dry_run),
        "days": days,
        "candidates": [],
        "deleted": [],
        "count": 0,
    }
    if not qroot.is_dir() or _is_link_like(qroot):
        return result
    qroot_res = resolve_inside(root_res, qroot)
    candidates: List[Tuple[Path, float]] = []
    for entry in sorted(qroot_res.iterdir()):
        if entry.name == JOURNAL_NAME:
            continue
        if not entry.is_dir() or _is_link_like(entry):
            continue
        for file_path in _iter_files(entry):
            if _is_link_like(file_path):
                continue
            try:
                mtime = file_path.stat().st_mtime
            except OSError as exc:
                raise VxError(
                    f"cannot stat quarantine entry {file_path}: {exc}"
                ) from exc
            age_days = max(0.0, (_as_timestamp(now) - mtime) / 86400.0)
            if mtime < cutoff:
                candidates.append((file_path, round(age_days, 3)))
    result["candidates"] = [
        {"path": str(path), "age_days": age} for path, age in candidates
    ]
    result["count"] = len(candidates)
    if dry_run:
        return result

    deleted: List[str] = []
    for file_path, _age in candidates:
        try:
            file_path.unlink()
            deleted.append(str(file_path))
        except OSError as exc:
            raise VxError(f"cannot purge quarantine entry {file_path}: {exc}") from exc
    for entry in sorted(qroot_res.iterdir()):
        if entry.name == JOURNAL_NAME or not entry.is_dir() or _is_link_like(entry):
            continue
        tree = [entry] + [p for p in entry.rglob("*") if p.is_dir()]
        for directory in sorted(tree, key=lambda p: len(p.parts), reverse=True):
            try:
                os.rmdir(str(directory))
            except OSError:
                pass
    result["deleted"] = deleted
    return result


# ---------------------------------------------------------------------------
# 4. space guard
# ---------------------------------------------------------------------------
def ensure_space(
    path: Union[str, Path],
    *,
    need_bytes: int,
    headroom_ratio: float = 1.5,
) -> Dict[str, Any]:
    """Raise before writing when free space < need * headroom_ratio."""
    if need_bytes < 0:
        raise VxError("need_bytes must be >= 0")
    if headroom_ratio < 0:
        raise VxError("headroom_ratio must be >= 0")
    probe = Path(os.path.abspath(str(path)))
    while not probe.exists():
        if probe.parent == probe:
            break
        probe = probe.parent
    try:
        usage = shutil.disk_usage(str(probe))
    except OSError as exc:
        raise VxError(f"cannot read disk usage for {probe}: {exc}") from exc
    required = int(math.ceil(float(need_bytes) * float(headroom_ratio)))
    if usage.free < required:
        error = VxError(
            "insufficient free space: "
            f"free={usage.free} need={need_bytes} required={required}"
        )
        error.payload = {
            "free_bytes": int(usage.free),
            "need_bytes": int(need_bytes),
            "required_bytes": required,
        }
        raise error
    return {
        "free_bytes": int(usage.free),
        "need_bytes": int(need_bytes),
        "required_bytes": required,
        "ok": True,
    }


# ---------------------------------------------------------------------------
# 5. verification
# ---------------------------------------------------------------------------
def _iter_disk_files(root_res: Path) -> Iterable[Path]:
    """Every file under root except the manifest and the quarantine tree.

    Directories that are links are not descended into, so a junction loop can
    never make the walk hang; the link itself is skipped as a file.
    """
    for dirpath, dirnames, filenames in os.walk(
        root_res, topdown=True, followlinks=False
    ):
        current = Path(dirpath)
        kept: List[str] = []
        for name in dirnames:
            child = current / name
            if current == root_res and name == QUARANTINE_DIR:
                continue
            if _is_link_like(child):
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            if current == root_res and name == MANIFEST_NAME:
                continue
            yield current / name


def _entry_key(root_res: Path, file_path: Path) -> str:
    try:
        return _canonical(file_path).relative_to(root_res).as_posix()
    except (VxError, ValueError):
        return file_path.relative_to(root_res).as_posix()


def verify(root: Union[str, Path], *, repair_manifest: bool = False) -> Dict[str, Any]:
    """Re-hash every owned entry; report missing/changed/unowned disk files."""
    root_res = _require_root(root)
    data = _read_data(root_res, strict=True)
    if data is None:
        raise VxError(f"no usable ownership manifest at {root_res}")
    entries: Dict[str, Dict[str, Any]] = {
        str(k): dict(v) for k, v in data["entries"].items()
    }
    missing: List[str] = []
    changed: List[str] = []

    for relative, record in sorted(entries.items()):
        try:
            file_path = resolve_inside(root_res, relative)
        except VxError:
            missing.append(relative)
            continue
        if not file_path.is_file():
            missing.append(relative)
            continue
        try:
            actual_hash = _sha256_file(file_path)
            actual_size = file_path.stat().st_size
        except OSError:
            changed.append(relative)
            continue
        expected_hash = record.get("sha256")
        expected_size = record.get("size")
        if actual_hash != expected_hash or actual_size != expected_size:
            changed.append(relative)

    unowned: List[str] = []
    for file_path in _iter_disk_files(root_res):
        try:
            relative = file_path.relative_to(root_res).as_posix()
        except ValueError:
            continue
        known = {relative, _entry_key(root_res, file_path)}
        if not known.intersection(entries):
            unowned.append(relative)

    ok = not missing and not changed
    if repair_manifest and (missing or changed):
        for relative in changed:
            record = entries.get(relative)
            if record is None:
                continue
            try:
                file_path = resolve_inside(root_res, relative)
                stat = file_path.stat()
                new_hash = _sha256_file(file_path)
            except (VxError, OSError):
                continue
            record["sha256"] = new_hash
            record["size"] = stat.st_size
        for relative in missing:
            entries.pop(relative, None)
        data["entries"] = entries
        _write_data(root_res, data)
        ok = True

    return {
        "checked": len(entries),
        "ok": ok,
        "missing": missing,
        "changed": changed,
        "manifest_entries": len(entries),
        "unowned_on_disk": sorted(unowned),
    }


def verify_original(path: Union[str, Path], expected_sha256: str) -> bool:
    """True when the file still hashes to the expected digest, else VxError."""
    target = Path(path)
    if not target.is_file():
        raise VxError(f"cannot verify a missing file: {target}")
    try:
        actual = _sha256_file(target)
    except OSError as exc:
        raise VxError(f"cannot hash {target}: {exc}") from exc
    expected = str(expected_sha256).strip().lower()
    if actual != expected:
        error = VxError(
            f"hash mismatch for {target}: expected {expected}, got {actual}"
        )
        error.expected_sha256 = expected
        error.actual_sha256 = actual
        error.payload = {"expected": expected, "actual": actual}
        raise error
    return True


__all__ = [
    "MANIFEST_NAME",
    "QUARANTINE_DIR",
    "SCHEMA",
    "declare",
    "ensure_space",
    "is_owned",
    "owned_entries",
    "purge",
    "register",
    "remove",
    "resolve_inside",
    "verify",
    "verify_original",
]
