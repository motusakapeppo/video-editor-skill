"""Caos controllato: nessun accessorio puo' fermare un montaggio.

La regola che questo test difende e' una sola riga: un lavoro si ferma per la
sorgente illeggibile, per ffmpeg assente, per un piano che non regge o per la
guardia alla consegna. Pannello, registro di sessione, learning, feedback,
decisioni, verifica e vista sono accessori: se cedono, il lavoro **continua** e
il report lo dichiara in ``degraded``.

Ogni caso e' simulato con ``monkeypatch`` a mano (attributo sostituito e
ripristinato in ``finally``) e cartelle temporanee: niente rete, niente ffmpeg,
niente lavoro vero.

    R1  pannello: PermissionError sulla scrittura -> nessuna eccezione
    R2  pannello: disco pieno (OSError 28) su os.replace -> file intatto
    R3  pannello: progress.json troncato -> la riscrittura e' valida
    R4  feedback: registro non scrivibile -> stored false, non solleva
    R5  feedback: riga troncata nel registro -> si legge il resto
    R6  feedback: aggregati rotti -> report vuoto, non eccezione
    R7  feedback: segnale in workdir non scrivibile -> asked false con motivo
    R8  learning: radice non creabile -> store_failure non solleva
    R9  learning: indice eventi rotto -> store_failure scrive lo stesso
    R10 learning: eventi troncati -> si leggono i validi
    R11 learning: policy troncata -> soglie di partenza
    R12 guard: audit che solleva -> skipped/degraded, non failed
    R13 verify: check che solleva -> degrado dichiarato, verdetto valore
    R14 serve: cartella vuota -> stato idle, non 500
    R15 serve: progress.json troncato -> stato di ripiego
    R16 serve: GET /api/state -> 200
    R17 serve: POST /api/feedback -> 200 (non 500)
    R18 serve: voto su registro non scrivibile -> ok false, non eccezione
    R19 session: VX_SESSION_DIR non creabile -> sessione in memoria, degrado
    R20 session: registro scrivibile -> la sessione si rilegge
    R21 degrade: il report porta nomi e motivi, una volta sola
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, degrade, feedback, learning, panel, session, verify  # noqa: E402
from vx import serve  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


class Denied(PermissionError):
    """Un permesso negato che si riconosce nel messaggio del test."""


# --------------------------------------------------------------------------- #
# R1-R3  pannello
# --------------------------------------------------------------------------- #


def test_panel() -> None:
    print("\nR1-R3  pannello")
    with tempfile.TemporaryDirectory(prefix="vx-rob-panel-") as raw:
        where = Path(raw)
        target = where / "progress.html"
        board = panel.Panel(target, phases=[("render", "Renderizzo")])
        board.write()
        before = (where / "progress.json").read_bytes()

        degrade.clear()
        original = panel.os.replace

        def denied(*args, **kwargs):
            raise Denied(13, "Access denied")

        panel.os.replace = denied
        try:
            board.record(50.0, "render", elapsed=1.0)
            raised = False
        except Exception:  # noqa: BLE001 - il test misura proprio questo
            raised = True
        finally:
            panel.os.replace = original

        check("R1 PermissionError: record() ritorna, il lavoro continua", not raised)
        check(
            "R1 il progress.json gia' scritto resta intatto",
            (where / "progress.json").read_bytes() == before,
        )
        check(
            "R1 nessun temporaneo lasciato dietro",
            not list(where.glob("*.tmp")),
            [p.name for p in where.glob("*.tmp")],
        )
        check(
            "R1 degrado dichiarato una volta, col motivo",
            board.degraded.get("panel", "").startswith("Denied")
            and "panel" in degrade.names(),
            board.degraded,
        )

        degrade.clear()
        full = panel.Panel(where / "pieno" / "progress.html")
        full.write()
        snapshot = (where / "pieno" / "progress.json").read_bytes()

        def disk_full(*args, **kwargs):
            raise OSError(28, "No space left on device")

        panel.os.replace = disk_full
        try:
            full.record(80.0, "qc", elapsed=2.0)
            raised = False
        except Exception:  # noqa: BLE001
            raised = True
        finally:
            panel.os.replace = original
        check("R2 OSError(28) su os.replace: nessuna eccezione", not raised)
        try:
            parsed = json.loads((where / "pieno" / "progress.json").read_text(encoding="utf-8"))
            clean = True
        except Exception:  # noqa: BLE001
            parsed, clean = {}, False
        check(
            "R2 il file resta un JSON valido e precedente",
            clean and (where / "pieno" / "progress.json").read_bytes() == snapshot,
        )
        check(
            "R2 disco pieno dichiarato in degraded",
            full.degraded.get("panel", "").startswith("OSError") and "panel" in degrade.names(),
            full.degraded,
        )

        # Un progress.json troncato non impedisce la riscrittura valida.
        truncated = where / "troncato"
        truncated.mkdir()
        (truncated / "progress.json").write_text('{"percent": 42, "jo', encoding="utf-8")
        board2 = panel.Panel(truncated / "progress.html", phases=[("render", "Renderizzo")])
        board2.record(10.0, "render")
        try:
            data = json.loads((truncated / "progress.json").read_text(encoding="utf-8"))
            ok = data["percent"] == 10.0
        except Exception:  # noqa: BLE001
            ok = False
        check("R3 progress.json troncato: la riscrittura e' valida", ok)


# --------------------------------------------------------------------------- #
# R4-R7  feedback
# --------------------------------------------------------------------------- #


def test_feedback() -> None:
    print("\nR4-R7  feedback")
    with tempfile.TemporaryDirectory(prefix="vx-rob-fb-") as raw:
        where = Path(raw)
        blocker = where / "blocker"
        blocker.write_text("not a folder\n", encoding="utf-8")
        uncreatable = blocker / "sub"

        degrade.clear()
        record = feedback.make_record(job_id="j1", rating=4, prompt="prova")
        try:
            written = feedback.append(record, root=uncreatable)
            raised = False
        except Exception:  # noqa: BLE001
            written, raised = {}, True
        check("R4 registro non scrivibile: append ritorna, non solleva", not raised)
        check(
            "R4 la riga non entra ma il motivo e' dichiarato",
            written.get("stored") is False and bool(written.get("error")),
            written,
        )
        check("R4 degrado feedback annotato", "feedback" in degrade.names(), degrade.names())

        degrade.clear()
        register = where / "reg"
        register.mkdir()
        path = register / feedback.REGISTER_NAME
        good = feedback.make_record(job_id="ok", rating=5)
        path.write_text(
            json.dumps(good, ensure_ascii=False) + "\n" + '{"rating": 4, "job_id": "tronc',
            encoding="utf-8",
        )
        try:
            rows = feedback.read_all(root=register)
            raised = False
        except Exception:  # noqa: BLE001
            rows, raised = [], True
        check(
            "R5 riga troncata: si legge il resto, nessuna eccezione",
            not raised and len(rows) == 1 and rows[0]["job_id"] == "ok",
            len(rows),
        )

        degrade.clear()
        original = feedback._build_report

        def broken(*args, **kwargs):
            raise ValueError("registro senza forma")

        feedback._build_report = broken
        try:
            data = feedback.report(root=register)
            raised = False
        except Exception:  # noqa: BLE001
            data, raised = {}, True
        finally:
            feedback._build_report = original
        check(
            "R6 aggregati rotti: report di ripiego, non eccezione",
            not raised and data.get("count") == 0 and bool(data.get("error")),
            data.get("error"),
        )

        degrade.clear()
        workdir = blocker / "work"
        answer = feedback.pending(workdir, {"outcome": "done"}, job_id="j", prompt="p")
        check(
            "R7 workdir non scrivibile: asked false col motivo, non eccezione",
            answer.get("asked") is False and bool(answer.get("error")),
            answer,
        )


# --------------------------------------------------------------------------- #
# R8-R11  learning
# --------------------------------------------------------------------------- #


def test_learning() -> None:
    print("\nR8-R11  learning")
    with tempfile.TemporaryDirectory(prefix="vx-rob-learn-") as raw:
        where = Path(raw)
        blocker = where / "blocker"
        blocker.write_text("not a folder\n", encoding="utf-8")
        uncreatable = blocker / "learning"

        degrade.clear()
        try:
            written = learning.store_failure("job-x", {"p": "testo"}, {"outcome": "done"}, root=uncreatable)
            raised = False
        except Exception:  # noqa: BLE001
            written, raised = {}, True
        check("R8 radice non creabile: store_failure non solleva", not raised)
        check(
            "R8 il corpus non entra e il motivo e' scritto",
            written.get("stored") is False and bool(written.get("error")),
            written,
        )
        check("R8 degrado learning annotato", "learning" in degrade.names(), degrade.names())

        root = where / "learning"
        degrade.clear()
        original = learning.append_event

        def broken_index(*args, **kwargs):
            raise OSError(5, "indice illeggibile")

        learning.append_event = broken_index
        try:
            written = learning.store_failure(
                "job-ok", {"p": "testo"}, {"outcome": "done", "rating": 4}, root=root
            )
            raised = False
        except Exception:  # noqa: BLE001
            written, raised = {}, True
        finally:
            learning.append_event = original
        folder = root / "corpus" / "job-ok"
        check(
            "R9 indice eventi rotto: store_failure scrive lo stesso",
            not raised and written.get("stored") is True and (folder / "plan.json").is_file(),
            written.get("error", "scritto"),
        )
        check("R9 il cedimento dell'indice e' dichiarato", "learning" in degrade.names())

        # Un evento troncato non porta via i validi.
        month = where / "events"
        month.mkdir()
        (month / "2026-11.jsonl").write_text(
            '{"ts": "2026-11-01T00:00:00Z", "kind": "failure", "payload": {}}\n'
            '{"ts": "2026-11-02T00:00:00Z", "kind": "tronc',
            encoding="utf-8",
        )
        try:
            rows = learning.read_events(root=where)
            raised = False
        except Exception:  # noqa: BLE001
            rows, raised = [], True
        check(
            "R10 eventi troncati: si leggono i validi, nessuna eccezione",
            not raised and len(rows) == 1,
            len(rows),
        )

        policy_dir = where / "policy"
        policy_dir.mkdir()
        (policy_dir / learning.POLICY_NAME).write_text('{"version": 3, "thresh', encoding="utf-8")
        try:
            policy = learning.policy_load(root=where)
            raised = False
        except Exception:  # noqa: BLE001
            policy, raised = {}, True
        check(
            "R11 policy troncata: soglie di partenza, non eccezione",
            not raised
            and policy.get("version") == 0
            and policy["thresholds"] == learning.DEFAULT_THRESHOLDS,
            policy.get("version"),
        )


# --------------------------------------------------------------------------- #
# R12  guard
# --------------------------------------------------------------------------- #


def test_guard() -> None:
    print("\nR12  guard")
    with tempfile.TemporaryDirectory(prefix="vx-rob-guard-") as raw:
        where = Path(raw)
        degrade.clear()
        original = auto.guard.audit

        def broken(directory):
            raise Denied(13, "audit negato")

        auto.guard.audit = broken
        try:
            section = auto.run_guard(type("A", (), {"no_guard": False})(), where)
            raised = False
        except Exception:  # noqa: BLE001
            section, raised = {}, True
        finally:
            auto.guard.audit = original

        check("R12 audit che solleva: run_guard ritorna, non solleva", not raised)
        check(
            "R12 la guardia si dichiara saltata e degradata",
            section.get("skipped") is True and section.get("degraded") is True,
            section.get("why"),
        )
        report: dict = {"ok": True}
        auto.apply_guard(report, section)
        check(
            "R12 il lavoro non diventa failed per la guardia rotta",
            report.get("delivered") is True
            and report.get("outcome_suggested") == "done"
            and not report.get("failures"),
            report.get("outcome_suggested"),
        )
        check("R12 degrado guard annotato", "guard" in degrade.names(), degrade.names())


# --------------------------------------------------------------------------- #
# R13  verify
# --------------------------------------------------------------------------- #


def test_verify() -> None:
    print("\nR13  verify")
    degrade.clear()
    original = verify.CHECKS["duration"]

    def broken(master, report):
        raise Denied(13, "master non leggibile")

    verify.CHECKS["duration"] = broken
    report: dict = {"decisions": []}
    try:
        verdict = verify.verify_all(report, "qualunque.mp4", checks=["duration"])
        raised = False
    except Exception:  # noqa: BLE001
        verdict, raised = {}, True
    finally:
        verify.CHECKS["duration"] = original

    check("R13 un check che solleva non ferma il verdetto", not raised)
    check(
        "R13 il verdetto e' un valore e il check non eseguito e' dichiarato",
        verdict.get("ok") is False and verdict.get("degraded") == ["duration"],
        verdict.get("degraded"),
    )
    check(
        "R13 lo stesso verdetto e' nel report",
        isinstance(report.get("verification"), dict)
        and report["verification"].get("degraded") == ["duration"],
    )


# --------------------------------------------------------------------------- #
# R14-R18  serve
# --------------------------------------------------------------------------- #


def _http(url: str, *, post: bool = False) -> tuple:
    request = urllib.request.Request(url, method="POST" if post else "GET", data=b"" if post else None)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:  # pragma: no cover - solo diagnosi
        return int(exc.code), exc.read()


def test_serve() -> None:
    print("\nR14-R18  serve")
    with tempfile.TemporaryDirectory(prefix="vx-rob-serve-") as raw:
        where = Path(raw)
        empty = where / "empty"
        empty.mkdir()

        try:
            payload = serve.state(empty)
            raised = False
        except Exception:  # noqa: BLE001
            payload, raised = {}, True
        check(
            "R14 cartella vuota: stato idle, non eccezione",
            not raised and payload.get("outcome") == "idle" and payload.get("jobs") == [],
            payload.get("outcome"),
        )

        job = where / "job"
        job.mkdir()
        (job / "progress.json").write_text('{"prompt": "reel", "percent": 4', encoding="utf-8")
        try:
            payload = serve.state(job)
            raised = False
        except Exception:  # noqa: BLE001
            payload, raised = {}, True
        check(
            "R15 progress.json troncato: stato di ripiego, nessuna eccezione",
            not raised and payload.get("progress_source") == "json",
            payload.get("progress_source"),
        )

        server = serve.Server(empty).start()
        try:
            status, body = _http(server.url + "api/state")
            check("R16 GET /api/state risponde 200", status == 200, status)
            try:
                decoded = json.loads(body.decode("utf-8"))
                ok = "outcome" in decoded
            except Exception:  # noqa: BLE001
                ok = False
            check("R16 il corpo e' uno stato JSON, non un errore", ok)

            status, body = _http(server.url + "api/feedback?rating=4", post=True)
            check("R17 POST /api/feedback risponde 200", status == 200, status)
            try:
                decoded = json.loads(body.decode("utf-8"))
                ok = isinstance(decoded, dict) and "ok" in decoded
            except Exception:  # noqa: BLE001
                ok = False
            check("R17 il corpo e' un oggetto JSON, non un 500", ok)
        finally:
            server.stop()

        # Un registro non scrivibile: serve.add_feedback risponde, non solleva.
        blocker = where / "blocker"
        blocker.write_text("file, non cartella\n", encoding="utf-8")
        previous = os.environ.get(feedback.ROOT_ENV)
        os.environ[feedback.ROOT_ENV] = str(blocker / "reg")
        degrade.clear()
        try:
            answer = serve.add_feedback(job, rating=4)
            raised = False
        except Exception:  # noqa: BLE001
            answer, raised = {}, True
        finally:
            if previous is None:
                os.environ.pop(feedback.ROOT_ENV, None)
            else:
                os.environ[feedback.ROOT_ENV] = previous
        check("R18 registro non scrivibile: add_feedback risponde", not raised)
        check(
            "R18 ok false col motivo, e degrado dichiarato",
            answer.get("ok") is False and answer.get("degraded") is True,
            answer,
        )


# --------------------------------------------------------------------------- #
# R19-R21  session e degrade
# --------------------------------------------------------------------------- #


def test_session_and_report() -> None:
    print("\nR19-R21  session e report")
    with tempfile.TemporaryDirectory(prefix="vx-rob-session-") as raw:
        where = Path(raw)
        blocker = where / "blocker"
        blocker.write_text("file, non cartella\n", encoding="utf-8")
        previous = os.environ.get(session.ENV_DIR)
        os.environ[session.ENV_DIR] = str(blocker / "sessions")

        degrade.clear()
        try:
            opened = session.begin("auto", ["auto", "--prompt", "x"], str(where))
            raised = False
        except Exception:  # noqa: BLE001
            opened, raised = None, True
        check("R19 VX_SESSION_DIR non creabile: begin non solleva", not raised)
        check(
            "R19 la sessione vive in memoria, disabilitata col motivo",
            opened is not None and opened.active is False and bool(opened.last_error),
            getattr(opened, "last_error", ""),
        )
        try:
            opened.stage("render", 10.0, "prova")
            opened.end("done")
            no_raise = True
        except Exception:  # noqa: BLE001
            no_raise = False
        check("R19 gli eventi in memoria non sollevano", no_raise)
        check("R19 degrado session annotato", "session" in degrade.names(), degrade.names())
        check("R19 la vista legge lo stato vuoto, non un errore", session.load() is None)

        if previous is None:
            os.environ.pop(session.ENV_DIR, None)
        else:
            os.environ[session.ENV_DIR] = previous

        os.environ[session.ENV_DIR] = str(where / "sessions")
        try:
            second = session.begin("auto", ["auto"], str(where))
            second.set_job(where)
            second.stage("triage", 5.0, "Guardo le clip")
            second.end("done")
            loaded = session.load()
        finally:
            os.environ.pop(session.ENV_DIR, None)
        check(
            "R20 registro scrivibile: la sessione si rilegge",
            loaded is not None and loaded.status == "done" and len(loaded.events) >= 3,
            len(getattr(loaded, "events", []) or []),
        )
        snapshot = loaded.as_dict() if loaded is not None else {}
        check(
            "R20 lo snapshot ha la forma promessa alla vista",
            {"id", "status", "events_path", "stages", "gate_state", "journal"} <= set(snapshot),
            sorted(snapshot)[:4],
        )

        degrade.clear()
        degrade.note("panel", Denied(13, "Access denied"))
        degrade.note("panel", Denied(13, "secondo guasto"))
        report: dict = {"ok": True}
        degrade.merge_into(report)
        check(
            "R21 il report porta i nomi degradati",
            report.get("degraded") == ["panel"],
            report.get("degraded"),
        )
        check(
            "R21 il motivo e' accanto al nome, e il primo vince",
            "Access denied" in report.get("degraded_why", {}).get("panel", ""),
            report.get("degraded_why"),
        )
        check(
            "R21 la riga di narrazione nomina il cedimento e il motivo",
            "panel" in degrade.text(report) and "Access denied" in degrade.text(report),
            degrade.text(report),
        )


def main() -> int:
    test_panel()
    test_feedback()
    test_learning()
    test_guard()
    test_verify()
    test_serve()
    test_session_and_report()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
