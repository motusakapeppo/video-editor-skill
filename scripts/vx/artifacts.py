"""vx.artifacts — a write-ahead journal for files a stage is about to write.

A stage artifact is written by an external tool (ffmpeg) straight onto its
final path.  If the process dies mid-write the cache can only observe the
damage afterwards: a short file, a bad hash, a miss with no story about *why*
the file is short.  This module keeps that story.

The contract is one append-only JSONL file, ``<root>/.vx-writes.jsonl``.  Every
line is a JSON object sharing the schema tag ``vx.artifacts/1``:

* ``{"schema": "vx.artifacts/1", "event": "plan", "id": "<ns>", "path": ...,
  "path_key": ..., "kind": "artifact", "expected_sha256": ... | null,
  "ts": ...}`` — written *before* the bytes are placed at ``path``.
* ``{"schema": "vx.artifacts/1", "event": "complete", "id": "<ns>", "path":
  ..., "path_key": ..., "sha256": ..., "ts": ...}`` — written *after* the
  bytes are on disk.

``id`` is a zero-padded nanosecond stamp, strictly increasing within a process,
so journal order and id order agree without a counter scan.  ``path_key`` is the
case-normalised absolute path used for matching a disk file to an intent, so a
journal stays comparable across the relative and absolute spellings callers use.

An intent without a completion is a write that may not have finished.  That is
the only thing :func:`classify` needs in order to point at a file that is
"possibly half-written but the cache entry still looks plausible".

Reads tolerate damage: a truncated or non-JSON line, a line that is not an
object, or a line from another schema is skipped and the rest of the journal
still reads.  Writes never raise for I/O: a journal that cannot be appended is
a lost record, not a failed render.  stdlib only.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

JOURNAL_SCHEMA = "vx.artifacts/1"
JOURNAL_NAME = ".vx-writes.jsonl"
EVENT_PLAN = "plan"
EVENT_COMPLETE = "complete"

_LOCK = threading.Lock()
_LAST_ID = 0


def journal_path(root: str | Path) -> Path:
    """Where a root's journal lives: ``<root>/.vx-writes.jsonl``."""
    return Path(root) / JOURNAL_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_id() -> str:
    """A monotonic intent id: zero-padded nanoseconds, unique per process."""
    global _LAST_ID
    with _LOCK:
        moment = time.time_ns()
        if moment <= _LAST_ID:
            moment = _LAST_ID + 1
        _LAST_ID = moment
    return f"{moment:020d}"


def _normalise(path: str | Path | None) -> str:
    """The comparison key of a path: absolute, symlink-free, case-folded."""
    if path is None:
        return ""
    try:
        absolute = os.path.realpath(os.path.abspath(str(path)))
    except (OSError, ValueError):
        return ""
    return os.path.normcase(absolute)


def _fsync(fd: int) -> None:
    try:
        os.fsync(fd)
    except (OSError, ValueError, AttributeError):
        pass


def _fsync_dir(path: Path) -> None:
    """Best-effort fsync of a directory; Windows cannot open one."""
    if os.name == "nt":
        return
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        _fsync(fd)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _records(root: str | Path) -> Iterator[Dict[str, Any]]:
    """Yield the well-formed records of the journal, skipping damaged lines."""
    try:
        handle = journal_path(root).open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except (ValueError, TypeError):
                continue
            if not isinstance(record, dict):
                continue
            if record.get("schema") != JOURNAL_SCHEMA:
                continue
            yield record


def _append(root: str | Path, record: Dict[str, Any]) -> None:
    """Append one record and fsync it.  Never fatal for the caller."""
    target = journal_path(root)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            _fsync(handle.fileno())
    except OSError:
        pass


