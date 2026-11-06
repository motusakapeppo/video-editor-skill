"""vx.session — il registro di sessione, indipendente dalla cartella di lavoro.

La vista non deve piu' dipendere da *dove* un lavoro scrive i file: ogni comando
apre una sessione in ``~/.vx/session/<id>/`` (override ``VX_SESSION_DIR``),
appende una riga in ``events.jsonl`` e riscrive atomicamente ``state.json``.
Una sessione racconta il principio (``start``), le fasi (``stage``), i file
prodotti (``artifact``), le scelte (``decision``), i cancelli (``gate``),
gli errori (``error``) e la fine (``end``), con ``t`` in secondi dall'inizio e
``job`` (la workdir, se c'e') su ogni evento.

Forma di un evento::

    {"t": 1.234, "type": "stage", "stage": "proxy", "percent": 12.0,
     "detail": "Proxy pronti", "job": "lavoro", "job_path": "work/lavoro"}

Il registro non deve costare: con ``VX_SESSION=0`` (o ``false``/``off``/``no``)
non si apre nessun file, e qualunque errore di scrittura spegne la sessione in
silenzio. Nessuna eccezione di questo modulo deve fermare un lavoro.

Il silenzio e' un guasto della vista, non un dettaglio: uno stadio lungo che non
scrive niente per minuti sembra un processo morto. Per questo la sessione batte
il cuore da sola — un ``heartbeat`` ogni :data:`HEARTBEAT_SEC` al massimo — e
tace **solo** quando e' ferma ad aspettare una risposta umana: li' l'evento e'
``gate`` e lo dice. Con ``VX_HEARTBEAT=0`` o un ``clock`` iniettato il thread non
parte: il battito lo pilota chi chiama ``tick()``, cosi' i test sono
deterministici.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import contract, degrade

SCHEMA = "vx.session/1"
EVENTS_NAME = "events.jsonl"
STATE_NAME = "state.json"
ENV_DIR = "VX_SESSION_DIR"
ENV_ENABLED = "VX_SESSION"

#: Oltre questo tetto il file corrente viene ruotato: la vista legge tutti i
#: pezzi in ordine, quindi la rotazione non interrompe la storia.
MAX_EVENT_BYTES = 5 * 1024 * 1024

#: Il tetto di silenzio promesso alla vista. Il battito parte a meta' di questo
#: intervallo: un thread non e' un orologio, e il margine e' quello che rende
#: vera la promessa "< 2 s" anche quando lo scheduling del sistema ritarda.
HEARTBEAT_SEC = 2.0
ENV_HEARTBEAT = "VX_HEARTBEAT"

#: Valori che spengono il registro. Il confronto e' case-insensitive.
_DISABLED_VALUES = frozenset({"0", "false", "no", "off", "none", "disabled"})

#: Un guasto del registro si dice **una volta** su stderr, mai a ogni evento:
#: il lavoro prosegue, chi legge i log sa che la sessione non e' su disco.
_WARN_LOCK = threading.Lock()
_WARNED = False


def _warn_once(message: Any) -> None:
    """Una riga su stderr, la prima volta. Non solleva mai."""
    global _WARNED
    try:
        with _WARN_LOCK:
            if _WARNED:
                return
            _WARNED = True
        print(f"[vx] session: {message}", file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 - nemmeno l'avviso puo' fermare un lavoro
        pass


def enabled() -> bool:
    """True se il registro e' acceso (default) e la scrittura vale la pena."""
    raw = str(os.environ.get(ENV_ENABLED, "1")).strip().lower()
    return raw not in _DISABLED_VALUES


def heartbeats_enabled() -> bool:
    """True se il battito automatico e' acceso (default), spento da ``VX_HEARTBEAT=0``."""
    raw = str(os.environ.get(ENV_HEARTBEAT, "1")).strip().lower()
    return raw not in _DISABLED_VALUES


