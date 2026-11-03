"""Il registro dei giudizi: appeso atomico, aggregati onesti, vista e CLI.

F1 - ``append`` scrive **una** riga nella forma ``vx.feedback/1``; un voto fuori
     1..5 e' rifiutato prima di toccare il disco.
F2 - l'append e' atomico: due scrittori simulati con thread (50 righe) non si
     perdono ne' si spezzano una riga a vicenda, e il file non viene riscritto.
F3 - ``report`` con 0, 3 e 10 valutazioni: media, distribuzione, media per
     esito e per equilibrio, MAPE stima/consuntivo, Pearson; sotto la soglia
     dichiarata la frase "non e' conclusiva" esce insieme al numero.
F4 - ``pending`` scrive/legge ``feedback_request.json`` e non blocca su una
     workdir che non puo' scrivere (nessuna eccezione).
F5 - la CLI: ``feedback add`` prende il contesto dal report, ``feedback report``
     parla italiano (o JSON), un voto invalido esce 2 senza scrivere.
F6 - ``serve``: ``POST /api/feedback`` registra col contesto del job e
     ``GET /api/state`` espone asked/given/rating/summary.
F7 - ``auto`` lascia il segnale a fine lavoro, con il contesto gia' compilato,
     dopo guard e verification, e un report rotto non lo fa sollevare.

python tests/test_feedback.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, cli, feedback, serve  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


@contextlib.contextmanager
def register_at(path: Path):
    """Il registro in una cartella temporanea: nessun test tocca ``~/.vx``."""
    previous = os.environ.get(feedback.ROOT_ENV)
    os.environ[feedback.ROOT_ENV] = str(path)
    try:
        yield path
    finally:
        if previous is None:
            os.environ.pop(feedback.ROOT_ENV, None)
        else:
            os.environ[feedback.ROOT_ENV] = previous


def lines(root: Path) -> list:
    path = root / feedback.REGISTER_NAME
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def report_fixture(master: Path) -> dict:
    """Un referto ``auto`` ridotto all'osso, ma con tutte le sezioni vere."""
    return {
        "ok": True,
        "outcome_suggested": "done",
        "delivered": True,
        "master": str(master),
        "timeline_sec": 30.4,
        "estimate": {"seconds": 274.0, "effects": {"count": 3}},
        "timings": {"render": 300.0, "qc": 12.0},
        "guard": {"ok": True, "skipped": False, "violations": [], "warnings": []},
        "verification": {"ok": True, "checks": []},
        "coherence": {
            "ok": True,
            "violations": [],
            "metrics": {"balance": "ok", "shots": 24},
        },
        "font": {"family": "Archivo Black"},
    }


