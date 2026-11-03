"""vx.session — il registro che la vista legge anche senza una workdir.

Nessuna rete, nessun ffmpeg: la sessione, lo snapshot, la rotazione, la vista e
le azioni che si risolvono sull'ultimo job registrato. I comandi veri provati
sono ``vx doctor`` (sessione minima) e il manifest live (chunk in un'altra
cartella). Avvio: ``python tests/test_session.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import serve, session  # noqa: E402

PASS = 0
FAIL = 0
TEMP = Path(tempfile.mkdtemp(prefix="vx-session-"))


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def root(name: str) -> Path:
    path = TEMP / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def events_of(directory: Path) -> list:
    rows = []
    for path in sorted(directory.glob("events*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read()


def get_status(url: str) -> tuple:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def post(url: str) -> dict:
    request = urllib.request.Request(url, method="POST", data=b"")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def make_job(base: Path, name: str = "lavoro") -> Path:
    job = base / name
    job.mkdir(parents=True, exist_ok=True)
    (job / "progress.json").write_text(
        json.dumps({"prompt": "prova", "percent": 40.0, "current": "render"}),
        encoding="utf-8",
    )
    (job / "plan.json").write_text(json.dumps({"cuts": []}), encoding="utf-8")
    (job / "report.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    return job


# --------------------------------------------------------------------------- #
# 1. begin/event/end e snapshot
# --------------------------------------------------------------------------- #


def check_lifecycle() -> None:
    base = root("lifecycle")
    report = base / "report.json"
    report.write_text("abc", encoding="utf-8")
    sess = session.Session.begin("doctor", ["doctor", "--json"], base, root=base)
    check("S1 begin crea id, events.jsonl e state.json",
          bool(sess.id) and sess.path.is_file() and (sess.directory / "state.json").is_file(),
          sess.id)
    check("S1 id nel formato YYYYMMDD-HHMMSS-xxxx",
          len(sess.id) == 20 and sess.id[8] == "-" and sess.id[15] == "-",
          sess.id)
    sess.stage("scan", 50.0, "metà")
    sess.artifact("report", report)
    sess.decision("font", "Inter", by="auto", confidence="high")
    sess.gate("font", open=True, detail="scelta in attesa")
    sess.error("boom")
    sess.journal("riga di diario", phase="scan")
    sess.end("done")

    rows = events_of(sess.directory)
    check("S2 start porta kind, argv, cwd",
          rows[0]["type"] == "start" and rows[0]["kind"] == "doctor"
          and rows[0]["argv"][0] == "doctor" and rows[0]["cwd"] == str(base),
          rows[0].get("kind"))
    check("S2 ogni evento porta t non decrescente",
          [row["t"] for row in rows] == sorted(row["t"] for row in rows), len(rows))
    stage = next(row for row in rows if row["type"] == "stage")
    check("S3 stage porta nome, percent e detail",
          stage["stage"] == "scan" and stage["percent"] == 50.0 and stage["detail"] == "metà",
          stage)
    artifact = next(row for row in rows if row["type"] == "artifact")
    check("S4 artifact porta tipo, path e bytes",
          artifact["kind"] == "report" and artifact["path"] == str(report)
          and artifact["bytes"] == 3, artifact)
    decision = next(row for row in rows if row["type"] == "decision")
    gate = next(row for row in rows if row["type"] == "gate")
    check("S5 decision porta id e scelta",
          decision["id"] == "font" and decision["choice"] == "Inter"
          and decision["by"] == "auto", decision)
    check("S5 gate porta tipo e stato aperto",
          gate["gate"] == "font" and gate["open"] is True, gate)
    end = rows[-1]
    check("S6 end chiude con status",
          end["type"] == "end" and end["status"] == "done" and sess.status == "done",
          end.get("status"))

    snap = sess.as_dict()
    check("S7 snapshot: event_count e counts combaciano con gli eventi",
          snap["event_count"] == len(rows)
          and snap["counts"] == dict(Counter(row["type"] for row in rows)),
          (snap["event_count"], snap["counts"]))
    check("S7 snapshot: artifacts coincide con gli eventi artifact",
          [item["path"] for item in snap["artifacts"]]
          == [row["path"] for row in rows if row["type"] == "artifact"], snap["artifacts"])
    check("S7 snapshot: gate_state dice aperto/chiuso",
          snap["gate_state"].get("font") is True, snap["gate_state"])
    on_disk = json.loads((sess.directory / "state.json").read_text(encoding="utf-8"))
    check("S8 lo state.json su disco e' lo stesso snapshot",
          on_disk["event_count"] == snap["event_count"]
          and on_disk["artifacts"] == snap["artifacts"], on_disk["event_count"])


# --------------------------------------------------------------------------- #
# 2. job sugli eventi successivi, lettura e ultima sessione
# --------------------------------------------------------------------------- #


def check_job_and_load() -> None:
    base = root("load")
    job = make_job(base)
    first = session.Session.begin("run", ["run"], base, root=base)
    first.set_job(job)
    first.stage("render", 10.0, "parto")
    first.end()

    row = events_of(first.directory)[-2]
    check("S9 set_job timbra gli eventi successivi con job e job_path",
          row.get("job") == "lavoro" and row.get("job_path") == str(job), row.get("job"))

    time.sleep(0.02)
    second = session.Session.begin("ingest", ["ingest"], base, root=base)
    second.stage("proxy", 5.0, "x")
    second.end()

    check("S10 latest e' l'ultima sessione aperta",
          session.latest(root=base) == second.id, session.latest(root=base))
    loaded = session.Session.load(first.id, root=base)
    check("S10 load(id) rilegge la storia giusta",
          loaded is not None and len(loaded.events) == 3
          and loaded.events[1]["stage"] == "render", loaded and len(loaded.events))
    check("S10 load di un id con separatori e' rifiutato",
          session.Session.load("../../evil", root=base) is None)


# --------------------------------------------------------------------------- #
# 3. costo nullo: spento e non scrivibile
# --------------------------------------------------------------------------- #


def check_disabled_and_failure() -> None:
    base = root("disabled")
    old = os.environ.get("VX_SESSION")
    os.environ["VX_SESSION"] = "0"
    try:
        sess = session.Session.begin("doctor", ["doctor"], base, root=base / "reg")
        sess.stage("scan", 1.0, "x")
        sess.artifact("report", base / "nope")
        sess.end("done")
    finally:
        if old is None:
            os.environ.pop("VX_SESSION", None)
        else:
            os.environ["VX_SESSION"] = old
    check("S11 con VX_SESSION=0 non si crea nessun file",
          not (base / "reg").exists(), list(base.iterdir()))
    check("S11 con VX_SESSION=0 nessun evento e nessuna eccezione",
          sess.active is False and sess.events == [], sess.as_dict()["status"])

    blocker = root("blocked")
    (blocker / "file").write_text("non una cartella", encoding="utf-8")
    broken = session.Session.begin("doctor", ["doctor"], blocker, root=blocker / "file")
    broken.stage("scan", 1.0, "x")
    broken.end()
    check("S12 una radice non scrivibile spegne il registro senza sollevare",
          broken.active is False and broken.events == [])


# --------------------------------------------------------------------------- #
# 4. rotazione e storia completa
# --------------------------------------------------------------------------- #


def check_rotation() -> None:
    base = root("rotation")
    sess = session.Session.begin("auto", ["auto"], base, root=base, max_bytes=420)
    for index in range(24):
        sess.stage("fase", float(index), "x" * 80)
    sess.end("done")
    files = sorted(sess.directory.glob("events*.jsonl"))
    check("S13 il tetto di dimensione ruota su piu' file",
          len(files) > 1 and (sess.directory / "events-002.jsonl").is_file(),
          [path.name for path in files])
    rows = events_of(sess.directory)
    loaded = session.Session.load(sess.id, root=base)
    check("S13 la rilettura unisce i file ruotati in ordine",
          loaded is not None and len(loaded.events) == len(rows) == 26,
          (len(rows), loaded and len(loaded.events)))
    check("S13 gli eventi restano in ordine dopo la rotazione",
          [row["t"] for row in loaded.events] == sorted(row["t"] for row in loaded.events))


# --------------------------------------------------------------------------- #
# 5. la vista: state, azioni risolte sul job, live da un'altra cartella
# --------------------------------------------------------------------------- #


def check_view_and_actions() -> None:
    base = root("view")
    job = make_job(base)
    (job / "keyframes").mkdir()
    (job / "keyframes" / "pending.json").write_text(
        json.dumps({"digest": "d1", "timeline_digest": "t1", "count": 3, "frames": []}),
        encoding="utf-8",
    )
    (job / "font_proposal.json").write_text(
        json.dumps({"candidates": [{"family": "Inter"}, {"family": "Bebas Neue"}]}),
        encoding="utf-8",
    )
    master = base / "master.mp4"
    master.write_bytes(b"master-bytes" * 4)

    sess = session.Session.begin("auto", ["auto"], base, root=base)
    sess.set_job(job)
    sess.stage("render", 55.0, "metà")
    sess.artifact("report", job / "report.json")
    sess.artifact("master", master)
    sess.decision("font", "Inter", by="user")
    sess.gate("font", open=True, detail="in attesa")
    sess.gate("keyframes", open=False, chosen="ok", by="user")
    sess.journal("scelgo il font", phase="font")
    sess.end("done")

    old_root = os.environ.get("VX_SESSION_DIR")
    old_feedback = os.environ.get("VX_FEEDBACK_ROOT")
    os.environ["VX_SESSION_DIR"] = str(base)
    os.environ["VX_FEEDBACK_ROOT"] = str(base / "feedback")
    server = serve.Server(port=0, session=True).start()
    try:
        data = json.loads(get(server.url + "api/state").decode("utf-8"))
        check("S14 /api/state ha le sei chiavi promesse",
              set(data) == {"session", "events", "artifacts", "gates", "live", "journal"},
              sorted(data))
        check("S14 la sessione racconta start, stage e end",
              data["session"]["id"] == sess.id and data["session"]["status"] == "done"
              and [row["type"] for row in data["events"]][:1] == ["start"]
              and data["events"][-1]["type"] == "end",
              [row["type"] for row in data["events"]])
        check("S14 gli artifact sono la lista dei file prodotti",
              [item["kind"] for item in data["artifacts"]] == ["report", "master"],
              data["artifacts"])
        check("S14 i cancelli mostrano aperto/chiuso",
              [(item["gate"], item["open"]) for item in data["gates"]]
              == [("font", True), ("keyframes", False)], data["gates"])
        check("S14 il journal e' nella sessione, non in progress.json",
              data["journal"] and data["journal"][0]["line"] == "scelgo il font",
              data["journal"])

        answer = post(server.url + "api/approve")
        check("S15 /api/approve risolve il job dall'evento piu' recente",
              answer.get("ok") is True and (job / "keyframes.json").is_file(),
              answer)
        answer = post(server.url + "api/font-pick?family=Inter")
        check("S15 /api/font-pick scrive la scelta nella workdir risolta",
              answer.get("ok") is True
              and json.loads((job / "font_choice.json").read_text(encoding="utf-8"))["family"]
              == "Inter", answer)
        answer = post(server.url + "api/plan-choice?choice=fast")
        check("S15 /api/plan-choice scrive full/fast nel job risolto",
              answer.get("ok") is True
              and json.loads((job / "plan_choice.json").read_text(encoding="utf-8"))["choice"]
              == "fast", answer)
        answer = post(server.url + "api/feedback?rating=5&note=bene")
        check("S15 /api/feedback registra il voto sul job risolto",
              answer.get("ok") is True
              and (base / "feedback" / "feedback.jsonl").is_file(), answer)
        check("S16 /result serve il master registrato nella sessione",
              get(server.url + "result") == master.read_bytes(), len(master.read_bytes()))
        revealed = []
        old_reveal = serve.REVEAL
        serve.REVEAL = lambda path: revealed.append(Path(path))
        try:
            answer = post(server.url + "api/reveal")
        finally:
            serve.REVEAL = old_reveal
        check("S16 /api/reveal apre il master della sessione",
              answer.get("ok") is True and revealed == [master], revealed)
    finally:
        server.stop()
        if old_root is None:
            os.environ.pop("VX_SESSION_DIR", None)
        else:
            os.environ["VX_SESSION_DIR"] = old_root
        if old_feedback is None:
            os.environ.pop("VX_FEEDBACK_ROOT", None)
        else:
            os.environ["VX_FEEDBACK_ROOT"] = old_feedback


def check_live_from_session() -> None:
    base = root("live-session")
    job = make_job(base, "job")
    live_dir = base / "altrove" / "live"
    live_dir.mkdir(parents=True)
    (live_dir / "chunk_000.mp4").write_bytes(b"0123456789")
    manifest = {
        "schema": "vx.live/1",
        "total": 2,
        "chunks": [
            {"index": 0, "t_in": 0.0, "t_out": 4.0, "ready": True},
            {"index": 1, "t_in": 4.0, "t_out": 8.0, "ready": False},
        ],
    }
    (live_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    sess = session.Session.begin("auto", ["auto", "--live"], base, root=base)
    sess.set_job(job)
    from vx import cli  # noqa: PLC0415 - solo per il ponte live

    cli._session_live(sess, base / "altrove")
    sess.end("done")

    events = events_of(sess.directory)
    chunk_events = [row for row in events if row["type"] == "artifact" and row["kind"] == "chunk"]
    check("S17 --live registra un artifact per ogni chunk",
          len(chunk_events) == 2, len(chunk_events))
    live = sess.as_dict()["live"]
    check("S17 lo snapshot live dice ready/total",
          live["ready"] == 1 and live["total"] == 2
          and live["directory"] == str(live_dir), live)

    old_root = os.environ.get("VX_SESSION_DIR")
    os.environ["VX_SESSION_DIR"] = str(base)
    server = serve.Server(port=0, session=True).start()
    try:
        data = json.loads(get(server.url + "api/state").decode("utf-8"))
        check("S17 /api/state mostra ready/total anche se live/ sta altrove",
              data["live"]["ready"] == 1 and data["live"]["total"] == 2,
              data["live"])
        status, body = get_status(server.url + "live/chunk_000.mp4")
        check("S17 /live/chunk_000.mp4 viene dal manifesto della sessione",
              status == 200 and body == b"0123456789", (status, body))
        status, _ = get_status(server.url + "live/chunk_001.mp4")
        check("S17 un chunk non pronto resta 404", status == 404, status)
        status, _ = get_status(server.url + "live/../secret.mp4")
        check("S17 un percorso che esce da live/ resta 404", status == 404, status)
    finally:
        server.stop()
        if old_root is None:
            os.environ.pop("VX_SESSION_DIR", None)
        else:
            os.environ["VX_SESSION_DIR"] = old_root


# --------------------------------------------------------------------------- #
# 6. vx doctor da solo, e la vista lo mostra
# --------------------------------------------------------------------------- #


def check_doctor_command() -> None:
    base = root("doctor")
    env = os.environ.copy()
    env["VX_SESSION_DIR"] = str(base)
    env["PYTHONPATH"] = str(ROOT / "scripts")
    env["VX_SESSION"] = "1"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "vx.py"), "doctor", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    check("S18 vx doctor esce senza traceback",
          proc.returncode in (0, 2) and "Traceback" not in proc.stderr,
          (proc.returncode, proc.stderr[-120:]))
    loaded = session.Session.load(root=base)
    types = [row["type"] for row in loaded.events] if loaded else []
    check("S18 vx doctor produce start, stage, end",
          bool(loaded) and types[0] == "start" and "stage" in types and types[-1] == "end",
          types)
    check("S18 nessun progress.json serve alla sessione",
          not list(base.rglob("progress.json")), list(base.rglob("progress.json")))
    old_root = os.environ.get("VX_SESSION_DIR")
    os.environ["VX_SESSION_DIR"] = str(base)
    server = serve.Server(port=0, session=True).start()
    try:
        data = json.loads(get(server.url + "api/state").decode("utf-8"))
        check("S18 la vista mostra la sessione del doctor",
              data["session"]["kind"] == "doctor"
              and [row["type"] for row in data["events"]][-1] == "end",
              data["session"]["id"])
    finally:
        server.stop()
        if old_root is None:
            os.environ.pop("VX_SESSION_DIR", None)
        else:
            os.environ["VX_SESSION_DIR"] = old_root


def check_cli_shapes() -> None:
    from vx import cli  # noqa: PLC0415

    parser = cli.build_parser()
    args = parser.parse_args(
        ["serve", "--session", "--port", "0"]
    )
    check("S19 il parser accetta vx serve --session", args.session is True and args.dir is None)
    args = parser.parse_args(
        ["auto", "--prompt", "p", "--assets", "a", "--out", "o.mp4", "--live"]
    )
    check("S19 il parser accetta vx auto --live", args.live is True)


def check_robustness() -> None:
    # 1. un guasto di scrittura non risale e si dice una volta sola.
    base = root("warn")
    (base / "file").write_text("non una cartella", encoding="utf-8")
    session._WARNED = False
    buffer = StringIO()
    failures = []
    with redirect_stderr(buffer):
        for _ in range(2):
            broken = session.Session.begin("doctor", ["doctor"], base, root=base / "file")
            broken.stage("scan", 1.0, "x")
            broken.end()
            failures.append(broken)
    warning = buffer.getvalue()
    check("R1 scrittura fallita: nessuna eccezione e sessione muta",
          all(item.active is False for item in failures), [item.last_error for item in failures])
    check("R1 il guasto si annota una sola volta su stderr",
          warning.count("[vx] session:") == 1 and "prosegue" in warning, warning.strip())

    # 2. una riga troncata a metà JSON si salta, il resto si legge.
    base = root("truncated")
    sess = session.Session.begin("ingest", ["ingest"], base, root=base)
    sess.stage("proxy", 10.0, "prima")
    sess.end("done")
    path = sess.path
    assert path is not None
    before = len(events_of(sess.directory))
    after = json.dumps({"t": 99.0, "type": "stage", "stage": "dopo"}) + "\n"
    path.write_bytes(
        path.read_bytes()
        + b'{"t": 50.0, "type": "stage", "stage": "tronc'
        + b"\n"
        + after.encode("utf-8")
    )
    loaded = session.Session.load(sess.id, root=base)
    check("R2 un file tagliato a metà riga non ferma la lettura",
          loaded is not None and len(loaded.events) == before + 1,
          loaded and len(loaded.events))
    check("R2 la riga valida dopo quella troncata viene letta",
          loaded is not None and loaded.events[-1].get("stage") == "dopo",
          loaded and loaded.events[-1])

    # 3. due sessioni in parallelo: id, file e viste separati.
    base = root("parallel")
    first = session.Session.begin("run", ["run"], base, root=base)
    first.stage("A", 10.0, "solo A")
    first.end()
    time.sleep(0.03)
    second = session.Session.begin("auto", ["auto"], base, root=base)
    second.stage("B", 20.0, "solo B")
    second.end()
    check("R3 due sessioni hanno id e cartelle diversi",
          first.id != second.id and first.directory != second.directory,
          (first.id, second.id))
    a = session.Session.load(first.id, root=base)
    b = session.Session.load(second.id, root=base)
    check("R3 ogni sessione legge solo i propri eventi",
          a is not None and b is not None
          and {row.get("stage") for row in a.events if row["type"] == "stage"} == {"A"}
          and {row.get("stage") for row in b.events if row["type"] == "stage"} == {"B"},
          (a and len(a.events), b and len(b.events)))
    check("R3 l'ultima per mtime e' la seconda", session.latest(root=base) == second.id)
    old_root = os.environ.get("VX_SESSION_DIR")
    os.environ["VX_SESSION_DIR"] = str(base)
    server = serve.Server(port=0, session=True).start()
    try:
        default = json.loads(get(server.url + "api/state").decode("utf-8"))
        chosen = json.loads(
            get(server.url + "api/state?session=" + first.id).decode("utf-8")
        )
        check("R3 ?session=ID non mescola gli eventi delle due sessioni",
              default["session"]["id"] == second.id
              and chosen["session"]["id"] == first.id
              and all(row.get("session") is None or row.get("session") == first.id
                      for row in chosen["events"])
              and {row.get("stage") for row in chosen["events"] if row["type"] == "stage"} == {"A"},
              (default["session"]["id"], chosen["session"]["id"]))
    finally:
        server.stop()
        if old_root is None:
            os.environ.pop("VX_SESSION_DIR", None)
        else:
            os.environ["VX_SESSION_DIR"] = old_root

    # 4. nessuna sessione: stato vuoto dichiarato, non 500.
    base = root("no-session")
    old_root = os.environ.get("VX_SESSION_DIR")
    os.environ["VX_SESSION_DIR"] = str(base)
    server = serve.Server(port=0, session=True).start()
    try:
        status, body = get_status(server.url + "api/state")
        data = json.loads(body.decode("utf-8"))
        check("R4 senza sessioni /api/state risponde 200, non 500", status == 200, status)
        check("R4 lo stato vuoto dichiara perche'",
              data["events"] == [] and data["session"]["reason"]
              == "nessuna sessione: nessun comando vx e' stato eseguito",
              data["session"].get("reason"))
    finally:
        server.stop()
        if old_root is None:
            os.environ.pop("VX_SESSION_DIR", None)
        else:
            os.environ["VX_SESSION_DIR"] = old_root

    # 5. rotazione: events-002.jsonl e lettura in ordine.
    base = root("rotation-002")
    sess = session.Session.begin("auto", ["auto"], base, root=base, max_bytes=420)
    for index in range(10):
        sess.stage("passo", float(index), "x" * 80)
    sess.end("done")
    names = [path.name for path in sorted(sess.directory.glob("events*.jsonl"))]
    loaded = session.Session.load(sess.id, root=base)
    stages = [row.get("stage") for row in loaded.events if row["type"] == "stage"]
    check("R5 la rotazione crea events-002.jsonl", "events-002.jsonl" in names, names)
    check("R5 la vista legge tutti i file in ordine cronologico",
          loaded is not None and stages == ["passo"] * 10
          and [row["t"] for row in loaded.events] == sorted(row["t"] for row in loaded.events),
          (len(stages), names))

    # 6. riavvio della vista: nessun evento duplicato.
    base = root("restart")
    sess = session.Session.begin("run", ["run"], base, root=base)
    for index in range(12):
        sess.stage("fase", float(index), "in corso")
    old_root = os.environ.get("VX_SESSION_DIR")
    os.environ["VX_SESSION_DIR"] = str(base)
    first_server = serve.Server(port=0, session=True).start()
    try:
        one = json.loads(get(first_server.url + "api/state").decode("utf-8"))
    finally:
        first_server.stop()
    second_server = serve.Server(port=0, session=True).start()
    try:
        two = json.loads(get(second_server.url + "api/state").decode("utf-8"))
    finally:
        second_server.stop()
        if old_root is None:
            os.environ.pop("VX_SESSION_DIR", None)
        else:
            os.environ["VX_SESSION_DIR"] = old_root
    check("R6 dopo il riavvio la pagina ritrova id, stato ed eventi",
          one["session"]["id"] == two["session"]["id"] == sess.id
          and one["session"]["status"] == two["session"]["status"] == "running"
          and len(one["events"]) == len(two["events"]) == 13,
          (len(one["events"]), len(two["events"])))

    # 7. atomicità: chi legge non vede mai un JSON a metà.
    base = root("atomic")
    sess = session.Session.begin("auto", ["auto"], base, root=base)
    stop = threading.Event()
    torn = []

    def reader() -> None:
        state = sess.directory / "state.json"
        while not stop.is_set():
            try:
                json.loads(state.read_text(encoding="utf-8"))
            except OSError:
                continue  # il file e' stato sostituito in quell'istante
            except ValueError as exc:
                torn.append(str(exc))
                return

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    for index in range(80):
        sess.stage("atomico", float(index), "y" * 20)
    sess.end("done")
    stop.set()
    thread.join(timeout=2.0)
    leftovers = list(sess.directory.glob("*.tmp.*"))
    check("R7 state.json non si legge mai a metà", torn == [] and sess.status == "done",
          (torn[:1], sess.last_error))
    check("R7 nessun file temporaneo resta dopo il replace", leftovers == [], leftovers)

    # metrica: 500 eventi, poi 10 richieste consecutive senza errori.
    base = root("scale")
    sess = session.Session.begin("auto", ["auto"], base, root=base)
    for index in range(500):
        sess.stage("scala", float(index % 100), "z" * 8)
    sess.end("done")
    old_root = os.environ.get("VX_SESSION_DIR")
    os.environ["VX_SESSION_DIR"] = str(base)
    server = serve.Server(port=0, session=True).start()
    start = time.monotonic()
    answers = []
    try:
        for _ in range(10):
            status, body = get_status(server.url + "api/state")
            answers.append((status, len(body)))
    finally:
        server.stop()
        if old_root is None:
            os.environ.pop("VX_SESSION_DIR", None)
        else:
            os.environ["VX_SESSION_DIR"] = old_root
    elapsed = time.monotonic() - start
    check("R8 500 eventi e 10 richieste: tutte 200, nessun errore",
          all(status == 200 for status, _ in answers) and len(answers) == 10,
          answers[:2])
    check("R8 la vista regge almeno 10 richieste al secondo",
          elapsed <= 1.0, f"{10 / elapsed:.1f} req/s in {elapsed:.3f}s")


def main() -> int:
    print("vx.session — registro, snapshot, vista e azioni")
    check_lifecycle()
    check_job_and_load()
    check_disabled_and_failure()
    check_rotation()
    check_view_and_actions()
    check_live_from_session()
    check_doctor_command()
    check_cli_shapes()
    check_robustness()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    try:
        shutil.rmtree(TEMP)
    except OSError:
        pass
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
