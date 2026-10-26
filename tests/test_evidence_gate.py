"""Evidence gate: no finding -> no treatment, enforced in code.

The law lives in ``contract.validate_evidence`` (report-aware) and
``contract.validate_evidence_shape`` (structure only).  This test pins the three
surfaces that must obey it:

* ``gates.pre_render`` fails closed when findings are missing and a treatment is
  enabled, and fails when the evidence does not match the report;
* ``render.render`` refuses a plan whose enabled treatments carry no evidence,
  before any ffmpeg process is spawned;
* ``cli.cmd_render`` resolves the report, verifies the source identity, and maps
  every rejection to the documented exit code.

python tests/test_evidence_gate.py
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import cli, contract, gates, render  # noqa: E402

PASS = 0
FAIL = 0

FINDING_ID = "audio.loudness.off"
FINDINGS = [{"id": FINDING_ID, "kind": FINDING_ID, "span": [0.0, 4.0]}]


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def _treatment(
    name: str = "loudnorm",
    *,
    enabled: bool = True,
    finding_id: str = FINDING_ID,
    evidence: object = "default",
) -> dict:
    item = {
        "name": name,
        "kind": "technical",
        "enabled": enabled,
        "params": {},
        "reason": {"finding_id": finding_id, "explain": "test"},
    }
    if evidence == "default":
        item["evidence"] = [{"finding_id": finding_id, "t0": 0.0, "t1": 4.0}]
    elif evidence is not None:
        item["evidence"] = evidence
    return item


def _plan(treatments: list[dict], cuts: list[dict] | None = None) -> contract.EditPlan:
    return contract.EditPlan(
        output={"aspect": "9:16"},
        cuts=cuts if cuts is not None else [{"in": 0.0, "out": 2.0}],
        treatments=treatments,
    )


def _media(duration: float = 4.0) -> dict:
    return {"fps": 25.0, "duration": duration, "has_audio": True}


def _expect_qc(label: str, fn, *, code: str | None = None) -> None:
    raised = False
    payload = None
    try:
        fn()
    except contract.QCFailure as exc:
        raised = True
        payload = exc.result
    check(label, raised, payload)
    if code is not None:
        failures = (payload or {}).get("failures") or []
        check(f"{label}: failure code {code}", code in failures, failures)


# --------------------------------------------------------------------------- #
# 1. gates.pre_render
# --------------------------------------------------------------------------- #


def run_pre_render() -> None:
    print("\n-- gates.pre_render --")

    _expect_qc(
        "enabled treatment + findings None -> QCFailure",
        lambda: gates.pre_render(_plan([_treatment()]), _media()),
        code="evidence_findings",
    )

    ok = True
    try:
        result = gates.pre_render(_plan([_treatment()]), _media(), findings=FINDINGS)
    except contract.QCFailure:
        ok = False
        result = None
    check("valid findings -> gate passes", ok and result is not None and result.ok)

    bad_evidence = _treatment(
        evidence=[{"finding_id": "audio.noise.high", "t0": 0.0, "t1": 4.0}]
    )
    _expect_qc(
        "evidence cites a finding absent from the report -> QCFailure",
        lambda: gates.pre_render(_plan([bad_evidence]), _media(), findings=FINDINGS),
    )

    outside = _treatment(evidence=[{"finding_id": FINDING_ID, "t0": 0.0, "t1": 99.0}])
    _expect_qc(
        "evidence outside the source duration -> QCFailure",
        lambda: gates.pre_render(
            _plan([outside]),
            _media(),
            findings=[{"id": FINDING_ID, "span": [0.0, 99.0]}],
        ),
    )

    ok = True
    try:
        result = gates.pre_render(_plan([]), _media(), findings=None)
    except contract.QCFailure:
        ok = False
        result = None
    check(
        "cuts-only plan skips the evidence gate",
        ok and result is not None and result.ok,
    )

    _expect_qc(
        "duration 0 raises instead of returning a result",
        lambda: gates.pre_render(_plan([_treatment()]), _media(0.0), findings=FINDINGS),
        code="source_duration",
    )


# --------------------------------------------------------------------------- #
# 2. render.render shape gate
# --------------------------------------------------------------------------- #


def run_render_shape() -> None:
    print("\n-- render.render shape gate --")

    calls: list = []
    original_run = render.ff.run_ff
    original_require = render.ff.require

    def fake_run(*args, **kwargs):
        calls.append(("run_ff", args))
        raise AssertionError("ffmpeg must not run for a plan without evidence")

    def fake_require(*args, **kwargs):
        calls.append(("require", args))
        raise AssertionError("ffmpeg must not be required for a plan without evidence")

    render.ff.run_ff = fake_run  # type: ignore[assignment]
    render.ff.require = fake_require  # type: ignore[assignment]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src.mp4"
            source.write_bytes(b"")
            media = {
                "path": str(source),
                "width": 640,
                "height": 360,
                "fps": 25.0,
                "has_audio": True,
                "duration": 4.0,
            }
            plan = _plan([_treatment(evidence=None)])
            raised = False
            message = ""
            try:
                render.render(plan, media, precision="proxy", workdir=root / "work")
            except contract.RenderFailure as exc:
                raised = True
                message = str(exc)
            check("no-evidence enabled treatment -> RenderFailure", raised, message)
            check(
                "message names the missing evidence",
                "plan without evidence" in message,
                message,
            )
            check("ffmpeg was never invoked", not calls, calls)
    finally:
        render.ff.run_ff = original_run  # type: ignore[assignment]
        render.ff.require = original_require  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# 3. cli.cmd_render matrix
# --------------------------------------------------------------------------- #


class _Probe:
    def __init__(self, media: dict) -> None:
        self._media = media

    def to_dict(self) -> dict:
        return dict(self._media)


def _write_plan(path: Path, treatments: list[dict]) -> None:
    plan = _plan(treatments)
    path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")


def _source_block(media: dict) -> dict:
    return {
        "path": media["path"],
        "size": media["size"],
        "mtime": media["mtime"],
        "duration": media["duration"],
        "width": media["width"],
        "height": media["height"],
    }


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def run_cmd_render() -> None:
    print("\n-- cli.cmd_render --")

    parser = cli.build_parser()
    parsed = parser.parse_args(["render", "--plan", "p.json", "--report", "r.json"])
    check("--report flag is parsed", parsed.report == "r.json", parsed.report)

    original_probe = cli.probe.probe if hasattr(cli, "probe") else None
    import vx.probe as probe_mod
    import vx.render as render_mod

    original_probe = probe_mod.probe
    original_render = render_mod.render

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "src.mp4"
        source.write_bytes(b"payload")
        stat = source.stat()
        media = {
            "path": str(source),
            "size": stat.st_size,
            "mtime": round(stat.st_mtime, 3),
            "duration": 4.0,
            "fps": 25.0,
            "width": 640,
            "height": 360,
            "has_audio": True,
        }
        probe_mod.probe = lambda path: _Probe(media)  # type: ignore[assignment]

        calls: list = []

        def fake_render(plan, media_dict, **kwargs):
            calls.append((plan, media_dict, kwargs))
            return {
                "path": str(root / "out.mp4"),
                "frames": 1,
                "expected_frames": 1,
                "loudness": {},
                "stages": [],
                "target": None,
            }

        render_mod.render = fake_render  # type: ignore[assignment]

        try:
            # (a) no report + enabled treatment -> exit 2, render not called
            case_a = root / "a"
            case_a.mkdir()
            plan_a = case_a / "plan.json"
            _write_plan(plan_a, [_treatment()])
            calls.clear()
            rc, _out, err = _run_cli(
                [
                    "render",
                    "--plan",
                    str(plan_a),
                    "--input",
                    str(source),
                    "--workdir",
                    str(case_a / "work"),
                ]
            )
            check("no report + enabled -> exit 2", rc == contract.EXIT_USAGE, rc)
            check("no report + enabled -> render not called", not calls, calls)
            check(
                "no report + enabled -> stderr explains", bool(err.strip()), err.strip()
            )

            # (b) explicit report with evidence mismatch -> exit 5
            case_b = root / "b"
            case_b.mkdir()
            plan_b = case_b / "plan.json"
            _write_plan(plan_b, [_treatment()])
            report_b = case_b / "explicit.json"
            report_b.write_text(
                json.dumps(
                    {
                        "source": _source_block(media),
                        "findings": [{"id": "audio.noise.high", "span": [0.0, 4.0]}],
                    }
                ),
                encoding="utf-8",
            )
            calls.clear()
            rc, _out, _err = _run_cli(
                [
                    "render",
                    "--plan",
                    str(plan_b),
                    "--input",
                    str(source),
                    "--workdir",
                    str(case_b / "work"),
                    "--report",
                    str(report_b),
                ]
            )
            check("evidence mismatch -> exit 5", rc == contract.EXIT_QC_FAIL, rc)
            check("evidence mismatch -> render not called", not calls, calls)

            # (c) sibling report.json with valid evidence -> render called
            case_c = root / "c"
            case_c.mkdir()
            plan_c = case_c / "plan.json"
            _write_plan(plan_c, [_treatment()])
            (case_c / "report.json").write_text(
                json.dumps({"source": _source_block(media), "findings": FINDINGS}),
                encoding="utf-8",
            )
            calls.clear()
            rc, _out, _err = _run_cli(
                [
                    "render",
                    "--plan",
                    str(plan_c),
                    "--input",
                    str(source),
                    "--workdir",
                    str(case_c / "work"),
                ]
            )
            check("valid sibling report -> exit 0", rc == contract.EXIT_OK, rc)
            check(
                "valid sibling report -> render called once",
                len(calls) == 1,
                len(calls),
            )

            # (d) source identity mismatch -> exit 2
            case_d = root / "d"
            case_d.mkdir()
            plan_d = case_d / "plan.json"
            _write_plan(plan_d, [_treatment()])
            other = dict(_source_block(media))
            other["path"] = str(root / "other.mp4")
            (case_d / "report.json").write_text(
                json.dumps({"source": other, "findings": FINDINGS}),
                encoding="utf-8",
            )
            calls.clear()
            rc, _out, _err = _run_cli(
                [
                    "render",
                    "--plan",
                    str(plan_d),
                    "--input",
                    str(source),
                    "--workdir",
                    str(case_d / "work"),
                ]
            )
            check("source mismatch -> exit 2", rc == contract.EXIT_USAGE, rc)
            check("source mismatch -> render not called", not calls, calls)

            # (e) unreadable / non-object report -> exit 2
            case_e = root / "e"
            case_e.mkdir()
            plan_e = case_e / "plan.json"
            _write_plan(plan_e, [_treatment()])
            (case_e / "report.json").write_text("[1, 2, 3]", encoding="utf-8")
            calls.clear()
            rc, _out, _err = _run_cli(
                [
                    "render",
                    "--plan",
                    str(plan_e),
                    "--input",
                    str(source),
                    "--workdir",
                    str(case_e / "work"),
                ]
            )
            check("non-object report -> exit 2", rc == contract.EXIT_USAGE, rc)
            check("non-object report -> render not called", not calls, calls)
        finally:
            probe_mod.probe = original_probe  # type: ignore[assignment]
            render_mod.render = original_render  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# entry
# --------------------------------------------------------------------------- #


def main() -> int:
    missing = [
        name
        for name in ("validate_evidence", "validate_evidence_shape")
        if not hasattr(contract, name)
    ]
    if missing:
        print(
            "[RED] contract is missing: "
            + ", ".join(missing)
            + " — waiting for law-core"
        )
        return 1

    run_pre_render()
    run_render_shape()
    run_cmd_render()

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