def _tally(
    root: str | Path,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split the journal into (plans, open intents, completions)."""
    planned: List[Dict[str, Any]] = []
    completed: List[Dict[str, Any]] = []
    done: set[str] = set()
    for record in _records(root):
        ident = record.get("id")
        event = record.get("event")
        if ident is None:
            continue
        key = str(ident)
        if event == EVENT_PLAN:
            planned.append(record)
        elif event == EVENT_COMPLETE:
            done.add(key)
            completed.append(record)
    open_intents = [record for record in planned if str(record.get("id")) not in done]
    return planned, open_intents, completed


def plan(
    root: str | Path,
    path: str | Path,
    *,
    expected_sha256: Optional[str] = None,
    kind: str = "artifact",
) -> str:
    """Record the intent to write ``path``; return its monotonic id.

    Called *before* the bytes reach ``path``.  ``expected_sha256`` is the hash
    the writer promises, when it knows it; ``kind`` lets one journal carry more
    than stage artifacts.  The append is best-effort: a read-only journal loses
    the record rather than failing the render.
    """
    intent_id = _next_id()
    record: Dict[str, Any] = {
        "schema": JOURNAL_SCHEMA,
        "event": EVENT_PLAN,
        "id": intent_id,
        "path": str(path),
        "path_key": _normalise(path),
        "kind": str(kind),
        "expected_sha256": None if expected_sha256 is None else str(expected_sha256),
        "ts": _now(),
    }
    _append(root, record)
    return intent_id


def complete(
    root: str | Path,
    intent_id: str,
    path: str | Path,
    *,
    sha256: str,
) -> Dict[str, Any]:
    """Record that the write announced by ``intent_id`` landed; return it.

    The returned dict is the record as journaled, so a caller can log the same
    line the next run will read.
    """
    record: Dict[str, Any] = {
        "schema": JOURNAL_SCHEMA,
        "event": EVENT_COMPLETE,
        "id": str(intent_id),
        "path": str(path),
        "path_key": _normalise(path),
        "sha256": str(sha256),
        "ts": _now(),
    }
    _append(root, record)
    return dict(record)


def pending(root: str | Path) -> List[Dict[str, Any]]:
    """The intents with no completion, in the order they were planned."""
    return _tally(root)[1]


def pending_for(root: str | Path, path: str | Path) -> Optional[Dict[str, Any]]:
    """The open intent that targets ``path``, or ``None``.

    Cheap to call per lookup when no journal exists: the file test short
    circuits, which is what keeps a journal-free cache exactly as it was.
    """
    if not journal_path(root).is_file():
        return None
    wanted = _normalise(path)
    if not wanted:
        return None
    for record in _tally(root)[1]:
        known = record.get("path_key")
        if not isinstance(known, str):
            known = _normalise(record.get("path"))
        if known == wanted:
            return record
    return None


def is_pending(root: str | Path, path: str | Path) -> bool:
    """Whether ``path`` has a planned write that was never completed."""
    return pending_for(root, path) is not None


def classify(root: str | Path) -> Dict[str, Any]:
    """The journal's story: completed writes, open intents, possible debris.

    ``completed`` holds the completion records, ``pending`` the plans without
    one, and ``orphan_files`` the subset of pending paths that exist on disk —
    files a crash may have left half-written.  A missing journal classifies as
    ``{"completed": [], "pending": [], "orphan_files": []}``: no story to tell.
    """
    _planned, open_intents, completed = _tally(root)
    orphans: List[str] = []
    seen: set[str] = set()
    for record in open_intents:
        raw = record.get("path")
        if not isinstance(raw, str) or not raw:
            continue
        known = record.get("path_key")
        if not isinstance(known, str):
            known = _normalise(raw)
        if known in seen:
            continue
        seen.add(known)
        try:
            if Path(raw).is_file():
                orphans.append(raw)
        except OSError:
            continue
    return {"completed": completed, "pending": open_intents, "orphan_files": orphans}


def inside(root: str | Path, path: str | Path) -> bool:
    """Whether ``path`` really lives below ``root`` (never ``root`` itself)."""
    root_abs = _normalise(root)
    target_abs = _normalise(path)
    if not root_abs or not target_abs:
        return False
    if target_abs == root_abs:
        return False
    try:
        common = os.path.commonpath([root_abs, target_abs])
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(root_abs)


def _require_inside(root: str | Path, target: Path) -> None:
    if not inside(root, target):
        raise ValueError(f"refusing to write outside root {str(root)!r}: {target}")


def write_bytes_atomic(
    path: str | Path,
    data: bytes,
    *,
    root: Optional[str | Path] = None,
) -> Path:
    """Write ``data`` to ``path`` through a same-directory temp file.

    The bytes are flushed and fsynced to ``<path>.<random>.tmp``, then moved
    onto ``path`` with :func:`os.replace`, so a reader sees either the previous
    file or the whole new one, never a prefix.  When ``root`` is given the
    destination must live strictly below it; anything else raises
    :class:`ValueError` before a byte is written.  On any failure the temp file
    is removed and the destination is left untouched.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes")
    target = Path(path)
    if root is not None:
        _require_inside(root, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(bytes(data))
            handle.flush()
            _fsync(handle.fileno())
        os.replace(raw, target)
    except BaseException:
        try:
            os.unlink(raw)
        except OSError:
            pass
        raise
    _fsync_dir(target.parent)
    return target


def enable(root: str | Path) -> Path:
    """Create an empty journal and return its path.

    Presence is the switch: a stage cache records intents only when a journal
    already exists, so a workdir that never asked for one behaves exactly as it
    did before this module.  Callers that want the safety net call this once at
    the start of a run, or write the same file themselves.
    """
    target = journal_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8"):
        pass
    return target
