"""vx.lock — ownership of a workdir by liveness, never by wall-clock age.

The old rule was "if the lock is older than 30 minutes, take it". That is not a
safety net, it is a way to let a second run stomp the stage files of a render that is
still encoding: age says nothing about whether the owner is alive.

This module asks the operating system instead:

  * the lock file carries the owner PID, a random token, the process creation time
    and a heartbeat timestamp;
  * a workdir is stale only when the owner process is gone (or the PID was recycled
    by an unrelated process, detected by comparing creation times);
  * an owner that is alive keeps the lock forever, no matter how long the render runs;
  * a background thread refreshes the heartbeat while the owner works, so a *stuck*
    owner is distinguishable from a *dead* one.

Windows note: ``os.kill(pid, 0)`` must never be used here. On Windows any signal other
than CTRL_C_EVENT/CTRL_BREAK_EVENT is passed to TerminateProcess — asking "are you
alive?" would kill the very render we are protecting. Liveness goes through
OpenProcess/GetExitCodeProcess.
"""

from __future__ import annotations

import json
import os
import random
import socket
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from . import contract

LOCK_NAME = ".lock"
_WINDOWS = os.name == "nt"
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass
class LockInfo:
    pid: int = 0
    token: str = ""
    created: float = 0.0
    updated: float = 0.0
    stage: str = ""
    host: str = ""
    created_process: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LockInfo":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


# --------------------------------------------------------------------------- #
# process liveness
# --------------------------------------------------------------------------- #


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _WINDOWS:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_created(pid: int) -> float:
    """Creation time of `pid` as a unix timestamp (0.0 when unknown)."""
    if pid <= 0:
        return 0.0
    if _WINDOWS:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return 0.0
        try:
            creation, exit_, kernel, user = (
                FILETIME(),
                FILETIME(),
                FILETIME(),
                FILETIME(),
            )
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return 0.0
            ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return ticks / 10_000_000 - 11_644_473_600
        finally:
            kernel32.CloseHandle(handle)
    try:
        import psutil  # type: ignore

        return float(psutil.Process(pid).create_time())
    except Exception:  # noqa: BLE001 - optional dependency
        return 0.0


def _matches_owner(info: LockInfo) -> bool:
    if not process_alive(info.pid):
        return False
    if info.created_process <= 0:
        return True
    actual = process_created(info.pid)
    if actual <= 0:
        return True
    return abs(actual - info.created_process) <= 2.0


# --------------------------------------------------------------------------- #
# lock file
# --------------------------------------------------------------------------- #


def path_for(workdir: str | Path) -> Path:
    return Path(workdir) / LOCK_NAME


def read(workdir: str | Path) -> Optional[LockInfo]:
    target = path_for(workdir)
    try:
        return LockInfo.from_dict(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None


def _write(workdir: str | Path, info: LockInfo) -> None:
    target = path_for(workdir)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(info.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, target)


def state(workdir: str | Path) -> Dict[str, Any]:
    """Owner liveness plus heartbeat freshness. `stale` means: safe to reclaim."""
    info = read(workdir)
    if info is None:
        return {
            "present": False,
            "stale": True,
            "reason": "no lock file",
            "owner": None,
        }
    alive = process_alive(info.pid)
    matches = _matches_owner(info)
    age = max(0.0, time.time() - info.updated) if info.updated else 0.0
    if not alive:
        return {
            "present": True,
            "stale": True,
            "reason": "owner pid is gone",
            "owner": info.to_dict(),
            "heartbeat_age_s": round(age, 1),
        }
    if not matches:
        return {
            "present": True,
            "stale": True,
            "reason": "pid was recycled by another process",
            "owner": info.to_dict(),
            "heartbeat_age_s": round(age, 1),
        }
    return {
        "present": True,
        "stale": False,
        "reason": "owner alive",
        "owner": info.to_dict(),
        "heartbeat_age_s": round(age, 1),
    }


class Heartbeat:
    """Refreshes `updated` while the owner works. Never steals, never writes a foreign lock."""

    def __init__(self, workdir: str | Path, interval: float = 30.0) -> None:
        self.workdir = Path(workdir)
        self.interval = max(1.0, float(interval))
        self.info = read(self.workdir)
        self.stage = self.info.stage if self.info else ""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.lost = False

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            current = read(self.workdir)
            if current is None or current.token != (
                self.info.token if self.info else ""
            ):
                self.lost = True
                return
            current.updated = time.time()
            current.stage = self.stage
            _write(self.workdir, current)

    def start(self) -> "Heartbeat":
        if self.info is None:
            return self
        self._thread = threading.Thread(
            target=self._run, name="vx-heartbeat", daemon=True
        )
        self._thread.start()
        return self

    def bump(self, stage: str) -> None:
        self.stage = stage
        current = read(self.workdir)
        if current is None or not self.info or current.token != self.info.token:
            self.lost = True
            return
        current.updated = time.time()
        current.stage = stage
        _write(self.workdir, current)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def acquire(
    workdir: str | Path, *, stage: str = "init", heartbeat_seconds: float = 30.0
) -> Heartbeat:
    """Take the workdir or fail with a message that says who holds it and since when."""
    workdir = Path(workdir)
    info = read(workdir)
    if info is not None:
        snapshot = state(workdir)
        if not snapshot["stale"]:
            owner = snapshot["owner"] or {}
            raise contract.VxError(
                f"workdir busy: pid {owner.get('pid')} on {owner.get('host')} "
                f"stage '{owner.get('stage')}' heartbeat {snapshot['heartbeat_age_s']}s ago "
                f"({workdir})"
            )
        _write(
            workdir,
            LockInfo(
                pid=os.getpid(),
                token=f"{int(time.time())}-{random.randrange(16**8):08x}",
                created=time.time(),
                updated=time.time(),
                stage=stage,
                host=socket.gethostname(),
                created_process=process_created(os.getpid()),
            ),
        )
    else:
        _write(
            workdir,
            LockInfo(
                pid=os.getpid(),
                token=f"{int(time.time())}-{random.randrange(16**8):08x}",
                created=time.time(),
                updated=time.time(),
                stage=stage,
                host=socket.gethostname(),
                created_process=process_created(os.getpid()),
            ),
        )
    beat = Heartbeat(workdir, interval=heartbeat_seconds)
    beat.start()
    return beat


def release(beat: Optional[Heartbeat]) -> bool:
    """Remove the lock only if this process still owns it."""
    if beat is None:
        return False
    beat.stop()
    current = read(beat.workdir)
    if current is None or not beat.info or current.token != beat.info.token:
        return False
    path_for(beat.workdir).unlink(missing_ok=True)
    return True


def assert_safe_liveness_check(timeout: float = 5.0) -> bool:
    """Prove that probing liveness cannot kill the probe target.

    On Windows `os.kill(pid, 0)` calls TerminateProcess, so a naive "is it alive?"
    would terminate the render it was asked to protect. This spawns a short-lived
    child, queries it twice and confirms it is still running afterwards.
    """
    import subprocess
    import sys

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(int(timeout * 10)):
            if process_alive(child.pid):
                break
            time.sleep(0.1)
        before = process_alive(child.pid)
        process_alive(child.pid)
        process_alive(child.pid)
        after = child.poll() is None
        return bool(before and after)
    finally:
        try:
            child.kill()
            child.wait(timeout=5)
        except Exception:  # noqa: BLE001 - best effort cleanup
            pass