def default_root() -> Path:
    """``~/.vx/session`` oppure ``VX_SESSION_DIR``."""
    raw = str(os.environ.get(ENV_DIR) or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".vx" / "session"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


def _safe_id(value: Any) -> str:
    """Un id non puo' contenere separatori: la sessione sta sotto la radice."""
    text = str(value or "")
    name = Path(text).name
    return name if name == text else ""


def _atomic_write(path: Path, data: bytes) -> None:
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    try:
        temp.write_bytes(data)
        # Su Windows un lettore che ha il file aperto puo' far fallire lo
        # ``replace`` per un istante: si riprova, senza mai scrivere a metà.
        for attempt in range(5):
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.002)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _rotation_index(path: Path) -> Optional[int]:
    match = re.search(r"(\d+)", path.stem)
    return int(match.group(1)) if match else None


def _event_files(directory: Path) -> List[Path]:
    """I file di eventi in ordine cronologico: i ruotati prima del corrente.

    I nomi ruotati sono ``events-002.jsonl``, ``events-003.jsonl``, ...; si
    accetta anche la vecchia forma ``events.0001.jsonl``. L'ordine non dipende
    dall'alfabeto: si ordina l'indice, e il file corrente viene per ultimo.
    """
    try:
        found = [
            path
            for path in directory.glob("events*.jsonl")
            if path.is_file() and ".tmp." not in path.name
        ]
    except OSError:
        return []

    def order(path: Path) -> tuple:
        if path.name == EVENTS_NAME:
            return (1, 0)
        index = _rotation_index(path)
        return (0, index if index is not None else 0)

    return sorted(found, key=order)


