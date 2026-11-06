"""vx.lock — un lease **per cartella**, tenuto vivo da un battito fuori banda.

Il lock non e' un TTL sulla cartella: e' la prova che *quel* processo sta ancora
lavorando. La regola e' questa:

  * il lease vive in ``<workdir>/.vx/lock`` e dichiara
    ``{pid, host, token, created, heartbeat_epoch, stage}``;
  * un thread dedicato rinnova ``heartbeat_epoch`` ogni :data:`HEARTBEAT_SECONDS`
    secondi, **fuori** dal loop di lavoro: se il render blocca per minuti, il
    battito continua e il lease non scade;
  * il lease e' vivo se ``now - heartbeat_epoch <= LEASE_TTL_SECONDS``;
  * si scade **solo su morte reale**: heartbeat vecchio *e* PID sparito (o PID
    riciclato, o host diverso con battito fermo da :data:`LEASE_TTL_SECONDS`).
    Un processo vivo tiene il lease per sempre, anche con il battito fermo: la
    prudenza costa un'attesa, la fretta corrompe ``run.jsonl``;
  * quando si recupera un lease stantio lo si **dichiara** sui campi
    ``stale_recovered``/``stale_recovered_pid``/``stale_recovered_age_s`` e su
    ``stderr``, cosi' il report dice chi c'era prima e da quanto taceva;
  * due cartelle diverse hanno due lease diversi: due lavori legittimi non si
    bloccano a vicenda.

Windows: ``os.kill(pid, 0)`` non si usa mai qui. Su Windows qualunque segnale
diverso da CTRL_C_EVENT/CTRL_BREAK_EVENT finisce in TerminateProcess: chiedere
"sei vivo?" ammazzerebbe il render che stiamo proteggendo. La liveness passa da
OpenProcess/GetExitCodeProcess.

Il file ``.vx/lock`` non e' l'unica cosa che si tocca: l'acquisizione e i
rinnovi stanno sotto un guard file (``.vx/lock.guard``), cosi' due processi che
partono insieme non scrivono entrambi lo stesso lease. Il guard e' un lock di
sistema: se chi lo tiene muore, lo rilascia il sistema operativo, senza TTL.
"""

from __future__ import annotations

import json
import os
import random
import socket
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from . import contract

#: Il lease vive dentro la cartella, in una sottocartella che non e' un artefatto.
LOCK_DIR = ".vx"
LOCK_FILE = "lock"

#: Il vecchio nome (``<workdir>/.lock``). Resta leggibile per compatibilita' con
#: i chiamanti che lo scrivono a mano (``vx serve``, i test di crash); la nuova
#: scrittura usa sempre :data:`LOCK_DIR`/:data:`LOCK_FILE`.
LOCK_NAME = ".lock"

#: Cadenza del battito: fuori banda, non dipende dal loop di lavoro.
HEARTBEAT_SECONDS = 60.0

#: Un lease e' vivo se il battito ha meno di cinque minuti.
LEASE_TTL_SECONDS = 300.0

#: Nome del guard che serializza lettura+decisione+scrittura del lease.
GUARD_NAME = "lock.guard"

_WINDOWS = os.name == "nt"
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

#: Su Windows ``msvcrt.locking`` e' per handle: due thread dello stesso processo
#: devono comunque mettersi in fila.
_GUARD_THREAD = threading.Lock()


@dataclass
class LockInfo:
    pid: int = 0
    host: str = ""
    token: str = ""
    created: float = 0.0
    heartbeat_epoch: float = 0.0
    stage: str = ""
    #: Ora di creazione del processo: distingue un PID riciclato da quello vero.
    created_process: float = 0.0
    #: Recupero di un lease stantio: chi c'era, quanto taceva il battito.
    stale_recovered: bool = False
    stale_recovered_pid: int = 0
    stale_recovered_age_s: float = 0.0
    recovered_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LockInfo":
        known = {f for f in cls.__dataclass_fields__}
        raw = dict(data or {})
        # Il formato precedente chiamava il battito ``updated``.
        if "heartbeat_epoch" not in raw and "updated" in raw:
            raw["heartbeat_epoch"] = raw.get("updated") or 0.0
        return cls(**{k: v for k, v in raw.items() if k in known})