def make_job(directory: Path, prompt: str = "reel di prova") -> dict:
    """Una workdir riconoscibile da ``serve`` e da ``cli``: report, master, prompt."""
    directory.mkdir(parents=True, exist_ok=True)
    master = directory / "out" / "master.mp4"
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_bytes(b"\x00" * 64)
    report = report_fixture(master)
    (directory / "auto_report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )
    (directory / "progress.json").write_text(
        json.dumps(
            {
                "prompt": prompt,
                "percent": 100.0,
                "current": None,
                "master": str(master),
                "estimate": report["estimate"],
            }
        ),
        encoding="utf-8",
    )
    return report


def record(rating: int, *, outcome: str, balance_name: str, estimate=None, actual=None):
    return feedback.make_record(
        job_id=f"job-{rating}-{outcome}-{balance_name}",
        prompt="prova",
        rating=rating,
        note="",
        context={
            "outcome": outcome,
            "balance": balance_name,
            "violations": [],
            "estimate_seconds": estimate,
            "actual_seconds": actual,
        },
    )


def cli_run(argv: list) -> tuple:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def post(url: str) -> dict:
    request = urllib.request.Request(url, method="POST", data=b"")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# F1 - forma del record e voto validato
# --------------------------------------------------------------------------- #


def section_shape() -> None:
    with tempfile.TemporaryDirectory(prefix="vx-fb-shape-") as raw:
        root = Path(raw)
        with register_at(root):
            written = feedback.append(
                feedback.make_record(
                    job_id="lavoro-1",
                    prompt="reel di prova",
                    rating=5,
                    note="funziona",
                    context={"outcome": "done", "balance": "ok"},
                )
            )
            check("F1 append restituisce il record normalizzato", written["rating"] == 5)
            rows = lines(root)
            check("F1 una riga JSON, non un report riscritto", len(rows) == 1, len(rows))
            check(
                "F1 la forma e' esattamente vx.feedback/1",
                rows
                and set(rows[0]) == {
                    "schema",
                    "job_id",
                    "prompt",
                    "created_utc",
                    "rating",
                    "note",
                    "context",
                }
                and rows[0]["schema"] == "vx.feedback/1",
                sorted(rows[0]) if rows else "",
            )
            check(
                "F1 il contesto viaggia dentro il record",
                rows and rows[0]["context"] == {"outcome": "done", "balance": "ok"},
                rows[0]["context"] if rows else "",
            )
            created = str(rows[0]["created_utc"]) if rows else ""
            check(
                "F1 created_utc e' un istante ISO con fuso",
                created.endswith("+00:00") and "T" in created,
                created,
            )

            for bad in (0, 6, 4.5, "boh", None, True):
                raised = False
                try:
                    feedback.append(feedback.make_record(job_id="x", rating=bad))
                except ValueError:
                    raised = True
                check(f"F1 voto {bad!r} rifiutato", raised)
            check(
                "F1 i voti rifiutati non hanno scritto niente",
                len(lines(root)) == 1,
                len(lines(root)),
            )
            check(
                "F1 la query string '4' e' un voto valido",
                feedback.validate_rating("4") == 4,
            )


# --------------------------------------------------------------------------- #
# F2 - append atomico: due scrittori, nessuna riga persa o spezzata
# --------------------------------------------------------------------------- #


def section_atomic() -> None:
    with tempfile.TemporaryDirectory(prefix="vx-fb-atomic-") as raw:
        root = Path(raw)
        with register_at(root):
            first = feedback.make_record(job_id="primo", rating=3)
            feedback.append(first)
            path = root / feedback.REGISTER_NAME
            before = path.read_bytes()

            def writer(worker: int) -> None:
                for index in range(25):
                    feedback.append(
                        feedback.make_record(
                            job_id=f"t{worker}-{index:02d}", rating=4, note="x" * 200
                        )
                    )

            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(writer, (0, 1)))

            after = path.read_bytes()
            rows = lines(root)
            check(
                "F2 due scrittori simultanei: 1 + 50 righe, tutte leggibili",
                len(rows) == 51,
                len(rows),
            )
            check(
                "F2 nessuna riga persa o doppia",
                len({row["job_id"] for row in rows}) == 51,
                len({row["job_id"] for row in rows}),
            )
            check(
                "F2 l'append non riscrive il file (prefisso intatto)",
                after.startswith(before) and len(after) > len(before),
                (len(before), len(after)),
            )
            check(
                "F2 i voti scritti dai thread sono quelli chiesti",
                all(row["rating"] == 4 for row in rows[1:]),
            )

            # Una riga rotta (crash a meta' append) si salta, non si perde tutto.
            with open(path, "a", encoding="utf-8") as handle:
                handle.write('{"rating": 4, "job_id": "tronc')
            valid = feedback.read_all()
            data = feedback.report()
            check(
                "F2 una riga troncata non azzera il registro",
                len(valid) == 51 and data["broken_lines"] == 1,
                (len(valid), data["broken_lines"]),
            )


# --------------------------------------------------------------------------- #
# F3 - report: 0, 3, 10 valutazioni; MAPE; correlazione e soglia
# --------------------------------------------------------------------------- #


