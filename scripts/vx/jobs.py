"""vx.jobs — file-based job store for the async engine.

One job is one directory: ``<root>/<job_id>/job.json``. The record is the whole
state machine (``queued -> running -> completed|failed``), and every write goes
through a same-directory temp file, ``flush`` + ``os.fsync``, then
``os.replace``. That gives the store its two invariants:

* **single writer per job** — only the worker that owns the job mutates it, so
  there is no read-modify-write race between writers;
* **lock-free readers** — a reader either sees the previous complete file or the
  next complete file, never a half-written one, because ``os.replace`` is atomic
  on both POSIX and Windows.

Job ids are ``uuid4().hex`` and are validated against :data:`JOB_ID_RE` *before*
any path join, so a crafted id can never escape the jobs root. Liveness is asked
of the operating system, never of the clock: on Windows ``os.kill(pid, 0)`` would
terminate the process it was asked about, so the Windows branch uses
``OpenProcess``/``GetExitCodeProcess`` instead.

This module is stdlib only: no ffmpeg, no process spawning.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Mapping as _MappingABC
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from . import config, contract

STATUSES = ("queued", "running", "completed", "failed")
TRANSITIONS = {
    "queued": {"running", "failed"},
    "running": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
}
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")

JOB_FILE = "job.json"
CLAIM_SUFFIX = ".claim"

_REQUIRED_PARAMS = ("prompt", "assets", "out")
_OPTIONAL_PARAMS = ("workdir", "fps", "manifest", "autonomous", "crf", "preset")
_ALLOWED_PARAMS = frozenset(_REQUIRED_PARAMS) | frozenset(_OPTIONAL_PARAMS)

_WINDOWS = os.name == "nt"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259

# A Windows reader holds job.json open without delete sharing for a fraction of
# a millisecond; os.replace then raises PermissionError. Retrying for ~2 s turns
# a lost terminal write into a bounded wait.
_REPLACE_ATTEMPTS = 40
_REPLACE_DELAY_SEC = 0.05
QUEUED_STALE_SEC = 900.0


# --------------------------------------------------------------------------- #
# paths and ids
# --------------------------------------------------------------------------- #


def jobs_root(root: Optional[Union[str, Path]] = None) -> Path:
    """The jobs directory, created if missing.

    ``None`` means ``config.work_root() / "jobs"``. An explicit ``root`` is used
    as given, which is what tests rely on to stay away from the real work root.
    """
    base = Path(root) if root is not None else config.work_root() / "jobs"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _validate_id(job_id: str) -> str:
    """Reject anything that is not a 32-char lowercase hex id, before any join."""
    if not isinstance(job_id, str) or JOB_ID_RE.fullmatch(job_id) is None:
        raise contract.VxError(f"invalid job id: {job_id!r}")
    return job_id


def _job_dir(base: Path, job_id: str) -> Path:
    return base / job_id


def _job_path(base: Path, job_id: str) -> Path:
    return _job_dir(base, job_id) / JOB_FILE


# --------------------------------------------------------------------------- #
# atomic record I/O
# --------------------------------------------------------------------------- #


def _write_job(base: Path, job_id: str, record: Dict[str, Any]) -> None:
    """Write ``job.json`` through ``<job_id>.json.tmp`` in the same directory."""
    target = _job_path(base, job_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f"{job_id}.json.tmp"
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp, target)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _replace_with_retry(tmp: Path, target: Path) -> None:
    """Replace ``target`` with ``tmp``, waiting out a Windows reader.

    ``os.replace`` is atomic, but on Windows it fails with ``PermissionError``
    while another process has the target open without delete sharing. A status
    poll does exactly that for a fraction of a millisecond, so a terminal write
    would otherwise be lost and a finished job reaped as ``failed``.
    """
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_DELAY_SEC)


def _read_record(base: Path, job_id: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(_job_path(base, job_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------- #
# liveness
# --------------------------------------------------------------------------- #


def is_alive(pid: Optional[int]) -> bool:
    """True only when ``pid`` names a process that is still running.

    ``None`` (and any non-positive or non-numeric value) is dead. On Windows the
    query goes through ``OpenProcess`` + ``GetExitCodeProcess``: ``os.kill(pid,
    0)`` is never used there because it maps to ``TerminateProcess``.
    """
    if pid is None:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False

    if _WINDOWS:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        fields = proc_stat.read_text(encoding="utf-8", errors="replace").rsplit(")", 1)
    except OSError:
        fields = []
    if len(fields) == 2:
        state = fields[1].strip().split(" ", 1)[0]
        if state in ("Z", "X", "x"):
            # A zombie still answers os.kill(pid, 0) but cannot make progress.
            return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --------------------------------------------------------------------------- #
# state machine
# --------------------------------------------------------------------------- #


def _apply_update(
    base: Path,
    job_id: str,
    record: Dict[str, Any],
    updates: Mapping[str, Any],
) -> Dict[str, Any]:
    now = time.time()
    new = dict(record)

    if "status" in updates:
        status = updates["status"]
        if status not in STATUSES:
            raise contract.VxError(f"unknown job status: {status!r}")
        current = new.get("status")
        if status != current:
            allowed = TRANSITIONS.get(current, set())
            if status not in allowed:
                raise contract.VxError(f"illegal job transition: {current} -> {status}")
            new["status"] = status
    if new.get("status") == "running" and new.get("started_at") is None:
        new["started_at"] = now

    if "progress_percent" in updates:
        value = updates["progress_percent"]
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise contract.VxError(
                f"progress_percent must be a number: {value!r}"
            ) from exc
        value = contract.clamp(value, 0.0, 100.0)
        try:
            previous = float(new.get("progress_percent", 0.0))
        except (TypeError, ValueError):
            previous = 0.0
        new["progress_percent"] = max(previous, value)

    for key, value in updates.items():
        if key in ("status", "progress_percent", "job_id"):
            continue
        new[key] = value

    new["job_id"] = job_id
    new["updated_at"] = now
    _write_job(base, job_id, new)
    return new


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def create_job(
    params: Mapping[str, Any], *, root: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """Validate ``params`` and persist a fresh ``queued`` job."""
    if not isinstance(params, _MappingABC):
        raise contract.VxError("job params must be a mapping")

    unknown = sorted(set(params) - _ALLOWED_PARAMS)
    if unknown:
        raise contract.VxError(f"unknown job param(s): {', '.join(unknown)}")

    validated: Dict[str, Any] = {}
    for key in _REQUIRED_PARAMS:
        value = params.get(key)
        if not isinstance(value, str) or not value.strip():
            raise contract.VxError(f"job param {key!r} must be a non-empty string")
        validated[key] = value

    if not Path(validated["assets"]).is_dir():
        raise contract.VxError(f"assets directory not found: {validated['assets']}")

    for key in _OPTIONAL_PARAMS:
        if key not in params:
            continue
        value = params[key]
        if key in ("workdir", "manifest", "preset"):
            if not isinstance(value, str) or not value.strip():
                raise contract.VxError(f"job param {key!r} must be a non-empty string")
        elif key == "fps":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise contract.VxError("job param 'fps' must be a number")
            if value <= 0:
                raise contract.VxError("job param 'fps' must be positive")
        elif key == "crf":
            if isinstance(value, bool) or not isinstance(value, int):
                raise contract.VxError("job param 'crf' must be an integer")
        elif key == "autonomous":
            if not isinstance(value, bool):
                raise contract.VxError("job param 'autonomous' must be a boolean")
        validated[key] = value

    base = jobs_root(root)
    job_id = uuid.uuid4().hex
    now = time.time()
    record: Dict[str, Any] = {
        "job_id": job_id,
        "status": "queued",
        "stage": "queued",
        "progress_percent": 0.0,
        "params": validated,
        "pid": None,
        "created_at": now,
        "started_at": None,
        "updated_at": now,
        "result": None,
        "error": None,
    }
    _job_dir(base, job_id).mkdir(parents=True, exist_ok=True)
    _write_job(base, job_id, record)
    return record


def read_job(
    job_id: str,
    *,
    root: Optional[Union[str, Path]] = None,
    reap: bool = True,
) -> Optional[Dict[str, Any]]:
    """Return the record, or ``None`` when the id is unknown.

    With ``reap`` a ``running`` job whose worker pid is gone is atomically
    marked ``failed`` with ``error="worker died"`` before being returned.
    """
    _validate_id(job_id)
    base = jobs_root(root)
    record = _read_record(base, job_id)
    if record is None:
        return None
    if reap and record.get("status") == "running":
        pid = record.get("pid")
        if pid is not None and not is_alive(pid):
            record = _apply_update(
                base, job_id, record, {"status": "failed", "error": "worker died"}
            )
    if reap and record.get("status") == "queued":
        updated = float(record.get("updated_at") or 0.0)
        if (time.time() - updated) > QUEUED_STALE_SEC:
            record = _apply_update(
                base,
                job_id,
                record,
                {"status": "failed", "error": "worker never started"},
            )
    return record


def update_job(
    job_id: str,
    updates: Mapping[str, Any],
    *,
    root: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Apply ``updates`` to an existing job and return the new record."""
    _validate_id(job_id)
    base = jobs_root(root)
    record = _read_record(base, job_id)
    if record is None:
        raise contract.VxError(f"unknown job: {job_id}")
    return _apply_update(base, job_id, record, updates)


def list_jobs(*, root: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Every readable record, oldest first. Malformed entries are ignored."""
    base = jobs_root(root)
    try:
        children = list(base.iterdir())
    except OSError:
        return []
    records: List[Dict[str, Any]] = []
    for child in children:
        if not child.is_dir() or JOB_ID_RE.fullmatch(child.name) is None:
            continue
        record = _read_record(base, child.name)
        if record is None:
            continue
        records.append(record)
    records.sort(key=lambda item: float(item.get("created_at") or 0.0))
    return records


def claim_job(job_id: str, *, root: Optional[Union[str, Path]] = None) -> bool:
    """Take the one-shot claim file. True on the first call, False afterwards.

    An unknown (but well-formed) id returns False; a malformed id raises.
    """
    _validate_id(job_id)
    base = jobs_root(root)
    if not _job_path(base, job_id).is_file():
        return False
    claim = base / f"{job_id}{CLAIM_SUFFIX}"
    try:
        fd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.close(fd)
    return True
