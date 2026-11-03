"""vx.resources - quanta macchina c'e', chi la sta gia' usando, a che priorita'.

Il profilo dice dove va il tempo; questo modulo dice con quale diritto lo si
prende. Tre regole, tutte dichiarate nel report:

  * **niente over-subscription**: i worker dell'analisi sono
    ``min(6, cpu - 2)``, e ognuno tiene un solo thread per OpenCV e un numero
    esplicito di thread ffmpeg. Un processo figlio che si moltiplica i thread
    non e' piu' veloce: e' solo piu' rumoroso.
  * **memoria**: sotto 1.5 GB liberi per worker i worker si riducono. Una
    macchina che swappa e' piu' lenta di una che aspetta.
  * **un lavoro pesante per volta**: i lavori si registrano in un file di
    sessione; chi trova un altro render/analisi in corso dimezza i worker e lo
    scrive, invece di rubargli i core in silenzio.

La liveness dei PID passa da :func:`vx.lock.process_alive`: su Windows
``os.kill(pid, 0)`` termina il processo, non lo interroga.
"""

from __future__ import annotations

import ctypes
import functools
import json
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from . import lock

#: Processi di misura al massimo, qualunque sia la macchina.
MAX_WORKERS = 6
#: Core lasciati liberi: il render deve poter partire mentre l'analisi finisce.
CPU_HEADROOM = 2
#: RAM che un worker di misura puo' chiedere senza far swappare la macchina.
RAM_PER_WORKER_MB = 1536.0
#: Priorita' dei processi lunghi su Windows.
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000

REGISTRY_NAME = "vx-active-jobs.json"
_HEAVY = ("analysis", "render")
_WINDOWS = os.name == "nt"
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def cpu_logical() -> int:
    return max(1, int(os.cpu_count() or 1))


def ram_free_mb() -> Optional[float]:
    """Free physical RAM right now, or None when the platform will not say."""
    if _WINDOWS:
        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        try:
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
        except Exception:  # noqa: BLE001 - a probe that cannot answer is not fatal
            return None
        return float(status.ullAvailPhys) / (1024.0 * 1024.0)
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return float(line.split()[1]) / 1024.0
    except OSError:
        return None
    return None


def gpu() -> Dict[str, Any]:
    """The first NVIDIA adapter, asked of the driver (nvidia-smi), not of ffmpeg.

    ``ffmpeg -hwaccels`` lists what the build *can* do; nvidia-smi lists what the
    machine *has*. Whisper usa questo verdetto: senza GPU il fallback e' la CPU,
    e il report lo dice invece di lasciarlo dedurre. Il verdetto si cache: una
    GPU non compare a meta' lavoro, e nvidia-smi costa un processo.
    """
    return dict(_gpu_cached())


