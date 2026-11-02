"""vx.serve — la UI locale: guardi i lavori e approvi i fotogrammi.

Un server stdlib su `127.0.0.1`. Con **un** lavoro si comporta come sempre; con
**piu'** lavori in parallelo ne scopre quanti sono sotto la cartella indicata
(fino a due livelli) e li espone tutti:

* ``/``                    la pagina, che chiede lo stato ogni secondo;
* ``/api/state?job=N``     lo stato: fase, percentuale, fotogrammi, approvazione,
                           il master, e la lista ``jobs``;
* ``/api/approve?job=N``   POST che scrive il record **nella forma di keygate**;
* ``/frames/<nome>?job=N`` il PNG di un fotogramma;
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
import subprocess
import sys
import threading
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

#: La proposta di font e la scelta dell'operatore, scritte nella workdir.
FONT_PROPOSAL_NAME = "font_proposal.json"
FONT_CHOICE_NAME = "font_choice.json"
FONT_CHOICE_SCHEMA = "vx.fontchoice/1"
FONT_SAMPLE_PREFIX = "/fonts/sample/"


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

    Un lavoro e' una cartella che contiene ``progress.json``. L'ordine e' per
    data di modifica, il piu' recente prima, cosi' quando ne parte uno nuovo
    finisce davanti senza che nessuno debba dirlo.
    """
    root = Path(root)
    found: List[Path] = []
    if (root / "progress.json").is_file():
        found.append(root)
    for level in range(1, MAX_DEPTH + 1):
        pattern = "/".join(["*"] * level) + "/progress.json"
        found.extend(path.parent for path in root.glob(pattern))
    unique = {path.resolve(): path for path in found}

    def stamp(path: Path) -> float:
        try:
            return (path / "progress.json").stat().st_mtime
        except OSError:
            return 0.0

    return sorted(unique.values(), key=stamp, reverse=True)


def _job_state(directory: Path) -> Dict[str, Any]:
    progress = _read_json(directory / "progress.json")
    data_dir = _data_dir(directory)
    plan = _read_json(data_dir / "plan.json")
    frames_dir = directory / "keyframes"
    frames = [
        {"name": item.name, "bytes": item.stat().st_size}
        for item in sorted(frames_dir.glob("*.png"))
    ] if frames_dir.is_dir() else []
    record = keygate.read(directory / keygate.RECORD_NAME)
    master_text = str(progress.get("master") or "")
    master = Path(master_text) if master_text else None
    ready = bool(master and master.is_file() and master.stat().st_size > 0)
    return {
        "job": directory.name,
        "path": str(directory),
        "prompt": progress.get("prompt") or "",
        "clips": progress.get("clips") or 0,
        "percent": progress.get("percent") or 0.0,
        "elapsed_sec": progress.get("elapsed_sec") or 0,
        "current": progress.get("current"),
        "note": progress.get("note") or "",
        "phases": progress.get("phases") or [],
        "frames": frames,
        "approved": bool(record and record.get("approved") is True),
        "master": master.name if ready and master is not None else "",
        "master_ready": ready,
        "master_bytes": master.stat().st_size if ready and master is not None else 0,
        "render": render_state(data_dir, plan),
        "timeline": timeline(plan),
        "font": _font_state(directory),
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
            "phases": [],
            "frames": [],
            "approved": False,
            "master": "",
            "master_ready": False,
            "master_bytes": 0,
            "render": _empty_render(),
            "timeline": [],
            "font": _empty_font(),
            "jobs": [],
            "selected": 0,
        }
    chosen = 0 if index is None else max(0, min(int(index), len(jobs) - 1))
    payload = _job_state(jobs[chosen])
    payload["jobs"] = [
        {
            "index": position,
            "job": _job_state(path)["job"],
            "prompt": _read_json(path / "progress.json").get("prompt") or "",
            "percent": _read_json(path / "progress.json").get("percent") or 0.0,
            "current": _read_json(path / "progress.json").get("current"),
        }
        for position, path in enumerate(jobs)
    ]
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

    Si apre **solo** il file registrato in ``progress.json`` di un lavoro
    scoperto: la richiesta sceglie il lavoro, non il percorso.
    """
    jobs = discover(root)
    if not jobs:
        return {"ok": False, "reason": "nessun lavoro"}
    chosen = 0 if index is None else max(0, min(int(index), len(jobs) - 1))
    directory = jobs[chosen]
    text = str(_read_json(directory / "progress.json").get("master") or "")
    master = Path(text) if text else None
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
    pending = _read_json(directory / "keyframes" / "pending.json")
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
    (directory / keygate.RECORD_NAME).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
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
            candidate = jobs[chosen] / "keyframes" / name
            if not candidate.is_file():
                self._send(b"not found", "text/plain", status=404)
                return
            self._send(candidate.read_bytes(), "image/png")

        def _result(self, index: Optional[int]) -> None:
            """Il master, con Range: il browser deve poter scorrere."""
            jobs = discover(root)
            if not jobs:
                self._send(b"nessun lavoro", "text/plain", status=404)
                return
            chosen = 0 if index is None else max(0, min(int(index), len(jobs) - 1))
            progress = _read_json(jobs[chosen] / "progress.json")
            text = str(progress.get("master") or "")
            path = Path(text) if text else None
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
           "FONT_SAMPLE_PREFIX", "MAX_DEPTH", "PAGE", "REVEAL", "Server", "UI_DIR",
           "UI_FILES", "approve", "discover", "font_pick", "make_handler",
           "render_state", "reveal", "reveal_native", "state", "timeline"]
