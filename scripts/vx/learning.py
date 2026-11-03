"""vx.learning — l'archivio leggero su cui la skill impara dai giudizi.

Il registro dei voti (``vx.feedback``) dice *che* un lavoro e' piaciuto; non
dice *perche'*. Questo modulo conserva il minimo che serve a rispondere: il
piano in forma di testo, le misure che il lavoro aveva gia' preso, al massimo
tre fotogrammi a 320 px, e le soglie che quei dati hanno spostato.

Mai il video. Non entra qui nessun ``master.mp4``, nessun chunk, nessun proxy:
il peso e' un vincolo dichiarato, non un dettaglio. L'archivio intero sta in
20 MB e, se li supera, invecchia da solo — i mesi di eventi si comprimono e i
corpus piu' vecchi si potano, tenendo sempre i piu' recenti e tutti quelli che
una policy cita.

Layout sotto ``~/.vx/learning`` (o ``$VX_LEARNING_DIR``)::

    events/2026-11.jsonl            eventi append-only, uno per riga, per mese
    archive/2026-10.jsonl.zst       mesi vecchi compressi (zstd, altrimenti gzip)
    corpus/<job_id>/plan.json       il piano, testo
    corpus/<job_id>/metrics.json    coerenza, verifica, guard, stima, voto
    corpus/<job_id>/thumb_0..2.jpg  tre fotogrammi a 320 px, non di piu'
    policy/policy.json              soglie versionate
    policy/history/vNNN.json        diff, motivo e hash dei feedback citati

Regole di scrittura, prese da ``vx.events`` e ``vx.feedback``: una riga per
evento, ``flush`` + ``fsync`` sotto lock di file (e di thread), nessuna riga
riscritta. Un JSON che si legge per intero anche mentre lo si guarda si scrive
su un temporaneo e si sostituisce con ``os.replace``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple, Union

SCHEMA_EVENT = "vx.learning.event/1"
SCHEMA_PLAN = "vx.learning.plan/1"
SCHEMA_METRICS = "vx.learning.metrics/1"
SCHEMA_POLICY = "vx.learning.policy/1"
SCHEMA_POLICY_HISTORY = "vx.learning.policy-history/1"

#: Dove vive l'archivio: override esplicito, ambiente, ``~/.vx/learning``.
ROOT_ENV = "VX_LEARNING_DIR"
DEFAULT_BUDGET_MB = 20.0

#: Il tetto di peso di un singolo corpus: un piano e tre fotogrammi non
#: arrivano qui, e se ci arrivano e' un segnale che qualcosa di grosso e'
#: finito dentro per sbaglio.
MAX_CORPUS_BYTES = 512 * 1024

#: I fotogrammi: tre, a 320 px di lato lungo, JPEG.
MAX_THUMBS = 3
THUMB_MAX_PX = 320
MAX_THUMB_BYTES = 256 * 1024
THUMB_SUFFIXES = (".jpg", ".jpeg")

EVENTS_DIR = "events"
ARCHIVE_DIR = "archive"
CORPUS_DIR = "corpus"
POLICY_DIR = "policy"
HISTORY_DIR = "history"
POLICY_NAME = "policy.json"

#: I file di lock non sono dati e non si contano nel budget.
LOCK_NAME = "learning.lock"
EVENTS_LOCK = "events.lock"
POLICY_LOCK = "policy.lock"
CORPUS_LOCK = "corpus.lock"

#: Le soglie di partenza: un archivio vuoto ha comunque una policy leggibile.
DEFAULT_THRESHOLDS: Dict[str, Any] = {
    "variety_min": 0.55,
    "cut_rate_min": 0.18,
    "cut_rate_max": 0.75,
    "effects_density_max": 0.35,
}

META_KEYS = (
    "schema",
    "version",
    "previous_version",
    "updated_utc",
    "reason",
    "feedback_ids",
    "feedback_digest",
)

_JOB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

_THREAD_LOCK = threading.Lock()

_Path = Union[str, Path]


# --------------------------------------------------------------------------- #
# dove sta l'archivio
# --------------------------------------------------------------------------- #


def root_dir(root: Optional[Any] = None) -> Path:
    """La radice: argomento, ``VX_LEARNING_DIR``, ``~/.vx/learning``."""
    if root:
        return Path(root)
    env = str(os.environ.get(ROOT_ENV) or "").strip()
    if env:
        return Path(env)
    return Path.home() / ".vx" / "learning"


def events_dir(root: Optional[Any] = None) -> Path:
    return root_dir(root) / EVENTS_DIR


def archive_dir(root: Optional[Any] = None) -> Path:
    return root_dir(root) / ARCHIVE_DIR


def corpus_dir(root: Optional[Any] = None) -> Path:
    return root_dir(root) / CORPUS_DIR


def policy_dir(root: Optional[Any] = None) -> Path:
    return root_dir(root) / POLICY_DIR


def policy_path(root: Optional[Any] = None) -> Path:
    return policy_dir(root) / POLICY_NAME


def history_dir(root: Optional[Any] = None) -> Path:
    return policy_dir(root) / HISTORY_DIR


# --------------------------------------------------------------------------- #
# tempo, json, lock
# --------------------------------------------------------------------------- #


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _as_datetime(value: Any) -> Optional[datetime]:
    """Un datetime (naive = UTC) o una stringa ISO; ``None`` se non si legge."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _read_json(path: _Path) -> Dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(data) if isinstance(data, Mapping) else {}


