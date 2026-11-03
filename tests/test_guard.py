"""vx guard: un lavoro e' conforme solo se e' passato davvero dalla pipeline.

G1 - una cartella senza firma vx non e' un lavoro vx (exit 2, nessun'altra voce).
G2 - un master in out/ o nella radice senza report.json e' produzione fuori pipeline.
G3 - index.html + serve.py (o http.server) nella stessa cartella e' una UI a mano.
G4 - uno script .py che chiama ffmpeg via subprocess/os.system e' una pipeline a mano.
G5 - progress.json mancante e' un avviso, non una violazione (i lavori vecchi non l'hanno).
G6 - lo sha256 dichiarato in source.json deve combaciare ancora: altrimenti e' grave.

Niente rete, niente ffmpeg: solo cartelle temporanee e file finti.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import cli, guard  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def run(argv: list) -> tuple:
    """Run the CLI in-process, capturing stdout, and return (rc, text)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


def run_json(directory: Path) -> tuple:
    rc, text = run(["guard", str(directory), "--json"])
    return rc, json.loads(text)


def codes(report: dict, key: str = "violations") -> list:
    return [item["code"] for item in report.get(key) or []]


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def source_json(job: Path, media: Path, sha: str | None = None) -> Path:
    payload = {
        "path": str(media),
        "sha256": sha if sha is not None else hashlib.sha256(media.read_bytes()).hexdigest(),
        "size": media.stat().st_size,
        "mtime": round(media.stat().st_mtime, 3),
    }
    return write(job / "source.json", json.dumps(payload))


def t_conforme(raw: str) -> None:
    """source.json + report.json + out/master.mp4: conforme, exit 0."""
    job = Path(raw) / "conforme"
    media = write(job / "clip.mp4", "sorgente")
    source_json(job, media)
    write(job / "report.json", json.dumps({"ok": True}))
    write(job / "progress.json", json.dumps({"percent": 100.0}))
    write(job / "out" / "master.mp4", "master")
    rc, report = run_json(job)
    check("conforme: exit 0", rc == 0, rc)
    check("conforme: nessuna violazione", codes(report) == [], codes(report))
    check("conforme: tutte le voci presenti", len(report["checks"]) == 6, len(report["checks"]))
    check("conforme: job riconosciuto", report["job"] is True, report["signature"])
    rc_text, text = run(["guard", str(job)])
    check("conforme: uscita leggibile con [ok]", rc_text == 0 and "[ok]" in text, rc_text)
    check("conforme: nessun [!]", "[!]" not in text, text.splitlines()[-1])


def t_master_senza_report(raw: str) -> None:
    """Un master in out/ senza report.json e' produzione fuori dalla pipeline."""
    job = Path(raw) / "senza_report"
    write(job / "out" / "master.mp4", "master")
    rc, report = run_json(job)
    check("master senza report: exit 2", rc == 2, rc)
    check("master senza report: voce G2", "G2" in codes(report), codes(report))
    g2 = next(c for c in report["checks"] if c["code"] == "G2")
    check("master senza report: messaggio giusto", "fuori dalla pipeline" in g2["detail"], g2["detail"])
    check("master senza report: cita il file", "out/master.mp4" in g2["files"], g2["files"])


def t_master_con_deliverables(raw: str) -> None:
    """deliverables.json vale come report.json: nessun falso positivo."""
    job = Path(raw) / "deliverables"
    write(job / "out" / "master.mp4", "master")
    write(job / "deliverables.json", json.dumps({"files": ["out/master.mp4"]}))
    rc, report = run_json(job)
    check("deliverables.json: exit 0", rc == 0, rc)
    check("deliverables.json: nessuna violazione", codes(report) == [], codes(report))


def t_ui_artigianale(raw: str) -> None:
    """index.html + serve.py nella cartella: UI scritta a mano."""
    job = Path(raw) / "ui"
    write(job / "source.json", json.dumps({"path": "", "sha256": ""}))
    write(job / "index.html", "<html><body>pannello</body></html>")
    write(job / "serve.py", "import http.server\nhttp.server.HTTPServer\n")
    rc, report = run_json(job)
    check("ui: exit 2", rc == 2, rc)
    check("ui: voce G3", "G3" in codes(report), codes(report))
    g3 = next(c for c in report["checks"] if c["code"] == "G3")
    check("ui: messaggio 'UI scritta a mano'", "UI scritta a mano" in g3["detail"], g3["detail"])
    check("ui: indica entrambi i file", {"index.html", "serve.py"} <= set(g3["files"]), g3["files"])


def t_ui_http_server(raw: str) -> None:
    """Anche 'http.server' dentro un altro nome di file e' una UI artigianale."""
    job = Path(raw) / "ui_http"
    write(job / "job.json", json.dumps({"kind": "job"}))
    write(job / "panel.html", "<html></html>")
    write(job / "server.py", "from http.server import SimpleHTTPRequestHandler\n")
    rc, report = run_json(job)
    check("http.server: exit 2 e G3", rc == 2 and "G3" in codes(report), codes(report))