# --------------------------------------------------------------------------- #
# liveness del processo
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
    """Ora di creazione di ``pid`` come timestamp unix (0.0 se non si sa)."""
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
    except Exception:  # noqa: BLE001 - dipendenza opzionale
        return 0.0


def _matches_owner(info: LockInfo, alive: Optional[bool] = None) -> bool:
    is_alive = process_alive(info.pid) if alive is None else alive
    if not is_alive:
        return False
    if info.created_process <= 0:
        return True
    actual = process_created(info.pid)
    if actual <= 0:
        return True
    return abs(actual - info.created_process) <= 2.0


# --------------------------------------------------------------------------- #
# dove vive il lease
# --------------------------------------------------------------------------- #


def lock_dir(workdir: str | Path) -> Path:
    return Path(workdir) / LOCK_DIR


def path_for(workdir: str | Path) -> Path:
    """Il lease nuovo: ``<workdir>/.vx/lock``."""
    return lock_dir(workdir) / LOCK_FILE


def _legacy_path(workdir: str | Path) -> Path:
    return Path(workdir) / LOCK_NAME


def read(workdir: str | Path) -> Optional[LockInfo]:
    """Legge il lease nuovo; se manca, accetta il vecchio ``.lock``."""
    for target in (path_for(workdir), _legacy_path(workdir)):
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return LockInfo.from_dict(data)
        except (TypeError, ValueError):
            return None
    return None