def section_report() -> None:
    with tempfile.TemporaryDirectory(prefix="vx-fb-report-") as raw:
        root = Path(raw)
        with register_at(root):
            empty = feedback.report()
            text = feedback.report_text(empty)
            check(
                "F3 zero valutazioni: nessuna media inventata",
                empty["count"] == 0
                and empty["mean"] is None
                and empty["mape"]["n"] == 0
                and empty["correlation"]["n"] == 0,
                (empty["mean"], empty["mape"]["n"]),
            )
            check(
                "F3 zero valutazioni: la frase c'e' e dice che non basta",
                "con 0 valutazioni la correlazione non e' conclusiva" in text,
                [line for line in text.splitlines() if "Correlazione" in line],
            )
            check(
                "F3 zero valutazioni: la distribuzione e' a zero, non vuota",
                empty["distribution"] == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
                empty["distribution"],
            )

            feedback.append(record(5, outcome="done", balance_name="ok", estimate=100, actual=100))
            feedback.append(record(3, outcome="failed", balance_name="high", estimate=100, actual=200))
            feedback.append(record(4, outcome="stopped", balance_name="low", estimate=50, actual=50))
            three = feedback.report()
            check(
                "F3 tre valutazioni: media e distribuzione",
                three["count"] == 3
                and three["mean"] == 4.0
                and three["distribution"] == {"1": 0, "2": 0, "3": 1, "4": 1, "5": 1},
                (three["mean"], three["distribution"]),
            )
            check(
                "F3 media per esito (e gli esiti assenti restano n=0)",
                three["by_outcome"]["done"]["mean"] == 5.0
                and three["by_outcome"]["failed"]["mean"] == 3.0
                and three["by_outcome"]["stopped"]["mean"] == 4.0
                and three["by_outcome"]["incomplete"]["n"] == 0,
                three["by_outcome"],
            )
            check(
                "F3 media per equilibrio low/ok/high",
                three["by_balance"]["low"]["mean"] == 4.0
                and three["by_balance"]["ok"]["mean"] == 5.0
                and three["by_balance"]["high"]["mean"] == 3.0,
                three["by_balance"],
            )
            check(
                "F3 MAPE: (0 + 50 + 0) / 3 = 16.7%",
                three["mape"]["n"] == 3 and three["mape"]["mean_percent"] == 16.7,
                three["mape"],
            )
            check(
                "F3 tre valutazioni: la correlazione e' dichiarata non conclusiva",
                three["correlation"]["n"] == 3
                and three["correlation"]["conclusive"] is False
                and "con 3 valutazioni la correlazione non e' conclusiva"
                in three["correlation"]["note"],
                three["correlation"]["note"],
            )
            check(
                "F3 la frase esce anche nel testo per il terminale",
                "con 3 valutazioni la correlazione non e' conclusiva"
                in feedback.report_text(three),
            )

    # -- dieci valutazioni: la soglia dichiarata e' superata ---------------- #
    with tempfile.TemporaryDirectory(prefix="vx-fb-ten-") as raw:
        root = Path(raw)
        with register_at(root):
            for index in range(4):
                feedback.append(record(2, outcome="done", balance_name="high"))
            for index in range(6):
                feedback.append(record(4, outcome="done", balance_name="ok"))
            ten = feedback.report()
            check(
                "F3 dieci valutazioni: conteggio e media",
                ten["count"] == 10 and ten["mean"] == 3.2,
                (ten["count"], ten["mean"]),
            )
            check(
                "F3 dieci valutazioni: Pearson -1.0, n=10, conclusiva",
                ten["correlation"]["n"] == 10
                and ten["correlation"]["pearson"] == -1.0
                and ten["correlation"]["conclusive"] is True,
                (ten["correlation"]["n"], ten["correlation"]["pearson"]),
            )
            check(
                "F3 sopra soglia la frase cambia, la cautela resta",
                "sopra la soglia" in ten["correlation"]["note"]
                and "non e' conclusiva" not in ten["correlation"]["note"]
                and bool(ten["correlation"]["caveat"]),
                ten["correlation"]["note"],
            )
            check(
                "F3 la soglia dichiarata e' nel referto (non nascosta)",
                ten["correlation"]["min_n"] == feedback.CORRELATION_MIN_N == 10,
                ten["correlation"]["min_n"],
            )
            check(
                "F3 i voti alti hanno una media piu' alta dei bassi",
                ten["by_balance"]["high"]["mean"] == 2.0
                and ten["by_balance"]["ok"]["mean"] == 4.0,
                ten["by_balance"],
            )

    # -- MAPE: solo coppie vere, e il segno dell'errore --------------------- #
    with tempfile.TemporaryDirectory(prefix="vx-fb-mape-") as raw:
        root = Path(raw)
        with register_at(root):
            feedback.append(record(4, outcome="done", balance_name="ok", estimate=100, actual=200))
            feedback.append(record(4, outcome="done", balance_name="ok", estimate=50, actual=50))
            feedback.append(
                feedback.make_record(
                    job_id="senza-consuntivo",
                    rating=4,
                    context={"outcome": "done", "balance": "ok", "estimate_seconds": 80},
                )
            )
            mape = feedback.report()
            check(
                "F3 MAPE: 25.0% su 2 coppie, la terza (senza consuntivo) e' esclusa",
                mape["mape"]["n"] == 2 and mape["mape"]["mean_percent"] == 25.0,
                mape["mape"],
            )
            check(
                "F3 l'errore medio ha il segno: stima bassa = negativo",
                mape["mape"]["mean_error_sec"] == -50.0,
                mape["mape"]["mean_error_sec"],
            )
            check(
                "F3 equilibrio costante: n conta, Pearson non e' calcolabile",
                mape["correlation"]["n"] == 3
                and mape["correlation"]["pearson"] is None
                and "non e' conclusiva" in mape["correlation"]["note"],
                mape["correlation"],
            )
            check(
                "F3 MAPE anche nel testo italiano",
                "MAPE 25.0%" in feedback.report_text(mape),
                [line for line in feedback.report_text(mape).splitlines() if "MAPE" in line],
            )


