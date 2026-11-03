"""vx.serve — la UI locale: guardi i lavori e approvi i fotogrammi.

Un server stdlib su `127.0.0.1`. Con **un** lavoro si comporta come sempre; con
**piu'** lavori in parallelo ne scopre quanti sono sotto la cartella indicata
(fino a due livelli) e li espone tutti:

* ``/``                    la pagina, che chiede lo stato ogni secondo;
* ``/api/state?job=N``     lo stato: fase, percentuale, fotogrammi, approvazione,
                           il master, gli ``shots`` con sorgente ed effetti, e la
                           lista ``jobs``;
* ``/api/approve?job=N``   POST che scrive il record **nella forma di keygate**;
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

from . import keygate

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
JOB_MARKER_FILES = ("source.json", "report.json", "plan.json")

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

#: I file ``-progress`` di ffmpeg: uno per ``auto``, uno per il worker.
RENDER_PROGRESS_FILES = (
    ("render", "ffmpeg-progress.txt"),
    ("", "ffprogress.log"),
)

#: Un progress file piu' vecchio di cosi' appartiene a un render finito.
RENDER_FRESH_SEC = 30.0

#: ``thumb_00_002.500.jpg``: l'istante campionato sta nel nome.
THUMB_NAME = re.compile(r"^thumb_.*_(\d+(?:\.\d+)?)\.jpg$", re.IGNORECASE)


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


def _looks_like_job(directory: Path) -> bool:
    """Una cartella e' un lavoro se ha il panel o i segni veri della skill.

    Il riconoscimento e' stretto di proposito: uno solo tra ``stage_00/``,
    ``proxy/``, ``out/``, ``source.json``, ``report.json``, ``plan.json``. Un
    video sciolto in una cartella resta fuori.
    """
    if (directory / "progress.json").is_file():
        return True
    if any((directory / name).is_dir() for name in JOB_MARKER_DIRS):
        return True
    return any((directory / name).is_file() for name in JOB_MARKER_FILES)


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
    return directory


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
    return candidate.read_bytes() if candidate.is_file() else None


def discover(root: Path) -> List[Path]:
    """Le cartelle di lavoro sotto ``root``: quella stessa, poi i figli.

    Un lavoro e' una cartella con ``progress.json`` **oppure** con i segni veri
    della skill (``stage_00/``, ``proxy/``, ``out/``, ``source.json``,
    ``report.json``, ``plan.json``): cosi' anche un lavoro condotto a mano con
    ``ingest`` compare, pur senza panel. L'ordine e' per data di modifica, il
    piu' recente prima; per un lavoro dedotto vale il segno piu' recente.
    """
    root = Path(root)
    found: List[Path] = []
    if _looks_like_job(root):
        found.append(root)
    known = [path.resolve() for path in found]
    for level in range(1, MAX_DEPTH + 1):
        for candidate in root.glob("/".join(["*"] * level)):
            if not candidate.is_dir() or not _looks_like_job(candidate):
                continue
            resolved = candidate.resolve()
            # I file interni di un lavoro (es. ``work/plan.json``) non sono un
            # altro lavoro: si scende solo fuori dai lavori gia' riconosciuti.
            # Un ``progress.json`` esplicito invece vale sempre.
            if not (candidate / "progress.json").is_file() and any(
                parent in known for parent in resolved.parents
            ):
                continue
            found.append(candidate)
            known.append(resolved)
    unique = {path.resolve(): path for path in found}

    def stamp(path: Path) -> float:
        panel = path / "progress.json"
        if panel.is_file():
            try:
                return panel.stat().st_mtime
            except OSError:
                return 0.0
        stamps: List[float] = []
        for name in JOB_MARKER_DIRS + JOB_MARKER_FILES:
            try:
                stamps.append((path / name).stat().st_mtime)
            except OSError:
                continue
        return max(stamps) if stamps else 0.0

    return sorted(unique.values(), key=stamp, reverse=True)


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


def _outcome(
    *,
    percent: float,
    current: Any,
    note: str,
    ready: bool,
    waiting: bool,
) -> str:
    """L'esito del lavoro, non solo la percentuale.

    ``percent`` racconta quanto si e' visto; ``outcome`` racconta come e'
    finita, ed e' l'unica cosa che la UI puo' usare per dire "concluso". Un
    master annunciato ma assente non e' un lavoro concluso: al massimo e'
    ``incomplete``, e se la nota dice che il lavoro si e' fermato e' ``stopped``.
    ``ready`` arriva da ``master_ready``: il file e' la verita', la nota no.
    """
    lowered = str(note or "").lower()
    if "fermato" in lowered:
        return "stopped"
    if any(word in lowered for word in ("errore", "fallit", "eccezione")):
        return "failed"
    if current is not None or waiting:
        return "running"
    if ready:
        return "done"
    if float(_number(percent) or 0.0) >= 100.0:
        return "incomplete"
    return "idle"


def _job_state(directory: Path) -> Dict[str, Any]:
    """Lo stato del lavoro: pubblicato se c'e' il panel, altrimenti dedotto.

    Un lavoro senza ``progress.json`` non finge una percentuale: ``percent`` e'
    zero, ``current`` e' nullo, la ``note`` dice che manca il progresso, le fasi
    sono quelle che i file presenti raccontano e il master vale solo se il file
    d'uscita esiste davvero. ``progress_source`` dichiara quale dei due casi e'.
    """
    published = (directory / "progress.json").is_file()
    derived = not published
    progress = _read_json(directory / "progress.json") if published else {}
    data_dir = _data_dir(directory)
    plan = _read_json(data_dir / "plan.json")
    frames_dir = data_dir / "keyframes"
    frames = [
        {"name": item.name, "bytes": item.stat().st_size}
        for item in sorted(frames_dir.glob("*.png"))
    ] if frames_dir.is_dir() else []
    pending = _read_json(frames_dir / "pending.json")
    approval = _approval_state(data_dir, pending)
    font = _empty_font() if derived else _font_state(directory)
    master = _master_path(directory, progress)
    ready = bool(master is not None and master.is_file() and master.stat().st_size > 0)
    note = progress.get("note") or (
        "lavoro vx senza progresso pubblicato" if derived else ""
    )
    percent = progress.get("percent") or 0.0
    outcome = _outcome(
        percent=percent,
        current=progress.get("current"),
        note=note,
        ready=ready,
        waiting=bool(approval["waiting"]) or bool(font.get("pending")),
    )
    return {
        "job": directory.name,
        "path": str(directory),
        "prompt": progress.get("prompt") or "",
        "clips": progress.get("clips") or 0,
        "percent": percent,
        "elapsed_sec": progress.get("elapsed_sec") or 0,
        "current": progress.get("current"),
        "note": note,
        "outcome": outcome,
        "phases": _derived_phases(directory) if derived else (progress.get("phases") or []),
        "journal": [] if derived else list(progress.get("journal") or []),
        "frames": frames,
        "approved": bool(approval["approved"]),
        "approval": approval,
        "master": master.name if ready and master is not None else "",
        "master_ready": ready,
        "master_bytes": master.stat().st_size if ready and master is not None else 0,
        "render": _empty_render() if derived else render_state(data_dir, plan),
        "timeline": [] if derived else timeline(plan),
        "shots": shots(directory, plan, data_dir=data_dir),
        "font": font,
        "progress_source": "derived" if derived else "json",
    }


def state(root: Path, index: Optional[int] = None) -> Dict[str, Any]:
    """Lo stato del lavoro scelto, piu' l'elenco di tutti quelli vivi."""
    jobs = discover(root)
    if not jobs:
        return {
            "job": "",
            "path": str(root),
            "prompt": "",
            "clips": 0,
            "percent": 0.0,
            "elapsed_sec": 0,
            "current": None,
            "note": "",
            "outcome": "idle",
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
            "font": _empty_font(),
            "progress_source": "",
            "jobs": [],
            "selected": 0,
        }
    chosen = 0 if index is None else max(0, min(int(index), len(jobs) - 1))
    payload = _job_state(jobs[chosen])
    entries = []
    for position, path in enumerate(jobs):
        item = _job_state(path)
        entries.append(
            {
                "index": position,
                "job": item["job"],
                "prompt": item["prompt"],
                "percent": item["percent"],
                "current": item["current"],
                "progress_source": item["progress_source"],
            }
        )
    payload["jobs"] = entries
    payload["selected"] = chosen
    return payload


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
    chosen = 0 if index is None else max(0, min(int(index), len(jobs) - 1))
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
    chosen = 0 if index is None else max(0, min(int(index), len(jobs) - 1))
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
    (data_dir / keygate.RECORD_NAME).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    # L'attesa e' finita: il segnale che il render e' fermo non deve restare.
    (data_dir / "keyframes" / KEYFRAMES_WAITING_NAME).unlink(missing_ok=True)
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
    chosen = 0 if index is None else max(0, min(int(index), len(jobs) - 1))
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
    (directory / FONT_CHOICE_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
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


def make_handler(root: Path):
    def query(path: str) -> tuple:
        parsed = urllib.parse.urlsplit(path)
        params = urllib.parse.parse_qs(parsed.query)
        raw = (params.get("job") or [None])[0]
        try:
            index = int(raw) if raw is not None else None
        except ValueError:
            index = None
        return parsed.path, index

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D102 - silence the console
            pass

        def _send(self, body: bytes, content_type: str, status: int = 200,
                  extra: Optional[Dict[str, str]] = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: D102
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
                self._send(json.dumps(state(root, index), ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
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
            jobs = discover(root)
            if not jobs:
                self._send(b"not found", "text/plain", status=404)
                return
            chosen = 0 if index is None else max(0, min(int(index), len(jobs) - 1))
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
            jobs = discover(root)
            if not jobs:
                self._send(b"not found", "text/plain", status=404)
                return
            chosen = 0 if index is None else max(0, min(int(index), len(jobs) - 1))
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
            jobs = discover(root)
            if not jobs:
                self._send("nessun lavoro".encode("utf-8"), "text/plain; charset=utf-8",
                           status=404)
                return
            chosen = 0 if index is None else max(0, min(int(index), len(jobs) - 1))
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

        def _result(self, index: Optional[int]) -> None:
            """Il master, con Range: il browser deve poter scorrere."""
            jobs = discover(root)
            if not jobs:
                self._send(b"nessun lavoro", "text/plain", status=404)
                return
            chosen = 0 if index is None else max(0, min(int(index), len(jobs) - 1))
            progress = _read_json(jobs[chosen] / "progress.json")
            path = _master_path(jobs[chosen], progress)
            if path is None or not path.is_file() or path.stat().st_size == 0:
                self._send(b"il master non esiste ancora", "text/plain", status=404)
                return
            size = path.stat().st_size
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

        def do_POST(self):  # noqa: D102
            path, index = query(self.path)
            if path == "/api/approve":
                self._send(json.dumps(approve(root, index), ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if path == "/api/reveal":
                self._send(json.dumps(reveal(root, index), ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if path == "/api/font-pick":
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                family = (params.get("family") or [""])[0]
                answer = font_pick(root, index, family)
                self._send(json.dumps(answer, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            self._send(b"not found", "text/plain", status=404)

    return Handler


class Server:
    """The server, started in a background thread on port 0 by default."""

    def __init__(self, root: Path, port: int = 0) -> None:
        self.root = Path(root)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", int(port)), make_handler(self.root))
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


__all__ = ["FONT_CHOICE_NAME", "FONT_CHOICE_SCHEMA", "FONT_PROPOSAL_NAME",
           "FONT_SAMPLE_PREFIX", "MAX_DEPTH", "PAGE", "REVEAL", "RENDER_FRESH_SEC",
           "SHOT_DIR", "SHOT_LIMIT", "Server", "UI_DIR", "UI_FILES", "approve",
           "discover", "font_pick", "make_handler", "render_state", "reveal",
           "reveal_native", "shots", "state", "timeline"]
