"""vx.history — append-only journal of the EDL, with checkpoints and undo.

The EDL is the document every stage of the engine agrees on. Nothing used to
record how it changed: when a montage came out wrong there was no way to ask
"what did it look like three decisions ago". This module keeps that answer.

One JSONL line per change, written and flushed the moment the change happens.
The file is only ever appended to: no line is rewritten, and the sequence
continues from the last valid line already on disk, so re-opening a journal
never restarts numbering. The journal is a private document format; callers
hand it a mapping (for the engine, ``EDLPlan.to_dict()``) and get back the
record that was written.

Record schema, in this order::

    {"seq": int, "ts": "…Z", "kind": str, "note": str,
     "sha256": str, "document": {...}}

``sha256`` covers the canonical JSON of ``document`` — sorted keys, no
separators padding, no ASCII escaping, UTF-8 bytes — so any reader can
recompute it and notice an edited record.

Durability rules, borrowed from ``vx.events``:

* a crash can cut the final line in half; that fragment is not evidence of
  anything and is dropped by ``read`` (the next ``append`` closes it off so a
  new record never glues itself to the fragment);
* a record that parses but whose ``sha256`` no longer matches its document was
  tampered with; ``read_checked`` reports it apart and ``current`` skips it;
* nothing is buffered, so every record already written survives a kill.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# The contract: every line carries exactly these keys, in this order.
FIELDS = ("seq", "ts", "kind", "note", "sha256", "document")

# Kinds written by this module. The writer does not enforce the list, so a
# caller can record a new kind without editing this module; ``undo`` only
# counts ``plan`` records as steps.
KINDS = ("plan", "restore", "checkpoint")

_Path = Union[str, Path]


def _timestamp() -> str:
    """ISO8601 in UTC with millisecond precision and a trailing ``Z``."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _canonical(document: Mapping[str, Any]) -> str:
    """The exact bytes-able form the digest covers: sorted, compact, UTF-8."""
    return json.dumps(
        dict(document),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha256(document: Mapping[str, Any]) -> str:
    """Digest of the canonical JSON, recomputable by anyone reading the file."""
    return hashlib.sha256(_canonical(document).encode("utf-8")).hexdigest()


def read_checked(path: _Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """``(valid records, damaged records)`` in file order.

    A fragment that does not parse is dropped: it is a write interrupted
    mid-line, not a record. A record that parses but fails verification is
    kept aside as damage, with the 1-based line number and the reason
    (``not_a_record``, ``malformed_record`` or ``sha256_mismatch``).
    """
    target = Path(path)
    if not target.is_file():
        return [], []
    records: List[Dict[str, Any]] = []
    damaged: List[Dict[str, Any]] = []
    for index, line in enumerate(target.read_text(encoding="utf-8").splitlines()):
        text = line.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            # Torn write: the line never became a record, so it proves nothing.
            continue
        if not isinstance(record, dict):
            damaged.append(
                {"line": index + 1, "reason": "not_a_record", "record": record}
            )
            continue
        missing = [key for key in FIELDS if key not in record]
        if missing or not isinstance(record.get("document"), dict):
            damaged.append(
                {"line": index + 1, "reason": "malformed_record", "record": record}
            )
            continue
        if _sha256(record["document"]) != record["sha256"]:
            damaged.append(
                {"line": index + 1, "reason": "sha256_mismatch", "record": record}
            )
            continue
        records.append(record)
    return records, damaged


def read(path: _Path) -> List[Dict[str, Any]]:
    """Every verified record on disk, in write order."""
    return read_checked(path)[0]


def _last_seq(path: Path) -> int:
    highest = 0
    for record in read(path):
        try:
            value = int(record.get("seq") or 0)
        except (TypeError, ValueError):
            continue
        highest = max(highest, value)
    return highest


def _write(
    path: _Path,
    document: Mapping[str, Any],
    *,
    note: str,
    kind: str,
) -> Dict[str, Any]:
    if not isinstance(document, Mapping):
        raise TypeError("history: document must be a mapping")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record: Dict[str, Any] = {
        "seq": _last_seq(target) + 1,
        "ts": _timestamp(),
        "kind": str(kind),
        "note": str(note),
        "sha256": _sha256(document),
        "document": dict(document),
    }
    torn = False
    if target.is_file() and target.stat().st_size:
        with target.open("rb") as raw:
            raw.seek(-1, 2)
            torn = raw.read(1) != b"\n"
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        if torn:
            # Close the half-written line so this record starts on its own.
            handle.write("\n")
        handle.write(line + "\n")
        handle.flush()
    return record


def append(
    path: _Path,
    document: Mapping[str, Any],
    *,
    note: str = "",
    kind: str = "plan",
) -> Dict[str, Any]:
    """Append one verified record and flush it; returns the record written."""
    return _write(path, document, note=note, kind=kind)


def current(path: _Path) -> Optional[Dict[str, Any]]:
    """The last document that still verifies, or ``None`` on an empty journal."""
    records = read(path)
    if not records:
        return None
    return records[-1]["document"]


def _plan_cursor(
    records: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    """Plan records in order, plus where the journal currently stands in them.

    A ``restore`` record moves the cursor back to the plan record it replayed,
    matched by digest walking backwards from where it happened; that is what
    makes a second undo count from the restored point instead of from the
    newest plan.
    """
    plans: List[Dict[str, Any]] = []
    cursor = -1
    for record in records:
        kind = record.get("kind")
        if kind == "plan":
            plans.append(record)
            cursor = len(plans) - 1
        elif kind == "restore":
            digest = record.get("sha256")
            for index in range(cursor, -1, -1):
                if plans[index].get("sha256") == digest:
                    cursor = index
                    break
    return plans, cursor


def undo(path: _Path, *, steps: int = 1) -> Dict[str, Any]:
    """Replay a document from ``steps`` plan records back, as a new record.

    ``steps`` counts ``plan`` records only, so a restore never consumes a
    step. Asking for more steps than the journal holds clamps to the oldest
    plan record and says so in the note: it never raises on that account and
    never pretends to have gone further.
    """
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
        raise ValueError("history: steps must be an integer >= 1")
    plans, cursor = _plan_cursor(read(path))
    if not plans:
        raise ValueError("history: no plan record to restore")
    target_index = cursor - steps
    note = f"undo {steps} step(s)"
    if target_index < 0:
        target_index = 0
        note = f"clamped_to_oldest: asked {steps} step(s), {cursor} available"
    chosen = plans[target_index]["document"]
    return _write(path, chosen, note=note, kind="restore")


def checkpoint(path: _Path, label: str) -> Dict[str, Any]:
    """Record the current document under a label (carried in ``note``)."""
    if not isinstance(label, str) or not label:
        raise ValueError("history: checkpoint label must be a non-empty string")
    document = current(path)
    if document is None:
        raise ValueError("history: nothing to checkpoint")
    return _write(path, document, note=label, kind="checkpoint")


def at(path: _Path, label: str) -> Optional[Dict[str, Any]]:
    """The document of the last checkpoint with this label, or ``None``."""
    if not isinstance(label, str) or not label:
        return None
    for record in reversed(read(path)):
        if record.get("kind") == "checkpoint" and record.get("note") == label:
            return record["document"]
    return None


def _key_path(prefix: str, key: Any) -> str:
    return f"{prefix}.{key}" if prefix else str(key)


def _index_path(prefix: str, index: int) -> str:
    return f"{prefix}[{index}]"


def _walk(
    left: Any,
    right: Any,
    path: str,
    changed: List[str],
    added: List[str],
    removed: List[str],
) -> None:
    if isinstance(left, dict) and isinstance(right, dict):
        for key in left:
            if key not in right:
                removed.append(_key_path(path, key))
        for key in right:
            if key not in left:
                added.append(_key_path(path, key))
            else:
                _walk(
                    left[key],
                    right[key],
                    _key_path(path, key),
                    changed,
                    added,
                    removed,
                )
        return
    if isinstance(left, list) and isinstance(right, list):
        shared = min(len(left), len(right))
        for index in range(shared):
            _walk(
                left[index],
                right[index],
                _index_path(path, index),
                changed,
                added,
                removed,
            )
        for index in range(shared, len(left)):
            removed.append(_index_path(path, index))
        for index in range(shared, len(right)):
            added.append(_index_path(path, index))
        return
    if left != right:
        changed.append(path or "$")


def diff(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict:
    """Structural difference between two documents, as JSON paths.

    ``changed`` lists the leaves whose value differs (``tracks.video[0]
    .duration_sec``), ``added`` the paths present only in ``b`` and ``removed``
    the paths present only in ``a``. Enough to read a change out loud.
    """
    changed: List[str] = []
    added: List[str] = []
    removed: List[str] = []
    _walk(dict(a), dict(b), "", changed, added, removed)
    return {"changed": changed, "added": added, "removed": removed}
