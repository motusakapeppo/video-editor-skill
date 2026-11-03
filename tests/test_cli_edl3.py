"""The ``vx edl3`` contract: validate without rendering, one schema version.

Five properties, each a check that can fail:

a. ``EDL3_VERSION`` is the same string in the code, in the CLI help and in
   ``references/26-edl3-e-ponte.md``: three places, one spelling;
b. ``vx edl3 validate`` exits 0 on a valid document;
c. it exits 2 on an unknown field and names that field, because a typo dropped
   silently renders something the author did not write;
d. the unknown-transition error lists the valid kinds, so the fix is in the
   message;
e. ``vx ingest`` returns 0 on success (D18), and normal progress never touches
   stderr (where PowerShell turns it into a failed pipeline).

The shared-function rule is checked on the source: both ``vx edl3 validate`` and
the renderer call ``Timeline3.validate``; the command has no copy of it.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import cli, edl3, edl3_render  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def run(argv: list) -> tuple:
    """``cli.main`` with stdout captured; SystemExit becomes its code."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, out.getvalue()


def document(**overrides) -> dict:
    data = {
        "version": edl3.EDL3_VERSION,
        "fps": 30,
        "size": [320, 240],
        "clips": [
            {
                "id": "a",
                "source": "a.mp4",
                "src_in": 0.0,
                "src_out": 2.0,
                "layer": 0,
            }
        ],
    }
    data.update(overrides)
    return data


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="vx_edl3_cli_"))
    try:
        print("\n(1) one version, three places")
        help_code, help_text = run(["edl3", "--help"])
        check(
            "a. the CLI builds `edl3 --help`",
            help_code == 0 and "edl3" in help_text,
            help_code,
        )
        check(
            "a. the help carries EDL3_VERSION",
            edl3.EDL3_VERSION in help_text,
            edl3.EDL3_VERSION,
        )
        doc = (ROOT / "references" / "26-edl3-e-ponte.md").read_text(
            encoding="utf-8"
        )
        check(
            "a. the reference carries the same version",
            f'"version": "{edl3.EDL3_VERSION}"' in doc,
            edl3.EDL3_VERSION,
        )
        check(
            "a. PRESET_APPLIED_VERSION is EDL3_VERSION",
            edl3.PRESET_APPLIED_VERSION == edl3.EDL3_VERSION,
            edl3.PRESET_APPLIED_VERSION,
        )

        print("\n(2) validate: a valid document")
        good = work / "good.json"
        good.write_text(json.dumps(document()), encoding="utf-8")
        code, text = run(["edl3", "validate", str(good)])
        check("b. a valid document exits 0", code == 0, (code, text))
        check("b. the line says ok", "ok:" in text, text.strip())
        code, text = run(["edl3", "validate", str(good), "--json"])
        payload = json.loads(text)
        check(
            "b. --json is a clean document",
            code == 0 and payload["ok"] is True and payload["count"] == 0,
            payload,
        )

        print("\n(3) validate: an unknown field")
        bad = work / "bad.json"
        bad.write_text(json.dumps(document(surprise=1)), encoding="utf-8")
        code, text = run(["edl3", "validate", str(bad)])
        check("c. an unknown field exits 2", code == 2, (code, text))
        check("c. the field is named", "surprise" in text, text.strip())
        check(
            "c. the path is the field itself",
            "TIMELINE_SHAPE surprise:" in text,
            text.strip(),
        )

        print("\n(4) one validation function, not a copy")
        calls = []
        original = edl3.Timeline3.validate

        def spy(self, *args, **kwargs):
            calls.append(1)
            return original(self, *args, **kwargs)

        edl3.Timeline3.validate = spy
        try:
            run(["edl3", "validate", str(good)])
        finally:
            edl3.Timeline3.validate = original
        check("d. validate calls Timeline3.validate", len(calls) == 1, len(calls))
        source = inspect.getsource(edl3_render.compile_timeline)
        check(
            "d. the renderer calls the same Timeline3.validate",
            "timeline.validate(" in source,
        )

        print("\n(5) the transition error lists the valid kinds")
        stray = edl3.Timeline3.from_dict(
            document(
                clips=[
                    {
                        "id": "a",
                        "source": "a.mp4",
                        "src_in": 0.0,
                        "src_out": 2.0,
                        "layer": 0,
                        "transition_in": {"kind": "wipe", "duration": 0.3},
                    }
                ]
            )
        )
        problems = [p for p in stray.validate() if p["code"] == "TRANSITION_UNKNOWN"]
        check("d. an unknown kind is refused", len(problems) == 1, problems)
        detail = problems[0]["detail"] if problems else ""
        check(
            "d. every valid kind is in the message",
            all(kind in detail for kind in edl3.TRANSITION_KINDS),
            detail,
        )

        print("\n(6) ingest exits 0 and keeps stderr for errors only")
        saved_ingest = cli._ingest
        saved_mode = cli._JSON_MODE

        class Args:
            input = "clip.mp4"
            json = False
            autonomous = True
            brief = None

        state = {
            "workdir": "w",
            "report": {},
            "report_path": "r",
            "proxy": None,
            "triage": None,
            "contact_sheet": None,
        }
        cli._ingest = lambda *args, **kwargs: state
        cli._JSON_MODE = False
        try:
            code = cli.cmd_ingest.__wrapped__(Args())
        finally:
            cli._ingest = saved_ingest
        check("e. a successful ingest returns EXIT_OK", code == cli.EXIT_OK, code)

        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cli._JSON_MODE = False
            cli._say("[1/6] proxy")
            cli._JSON_MODE = True
            cli._say("[2/7] parlato")
        cli._JSON_MODE = saved_mode
        check("e. progress goes to stdout", "[1/6] proxy" in out.getvalue())
        check("e. stderr stays empty on success", err.getvalue() == "", err.getvalue())
        check("e. --json suppresses progress", out.getvalue().count("[") == 1)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
