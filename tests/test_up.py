"""vx up / vx down e il battito: la vista vive oltre il singolo lavoro.

Nessuna rete esterna: solo loopback. Il daemon e' un processo vero, quindi il
test lo avvia, lo riusa, ci fa girare un lavoro (`vx doctor`) e lo ferma; il
battito invece si prova con un clock iniettato, per non aspettare dieci secondi
veri per ogni scenario.

Avvio: ``python tests/test_up.py``
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import cli, session  # noqa: E402

PASS = 0
FAIL = 0
TEMP = Path(tempfile.mkdtemp(prefix="vx-up-"))

#: La cartella delle sessioni del test: `up.json` vive qui, non nella workdir.
SESSIONS = TEMP / "sessions"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def env() -> dict:
    values = os.environ.copy()
    values["PYTHONPATH"] = str(ROOT / "scripts")
    values["PYTHONIOENCODING"] = "utf-8"
    values["VX_SESSION_DIR"] = str(SESSIONS)
    values["VX_SESSION"] = "1"
    values.pop("VX_HEARTBEAT", None)
    return values


def cli_run(args: list, timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "vx.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env(),
        timeout=timeout,
    )


def get_state(port: int, timeout: float = 3.0) -> dict:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{int(port)}/api/state", timeout=timeout
    ) as response:
        return json.loads(response.read().decode("utf-8"))


def answers(port: int, timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{int(port)}/api/state", timeout=timeout
        ) as response:
            return int(response.status) == 200
    except Exception:  # noqa: BLE001 - una porta chiusa e' una risposta
        return False


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def rows_of(directory: Path) -> list:
    rows = []
    for path in sorted(directory.glob("events*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def gaps(rows: list) -> list:
    return [round(b["t"] - a["t"], 3) for a, b in zip(rows, rows[1:])]


# --------------------------------------------------------------------------- #
# 1. il battito: clock iniettato, dieci secondi simulati
# --------------------------------------------------------------------------- #


class FakeClock:
    """Un orologio che avanza solo quando glielo si dice."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def check_heartbeat() -> None:
    base = TEMP / "beat"
    root = base / "root"
    clock = FakeClock()
    sess = session.Session.begin(
        "auto", ["auto", "--watch"], base, root=root, clock=clock
    )
    sess.stage("render", 10.0, "Encoding 1080p")
    # Dieci secondi di stadio lungo, senza un solo evento dal lavoro.
    for _ in range(40):
        clock.advance(0.25)
        sess.tick()
    rows = rows_of(sess.directory)
    heartbeat = [row for row in rows if row["type"] == "heartbeat"]

    check("H1 il clock iniettato non avvia il thread", sess._beat_thread is None,
          sess._beat_thread)
    check("H2 dieci secondi di stadio producono almeno 5 battiti",
          len(heartbeat) >= 5, len(heartbeat))
    widest = max(gaps(rows)) if len(rows) > 1 else 0.0
    check("H3 il silenzio massimo resta sotto i 2 s", widest <= 2.0, f"{widest}s")
    first = heartbeat[0] if heartbeat else {}
    check("H4 il battito porta stage, detail, elapsed_sec, eta_sec",
          {"stage", "detail", "elapsed_sec", "eta_sec"} <= set(first)
          and first.get("stage") == "render", first)
    check("H5 eta_sec viene da estimate e scende nel tempo",
          isinstance(first.get("eta_sec"), (int, float))
          and isinstance(heartbeat[-1].get("eta_sec"), (int, float))
          and heartbeat[-1]["eta_sec"] < first["eta_sec"]
          and first["eta_sec"] >= 0,
          (first.get("eta_sec"), heartbeat[-1].get("eta_sec")))

    # Un cancello aperto e' una pausa voluta: niente battito, l'evento e' `gate`.
    sess.gate("font", open=True, detail="in attesa dell'utente")
    before = len(rows_of(sess.directory))
    for _ in range(12):
        clock.advance(0.25)
        sess.tick()
    after_gate = rows_of(sess.directory)
    check("H6 con un cancello aperto nessun heartbeat",
          len(after_gate) == before
          and not any(row["type"] == "heartbeat" for row in after_gate[before:]),
          [row["type"] for row in after_gate[before:]])
    check("H6 il cancello aperto e' dichiarato nel registro",
          after_gate[before - 1]["type"] == "gate"
          and after_gate[before - 1]["gate"] == "font"
          and after_gate[before - 1]["open"] is True, after_gate[before - 1])

    sess.gate("font", open=False, chosen="Inter", by="user")
    clock.advance(1.6)
    resumed = sess.tick()
    check("H7 chiuso il cancello il battito riprende",
          resumed is not None and resumed["type"] == "heartbeat", resumed)

    sess.end("done")
    total = len(rows_of(sess.directory))
    clock.advance(5.0)
    check("H8 dopo la fine non batte piu'", sess.tick() is None
          and len(rows_of(sess.directory)) == total, total)

    state = json.loads((sess.directory / "state.json").read_text(encoding="utf-8"))
    check("H9 lo stato su disco resta leggibile dopo la fine",
          state.get("status") == "done"
          and state.get("event_count") == len(rows_of(sess.directory)),
          (state.get("status"), state.get("event_count")))
    loaded = session.Session.load(sess.id, root=root)
    check("H9 la sessione riletta racconta lo stesso stadio",
          loaded is not None and loaded.status == "done"
          and loaded.as_dict()["heartbeat"]["count"] >= 5,
          loaded and loaded.as_dict()["heartbeat"])


