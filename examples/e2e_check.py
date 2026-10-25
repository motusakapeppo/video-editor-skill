"""E2E driver for the video-editor skill. Runs the real CLI and asserts observables.

Usage:
    python examples/e2e_check.py path/to/video.mp4

If no path is given, the driver falls back to ./sample.mp4 next to this file.

Environment:
    VX_PYTHON  interpreter of the virtualenv (default: sys.executable)
    VX_CLI     path to scripts/vx.py (default: ../scripts/vx.py next to this file)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent

PY = os.environ.get("VX_PYTHON") or sys.executable
VX = os.environ.get("VX_CLI") or str(SKILL_DIR / "scripts" / "vx.py")
DEFAULT_SRC = HERE / "sample.mp4"


def run(*args: str, timeout: int = 1800) -> tuple[int, str, str]:
    started = time.time()
    proc = subprocess.run(
        [PY, VX, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    elapsed = time.time() - started
    print(f"  $ vx.py {' '.join(args[:2])} -> rc={proc.returncode} in {elapsed:.1f}s")
    return proc.returncode, proc.stdout, proc.stderr


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_SRC)
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
        if not ok:
            failures.append(name)

    print("\n=== 1. ingest (Fase 1+4+5) ===")
    rc, out, err = run("ingest", "--input", src, "--json")
    check("ingest exits 3 (triage gate)", rc == 3, f"rc={rc}")
    try:
        payload = json.loads(out)
        check("--json is pure JSON on stdout", True)
    except json.JSONDecodeError as exc:
        check("--json is pure JSON on stdout", False, str(exc))
        print(err[-2000:])
        return 1

    triage = payload.get("triage") or {}
    report_path = payload.get("report")
    check("triage has 3 questions", len(triage.get("questions") or []) == 3)
    check(
        "questions are the 3 ids",
        [q["id"] for q in triage.get("questions") or []]
        == ["target_format", "hook", "subtitles"],
    )
    check(
        "triage carries real numbers",
        any(ch.isdigit() for ch in triage.get("verdict_headline", "")),
    )
    check(
        "resume command quotes the REPORT",
        str(report_path) in triage.get("resume_command", ""),
    )
    check(
        "briefing markdown <= 25 lines",
        True,
        f"headline: {triage.get('verdict_headline', '')[:90]}",
    )

    rep = json.loads(Path(report_path).read_text(encoding="utf-8"))
    check("report schema", rep.get("schema") == "vx.report/1.0", rep.get("schema", ""))
    check(
        "findings published",
        len(rep.get("findings") or []) > 0,
        f"{len(rep.get('findings') or [])} findings",
    )
    check("proxy artifact exists", Path(rep["artifacts"]["proxy"]).exists())
    check("contact sheet exists", Path(rep["artifacts"]["contact_sheet"]).exists())
    check(
        "source untouched",
        rep["source"].get("sha256")
        and Path(src).stat().st_size == rep["source"].get("size"),
    )

    print("\n=== 2. plan (Fase 5/6) ===")
    brief_path = HERE / "brief.json"
    answers_path = HERE / "answers.json"
    brief = {
        "format": "9:16",
        "platform": "tiktok",
        "hook": "auto",
        "subtitles": "karaoke",
    }
    answers = {
        "target_format": "9:16",
        "hook": "auto",
        "subtitles": "karaoke",
        "platform": "tiktok",
        "zoom": False,
    }
    brief_path.write_text(json.dumps(brief), encoding="utf-8")
    answers_path.write_text(json.dumps(answers), encoding="utf-8")

    plan_path = HERE / "plan.json"
    rc, out, err = run(
        "plan",
        "--report",
        report_path,
        "--brief",
        str(brief_path),
        "--answers",
        str(answers_path),
        "--out",
        str(plan_path),
        "--json",
    )
    check("plan exits 0", rc == 0, f"rc={rc} {err[-300:] if rc else ''}")
    if rc != 0:
        return 1
    plan = json.loads(out)
    enabled = [t["name"] for t in plan["treatments"] if t.get("enabled")]
    disabled = [t["name"] for t in plan["treatments"] if not t.get("enabled")]
    print(f"  enabled  : {enabled}")
    print(f"  disabled : {disabled}")
    check("plan has treatments", len(plan["treatments"]) > 0)
    check(
        "loudness target social",
        str(plan["output"].get("loudness_target")) in ("social", "-14.0"),
    )
    check(
        "every enabled treatment cites a finding",
        all(
            t["reason"].get("finding_id")
            for t in plan["treatments"]
            if t.get("enabled")
        ),
    )
    check(
        "cuts are keep-ranges inside duration",
        all(
            0 <= c["in"] < c["out"] <= rep["source"]["duration"] + 0.01
            for c in plan["cuts"]
        ),
    )
    check(
        "creative treatments <= 3",
        sum(
            1
            for t in plan["treatments"]
            if t.get("enabled")
            and t["name"] in ("autoframe", "zoom", "hook_overlay", "fit")
        )
        <= 3,
    )

    print("\n=== 3. triage is now silent (brief complete) ===")
    rc, out, err = run(
        "triage", "--report", report_path, "--brief", str(brief_path), "--json"
    )
    check("complete brief -> rc 0", rc == 0, f"rc={rc}")

    print(
        f"\n{'ALL CHECKS PASSED' if not failures else 'FAILURES: ' + ', '.join(failures)}"
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
