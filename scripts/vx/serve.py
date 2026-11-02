"""vx.serve — la UI locale: guardi il lavoro e approvi i fotogrammi.

Un server stdlib su `127.0.0.1`, tre cose e niente di piu':

* ``/``            la pagina, che chiede lo stato ogni secondo e si ridisegna;
* ``/api/state``   lo stato in JSON (fase, percentuale, fotogrammi, approvazione);
* ``/api/approve`` un POST che scrive il record di approvazione **nella forma
                   che ``keygate`` legge**, con lo stesso digest dei fotogrammi.

Perche' non e' un secondo formato: l'approvazione che nasce da un clic deve essere
la stessa che nasce dalla CLI, altrimenti il cancello avrebbe due verita'.

Cosa NON fa, e va detto: non lancia il render e non lo sblocca da sola. Il render
legge il record quando il percorso lo chiama; agganciare il cancello al render e'
un passo separato, con la sua prova.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from . import keygate

PAGE = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<title>vx - lavoro in corso</title>
<style>
:root { color-scheme: dark; }
body { margin:0; padding:24px; background:#111214; color:#e8e6e3;
       font:15px/1.5 ui-monospace,"JetBrains Mono",Consolas,monospace; }
h1 { font-size:18px; margin:0 0 2px; }
h2 { font-size:14px; margin:26px 0 10px; color:#8b8f96; font-weight:600;
     text-transform:uppercase; letter-spacing:.08em; }
.muted { color:#8b8f96; }
.bar { height:6px; background:#23262b; border-radius:3px; margin:16px 0 8px; overflow:hidden; }
.bar > i { display:block; height:100%; background:#4ea1ff; transition:width .4s ease; }
.frames { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:10px; }
figure { margin:0; background:#1a1c20; border-radius:6px; overflow:hidden; }
figure img { width:100%; display:block; }
figcaption { padding:6px 8px; font-size:12px; color:#8b8f96; }
button { margin-top:16px; padding:10px 18px; font:inherit; border:0; border-radius:6px;
         background:#4ea1ff; color:#08131f; font-weight:700; cursor:pointer; }
button[disabled] { background:#2a2e34; color:#6d727a; cursor:default; }
.ok { color:#9fe3a6; }
</style></head><body>
<h1 id="title">Lavoro in corso</h1>
<div class="muted" id="meta"></div>
<div class="bar"><i id="fill" style="width:0%"></i></div>
<div class="muted" id="status">carico...</div>
<h2>Fotogrammi chiave</h2>
<div class="frames" id="frames"></div>
<button id="approve" disabled>Approva questi fotogrammi</button>
<div class="muted" id="verdict" style="margin-top:10px"></div>
<script>
async function tick() {
  const r = await fetch('/api/state', {cache: 'no-store'});
  const s = await r.json();
  document.getElementById('title').textContent = s.prompt || 'Lavoro in corso';
  document.getElementById('meta').textContent =
    (s.clips ? s.clips + ' clip · ' : '') + s.elapsed_sec + ' secondi';
  document.getElementById('fill').style.width = s.percent + '%';
  const f = document.getElementById('status');
  f.textContent = s.current ? s.phases.find(p => p.key === s.current)?.label || s.current
                            : (s.note || 'in attesa');
  f.className = s.current ? 'muted' : 'ok';
  const box = document.getElementById('frames');
  box.innerHTML = '';
  for (const fr of s.frames) {
    const fig = document.createElement('figure');
    fig.innerHTML = '<img src="/frames/' + encodeURIComponent(fr.name) + '" alt="">' +
      '<figcaption>' + fr.name + '</figcaption>';
    box.appendChild(fig);
  }
  const b = document.getElementById('approve');
  b.disabled = s.approved || s.frames.length === 0;
  b.textContent = s.approved ? 'Approvati' : 'Approva questi fotogrammi';
  document.getElementById('verdict').textContent = s.approved
    ? 'Il cancello e\' aperto per questo lavoro.' : '';
}
document.getElementById('approve').onclick = async () => {
  await fetch('/api/approve', {method: 'POST'});
  tick();
};
setInterval(tick, 1000); tick();
</script></body></html>
"""


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def state(root: Path) -> Dict[str, Any]:
    """Lo stato del lavoro, letto dai file che la skill scrive gia'."""
    root = Path(root)
    progress = _read_json(root / "progress.json")
    if not progress:
        progress = _read_json(root / "master-auto" / "progress.json")
    base = root if (root / "progress.json").is_file() else root / "master-auto"
    frames_dir = base / "keyframes"
    frames = []
    if frames_dir.is_dir():
        frames = [
            {"name": item.name, "bytes": item.stat().st_size}
            for item in sorted(frames_dir.glob("*.png"))
        ]
    record = keygate.read(base / keygate.RECORD_NAME)
    master_text = str(progress.get("master") or "")
    master = Path(master_text) if master_text else None
    ready = bool(master and master.is_file() and master.stat().st_size > 0)
    return {
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
    }