def _read_events(directory: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in _event_files(directory):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                # Una riga troncata (scrittore ancora in corso) non ferma la
                # lettura: la storia e' tutto cio' che si e' potuto leggere.
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class Session:
    """Una sessione: eventi append-only, snapshot atomico, errori muti."""

    def __init__(
        self,
        directory: Optional[Path],
        *,
        session_id: str = "",
        kind: str = "",
        argv: Optional[Sequence[Any]] = None,
        cwd: str = "",
        job: str = "",
        job_path: str = "",
        active: bool = True,
        max_bytes: int = MAX_EVENT_BYTES,
        clock: Any = None,
        started: Optional[float] = None,
        started_utc: str = "",
        status: str = "running",
        events: Optional[Sequence[Dict[str, Any]]] = None,
        heartbeat: Optional[float] = None,
    ) -> None:
        self.directory = Path(directory) if directory is not None else None
        self.id = str(session_id)
        self.kind = str(kind)
        self.argv = [str(item) for item in (argv or [])]
        self.cwd = str(cwd)
        self.job = str(job)
        self.job_path = str(job_path)
        self.status = str(status)
        self.max_bytes = max(256, int(max_bytes))
        self.started_utc = str(started_utc)
        self._clock = clock if clock is not None else time.monotonic
        self._started = float(started) if started is not None else self._clock()
        self._last_t = 0.0
        self._ended = False
        self._active = bool(active) and self.directory is not None
        self._counts: Dict[str, int] = {}
        self._stage: Dict[str, Any] = {}
        self._stages: List[Dict[str, Any]] = []
        self._artifacts: List[Dict[str, Any]] = []
        self._decisions: List[Dict[str, Any]] = []
        self._gates: List[Dict[str, Any]] = []
        self._gate_state: Dict[str, bool] = {}
        self._errors: List[Dict[str, Any]] = []
        self._journal: List[Dict[str, Any]] = []
        self._live: Dict[str, Any] = {"ready": 0, "total": 0, "chunks": []}
        self._rotated: List[str] = []
        self.events: List[Dict[str, Any]] = []
        #: Il battito: intervallo dichiarato, soglia reale (meta'), thread e stato.
        self._beat = max(0.05, float(HEARTBEAT_SEC if heartbeat is None else heartbeat))
        self._beat_at = max(0.02, self._beat / 2.0)
        self._beat_thread: Optional[threading.Thread] = None
        self._beat_stop = threading.Event()
        self._beats = 0
        self._real_clock = clock is None
        self._stage_started = 0.0
        self._eta_total: Optional[float] = None
        self._duration_sec: Optional[float] = None
        self._last_heartbeat: Dict[str, Any] = {}
        #: Un solo scrittore alla volta: il thread del battito e il lavoro
        #: appende righe allo stesso file.
        self._lock = threading.RLock()
        #: Perche' la sessione non scrive (se non scrive): lo legge ``auto``
        #: per dichiarare il degrado senza indovinare.
        self.last_error = ""
        for row in events or []:
            self._ingest_row(row)

    # -- costruzione ----------------------------------------------------- #

    @classmethod
    def disabled(
        cls,
        kind: str = "",
        argv: Optional[Sequence[Any]] = None,
        cwd: str = "",
        *,
        job: str = "",
        job_path: str = "",
    ) -> "Session":
        return cls(
            None,
            kind=kind,
            argv=argv,
            cwd=cwd,
            job=job,
            job_path=job_path,
            active=False,
        )

    @classmethod
    def begin(
        cls,
        kind: str,
        argv: Optional[Sequence[Any]] = None,
        cwd: Optional[Any] = None,
        *,
        job: Optional[Any] = None,
        root: Optional[Any] = None,
        max_bytes: Optional[int] = None,
        clock: Any = None,
        heartbeat: Optional[float] = None,
    ) -> "Session":
        """Apre una sessione e scrive ``start``. Non solleva mai eccezioni."""
        try:
            if not enabled():
                return cls.disabled(kind, argv, str(cwd or ""))
            base = Path(root) if root is not None else default_root()
            session_id = _new_id()
            directory = base / session_id
            directory.mkdir(parents=True, exist_ok=True)
            now = time.monotonic() if clock is None else float(clock())
            session = cls(
                directory,
                session_id=session_id,
                kind=kind,
                argv=argv,
                cwd=str(cwd or os.getcwd()),
                job=Path(job).name if job else "",
                job_path=str(Path(job)) if job else "",
                max_bytes=MAX_EVENT_BYTES if max_bytes is None else int(max_bytes),
                clock=clock,
                started=now,
                started_utc=_utc_now(),
                status="running",
                heartbeat=heartbeat,
            )
            session.event("start", kind=str(kind), argv=session.argv, cwd=session.cwd)
            session.start_heartbeat()
            return session
        except Exception as exc:  # noqa: BLE001 - un registro rotto non ferma un lavoro
            try:
                degrade.note("session", exc)
            except Exception:  # noqa: BLE001
                pass
            lost = cls.disabled(kind, argv, str(cwd or ""))
            lost.last_error = f"{type(exc).__name__}: {exc}"
            _warn_once(
                f"apertura non riuscita, lavoro prosegue "
                f"({lost.last_error})"
            )
            return lost

    @classmethod
    def load(
        cls, session_id: Optional[Any] = None, *, root: Optional[Any] = None
    ) -> Optional["Session"]:
        """Rilegge una sessione (l'ultima se ``session_id`` manca), senza scrivere."""
        try:
            base = Path(root) if root is not None else default_root()
            if session_id is None or str(session_id) == "":
                name = latest(root=base)
                if not name:
                    return None
            else:
                name = _safe_id(session_id)
                if not name:
                    return None
            directory = base / name
            if not directory.is_dir():
                return None
            state = _read_json(directory / STATE_NAME)
            events = _read_events(directory)
            if not state and not events:
                return None
            session = cls(
                directory,
                session_id=str(state.get("id") or name),
                kind=str(state.get("kind") or ""),
                argv=state.get("argv") or [],
                cwd=str(state.get("cwd") or ""),
                job=str(state.get("job") or ""),
                job_path=str(state.get("job_path") or ""),
                status=str(state.get("status") or "running"),
                started_utc=str(state.get("started_utc") or ""),
                events=events,
                active=False,
            )
            session._merge_state(state)
            session._rotated = [
                str(item) for item in (state.get("rotated") or []) if str(item)
            ]
            return session
        except Exception:  # noqa: BLE001
            return None

    # -- eventi ---------------------------------------------------------- #

    @property
    def path(self) -> Optional[Path]:
        return self.directory / EVENTS_NAME if self.directory is not None else None

    @property
    def active(self) -> bool:
        return self._active and not self._ended

    def set_job(self, job: Any, *, name: Optional[str] = None) -> None:
        """La workdir di questa sessione: da qui in poi ogni evento la porta."""
        if not self._active or job is None:
            return
        try:
            path = Path(str(job))
            self.job_path = str(path)
            self.job = str(name or path.name)
            self._write_state()
        except Exception as exc:  # noqa: BLE001
            self._soft_fail(exc)

    def _soft_fail(self, exc: Any) -> None:
        """Un guasto non fatale (es. lo snapshot): si annota, si continua."""
        self.last_error = f"{type(exc).__name__}: {exc}"
        try:
            degrade.note("session", exc)
        except Exception:  # noqa: BLE001 - anche l'annotazione e' accessoria
            pass
        _warn_once(f"registro non scrivibile, lavoro prosegue ({self.last_error})")

    def _fail(self, exc: Any) -> None:
        """Spegne la sessione, annota il motivo una volta, non solleva."""
        self._active = False
        self._soft_fail(exc)

    def event(self, type: str, **fields: Any) -> Optional[Dict[str, Any]]:
        """Appende un evento e aggiorna lo snapshot. Muto se spento/rotto."""
        with self._lock:
            return self._event(type, **fields)

    def _event(self, type: str, **fields: Any) -> Optional[Dict[str, Any]]:
        if not self._active or self._ended or self.directory is None:
            return None
        try:
            moment = round(max(0.0, float(self._clock()) - self._started), 3)
            if moment < self._last_t:
                moment = self._last_t
            row: Dict[str, Any] = {"t": moment, "type": str(type)}
            if self.job:
                row["job"] = self.job
            if self.job_path:
                row["job_path"] = self.job_path
            for key, value in fields.items():
                if value is None:
                    continue
                row[str(key)] = value
            line = (
                json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str)
                + "\n"
            ).encode("utf-8")
            self._append(line)
            self._ingest_row(row)
        except Exception as exc:  # noqa: BLE001 - il registro tace, il lavoro continua
            self._fail(exc)
            return None
        try:
            self._write_state()
        except Exception as exc:  # noqa: BLE001 - lo snapshot e' accessorio
            self._soft_fail(exc)
        return row

    # -- battito --------------------------------------------------------- #

    def set_eta(self, seconds: Optional[float] = None, *, duration_sec: Any = None) -> None:
        """Dichiara il tempo atteso: l'ETA del battito parte da qui.

        ``seconds`` e' il totale previsto per il lavoro; ``duration_sec`` e' la
        durata del montaggio, che :mod:`vx.estimate` sa convertire in secondi di
        render. Senza nessuna dichiarazione l'ETA resta quella dell'estimatore.
        """
        with self._lock:
            try:
                self._eta_total = float(seconds) if seconds is not None else None
            except (TypeError, ValueError):
                self._eta_total = None
            try:
                self._duration_sec = (
                    float(duration_sec) if duration_sec is not None else None
                )
            except (TypeError, ValueError):
                self._duration_sec = None

    def _eta_sec(self, stage_elapsed: float) -> float:
        """Quanto manca secondo :mod:`vx.estimate`: mai un numero inventato qui."""
        total = self._eta_total
        if total is None:
            try:
                from . import estimate as estimate_mod

                predicted = estimate_mod.estimate(
                    {"duration_sec": float(self._duration_sec or 0.0)}
                ).get("predicted_seconds")
                total = float(predicted or 0.0)
            except Exception:  # noqa: BLE001 - la stima e' un di piu', non un requisito
                total = 0.0
        return max(0.0, round(float(total) - float(stage_elapsed), 3))

    def waiting_on(self) -> List[str]:
        """I cancelli aperti: la sessione e' ferma aspettando una risposta umana."""
        return [name for name, is_open in self._gate_state.items() if is_open]

    def tick(self) -> Optional[Dict[str, Any]]:
        """Un battito se lo stadio tace da troppo; ``None`` se non serve.

        Regola: al massimo :data:`HEARTBEAT_SEC` di silenzio durante uno stadio.
        Se un cancello e' aperto non si batte — la pausa e' voluta e l'evento
        ``gate`` l'ha gia' detta. Con un ``clock`` iniettato questo metodo e' la
        sola sorgente di battiti: il thread non parte.
        """
        with self._lock:
            if not self._active or self._ended:
                return None
            try:
                moment = max(0.0, float(self._clock()) - self._started)
            except Exception:  # noqa: BLE001
                return None
            if moment - self._last_t < self._beat_at:
                return None
            waiting = self.waiting_on()
            if waiting:
                return None
            stage = str(self._stage.get("stage") or "")
            if not stage:
                # Nessuno stadio dichiarato: non c'e' un lavoro da rassicurare.
                return None
            stage_elapsed = max(0.0, moment - self._stage_started)
            detail = str(self._stage.get("detail") or "")
            return self._event(
                "heartbeat",
                stage=stage,
                detail=detail or "lavoro in corso",
                elapsed_sec=round(stage_elapsed, 3),
                eta_sec=self._eta_sec(stage_elapsed),
                percent=self._stage.get("percent"),
            )

    def start_heartbeat(self) -> "Session":
        """Avvia il battito automatico, una volta sola, senza mai sollevare."""
        try:
            if not self._active or not self._real_clock or not heartbeats_enabled():
                return self
            with self._lock:
                if self._beat_thread is not None:
                    return self
                self._beat_stop.clear()
                thread = threading.Thread(
                    target=self._beat_loop,
                    name=f"vx-heartbeat-{self.id or 'session'}",
                    daemon=True,
                )
                self._beat_thread = thread
            thread.start()
        except Exception as exc:  # noqa: BLE001 - la vista perde il battito, non il lavoro
            self._soft_fail(exc)
        return self

    def _beat_loop(self) -> None:
        """Batte finche' la sessione vive: ogni giro costa un ``tick`` muto."""
        while not self._beat_stop.wait(min(0.25, self._beat_at)):
            if not self._active or self._ended:
                return
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                return

    def stop_heartbeat(self) -> None:
        """Ferma e aspetta il thread: dopo ``end`` non deve restare nessuno."""
        self._beat_stop.set()
        with self._lock:
            thread = self._beat_thread
            self._beat_thread = None
        if thread is not None and thread is not threading.current_thread():
            try:
                thread.join(timeout=1.0)
            except Exception:  # noqa: BLE001
                pass

    def stage(
        self,
        stage: str,
        percent: Optional[float] = None,
        detail: str = "",
        **extra: Any,
    ) -> Optional[Dict[str, Any]]:
        return self.event(
            "stage",
            stage=str(stage),
            percent=percent,
            detail=str(detail or ""),
            **extra,
        )

    def artifact(
        self, kind: str, path: Any, bytes: Optional[int] = None, **extra: Any
    ) -> Optional[Dict[str, Any]]:
        size = bytes
        if size is None:
            try:
                candidate = Path(str(path))
                size = candidate.stat().st_size if candidate.is_file() else None
            except OSError:
                size = None
        return self.event(
            "artifact", kind=str(kind), path=str(path), bytes=size, **extra
        )

    def decision(self, id: str, choice: Any, **extra: Any) -> Optional[Dict[str, Any]]:
        return self.event("decision", id=str(id), choice=str(choice or ""), **extra)

    def gate(self, gate: str, *, open: bool, **extra: Any) -> Optional[Dict[str, Any]]:
        return self.event("gate", gate=str(gate), open=bool(open), **extra)

    def error(self, message: Any, **extra: Any) -> Optional[Dict[str, Any]]:
        return self.event("error", message=str(message), **extra)

    def journal(
        self, line: Any, phase: str = "", **extra: Any
    ) -> Optional[Dict[str, Any]]:
        text = str(line or "").strip()
        if not text:
            return None
        return self.event("journal", line=text, phase=str(phase or ""), **extra)

    def live(
        self,
        ready: int,
        total: int,
        directory: Any = "",
        *,
        chunks: Optional[Iterable[Any]] = None,
        **extra: Any,
    ) -> Optional[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for item in chunks or []:
            if not isinstance(item, dict):
                continue
            try:
                number = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            entries.append(
                {
                    "index": number,
                    "t_in": item.get("t_in"),
                    "t_out": item.get("t_out"),
                    "ready": bool(item.get("ready")),
                }
            )
        return self.event(
            "live",
            ready=int(ready),
            total=int(total),
            directory=str(directory or ""),
            chunks=entries,
            **extra,
        )

    def end(self, status: str = "done") -> Optional[Dict[str, Any]]:
        if not self._active or self._ended:
            self.stop_heartbeat()
            return None
        row = self.event("end", status=str(status or "done"))
        self.status = str(status or "done")
        self._ended = True
        self.stop_heartbeat()
        return row

    # -- snapshot -------------------------------------------------------- #

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "id": self.id,
            "kind": self.kind,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "job": self.job,
            "job_path": self.job_path,
            "status": self.status,
            "started_utc": self.started_utc,
            "updated_utc": _utc_now() if self._active else "",
            "t": round(self._last_t, 3),
            "event_count": len(self.events),
            "counts": dict(self._counts),
            "stage": dict(self._stage),
            "stages": [dict(item) for item in self._stages],
            "artifacts": [dict(item) for item in self._artifacts],
            "decisions": [dict(item) for item in self._decisions],
            "gates": [dict(item) for item in self._gates],
            "gate_state": dict(self._gate_state),
            "errors": [dict(item) for item in self._errors],
            "journal": [dict(item) for item in self._journal],
            "live": {
                **{
                    key: value
                    for key, value in self._live.items()
                    if key != "chunks"
                },
                "chunks": [
                    dict(item) for item in self._live.get("chunks") or []
                ],
            },
            "events_path": str(self.path or ""),
            "rotated": list(self._rotated),
            "heartbeat": {
                "interval_sec": round(self._beat, 3),
                "max_silence_sec": HEARTBEAT_SEC,
                "count": int(self._beats),
                "last": dict(self._last_heartbeat),
            },
        }

    # -- interni --------------------------------------------------------- #

    def _ingest_row(self, row: Dict[str, Any]) -> None:
        self.events.append(dict(row))
        self._last_t = max(self._last_t, float(row.get("t") or 0.0))
        self._apply(row)

    def _append(self, data: bytes) -> None:
        path = self.path
        assert path is not None
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size and size + len(data) > self.max_bytes:
            self._rotate(path)
        with path.open("ab") as handle:
            handle.write(data)
            handle.flush()

    def _rotate(self, path: Path) -> None:
        assert self.directory is not None
        # La prima ruotata e' events-002.jsonl (il corrente e' il numero 1).
        index = 2
        while (self.directory / f"events-{index:03d}.jsonl").exists():
            index += 1
        target = self.directory / f"events-{index:03d}.jsonl"
        os.replace(path, target)
        self._rotated.append(str(target))

    def _apply(self, row: Dict[str, Any]) -> None:
        kind = str(row.get("type") or "")
        self._counts[kind] = self._counts.get(kind, 0) + 1
        if kind == "start":
            self.kind = self.kind or str(row.get("kind") or "")
            if row.get("argv") is not None:
                self.argv = [str(item) for item in row.get("argv") or []]
            if row.get("cwd"):
                self.cwd = str(row.get("cwd"))
        elif kind == "stage":
            previous = str(self._stage.get("stage") or "")
            entry = {
                "t": row.get("t"),
                "stage": str(row.get("stage") or ""),
                "percent": row.get("percent"),
                "detail": str(row.get("detail") or ""),
            }
            self._stage = entry
            # Un aggiornamento dello stesso stadio non sposta l'inizio:
            # `elapsed_sec` conta dal primo ingresso nello stadio.
            if entry["stage"] != previous:
                self._stage_started = float(row.get("t") or 0.0)
            self._stages.append(entry)
            if len(self._stages) > 2000:
                del self._stages[: len(self._stages) - 2000]
        elif kind == "artifact":
            entry = {
                "t": row.get("t"),
                "kind": str(row.get("kind") or ""),
                "path": str(row.get("path") or ""),
                "bytes": row.get("bytes"),
            }
            self._artifacts.append(entry)
            if len(self._artifacts) > 5000:
                del self._artifacts[: len(self._artifacts) - 5000]
        elif kind == "decision":
            self._decisions.append(
                {
                    "t": row.get("t"),
                    "id": str(row.get("id") or ""),
                    "choice": str(row.get("choice") or ""),
                    "by": str(row.get("by") or ""),
                }
            )
            if len(self._decisions) > 500:
                del self._decisions[: len(self._decisions) - 500]
        elif kind == "gate":
            entry = {
                "t": row.get("t"),
                "gate": str(row.get("gate") or ""),
                "open": bool(row.get("open")),
                "detail": str(row.get("detail") or ""),
                "chosen": str(row.get("chosen") or ""),
                "by": str(row.get("by") or ""),
            }
            self._gates.append(entry)
            self._gate_state[entry["gate"]] = entry["open"]
            if len(self._gates) > 500:
                del self._gates[: len(self._gates) - 500]
        elif kind == "error":
            self._errors.append(
                {"t": row.get("t"), "message": str(row.get("message") or "")}
            )
        elif kind == "journal":
            self._journal.append(
                {
                    "t": row.get("t"),
                    "line": str(row.get("line") or ""),
                    "phase": str(row.get("phase") or ""),
                }
            )
            if len(self._journal) > 500:
                del self._journal[: len(self._journal) - 500]
        elif kind == "live":
            chunks = row.get("chunks")
            self._live = {
                "ready": int(row.get("ready") or 0),
                "total": int(row.get("total") or 0),
                "directory": str(row.get("directory") or ""),
                "chunks": [
                    dict(item)
                    for item in (chunks if isinstance(chunks, list) else [])
                    if isinstance(item, dict)
                ],
            }
        elif kind == "heartbeat":
            self._beats = max(self._beats, int(self._counts.get("heartbeat") or 0))
            self._last_heartbeat = {
                "t": row.get("t"),
                "stage": str(row.get("stage") or ""),
                "elapsed_sec": row.get("elapsed_sec"),
                "eta_sec": row.get("eta_sec"),
            }
        elif kind == "end":
            self.status = str(row.get("status") or "done")
            self._ended = True
        if row.get("job"):
            self.job = str(row.get("job"))
        if row.get("job_path"):
            self.job_path = str(row.get("job_path"))

    def _merge_state(self, state: Dict[str, Any]) -> None:
        """Completa la replay con lo snapshot, senza mai inventare eventi."""
        if not isinstance(state, dict):
            return
        for key in ("id", "kind", "cwd", "job", "job_path", "status", "started_utc"):
            if state.get(key):
                setattr(self, key, str(state[key]))
        if isinstance(state.get("argv"), list):
            self.argv = [str(item) for item in state["argv"]]
        for attr, key in (
            ("_artifacts", "artifacts"),
            ("_decisions", "decisions"),
            ("_gates", "gates"),
            ("_journal", "journal"),
            ("_errors", "errors"),
        ):
            if not getattr(self, attr) and isinstance(state.get(key), list):
                setattr(
                    self,
                    attr,
                    [dict(item) for item in state[key] if isinstance(item, dict)],
                )
        if not self._stage and isinstance(state.get("stage"), dict):
            self._stage = dict(state["stage"])
        if (
            isinstance(state.get("live"), dict)
            and not self._live.get("chunks")
            and not self._live.get("total")
        ):
            live = state["live"]
            self._live = {
                "ready": int(live.get("ready") or 0),
                "total": int(live.get("total") or 0),
                "directory": str(live.get("directory") or ""),
                "chunks": [
                    dict(item)
                    for item in (live.get("chunks") or [])
                    if isinstance(item, dict)
                ],
            }

    def _write_state(self) -> None:
        if self.directory is None:
            return
        with self._lock:
            payload = json.dumps(
                self.as_dict(), ensure_ascii=False, indent=2, default=str
            ) + "\n"
            _atomic_write(self.directory / STATE_NAME, payload.encode("utf-8"))


