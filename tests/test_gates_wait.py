"""vx.auto: un cancello aspetta solo quando c'e' qualcuno che puo' rispondere.

Niente rete e niente render: i cancelli si provano con proposte finte e con
attese brevi. Il canale "c'e' un client" e' il timbro ``view_client.json`` che
``vx serve`` lascia nella workdir; qui si scrive a mano.

    python tests/test_gates_wait.py
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import ask, auto, serve  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:70} {detail}")


def skip(label: str, detail: str = "") -> None:
    global SKIP
    SKIP += 1
    print(f"{'SKIP':4} {label:70} {detail}")


def stamp_client(workdir: Path, *, age_sec: float = 0.0) -> None:
    (workdir / auto.VIEW_CLIENT_NAME).write_text(
        json.dumps(
            {
                "schema": auto.VIEW_CLIENT_SCHEMA,
                "at_epoch": time.time() - age_sec,
            }
        ),
        encoding="utf-8",
    )


def fake_proposal(workdir, requirement, energy, *, allow_network=False, **kwargs):
    """Una proposta valida senza rete: serve solo al cancello."""
    payload = {
        "schema": "vx.fontproposal/1",
        "requirement": str(requirement),
        "pool": "local",
        "chosen": "Inter",
        "candidates": [
            {
                "family": "Inter",
                "licence": "commercial",
                "source": "bundle",
                "downloaded": False,
                "file": str(Path(workdir) / "fonts" / "Inter.ttf"),
                "score": 0.9,
                "because": "prova",
            },
            {
                "family": "Oswald",
                "licence": "commercial",
                "source": "bundle",
                "downloaded": False,
                "file": str(Path(workdir) / "fonts" / "Oswald.ttf"),
                "score": 0.7,
                "because": "prova",
            },
        ],
    }
    (Path(workdir) / auto.FONT_PROPOSAL_NAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return payload


class Plan:
    duration_sec = 10.0
    aspect_ratio = "9:16"
    video_tracks: list = []


def test_client_stamp() -> None:
    print("\n-- chi guarda: il timbro lasciato dalla vista")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        check(
            "senza timbro nessuno puo' rispondere",
            auto.view_client_recent(workdir, ttl=5.0) is False,
        )
        stamp_client(workdir)
        check(
            "un timbro fresco dice che c'e' un client",
            auto.view_client_recent(workdir, ttl=5.0) is True,
        )
        stamp_client(workdir, age_sec=10.0)
        check(
            "un timbro vecchio non conta piu'",
            auto.view_client_recent(workdir, ttl=5.0) is False,
        )
        (workdir / auto.VIEW_CLIENT_NAME).unlink(missing_ok=True)
        check(
            "il pannello da solo non e' una vista agganciata",
            auto.view_is_attached(workdir, panel=object()) is False,
        )
        stamp_client(workdir)
        check(
            "pannello + client fresco = c'e' qualcuno",
            auto.view_is_attached(workdir, panel=object()) is True,
        )
        check(
            "un --watch esplicito vince anche senza timbro",
            auto.view_is_attached(
                workdir, panel=object(), view_url="http://127.0.0.1:8100/"
            )
            is True,
        )


def test_triage_without_view() -> None:
    print("\n-- triage senza vista: nessuna attesa")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        started = time.monotonic()
        entries = auto.collect_triage_questions(
            workdir, view_active=False, wait=20.0
        )
        elapsed = time.monotonic() - started
        check("nessuno da aspettare: torna subito", elapsed < 0.5, f"{elapsed:.3f}s")
        check(
            "tutte le risposte sono dichiarate auto",
            all(item["by"] == "auto" for item in entries),
            [item["by"] for item in entries],
        )
        check(
            "nessun cancello scaduto senza dirlo",
            all(item["expired"] is False for item in entries),
        )
        check(
            "waited_sec e' zero, non il timeout",
            all(float(item["waited_sec"]) == 0.0 for item in entries),
        )
        check(
            "nessuna domanda scritta nella workdir",
            not (workdir / ask.QUESTION_DIR).exists(),
        )


def test_triage_explicit_wait_wins() -> None:
    print("\n-- triage: un'attesa esplicita vince anche senza vista")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        started = time.monotonic()
        entries = auto.collect_triage_questions(
            workdir, view_active=False, wait=0.3, ask_anyway=True
        )
        elapsed = time.monotonic() - started
        check(
            "ha aspettato la scadenza chiesta",
            0.25 <= elapsed < 2.0,
            f"{elapsed:.3f}s",
        )
        check(
            "ogni risposta dichiara timeout",
            all(item["by"] == "timeout" for item in entries),
            [item["by"] for item in entries],
        )
        check(
            "ogni cancello scaduto dichiara expired",
            all(item["expired"] is True for item in entries),
        )
        check(
            "waited_sec e' quello vero",
            all(float(item["waited_sec"]) >= 0.2 for item in entries),
            [item["waited_sec"] for item in entries],
        )


def test_triage_with_client() -> None:
    print("\n-- triage con un client che risponde")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        stamp_client(workdir)

        def responder() -> None:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                pending = ask.pending(workdir)
                if len(pending) >= len(auto.TRIAGE_QUESTIONS):
                    for item in pending:
                        ask.answer(workdir, item["id"], item["default"])
                    return
                time.sleep(0.02)

        thread = threading.Thread(target=responder, daemon=True)
        thread.start()
        started = time.monotonic()
        entries = auto.collect_triage_questions(
            workdir, view_active=True, wait=3.0
        )
        elapsed = time.monotonic() - started
        thread.join(2.0)
        check("la vista risponde e il cancello si chiude", elapsed < 2.0, f"{elapsed:.3f}s")
        check(
            "tutte le scelte sono by user",
            all(item["by"] == "user" for item in entries),
            [item["by"] for item in entries],
        )
        check(
            "nessuna scadenza: expired resta falso",
            all(item["expired"] is False for item in entries),
        )
        check(
            "waited_sec e' il tempo vero, non 3 s",
            all(0.0 < float(item["waited_sec"]) < 2.0 for item in entries),
            [item["waited_sec"] for item in entries],
        )


def test_answer_effects() -> None:
    print("\n-- risposte: ognuna dice se ha cambiato qualcosa")
    answered = [
        {"id": "target_format", "value": "16:9", "answered_by": "user"},
        {"id": "hook", "value": "testo", "answered_by": "user"},
        {"id": "subtitles", "value": "nessuno", "answered_by": "user"},
    ]
    unapplied = auto.declare_question_answers(answered)
    check(
        "solo i sottotitoli hanno un interruttore",
        unapplied == ["target_format", "hook"],
        unapplied,
    )
    check(
        "il sottotitoli risulta applicato",
        answered[2].get("applied") is True,
        answered[2],
    )
    check(
        "ogni risposta porta il suo perche'",
        all(item.get("why") for item in answered),
        [item.get("why") for item in answered],
    )
    defaults = [
        {"id": "target_format", "value": "9:16", "answered_by": "default"},
        {"id": "subtitles", "value": "karaoke", "answered_by": "default"},
    ]
    none = auto.declare_question_answers(defaults)
    check(
        "un default non e' una scelta applicata e non accusa nessuno",
        none == [] and defaults[1]["applied"] is False,
        (none, defaults[1]),
    )


def test_font_gate() -> None:
    print("\n-- font: proposta senza attesa, e scadenza dichiarata")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        started = time.monotonic()
        closed = auto.font_gate(
            workdir,
            requirement="reel",
            energy=0.8,
            wait=0.0,
            gate=False,
            propose=fake_proposal,
        )
        elapsed = time.monotonic() - started
        check("cancello chiuso: nessuna attesa", elapsed < 0.5, f"{elapsed:.3f}s")
        check(
            "cancello chiuso: by auto e non expired",
            closed["by"] == "auto" and closed["expired"] is False,
            (closed["by"], closed["expired"]),
        )
        check(
            "cancello chiuso: asked e' falso",
            closed["asked"] is False,
            closed["asked"],
        )

        started = time.monotonic()
        expired = auto.font_gate(
            workdir,
            requirement="reel",
            energy=0.8,
            wait=0.2,
            gate=True,
            propose=fake_proposal,
        )
        elapsed = time.monotonic() - started
        check("attesa esplicita: aspetta davvero", elapsed >= 0.15, f"{elapsed:.3f}s")
        check(
            "alla scadenza by timeout e expired",
            expired["by"] == "timeout" and expired["expired"] is True,
            (expired["by"], expired["expired"]),
        )
        check(
            "waited_sec e' il tempo vero",
            float(expired["waited_sec"]) >= 0.15,
            expired["waited_sec"],
        )


def test_font_wait_flag_wins() -> None:
    print("\n-- --font-wait esplicito vince; senza vista il default e' zero")
    observed: dict = {}
    original = auto.font_gate

    def capture(workdir, **kwargs):
        observed.clear()
        observed.update(kwargs)
        return {
            "family": "Inter",
            "selected_by": "auto",
            "waited_sec": 0.0,
            "asked": False,
            "by": "auto",
            "expired": False,
        }

    auto.font_gate = capture
    try:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            args = argparse.Namespace(
                font_wait=5.0, font_network=False, no_font_gate=False
            )
            auto._run_font_gate(
                args, workdir, Plan(), {}, view_active=False
            )
            check(
                "--font-wait 5 apre il cancello per 5 s senza vista",
                observed.get("wait") == 5.0 and observed.get("gate") is True,
                (observed.get("wait"), observed.get("gate")),
            )

            args = argparse.Namespace(
                font_wait=None, font_network=False, no_font_gate=False
            )
            auto._run_font_gate(
                args, workdir, Plan(), {}, view_active=False
            )
            check(
                "senza vista il default non aspetta",
                observed.get("wait") == 0.0 and observed.get("gate") is False,
                (observed.get("wait"), observed.get("gate")),
            )

            auto._run_font_gate(
                args, workdir, Plan(), {}, view_active=True
            )
            check(
                "con la vista il default e' 180 s",
                observed.get("wait") == auto.FONT_WAIT_DEFAULT
                and observed.get("gate") is True,
                (observed.get("wait"), observed.get("gate")),
            )
    finally:
        auto.font_gate = original


def test_key_gate() -> None:
    print("\n-- fotogrammi: la stessa regola del font")
    observed: dict = {}
    original = auto.keyframe_gate

    def capture(workdir, **kwargs):
        observed.clear()
        observed.update(kwargs)
        return {
            "selected_by": "auto",
            "waited_sec": 0.0,
            "asked": False,
            "by": "auto",
            "expired": False,
        }

    auto.keyframe_gate = capture
    try:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            args = argparse.Namespace(key_gate=None, key_wait=None)
            auto._run_key_gate(args, workdir, Plan(), view_active=False)
            check(
                "senza vista non si aspetta",
                observed.get("wait") == 0.0 and observed.get("gate") is False,
                (observed.get("wait"), observed.get("gate")),
            )

            auto._run_key_gate(args, workdir, Plan(), view_active=True)
            check(
                "con la vista il default e' 180 s",
                observed.get("wait") == auto.KEY_GATE_WAIT_DEFAULT
                and observed.get("gate") is True,
                (observed.get("wait"), observed.get("gate")),
            )

            explicit = argparse.Namespace(key_gate=None, key_wait=5.0)
            auto._run_key_gate(explicit, workdir, Plan(), view_active=False)
            check(
                "--key-wait 5 vince anche senza vista",
                observed.get("wait") == 5.0 and observed.get("gate") is True,
                (observed.get("wait"), observed.get("gate")),
            )
    finally:
        auto.keyframe_gate = original


def test_report() -> None:
    print("\n-- report: per ogni cancello asked, by, waited_sec, expired, choice")
    questions = [
        {
            "id": "hook",
            "value": "auto",
            "answered_by": "timeout",
            "by": "timeout",
            "asked": True,
            "waited_sec": 1.2,
            "expired": True,
            "choice": "auto",
        }
    ]
    report = auto.gates_report(
        questions=questions,
        font_section={
            "asked": True,
            "selected_by": "auto_after_timeout",
            "waited_sec": 0.5,
            "expired": True,
            "choice": "Inter",
        },
        key_section={
            "asked": False,
            "selected_by": "auto",
            "waited_sec": 0.0,
            "expired": False,
            "choice": "auto",
        },
        plan_section={
            "asked": False,
            "selected_by": "budget",
            "waited_sec": 0.0,
            "expired": False,
            "chosen": "full",
        },
    )
    check(
        "il totale somma i waited_sec veri",
        report["total_waited_sec"] == 1.7,
        report["total_waited_sec"],
    )
    check(
        "ogni cancello ha i cinque campi",
        all(
            {"asked", "answered_by", "by", "waited_sec", "expired", "choice"}
            <= set(item)
            for item in report["items"].values()
        ),
        report["items"],
    )
    check(
        "il triage scaduto e' by timeout con expired",
        report["items"]["triage"]["by"] == "timeout"
        and report["items"]["triage"]["expired"] is True,
        report["items"]["triage"],
    )
    check(
        "il font scaduto e' by timeout con expired",
        report["items"]["font"]["by"] == "timeout"
        and report["items"]["font"]["expired"] is True,
        report["items"]["font"],
    )
    check(
        "nessun cancello scade senza expired: true",
        all(
            not item["answered_by"] == "timeout" or item["expired"] is True
            for item in report["items"].values()
        ),
        report["items"],
    )


def test_view_state_and_stamp() -> None:
    print("\n-- la vista: gates con conto alla rovescia e timbro del client")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        (workdir / auto.GATE_STATUS_NAME).write_text(
            json.dumps(
                {
                    "schema": auto.GATE_STATUS_SCHEMA,
                    "gates": {
                        "triage": {
                            "open": True,
                            "asked": True,
                            "wait_sec": 3.0,
                            "until_epoch": time.time() + 3.0,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        gates = serve._gates_state(workdir)
        check("la vista vede il cancello aperto", len(gates) == 1, gates)
        check(
            "la vista dice quanto manca",
            0.0 < gates[0]["remaining_sec"] <= 3.0,
            gates[0] if gates else {},
        )
        before = auto.view_client_recent(workdir, ttl=5.0)
        serve._mark_view_client(workdir)
        after = auto.view_client_recent(workdir, ttl=5.0)
        check("prima del GET nessun client", before is False)
        check("dopo /api/state il timbro c'e' ed e' fresco", after is True)
        check(
            "il timbro e' il file promesso",
            (workdir / auto.VIEW_CLIENT_NAME).is_file(),
        )


def main() -> int:
    test_client_stamp()
    test_triage_without_view()
    test_triage_explicit_wait_wins()
    test_triage_with_client()
    test_answer_effects()
    test_font_gate()
    test_font_wait_flag_wins()
    test_key_gate()
    test_report()
    test_view_state_and_stamp()
    print(f"\n{PASS} PASS, {FAIL} FAIL, {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