def _write(workdir: str | Path, info: LockInfo) -> None:
    """Scrittura atomica: chi legge vede il lease vecchio o quello nuovo."""
    target = path_for(workdir)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(
        f"{LOCK_FILE}.{os.getpid()}.{random.randrange(16**6):06x}.tmp"
    )
    tmp.write_text(json.dumps(info.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, target)


# --------------------------------------------------------------------------- #
# guard fra processi (breve: millisecondi)
# --------------------------------------------------------------------------- #


@contextmanager
def _guard(workdir: str | Path) -> Iterator[None]:
    target = lock_dir(workdir) / GUARD_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    with _GUARD_THREAD:
        handle = open(target, "a+b")
        locked = False
        try:
            # Un byte fisico: su Windows il lock e' su un intervallo del file.
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            if _WINDOWS:
                import msvcrt

                deadline = time.monotonic() + 15.0
                while True:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        locked = True
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            break
                        time.sleep(0.02)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked = True
            # Se il file system non sa fare il lock si perde solo la garanzia
            # fra processi: il guard resta un attrezzo, non un requisito.
            yield
        finally:
            if locked:
                try:
                    handle.seek(0)
                    if _WINDOWS:
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()


# --------------------------------------------------------------------------- #
# stato
# --------------------------------------------------------------------------- #


def _heartbeat_age(info: LockInfo, now: float) -> float:
    beat = info.heartbeat_epoch or info.created or 0.0
    return max(0.0, now - beat) if beat else 0.0


def state(workdir: str | Path) -> Dict[str, Any]:
    """Vivo o stantio? ``stale`` significa: si puo' prendere, dichiarandolo.

    ``stale`` non e' l'eta' del file: e' la morte del proprietario. Un processo
    vivo con il battito vecchio resta proprietario; un PID sparito no.
    """
    info = read(workdir)
    if info is None:
        return {
            "present": False,
            "stale": True,
            "reason": "no lock file",
            "owner": None,
            "heartbeat_age_s": 0.0,
            "stale_recovered": False,
        }

    now = time.time()
    age = _heartbeat_age(info, now)
    fresh = age <= LEASE_TTL_SECONDS
    local_host = socket.gethostname()
    same_host = (not info.host) or info.host == local_host

    if same_host:
        alive = process_alive(info.pid)
        matches = _matches_owner(info, alive=alive)
        if not alive:
            stale, reason = True, "owner pid is gone"
        elif not matches:
            stale, reason = True, "pid was recycled by another process"
        elif fresh:
            stale, reason = False, "heartbeat fresh"
        else:
            # Il processo c'e' ancora: non si scade. Un loop bloccato non e'
            # un processo morto, e due scrittori sono peggio di un'attesa.
            stale, reason = False, "owner pid alive (heartbeat old)"
    else:
        # Da un altro host il PID non si puo' interrogare: decide il battito.
        if fresh:
            stale, reason = False, "remote heartbeat fresh"
        else:
            stale, reason = (
                True,
                f"remote heartbeat older than {LEASE_TTL_SECONDS:.0f}s",
            )

    return {
        "present": True,
        "stale": stale,
        "reason": reason,
        "owner": info.to_dict(),
        "heartbeat_age_s": round(age, 1),
        "stale_recovered": bool(info.stale_recovered),
        "stale_recovered_pid": int(info.stale_recovered_pid or 0),
        "stale_recovered_age_s": round(float(info.stale_recovered_age_s or 0.0), 1),
    }


# --------------------------------------------------------------------------- #
# lease
# --------------------------------------------------------------------------- #


class Heartbeat:
    """Rinnova ``heartbeat_epoch`` mentre il proprietario lavora.

    Il thread non guarda il loop di lavoro: dorme il proprio intervallo e
    riscrive il battito. Non ruba mai un lease, non scrive quello di un altro.
    """

    def __init__(
        self,
        workdir: str | Path,
        interval: float = HEARTBEAT_SECONDS,
        *,
        info: Optional[LockInfo] = None,
    ) -> None:
        self.workdir = Path(workdir)
        self.interval = max(1.0, float(interval))
        self.info = info if info is not None else read(self.workdir)
        self.stage = self.info.stage if self.info else ""
        self.recovered: Optional[Dict[str, Any]] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.lost = False

    def _beat(self) -> bool:
        with _guard(self.workdir):
            current = read(self.workdir)
            token = self.info.token if self.info else ""
            if current is None or current.token != token:
                self.lost = True
                return False
            current.heartbeat_epoch = time.time()
            current.stage = self.stage
            _write(self.workdir, current)
            self.info = current
        return True

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            if not self._beat():
                return

    def start(self) -> "Heartbeat":
        if self.info is None:
            return self
        self._thread = threading.Thread(
            target=self._run, name="vx-lease-heartbeat", daemon=True
        )
        self._thread.start()
        return self

    def bump(self, stage: str) -> None:
        """Dichiara lo stadio e rinnova subito il battito."""
        self.stage = stage
        if self.info is None:
            return
        if not self._beat():
            self.lost = True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def _new_info(stage: str) -> LockInfo:
    now = time.time()
    return LockInfo(
        pid=os.getpid(),
        host=socket.gethostname(),
        token=f"{int(now)}-{random.randrange(16**8):08x}",
        created=now,
        heartbeat_epoch=now,
        stage=stage,
        created_process=process_created(os.getpid()),
    )


def _busy_message(workdir: Path, snapshot: Dict[str, Any]) -> str:
    owner = snapshot.get("owner") or {}
    held_for = max(0.0, time.time() - float(owner.get("created") or 0.0))
    return (
        "cartella occupata: il lease e' tenuto dal pid "
        f"{owner.get('pid')} su {owner.get('host') or 'questo host'}, "
        f"stadio '{owner.get('stage') or 'sconosciuto'}', "
        f"da {held_for:.0f}s (battito {snapshot.get('heartbeat_age_s', 0)}s fa); "
        f"non ho toccato niente ({workdir})"
    )


def assert_free(workdir: str | Path) -> Optional[Dict[str, Any]]:
    """Controllo di sola lettura: nessun file scritto, nessun lease preso.

    Serve ai comandi che devono fermarsi *prima* di toccare la workdir (pannello,
    journal, run log): se un altro processo tiene il lease, si esce 2 subito.
    """
    snapshot = state(workdir)
    if snapshot.get("present") and not snapshot.get("stale"):
        raise contract.VxError(_busy_message(Path(workdir), snapshot))
    return snapshot if snapshot.get("present") else None


def acquire(
    workdir: str | Path,
    *,
    stage: str = "init",
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> Heartbeat:
    """Prende il lease della cartella o fallisce dicendo chi lo tiene.

    ``heartbeat_seconds`` esiste per i test e per chi vuole un battito piu'
    fitto; il default e' :data:`HEARTBEAT_SECONDS`. La scadenza non dipende da
    questo numero: la decide :data:`LEASE_TTL_SECONDS` piu' la liveness del PID.
    """
    workdir = Path(workdir)
    recovered: Optional[Dict[str, Any]] = None

    with _guard(workdir):
        info = read(workdir)
        if info is not None:
            snapshot = state(workdir)
            if not snapshot.get("stale"):
                raise contract.VxError(_busy_message(workdir, snapshot))
            recovered = {
                "pid": int(info.pid or 0),
                "host": info.host,
                "stage": info.stage,
                "age_s": round(float(snapshot.get("heartbeat_age_s") or 0.0), 1),
                "reason": snapshot.get("reason"),
            }
        fresh = _new_info(stage)
        if recovered is not None:
            fresh.stale_recovered = True
            fresh.stale_recovered_pid = recovered["pid"]
            fresh.stale_recovered_age_s = recovered["age_s"]
            fresh.recovered_at = time.time()
        _write(workdir, fresh)
        if recovered is not None and info is not None:
            # Un vecchio `.lock` stantio non deve far dichiarare "recuperato" a
            # ogni acquisizione successiva: se e' quello che abbiamo appena
            # sostituito, sparisce.
            legacy = _legacy_path(workdir)
            try:
                old = LockInfo.from_dict(
                    json.loads(legacy.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                old = None
            if old is not None and old.token == info.token:
                legacy.unlink(missing_ok=True)

    if recovered is not None:
        # La dichiarazione e' parte del contratto: chi legge il log deve sapere
        # che il lavoro precedente non e' finito, e' morto.
        print(
            "[lock] lease stantio recuperato: "
            f"pid {recovered['pid']} ({recovered['host'] or 'host sconosciuto'}), "
            f"stadio '{recovered['stage'] or 'sconosciuto'}', "
            f"battito fermo da {recovered['age_s']}s "
            f"({recovered['reason']}); il lease e' ora del pid {fresh.pid}",
            file=sys.stderr,
            flush=True,
        )

    beat = Heartbeat(workdir, interval=heartbeat_seconds, info=fresh)
    beat.recovered = recovered
    beat.start()
    return beat


def release(beat: Optional[Heartbeat]) -> bool:
    """Rimuove il lease solo se questo processo ne e' ancora il proprietario."""
    if beat is None:
        return False
    beat.stop()
    with _guard(beat.workdir):
        current = read(beat.workdir)
        if current is None or not beat.info or current.token != beat.info.token:
            return False
        path_for(beat.workdir).unlink(missing_ok=True)
        return True


def assert_safe_liveness_check(timeout: float = 5.0) -> bool:
    """Prova che chiedere "sei vivo?" non puo' uccidere il processo chiesto.

    Su Windows `os.kill(pid, 0)` chiama TerminateProcess: un ingenuo "e' vivo?"
    terminerebbe il render che doveva proteggere. Qui si lancia un figlio breve,
    lo si interroga due volte e si verifica che sia ancora in piedi.
    """
    import subprocess

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
        except Exception:  # noqa: BLE001 - pulizia best effort
            pass
