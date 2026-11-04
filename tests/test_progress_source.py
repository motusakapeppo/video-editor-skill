"""La vista e il lavoro condividono una sola fonte di verita'.

Il progresso vive nella **workdir** (accanto a ``plan.json``), la pagina non
inventa uno 0% quando il numero manca o e' vecchio, gli asset non restano in
cache e ``vx up`` si accorge di servire codice piu' vecchio del file su disco.

Avvio: ``python tests/test_progress_source.py``
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import cli, lock, panel, serve  # noqa: E402

PASS = 0
FAIL = 0
TEMP = Path(tempfile.mkdtemp(prefix="vx-progress-"))
#: Nessuna sessione vera del sistema entra in questi test.
os.environ["VX_SESSION_DIR"] = str(TEMP / "sessions")
os.environ.pop("VX_SESSION", None)


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def args_for(**values: object) -> Namespace:
    base = {
        "panel": None,
        "no_panel": False,
        "out": str(TEMP / "out" / "master.mp4"),
        "workdir": None,
        "prompt": "prova",
        "assets": str(TEMP / "assets"),
    }
    base.update(values)
    return Namespace(**base)


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).replace(microsecond=0).isoformat()


def old_file(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


# --------------------------------------------------------------------------- #
# 1. dove vive il pannello di `auto`
# --------------------------------------------------------------------------- #


def check_panel_location() -> None:
    work = TEMP / "work-esplicita"
    args = args_for(workdir=str(work))
    chosen = cli._auto_panel_dir(args, Path(args.workdir))
    check("P1 senza --panel il pannello sta nella workdir risolta",
          chosen == work, chosen)
    check("P2 e non accanto al master",
          chosen != Path(args.out).parent / "master-auto", chosen)

    args = args_for()
    derived = Path(args.out).parent / "master-auto"
    chosen = cli._auto_panel_dir(args, derived)
    check("P3 senza --workdir il default resta la workdir dedotta da --out",
          chosen == derived, chosen)

    custom = TEMP / "pannello-scelto"
    args = args_for(panel=str(custom), workdir=str(work))
    chosen = cli._auto_panel_dir(args, Path(args.workdir))
    check("P4 --panel vince sulla workdir", chosen == custom, chosen)

    # Il pannello scrive davvero i due file dove dice, e la copia accanto al
    # master e' **solo** HTML: il json non si duplica.
    main = TEMP / "pannello-main"
    copy_dir = TEMP / "master-auto"
    widget = panel.Panel(main / "progress.html", prompt="prova")
    widget.copy = copy_dir / "progress.html"
    widget.record(12.0, "render")
    check("P5 progress.json e progress.html finiscono nella workdir",
          (main / "progress.json").is_file() and (main / "progress.html").is_file(),
          sorted(item.name for item in main.iterdir()))
    data = json.loads((main / "progress.json").read_text(encoding="utf-8"))
    check("P6 il json nella workdir e' quello aggiornato",
          data.get("percent") == 12.0, data.get("percent"))
    check("P7 la copia accanto al master e' il solo HTML",
          (copy_dir / "progress.html").is_file()
          and not (copy_dir / "progress.json").exists(),
          sorted(item.name for item in copy_dir.iterdir()))


# --------------------------------------------------------------------------- #
# 2. progresso giovane, vecchio, assente: la pagina non inventa 0%
# --------------------------------------------------------------------------- #


def check_progress_states() -> None:
    serve._cache_clear()

    fresh = TEMP / "giovane"
    fresh.mkdir(parents=True, exist_ok=True)
    (fresh / "progress.json").write_text(
        json.dumps({"percent": 42.0, "current": "render"}), encoding="utf-8"
    )
    state = serve._job_state(fresh)
    block = state["progress"]
    check("G1 progresso giovane: il valore e' la percentuale vera",
          block["value"] == 42.0 and block["source"] == "json" and block["alive"],
          block)
    check("G2 progresso giovane: l'eta' e' quasi zero",
          isinstance(block["age_sec"], (int, float)) and block["age_sec"] < 3,
          block["age_sec"])

    stale_lock = TEMP / "vecchio-con-lock"
    stale_lock.mkdir(parents=True, exist_ok=True)
    (stale_lock / "progress.json").write_text(
        json.dumps({"percent": 42.0, "current": "render"}), encoding="utf-8"
    )
    old_file(stale_lock / "progress.json", 30)
    beat = lock.acquire(stale_lock, stage="render", heartbeat_seconds=60)
    try:
        serve._cache_clear()
        state = serve._job_state(stale_lock)
        block = state["progress"]
        check("G3 progresso vecchio + lock vivo: non e' 0%, e' nessun numero",
              block["value"] is None and block["alive"] is True, block)
        check("G4 il testo esatto e' «sto lavorando · nessun aggiornamento da 30 s»",
              block["why"] == "sto lavorando · nessun aggiornamento da 30 s",
              block["why"])
    finally:
        lock.release(beat)

    absent_lock = TEMP / "assente-con-lock"
    absent_lock.mkdir(parents=True, exist_ok=True)
    beat = lock.acquire(absent_lock, stage="triage", heartbeat_seconds=60)
    try:
        serve._cache_clear()
        block = serve._job_state(absent_lock)["progress"]
        check("G5 progress assente + lock vivo: nessun 0%, il lavoro si dichiara vivo",
              block["value"] is None and block["alive"] is True
              and block["source"] == "lock", block)
        check("G6 il testo dice che sta lavorando",
              "sto lavorando" in block["why"], block["why"])
    finally:
        lock.release(beat)

    absent = TEMP / "assente"
    absent.mkdir(parents=True, exist_ok=True)
    serve._cache_clear()
    block = serve._job_state(absent)["progress"]
    check("G7 senza progresso e senza lock: alive falso e la ragione lo dice",
          block["value"] is None and block["alive"] is False
          and "nessun lavoro in corso" in block["why"], block)

    stale = TEMP / "vecchio-senza-lock"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "progress.json").write_text(
        json.dumps({"percent": 42.0}), encoding="utf-8"
    )
    old_file(stale / "progress.json", 30)
    serve._cache_clear()
    block = serve._job_state(stale)["progress"]
    check("G8 progresso vecchio senza lavoro vivo: niente percentuale, esito onesto",
          block["value"] is None and block["alive"] is False
          and "nessun lavoro in corso" in block["why"], block)


# --------------------------------------------------------------------------- #
# 3. gli asset non restano in cache
# --------------------------------------------------------------------------- #


def check_headers() -> None:
    root = TEMP / "radice"
    job = root / "lavoro"
    job.mkdir(parents=True, exist_ok=True)
    (job / "progress.json").write_text(
        json.dumps({"percent": 25.0, "current": "render", "phases": []}),
        encoding="utf-8",
    )
    server = serve.Server(root, port=0, session=False).start()
    try:
        with urllib.request.urlopen(server.url, timeout=5) as first:
            body_one = first.read()
            index_cache = first.headers.get("Cache-Control") or ""
        with urllib.request.urlopen(server.url, timeout=5) as second:
            body_two = second.read()
        with urllib.request.urlopen(server.url + "ui/app.js", timeout=5) as asset:
            app_body = asset.read()
            app_cache = asset.headers.get("Cache-Control") or ""
        with urllib.request.urlopen(server.url + "api/state", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

        check("H1 index.html e' servito con Cache-Control: no-store",
              "no-store" in index_cache, index_cache)
        check("H2 app.js e' servito con Cache-Control: no-store",
              "no-store" in app_cache, app_cache)
        check("H3 due richieste danno lo stesso contenuto del file su disco",
              body_one == body_two
              and body_two == (serve.UI_DIR / "index.html").read_bytes(), len(body_one))
        check("H4 app.js servito == app.js su disco",
              app_body == (serve.UI_DIR / "app.js").read_bytes(), len(app_body))
        block = payload.get("progress") or {}
        check("H5 /api/state porta progress {value, source, age_sec, alive, why}",
              {"value", "source", "age_sec", "alive", "why"} <= set(block)
              and block.get("value") == 25.0, block)
    finally:
        server.stop()


# --------------------------------------------------------------------------- #
# 4. `vx up` riavvia un daemon che serve codice vecchio
# --------------------------------------------------------------------------- #


def check_restart() -> None:
    touched = TEMP / "ui" / "app.js"
    touched.parent.mkdir(parents=True, exist_ok=True)
    touched.write_text("// nuovo\n", encoding="utf-8")
    now = time.time()
    os.utime(touched, (now, now))
    record = {
        "pid": 4242,
        "port": 8123,
        "url": "http://127.0.0.1:8123/",
        "started_utc": iso(now - 60),
    }
    saved = {
        name: getattr(cli, name)
        for name in (
            "_code_files", "_daemon", "_stop_pid", "_up_file", "_spawn_daemon",
            "_read_up", "_pid_alive", "_answers", "_choose_port", "_log",
        )
    }
    stopped: list = []
    logs: list = []

    class Child:
        def poll(self):
            return None

        def terminate(self):
            return None

    new_record = {
        "pid": 4343,
        "port": 8124,
        "url": "http://127.0.0.1:8124/",
        "started_utc": iso(now + 1),
    }
    try:
        cli._code_files = lambda: [touched]
        cli._daemon = lambda wait=0.0: dict(record)
        cli._stop_pid = lambda pid: stopped.append(pid) or True
        cli._up_file = lambda: TEMP / "up.json"
        cli._spawn_daemon = lambda port: Child()
        cli._read_up = lambda: dict(new_record)
        cli._pid_alive = lambda pid: True
        cli._answers = lambda port, timeout=0.0: True
        cli._choose_port = lambda preferred: 8124
        cli._log = lambda msg: logs.append(str(msg))

        stale = cli._stale_code(record)
        check("R1 il codice toccato dopo l'avvio e' riconosciuto come nuovo",
              stale is not None and stale[0] == touched, stale)
        result = cli._start_up(None)
        check("R2 il vecchio pid viene fermato (solo il suo)",
              stopped == [4242], stopped)
        check("R3 il daemon nuovo ha un altro pid e un nuovo started_utc",
              result.get("pid") == 4343
              and result.get("started_utc") == new_record["started_utc"]
              and result.get("reused") is False, result)
        check("R4 il riavvio viene dichiarato, non taciuto",
              any("riavvio" in line or "riavviato" in line for line in logs), logs)
    finally:
        for name, value in saved.items():
            setattr(cli, name, value)


def main() -> int:
    print("vx.progress — una sola fonte di verita' fra vista e lavoro")
    try:
        check_panel_location()
        check_progress_states()
        check_headers()
        check_restart()
    finally:
        print(f"\n{PASS} PASS / {FAIL} FAIL")
        shutil.rmtree(TEMP, ignore_errors=True)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