def _write_json_atomic(path: _Path, payload: Mapping[str, Any]) -> Path:
    """Un JSON leggibile per intero anche mentre un altro processo lo guarda."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    handle = tempfile.NamedTemporaryFile(
        "w",
        dir=str(target.parent),
        prefix=target.name + ".",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
        newline="\n",
    )
    tmp = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def _lock_handle(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_handle(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _locked(lock_path: _Path) -> Iterator[None]:
    """Lock di thread piu' lock di file: due thread e due processi in fila.

    Se il file system non sa fare il lock si perde solo la garanzia fra
    processi: la riga e' gia' formata e il lock di thread la tiene intera.
    """
    target = Path(lock_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK:
        handle = open(target, "a+b")
        locked = False
        try:
            try:
                _lock_handle(handle)
                locked = True
            except OSError:
                locked = False
            yield
        finally:
            if locked:
                try:
                    _unlock_handle(handle)
                except OSError:
                    pass
            handle.close()


# --------------------------------------------------------------------------- #
# eventi: append-only, ruotati per mese
# --------------------------------------------------------------------------- #


def _month(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m")


def _torn(path: Path) -> bool:
    """L'ultima riga e' senza newline: un crash l'ha lasciata a meta'."""
    if not path.is_file() or not path.stat().st_size:
        return False
    with path.open("rb") as raw:
        raw.seek(-1, 2)
        return raw.read(1) != b"\n"


def append_event(
    kind: str,
    payload: Optional[Mapping[str, Any]] = None,
    *,
    job_id: Optional[str] = None,
    root: Optional[Any] = None,
    when: Optional[Any] = None,
) -> Dict[str, Any]:
    """Appende **un** evento e lo restituisce: una riga, un ``fsync``.

    Il file e' ``events/YYYY-MM.jsonl`` scelto dall'UTC (``when`` serve solo a
    riempire un mese passato o a provare la rotazione). Non si riscrive mai il
    file: la riga si aggiunge sotto lock, cosi' venti thread che registrano
    insieme non si perdono un evento a vicenda.
    """
    label = str(kind or "").strip()
    if not label:
        raise ValueError("append_event: kind non puo' essere vuoto")
    if payload is None:
        body: Dict[str, Any] = {}
    elif isinstance(payload, Mapping):
        body = dict(payload)
    else:
        raise TypeError("append_event: payload deve essere un oggetto JSON")
    moment = _as_datetime(when) or _now()
    base = root_dir(root)
    month = _month(moment)
    record: Dict[str, Any] = {
        "schema": SCHEMA_EVENT,
        "ts": _iso(moment),
        "kind": label,
        "job_id": str(job_id or ""),
        "payload": body,
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
    target = events_dir(base) / f"{month}.jsonl"
    with _locked(base / EVENTS_LOCK):
        target.parent.mkdir(parents=True, exist_ok=True)
        torn = _torn(target)
        with open(target, "a", encoding="utf-8", newline="\n") as handle:
            if torn:
                # Chiude la riga mutilata: il nuovo evento non si incolla a lei.
                handle.write("\n")
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    return record


def _lines(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in text.splitlines():
        text_line = raw.strip()
        if not text_line:
            continue
        try:
            data = json.loads(text_line)
        except ValueError:
            continue  # riga troncata da un crash: non e' un evento
        if isinstance(data, dict):
            out.append(data)
    return out


def read_events(
    month: Optional[str] = None,
    *,
    root: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Gli eventi in ordine di tempo; ``month="2026-11"`` filtra un mese.

    Legge sia i mesi vivi sia quelli archiviati (decompressione al volo), cosi'
    filtrare per mese non cambia risposta dopo una potatura.
    """
    base = root_dir(root)
    wanted = str(month or "").strip()
    if wanted and not re.fullmatch(r"\d{4}-\d{2}", wanted):
        raise ValueError(f"read_events: mese non valido {month!r} (atteso YYYY-MM)")
    records: List[Dict[str, Any]] = []
    for path in sorted(events_dir(base).glob("*.jsonl")):
        if wanted and path.stem != wanted:
            continue
        try:
            records.extend(_lines(path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    for path in sorted(archive_dir(base).glob("*.jsonl.*")):
        stem = path.name.split(".")[0]
        if wanted and stem != wanted:
            continue
        data = _decompress(path)
        if data is None:
            continue
        records.extend(_lines(data.decode("utf-8", errors="replace")))
    records.sort(key=lambda item: str(item.get("ts") or ""))
    return records


# --------------------------------------------------------------------------- #
# compressione: zstd se c'e', altrimenti gzip
# --------------------------------------------------------------------------- #


def _zstd_module() -> Any:
    try:  # pragma: no cover - dipende dall'ambiente, non dal codice
        import zstandard  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    return zstandard


def _zstd_exe() -> Optional[str]:
    return shutil.which("zstd")


def _zstd_compress(data: bytes) -> Optional[bytes]:
    module = _zstd_module()
    if module is not None:
        try:
            return module.ZstdCompressor(level=19).compress(data)
        except Exception:  # noqa: BLE001
            return None
    exe = _zstd_exe()
    if not exe:
        return None
    try:
        done = subprocess.run(
            [exe, "-q", "-19", "-c"],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    return done.stdout if done.returncode == 0 and done.stdout else None


def _zstd_decompress(data: bytes) -> Optional[bytes]:
    module = _zstd_module()
    if module is not None:
        try:
            return module.ZstdDecompressor().decompress(data)
        except Exception:  # noqa: BLE001
            return None
    exe = _zstd_exe()
    if not exe:
        return None
    try:
        done = subprocess.run(
            [exe, "-q", "-d", "-c"],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def compress_bytes(data: bytes) -> Tuple[bytes, str]:
    """``(bytes compressi, estensione)``: zstd quando c'e', gzip altrimenti."""
    compressed = _zstd_compress(data)
    if compressed is not None:
        return compressed, "zst"
    return gzip.compress(data, compresslevel=9, mtime=0), "gz"


def _decompress(path: Path) -> Optional[bytes]:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if path.suffix == ".gz":
        try:
            return gzip.decompress(raw)
        except OSError:
            return None
    if path.suffix == ".zst":
        return _zstd_decompress(raw)
    return None


def archive_events(
    *,
    root: Optional[Any] = None,
    keep_month: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Comprime i mesi di eventi vecchi in ``archive/`` e toglie l'originale.

    Il mese corrente resta vivo (ci si scrive sopra): la soglia e' l'UTC, o
    ``keep_month`` quando serve a provare la rotazione. Se la compressione non
    riesce il file resta dov'e': meglio pesante che perso.
    """
    base = root_dir(root)
    keep = str(keep_month or "").strip() or _month(_now())
    done: List[Dict[str, Any]] = []
    for path in sorted(events_dir(base).glob("*.jsonl")):
        if path.stem >= keep:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        blob, suffix = compress_bytes(data)
        target = archive_dir(base) / f"{path.stem}.jsonl.{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, target)
            path.unlink()
        except OSError:
            continue
        done.append(
            {
                "month": path.stem,
                "path": str(target),
                "bytes_before": len(data),
                "bytes_after": len(blob),
            }
        )
    return done


# --------------------------------------------------------------------------- #
# corpus: piano + misure + al massimo tre fotogrammi
# --------------------------------------------------------------------------- #


def _safe_job_id(job_id: Any) -> str:
    text = str(job_id or "").strip()
    if not text or text in (".", "..") or not _JOB_RE.match(text):
        raise ValueError(
            f"job_id non valido: {job_id!r} (lettere, cifre, '.', '_' e '-', fino a 80)"
        )
    return text


def jpeg_size(data: bytes) -> Optional[Tuple[int, int]]:
    """``(larghezza, altezza)`` dai marcatori SOF, senza dipendenze esterne."""
    if not data.startswith(b"\xff\xd8"):
        return None
    size = len(data)
    index = 2
    while index + 3 < size:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker == 0xFF:
            index += 1
            continue
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            index += 2
            continue
        if index + 4 > size:
            return None
        length = struct.unpack(">H", data[index + 2 : index + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if index + 9 > size:
                return None
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return int(width), int(height)
        if length < 2:
            return None
        index += 2 + length
    return None


def _thumb_path(item: Any) -> Path:
    if isinstance(item, Mapping):
        raw = item.get("path") or item.get("file") or ""
    else:
        raw = item
    text = str(raw or "").strip()
    if not text:
        raise ValueError("thumb senza percorso")
    return Path(text)


def _check_thumbs(thumbs: Optional[Sequence[Any]]) -> List[Tuple[Path, bytes, Tuple[int, int]]]:
    """Valida i fotogrammi *prima* di scrivere: o entrano tutti, o nessuno.

    Rifiuta piu' di tre file, un'estensione che non e' JPEG, un file che non
    e' un JPEG leggibile, un lato sopra i 320 px e un file sopra 256 KB: un
    archivio che deve stare in 20 MB non accetta "piu' o meno" 320 px.
    """
    items = list(thumbs or [])
    if len(items) > MAX_THUMBS:
        raise ValueError(
            f"troppi fotogrammi: {len(items)} (massimo {MAX_THUMBS} per lavoro bocciato)"
        )
    checked: List[Tuple[Path, bytes, Tuple[int, int]]] = []
    for item in items:
        path = _thumb_path(item)
        if path.suffix.lower() not in THUMB_SUFFIXES:
            raise ValueError(f"fotogramma non JPEG: {path.name} (ammessi .jpg/.jpeg)")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"fotogramma illeggibile: {path} ({exc})") from exc
        if len(data) > MAX_THUMB_BYTES:
            raise ValueError(
                f"fotogramma troppo pesante: {path.name} {len(data)} B (massimo {MAX_THUMB_BYTES} B)"
            )
        dimensions = jpeg_size(data)
        if dimensions is None:
            raise ValueError(f"fotogramma non leggibile come JPEG: {path.name}")
        width, height = dimensions
        if width > THUMB_MAX_PX or height > THUMB_MAX_PX:
            raise ValueError(
                f"fotogramma troppo grande: {path.name} {width}x{height} "
                f"(massimo {THUMB_MAX_PX} px per lato)"
            )
        if width <= 0 or height <= 0:
            raise ValueError(f"fotogramma senza dimensioni: {path.name}")
        checked.append((path, data, (width, height)))
    return checked


def _plan_payload(plan: Any) -> Dict[str, Any]:
    if isinstance(plan, Mapping):
        return dict(plan)
    if isinstance(plan, str):
        return {"text": plan}
    raise TypeError("store_failure: plan deve essere un testo o un oggetto JSON")


def _metrics_payload(metrics: Any) -> Dict[str, Any]:
    if metrics is None:
        return {}
    if isinstance(metrics, Mapping):
        return dict(metrics)
    if isinstance(metrics, str):
        return {"text": metrics}
    raise TypeError("store_failure: metrics deve essere un oggetto JSON o un testo")


def store_failure(
    job_id: Any,
    plan: Any,
    metrics: Any,
    thumbs: Optional[Sequence[Any]] = None,
    *,
    root: Optional[Any] = None,
    captured_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Scrive ``corpus/<job_id>/`` con piano, misure e fino a tre fotogrammi.

    Tutto viene validato prima di toccare il disco e poi montato in una
    cartella temporanea: un lavoro che si registra due volte non lascia il
    corpus vecchio mescolato al nuovo. Nessun file video entra qui: i
    fotogrammi sono JPEG a 320 px o il lavoro non si archivia.
    """
    job = _safe_job_id(job_id)
    body = _plan_payload(plan)
    measures = _metrics_payload(metrics)
    checked = _check_thumbs(thumbs)
    stamp = str(captured_utc or "").strip() or _iso(_now())

    base = root_dir(root)
    target = corpus_dir(base) / job
    plan_record = {
        "schema": SCHEMA_PLAN,
        "job_id": job,
        "captured_utc": stamp,
        "plan": body,
    }
    metrics_record = {
        "schema": SCHEMA_METRICS,
        "job_id": job,
        "captured_utc": stamp,
        "metrics": measures,
    }
    size = len(json.dumps(plan_record, ensure_ascii=False).encode("utf-8"))
    size += len(json.dumps(metrics_record, ensure_ascii=False).encode("utf-8"))
    size += sum(len(data) for _, data, _ in checked)
    if size > MAX_CORPUS_BYTES:
        raise ValueError(
            f"corpus troppo pesante: {size} B (massimo {MAX_CORPUS_BYTES} B): "
            "il piano va ridotto, non il video archiviato"
        )

    with _locked(base / CORPUS_LOCK):
        parent = corpus_dir(base)
        parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(dir=str(parent), prefix=f".{job}.staging-"))
        try:
            _write_json_atomic(staging / "plan.json", plan_record)
            _write_json_atomic(staging / "metrics.json", metrics_record)
            for index, (origin, data, _dimensions) in enumerate(checked):
                (staging / f"thumb_{index}{origin.suffix.lower()}").write_bytes(data)
            if target.exists():
                shutil.rmtree(target)
            os.replace(staging, target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    files = sorted(path.name for path in target.iterdir() if path.is_file())
    written = {
        "schema": "vx.learning.corpus/1",
        "job_id": job,
        "path": str(target),
        "files": files,
        "thumbs": len(checked),
        "bytes": _dir_bytes(target),
        "captured_utc": stamp,
    }
    append_event(
        "failure",
        {
            "files": files,
            "thumbs": len(checked),
            "bytes": written["bytes"],
            "outcome": measures.get("outcome", ""),
            "rating": measures.get("rating", measures.get("vote", "")),
        },
        job_id=job,
        root=base,
    )
    return written


# --------------------------------------------------------------------------- #
# policy: soglie versionate, con il motivo e l'hash dei feedback citati
# --------------------------------------------------------------------------- #


def _default_policy() -> Dict[str, Any]:
    return {
        "schema": SCHEMA_POLICY,
        "version": 0,
        "updated_utc": "",
        "reason": "",
        "feedback_ids": [],
        "feedback_digest": "",
        "thresholds": dict(DEFAULT_THRESHOLDS),
    }


def _policy_load_at(base: Path) -> Dict[str, Any]:
    stored = _read_json(policy_path(base))
    if not stored:
        return _default_policy()
    merged = _default_policy()
    merged.update(stored)
    if not isinstance(merged.get("thresholds"), Mapping):
        merged["thresholds"] = dict(DEFAULT_THRESHOLDS)
    return merged


def policy_load(*, root: Optional[Any] = None) -> Dict[str, Any]:
    """La policy corrente; senza file, le soglie di partenza con ``version=0``."""
    return _policy_load_at(root_dir(root))


def _feedback_candidates(base: Path) -> List[Path]:
    out = [base / "feedback.jsonl", base.parent / "feedback.jsonl"]
    env = str(os.environ.get("VX_FEEDBACK_ROOT") or "").strip()
    if env:
        out.append(Path(env) / "feedback.jsonl")
    unique: List[Path] = []
    for path in out:
        if path not in unique:
            unique.append(path)
    return unique


def _feedback_hashes(ids: Sequence[str], base: Path) -> Dict[str, str]:
    """L'hash della riga di registro di ogni feedback citato, quando esiste.

    Il registro dei giudizi non si tocca (e' di ``vx.feedback``): lo si legge
    e basta. Un id che non si trova resta con hash vuoto, dichiarato.
    """
    wanted = set(ids)
    found: Dict[str, str] = {}
    if not wanted:
        return found
    for path in _feedback_candidates(base):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            text_line = line.strip()
            if not text_line:
                continue
            try:
                record = json.loads(text_line)
            except ValueError:
                continue
            if not isinstance(record, Mapping):
                continue
            for key in ("id", "feedback_id", "job_id"):
                value = str(record.get(key) or "")
                if value and value in wanted and value not in found:
                    found[value] = hashlib.sha256(text_line.encode("utf-8")).hexdigest()
    return found


def _diff(old: Any, new: Any) -> Dict[str, List[str]]:
    """Differenza strutturale in percorsi JSON: cambiato, aggiunto, tolto."""
    changed: List[str] = []
    added: List[str] = []
    removed: List[str] = []

    def walk(left: Any, right: Any, path: str) -> None:
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            for key in left:
                if key not in right:
                    removed.append(f"{path}.{key}" if path else str(key))
            for key in right:
                child = f"{path}.{key}" if path else str(key)
                if key not in left:
                    added.append(child)
                else:
                    walk(left[key], right[key], child)
            return
        if isinstance(left, list) and isinstance(right, list):
            shared = min(len(left), len(right))
            for index in range(shared):
                walk(left[index], right[index], f"{path}[{index}]")
            for index in range(shared, len(left)):
                removed.append(f"{path}[{index}]")
            for index in range(shared, len(right)):
                added.append(f"{path}[{index}]")
            return
        if left != right:
            changed.append(path or "$")

    walk(old, new, "")
    return {"changed": changed, "added": added, "removed": removed}


def _policy_body(policy: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in policy.items() if key not in META_KEYS}


def policy_save(
    policy: Mapping[str, Any],
    *,
    reason: str,
    feedback_ids: Optional[Sequence[Any]] = None,
    root: Optional[Any] = None,
) -> Dict[str, Any]:
    """Scrive la policy con versione +1 e ne conserva il diff in ``history``.

    ``reason`` e' obbligatorio: una soglia che cambia senza un motivo scritto
    e' indistinguibile da un errore. La versione precedente resta leggibile in
    ``policy/history/vNNN.json`` insieme al diff, al motivo e all'hash delle
    righe di feedback citate.
    """
    if not isinstance(policy, Mapping):
        raise TypeError("policy_save: policy deve essere un oggetto JSON")
    why = str(reason or "").strip()
    if not why:
        raise ValueError("policy_save: serve un motivo scritto (reason)")
    ids = [str(item) for item in (feedback_ids or [])]
    base = root_dir(root)
    with _locked(base / POLICY_LOCK):
        current = _policy_load_at(base)
        version = int(current.get("version") or 0) + 1
        moment = _now()
        body = _policy_body(policy)
        saved: Dict[str, Any] = {
            "schema": SCHEMA_POLICY,
            "version": version,
            "previous_version": version - 1,
            "updated_utc": _iso(moment),
            "reason": why,
            "feedback_ids": ids,
            "feedback_digest": _sha256(sorted(ids)),
        }
        saved.update(body)
        if not isinstance(saved.get("thresholds"), Mapping):
            saved["thresholds"] = dict(DEFAULT_THRESHOLDS)
        _write_json_atomic(policy_path(base), saved)
        history = {
            "schema": SCHEMA_POLICY_HISTORY,
            "version": version,
            "previous_version": version - 1,
            "created_utc": _iso(moment),
            "reason": why,
            "feedback_ids": ids,
            "feedback_digest": _sha256(sorted(ids)),
            "feedback_hashes": _feedback_hashes(ids, base),
            "diff": _diff(_policy_body(current), body),
            "policy": body,
        }
        _write_json_atomic(history_dir(base) / f"v{version:03d}.json", history)
    return saved


# --------------------------------------------------------------------------- #
# peso: stato, piano di potatura, potatura
# --------------------------------------------------------------------------- #


def _dir_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _data_files(base: Path) -> Iterator[Path]:
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if name.endswith(".lock") or name.endswith(".tmp") or ".staging-" in path.parts[-2:]:
            continue
        yield path


def _total_bytes(base: Path) -> int:
    total = 0
    for path in _data_files(base):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _corpus_stamp(path: Path) -> datetime:
    record = _read_json(path / "metrics.json")
    moment = _as_datetime(record.get("captured_utc"))
    if moment is None:
        moment = _as_datetime(_read_json(path / "plan.json").get("captured_utc"))
    if moment is None:
        try:
            moment = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            moment = _now()
    return moment


def _corpus_entries(base: Path) -> List[Tuple[datetime, Path]]:
    parent = corpus_dir(base)
    if not parent.is_dir():
        return []
    out: List[Tuple[datetime, Path]] = []
    for path in parent.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        out.append((_corpus_stamp(path), path))
    out.sort(key=lambda pair: (pair[0], pair[1].name))
    return out


def _cited_job_ids(base: Path, jobs: Sequence[str]) -> set:
    """I job che una policy (o la sua storia) nomina: quelli non si potano.

    Si guardano le chiavi esplicite e, per sicurezza, qualunque occorrenza del
    nome dentro i file di policy: un id citato in prosa e' pur sempre citato.
    """
    cited: set = set()
    documents: List[Dict[str, Any]] = []
    if policy_path(base).is_file():
        documents.append(_read_json(policy_path(base)))
    if history_dir(base).is_dir():
        for path in sorted(history_dir(base).glob("*.json")):
            documents.append(_read_json(path))
    explicit = ("corpus_refs", "job_ids", "cited_jobs", "jobs", "job_id", "corpus")
    for data in documents:
        for key in explicit:
            value = data.get(key)
            if isinstance(value, str) and value:
                cited.add(value)
            elif isinstance(value, (list, tuple, set)):
                cited.update(str(item) for item in value if item)
    blob = "\n".join(json.dumps(data, ensure_ascii=False, default=str) for data in documents)
    for job in jobs:
        if job and job in blob:
            cited.add(job)
    return cited & set(jobs)


def _prune_plan(
    base: Path,
    budget_bytes: int,
    *,
    protect: Optional[Sequence[str]] = None,
) -> List[Tuple[Path, int]]:
    """Cosa si toglierebbe, in ordine: prima gli archivi, poi i corpus vecchi.

    Il corpus piu' recente resta sempre, anche se da solo sfora il budget: la
    potatura non deve mai azzerare la memoria del lavoro appena fatto.
    """
    total = _total_bytes(base)
    if total <= budget_bytes:
        return []
    keep = set(protect or ())
    jobs = _corpus_entries(base)
    if jobs:
        keep.add(jobs[-1][1].name)
    cited = _cited_job_ids(base, [path.name for _stamp, path in jobs])
    keep |= cited
    plan: List[Tuple[Path, int]] = []

    archives = sorted(archive_dir(base).glob("*.jsonl.*"), key=lambda path: path.name)
    for path in archives:
        if total <= budget_bytes:
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total -= size
        plan.append((path, size))

    for _stamp, path in jobs:
        if total <= budget_bytes:
            break
        if path.name in keep:
            continue
        size = _dir_bytes(path)
        total -= size
        plan.append((path, size))
    return plan


def _oldest(base: Path) -> Optional[str]:
    stamps: List[datetime] = []
    for record in read_events(root=base):
        moment = _as_datetime(record.get("ts"))
        if moment is not None:
            stamps.append(moment)
    for stamp, _path in _corpus_entries(base):
        stamps.append(stamp)
    if not stamps:
        return None
    return min(stamps).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def status(*, root: Optional[Any] = None, budget_mb: float = DEFAULT_BUDGET_MB) -> Dict[str, Any]:
    """Il peso reale dell'archivio, area per area, e cosa si poterebbe dopo."""
    base = root_dir(root)
    areas = ("events", "archive", "corpus", "policy", "other")
    by_area: Dict[str, int] = {area: 0 for area in areas}
    total = 0
    for path in _data_files(base):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        try:
            first = path.relative_to(base).parts[0]
        except ValueError:
            first = "other"
        area = first if first in by_area else "other"
        by_area[area] += size
        total += size
    budget_bytes = max(0, int(float(budget_mb) * 1024 * 1024))
    plan = _prune_plan(base, budget_bytes)
    return {
        "root": str(base),
        "bytes": total,
        "budget_mb": float(budget_mb),
        "budget_bytes": budget_bytes,
        "over_budget": total > budget_bytes,
        "by_area": by_area,
        "events_count": len(read_events(root=base)),
        "corpus_count": len(_corpus_entries(base)),
        "archive_count": len(sorted(archive_dir(base).glob("*.jsonl.*"))),
        "oldest": _oldest(base),
        "will_prune_next": [
            str(path.relative_to(base)).replace("\\", "/") for path, _size in plan
        ],
        "next_prune_bytes": sum(size for _path, size in plan),
    }


def prune(
    *,
    budget_mb: float = DEFAULT_BUDGET_MB,
    root: Optional[Any] = None,
    keep_job_ids: Optional[Sequence[str]] = None,
    archive: bool = True,
) -> Dict[str, Any]:
    """Riporta l'archivio sotto budget: comprime i mesi, poi pota i corpus.

    Ordine: prima i mesi di eventi (che diventano archivio compresso), poi gli
    archivi piu' vecchi, poi i corpus piu' vecchi. Non si tocca la policy ne'
    il mese corrente, e non si tocca nessun corpus citato da una policy.
    """
    base = root_dir(root)
    with _locked(base / LOCK_NAME):
        budget_bytes = max(0, int(float(budget_mb) * 1024 * 1024))
        before = _total_bytes(base)
        archived: List[Dict[str, Any]] = []
        if archive:
            archived = archive_events(root=base)
        removed: List[Dict[str, Any]] = []
        freed = 0
        for path, size in _prune_plan(
            base, budget_bytes, protect=keep_job_ids
        ):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError:
                continue
            removed.append(
                {
                    "path": str(path.relative_to(base)).replace("\\", "/"),
                    "bytes": size,
                }
            )
            freed += size
        after = _total_bytes(base)
        cited = _cited_job_ids(base, [path.name for _stamp, path in _corpus_entries(base)])
        result = {
            "root": str(base),
            "budget_mb": float(budget_mb),
            "budget_bytes": budget_bytes,
            "bytes_before": before,
            "bytes_after": after,
            "freed_bytes": freed,
            "archived": archived,
            "removed": removed,
            "kept_cited": sorted(cited),
            "over_budget": after > budget_bytes,
        }
    if removed or archived:
        append_event(
            "prune",
            {
                "freed_bytes": freed,
                "removed": len(removed),
                "archived": len(archived),
                "bytes_after": after,
            },
            root=base,
        )
        # L'evento stesso pesa: il consuntivo dice il dopo vero, non il prima.
        after = _total_bytes(base)
        result["bytes_after"] = after
        result["over_budget"] = after > budget_bytes
    return result