# --------------------------------------------------------------------------- #
# F4 - pending: scrive/legge il segnale, e non blocca
# --------------------------------------------------------------------------- #


def section_pending() -> None:
    with tempfile.TemporaryDirectory(prefix="vx-fb-pending-") as raw:
        workdir = Path(raw) / "lavoro"
        workdir.mkdir()
        absent = feedback.pending(workdir)
        check(
            "F4 senza segnale pending legge asked=false",
            absent["asked"] is False
            and not (workdir / feedback.REQUEST_NAME).exists(),
            absent,
        )
        written = feedback.pending(
            workdir,
            {"outcome": "done", "balance": "ok", "shots": 24},
            job_id="lavoro",
            prompt="reel di prova",
        )
        path = workdir / feedback.REQUEST_NAME
        saved = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        check(
            "F4 pending scrive asked=true col contesto gia' compilato",
            written["asked"] is True
            and saved.get("asked") is True
            and saved.get("context", {}).get("shots") == 24
            and saved.get("prompt") == "reel di prova",
            saved,
        )
        again = feedback.pending(workdir)
        check(
            "F4 pending non riscrive un segnale gia' presente",
            again["created_utc"] == saved.get("created_utc")
            and again["asked"] is True,
            again.get("created_utc"),
        )
        refreshed = feedback.pending(
            workdir,
            {"outcome": "failed", "balance": "high"},
            job_id="lavoro",
            refresh=True,
        )
        reread = json.loads(path.read_text(encoding="utf-8"))
        check(
            "F4 refresh riscrive il segnale per un nuovo giro",
            refreshed["asked"] is True
            and reread["context"]["outcome"] == "failed"
            and reread["created_utc"] == refreshed["created_utc"],
            reread["context"],
        )

        # Non blocca: una "workdir" che e' un file non si puo' scrivere.
        blocked = Path(raw) / "sono-un-file"
        blocked.write_text("x", encoding="utf-8")
        answer = feedback.pending(blocked, {"outcome": "done"})
        check(
            "F4 pending non solleva su una workdir non scrivibile",
            answer["asked"] is False and bool(answer.get("error")),
            answer.get("error"),
        )


# --------------------------------------------------------------------------- #
# F5 - la CLI: add dal report, report in italiano o JSON, voto invalido
# --------------------------------------------------------------------------- #