def begin(
    kind: str,
    argv: Optional[Sequence[Any]] = None,
    cwd: Optional[Any] = None,
    **kwargs: Any,
) -> Session:
    """Scorciatoia per :meth:`Session.begin`."""
    return Session.begin(kind, argv, cwd, **kwargs)


def prune_sessions(
    *,
    root: Optional[Any] = None,
    days: float = 7.0,
    dry_run: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """Toglie le sessioni piu' vecchie di ``days`` giorni, dichiarando cosa fa.

    Le prove ne lasciano centinaia (492 al 6 novembre 2026) e nessuno le guarda
    piu'. Le regole sono quelle di un file system condiviso con l'utente:

    - la radice **vera** (``~/.vx/session``) si tocca solo con ``--force``,
      perche' li' ci sono le sessioni che la vista mostra;
    - una cartella senza ``state.json`` ne' ``events*.jsonl`` non e' una
      sessione e resta dov'e' (potrebbe essere altro);
    - ``dry_run`` non cancella niente e dice cosa cancellerebbe;
    - l'eta' e' quella del file piu' recente dentro la sessione.
    """
    base = Path(root) if root is not None else default_root()
    target = base.resolve() if base.exists() else base
    real = default_root().resolve() if default_root().exists() else default_root()
    if target == real and not force:
        raise contract.VxError(
            "session prune: questa e' la radice vera delle sessioni "
            f"({real}): serve --force, oppure --root su una cartella di prova"
        )
    cutoff = time.time() - max(0.0, float(days)) * 86400.0
    removed: List[str] = []
    kept = 0
    foreign = 0
    freed = 0
    if not base.is_dir():
        # Una cartella che non c'e' non e' un errore: non c'e' niente da potare.
        return {
            "root": str(base),
            "days": float(days),
            "dry_run": bool(dry_run),
            "removed": [],
            "removed_count": 0,
            "kept_count": 0,
            "foreign_count": 0,
            "freed_bytes": 0,
        }
    try:
        folders = sorted(path for path in base.iterdir() if path.is_dir())
    except OSError as exc:
        raise contract.VxError(f"session prune: cartella non leggibile: {exc}") from exc
    for folder in folders:
        if not (_event_files(folder) or (folder / STATE_NAME).is_file()):
            foreign += 1
            continue
        try:
            stamp = max(
                (path.stat().st_mtime for path in folder.rglob("*") if path.is_file()),
                default=0.0,
            )
        except OSError:
            stamp = 0.0
        if stamp >= cutoff:
            kept += 1
            continue
        size = sum(
            path.stat().st_size for path in folder.rglob("*") if path.is_file()
        ) if not dry_run else 0
        removed.append(folder.name)
        if not dry_run:
            shutil.rmtree(folder, ignore_errors=True)
        freed += size
    return {
        "root": str(base),
        "days": float(days),
        "dry_run": bool(dry_run),
        "removed": removed,
        "removed_count": len(removed),
        "kept_count": kept,
        "foreign_count": foreign,
        "freed_bytes": freed,
    }


def list_sessions(*, root: Optional[Any] = None) -> List[str]:
    """Gli id delle sessioni, dalla piu' recente alla piu' vecchia."""
    base = Path(root) if root is not None else default_root()
    entries: List[tuple] = []
    try:
        folders = [path for path in base.iterdir() if path.is_dir()]
    except OSError:
        return []
    for folder in folders:
        state = folder / STATE_NAME
        events = folder / EVENTS_NAME
        try:
            stamp = max(
                state.stat().st_mtime if state.is_file() else 0.0,
                events.stat().st_mtime if events.is_file() else 0.0,
            )
        except OSError:
            continue
        if stamp <= 0.0:
            continue
        entries.append((stamp, folder.name))
    return [name for _, name in sorted(entries, reverse=True)]


def latest(*, root: Optional[Any] = None) -> str:
    ids = list_sessions(root=root)
    return ids[0] if ids else ""


def load(
    session_id: Optional[Any] = None, *, root: Optional[Any] = None
) -> Optional[Session]:
    return Session.load(session_id, root=root)


__all__ = [
    "ENV_DIR",
    "ENV_ENABLED",
    "ENV_HEARTBEAT",
    "EVENTS_NAME",
    "HEARTBEAT_SEC",
    "MAX_EVENT_BYTES",
    "SCHEMA",
    "STATE_NAME",
    "Session",
    "begin",
    "default_root",
    "enabled",
    "heartbeats_enabled",
    "latest",
    "list_sessions",
    "load",
]