def t_ui_senza_server(raw: str) -> None:
    """Un HTML senza server nella stessa cartella non e' una UI artigianale."""
    job = Path(raw) / "html_solo"
    write(job / "job.json", json.dumps({"kind": "job"}))
    write(job / "notes.html", "<html></html>")
    rc, report = run_json(job)
    check("html senza server: nessun G3", "G3" not in codes(report), codes(report))


def t_pipeline_artigianale(raw: str) -> None:
    """Uno script .py che chiama ffmpeg via subprocess e' una pipeline a mano."""
    job = Path(raw) / "pipeline"
    write(job / "job.json", json.dumps({"kind": "job"}))
    write(
        job / "tools" / "run.py",
        "import subprocess\nsubprocess.run(['ffmpeg', '-i', 'a.mp4', 'b.mp4'])\n",
    )
    rc, report = run_json(job)
    check("pipeline: exit 2", rc == 2, rc)
    check("pipeline: voce G4", "G4" in codes(report), codes(report))
    g4 = next(c for c in report["checks"] if c["code"] == "G4")
    check("pipeline: cita lo script", "tools/run.py" in g4["files"], g4["files"])
    check("pipeline: messaggio giusto", "pipeline scritta a mano" in g4["detail"], g4["detail"])


def t_subprocess_senza_ffmpeg(raw: str) -> None:
    """subprocess senza ffmpeg non e' la pipeline: nessun falso positivo."""
    job = Path(raw) / "subprocess_solo"
    write(job / "job.json", json.dumps({"kind": "job"}))
    write(job / "tools" / "git.py", "import subprocess\nsubprocess.run(['git', 'status'])\n")
    rc, report = run_json(job)
    check("subprocess senza ffmpeg: nessun G4", "G4" not in codes(report), codes(report))


def t_video_sciolto(raw: str) -> None:
    """Solo un video nella cartella: non e' un lavoro vx, senza violazioni inventate."""
    job = Path(raw) / "sciolto"
    write(job / "clip.mp4", "video")
    rc, report = run_json(job)
    check("video sciolto: exit 2", rc == 2, rc)
    check("video sciolto: voce G1", codes(report) == ["G1"], codes(report))
    g1 = next(c for c in report["checks"] if c["code"] == "G1")
    check("video sciolto: 'non e' un lavoro vx'", "non e' un lavoro vx" in g1["detail"], g1["detail"])
    check("video sciolto: nessun master inventato", codes(report) == ["G1"], "G2" in codes(report))


def t_sha_sbagliato(raw: str) -> None:
    """source.json con sha256 che non combacia: violazione grave."""
    job = Path(raw) / "sha"
    media = write(job / "clip.mp4", "sorgente originale")
    source_json(job, media, sha="0" * 64)
    write(job / "report.json", json.dumps({"ok": True}))
    rc, report = run_json(job)
    check("sha sbagliato: exit 2", rc == 2, rc)
    check("sha sbagliato: voce G6", "G6" in codes(report), codes(report))
    g6 = next(c for c in report["checks"] if c["code"] == "G6")
    check("sha sbagliato: messaggio 'modificato'", "modificato" in g6["detail"], g6["detail"])


def t_progress_avviso(raw: str) -> None:
    """Senza progress.json resta un avviso: il lavoro e' conforme (exit 0)."""
    job = Path(raw) / "senza_progress"
    media = write(job / "clip.mp4", "sorgente")
    source_json(job, media)
    write(job / "report.json", json.dumps({"ok": True}))
    rc, report = run_json(job)
    check("senza progress: exit 0", rc == 0, rc)
    check("senza progress: avviso G5", codes(report, "warnings") == ["G5"], codes(report, "warnings"))
    check("senza progress: nessuna violazione", codes(report) == [], codes(report))


def t_cartella_inesistente(raw: str) -> None:
    """Una cartella che non esiste e' un errore d'uso: exit 2, nessun crash."""
    rc, text = run(["guard", str(Path(raw) / "manca")])
    check("cartella inesistente: exit 2", rc == 2, rc)


def main() -> int:
    print("vx guard")
    with tempfile.TemporaryDirectory() as raw:
        t_conforme(raw)
        t_master_senza_report(raw)
        t_master_con_deliverables(raw)
        t_ui_artigianale(raw)
        t_ui_http_server(raw)
        t_ui_senza_server(raw)
        t_pipeline_artigianale(raw)
        t_subprocess_senza_ffmpeg(raw)
        t_video_sciolto(raw)
        t_sha_sbagliato(raw)
        t_progress_avviso(raw)
        t_cartella_inesistente(raw)
    print(f"\n{PASS} PASS  {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