def section_cli() -> None:
    with tempfile.TemporaryDirectory(prefix="vx-fb-cli-") as raw:
        base = Path(raw)
        register = base / "registro"
        job = base / "lavoro"
        make_job(job, prompt="reel di prova")
        with register_at(register):
            code, out, err = cli_run(
                [
                    "feedback",
                    "add",
                    "--rating",
                    "5",
                    "--note",
                    "montaggio giusto",
                    "--workdir",
                    str(job),
                ]
            )
            rows = lines(register)
            check(
                "F5 add esce 0 e scrive una riga",
                code == 0 and len(rows) == 1,
                (code, len(rows), err.strip()),
            )
            check(
                "F5 add prende il contesto dal report (esito, stima, shot, font)",
                rows
                and rows[0]["context"]["outcome"] == "done"
                and rows[0]["context"]["balance"] == "ok"
                and rows[0]["context"]["estimate_seconds"] == 274.0
                and rows[0]["context"]["actual_seconds"] == 312.0
                and rows[0]["context"]["effects"] == 3
                and rows[0]["context"]["shots"] == 24
                and rows[0]["context"]["font"] == "Archivo Black"
                and rows[0]["context"]["duration_sec"] == 30.4,
                rows[0]["context"] if rows else "",
            )
            check(
                "F5 il prompt arriva dal progresso del lavoro",
                rows and rows[0]["prompt"] == "reel di prova" and rows[0]["job_id"] == "lavoro",
                (rows[0]["job_id"], rows[0]["prompt"]) if rows else "",
            )
            check(
                "F5 add parla italiano",
                code == 0 and "Giudizio registrato: 5/5" in out,
                out.strip(),
            )

            code, out, err = cli_run(["feedback", "report"])
            check(
                "F5 report senza --json parla italiano",
                code == 0
                and "Giudizi umani: 1" in out
                and "non e' conclusiva" in out
                and "MAPE" in out,
                out.strip().splitlines()[:3],
            )

            code, out, err = cli_run(["feedback", "report", "--json"])
            payload = json.loads(out) if out.strip() else {}
            check(
                "F5 report --json e' macchina e non prosa",
                code == 0
                and payload.get("count") == 1
                and payload.get("correlation", {}).get("n") == 1,
                (code, payload.get("count")),
            )

            code, out, err = cli_run(
                [
                    "feedback",
                    "add",
                    "--rating",
                    "7",
                    "--workdir",
                    str(job),
                ]
            )
            check(
                "F5 un voto fuori 1..5 esce 2 e non scrive",
                code == 2 and len(lines(register)) == 1 and "fuori intervallo" in err,
                (code, err.strip()),
            )


# --------------------------------------------------------------------------- #
# F6 - serve: POST /api/feedback e GET /api/state
# --------------------------------------------------------------------------- #


def section_serve() -> None:
    with tempfile.TemporaryDirectory(prefix="vx-fb-serve-") as raw:
        base = Path(raw)
        register = base / "registro"
        job = base / "lavoro"
        report = make_job(job, prompt="reel feedback")
        with register_at(register):
            server = serve.Server(base).start()
            try:
                before = get(server.url + "api/state")
                check(
                    "F6 prima del segnale: asked=false, given=false, rating=null",
                    before["feedback"] == {
                        "asked": False,
                        "given": False,
                        "rating": None,
                        "summary": before["feedback"]["summary"],
                    }
                    and before["feedback"]["summary"]["count"] == 0,
                    before["feedback"],
                )

                auto.write_feedback_request(report, job, "reel feedback")
                after_ask = get(server.url + "api/state")
                check(
                    "F6 il segnale di auto accende asked",
                    after_ask["feedback"]["asked"] is True
                    and after_ask["feedback"]["given"] is False,
                    after_ask["feedback"],
                )

                answer = post(server.url + "api/feedback?job=0&rating=4&note=buono")
                rows = lines(register)
                check(
                    "F6 POST /api/feedback registra il voto del job",
                    answer.get("ok") is True and answer.get("rating") == 4 and len(rows) == 1,
                    answer,
                )
                check(
                    "F6 il record di serve porta il contesto del lavoro",
                    rows
                    and rows[0]["job_id"] == "lavoro"
                    and rows[0]["note"] == "buono"
                    and rows[0]["context"]["outcome"] == "done"
                    and rows[0]["context"]["estimate_seconds"] == 274.0
                    and rows[0]["context"]["shots"] == 24
                    and rows[0]["prompt"] == "reel feedback",
                    rows[0]["context"] if rows else "",
                )
                check(
                    "F6 la correttezza passa anche dalla vista",
                    all(row["rating"] == 4 for row in rows),
                    [row["rating"] for row in rows],
                )

                given = get(server.url + "api/state")
                check(
                    "F6 dopo il voto: given=true, rating=4, summary aggiornato",
                    given["feedback"]["given"] is True
                    and given["feedback"]["rating"] == 4
                    and given["feedback"]["summary"]["count"] == 1
                    and given["feedback"]["summary"]["mean"] == 4.0,
                    given["feedback"],
                )

                refused = post(server.url + "api/feedback?job=0&rating=9")
                check(
                    "F6 un voto fuori intervallo non scrive e dice perche'",
                    refused.get("ok") is False
                    and "fuori intervallo" in str(refused.get("reason") or "")
                    and len(lines(register)) == 1,
                    refused,
                )
            finally:
                server.stop()

        empty = base / "vuoto"
        empty.mkdir()
        with register_at(base / "registro-vuoto"):
            server = serve.Server(empty).start()
            try:
                state = get(server.url + "api/state")
                check(
                    "F6 senza lavori lo stato espone un feedback vuoto, non un errore",
                    state["feedback"] == {
                        "asked": False,
                        "given": False,
                        "rating": None,
                        "summary": state["feedback"]["summary"],
                    },
                    state["feedback"],
                )
                answer = post(server.url + "api/feedback?job=0&rating=3")
                check(
                    "F6 senza lavori il POST rifiuta invece di inventare un job",
                    answer.get("ok") is False and answer.get("reason") == "nessun lavoro",
                    answer,
                )
            finally:
                server.stop()