# --------------------------------------------------------------------------- #
# 2. il daemon: riuso, pid file, stato dopo il lavoro, down
# --------------------------------------------------------------------------- #


def up() -> subprocess.CompletedProcess:
    return cli_run(["up", "--port", "8100"], timeout=90.0)


def check_daemon() -> None:
    SESSIONS.mkdir(parents=True, exist_ok=True)
    up_file = SESSIONS / "up.json"
    try:
        up_file.unlink(missing_ok=True)
    except OSError:
        pass

    first = up()
    url = (first.stdout or "").strip().splitlines()[-1] if first.stdout else ""
    record = json.loads(up_file.read_text(encoding="utf-8")) if up_file.is_file() else {}
    check("U1 il primo `vx up` esce 0 e stampa un URL",
          first.returncode == 0 and url.startswith("http://127.0.0.1:"), url)
    check("U2 up.json porta pid, port, url, started_utc",
          {"pid", "port", "url", "started_utc"} <= set(record)
          and record.get("url") == url, record)
    check("U3 il pid del daemon e' vivo", cli._pid_alive(record.get("pid")),
          record.get("pid"))
    check("U4 la porta del daemon risponde", answers(record.get("port")),
          record.get("port"))

    second = up()
    url2 = (second.stdout or "").strip().splitlines()[-1] if second.stdout else ""
    again = json.loads(up_file.read_text(encoding="utf-8"))
    check("U5 il secondo `vx up` stampa lo stesso URL",
          second.returncode == 0 and url2 == url, (url, url2))
    check("U6 e' lo stesso processo: nessun secondo daemon",
          again.get("pid") == record.get("pid")
          and again.get("started_utc") == record.get("started_utc"),
          (record.get("pid"), again.get("pid")))

    # U6b: `--open` su un daemon gia' acceso non deve aprire un'altra scheda.
    # Prima ogni /reel ne lasciava una identica in piu'.
    import webbrowser

    opened: list = []
    real_open = webbrowser.open
    old_dir = os.environ.get("VX_SESSION_DIR")
    webbrowser.open = lambda url, *a, **k: opened.append(url) or True
    os.environ["VX_SESSION_DIR"] = str(SESSIONS)
    try:
        code = cli.cmd_up(type("A", (), {"port": 8100, "open": True})())
    finally:
        webbrowser.open = real_open
        if old_dir is None:
            os.environ.pop("VX_SESSION_DIR", None)
        else:
            os.environ["VX_SESSION_DIR"] = old_dir
    check("U6b `up --open` su daemon acceso non apre una seconda scheda",
          code == 0 and opened == [], opened)

    # U6c/U6d: niente finestra nera e nessun cambio d'indirizzo al riavvio.
    hidden = cli._console_free_python()
    check("U6c il daemon usa un interprete senza console (pythonw)",
          os.name != "nt" or hidden.lower().endswith("pythonw.exe"), hidden)
    spare = free_port()
    check("U6d una porta chiesta resta la stessa se e' libera",
          cli._choose_port(spare) == spare, spare)

    try:
        state = get_state(record["port"])
    except Exception as exc:  # noqa: BLE001
        state = {"error": f"{type(exc).__name__}: {exc}"}
    check("U7 /api/state risponde con le chiavi della vista",
          {"session", "events", "artifacts", "gates"} <= set(state), sorted(state))

    # `vx auto --watch` deve agganciarsi al daemon, non aprire un secondo server.
    assets = TEMP / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    job = TEMP / "job"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "vx.cli", "auto", "--prompt", "prova",
            "--assets", str(assets), "--out", str(job / "out.mp4"), "--watch",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env(),
    )
    line = ""
    try:
        line = proc.stdout.readline().strip() if proc.stdout else ""
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    watched = line.split("Lo guardi qui:", 1)[-1].strip() if line else ""
    after_watch = json.loads(up_file.read_text(encoding="utf-8"))
    check("U8 `auto --watch` usa il daemon acceso (stesso URL, stesso pid)",
          watched == url and after_watch.get("pid") == record.get("pid"),
          (watched, after_watch.get("pid")))

    # Un lavoro breve vero: la sessione finisce, il daemon no.
    doctor = cli_run(["doctor", "--json"], timeout=180.0)
    try:
        after_job = get_state(record["port"], timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        after_job = {"error": f"{type(exc).__name__}: {exc}"}
    summary = after_job.get("session") or {}
    check("U9 il lavoro e' passato (doctor esce 0)", doctor.returncode == 0,
          doctor.returncode)
    check("U10 dopo il lavoro la vista mostra la sessione chiusa e i suoi eventi",
          summary.get("kind") == "doctor" and summary.get("status") == "done"
          and len(after_job.get("events") or []) >= 2, summary)
    check("U11 il daemon e' ancora vivo dopo la fine del lavoro",
          cli._pid_alive(record.get("pid")) and answers(record.get("port")),
          record.get("pid"))

    down = cli_run(["down"], timeout=60.0)
    stopped = not cli._pid_alive(record.get("pid"))
    check("U12 `vx down` ferma il daemon, chiude la porta e toglie up.json",
          down.returncode == 0 and stopped and not up_file.exists()
          and not answers(record.get("port"), 0.3),
          (down.stdout or "").strip())

    # down non deve MAI fermare un processo che non e' il suo.
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    try:
        closed = free_port()
        up_file.write_text(
            json.dumps(
                {
                    "pid": sleeper.pid,
                    "port": closed,
                    "url": f"http://127.0.0.1:{closed}/",
                    "started_utc": "1970-01-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        foreign = cli_run(["down"], timeout=60.0)
        check("U13 `vx down` non tocca un pid che non risponde: resta vivo",
              foreign.returncode == 0 and sleeper.poll() is None,
              sleeper.pid)
        check("U14 ma il pid file stantio viene rimosso",
              not up_file.exists(), list(SESSIONS.iterdir()))
    finally:
        sleeper.terminate()
        try:
            sleeper.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sleeper.kill()

    empty = cli_run(["down"], timeout=60.0)
    check("U15 down senza daemon: nessun errore, nessun pid file",
          empty.returncode == 0 and "nessun daemon" in (empty.stdout or "")
          and not up_file.exists(), (empty.stdout or "").strip())

    # E dopo il down, `vx up` riparte: il ciclo non lascia residui.
    third = up()
    final = json.loads(up_file.read_text(encoding="utf-8")) if up_file.is_file() else {}
    check("U16 dopo il down il daemon si riavvia su un processo nuovo",
          third.returncode == 0 and final.get("pid")
          and final.get("pid") != record.get("pid")
          and cli._pid_alive(final.get("pid")), final.get("pid"))
    cli_run(["down"], timeout=60.0)


def main() -> int:
    print("vx.up — il daemon della vista e il battito che cura i vuoti")
    if os.name != "nt":
        print("    (nota: i percorsi di processo sono provati su Windows)")
    check_heartbeat()
    check_daemon()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    try:
        shutil.rmtree(TEMP)
    except OSError:
        pass
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
