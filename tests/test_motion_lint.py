"""F3-T04 gate -- the static linter for Motion Studio templates.

    python tests/test_motion_lint.py

Script-style: every check prints PASS/FAIL and the process exits 0 only when all
checks pass. No browser is launched and no network is touched: every case is
evaluated with `runtime_check=False`, which is the same mode `motion.capture`
uses before it decides to start an engine.

What it proves:

a. the shipped `vx-title-lower-third` template passes with no findings;
b. ten hostile templates, each with exactly one violation, each reported with
   the expected code;
c. three violations in one template are all reported in a deterministic order,
   and two runs produce byte-identical JSON;
d. `capture` refuses a hostile template through the declared error and never
   reaches the launch function;
e. the scanner does not cry wolf over a CSS selector such as
   `a[href^="http"]` or the word `fetch` inside a comment or a string.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import motion, motion_lint  # noqa: E402

TEMPLATE = ROOT / "assets" / "templates" / "vx-title-lower-third"

SEEK = "window.__vxSeek = function () { return Promise.resolve(); };"

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:<58} {detail}")


def note(label: str, detail: str) -> None:
    print(f"    [INFO] {label:<58} {detail}")


def manifest_text(**overrides: Any) -> str:
    data: Dict[str, Any] = {
        "name": "probe",
        "fps": 30,
        "duration": 1.0,
        "size": [320, 320],
        "params": {},
    }
    data.update(overrides)
    return json.dumps(data)


def write_template(
    root: Path,
    html: str,
    manifest: Optional[str] = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(html, encoding="utf-8")
    (root / "template.json").write_text(
        manifest if manifest is not None else manifest_text(), encoding="utf-8"
    )
    return root


def page(head: str = "", body: str = "", script: str = SEEK) -> str:
    return (
        "<!doctype html>\n"
        f"<html><head>{head}</head>\n"
        "<body>\n"
        f"{body}\n"
        f"<script>{script}</script>\n"
        "</body></html>\n"
    )


def codes_of(result: Dict[str, Any]) -> List[str]:
    return [item["code"] for item in result["findings"]]


def expect_one(label: str, root: Path, expected: str) -> Dict[str, Any]:
    result = motion_lint.lint(root, runtime_check=False)
    codes = codes_of(result)
    check(
        f"b. {label} -> {expected}",
        result["ok"] is False and codes == [expected],
        f"codes={codes}",
    )
    return result


def run(work: Path) -> None:
    good = motion_lint.lint(TEMPLATE, runtime_check=False)
    check("a. shipped template passes", good["ok"] is True, f"ok={good['ok']}")
    check(
        "a. shipped template has zero findings",
        good["findings"] == [],
        f"findings={good['findings']}",
    )
    check(
        "a. stats name files, bytes and deps",
        good["stats"]["files"] >= 2
        and good["stats"]["bytes"] > 0
        and good["stats"]["deps"] == 0,
        str(good["stats"]),
    )
    note("runtime", "static-only here; runtime_probe() is opt-in")

    remote_script = expect_one(
        "remote <script src>",
        write_template(
            work / "remote-script",
            page(head='<script src="https://cdn.example.com/x.js"></script>'),
        ),
        "NETWORK_TAG",
    )
    check(
        "b. text findings carry file:line",
        bool(remote_script["findings"])
        and remote_script["findings"][0]["where"].startswith("index.html:"),
        remote_script["findings"][0]["where"] if remote_script["findings"] else "none",
    )

    expect_one(
        "remote <link href>",
        write_template(
            work / "remote-link",
            page(head='<link rel="stylesheet" href="https://fonts.example.com/x.css">'),
        ),
        "NETWORK_TAG",
    )
    expect_one(
        "remote url() in CSS",
        write_template(
            work / "css-url",
            page(
                head="<style>body { background: url(https://cdn.example.com/bg.png); }</style>"
            ),
        ),
        "NETWORK_CSS",
    )
    expect_one(
        "fetch(",
        write_template(
            work / "fetch",
            page(script=f'{SEEK} fetch("local-api");'),
        ),
        "NETWORK_JS",
    )
    expect_one(
        "eval(",
        write_template(
            work / "eval",
            page(script=f'{SEEK} eval("1+1");'),
        ),
        "DANGEROUS_JS",
    )
    expect_one(
        "missing __vxSeek",
        write_template(work / "no-clock", page(script="")),
        "CLOCK_MISSING",
    )
    expect_one(
        "missing dependency",
        write_template(
            work / "missing-dep",
            page(body='<img src="missing.png" alt="">'),
        ),
        "DEP_MISSING",
    )
    expect_one(
        "absolute dependency",
        write_template(
            work / "absolute-dep",
            page(body='<img src="/missing.png" alt="">'),
        ),
        "DEP_ABSOLUTE",
    )
    expect_one(
        "escaped .. dependency",
        write_template(
            work / "escaped-dep",
            page(body='<img src="../outside.png" alt="">'),
        ),
        "DEP_ABSOLUTE",
    )
    expect_one(
        "malformed template.json",
        write_template(
            work / "bad-manifest",
            page(),
            manifest="{ this is not json",
        ),
        "MANIFEST_INVALID",
    )

    triple_html = (
        "<!doctype html>\n"
        "<html><head>\n"
        '<script src="https://cdn.example.com/x.js"></script>\n'
        "</head><body>\n"
        '<img src="missing.png" alt="">\n'
        f'<script>{SEEK} fetch("/api");</script>\n'
        "</body></html>\n"
    )
    triple = write_template(work / "triple", triple_html)
    first = motion_lint.lint(triple, runtime_check=False)
    second = motion_lint.lint(triple, runtime_check=False)
    reported = codes_of(first)
    check(
        "c. three violations are all reported",
        reported == ["NETWORK_TAG", "DEP_MISSING", "NETWORK_JS"],
        f"codes={reported}",
    )
    check(
        "c. findings are ordered by file, line, code",
        [item["where"] for item in first["findings"]]
        == ["index.html:3", "index.html:5", "index.html:6"],
        str([item["where"] for item in first["findings"]]),
    )
    dumped_first = json.dumps(first, sort_keys=True, ensure_ascii=False)
    dumped_second = json.dumps(second, sort_keys=True, ensure_ascii=False)
    check(
        "c. two runs produce byte-identical JSON",
        dumped_first == dumped_second,
        f"{len(dumped_first)} bytes",
    )

    hostile = write_template(
        work / "capture-hostile",
        page(head='<script src="https://cdn.example.com/x.js"></script>'),
    )
    calls = {"launches": 0}
    original_launch = motion._launch

    def forbidden_launch(*args: Any, **kwargs: Any) -> Any:
        calls["launches"] += 1
        raise AssertionError(
            "capture launched a browser for a template that failed lint"
        )

    caught: Optional[motion_lint.MotionLintError] = None
    motion._launch = forbidden_launch
    try:
        motion.capture(hostile, {}, work / "capture-out")
    except motion_lint.MotionLintError as exc:
        caught = exc
    finally:
        motion._launch = original_launch
    check(
        "d. capture raises the declared MotionLintError",
        isinstance(caught, motion_lint.MotionLintError),
        str(caught) if caught else "no error",
    )
    check("d. the engine is never started", calls["launches"] == 0, str(calls))
    check(
        "d. the error carries every finding",
        bool(caught) and [item["code"] for item in caught.findings] == ["NETWORK_TAG"],
        str(caught.payload.get("findings") if caught else "none"),
    )

    guard_html = (
        "<!doctype html>\n"
        "<html><head><style>\n"
        'a[href^="http"] { color: red; }\n'
        "/* body { background: url(https://cdn.example.com/commented.png); } */\n"
        "</style></head><body>\n"
        "<script>\n"
        '// fetch("https://cdn.example.com/never") is forbidden here\n'
        "window.__vxSeek = function () { return Promise.resolve(); };\n"
        "var selector = 'a[href^=\"http\"]';\n"
        'var note = "fetch is a word, not a call";\n'
        "</script>\n"
        "</body></html>\n"
    )
    guarded = motion_lint.lint(
        write_template(work / "false-positive", guard_html), runtime_check=False
    )
    check(
        "e. selector and commented/string fetch are not flagged",
        guarded["ok"] is True and guarded["findings"] == [],
        f"codes={codes_of(guarded)}",
    )


def main() -> int:
    global PASS, FAIL
    try:
        with tempfile.TemporaryDirectory(
            prefix="vx_motion_lint_", ignore_cleanup_errors=True
        ) as tmp:
            run(Path(tmp))
    except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
        print(f"\nFAIL: {type(exc).__name__}: {exc}")
        FAIL += 1
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