@functools.lru_cache(maxsize=1)
def _gpu_cached() -> Dict[str, Any]:
    from shutil import which

    tool = which("nvidia-smi")
    if not tool:
        return {"available": False, "name": None, "memory_mb": None, "source": "nvidia-smi:assente"}
    try:
        proc = subprocess.run(
            [
                tool,
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except Exception:  # noqa: BLE001 - driver present but not answering
        return {"available": False, "name": None, "memory_mb": None, "source": "nvidia-smi:errore"}
    first = (proc.stdout or "").strip().splitlines()
    if proc.returncode != 0 or not first:
        return {"available": False, "name": None, "memory_mb": None, "source": "nvidia-smi:vuoto"}
    parts = [item.strip() for item in first[0].split(",")]
    try:
        memory = int(float(parts[1]))
    except (IndexError, ValueError):
        memory = None
    return {"available": True, "name": parts[0], "memory_mb": memory, "source": "nvidia-smi"}


def set_current_priority(below: bool) -> bool:
    """Lower this process to BELOW_NORMAL (Windows) / nice +5 (POSIX).

    Return True only when the change actually happened: a report that says
    "below normal" must not be a guess about a call that failed.
    """
    if _WINDOWS:
        try:
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            flag = BELOW_NORMAL_PRIORITY_CLASS if below else 0x00000020  # NORMAL
            return bool(ctypes.windll.kernel32.SetPriorityClass(handle, flag))
        except Exception:  # noqa: BLE001
            return False
    try:
        current = os.nice(0)
        os.nice(5 - current if below else -current)
        return True
    except Exception:  # noqa: BLE001
        return False


def child_creation_flags() -> int:
    """Flags for a long child: hidden window, and below normal when asked."""
    flags = 0x08000000 if _WINDOWS else 0  # CREATE_NO_WINDOW
    if _WINDOWS and os.environ.get("VX_CHILD_PRIORITY", "").strip().lower() == "below":
        flags |= BELOW_NORMAL_PRIORITY_CLASS
    return flags


def ffmpeg_threads() -> Optional[int]:
    """Explicit thread count for ffmpeg when the caller declared one."""
    raw = os.environ.get("VX_FFMPEG_THREADS", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def worker_budget(
    jobs: int,
    *,
    ram_free: Optional[float] = None,
    concurrent: int = 0,
) -> Dict[str, Any]:
    """How many measurement workers this machine can carry, and why.

    Ogni limite che morde viene nominato in ``reasons``: un numero piccolo senza
    motivo e' indistinguibile da un bug.
    """
    reasons: List[str] = []
    cpus = cpu_logical()
    workers = min(int(jobs), MAX_WORKERS, max(1, cpus - CPU_HEADROOM))
    if workers < min(int(jobs), MAX_WORKERS):
        reasons.append(f"cpu={cpus}: massimo cpu-{CPU_HEADROOM} e {MAX_WORKERS} worker")
    free = ram_free_mb() if ram_free is None else ram_free
    if free is not None:
        affordable = max(1, int(free // RAM_PER_WORKER_MB))
        if affordable < workers:
            workers = affordable
            reasons.append(
                f"ram libera {free:.0f} MB: {affordable} worker da {RAM_PER_WORKER_MB:.0f} MB"
            )
    else:
        reasons.append("ram non dichiarata dal sistema")
    if concurrent > 0 and workers > 1:
        workers = max(1, (workers + 1) // 2)
        reasons.append(f"{concurrent} lavoro/i pesante/i gia' in corso: worker dimezzati")
    return {
        "workers": max(1, workers),
        "cpu_logical": cpus,
        "ram_free_mb": None if free is None else round(float(free), 1),
        "concurrent_jobs": int(concurrent),
        "reasons": reasons,
    }


def registry_path() -> Path:
    return Path(tempfile.gettempdir()) / REGISTRY_NAME


def _read_registry() -> List[Dict[str, Any]]:
    path = registry_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _write_registry(entries: List[Dict[str, Any]]) -> None:
    path = registry_path()
    try:
        temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(entries, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
    except OSError:
        # Un registro che non si scrive costa una dichiarazione in meno, non un
        # lavoro: si prosegue senza, e il report dira' solo quanti ne ha visti.
        return


def active_jobs(kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """Live heavy jobs on this machine, this process excluded.

    Un PID morto (o riciclato) esce dal registro quando lo si legge: nessuno
    deve aspettare il fantasma di un processo che non c'e' piu'.
    """
    mine = os.getpid()
    alive: List[Dict[str, Any]] = []
    for item in _read_registry():
        pid = int(item.get("pid") or 0)
        if pid in (0, mine):
            continue
        if not lock.process_alive(pid):
            continue
        created = float(item.get("created") or 0.0)
        if created and abs(lock.process_created(pid) - created) > 2.0:
            continue
        if kind is not None and str(item.get("kind")) != kind:
            continue
        alive.append(item)
    return alive


@contextmanager
def register_job(kind: str, workdir: Any = "") -> Iterator[None]:
    """Announce one heavy job for as long as the block runs."""
    entry = {
        "pid": os.getpid(),
        "kind": str(kind),
        "workdir": str(workdir or ""),
        "started": round(time.time(), 3),
        "created": round(lock.process_created(os.getpid()), 3),
    }
    entries = [item for item in _read_registry() if int(item.get("pid") or 0) != os.getpid()]
    entries.append(entry)
    _write_registry(entries)
    try:
        yield
    finally:
        _write_registry(
            [item for item in _read_registry() if int(item.get("pid") or 0) != os.getpid()]
        )


def heavy_jobs() -> int:
    """How many other heavy jobs (render or analysis) are running right now."""
    return len([item for item in active_jobs() if str(item.get("kind")) in _HEAVY])
