"""vx.serve — la UI locale: guardi i lavori e approvi i fotogrammi.

Un server stdlib su `127.0.0.1`. Con **un** lavoro si comporta come sempre; con
**piu'** lavori in parallelo ne scopre quanti sono sotto la cartella indicata
(fino a due livelli) e li espone tutti:

* ``/``                    la pagina, che chiede lo stato ogni secondo;
* ``/api/state?job=N``     lo stato: fase, percentuale, fotogrammi, approvazione,
                           il master, gli ``shots`` con sorgente ed effetti, il
                           registro ``decisions`` e la lista ``jobs``;
* ``/api/state?session=ID`` in modalita' sessione: ``session``, ``events``,
                           ``artifacts``, ``gates``, ``live`` e ``journal``;
* ``/api/approve?job=N``   POST che scrive il record **nella forma di keygate**;
* ``/api/answer?job=N&id=<id>&value=<value>`` POST che risponde a una domanda
                           aperta da ``vx.ask`` (triage: formato, hook, sottotitoli);
* ``/frames/<nome>?job=N`` il PNG di un fotogramma;
* ``/shots/<i>?job=N``     il JPEG dello shot ``i``: miniatura pronta, cache o un
                           fotogramma estratto dal sorgente;
* ``/result?job=N``        il master, con Range (il player puo' scorrere);
* ``/api/reveal?job=N``    POST che apre la cartella col master selezionato.

Due lavori non si pestano i piedi perche' ogni lavoro scrive i suoi file nella
propria cartella, con sostituzione atomica: chi legge vede prima o dopo, mai un
JSON troncato. L'indice del lavoro e' un numero, e se il numero non esiste si
cade sul primo — la pagina non si rompe.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from . import ask, degrade, feedback, guard, keygate, lock
from . import session as session_store

UI_DIR = Path(__file__).resolve().parent / "ui"
UI_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
}

#: Quanti livelli di cartelle guardare sotto la radice.
MAX_DEPTH = 2

#: I segni che una cartella e' un lavoro ``vx`` anche senza ``progress.json``.
#: Sono file e cartelle che la skill scrive davvero: una cartella con solo un
#: video dentro non basta, e non deve bastare.
JOB_MARKER_DIRS = ("stage_00", "proxy", "out")
JOB_MARKER_FILES = ("source.json", "report.json", "auto_report.json", "plan.json")

#: Le fasi che si possono dedurre dai file presenti in un lavoro senza panel.
#: ``audio`` non e' un segno di riconoscimento: da sola non fa un lavoro, ma
#: dentro un lavoro gia' riconosciuto dice che l'audio e' stato estratto.
DERIVED_PHASE_LABELS = (
    ("stage_00", "Materiale importato"),
    ("proxy", "Proxy pronti"),
    ("audio", "Audio estratto"),
)

#: La proposta di font e la scelta dell'operatore, scritte nella workdir.
FONT_PROPOSAL_NAME = "font_proposal.json"
FONT_CHOICE_NAME = "font_choice.json"
FONT_CHOICE_SCHEMA = "vx.fontchoice/1"
FONT_SAMPLE_PREFIX = "/fonts/sample/"

#: Il segnale che ``auto`` scrive quando il render e' fermo sui fotogrammi.
KEYFRAMES_WAITING_NAME = "waiting.json"

#: Al massimo tanti shot: la UI non deve disegnare una pellicola infinita.
SHOT_LIMIT = 60

#: La cache dei fotogrammi estratti, dentro la workdir del lavoro.
SHOT_DIR = "shots"

#: Un lavoro e' **attivo** se il suo stato e' stato scritto da meno di cosi'
#: secondi, oppure se un lock vivo dice che qualcuno ci sta lavorando adesso.
#: L'eta' da sola non basta: un lavoro concluso toccato tardi non sta girando.
ACTIVE_SEC = 90.0

#: Un lavoro non attivo ma toccato da meno di cosi' e' "recente": non e' la
#: vista iniziale, ma non e' nemmeno un lavoro chiuso da tempo.
RECENT_SEC = 900.0

#: I pezzi pubblicati da ``--live``: ``<workdir>/live/chunk_000.mp4`` + manifest.
LIVE_DIR = "live"
LIVE_MANIFEST = "manifest.json"
LIVE_NAME = re.compile(r"^chunk_(\d{3,})\.mp4$")
LIVE_PREFIX = "/live/"

#: I file ``-progress`` di ffmpeg: uno per ``auto``, uno per il worker.
RENDER_PROGRESS_FILES = (
    ("render", "ffmpeg-progress.txt"),
    ("", "ffprogress.log"),
)

#: Un progress file piu' vecchio di cosi' appartiene a un render finito.
RENDER_FRESH_SEC = 30.0

#: ``thumb_00_002.500.jpg``: l'istante campionato sta nel nome.
THUMB_NAME = re.compile(r"^thumb_.*_(\d+(?:\.\d+)?)\.jpg$", re.IGNORECASE)

#: Quanto vive in memoria una scansione costosa. La pagina polla ogni secondo:
#: un secondo di cache basta a non rifare lo stesso lavoro due volte di fila.
#: La scadenza e' solo una rete di sicurezza: la verita' e' l'mtime della
#: cartella, cosi' un lavoro che scrive invalida subito la sua voce.
CACHE_TTL_SEC = 1.0

#: Le scansioni costose (``discover``, ``keyframes/``, ``live/``, report, font).
_scan_cache: Dict[str, tuple] = {}
_cache_lock = threading.Lock()


def _cache_clear() -> None:
    """Svuota la cache delle scansioni (il benchmark la usa fra una misura e l'altra)."""
    with _cache_lock:
        _scan_cache.clear()


def _cached_scan(key: str, build, stamp) -> Any:
    """Il valore di ``build()``, riusato finche' lo ``stamp`` non cambia.

    ``stamp(value)`` e' il confronto economico: gli mtime delle cartelle e dei
    file da cui il valore dipende. Se lo stamp e' lo stesso e la voce non ha
    superato :data:`CACHE_TTL_SEC` si restituisce il valore gia' calcolato; un
    errore di I/O sullo stamp non e' una scusa per fidarsi, si ricalcola.
    """
    now = time.monotonic()
    with _cache_lock:
        entry = _scan_cache.get(key)
    if entry is not None:
        expires, old_stamp, value = entry
        if expires > now:
            try:
                if stamp(value) == old_stamp:
                    return value
            except OSError:
                pass
    value = build()
    try:
        fresh = stamp(value)
    except OSError:
        fresh = None
    with _cache_lock:
        if len(_scan_cache) > 1024:
            _scan_cache.clear()
        _scan_cache[key] = (now + CACHE_TTL_SEC, fresh, value)
    return value


def _mtime_ns(path: Path) -> int:
    """L'mtime in nanosecondi, ``0`` se il file non c'e': uno stamp compatto."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _number(value: Any) -> Optional[float]:
    """A finite float, or ``None``: a missing field is not a zero."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _parse_kv(path: Path) -> Dict[str, str]:
    """The ``chiave=valore`` file ffmpeg writes with ``-progress``.

    ffmpeg appends one block per update and the last block wins: reading the
    whole file and keeping the last value of each key gives the current state.
    An unreadable file is an empty block, never an exception.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    values: Dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        key = key.strip()
        if separator and key:
            values[key] = value.strip()
    return values


def _empty_render() -> Dict[str, Any]:
    return {"out_time_sec": 0.0, "duration_sec": 0.0, "percent": 0.0, "speed": ""}


def _looks_like_job(directory: Path, *, allow_live: bool = False) -> bool:
    """Una cartella e' un lavoro se ha il panel o i segni veri della skill.

    Il riconoscimento e' stretto di proposito: uno solo tra ``stage_00/``,
    ``proxy/``, ``out/``, ``source.json``, ``report.json``, ``plan.json``. Un
    video sciolto in una cartella resta fuori. ``allow_live`` accetta anche una
    workdir di render (``live/manifest.json``): vale per la radice che si serve
    esplicitamente, non per una sottocartella, che altrimenti comparirebbe come
    un secondo lavoro accanto a quello che l'ha prodotta.
    """
    if (directory / "progress.json").is_file():
        return True
    if allow_live and (directory / LIVE_DIR / LIVE_MANIFEST).is_file():
        return True
    if any((directory / name).is_dir() for name in JOB_MARKER_DIRS):
        return True
    return any((directory / name).is_file() for name in JOB_MARKER_FILES)


def _iso_utc(stamp: float) -> str:
    """L'istante di una scrittura in ISO 8601, o ``""`` se non si sa."""
    if not stamp or stamp <= 0:
        return ""
    return datetime.fromtimestamp(stamp, timezone.utc).replace(microsecond=0).isoformat()


def _state_mtime(directory: Path) -> float:
    """Quando il lavoro ha scritto lo stato l'ultima volta (0.0 se non lo dice).

    Per un lavoro pubblicato e' ``progress.json``: e' l'ultimo fatto che il
    lavoro ha raccontato. Per un lavoro dedotto sono i segni che lo rendono
    riconoscibile, presi dal piu' recente, piu' gli artefatti veri: il master in
    ``out/`` e il manifest live. Un lavoro il cui master e' stato scritto adesso
    e' il piu' recente anche senza un panel.
    """
    panel = directory / "progress.json"
    try:
        if panel.is_file():
            return panel.stat().st_mtime
    except OSError:
        pass
    stamps: List[float] = []
    for name in JOB_MARKER_DIRS + JOB_MARKER_FILES + (
        "out/master.mp4",
        f"{LIVE_DIR}/{LIVE_MANIFEST}",
    ):
        try:
            stamps.append((directory / name).stat().st_mtime)
        except OSError:
            continue
    return max(stamps) if stamps else 0.0


def _lock_alive(directory: Path, data_dir: Optional[Path] = None) -> bool:
    """Vero se la workdir ha un lock con un proprietario **vivo**.

    La domanda non e' "il file ``.lock`` e' recente?" ma "chi lo tiene e'
    ancora vivo?": la liveness la decide :mod:`vx.lock`, non l'eta' del file.
    Un lock illeggibile o di un processo morto non rende attivo un lavoro.
    """
    folders = [directory, directory / "work"]
    extra = _data_dir(directory) if data_dir is None else Path(data_dir)
    if extra not in folders and directory in extra.parents:
        folders.append(extra)
    for folder in folders:
        try:
            snapshot = lock.state(folder)
        except Exception:  # noqa: BLE001 - un lock rotto non e' un lavoro attivo
            continue
        if snapshot.get("present") and not snapshot.get("stale"):
            return True
    return False


def _activity(directory: Path, *, data_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Da quanto il lavoro tace, e se sta lavorando: ``active`` non e' l'eta'.

    ``active`` e' vero con lo stato scritto da meno di :data:`ACTIVE_SEC`
    secondi **oppure** con un lock vivo nella workdir. ``state`` distingue il
    lavoro in corso (``running``) da uno toccato da poco (``recent``) da uno
    chiuso (``finished``); ``updated_sec`` e' la verita' sull'eta', cosi' un
    100% vecchio si puo' leggere come vecchio.
    """
    stamp = _state_mtime(directory)
    age = max(0.0, time.time() - stamp) if stamp > 0 else None
    locked = _lock_alive(directory, data_dir)
    active = bool(locked or (age is not None and age < ACTIVE_SEC))
    if active:
        status = "running"
    elif age is not None and age < RECENT_SEC:
        status = "recent"
    else:
        status = "finished"
    return {
        "active": active,
        "locked": locked,
        "updated_sec": None if age is None else round(age, 1),
        "updated_at": _iso_utc(stamp),
        "state": status,
    }


def _default_index(
    jobs: List[Path], items: Optional[List[Mapping[str, Any]]] = None
) -> int:
    """Quale lavoro mostra la vista iniziale: quello che sta girando adesso.

    Se c'e' almeno un lavoro attivo vince il piu' recente tra gli attivi, e un
    **lock vivo** batte un mtime fresco: un lavoro concluso toccato tardi non
    e' un lavoro in corso. Senza nessun attivo si torna al primo, cioe' al piu'
    recente, che e' l'ultimo lavoro concluso.
    """
    if not jobs:
        return 0
    if items is None:
        items = [_activity(path) for path in jobs]
    best: Optional[tuple] = None
    for position, item in enumerate(items):
        if not item.get("active"):
            continue
        age = item.get("updated_sec")
        rank = (
            0 if item.get("locked") else 1,
            float("inf") if age is None else float(age),
        )
        if best is None or rank < best[0]:
            best = (rank, position)
    return 0 if best is None else best[1]


def _showing(items: List[Mapping[str, Any]], chosen: int) -> str:
    """Come si deve leggere la vista, senza far passare un concluso per in corso.

    ``active``: il lavoro scelto sta girando; ``last_finished``: non c'e' niente
    in corso e si sta mostrando l'ultimo concluso (la UI lo deve dire);
    ``selected``: c'e' un lavoro attivo, ma si sta guardando un altro.
    """
    if 0 <= chosen < len(items) and items[chosen].get("active"):
        return "active"
    if not any(item.get("active") for item in items):
        return "last_finished"
    return "selected"


def _derived_phases(directory: Path) -> List[Dict[str, str]]:
    """Le fasi che i file presenti raccontano, senza inventare le assenti."""
    return [
        {"key": key, "label": label}
        for key, label in DERIVED_PHASE_LABELS
        if (directory / key).is_dir()
    ]


def _master_path(directory: Path, progress: Mapping[str, Any]) -> Optional[Path]:
    """Il file d'uscita: quello pubblicato, o quello che si vede in ``out/``.

    Con ``progress.json`` vale solo il master registrato. Per un lavoro dedotto
    (senza panel) si guarda ``out/master.mp4`` e poi l'ultimo ``*.mp4`` della
    stessa cartella: e' un file che esiste davvero, non un nome promesso.
    """
    text = str(progress.get("master") or "")
    if text:
        return Path(text)
    if (directory / "progress.json").is_file():
        return None
    out = directory / "out"
    if not out.is_dir():
        return None
    named = out / "master.mp4"
    if named.is_file():
        return named
    candidates = [item for item in out.glob("*.mp4") if item.is_file()]
    if not candidates:
        return None

    def stamp(item: Path) -> float:
        try:
            return item.stat().st_mtime
        except OSError:
            return 0.0

    return max(candidates, key=lambda item: (stamp(item), item.name))


def _data_dir(directory: Path) -> Path:
    """Dove stanno ``plan.json`` e ``render/``: la cartella del lavoro o la sua ``work``.

    Il pannello (``progress.json``) vive accanto al master, mentre il piano e il
    render stanno nella workdir: con una workdir esplicita sono due cartelle
    diverse. Si guarda prima quella del lavoro, poi ``work`` dentro, poi quella
    del master. Se non c'e' niente si resta sulla cartella del lavoro: la UI
    legge zeri, non un errore.
    """
    candidates = [directory, directory / "work", directory.parent / "work"]
    for candidate in candidates:
        if (candidate / "plan.json").is_file():
            return candidate
    for candidate in candidates:
        if (candidate / "render").is_dir():
            return candidate
    # Una workdir di render a chunk non ha ``plan.json``: la riconoscono i suoi
    # artefatti (``chunks/`` o il manifest live), cosi' la vista trova il report
    # che il render ha scritto li' e non accanto al master.
    for candidate in candidates:
        if (candidate / "chunks").is_dir() or (
            candidate / LIVE_DIR / LIVE_MANIFEST
        ).is_file():
            return candidate
    return directory


def _report_workdirs(
    directory: Path, data_dir: Optional[Path] = None
) -> List[Path]:
    """Le workdir che il report dichiara, nell'ordine in cui vanno provate.

    ``--workdir X`` puo' stare ovunque: il report del render a chunk scrive
    ``workdir`` (e ``chunks.workdir``) proprio perche' la vista non deve
    indovinare. Il campo e' esplicito e vince sulla cartella accanto al master.
    """
    base = _data_dir(directory) if data_dir is None else Path(data_dir)
    return _cached_scan(
        f"reportdirs:{directory}:{base}",
        lambda: _report_workdirs_uncached(directory, base),
        lambda _value: (
            _mtime_ns(base / "report.json"),
            _mtime_ns(base / "auto_report.json"),
            _mtime_ns(directory / "report.json"),
            _mtime_ns(directory / "auto_report.json"),
            _mtime_ns(directory / "work" / "report.json"),
            _mtime_ns(directory / "work" / "auto_report.json"),
        ),
    )


def _report_workdirs_uncached(directory: Path, base: Path) -> List[Path]:
    folders: List[Path] = []
    for folder in (base, directory, directory / "work"):
        if folder in folders:
            continue
        folders.append(folder)
    found: List[Path] = []
    for folder in folders:
        for name in ("report.json", "auto_report.json"):
            report = _read_json(folder / name)
            if not report:
                continue
            chunks = report.get("chunks")
            candidates: List[Any] = []
            if isinstance(chunks, Mapping):
                candidates.append(chunks.get("workdir"))
            candidates.append(report.get("workdir"))
            live = report.get("live")
            if isinstance(live, Mapping) and live.get("directory"):
                candidates.append(Path(str(live["directory"])).parent)
            for raw in candidates:
                if not raw:
                    continue
                path = Path(str(raw))
                try:
                    if path.is_dir():
                        resolved = path.resolve()
                        if resolved not in found:
                            found.append(resolved)
                except OSError:
                    continue
    return found


def _workdir_candidates(directory: Path) -> List[Path]:
    """Ogni cartella in cui il lavoro puo' aver scritto domande e live."""
    folders: List[Path] = []
    for folder in [
        directory,
        _data_dir(directory),
        directory / "work",
        *_report_workdirs(directory),
    ]:
        try:
            resolved = folder.resolve()
        except OSError:
            continue
        if resolved not in folders:
            folders.append(resolved)
    return folders


def _live_folders(directory: Path, data_dir: Optional[Path] = None) -> List[Path]:
    """Dove cercare ``live/manifest.json``, prima la workdir dichiarata.

    Restituisce le cartelle ``live/`` vere, non le workdir: il chiamante apre
    ``<folder>/manifest.json`` e ``<folder>/chunk_NNN.mp4``.
    """
    folders: List[Path] = []
    base = _data_dir(directory) if data_dir is None else Path(data_dir)
    for workdir in [*_report_workdirs(directory, base), base, directory, directory / "work"]:
        folder = workdir / LIVE_DIR
        try:
            resolved = folder.resolve()
        except OSError:
            continue
        if resolved not in folders:
            folders.append(resolved)
    return folders


def _empty_font() -> Dict[str, Any]:
    """Nessuna proposta: il cancello dei font resta chiuso e la UI non lo apre."""
    return {
        "pending": False,
        "sample": "",
        "requirement": "",
        "pool": "",
        "chosen": "",
        "candidates": [],
    }


def _font_state(directory: Path) -> Dict[str, Any]:
    """La proposta di font e la scelta presa, nella forma promessa alla UI.

    ``pending`` e' vero solo con una proposta che ha candidati e nessun
    ``font_choice.json``: e' il segnale che apre il cancello. Il campione di un
    candidato e' un URL solo quando il PNG esiste davvero; se manca resta ``""``
    e la UI mostra il segnaposto invece di un'immagine rotta. Una scelta gia'
    presa non torna indietro: ``chosen`` arriva solo dal file di scelta.
    """
    proposal = _read_json(directory / FONT_PROPOSAL_NAME)
    if not proposal:
        return _empty_font()
    raw = proposal.get("candidates")
    items = [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []
    choice = _read_json(directory / FONT_CHOICE_NAME)

    candidates: List[Dict[str, Any]] = []
    for item in items:
        png = Path(str(item.get("sample_png") or ""))
        name = png.name if png.name and png.is_file() else ""
        candidates.append(
            {
                "family": str(item.get("family") or ""),
                "licence": str(item.get("licence") or ""),
                "because": str(item.get("because") or ""),
                "score": _number(item.get("score")) or 0.0,
                "downloaded": bool(item.get("downloaded")),
                "sample": (FONT_SAMPLE_PREFIX + urllib.parse.quote(name)) if name else "",
            }
        )

    first = next((item["sample"] for item in candidates if item["sample"]), "")
    return {
        "pending": bool(candidates) and not (directory / FONT_CHOICE_NAME).is_file(),
        "sample": Path(urllib.parse.unquote(first)).name if first else "",
        "requirement": str(proposal.get("requirement") or ""),
        "pool": str(proposal.get("pool") or ""),
        "chosen": str(choice.get("family") or ""),
        "candidates": candidates,
    }


def _font_pending(directory: Path) -> bool:
    """Vero se il cancello dei font e' aperto: proposta con candidati, nessuna scelta.

    E' la stessa domanda di :func:`_font_state` ma senza costruire i campioni:
    l'elenco dei lavori la usa per ogni voce, e non deve pagare i PNG. La
    risposta resta in cache finche' la proposta o la scelta non cambiano.
    """
    return _cached_scan(
        f"font:{directory}",
        lambda: _compute_font_pending(directory),
        lambda _value: (
            _mtime_ns(directory / FONT_PROPOSAL_NAME),
            _mtime_ns(directory / FONT_CHOICE_NAME),
        ),
    )


def _compute_font_pending(directory: Path) -> bool:
    if (directory / FONT_CHOICE_NAME).is_file():
        return False
    proposal = _read_json(directory / FONT_PROPOSAL_NAME)
    if not proposal:
        return False
    raw = proposal.get("candidates")
    items = raw if isinstance(raw, list) else []
    return any(isinstance(item, Mapping) for item in items)


def timeline(plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Gli shot del piano EDL, in ordine di destinazione.

    La fonte e' il ``plan.json`` che ``auto`` scrive nella workdir
    (``tracks.video[].clips[]`` con ``dst_in``/``dst_out``). Si tengono i clip
    del livello 0 — la sequenza dei tagli — e solo se non ce ne sono si usano
    tutti: una sovrapposizione e' un grafico, non un altro shot.
    """
    tracks = plan.get("tracks")
    video = tracks.get("video") if isinstance(tracks, Mapping) else None
    clips: List[tuple] = []
    for track in video or []:
        if not isinstance(track, Mapping):
            continue
        for clip in track.get("clips") or []:
            if not isinstance(clip, Mapping):
                continue
            start = _number(clip.get("dst_in"))
            end = _number(clip.get("dst_out"))
            if start is None or end is None or end <= start:
                continue
            layer = _number(clip.get("layer"))
            clips.append((start, end, int(layer) if layer is not None else 0))
    base = [item for item in clips if item[2] == 0] or clips
    base.sort(key=lambda item: (item[0], item[1]))
    return [
        {
            "index": index,
            "t_in": round(start, 6),
            "t_out": round(end, 6),
            "dur": round(end - start, 6),
        }
        for index, (start, end, _) in enumerate(base)
    ]


def _source_label(path: Any) -> str:
    """Solo il nome del file: il percorso completo non serve alla UI."""
    text = str(path or "").strip()
    return Path(text).name if text else ""


def _plan_sources(plan: Mapping[str, Any]) -> Dict[str, Any]:
    raw = plan.get("sources")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _source_id_label(source_id: Any) -> str:
    """Un ``source_id`` e' un nome di file solo se ha un'estensione."""
    text = str(source_id or "").strip()
    return Path(text).name if text and Path(text).suffix else ""


#: Parametri che descrivono *come* ffmpeg esegue, non l'effetto che si vede.
_EFFECT_PARAM_SKIP = {"filter", "fallback", "curves", "style", "path", "file", "backend"}


def _effect_text(kind: Any, params: Any) -> str:
    """Un effetto in una riga leggibile: ``zoom 1.15``, ``lut warm``, ``fade in 0.3s``."""
    label = str(kind or "").strip().replace("_", " ")
    if not label:
        return ""
    values = params if isinstance(params, Mapping) else {}
    if label in ("fade in", "fade out"):
        seconds = _number(values.get("duration", values.get("duration_sec")))
        return f"{label} {seconds:g}s" if seconds is not None else label
    if label == "lut":
        for key in ("preset", "name", "look", "lut"):
            text = values.get(key)
            if isinstance(text, str) and text.strip():
                return f"{label} {text.strip()}"
        return label
    if label == "zoom":
        amount = _number(values.get("amount"))
        if amount is not None:
            return f"{label} {1.0 + amount:.2f}"
        factor = _number(values.get("zoom", values.get("factor")))
        return f"{label} {factor:g}" if factor is not None else label
    bits: List[str] = []
    for key, value in values.items():
        if key in _EFFECT_PARAM_SKIP or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            number = _number(value)
            if number is None:
                continue
            suffix = "s" if "sec" in str(key) or "duration" in str(key) else ""
            bits.append(f"{number:g}{suffix}")
        elif isinstance(value, str) and value.strip() and "=" not in value and len(value) <= 24:
            bits.append(value.strip())
        if len(bits) >= 2:
            break
    return " ".join([label] + bits)


def _effects_from_treatments(plan: Mapping[str, Any]) -> List[str]:
    """I trattamenti globali accesi: nel piano a ``cuts`` valgono per ogni shot."""
    raw = plan.get("treatments")
    effects: List[str] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping) or not item.get("enabled"):
            continue
        text = _effect_text(item.get("name"), item.get("params"))
        if text:
            effects.append(text)
    return effects


def _effects_from_clip(clip: Mapping[str, Any]) -> List[str]:
    """Gli ``effects`` di un clip EDL3, uno per riga."""
    raw = clip.get("effects")
    effects: List[str] = []
    for item in raw if isinstance(raw, list) else []:
        text = _effect_text(item.get("kind"), item.get("params")) if isinstance(
            item, Mapping
        ) else _effect_text(item, {})
        if text:
            effects.append(text)
    return effects


def _shot_record(
    index: int,
    t_in: float,
    t_out: float,
    *,
    source: str = "",
    source_path: str = "",
    src_in: Optional[float] = None,
    effects: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Un shot con il suo dato privato ``src_in`` (per estrarre il fotogramma)."""
    return {
        "index": index,
        "t_in": round(float(t_in), 6),
        "t_out": round(float(t_out), 6),
        "dur": round(float(t_out) - float(t_in), 6),
        "source": source,
        "source_path": source_path,
        "src_in": float(t_in if src_in is None else src_in),
        "effects": list(effects or []),
    }


def _cut_shots(
    plan: Mapping[str, Any], directory: Path, data_dir: Path
) -> List[Dict[str, Any]]:
    """Piano vecchio: ``cuts`` sono intervalli di sorgente, i trattamenti sono globali."""
    source_path = ""
    for candidate in (data_dir / "source.json", directory / "source.json"):
        manifest = _read_json(candidate)
        source_path = str(manifest.get("path") or "")
        if source_path:
            break
    effects = _effects_from_treatments(plan)
    records: List[Dict[str, Any]] = []
    for cut in plan.get("cuts") or []:
        if not isinstance(cut, Mapping):
            continue
        start = _number(cut.get("in"))
        end = _number(cut.get("out"))
        if start is None or end is None or end <= start:
            continue
        records.append(
            _shot_record(
                len(records),
                start,
                end,
                source=_source_label(source_path),
                source_path=source_path,
                src_in=start,
                effects=effects,
            )
        )
        if len(records) >= SHOT_LIMIT:
            break
    return records


def _edl3_shots(plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Piano EDL3: i ``clips`` hanno i loro ``effects``."""
    raw_clips = [item for item in plan.get("clips") or [] if isinstance(item, Mapping)]

    def layer_of(clip: Mapping[str, Any]) -> int:
        layer = _number(clip.get("layer"))
        return int(layer) if layer is not None else 0

    base = [clip for clip in raw_clips if layer_of(clip) == 0] or raw_clips
    if any(_number(clip.get("dst_in")) is not None for clip in base):
        base.sort(key=lambda clip: float(_number(clip.get("dst_in")) or 0.0))
    records: List[Dict[str, Any]] = []
    cursor = 0.0
    for clip in base:
        src_in = _number(clip.get("src_in"))
        src_in = 0.0 if src_in is None else src_in
        src_out = _number(clip.get("src_out"))
        dst_in = _number(clip.get("dst_in"))
        dst_out = _number(clip.get("dst_out"))
        if dst_in is not None and dst_out is not None and dst_out > dst_in:
            start, end = dst_in, dst_out
        elif src_out is not None and src_out > src_in:
            start, end = cursor, cursor + (src_out - src_in)
        else:
            continue
        cursor = max(cursor, end)
        source_path = str(clip.get("source") or "")
        records.append(
            _shot_record(
                len(records),
                start,
                end,
                source=_source_label(source_path),
                source_path=source_path,
                src_in=src_in,
                effects=_effects_from_clip(clip),
            )
        )
        if len(records) >= SHOT_LIMIT:
            break
    return records


def _edl_shots(plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Piano EDL 2.2: stessa sequenza di :func:`timeline`, con sorgente ed effetti."""
    tracks = plan.get("tracks")
    video = tracks.get("video") if isinstance(tracks, Mapping) else None
    rows: List[tuple] = []
    for track in video or []:
        if not isinstance(track, Mapping):
            continue
        for clip in track.get("clips") or []:
            if not isinstance(clip, Mapping):
                continue
            start = _number(clip.get("dst_in"))
            end = _number(clip.get("dst_out"))
            if start is None or end is None or end <= start:
                continue
            layer = _number(clip.get("layer"))
            rows.append((start, end, int(layer) if layer is not None else 0, clip))
    base = [row for row in rows if row[2] == 0] or rows
    base.sort(key=lambda row: (row[0], row[1]))
    sources = _plan_sources(plan)
    global_effects = _effects_from_treatments(plan)
    records: List[Dict[str, Any]] = []
    for start, end, _layer, clip in base:
        source_id = str(clip.get("source_id") or "")
        entry = sources.get(source_id)
        if isinstance(entry, Mapping):
            source_path = str(entry.get("path") or "")
        elif isinstance(entry, str):
            source_path = entry
        else:
            source_path = ""
        if not source_path:
            source_path = _source_id_label(source_id)
        src_in = _number(clip.get("src_in"))
        effects = _effects_from_clip(clip) or global_effects
        records.append(
            _shot_record(
                len(records),
                start,
                end,
                source=_source_label(source_path),
                source_path=source_path,
                src_in=src_in if src_in is not None else start,
                effects=effects,
            )
        )
        if len(records) >= SHOT_LIMIT:
            break
    return records


def _shot_records(
    directory: Path, data_dir: Path, plan: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Gli shot del piano presente, qualunque sia la sua forma.

    L'ordine delle fonti e' quello vero della workdir: un piano a ``cuts``
    (trattamenti globali), un documento EDL3 (effetti per clip), un EDL 2.2
    (``tracks.video``). Un piano che non dice niente produce una lista vuota.
    """
    if not isinstance(plan, Mapping) or not plan:
        return []
    cuts = plan.get("cuts")
    if isinstance(cuts, list) and cuts:
        return _cut_shots(plan, directory, data_dir)
    clips = plan.get("clips")
    if isinstance(clips, list) and clips:
        return _edl3_shots(plan)
    if isinstance(plan.get("tracks"), Mapping):
        return _edl_shots(plan)
    return []


def _thumb_files(directory: Path, data_dir: Path) -> List[tuple]:
    """Le miniature gia' presenti in ``analysis/``, con il loro istante."""
    found: List[tuple] = []
    seen: List[Path] = []
    for folder in (data_dir / "analysis", directory / "analysis"):
        if folder in seen or not folder.is_dir():
            continue
        seen.append(folder)
        for item in folder.glob("thumb_*.jpg"):
            match = THUMB_NAME.match(item.name)
            if not match or not item.is_file():
                continue
            found.append((float(match.group(1)), item))
    return found


def _nearest_thumb(thumbs: List[tuple], t_in: float) -> Optional[Path]:
    if not thumbs:
        return None
    return min(thumbs, key=lambda pair: (abs(pair[0] - float(t_in)), pair[0]))[1]


def _render_active(directory: Path, data_dir: Path) -> bool:
    """Vero se un ``-progress`` recente dice che ffmpeg sta lavorando adesso."""
    now = time.time()
    for folder in (data_dir, directory):
        for parts in RENDER_PROGRESS_FILES:
            candidate = folder.joinpath(*parts)
            try:
                if candidate.is_file() and (now - candidate.stat().st_mtime) < RENDER_FRESH_SEC:
                    return True
            except OSError:
                continue
    return False


def _resolve_source(text: Any, directory: Path, data_dir: Path) -> Optional[Path]:
    """Il sorgente dichiarato dal piano, se il file esiste davvero."""
    raw = str(text or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    options = [candidate] if candidate.is_absolute() else [
        directory / candidate,
        data_dir / candidate,
        candidate,
    ]
    for item in options:
        try:
            if item.is_file():
                return item
        except OSError:
            continue
    return None


def _extract_shot(source: Path, at: float, target: Path) -> bool:
    """Un fotogramma a 480 px di larghezza, scritto nella cache del lavoro."""
    from . import ffmpeg as ff  # noqa: PLC0415 - solo quando serve davvero

    target.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-ss", f"{max(0.0, float(at)):.3f}",
        "-i", str(source),
        "-frames:v", "1",
        "-vf", "scale=480:-2",
        "-q:v", "2",
        str(target),
    ]
    try:
        proc = ff.run_ff(args)
    except Exception:  # noqa: BLE001 - un timeout e' un 404, non un crash del server
        proc = None
    if proc is None or not proc.ok:
        target.unlink(missing_ok=True)
        return False
    try:
        return target.is_file() and target.stat().st_size > 0
    except OSError:
        return False


def shots(
    directory: Path,
    plan: Mapping[str, Any],
    *,
    data_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """La forma pubblica degli shot: niente percorsi, solo cio' che la UI mostra."""
    data_dir = _data_dir(directory) if data_dir is None else data_dir
    records = _shot_records(directory, data_dir, plan)
    thumbs = _thumb_files(directory, data_dir)
    cache_dir = data_dir / SHOT_DIR
    entries: List[Dict[str, Any]] = []
    for record in records:
        index = int(record["index"])
        ready = bool(thumbs) or (cache_dir / f"{index}.jpg").is_file()
        entries.append(
            {
                "index": index,
                "t_in": record["t_in"],
                "t_out": record["t_out"],
                "dur": record["dur"],
                "source": record["source"],
                "effects": list(record["effects"]),
                "thumb": f"/shots/{index}",
                "thumb_ready": ready,
            }
        )
    return entries


def live_state(
    directory: Path, data_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """Lo stato live pubblicato dal render: pezzi pronti, totali e URL.

    La fonte e' il manifest ``vx.live/1`` nella cartella ``live/`` della workdir
    **vera** del render: quella che il report dichiara (``workdir``), non quella
    accanto al master. Se le due non coincidono lo stato lo dice (``workdir``,
    ``manifest``, ``mismatch``) invece di servire una pagina ferma. Un manifest
    assente e' uno stato vuoto, mai un errore: il player non deve distinguere
    "nessun render live" da "render live appena partito".
    """
    data_dir = _data_dir(directory) if data_dir is None else data_dir
    declared = _report_workdirs(directory, data_dir)
    folders = _live_folders(directory, data_dir)
    manifest: Dict[str, Any] = {}
    found: Optional[Path] = None
    for folder in folders:
        manifest = _read_json(folder / LIVE_MANIFEST)
        if manifest:
            found = folder
            break
    raw = manifest.get("chunks")
    entries: List[Dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        index = _number(item.get("index"))
        if index is None or index < 0:
            continue
        number = int(index)
        name = f"chunk_{number:03d}.mp4"
        entries.append(
            {
                "index": number,
                "t_in": round(float(_number(item.get("t_in")) or 0.0), 6),
                "t_out": round(float(_number(item.get("t_out")) or 0.0), 6),
                "ready": bool(item.get("ready")),
                "url": LIVE_PREFIX + name,
            }
        )
    if not entries:
        return {"ready": 0, "total": 0, "chunks": []}
    total = _number(manifest.get("total"))
    local = {
        Path(str(path)).resolve()
        for path in (
            data_dir / LIVE_DIR,
            directory / LIVE_DIR,
            directory / "work" / LIVE_DIR,
        )
    }
    workdir = found.parent if found is not None else None
    return {
        "ready": sum(1 for entry in entries if entry["ready"]),
        "total": int(total) if total is not None else len(entries),
        "chunks": entries,
        "directory": str(found.resolve()) if found is not None else "",
        "workdir": str(workdir.resolve()) if workdir is not None else "",
        "manifest": str((found / LIVE_MANIFEST).resolve()) if found is not None else "",
        "declared": bool(
            found is not None
            and any(found == (path / LIVE_DIR).resolve() for path in declared)
        ),
        "mismatch": bool(found is not None and found.resolve() not in local),
        "job_dir": str(data_dir.resolve()),
    }


def _live_state_cached(directory: Path, data_dir: Path) -> Dict[str, Any]:
    """``live_state`` con la scansione in cache: un manifest che non cambia non si rilegge.

    Lo stamp sono i ``live/`` veri, i manifest e i report che dichiarano la
    workdir del render: se uno di loro si muove, la voce scade subito.
    """
    return _cached_scan(
        f"live:{directory}:{data_dir}",
        lambda: live_state(directory, data_dir),
        lambda _value: (
            _mtime_ns(data_dir / LIVE_DIR),
            _mtime_ns(directory / LIVE_DIR),
            _mtime_ns(directory / "work" / LIVE_DIR),
            _mtime_ns(data_dir / LIVE_DIR / LIVE_MANIFEST),
            _mtime_ns(directory / LIVE_DIR / LIVE_MANIFEST),
            _mtime_ns(directory / "work" / LIVE_DIR / LIVE_MANIFEST),
            _mtime_ns(data_dir / "report.json"),
            _mtime_ns(data_dir / "auto_report.json"),
            _mtime_ns(directory / "report.json"),
            _mtime_ns(directory / "auto_report.json"),
        ),
    )


def render_state(directory: Path, plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Dove e' arrivato ffmpeg, letto dal suo file di progresso.

    ``out_time_us`` e ``out_time_ms`` sono **entrambi** microsecondi: ffmpeg ha
    chiamato cosi' il campo e non l'ha piu' cambiato. La durata non arriva dal
    file — ffmpeg non la scrive — ma dal piano. Senza file si torna zeri.
    """
    values = _parse_kv(directory / "render" / "ffmpeg-progress.txt")
    if not values:
        return _empty_render()
    micro: Optional[float] = None
    for key in ("out_time_us", "out_time_ms"):
        if key in values:
            micro = _number(values[key])
            if micro is not None:
                break
    out_time = max(0.0, micro) / 1_000_000.0 if micro is not None else 0.0
    block = plan.get("timeline")
    duration = _number(block.get("duration_sec")) if isinstance(block, Mapping) else None
    duration = duration if duration is not None and duration > 0 else 0.0
    percent = (
        min(100.0, max(0.0, out_time / duration * 100.0)) if duration > 0 else 0.0
    )
    return {
        "out_time_sec": round(out_time, 3),
        "duration_sec": round(duration, 3),
        "percent": round(percent, 2),
        "speed": str(values.get("speed") or ""),
    }


def _ui(name: str) -> Optional[bytes]:
    if name not in UI_FILES:
        return None
    candidate = UI_DIR / name
    try:
        return candidate.read_bytes() if candidate.is_file() else None
    except OSError as exc:
        degrade.note("serve", exc)
        return None


def discover(root: Path) -> List[Path]:
    """Le cartelle di lavoro sotto ``root``: quella stessa, poi i figli.

    Un lavoro e' una cartella con ``progress.json`` **oppure** con i segni veri
    della skill (``stage_00/``, ``proxy/``, ``out/``, ``source.json``,
    ``report.json``, ``plan.json``): cosi' anche un lavoro condotto a mano con
    ``ingest`` compare, pur senza panel. L'ordine e' per data di modifica, il
    piu' recente prima; per un lavoro dedotto vale il segno piu' recente. Non e'
    pero' la scelta della vista: ``state`` parte dal lavoro **attivo**, non dal
    file toccato per ultimo (vedi :func:`_default_index`).

    La scansione e' in cache per un secondo, ma lo stamp la invalida subito
    appena la radice cambia (una cartella creata o tolta) o un lavoro tocca il
    suo stato: la pagina non deve vedere un elenco vecchio.
    """
    root = Path(root)
    return _cached_scan(
        f"discover:{root}",
        lambda: _discover_uncached(root),
        lambda jobs: _discover_stamp(root, jobs),
    )


def _discover_stamp(root: Path, jobs: List[Path]) -> tuple:
    """Lo stamp che invalida la cache di :func:`discover`, senza rileggere tutto."""
    stamps: List[Any] = [root.stat().st_mtime_ns]
    try:
        for child in root.iterdir():
            try:
                stamps.append((child.name, child.stat().st_mtime_ns))
            except OSError:
                continue
    except OSError:
        return tuple(stamps)
    for job in jobs:
        stamps.append((str(job), _state_mtime(job)))
    return tuple(stamps)


def _subdirs(directory: Path) -> List[Path]:
    """Le sottocartelle dirette, in una sola scansione (niente ``glob`` profondo)."""
    try:
        with os.scandir(directory) as entries:
            return [Path(entry.path) for entry in entries if entry.is_dir()]
    except OSError as exc:
        degrade.note("serve", exc)
        return []


def _discover_uncached(root: Path) -> List[Path]:
    found: List[Path] = []
    if _looks_like_job(root, allow_live=True):
        found.append(root)
    known = [path.resolve() for path in found]
    level_dirs = _subdirs(root)
    for level in range(1, MAX_DEPTH + 1):
        next_level: List[Path] = []
        for candidate in level_dirs:
            # Dentro un lavoro gia' riconosciuto conta solo un ``progress.json``
            # esplicito: si evita di scandire le sue stage, i proxy, gli shot.
            parent_known = any(parent in known for parent in candidate.parents)
            if parent_known and not (candidate / "progress.json").is_file():
                continue
            is_job = _looks_like_job(candidate)
            if is_job:
                found.append(candidate)
                known.append(candidate.resolve())
            if level < MAX_DEPTH and (is_job or not parent_known):
                next_level.extend(_subdirs(candidate))
        level_dirs = next_level
    # Una cartella che contiene un lavoro pubblicato (progress.json) non e' un
    # lavoro a sua volta: la figlia vince sul padre, altrimenti servendo la
    # radice si vede lo stato dedotto vuoto invece del lavoro vero.
    published = [path for path in found if (path / "progress.json").is_file()]
    if published:
        found = [
            path
            for path in found
            if (path / "progress.json").is_file()
            or not any(parent == path for job in published for parent in job.parents)
        ]

    unique = {path.resolve(): path for path in found}

    return sorted(unique.values(), key=_state_mtime, reverse=True)


def _empty_approval(reason: str = "nessun piano di fotogrammi") -> Dict[str, Any]:
    """Nessun cancello aperto: la UI non mostra il pulsante, e sa perche'."""
    return {"required": False, "approved": False, "waiting": False, "reason": reason}


def _approval_state(directory: Path, pending: Mapping[str, Any]) -> Dict[str, Any]:
    """La verita' sul cancello dei fotogrammi, in quattro chiavi.

    ``required`` e' vero solo se c'e' un'attesa in corso (``waiting.json``) o un
    ``keyframes.json`` che vale per **questo** piano; ``waiting`` dice che il
    render e' fermo li' adesso; ``approved`` pretende il digest del piano, cosi'
    un'approvazione di un montaggio precedente non apre il cancello; ``reason``
    parla italiano solo quando il cancello non e' applicabile.
    """
    frames_dir = directory / "keyframes"
    waiting = (frames_dir / KEYFRAMES_WAITING_NAME).is_file()
    plan_digest = str(pending.get("digest") or "")
    record = keygate.read(directory / keygate.RECORD_NAME)
    coherent = bool(
        plan_digest
        and record
        and record.get("approved") is True
        and str(record.get("digest") or "") == plan_digest
    )
    required = bool(waiting or coherent)
    reason = ""
    if not required:
        if plan_digest:
            reason = "il render non sta aspettando l'approvazione"
        else:
            reason = "nessun piano di fotogrammi"
    return {
        "required": required,
        "approved": coherent,
        "waiting": waiting,
        "reason": reason,
    }


def _empty_guard(why: str = "") -> Dict[str, Any]:
    """Nessuna violazione dichiarata: la UI non mostra un allarme che non c'e'."""
    return {
        "ok": True,
        "skipped": True,
        "why": why,
        "violations": [],
        "warnings": [],
    }


def _guard_state(
    directory: Path, report: Mapping[str, Any], *, ready: bool
) -> Dict[str, Any]:
    """L'audit di consegna, letto dal report o rifatto sulla cartella.

    Un lavoro appena finito ha gia' ``report["guard"]``: e' la verita' misurata
    da ``auto`` e non si ricalcola a ogni giro della UI. Per un master senza
    report la guardia si esegue qui, ma solo quando il master esiste: durante il
    render non c'e' niente da consegnare e non si paga l'hash del sorgente.
    """
    declared = report.get("guard") if isinstance(report, Mapping) else None
    if isinstance(declared, Mapping):
        section = dict(declared)
        section.setdefault("ok", not (section.get("violations") or []))
        section.setdefault("skipped", False)
        section.setdefault("why", "")
        section.setdefault("violations", [])
        section.setdefault("warnings", [])
        return section
    if not ready:
        return _empty_guard("lavoro in corso: la guardia non e' ancora stata eseguita")
    try:
        audit = guard.audit(directory)
    except Exception as exc:  # noqa: BLE001 - la UI non muore per un audit
        return _empty_guard(f"guardia non eseguibile: {type(exc).__name__}: {exc}")
    return {
        "ok": bool(audit.get("ok")),
        "skipped": False,
        "why": ""
        if audit.get("ok")
        else "il lavoro non passa l'audit: non e' consegnabile",
        "violations": list(audit.get("violations") or []),
        "warnings": list(audit.get("warnings") or []),
        "dir": str(audit.get("dir") or directory),
        "exit_code": int(audit.get("exit_code") or 0),
    }


def _report_of(directory: Path, data_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Il referto del lavoro, con la stessa precedenza che usa lo stato.

    ``report.json`` prima, poi ``auto_report.json`` (e poi le stesse due nella
    data dir): la UI e il giudizio devono leggere lo stesso file, o il contesto
    del voto racconterebbe un altro lavoro.
    """
    folders = [directory]
    resolved = _data_dir(directory) if data_dir is None else data_dir
    if resolved != directory:
        folders.append(resolved)
    for folder in folders:
        for name in ("report.json", "auto_report.json"):
            found = _read_json(folder / name)
            if found:
                return found
    return {}


def _empty_feedback() -> Dict[str, Any]:
    """Nessun lavoro e nessun giudizio: la UI legge zeri, non un errore."""
    return {"asked": False, "given": False, "rating": None, "summary": feedback.report()}


def _feedback_state(directory: Path) -> Dict[str, Any]:
    """Se il voto e' stato chiesto, se e' arrivato, e gli aggregati del registro.

    ``asked`` viene dal segnale che ``auto`` scrive a fine lavoro; ``given`` dal
    registro, cercato per ``job_id``; ``rating`` e' l'ultimo voto di **questo**
    lavoro. Qui non si scrive niente: la vista legge e basta.
    """
    request = feedback.pending(directory)
    job_id = str(request.get("job_id") or directory.name)
    record = feedback.latest(job_id=job_id)
    rating = _number(record.get("rating")) if record else None
    return {
        "asked": bool(request.get("asked")),
        "given": record is not None,
        "rating": int(rating) if rating is not None else None,
        "summary": feedback.report(),
    }


def _beat_state(directory: Path) -> Dict[str, Any]:
    """L'ultimo battito che la sessione ha scritto per **questo** lavoro.

    ``/api/state`` in modalita' lavoro non porta ``events``: il battito si legge
    dal registro della sessione piu' recente e si attribuisce per ``job_path``
    (o, quando manca, per nome del lavoro). Un battito di un'altra cartella non
    vale: senza un battito che parli di questo lavoro si torna vuoto, mai un
    battito inventato.

    Il registro puo' pesare megabyte e la pagina lo chiede ogni secondo: si
    rilegge solo quando i file di eventi cambiano davvero (mtime e dimensione).
    Se il lavoro non e' quello dell'ultima sessione (due comandi insieme) si
    guardano le sessioni recenti, dalla piu' nuova: un battito vivo di un'altra
    cartella non deve nascondere il nostro.
    """
    try:
        base = session_store.default_root()
        names = session_store.list_sessions(root=base)[:5]
    except Exception:  # noqa: BLE001 - la vista perde il battito, non il lavoro
        return {}
    for name in names:
        folder = Path(base) / name
        beat = _cached_scan(
            f"beat:{folder}:{directory}",
            lambda folder=folder: _beat_uncached(folder, directory),
            lambda _value, folder=folder: _event_stamp(folder),
        )
        if beat:
            return beat
    return {}


def _event_stamp(folder: Path) -> tuple:
    """Lo stamp dei file da cui il battito dipende: cambia solo se cambiano."""
    stamps: List[Any] = []
    try:
        names = sorted(path.name for path in folder.glob("events*.jsonl"))
    except OSError:
        names = []
    for name in (session_store.STATE_NAME, *names):
        try:
            info = (folder / name).stat()
            stamps.append((name, info.st_mtime_ns, info.st_size))
        except OSError:
            stamps.append((name, 0, 0))
    return tuple(stamps)


def _beat_uncached(folder: Path, directory: Path) -> Dict[str, Any]:
    """La ricerca vera: l'ultimo ``heartbeat`` che parla di ``directory``."""
    session = _session_value(folder.name)
    if session is None:
        return {}
    try:
        target = Path(directory).resolve()
    except OSError:
        target = Path(directory).absolute()
    name = Path(directory).name
    for row in reversed(list(session.events or [])):
        if str(row.get("type") or "") != "heartbeat":
            continue
        raw = str(row.get("job_path") or "")
        if raw:
            try:
                if Path(raw).resolve() != target:
                    continue
            except OSError:
                continue
        elif str(row.get("job") or "") != name:
            continue
        return {
            "stage": str(row.get("stage") or ""),
            "detail": str(row.get("detail") or ""),
            "elapsed_sec": round(float(_number(row.get("elapsed_sec")) or 0.0), 3),
            "eta_sec": round(float(_number(row.get("eta_sec")) or 0.0), 3),
            "percent": _number(row.get("percent")),
            "at_sec": round(float(_number(row.get("t")) or 0.0), 3),
        }
    return {}


#: Gli esiti che un giudizio puo' dichiarare sono quelli del registro; il
#: ``running`` dello stato qui diventa ``incomplete`` (un lavoro in corso non e'
#: un lavoro finito da votare).


def _feedback_outcome(state_outcome: str, fallback: Any = None) -> str:
    """L'esito per il record: quello dello stato se e' un esito vero."""
    if state_outcome in feedback.OUTCOMES:
        return state_outcome
    if isinstance(fallback, str) and fallback in feedback.OUTCOMES:
        return fallback
    return "incomplete"


def add_feedback(
    root: Path, index: Optional[int] = None, rating: Any = None, note: str = ""
) -> Dict[str, Any]:
    """Registra il voto sul lavoro scelto, col contesto che il lavoro ha misurato.

    Il contesto arriva dal report; quello che il report non dice (esito dello
    stato, shot, font scelto) lo aggiunge lo stato gia' calcolato. Un voto
    fuori 1..5 non scrive niente: la risposta dice perche'.
    """
    jobs = discover(root)
    if not jobs:
        return {"ok": False, "reason": "nessun lavoro"}
    chosen = _default_index(jobs) if index is None else max(0, min(int(index), len(jobs) - 1))
    directory = jobs[chosen]
    try:
        value = feedback.validate_rating(rating)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    data_dir = _data_dir(directory)
    payload = _safe_job_state(directory)
    context = feedback.context_from_report(_report_of(directory, data_dir))
    if context.get("estimate_seconds") is None:
        estimate_block = payload.get("estimate")
        if isinstance(estimate_block, Mapping):
            context["estimate_seconds"] = _number(estimate_block.get("seconds"))
    if not context.get("shots"):
        context["shots"] = len(payload.get("shots") or [])
    if not context.get("font"):
        font_block = payload.get("font")
        if isinstance(font_block, Mapping):
            context["font"] = str(font_block.get("chosen") or "")
    if context.get("duration_sec") is None and payload.get("timeline"):
        context["duration_sec"] = float(payload["timeline"][-1]["t_out"])
    context["outcome"] = _feedback_outcome(
        str(payload.get("outcome") or ""), context.get("outcome")
    )
    record = feedback.append(
        feedback.make_record(
            job_id=directory.name,
            prompt=str(payload.get("prompt") or ""),
            rating=value,
            note=str(note or ""),
            context=context,
        )
    )
    if record.get("stored") is False:
        # Il voto e' valido ma il registro non si scrive: la vista lo dice,
        # non finge un salvataggio che non c'e' stato.
        return {
            "ok": False,
            "degraded": True,
            "job": directory.name,
            "rating": record.get("rating"),
            "reason": str(record.get("error") or "registro non scrivibile"),
        }
    return {
        "ok": True,
        "job": directory.name,
        "job_id": record["job_id"],
        "rating": record["rating"],
    }


def _outcome(
    *,
    percent: float,
    current: Any,
    note: str,
    ready: bool,
    waiting: bool,
    active: bool = True,
) -> str:
    """L'esito del lavoro, non solo la percentuale.

    ``percent`` racconta quanto si e' visto; ``outcome`` racconta come e'
    finita, ed e' l'unica cosa che la UI puo' usare per dire "concluso". Un
    master annunciato ma assente non e' un lavoro concluso: al massimo e'
    ``incomplete``, e se la nota dice che il lavoro si e' fermato e' ``stopped``.
    ``ready`` arriva da ``master_ready``: il file e' la verita', la nota no.
    ``active`` e' il lock/mtime: una fase dichiarata da un processo che non c'e'
    piu' e' un lavoro fermo, non un lavoro in corso.
    """
    lowered = str(note or "").lower()
    if "fermato" in lowered:
        return "stopped"
    if any(word in lowered for word in ("errore", "fallit", "eccezione")):
        return "failed"
    if current is not None and not active and not waiting:
        return "stopped"
    if current is not None or waiting:
        return "running"
    if ready:
        return "done"
    if float(_number(percent) or 0.0) >= 100.0:
        return "incomplete"
    return "idle"


def _ready_file(path: Optional[Path]) -> bool:
    """Un file consegnabile esiste davvero e ha byte dentro; un errore e' ``no``."""
    try:
        return bool(path is not None and path.is_file() and path.stat().st_size > 0)
    except OSError as exc:
        degrade.note("serve", exc)
        return False


def _size_of(path: Optional[Path]) -> int:
    if path is None:
        return 0
    try:
        return int(path.stat().st_size) if path.is_file() else 0
    except OSError as exc:
        degrade.note("serve", exc)
        return 0


def _questions_state(directory: Path, data_dir: Path) -> List[Dict[str, Any]]:
    """Le domande aperte da ``vx.ask`` in questo lavoro, una voce per id.

    ``ask`` scrive ``<workdir>/questions/``; la workdir vera e' quella che il
    report dichiara (una ``--workdir`` esplicita puo' stare ovunque), poi quella
    del piano e infine la cartella del lavoro. Si guardano tutte, senza far
    sparire l'una per l'altra, e la prima voce di un id vince: la vista non
    disegna due volte la stessa domanda.
    """
    entries: List[Dict[str, Any]] = []
    seen: set = set()
    for folder in _workdir_candidates(directory):
        for item in ask.pending(folder):
            key = str(item.get("id") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            entries.append(item)
    return entries


def _answer_workdir(directory: Path, data_dir: Path, qid: str) -> Path:
    """La cartella che contiene *quella* domanda: si risponde dove e' scritta."""
    for folder in _workdir_candidates(directory):
        try:
            if ask.question_path(folder, qid).is_file():
                return folder
        except OSError:
            continue
    return data_dir


def _valid_media(path: Optional[Path]) -> bool:
    """Un master che si puo' aprire: byte dentro e l'atomo ``ftyp`` in testa.

    La dimensione da sola non basta — un file troncato o un segnaposto di testo
    hanno byte ma non sono video. Per un contenitore MP4/MOV ``ftyp`` sta nei
    primi byte; per qualunque altro tipo resta la dimensione.
    """
    if not _ready_file(path):
        return False
    assert path is not None
    if path.suffix.lower() not in (".mp4", ".m4v", ".mov"):
        return True
    try:
        with path.open("rb") as handle:
            head = handle.read(4096)
    except OSError as exc:
        degrade.note("serve", exc)
        return False
    return b"ftyp" in head


def _derived_outcome(
    ready: bool, activity: Mapping[str, Any], guard_section: Mapping[str, Any]
) -> str:
    """L'esito di un lavoro senza ``progress.json``: lo dicono gli artefatti.

    Un master esistente e leggibile e' un lavoro **completato**, anche senza un
    panel che lo racconti. Senza master lo stato e' ``running`` se un lock vivo o
    un mtime fresco dicono che qualcuno ci sta lavorando, altrimenti ``stopped``.
    La guardia ha l'ultima parola solo per una violazione vera (un master
    prodotto fuori dalla pipeline, G2, da solo non e' un fallimento: e' un
    lavoro condotto a mano).
    """
    ok = guard_section.get("ok")
    violations = guard_section.get("violations") or []
    codes = {
        str(item.get("code") or "")
        for item in violations
        if isinstance(item, Mapping)
    }
    if ready:
        if ok is False and (codes - {"G2"}):
            return "failed"
        return "done"
    if ok is False and violations:
        return "failed"
    if activity.get("locked") or activity.get("active"):
        return "running"
    if activity.get("updated_sec") is None:
        return "idle"
    return "stopped"


def _frames_listing(data_dir: Path) -> List[Dict[str, Any]]:
    """I PNG di ``keyframes/`` con la loro dimensione, in cache finche' non cambiano."""
    frames_dir = data_dir / "keyframes"

    def build() -> List[Dict[str, Any]]:
        try:
            return [
                {"name": item.name, "bytes": _size_of(item)}
                for item in sorted(frames_dir.glob("*.png"))
            ]
        except OSError as exc:
            degrade.note("serve", exc)
            return []

    return _cached_scan(
        f"frames:{data_dir}",
        build,
        lambda _value: _mtime_ns(frames_dir),
    )


def _job_brief(directory: Path) -> Dict[str, Any]:
    """Il minimo che serve all'elenco dei lavori, calcolato **una volta** per lavoro.

    L'elenco ``jobs[]`` non ha bisogno degli shot, dei fotogrammi, del live, del
    journal: chiedergli lo stato completo di ogni lavoro era il costo vero di
    ``/api/state``. Qui si legge il panel, il report e il master, si deduce
    l'esito e ci si ferma. :func:`_job_state` riusa questo dizionario per il
    lavoro scelto, cosi' nessun file viene letto due volte.

    La risposta resta in cache per un secondo, con lo stamp sui due file che
    raccontano il lavoro (``progress.json`` e il report): finche' non cambiano,
    l'elenco non rilegge niente. La guardia non si esegue qui (un audit puo'
    avere un costo vero): vale il ``guard`` gia' dichiarato nel report, e per un
    lavoro dedotto senza report si assume pulito. Lo stato completo del lavoro
    scelto rifa' l'audit come prima.
    """
    directory = Path(directory)
    return _cached_scan(
        f"brief:{directory}",
        lambda: _job_brief_uncached(directory),
        lambda value: (
            (
                _mtime_ns(directory / "progress.json"),
                _mtime_ns(directory / "report.json"),
                _mtime_ns(directory / "out"),
                _mtime_ns(Path(str(value["data_dir"])) / "keyframes"),
            )
            if value.get("published")
            else (_state_mtime(directory),)
        ),
    )


def _job_brief_uncached(directory: Path) -> Dict[str, Any]:
    published = (directory / "progress.json").is_file()
    derived = not published
    progress = _read_json(directory / "progress.json") if published else {}
    data_dir = _data_dir(directory)
    activity = _activity(directory, data_dir=data_dir)
    report = _report_of(directory, data_dir)
    master = _master_path(directory, progress)
    ready = _ready_file(master)
    # Un master illeggibile conta solo per un lavoro dedotto: per uno pubblicato
    # l'esito lo dicono il panel e la guardia.
    artifact_ready = _valid_media(master) if derived else ready
    if derived and artifact_ready and not activity["locked"]:
        # Il master c'e' e si legge: il lavoro e' finito. Un mtime fresco sul
        # file appena scritto non e' "qualcuno ci sta lavorando".
        activity = {**activity, "active": False, "state": "finished"}
    font_pending = False if derived else _font_pending(directory)
    waiting = (data_dir / "keyframes" / KEYFRAMES_WAITING_NAME).is_file()
    declared = report.get("guard") if isinstance(report, Mapping) else None
    guard: Dict[str, Any]
    if isinstance(declared, Mapping):
        guard = dict(declared)
        guard.setdefault("ok", not (guard.get("violations") or []))
        guard.setdefault("skipped", False)
        guard.setdefault("why", "")
        guard.setdefault("violations", [])
        guard.setdefault("warnings", [])
    else:
        guard = _empty_guard()
    note = progress.get("note") or (
        "lavoro vx senza progresso pubblicato" if derived else ""
    )
    percent = progress.get("percent") or 0.0
    current = progress.get("current")
    if derived:
        # Senza panel lo stato viene dagli artefatti, non dall'assenza del panel.
        outcome = _derived_outcome(artifact_ready, activity, guard)
        if artifact_ready:
            note = "master presente: lavoro concluso (senza progresso pubblicato)"
        elif master is not None and not artifact_ready:
            note = "master illeggibile: non e' ancora un lavoro consegnato"
    else:
        outcome = _outcome(
            percent=percent,
            current=current,
            note=note,
            ready=ready,
            waiting=bool(waiting) or bool(font_pending),
            active=bool(activity["active"]),
        )
        # Un master scritto non e' un lavoro consegnato: la guardia ha l'ultima
        # parola sull'esito, e ``done`` non e' un esito possibile con violazioni.
        if outcome == "done" and guard.get("ok") is False:
            outcome = "failed"
    return {
        "job": directory.name,
        "path": str(directory),
        "prompt": progress.get("prompt") or "",
        "percent": percent,
        "current": current,
        "active": activity["active"],
        "locked": activity["locked"],
        "updated_sec": activity["updated_sec"],
        "updated_at": activity["updated_at"],
        "state": activity["state"],
        "outcome": outcome,
        "note": note,
        "published": published,
        "derived": derived,
        "progress": progress,
        "data_dir": data_dir,
        "activity": activity,
        "report": report,
        "master": master,
        "ready": ready,
        "artifact_ready": artifact_ready,
    }


def _render_section(
    directory: Path, data_dir: Path, plan: Mapping[str, Any], derived: bool
) -> Dict[str, Any]:
    """Dove e' arrivato ffmpeg, se c'e' qualcosa di vero da leggere.

    Un lavoro pubblicato ha il suo panel e lo si legge sempre. Un lavoro dedotto
    (``auto`` senza ``progress.json``) non ha percentuale **di piano**, ma mentre
    renderizza scrive lo stesso ``ffmpeg-progress.txt``: se quel file e' fresco
    (:data:`RENDER_FRESH_SEC`) la testina dell'encode e' un dato misurato, non
    una posizione inventata. Un progresso vecchio resta fuori: direbbe 70% di un
    render finito dieci minuti fa.
    """
    if not derived:
        return render_state(data_dir, plan)
    if not _render_active(directory, data_dir):
        return _empty_render()
    return render_state(data_dir, plan)


def _job_state(directory: Path, brief: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Lo stato del lavoro: pubblicato se c'e' il panel, altrimenti dedotto.

    Un lavoro senza ``progress.json`` non finge una percentuale: ``percent`` e'
    zero, ``current`` e' nullo, la ``note`` dice che manca il progresso, le fasi
    sono quelle che i file presenti raccontano e il master vale solo se il file
    d'uscita esiste davvero. ``progress_source`` dichiara quale dei due casi e'.

    Con un ``brief`` gia' calcolato da :func:`_job_brief` (il caso di ``state``)
    si riusa la lettura di panel, report, master e attivita': il lavoro scelto
    paga una volta quello che l'elenco ha gia' pagato per tutti.
    """
    brief = _job_brief(directory) if brief is None else brief
    directory = Path(directory)
    derived = bool(brief["derived"])
    progress = brief["progress"]
    data_dir = brief["data_dir"]
    activity = brief["activity"]
    report = brief["report"]
    plan = _read_json(data_dir / "plan.json")
    estimate = report.get("estimate") if isinstance(report.get("estimate"), Mapping) else {}
    plan_choice = report.get("plan_choice") if isinstance(report.get("plan_choice"), Mapping) else {}
    raw_decisions = report.get("decisions")
    decision_log = (
        [dict(item) for item in raw_decisions if isinstance(item, Mapping)]
        if isinstance(raw_decisions, list)
        else []
    )
    if not estimate and isinstance(progress.get("estimate"), Mapping):
        estimate = progress.get("estimate")
    if not plan_choice and isinstance(progress.get("plan_choice"), Mapping):
        plan_choice = progress.get("plan_choice")
    frames_dir = data_dir / "keyframes"
    frames = _frames_listing(data_dir)
    pending = _read_json(frames_dir / "pending.json")
    approval = _approval_state(data_dir, pending)
    font = _empty_font() if derived else _font_state(directory)
    master = brief["master"]
    ready = bool(brief["ready"])
    artifact_ready = bool(brief["artifact_ready"])
    note = brief["note"]
    percent = brief["percent"]
    guard_section = _guard_state(data_dir, report, ready=ready)
    if derived:
        # Senza panel lo stato viene dagli artefatti, non dall'assenza del panel.
        outcome = _derived_outcome(artifact_ready, activity, guard_section)
        if artifact_ready:
            note = "master presente: lavoro concluso (senza progresso pubblicato)"
        elif master is not None and not artifact_ready:
            note = "master illeggibile: non e' ancora un lavoro consegnato"
    else:
        outcome = _outcome(
            percent=percent,
            current=progress.get("current"),
            note=note,
            ready=ready,
            waiting=bool(approval["waiting"]) or bool(font.get("pending")),
            active=bool(activity["active"]),
        )
        # Un master scritto non e' un lavoro consegnato: la guardia ha l'ultima
        # parola sull'esito, e ``done`` non e' un esito possibile con violazioni.
        if outcome == "done" and guard_section.get("ok") is False:
            outcome = "failed"
    return {
        "job": directory.name,
        "path": str(directory),
        "active": activity["active"],
        "locked": activity["locked"],
        "updated_sec": activity["updated_sec"],
        "updated_at": activity["updated_at"],
        "state": activity["state"],
        "prompt": progress.get("prompt") or "",
        "clips": progress.get("clips") or 0,
        "percent": percent,
        "elapsed_sec": progress.get("elapsed_sec") or 0,
        "current": progress.get("current"),
        "note": note,
        "estimate": estimate,
        "plan_choice": plan_choice,
        "decisions": decision_log,
        "outcome": outcome,
        "guard": guard_section,
        "phases": _derived_phases(directory) if derived else (progress.get("phases") or []),
        "journal": [] if derived else list(progress.get("journal") or []),
        "frames": frames,
        "approved": bool(approval["approved"]),
        "approval": approval,
        "master": master.name if ready and master is not None else "",
        "master_ready": ready,
        "master_bytes": _size_of(master) if ready else 0,
        "render": _render_section(directory, data_dir, plan, derived),
        "timeline": [] if derived else timeline(plan),
        "shots": shots(directory, plan, data_dir=data_dir),
        "live": _live_state_cached(directory, data_dir),
        "font": font,
        "questions": _questions_state(directory, data_dir),
        "feedback": _feedback_state(directory),
        "beat": _beat_state(directory),
        "progress_source": "derived" if derived else "json",
    }


#: Lo stato completo dei lavori chiesti da una sessione: ``percorso -> (stamp,
#: stato)``. Lo stamp sono gli mtime della cartella: se non cambiano, lo stato
#: non e' invecchiato e la pagina lo riceve in millisecondi.
_job_state_cache: Dict[str, tuple] = {}


def _job_state_stamp(directory: Path) -> tuple:
    """Gli mtime da cui dipende :func:`_job_state`: cambiano solo se cambia.

    Sono i file che il lavoro riscrive (panel, report, piano, progresso di
    ffmpeg) e le cartelle in cui deposita gli artefatti (uscita, fotogrammi,
    miniature, live). Basta uno solo che si muove per ricalcolare lo stato.
    """
    data_dir = _data_dir(directory)
    names = (
        "progress.json",
        "report.json",
        "auto_report.json",
        "plan.json",
        "render/ffmpeg-progress.txt",
        "keyframes",
        "analysis",
        "out",
        "shots",
        LIVE_DIR,
    )
    stamps: List[Any] = [_mtime_ns(directory / name) for name in names]
    if data_dir != directory:
        stamps.extend(_mtime_ns(data_dir / name) for name in names)
    return tuple(stamps)


def _job_state_cached(directory: Path) -> Dict[str, Any]:
    """Lo stato completo di un lavoro, riusato finche' la cartella non cambia.

    Servendo una sessione la pagina chiede lo stato ogni secondo: senza cache
    ogni giro rileggerebbe panel, report, piano, fotogrammi e registro delle
    sessioni (centinaia di ``stat``) per lo stesso lavoro. La verita' e' lo
    stamp: appena il lavoro scrive qualcosa la voce scade, e finche' non scrive
    la risposta e' quella gia' calcolata.
    """
    directory = Path(directory)
    key = str(directory)
    stamp = _job_state_stamp(directory)
    with _cache_lock:
        entry = _job_state_cache.get(key)
    if entry is not None and entry[0] == stamp:
        return entry[1]
    value = _job_state(directory)
    # Se la cartella si e' mossa mentre leggevamo, non si mette in cache: lo
    # stamp di prima descriverebbe uno stato che non e' piu' quello.
    if _job_state_stamp(directory) == stamp:
        with _cache_lock:
            if len(_job_state_cache) > 64:
                _job_state_cache.clear()
            _job_state_cache[key] = (stamp, value)
    return value


def _session_job_view(
    snapshot: Mapping[str, Any], job: Optional[Path]
) -> Dict[str, Any]:
    """Lo stato di lavoro della sessione, nella forma che la pagina sa disegnare.

    La sessione racconta **cosa e' stato eseguito**, il lavoro **cosa ne e'
    uscito**: la pagina conosce solo la seconda lingua e finora, aprendo ``/``
    sul daemon, restava sul guscio vuoto. Qui lo stato del lavoro si risolve con
    le stesse funzioni di ``?job=`` (:func:`_job_state`) e viene messo sotto
    ``job``, con ``jobs`` di una voce sola per la barra laterale.

    Una cartella cancellata non diventa uno stato vuoto: ``job`` e' ``None`` e
    ``reason`` dice quale percorso non c'e' piu', cosi' la pagina lo scrive
    invece di tacere.
    """
    raw = str(snapshot.get("job_path") or "") or (str(job) if job is not None else "")
    if not raw:
        return {
            "job": None,
            "jobs": [],
            "reason": "la sessione non dichiara una cartella di lavoro: niente da rileggere",
        }
    directory = Path(raw)
    try:
        exists = directory.is_dir()
    except OSError:
        exists = False
    if not exists:
        return {
            "job": None,
            "jobs": [],
            "reason": f"la cartella del lavoro non esiste piu': {directory}",
        }
    try:
        payload = _job_state_cached(directory)
    except Exception as exc:  # noqa: BLE001 - la vista della sessione non muore
        degrade.note("serve", exc)
        return {
            "job": None,
            "jobs": [],
            "reason": f"lo stato del lavoro non si legge: {type(exc).__name__}: {exc}",
        }
    entry = {
        "index": 0,
        "job": payload["job"],
        "prompt": payload["prompt"],
        "percent": payload["percent"],
        "current": payload["current"],
        "active": payload["active"],
        "outcome": payload["outcome"],
        "updated_sec": payload["updated_sec"],
        "updated_at": payload["updated_at"],
    }
    return {"job": payload, "jobs": [entry], "reason": ""}


def _session_value(session_id: Optional[str] = None) -> Optional[session_store.Session]:
    """Rilegge la sessione scelta (o l'ultima), senza mai scrivere."""
    try:
        if session_id is None or str(session_id) == "":
            latest = _latest_session_id()
            if not latest:
                return None
            return session_store.load(latest)
        return session_store.load(session_id)
    except Exception:  # noqa: BLE001 - un registro rotto non ferma la vista
        return None


#: Per quanti secondi si ricorda qual e' l'ultima sessione. ``list_sessions``
#: scandisce ogni cartella del registro (due ``stat`` per sessione, e su Windows
#: costano): la pagina chiede lo stato ogni secondo, il registro cambia solo
#: quando un comando parte o finisce. Cinque secondi sono un ritardo che non si
#: vede, e tolgono dalla risposta una scansione che cresce col numero di sessioni.
LATEST_TTL_SEC = 5.0

_latest_cache: Dict[str, tuple] = {}


def _latest_session_id() -> str:
    """L'id della sessione piu' recente, senza riscandire il registro ogni volta."""
    try:
        base = str(session_store.default_root())
    except Exception:  # noqa: BLE001 - senza radice si torna al comportamento di prima
        return ""
    now = time.monotonic()
    with _cache_lock:
        entry = _latest_cache.get(base)
    if entry is not None and entry[0] > now:
        return entry[1]
    try:
        value = session_store.latest() or ""
    except Exception:  # noqa: BLE001 - un registro rotto non ferma la vista
        value = ""
    with _cache_lock:
        if len(_latest_cache) > 16:
            _latest_cache.clear()
        _latest_cache[base] = (now + LATEST_TTL_SEC, value)
    return value


def _empty_session_state(
    reason: str = "nessuna sessione: nessun comando vx e' stato eseguito",
) -> Dict[str, Any]:
    return {
        "session": {
            "schema": session_store.SCHEMA,
            "id": "",
            "kind": "",
            "status": "absent",
            "reason": reason,
            "event_count": 0,
            "decisions": [],
        },
        "events": [],
        "artifacts": [],
        "gates": [],
        "questions": [],
        "live": {"ready": 0, "total": 0, "chunks": []},
        "journal": [],
        "job": None,
        "jobs": [],
        "reason": reason,
    }


def session_state(session_id: Optional[str] = None) -> Dict[str, Any]:
    """Lo stato della sessione: eventi, file, cancelli, live, journal **e lavoro**.

    La forma e' quella promessa alla pagina: ``session`` e' lo snapshot
    riassunto, ``events`` la storia completa. Tutto viene dal registro, non da
    una cartella di lavoro: un ``vx doctor`` senza ``progress.json`` si vede.

    In piu' c'e' ``job``: lo stato del lavoro che la sessione ha prodotto,
    risolto con la stessa funzione della modalita' ``?job=`` quando la sua
    cartella esiste ancora. Senza questo la pagina, che parla solo la lingua del
    lavoro, restava sul guscio vuoto. Nessun campo di prima e' stato tolto.
    """
    session = _session_value(session_id)
    if session is None:
        return _empty_session_state()
    snapshot = session.as_dict()
    summary = {
        key: snapshot.get(key)
        for key in (
            "schema",
            "id",
            "kind",
            "argv",
            "cwd",
            "job",
            "job_path",
            "status",
            "started_utc",
            "t",
            "event_count",
            "counts",
            "stage",
            "stages",
            "gate_state",
            "errors",
            "events_path",
            "rotated",
        )
    }
    summary["decisions"] = list(snapshot.get("decisions") or [])
    live = snapshot.get("live") or {"ready": 0, "total": 0, "chunks": []}
    live_chunks = []
    for item in live.get("chunks") or []:
        entry = dict(item)
        try:
            entry["url"] = f"{LIVE_PREFIX}chunk_{int(entry.get('index')):03d}.mp4"
        except (TypeError, ValueError):
            entry["url"] = ""
        live_chunks.append(entry)
    live = {**{key: value for key, value in live.items() if key != "chunks"},
            "chunks": live_chunks}
    job = _session_job(session_id, session=session)
    payload = {
        "session": summary,
        "events": [dict(row) for row in session.events],
        "artifacts": list(snapshot.get("artifacts") or []),
        "gates": list(snapshot.get("gates") or []),
        "questions": _questions_state(job, _data_dir(job)) if job is not None else [],
        "live": live,
        "journal": list(snapshot.get("journal") or []),
    }
    payload.update(_session_job_view(snapshot, job))
    return payload


def _session_job(
    session_id: Optional[str] = None,
    session: Optional[session_store.Session] = None,
) -> Optional[Path]:
    """La workdir dell'evento piu' recente che ne dichiara una.

    Il registro puo' contenere piu' percorsi (report, proxy, chunk): si guarda
    dal fondo, perche' l'ultimo evento e' quello che il lavoro sta vivendo. Se
    il percorso dichiarato non esiste si prova un file prodotto, risalendo
    alla cartella che sembra un lavoro. Con una ``session`` gia' letta non si
    riapre il registro: chi chiama ce l'ha in mano.
    """
    session = session if session is not None else _session_value(session_id)
    if session is None:
        return None
    seen: List[Path] = []
    for row in reversed(session.events):
        raw = row.get("job_path")
        if raw:
            candidate = Path(str(raw))
            if candidate not in seen:
                seen.append(candidate)
    snapshot = session.as_dict()
    if snapshot.get("job_path"):
        candidate = Path(str(snapshot["job_path"]))
        if candidate not in seen:
            seen.append(candidate)
    for candidate in seen:
        if candidate.is_dir():
            return candidate
    for row in reversed(session.events):
        if str(row.get("type") or "") != "artifact":
            continue
        path = Path(str(row.get("path") or ""))
        for parent in path.parents:
            if any(
                (parent / name).is_file()
                for name in ("progress.json", "report.json", "auto_report.json", "plan.json")
            ):
                return parent
    return seen[0] if seen else None


def session_approve(session_id: Optional[str] = None) -> Dict[str, Any]:
    job = _session_job(session_id)
    if job is None:
        return {"ok": False, "reason": "nessun lavoro nella sessione"}
    return approve(job)


def session_font_pick(session_id: Optional[str] = None, family: str = "") -> Dict[str, Any]:
    job = _session_job(session_id)
    if job is None:
        return {"ok": False, "reason": "nessun lavoro nella sessione"}
    return font_pick(job, family=family)


def _write_plan_choice(directory: Path, choice: Any) -> Dict[str, Any]:
    value = str(choice or "").strip().lower()
    if value not in ("full", "fast"):
        return {"ok": False, "reason": "scelta non valida: full o fast"}
    payload = {
        "schema": "vx.plan-choice/1",
        "choice": value,
        "chosen_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "from": "ui",
    }
    try:
        (directory / "plan_choice.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except OSError as exc:
        degrade.note("serve", exc)
        return {
            "ok": False,
            "degraded": True,
            "reason": f"scelta non scritta: {type(exc).__name__}: {exc}",
        }
    return {"ok": True, "choice": value, "job": directory.name}


def session_plan_choice(session_id: Optional[str] = None, choice: Any = "") -> Dict[str, Any]:
    job = _session_job(session_id)
    if job is None:
        return {"ok": False, "reason": "nessun lavoro nella sessione"}
    return _write_plan_choice(_data_dir(job), choice)


def session_feedback(
    session_id: Optional[str] = None, rating: Any = None, note: str = ""
) -> Dict[str, Any]:
    job = _session_job(session_id)
    if job is None:
        return {"ok": False, "reason": "nessun lavoro nella sessione"}
    return add_feedback(job, None, rating, note)


def session_master(session_id: Optional[str] = None) -> Optional[Path]:
    """Il master piu' recente registrato nella sessione, se esiste davvero."""
    session = _session_value(session_id)
    if session is not None:
        for row in reversed(session.events):
            if str(row.get("type") or "") != "artifact":
                continue
            if str(row.get("kind") or "") not in ("master", "output"):
                continue
            candidate = Path(str(row.get("path") or ""))
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
    job = _session_job(session_id)
    if job is None:
        return None
    path = _master_path(job, _read_json(job / "progress.json"))
    return path if path is not None and path.is_file() else None


def session_reveal(session_id: Optional[str] = None) -> Dict[str, Any]:
    master = session_master(session_id)
    if master is None:
        return {"ok": False, "reason": "il master non esiste ancora"}
    REVEAL(master)
    return {"ok": True, "folder": str(master.parent), "name": master.name}


def session_chunk(session_id: Optional[str], name: str) -> Optional[Path]:
    """Un pezzo live dal manifesto della sessione, non da una cartella fissa."""
    if not LIVE_NAME.fullmatch(name):
        return None
    session = _session_value(session_id)
    if session is None:
        return None
    snapshot = session.as_dict()
    folders: List[Path] = []
    live = snapshot.get("live") if isinstance(snapshot.get("live"), Mapping) else {}
    if live and live.get("directory"):
        folders.append(Path(str(live["directory"])))
    for row in reversed(session.events):
        if str(row.get("type") or "") == "artifact" and Path(str(row.get("path") or "")).name == name:
            candidate = Path(str(row.get("path") or ""))
            if candidate.is_file():
                return candidate
    for folder in folders:
        candidate = folder / name
        try:
            base = folder.resolve()
            target = candidate.resolve()
            if (target == base or base in target.parents) and candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _empty_job_payload(directory: Any, why: str = "") -> Dict[str, Any]:
    """Lo stato di un lavoro che non si legge: zeri e il motivo, non un 500."""
    return {
        "job": Path(str(directory)).name if directory else "",
        "path": str(directory or ""),
        "active": False,
        "locked": False,
        "updated_sec": None,
        "updated_at": "",
        "state": "",
        "showing": "none",
        "prompt": "",
        "clips": 0,
        "percent": 0.0,
        "elapsed_sec": 0,
        "current": None,
        "note": why or "",
        "estimate": {},
        "plan_choice": {},
        "decisions": [],
        "outcome": "idle",
        "guard": _empty_guard(why or "stato non leggibile"),
        "phases": [],
        "journal": [],
        "frames": [],
        "approved": False,
        "approval": _empty_approval(),
        "master": "",
        "master_ready": False,
        "master_bytes": 0,
        "render": _empty_render(),
        "timeline": [],
        "shots": [],
        "live": {"ready": 0, "total": 0, "chunks": []},
        "font": _empty_font(),
        "questions": [],
        "feedback": _empty_feedback(),
        "beat": {},
        "progress_source": "",
        "jobs": [],
        "selected": 0,
        "selector": False,
    }


def _safe_job_state(directory: Path) -> Dict[str, Any]:
    """``_job_state`` senza eccezioni: un lavoro rotto non ferma gli altri."""
    try:
        return _job_state(directory)
    except Exception as exc:  # noqa: BLE001 - la vista non e' il lavoro
        degrade.note("serve", exc)
        payload = _empty_job_payload(directory, f"stato non leggibile: {type(exc).__name__}: {exc}")
        payload["degraded"] = True
        return payload


def state(root: Path, index: Optional[int] = None) -> Dict[str, Any]:
    """Lo stato del lavoro scelto, piu' l'elenco di tutti quelli scoperti.

    Senza ``?job`` la vista non parte dal file toccato per ultimo ma dal lavoro
    che sta girando **adesso** (lock vivo o stato fresco): un lavoro concluso
    con un ``progress.json`` toccato tardi non ruba piu' la schermata. Se non
    c'e' niente in corso si mostra l'ultimo concluso, e ``showing`` lo dichiara
    (``last_finished``) invece di far finta che stia lavorando.
    """
    jobs = discover(root)
    if not jobs:
        return {
            "job": "",
            "path": str(root),
            "active": False,
            "locked": False,
            "updated_sec": None,
            "updated_at": "",
            "state": "",
            "showing": "none",
            "prompt": "",
            "clips": 0,
            "percent": 0.0,
            "elapsed_sec": 0,
            "current": None,
            "note": "",
            "estimate": {},
            "plan_choice": {},
            "decisions": [],
            "outcome": "idle",
            "guard": _empty_guard("nessun lavoro"),
            "phases": [],
            "journal": [],
            "frames": [],
            "approved": False,
            "approval": _empty_approval(),
            "master": "",
            "master_ready": False,
            "master_bytes": 0,
            "render": _empty_render(),
            "timeline": [],
            "shots": [],
            "live": {"ready": 0, "total": 0, "chunks": []},
            "font": _empty_font(),
            "questions": [],
            "feedback": _empty_feedback(),
            "beat": {},
            "progress_source": "",
            "jobs": [],
            "selected": 0,
            "selector": False,
        }
    # Un brief leggero per ogni lavoro: l'elenco non ha bisogno del live, degli
    # shot o del journal di chi non e' scelto. Un lavoro che non si legge non
    # ferma gli altri: al suo posto resta una voce vuota, con il motivo.
    items: List[Dict[str, Any]] = []
    for path in jobs:
        try:
            items.append(_job_brief(path))
        except Exception as exc:  # noqa: BLE001 - la vista non e' il lavoro
            degrade.note("serve", exc)
            items.append(_failed_brief(path, exc))
    chosen = (
        _default_index(jobs, items)
        if index is None
        else max(0, min(int(index), len(jobs) - 1))
    )
    entries = [
        {
            "index": position,
            "job": item["job"],
            "prompt": item["prompt"],
            "percent": item["percent"],
            "current": item["current"],
            "active": item["active"],
            "outcome": item["outcome"],
            "updated_sec": item["updated_sec"],
            "updated_at": item["updated_at"],
        }
        for position, item in enumerate(items)
    ]
    # Lo stato completo si costruisce **solo** per il lavoro scelto, riusando il
    # brief che l'elenco ha gia' calcolato: niente file letti due volte.
    try:
        payload = _job_state(jobs[chosen], brief=items[chosen])
    except Exception as exc:  # noqa: BLE001 - la vista non e' il lavoro
        degrade.note("serve", exc)
        payload = _empty_job_payload(
            jobs[chosen], f"stato non leggibile: {type(exc).__name__}: {exc}"
        )
        payload["degraded"] = True
    payload["jobs"] = entries
    payload["selected"] = chosen
    payload["selector"] = len(entries) > 1
    payload["showing"] = _showing(items, chosen)
    return payload


def _failed_brief(directory: Path, exc: BaseException) -> Dict[str, Any]:
    """Il brief di un lavoro che non si legge: zeri, il motivo, nessun crash."""
    return {
        "job": Path(str(directory)).name,
        "path": str(directory),
        "prompt": "",
        "percent": 0.0,
        "current": None,
        "active": False,
        "locked": False,
        "updated_sec": None,
        "updated_at": "",
        "state": "",
        "outcome": "idle",
        "note": f"stato non leggibile: {type(exc).__name__}: {exc}",
        "published": False,
        "derived": True,
        "progress": {},
        "data_dir": Path(str(directory)),
        "activity": {"active": False, "locked": False, "updated_sec": None,
                      "updated_at": "", "state": ""},
        "report": {},
        "master": None,
        "ready": False,
        "artifact_ready": False,
    }


def reveal_native(path: Path) -> None:
    """Apre la cartella col file selezionato, senza toccare il file."""
    path = Path(path)
    if os.name == "nt":
        # `explorer /select,` vuole la virgola attaccata al percorso.
        subprocess.Popen(["explorer", f"/select,{path}"])  # noqa: S603,S607
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])  # noqa: S603,S607
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])  # noqa: S603,S607


#: Sostituibile nei test: qui si vuole provare il contratto, non aprire finestre.
REVEAL = reveal_native


def reveal(root: Path, index: Optional[int] = None) -> Dict[str, Any]:
    """Apre la cartella del master. Il percorso non arriva mai dal client.

    Si apre **solo** il file d'uscita di un lavoro scoperto — quello registrato
    in ``progress.json`` o, per un lavoro dedotto, quello presente in ``out/``:
    la richiesta sceglie il lavoro, non il percorso.
    """
    jobs = discover(root)
    if not jobs:
        return {"ok": False, "reason": "nessun lavoro"}
    chosen = _default_index(jobs) if index is None else max(0, min(int(index), len(jobs) - 1))
    directory = jobs[chosen]
    master = _master_path(directory, _read_json(directory / "progress.json"))
    if master is None or not master.is_file():
        return {"ok": False, "reason": "il master non esiste ancora"}
    REVEAL(master)
    return {"ok": True, "folder": str(master.parent), "name": master.name}


def approve(root: Path, index: Optional[int] = None) -> Dict[str, Any]:
    """Scrive il record nella forma di ``keygate``, con il digest del piano."""
    jobs = discover(root)
    if not jobs:
        return {"ok": False, "reason": "nessun lavoro in corso"}
    chosen = _default_index(jobs) if index is None else max(0, min(int(index), len(jobs) - 1))
    directory = jobs[chosen]
    # Il piano e il record stanno dove sta ``plan.json`` (la workdir del job):
    # ``auto`` aspetta li', non nella cartella del pannello.
    data_dir = _data_dir(directory)
    pending = _read_json(data_dir / "keyframes" / "pending.json")
    digest = str(pending.get("digest") or "")
    if not digest:
        return {"ok": False, "reason": "nessun piano di fotogrammi da approvare"}
    payload = {
        "version": keygate.GATE_VERSION,
        "approved": True,
        "digest": digest,
        "timeline_digest": str(pending.get("timeline_digest") or ""),
        "count": int(pending.get("count") or 0),
        "frames": list(pending.get("frames") or []),
    }
    try:
        (data_dir / keygate.RECORD_NAME).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        # L'attesa e' finita: il segnale che il render e' fermo non deve restare.
        (data_dir / "keyframes" / KEYFRAMES_WAITING_NAME).unlink(missing_ok=True)
    except OSError as exc:
        degrade.note("serve", exc)
        return {
            "ok": False,
            "degraded": True,
            "reason": f"approvazione non scritta: {type(exc).__name__}: {exc}",
        }
    return {"ok": True, "count": payload["count"], "job": directory.name}


def font_pick(root: Path, index: Optional[int] = None, family: str = "") -> Dict[str, Any]:
    """Registra la famiglia scelta dalla UI per un lavoro.

    Solo una famiglia che la proposta contiene davvero viene accettata: un nome
    inventato non scrive niente, cosi' il render non parte con un ``Fontname``
    che il job non ha. Il file e' nella forma ``vx.fontchoice/1``.
    """
    jobs = discover(root)
    if not jobs:
        return {"ok": False, "reason": "nessun lavoro in corso"}
    chosen = _default_index(jobs) if index is None else max(0, min(int(index), len(jobs) - 1))
    directory = jobs[chosen]
    proposal = _read_json(directory / FONT_PROPOSAL_NAME)
    raw = proposal.get("candidates")
    families = [
        str(item.get("family") or "")
        for item in (raw if isinstance(raw, list) else [])
        if isinstance(item, Mapping)
    ]
    wanted = str(family or "").strip()
    if not wanted or wanted not in families:
        return {"ok": False, "reason": "famiglia non proposta"}
    payload = {
        "schema": FONT_CHOICE_SCHEMA,
        "family": wanted,
        "chosen_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "from": "ui",
    }
    try:
        (directory / FONT_CHOICE_NAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except OSError as exc:
        degrade.note("serve", exc)
        return {
            "ok": False,
            "degraded": True,
            "reason": f"scelta non scritta: {type(exc).__name__}: {exc}",
        }
    return {"ok": True, "chosen": wanted}


#: Il disegno di ripiego, se i file della UI non ci sono ancora.
PAGE = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<title>vx - lavoro in corso</title></head><body>
<h1>Lavoro in corso</h1><p id="status">carico…</p>
<script>
setInterval(async () => {
  const s = await (await fetch('/api/state', {cache:'no-store'})).json();
  document.getElementById('status').textContent =
    (s.prompt || '') + ' — ' + s.percent + '% ' + (s.note || '');
}, 1000);
</script></body></html>
"""


def make_handler(root: Optional[Path], session_mode: bool = False):
    def query(path: str) -> tuple:
        parsed = urllib.parse.urlsplit(path)
        params = urllib.parse.parse_qs(parsed.query)
        raw = (params.get("job") or [None])[0]
        try:
            index = int(raw) if raw is not None else None
        except ValueError:
            index = None
        return parsed.path, index

    def chosen_session(path: str) -> Optional[str]:
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        return (params.get("session") or [None])[0]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D102 - silence the console
            pass

        def _session_id(self) -> Optional[str]:
            return chosen_session(self.path)

        def _root(self) -> Path:
            """In modalita' sessione il lavoro e' quello dell'ultimo evento."""
            if not session_mode:
                return root if root is not None else Path.cwd()
            job = _session_job(self._session_id())
            return job if job is not None else Path.cwd()

        def _send(self, body: bytes, content_type: str, status: int = 200,
                  extra: Optional[Dict[str, str]] = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: Any, status: int = 200) -> None:
            """Il JSON, compresso quando il client dichiara ``Accept-Encoding: gzip``.

            Con 40 lavori lo stato sfiora i 100 KB e la pagina lo chiede ogni
            secondo: gzip lo porta a una frazione. Un client che non lo chiede
            (o un corpo gia' minuscolo) riceve byte identici a prima.
            """
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {"Vary": "Accept-Encoding"}
            accepted = str(self.headers.get("Accept-Encoding") or "").lower()
            if "gzip" in accepted and len(body) > 512:
                body = gzip.compress(body, compresslevel=6)
                headers["Content-Encoding"] = "gzip"
            self._send(body, "application/json; charset=utf-8", status=status, extra=headers)

        def do_GET(self):  # noqa: D102
            """Ogni lettura risponde 200 o 404: un errore non diventa un 500 muto."""
            try:
                self._do_get()
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:  # noqa: BLE001 - la UI legge zeri, non un crash
                degrade.note("serve", exc)
                try:
                    body = json.dumps(
                        {
                            "ok": False,
                            "degraded": True,
                            "reason": f"{type(exc).__name__}: {exc}",
                        },
                        ensure_ascii=False,
                    ).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8")
                except Exception:  # noqa: BLE001 - la connessione e' gia' caduta
                    pass

        def _do_get(self):  # noqa: D102
            path, index = query(self.path)
            if path in ("/", "/index.html"):
                body = _ui("index.html") or PAGE.encode("utf-8")
                self._send(body, "text/html; charset=utf-8")
                return
            if path.startswith("/ui/"):
                name = Path(path[len("/ui/"):]).name
                body = _ui(name)
                if body is None:
                    self._send(b"not found", "text/plain", status=404)
                    return
                self._send(body, UI_FILES[name])
                return
            if path == "/api/state":
                try:
                    if session_mode:
                        payload = session_state(self._session_id())
                    else:
                        payload = state(self._root(), index)
                except Exception as exc:  # noqa: BLE001 - la vista legge zeri
                    degrade.note("serve", exc)
                    payload = (
                        _empty_session_state("stato non leggibile")
                        if session_mode
                        else _empty_job_payload(self._root(), f"stato non leggibile: {type(exc).__name__}: {exc}")
                    )
                    payload["degraded"] = True
                self._send_json(payload)
                return
            if path in ("/result", "/result.mp4"):
                self._result(index)
                return
            if path.startswith("/frames/"):
                self._frame(path[len("/frames/"):], index)
                return
            if path.startswith("/shots/"):
                self._shot(path[len("/shots/"):], index)
                return
            if path.startswith(LIVE_PREFIX):
                self._live_chunk(path[len(LIVE_PREFIX):], index)
                return
            if path.startswith(FONT_SAMPLE_PREFIX):
                self._font_sample(path[len(FONT_SAMPLE_PREFIX):], index)
                return
            self._send(b"not found", "text/plain", status=404)

        def _font_sample(self, raw: str, index: Optional[int]) -> None:
            """Il PNG del campione, solo dalla cartella ``fonts`` del lavoro.

            Il nome e' ridotto a ``Path(name).name`` e deve finire in ``.png``:
            un percorso con ``..`` non esce dalla cartella, un altro tipo di
            file non si serve.
            """
            jobs = discover(self._root())
            if not jobs:
                self._send(b"not found", "text/plain", status=404)
                return
            chosen = _default_index(jobs) if index is None else max(0, min(int(index), len(jobs) - 1))
            name = Path(urllib.parse.unquote(raw)).name
            if not name.lower().endswith(".png"):
                self._send(b"not found", "text/plain", status=404)
                return
            candidate = jobs[chosen] / "fonts" / name
            if not candidate.is_file():
                self._send(b"not found", "text/plain", status=404)
                return
            self._send(candidate.read_bytes(), "image/png")

        def _frame(self, raw: str, index: Optional[int]) -> None:
            jobs = discover(self._root())
            if not jobs:
                self._send(b"not found", "text/plain", status=404)
                return
            chosen = _default_index(jobs) if index is None else max(0, min(int(index), len(jobs) - 1))
            name = Path(urllib.parse.unquote(raw)).name
            candidate = _data_dir(jobs[chosen]) / "keyframes" / name
            if not candidate.is_file():
                self._send(b"not found", "text/plain", status=404)
                return
            self._send(candidate.read_bytes(), "image/png")

        def _shot(self, raw: str, index: Optional[int]) -> None:
            """Il fotogramma di uno shot: miniatura pronta, cache, o un frame nuovo.

            L'indice e' un intero e il file sta sempre dentro la workdir. Durante
            un render non si estrae niente: ffmpeg ha gia' la CPU occupata, e la
            risposta lo dice invece di restare appesa.
            """
            jobs = discover(self._root())
            if not jobs:
                self._send("nessun lavoro".encode("utf-8"), "text/plain; charset=utf-8",
                           status=404)
                return
            chosen = _default_index(jobs) if index is None else max(0, min(int(index), len(jobs) - 1))
            directory = jobs[chosen]
            data_dir = _data_dir(directory)
            plan = _read_json(data_dir / "plan.json")
            records = _shot_records(directory, data_dir, plan)
            try:
                shot_index = int(urllib.parse.unquote(raw).strip())
            except (TypeError, ValueError):
                self._send("indice shot non valido".encode("utf-8"),
                           "text/plain; charset=utf-8", status=404)
                return
            if shot_index < 0 or shot_index >= len(records):
                self._send("shot fuori intervallo".encode("utf-8"),
                           "text/plain; charset=utf-8", status=404)
                return

            record = records[shot_index]
            nearest = _nearest_thumb(_thumb_files(directory, data_dir), record["t_in"])
            if nearest is not None:
                try:
                    self._send(nearest.read_bytes(), "image/jpeg")
                    return
                except OSError:
                    pass

            cache = data_dir / SHOT_DIR / f"{shot_index}.jpg"
            try:
                if cache.is_file() and cache.stat().st_size > 0:
                    self._send(cache.read_bytes(), "image/jpeg")
                    return
            except OSError:
                pass

            if _render_active(directory, data_dir):
                self._send("fotogramma non disponibile durante il render".encode("utf-8"),
                           "text/plain; charset=utf-8", status=404)
                return

            source = _resolve_source(record.get("source_path"), directory, data_dir)
            if source is None:
                self._send("sorgente del fotogramma non nota".encode("utf-8"),
                           "text/plain; charset=utf-8", status=404)
                return
            if not _extract_shot(source, float(record.get("src_in") or 0.0), cache):
                self._send("fotogramma non disponibile".encode("utf-8"),
                           "text/plain; charset=utf-8", status=404)
                return
            self._send(cache.read_bytes(), "image/jpeg")

        def _live_chunk(self, raw: str, index: Optional[int]) -> None:
            """Un pezzo pubblicato da ``--live``, con Range.

            Solo nomi ``chunk_NNN.mp4`` e solo dentro ``<workdir>/live``: il nome
            non passa da ``Path.name`` (che accetterebbe ``a/../b``), quindi un
            percorso con separatori o ``..`` non corrisponde e resta 404.
            """
            name = urllib.parse.unquote(raw)
            if not LIVE_NAME.fullmatch(name):
                self._send(b"not found", "text/plain", status=404)
                return
            if session_mode:
                candidate = session_chunk(self._session_id(), name)
                if candidate is None:
                    self._send(b"not found", "text/plain", status=404)
                    return
                self._serve_video(candidate)
                return
            jobs = discover(self._root())
            if not jobs:
                self._send(b"not found", "text/plain", status=404)
                return
            chosen = _default_index(jobs) if index is None else max(0, min(int(index), len(jobs) - 1))
            directory = jobs[chosen]
            data_dir = _data_dir(directory)
            for folder in _live_folders(directory, data_dir):
                if not (folder / LIVE_MANIFEST).is_file():
                    continue
                try:
                    base = folder.resolve()
                    candidate = (folder / name).resolve()
                    inside = candidate == base or base in candidate.parents
                    exists = inside and candidate.is_file()
                except OSError:
                    inside, exists = False, False
                if inside and exists:
                    self._serve_video(candidate)
                    return
            self._send(b"not found", "text/plain", status=404)

        def _serve_video(self, path: Path) -> None:
            """Il file video, con Range: il browser deve poter scorrere."""
            try:
                size = path.stat().st_size
            except OSError:
                self._send(b"not found", "text/plain", status=404)
                return
            if size <= 0:
                self._send(b"not found", "text/plain", status=404)
                return
            start, end, status = 0, size - 1, 200
            header = self.headers.get("Range")
            if header and header.startswith("bytes="):
                chunk = header[len("bytes="):].split(",")[0].strip()
                first, _, last = chunk.partition("-")
                try:
                    start = int(first) if first else 0
                    end = int(last) if last else size - 1
                except ValueError:
                    start, end = 0, size - 1
                start = max(0, min(start, size - 1))
                end = max(start, min(end, size - 1))
                status = 206
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    block = handle.read(min(65536, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)

        def _result(self, index: Optional[int]) -> None:
            """Il master, con Range: il browser deve poter scorrere."""
            if session_mode:
                path = session_master(self._session_id())
                if path is None:
                    self._send(b"il master non esiste ancora", "text/plain", status=404)
                    return
                self._serve_video(path)
                return
            jobs = discover(self._root())
            if not jobs:
                self._send(b"nessun lavoro", "text/plain", status=404)
                return
            chosen = _default_index(jobs) if index is None else max(0, min(int(index), len(jobs) - 1))
            progress = _read_json(jobs[chosen] / "progress.json")
            path = _master_path(jobs[chosen], progress)
            if path is None or not path.is_file() or path.stat().st_size == 0:
                self._send(b"il master non esiste ancora", "text/plain", status=404)
                return
            self._serve_video(path)

        def do_POST(self):  # noqa: D102
            """Un giudizio o un'approvazione che fallisce risponde, non chiude."""
            try:
                self._do_post()
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:  # noqa: BLE001 - la UI legge il motivo
                degrade.note("serve", exc)
                try:
                    body = json.dumps(
                        {
                            "ok": False,
                            "degraded": True,
                            "reason": f"{type(exc).__name__}: {exc}",
                        },
                        ensure_ascii=False,
                    ).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8")
                except Exception:  # noqa: BLE001
                    pass

        def _do_post(self):  # noqa: D102
            path, index = query(self.path)
            session_id = self._session_id()
            if path == "/api/approve":
                answer = (
                    session_approve(session_id)
                    if session_mode
                    else approve(self._root(), index)
                )
                self._send(json.dumps(answer, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if path == "/api/reveal":
                answer = (
                    session_reveal(session_id)
                    if session_mode
                    else reveal(self._root(), index)
                )
                self._send(json.dumps(answer, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if path == "/api/font-pick":
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                family = (params.get("family") or [""])[0]
                answer = (
                    session_font_pick(session_id, family)
                    if session_mode
                    else font_pick(self._root(), index, family)
                )
                self._send(json.dumps(answer, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if path == "/api/plan-choice":
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                choice = (params.get("choice") or [""])[0]
                answer = (
                    session_plan_choice(session_id, choice)
                    if session_mode
                    else _write_plan_choice(_data_dir(self._root()), choice)
                )
                self._send(json.dumps(answer, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if path == "/api/answer":
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                qid = (params.get("id") or [""])[0]
                value = (params.get("value") or [""])[0]
                if session_mode:
                    job = _session_job(session_id)
                else:
                    jobs = discover(self._root())
                    if jobs:
                        chosen = (
                            _default_index(jobs)
                            if index is None
                            else max(0, min(int(index), len(jobs) - 1))
                        )
                        job = jobs[chosen]
                    else:
                        job = None
                if job is None:
                    answer = {"ok": False, "id": qid, "reason": "nessun lavoro"}
                else:
                    folder = _answer_workdir(job, _data_dir(job), qid)
                    answer = ask.answer(folder, qid, value)
                    answer["job"] = job.name
                self._send(json.dumps(answer, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if path == "/api/feedback":
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                rating = (params.get("rating") or [""])[0]
                note = (params.get("note") or [""])[0]
                answer = (
                    session_feedback(session_id, rating, note)
                    if session_mode
                    else add_feedback(self._root(), index, rating, note)
                )
                self._send(json.dumps(answer, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            self._send(b"not found", "text/plain", status=404)

    return Handler


class Server:
    """The server, started in a background thread on port 0 by default."""

    def __init__(
        self,
        root: Optional[Path] = None,
        port: int = 0,
        session: bool = False,
    ) -> None:
        self.root = Path(root) if root is not None else None
        self.session = bool(session) or self.root is None
        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", int(port)), make_handler(self.root, self.session)
        )
        self.port = int(self.httpd.server_address[1])
        self.thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> "Server":
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


__all__ = ["ACTIVE_SEC", "FONT_CHOICE_NAME", "FONT_CHOICE_SCHEMA", "FONT_PROPOSAL_NAME",
           "FONT_SAMPLE_PREFIX", "LIVE_DIR", "LIVE_MANIFEST", "LIVE_PREFIX",
           "MAX_DEPTH", "PAGE", "RECENT_SEC", "REVEAL", "RENDER_FRESH_SEC",
           "SHOT_DIR", "SHOT_LIMIT", "Server", "UI_DIR", "UI_FILES", "add_feedback",
           "approve", "discover", "font_pick", "live_state", "make_handler",
           "render_state", "reveal", "reveal_native", "session_approve",
           "session_chunk", "session_feedback", "session_font_pick",
           "session_master", "session_plan_choice", "session_reveal",
           "session_state", "shots", "state", "timeline"]