# --------------------------------------------------------------------------- #
# F7 - auto: il segnale a fine lavoro, dopo guard e verification
# --------------------------------------------------------------------------- #


def section_auto() -> None:
    with tempfile.TemporaryDirectory(prefix="vx-fb-auto-") as raw:
        base = Path(raw)
        job = base / "lavoro"
        report = make_job(job, prompt="reel auto")
        with register_at(base / "registro"):
            request = auto.write_feedback_request(report, job, "reel auto")
            path = job / feedback.REQUEST_NAME
            saved = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            context = saved.get("context") or {}
            check(
                "F7 auto lascia feedback_request.json con asked=true",
                request["asked"] is True and saved.get("asked") is True,
                request.get("asked"),
            )
            check(
                "F7 il contesto e' gia' compilato dal report",
                context.get("outcome") == "done"
                and context.get("balance") == "ok"
                and context.get("violations") == []
                and context.get("estimate_seconds") == 274.0
                and context.get("actual_seconds") == 312.0
                and context.get("font") == "Archivo Black"
                and context.get("effects") == 3
                and context.get("shots") == 24
                and context.get("duration_sec") == 30.4,
                context,
            )
            check(
                "F7 il prompt del giro e' quello dell'utente",
                saved.get("prompt") == "reel auto" and saved.get("job_id") == "lavoro",
                (saved.get("job_id"), saved.get("prompt")),
            )

            broken = base / "rotto"
            broken.mkdir()
            answer = auto.write_feedback_request({}, broken, "")
            broken_saved = json.loads(
                (broken / feedback.REQUEST_NAME).read_text(encoding="utf-8")
            )
            check(
                "F7 un report vuoto non fa sollevare auto: esito incomplete",
                answer["asked"] is True
                and broken_saved["context"]["outcome"] == "incomplete"
                and broken_saved["context"]["estimate_seconds"] is None,
                broken_saved["context"],
            )

            # Un secondo giro sulla stessa workdir deve portare il **nuovo**
            # contesto: il voto riguarda l'ultimo lavoro, non il primo.
            failed_report = dict(report)
            failed_report["outcome_suggested"] = "failed"
            failed_report["delivered"] = False
            failed_report["guard"] = {
                "ok": False,
                "skipped": False,
                "violations": [{"code": "master_outside_pipeline"}],
                "warnings": [],
            }
            auto.write_feedback_request(failed_report, job, "reel auto")
            rerun = json.loads(path.read_text(encoding="utf-8"))
            check(
                "F7 un nuovo giro aggiorna il contesto (esito e violazioni)",
                rerun["context"]["outcome"] == "failed"
                and rerun["context"]["violations"] == ["master_outside_pipeline"],
                rerun["context"],
            )

            # Il punto di innesto: la scrittura sta dopo guard e verification.
            source = __import__("inspect").getsource(auto._run_locked)
            written_at = source.find("write_feedback_request(")
            check(
                "F7 auto scrive il segnale a fine lavoro, non prima",
                written_at > source.find("run_guard(")
                and written_at > source.find("verify_mod.verify_all("),
                written_at,
            )
            check(
                "F7 il segnale e' scritto dopo l'ultimo auto_report.json",
                written_at > source.rfind("_write_json_atomic(auto_report, report)"),
                written_at,
            )


def main() -> int:
    section_shape()
    section_atomic()
    section_report()
    section_pending()
    section_cli()
    section_serve()
    section_auto()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
