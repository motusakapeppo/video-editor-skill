"""La guardia di consegna: un master che esiste non e' un lavoro consegnato.

python tests/test_guard_delivery.py

Il difetto che questo test sorveglia: ``vx guard`` sapeva dire "non conforme",
ma nessuno lo eseguiva alla fine di ``auto``. Un video prodotto fuori dalla
pipeline poteva dirsi consegnato. Qui si prova, con cartelle temporanee e senza
render, che:

* un lavoro ``auto`` sano passa (``guard.ok`` vero, ``outcome`` done);
* il report e le cartelle interne (``work/``, ``render/``, ``live/``,
  ``stage_*``, ``keyframes/``, ``progress.html``, ``font_proposal.json``) non
  sono falsi positivi;
* un ``out/master.mp4`` senza report non passa, e ``serve`` non lo dice ``done``;
* ``--no-guard`` salta l'audit e lo dichiara nel report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from vx import auto, cli, guard, serve  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def _write(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _healthy_job(root: Path, *, panel: bool = True) -> Path:
    """Un lavoro ``auto`` normale: piano, progresso, master, consegne, report.

    Dentro ci sono anche i file che la pipeline scrive per se stessa, compreso
    uno script che chiama ffmpeg in ``render/``: non e' codice dell'utente e non
    deve diventare una violazione.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "out").mkdir(exist_ok=True)
    (root / "out" / "master.mp4").write_bytes(b"vx master placeholder\n")
    (root / "out" / "thumbnail.jpg").write_bytes(b"\xff\xd8\xff\xe0thumb")
    (root / "out" / "contact_sheet.jpg").write_bytes(b"\xff\xd8\xff\xe0sheet")
    _write(root / "plan.json", {"timeline": {"duration_sec": 2.0}, "cuts": []})
    _write(
        root / "source.json",
        {"path": str(root / "source.mp4"), "sha256": ""},
    )
    (root / "source.mp4").write_bytes(b"vx source placeholder\n")
    _write(
        root / "source.json",
        {
            "path": str(root / "source.mp4"),
            "sha256": hashlib.sha256((root / "source.mp4").read_bytes()).hexdigest(),
        },
    )
    _write(
        root / "auto_report.json",
        {
            "ok": True,
            "master": str(root / "out" / "master.mp4"),
            "deliverables": [],
            "guard": {"ok": True, "skipped": False, "violations": [], "warnings": []},
        },
    )
    if panel:
        _write(
            root / "progress.json",
            {
                "prompt": "un lavoro sano",
                "percent": 100.0,
                "current": None,
                "note": "",
                "master": str(root / "out" / "master.mp4"),
                "phases": [],
                "journal": [],
            },
        )
        (root / "progress.html").write_text("<html><body>panel</body></html>", encoding="utf-8")
    # File interni: non sono prove contro il lavoro.
    for folder in ("work", "render", "live", "stage_00", "stage_01", "keyframes"):
        (root / folder).mkdir(exist_ok=True)
    (root / "render" / "tool.py").write_text(
        "import subprocess\nsubprocess.run(['ffmpeg', '-version'])\n", encoding="utf-8"
    )
    (root / "live" / "publish.py").write_text(
        "import os\nos.system('ffmpeg -version')\n", encoding="utf-8"
    )
    (root / "stage_00" / "helper.py").write_text(
        "import subprocess\nsubprocess.check_call(['ffmpeg'])\n", encoding="utf-8"
    )
    (root / "work" / "helper.py").write_text(
        "import subprocess\nsubprocess.check_call(['ffmpeg'])\n", encoding="utf-8"
    )
    (root / "keyframes" / "kf0.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    _write(root / "font_proposal.json", {"chosen": "Inter", "candidates": []})
    return root


def _bare_master(root: Path) -> Path:
    """Solo un master e uno script ffmpeg: nessun report, nessuna firma vx."""
    (root / "out").mkdir(parents=True, exist_ok=True)
    (root / "out" / "master.mp4").write_bytes(b"hand made master\n")
    (root / "pipeline.py").write_text(
        "import subprocess\nsubprocess.run(['ffmpeg', '-i', 'in.mp4', 'out/master.mp4'])\n",
        encoding="utf-8",
    )
    return root


def test_healthy_job_passes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _healthy_job(Path(tmp) / "job")
        audit = guard.audit(root)
        codes = {item["code"]: item["status"] for item in audit["checks"]}
        check("lavoro sano: guard.ok vero", audit["ok"] is True, str(audit["exit_code"]))
        check("lavoro sano: nessuna violazione", not audit["violations"], str(audit["violations"]))
        check("lavoro sano: nessun avviso", not audit["warnings"], str(audit["warnings"]))
        check("lavoro sano: e' un lavoro vx", audit["job"] is True, str(audit["signature"]))
        check("lavoro sano: auto_report.json e' una firma", "auto_report.json" in audit["signature"], "")
        check("lavoro sano: G2 master tracciato", codes.get("G2") == "ok", str(codes.get("G2")))
        check("lavoro sano: G4 non accusa i file interni", codes.get("G4") == "ok", str(codes.get("G4")))
        check("lavoro sano: G5 progress.json pubblicato", codes.get("G5") == "ok", str(codes.get("G5")))
        check("lavoro sano: G6 sorgente intatto", codes.get("G6") == "ok", str(codes.get("G6")))


def test_healthy_job_without_panel_is_warning_not_violation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _healthy_job(Path(tmp) / "job", panel=False)
        audit = guard.audit(root)
        warnings = {item["code"] for item in audit["warnings"]}
        check("senza panel: guard.ok vero", audit["ok"] is True, str(audit["exit_code"]))
        check("senza panel: G5 e' un avviso", warnings == {"G5"}, str(warnings))
        check("senza panel: exit 0", audit["exit_code"] == 0, str(audit["exit_code"]))


def test_master_without_report_never_done() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _bare_master(Path(tmp) / "handmade")
        audit = guard.audit(root)
        codes = {item["code"] for item in audit["violations"]}
        check("master sciolto: guard.ok falso", audit["ok"] is False, str(audit["exit_code"]))
        check("master sciolto: violazioni G2/G4", {"G2", "G4"} <= codes, str(codes))
        check("master sciolto: exit 2", audit["exit_code"] == 2, str(audit["exit_code"]))

        state = serve.state(Path(tmp))
        check("master sciolto: la UI espone guard", isinstance(state.get("guard"), dict), str(state.get("guard")))
        check("master sciolto: guard.ok falso nella UI", state["guard"]["ok"] is False, str(state["guard"]))
        check("master sciolto: outcome mai done", state["outcome"] != "done", str(state["outcome"]))
        check("master sciolto: outcome failed", state["outcome"] == "failed", str(state["outcome"]))
        check("master sciolto: master_ready vero", state["master_ready"] is True, str(state["master_ready"]))


def test_auto_report_decides_delivery() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _healthy_job(Path(tmp) / "job")
        args = argparse.Namespace(no_guard=False)
        section = auto.run_guard(args, root)
        report = {"master": "x", "ok": True}
        auto.apply_guard(report, section)
        check("auto: guardia eseguita", section["skipped"] is False, str(section.get("why")))
        check("auto: guardia verde", report["guard"]["ok"] is True, str(report["guard"]))
        check("auto: outcome_suggested done", report["outcome_suggested"] == "done", str(report))

        broken = _bare_master(Path(tmp) / "broken")
        section = auto.run_guard(args, broken)
        report = {"master": "x", "ok": True}
        auto.apply_guard(report, section)
        check("auto: guardia rossa", section["ok"] is False, str(section.get("why")))
        check("auto: delivered falso", report["delivered"] is False, str(report.get("delivered")))
        check(
            "auto: outcome_suggested failed",
            report["outcome_suggested"] == "failed",
            str(report.get("outcome_suggested")),
        )
        check(
            "auto: il motivo e' dichiarato",
            any("non e' consegnabile" in str(item) for item in report.get("failures") or []),
            str(report.get("failures")),
        )
        check(
            "auto: la narrazione dice le violazioni",
            auto.guard_line(section) == "Guardia: 2 violazioni, il lavoro non e' consegnabile",
            auto.guard_line(section),
        )


def test_no_guard_is_skipped_and_declared() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _bare_master(Path(tmp) / "handmade")
        args = argparse.Namespace(no_guard=True)
        section = auto.run_guard(args, root)
        report = {"master": "x", "ok": True}
        auto.apply_guard(report, section)
        check("--no-guard: saltata", section["skipped"] is True, str(section))
        check("--no-guard: motivo dichiarato", "--no-guard" in str(section.get("why")), str(section.get("why")))
        check("--no-guard: il report lo dice", report["guard"]["skipped"] is True, str(report["guard"]))
        check("--no-guard: nessuna violazione inventata", section["violations"] == [], str(section["violations"]))
        check("--no-guard: la narrazione lo dichiara", auto.guard_line(section).startswith("Guardia: saltata"), auto.guard_line(section))
        check("--no-guard: outcome done", report["outcome_suggested"] == "done", str(report))


def test_serve_reads_declared_guard() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _healthy_job(Path(tmp) / "job")
        report = json.loads((root / "auto_report.json").read_text(encoding="utf-8"))
        report["guard"] = {"ok": False, "violations": [{"code": "G4"}], "warnings": []}
        _write(root / "auto_report.json", report)
        state = serve.state(Path(tmp))
        check("serve: guard dichiarata letta", state["guard"]["ok"] is False, str(state["guard"]))
        check("serve: una violazione riportata", len(state["guard"]["violations"]) == 1, str(state["guard"]["violations"]))
        check("serve: outcome non done", state["outcome"] != "done", str(state["outcome"]))

    with tempfile.TemporaryDirectory() as tmp:
        root = _healthy_job(Path(tmp) / "job")
        state = serve.state(Path(tmp))
        check("serve: lavoro sano done", state["outcome"] == "done", str(state["outcome"]))
        check("serve: lavoro sano guard ok", state["guard"]["ok"] is True, str(state["guard"]))


def test_cli_flag_exists() -> None:
    parser = cli.build_parser()
    base = ["auto", "--prompt", "x", "--assets", ".", "--out", "out.mp4"]
    with_flag = parser.parse_args(base + ["--no-guard"])
    without = parser.parse_args(base)
    check("cli: --no-guard vale True", with_flag.no_guard is True, str(with_flag.no_guard))
    check("cli: senza flag vale False", without.no_guard is False, str(without.no_guard))


def main() -> int:
    print("lavoro sano:")
    test_healthy_job_passes()
    print("lavoro sano senza panel:")
    test_healthy_job_without_panel_is_warning_not_violation()
    print("master sciolto:")
    test_master_without_report_never_done()
    print("report di auto:")
    test_auto_report_decides_delivery()
    print("--no-guard:")
    test_no_guard_is_skipped_and_declared()
    print("serve:")
    test_serve_reads_declared_guard()
    print("cli:")
    test_cli_flag_exists()
    print(f"{'OK' if FAIL == 0 else 'FALLITO'} — {PASS} pass, {FAIL} fail")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
