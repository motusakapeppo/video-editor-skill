"""vx.ask: una domanda nella vista, con opzioni, scadenza e default dichiarato.

Nessuna rete e nessun render: si provano il file della domanda, la risposta
valida, il valore inventato rifiutato, la scadenza, due domande in parallelo,
un file troncato e la vista che elenca e risponde.

    python tests/test_ask.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import ask, serve  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


OPTIONS = [
    {"value": "a", "label": "Prima", "detail": "la prima"},
    {"value": "b", "label": "Seconda", "detail": ""},
]


def ask_in_thread(work: Path, qid: str, **kwargs) -> tuple:
    """Avvia ``ask`` in un thread e restituisce (thread, risultato)."""
    result: dict = {}
    options = kwargs.pop("options", OPTIONS)
    default = kwargs.pop("default", "a")
    timeout = kwargs.pop("timeout", 10.0)

    def runner() -> None:
        result.update(
            ask.ask(
                work,
                qid,
                "Quale?",
                options,
                default=default,
                context={"stage": "triage"},
                timeout=timeout,
                poll=0.02,
                **kwargs,
            )
        )

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread, result


def check_valid_answer(raw: str) -> None:
    work = Path(raw) / "valida"
    thread, result = ask_in_thread(work, "q_valida")
    check(
        "la domanda compare mentre si aspetta",
        wait_for(lambda: ask.question_path(work, "q_valida").is_file()),
    )
    record = json.loads(ask.question_path(work, "q_valida").read_text(encoding="utf-8"))
    check(
        "il file ha le sei chiavi promesse",
        set(record) == {"id", "question", "options", "default", "created_utc", "context"},
        sorted(record),
    )
    check("l'id e' quello chiesto", record.get("id") == "q_valida", record.get("id"))
    check(
        "le opzioni sono {value,label,detail}",
        [item.get("value") for item in record.get("options") or []] == ["a", "b"]
        and all(set(item) == {"value", "label", "detail"} for item in record["options"]),
        record.get("options"),
    )
    check("il default e' scritto", record.get("default") == "a", record.get("default"))
    check(
        "il contesto viaggia con la domanda",
        record.get("context") == {"stage": "triage"},
        record.get("context"),
    )
    pending = ask.pending(work)
    check("la vista la elenca come in attesa", len(pending) == 1 and not pending[0]["answered"])
    check(
        "waiting_for e' un numero di secondi",
        isinstance(pending[0]["waiting_for"], (int, float)),
        pending[0]["waiting_for"],
    )

    invalid = ask.answer(work, "q_valida", "inventato")
    check("un valore fuori elenco e' rifiutato", invalid.get("ok") is False, invalid)
    check(
        "il rifiuto non chiude la domanda",
        ask.question_path(work, "q_valida").is_file(),
    )

    accepted = ask.answer(work, "q_valida", "b")
    check("una risposta valida e' accettata", accepted.get("ok") is True, accepted)
    thread.join(5)
    check("ask riceve il valore scelto", result.get("value") == "b", result)
    check("answered_by dice user", result.get("answered_by") == "user", result)
    check("timed_out resta falso", result.get("timed_out") is False, result)
    check(
        "la domanda chiusa sparisce dalla vista",
        not ask.question_path(work, "q_valida").is_file() and ask.pending(work) == [],
    )
    answer_file = ask.answer_path(work, "q_valida")
    stored = json.loads(answer_file.read_text(encoding="utf-8")) if answer_file.is_file() else {}
    check(
        "la risposta resta scritta, atomica",
        stored.get("value") == "b" and stored.get("id") == "q_valida",
        stored,
    )


def check_timeout(raw: str) -> None:
    work = Path(raw) / "scadenza"
    started = time.monotonic()
    result = ask.ask(
        work,
        "q_scaduta",
        "Senza risposta?",
        OPTIONS,
        default="a",
        timeout=0.15,
        poll=0.02,
    )
    elapsed = time.monotonic() - started
    check("allo scadere si usa il default", result.get("value") == "a", result)
    check("lo dichiara: answered_by default", result.get("answered_by") == "default", result)
    check("timed_out e' vero", result.get("timed_out") is True, result)
    check("l'attesa e' finita davvero", 0.1 <= elapsed < 3.0, round(elapsed, 3))
    check(
        "anche la domanda scaduta si chiude",
        not ask.question_path(work, "q_scaduta").is_file(),
    )


def check_parallel(raw: str) -> None:
    work = Path(raw) / "parallelo"
    first = ask_in_thread(work, "q_uno", timeout=5.0)
    second = ask_in_thread(work, "q_due", timeout=5.0)
    both = wait_for(
        lambda: ask.question_path(work, "q_uno").is_file()
        and ask.question_path(work, "q_due").is_file()
    )
    check("due domande convivono", both, [item["id"] for item in ask.pending(work)])
    answers = [ask.answer(work, "q_due", "b"), ask.answer(work, "q_uno", "a")]
    check("entrambe accettano una risposta", all(item.get("ok") for item in answers), answers)
    first[0].join(5)
    second[0].join(5)
    check(
        "ogni thread riceve la propria risposta",
        first[1].get("value") == "a" and second[1].get("value") == "b",
        (first[1].get("value"), second[1].get("value")),
    )


def check_truncated(raw: str) -> None:
    work = Path(raw) / "troncato"
    folder = work / ask.QUESTION_DIR
    folder.mkdir(parents=True)
    (folder / "q_troncata.json").write_text('{"id": "q_troncata", "quest', encoding="utf-8")
    check("un file troncato non e' una domanda", ask.pending(work) == [])

    thread, result = ask_in_thread(work, "q_troncata", timeout=5.0)
    wait_for(lambda: ask.question_path(work, "q_troncata").is_file())
    answer_file = ask.answer_path(work, "q_troncata")
    answer_file.write_text('{"id": "q_troncata", "value": "a"', encoding="utf-8")
    deadline = time.time() + 0.3
    while time.time() < deadline:
        time.sleep(0.02)
    check("una risposta troncata non chiude l'attesa", thread.is_alive())
    accepted = ask.answer(work, "q_troncata", "a")
    thread.join(5)
    check("dopo il troncamento una risposta valida passa", accepted.get("ok") is True, accepted)
    check("il risultato e' intatto", result.get("value") == "a", result)


def check_fail_soft(raw: str) -> None:
    blocker = Path(raw) / "non_una_cartella"
    blocker.write_text("sono un file", encoding="utf-8")
    started = time.monotonic()
    result = ask.ask(blocker, "q_rotta", "Dove scrivo?", OPTIONS, default="a", timeout=5.0)
    check("una cartella non scrivibile non ferma il lavoro", result.get("value") == "a", result)
    check("il guasto e' dichiarato", bool(result.get("error")) and bool(result.get("degraded")), result)
    check("il guasto e' immediato, non un'attesa", time.monotonic() - started < 2.0)


def state(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def post(url: str) -> dict:
    request = urllib.request.Request(url, method="POST", data=b"")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def check_view(raw: str) -> None:
    job = Path(raw) / "vista"
    job.mkdir(parents=True)
    (job / "progress.json").write_text(
        json.dumps({"prompt": "triage", "percent": 3.0, "current": "triage"}),
        encoding="utf-8",
    )
    folder = job / ask.QUESTION_DIR
    folder.mkdir()
    for qid, default in (("target_format", "9:16"), ("hook", "auto")):
        (folder / f"{qid}.json").write_text(
            json.dumps(
                {
                    "id": qid,
                    "question": f"Domanda {qid}?",
                    "options": [
                        {"value": default, "label": default, "detail": ""},
                        {"value": "altro", "label": "Altro", "detail": ""},
                    ],
                    "default": default,
                    "created_utc": "2026-01-01T00:00:00+00:00",
                    "context": {},
                }
            ),
            encoding="utf-8",
        )

    server = serve.Server(job).start()
    base = f"http://127.0.0.1:{server.port}"
    try:
        payload = state(base + "/api/state")
        questions = payload.get("questions") or []
        check("la vista elenca le domande aperte", len(questions) == 2, len(questions))
        check(
            "ogni voce ha la forma promessa",
            all(
                set(item)
                == {
                    "id",
                    "question",
                    "options",
                    "default",
                    "waiting_for",
                    "answered",
                    "answer",
                }
                for item in questions
            ),
            questions[0] if questions else {},
        )

        invalid = post(
            base
            + "/api/answer?job=0&id=target_format&value="
            + urllib.parse.quote("non-esiste")
        )
        check("la vista rifiuta un valore inventato", invalid.get("ok") is False, invalid)
        check(
            "la domanda resta aperta dopo il rifiuto",
            any(item["id"] == "target_format" for item in state(base + "/api/state")["questions"]),
        )

        accepted = post(
            base
            + "/api/answer?job=0&id=target_format&value="
            + urllib.parse.quote("9:16")
        )
        check("la vista risponde a una domanda aperta", accepted.get("ok") is True, accepted)
        after = state(base + "/api/state").get("questions") or []
        check(
            "la domanda risposta sparisce dalla vista",
            [item["id"] for item in after] == ["hook"],
            [item["id"] for item in after],
        )
        check(
            "l'altra domanda resta in attesa",
            after and after[0].get("answered") is False and after[0].get("answer") is None,
            after[0] if after else {},
        )

        missing = post(base + "/api/answer?job=0&id=inesistente&value=x")
        check("una domanda inesistente risponde, non crasha", missing.get("ok") is False, missing)
    finally:
        server.stop()


def check_live_view(raw: str) -> None:
    """Il giro intero: ``ask`` scrive, la vista elenca, il POST sblocca ``ask``."""
    job = Path(raw) / "vista_live"
    job.mkdir(parents=True)
    (job / "progress.json").write_text(
        json.dumps({"prompt": "live", "percent": 1.0, "current": "triage"}),
        encoding="utf-8",
    )
    thread, result = ask_in_thread(job, "q_live", timeout=10.0)
    server = serve.Server(job).start()
    base = f"http://127.0.0.1:{server.port}"
    try:
        listed = wait_for(
            lambda: any(
                item["id"] == "q_live" for item in state(base + "/api/state")["questions"]
            )
        )
        check("la domanda scritta da ask compare nella vista", listed)
        accepted = post(base + "/api/answer?job=0&id=q_live&value=a")
        check("il POST della vista chiude la domanda", accepted.get("ok") is True, accepted)
        thread.join(5)
        check(
            "ask si sblocca con la risposta della vista",
            result.get("value") == "a" and result.get("answered_by") == "user",
            result,
        )
    finally:
        server.stop()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-ask-") as raw:
        print("S1 - risposta valida e file della domanda")
        check_valid_answer(raw)
        print("S2 - scadenza e default dichiarato")
        check_timeout(raw)
        print("S3 - due domande in parallelo")
        check_parallel(raw)
        print("S4 - file troncato")
        check_truncated(raw)
        print("S5 - scrittura fail-soft")
        check_fail_soft(raw)
        print("S6 - la vista elenca e risponde")
        check_view(raw)
        print("S7 - il giro intero: ask -> vista -> ask")
        check_live_view(raw)

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