def approve(root: Path) -> Dict[str, Any]:
    """Scrive il record nella forma di ``keygate``, con il digest del piano.

    Il digest arriva da ``keyframes/pending.json``, che e' quello che la skill ha
    calcolato estraendo i fotogrammi: qui non si ricalcola niente, si firma quello
    che si e' visto.
    """
    root = Path(root)
    base = root if (root / "progress.json").is_file() else root / "master-auto"
    pending = _read_json(base / "keyframes" / "pending.json")
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
    (base / keygate.RECORD_NAME).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {"ok": True, "count": payload["count"]}


UI_DIR = Path(__file__).resolve().parent / "ui"
#: Solo questi file: il server non deve poter leggere altro dal disco.
UI_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
}


def _ui(name: str):
    """The bytes of one whitelisted UI file, or ``None``."""
    if name not in UI_FILES:
        return None
    candidate = UI_DIR / name
    if not candidate.is_file():
        return None
    return candidate.read_bytes()


def make_handler(root: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D102 - silence the console
            pass

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: D102
            if self.path in ("/", "/index.html"):
                body = _ui("index.html") or PAGE.encode("utf-8")
                self._send(body, "text/html; charset=utf-8")
                return
            if self.path.startswith("/ui/"):
                name = Path(self.path[len("/ui/"):]).name
                body = _ui(name)
                if body is None:
                    self._send(b"not found", "text/plain", status=404)
                    return
                self._send(body, UI_FILES[name])
                return
            if self.path == "/api/state":
                self._send(json.dumps(state(root), ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if self.path in ("/result", "/result.mp4"):
                self._result()
                return
            if self.path.startswith("/frames/"):
                name = Path(self.path[len("/frames/"):]).name
                candidate = root / "keyframes" / name
                if not candidate.is_file():
                    candidate = root / "master-auto" / "keyframes" / name
                if candidate.is_file():
                    self._send(candidate.read_bytes(), "image/png")
                else:
                    self._send(b"not found", "text/plain", status=404)
                return
            self._send(b"not found", "text/plain", status=404)

        def _result(self) -> None:
            """Il master, con Range: il browser deve poter scorrere."""
            root_state = state(root)
            master_text = str(_read_json(root / "progress.json").get("master") or "")
            if not master_text:
                master_text = str(_read_json(root / "master-auto" / "progress.json").get("master") or "")
            path = Path(master_text) if master_text else None
            if not root_state.get("master_ready") or path is None or not path.is_file():
                body = b"il master non esiste ancora"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            size = path.stat().st_size
            start, end = 0, size - 1
            status = 200
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
            if self.path == "/api/approve":
                result = approve(root)
                self._send(json.dumps(result, ensure_ascii=False).encode("utf-8"),
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


__all__ = ["PAGE", "Server", "approve", "make_handler", "state"]
