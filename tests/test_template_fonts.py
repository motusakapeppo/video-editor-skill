"""F28-T01 gate -- every Motion Studio template declares its font and draws with a bundled one.

    python tests/test_template_fonts.py

Script-style: every check prints PASS/FAIL and the process exits 0 only when all
checks pass; the whole file reports SKIP only when no system browser exists,
which is the same optional-dependency rule the other Motion suites use.

What it proves:

a. no template paints with a Windows system stack: zero ``Segoe UI`` matches in
   the 54 ``index.html`` files, and no ``system-ui`` fallback either;
b. every page declares the same three ``@font-face`` blocks, each naming a
   bundled file through the library's relative convention
   (``../../fonts/...``), and drives the family from ``--vx-font-family``;
c. every ``template.json`` declares ``font`` as one of ``antonio`` | ``inter``,
   the manifest default is the documented one for that family, and the page's
   own ``DEFAULTS`` agrees with the manifest -- a declaration that the picture
   does not read would be a lie;
d. the static linter admits all 54 templates and counts the three font files as
   dependencies (``deps == 3``), never as ``DEP_ABSOLUTE``;
e. the bundled bytes are the ones the licence matrix pins: the recorded sha256
   prefixes match ``assets/fonts/Antonio-Regular.ttf`` and
   ``assets/fonts/Inter-Variable.ttf``;
f. the font reaches the frame: the same template rendered twice with
   ``antonio`` is byte-identical, and rendered once with ``inter`` is a
   different sha256;
g. inside the real browser both bundled families -- and the Antonio bold face --
   report as loaded, so a missing file cannot hide behind a silent fallback.

`transition` and `background` are in neither group the task names; both declare
`inter`, the neutral body face, because neither carries display typography.

stdlib plus the repo's own ``vx`` modules; the browser is the system Chromium
channel, never a download.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# allow: SIZE_OK - one standalone gate: the static audit of 54 pages, the
# manifest/default rule, the licence-matrix pin and the live render proof are one
# contract; splitting it would break the repo's one-file-per-test convention.
from vx import motion, motion_lint  # noqa: E402

TEMPLATES = ROOT / "assets" / "templates"
FONTS = ROOT / "assets" / "fonts"
MATRIX = ROOT / "LICENSES_MATRIX.md"
EXPECTED_TEMPLATES = 54

DISPLAY_FAMILIES = ("title", "intro", "kinetic", "sticker", "counter")
TEXT_FAMILIES = ("subtitle", "card", "callout", "chart", "progress")
NEUTRAL_FAMILIES = ("transition", "background")

BUNDLED = (
    "Antonio-Regular.ttf",
    "Antonio-Bold.ttf",
    "Inter-Variable.ttf",
)
PINNED = {
    "Antonio-Regular.ttf": ("Antonio", "27f419ff2c909c2f"),
    "Inter-Variable.ttf": ("Inter", "29160a80ff49ddca"),
}

SAMPLE = "vx-title-kicker"
FPS = 2.0
SIZE = (320, 568)

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<62} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [{'SKIP'}] {label:<62} {reason}")


def template_dirs() -> List[Path]:
    return sorted(path for path in TEMPLATES.iterdir() if path.is_dir())


def expected_default(name: str) -> str:
    family = name.split("-")[1]
    return "antonio" if family in DISPLAY_FAMILIES else "inter"


def manifest(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frame_sha(template: Path, params: Dict[str, Any], out: Path) -> Tuple[str, Dict[str, Any]]:
    result = motion.capture(
        template, params, out, fps=FPS, size=SIZE, workdir=None
    )
    frames = [Path(item) for item in result["frames"]]
    middle = frames[len(frames) // 2]
    return sha256(middle), result


def loaded_faces(template: Path) -> Optional[Dict[str, bool]]:
    """Ask the browser which bundled faces it actually resolved for this page."""
    probe = motion.available()
    if not probe["available"]:
        return None
    pw = browser = context = page = None
    try:
        pw, browser, _engine = motion._launch(probe["candidates"])
        context = browser.new_context(
            viewport={"width": SIZE[0], "height": SIZE[1]}, device_scale_factor=1
        )
        page = context.new_page()
        page.goto(
            (template / "index.html").as_uri(), wait_until="load", timeout=10000
        )
        return page.evaluate(
            """async () => {
                 await window.__vxSeek(0.2);
                 return {
                   antonio: document.fonts.check('400 40px "VX Antonio"'),
                   antonioBold: document.fonts.check('700 40px "VX Antonio"'),
                   inter: document.fonts.check('400 40px "VX Inter"')
                 };
               }"""
        )
    finally:
        motion._close_quietly(page, context, browser, pw)


def run_static() -> Dict[str, Path]:
    directories = template_dirs()
    check(
        "the library is the promised 54 templates",
        len(directories) == EXPECTED_TEMPLATES,
        f"{len(directories)} directories",
    )

    pages: Dict[str, str] = {}
    missing = [
        directory.name
        for directory in directories
        if not (directory / "index.html").is_file()
        or not (directory / "template.json").is_file()
    ]
    check("every directory has index.html and template.json", not missing, str(missing[:4]))
    for directory in directories:
        pages[directory.name] = (directory / "index.html").read_text(encoding="utf-8")

    segoue = [name for name, text in pages.items() if "Segoe UI" in text]
    check("zero 'Segoe UI' matches in the 54 pages", not segoue, str(segoue[:4]))
    system_ui = [name for name, text in pages.items() if "system-ui" in text]
    check("zero 'system-ui' fallbacks left behind", not system_ui, str(system_ui[:4]))

    no_faces = [
        name for name, text in pages.items() if text.count("@font-face") != len(BUNDLED)
    ]
    check(
        "every page declares one @font-face per bundled file",
        not no_faces,
        str(no_faces[:4]),
    )
    unreferenced = [
        name
        for name, text in pages.items()
        if any(f"../../fonts/{face}" not in text for face in BUNDLED)
    ]
    check(
        "every page names the three bundled files as ../../fonts/*.ttf",
        not unreferenced,
        str(unreferenced[:4]),
    )
    no_variable = [
        name
        for name, text in pages.items()
        if "font-family: var(--vx-font-family);" not in text
    ]
    check(
        "every page takes its family from --vx-font-family",
        not no_variable,
        str(no_variable[:4]),
    )
    no_mapping = [
        name
        for name, text in pages.items()
        if 'setProperty("--vx-font-family"' not in text or '.font === "inter"' not in text
    ]
    check(
        "every page maps the font param onto that property",
        not no_mapping,
        str(no_mapping[:4]),
    )

    bad_declarations: List[str] = []
    bad_defaults: List[str] = []
    defaults_disagree: List[str] = []
    for directory in directories:
        name = directory.name
        declared = manifest(directory / "template.json").get("params", {}).get("font")
        if (
            not isinstance(declared, dict)
            or declared.get("type") != "string"
            or list(declared.get("values") or []) != ["antonio", "inter"]
        ):
            bad_declarations.append(name)
            continue
        if declared.get("default") != expected_default(name):
            bad_defaults.append(name)
        page_default = re.search(r'font: "([a-z]+)",', pages[name])
        if page_default is None or page_default.group(1) != declared.get("default"):
            defaults_disagree.append(name)
    check(
        "every template.json declares font as antonio|inter",
        not bad_declarations,
        str(bad_declarations[:4]),
    )
    check(
        "the default follows the family rule (display antonio, text inter)",
        not bad_defaults,
        str(bad_defaults[:4]),
    )
    check(
        "the page DEFAULTS repeat the manifest default",
        not defaults_disagree,
        str(defaults_disagree[:4]),
    )

    families = {name.split("-")[1] for name in pages}
    unknown = families - set(DISPLAY_FAMILIES) - set(TEXT_FAMILIES) - set(NEUTRAL_FAMILIES)
    check("every family is covered by the default rule", not unknown, str(sorted(unknown)))
    display = [name for name in pages if expected_default(name) == "antonio"]
    text = [name for name in pages if expected_default(name) == "inter"]
    check(
        "the rule splits the library into display and text defaults",
        len(display) + len(text) == len(pages) and bool(display) and bool(text),
        f"antonio={len(display)} inter={len(text)}",
    )

    findings = {
        directory.name: motion_lint.lint(directory, runtime_check=False)
        for directory in directories
    }
    blocked = {name: item["findings"] for name, item in findings.items() if not item["ok"]}
    check("the static linter admits all 54 templates", not blocked, str(blocked)[:300])
    deps = {item["stats"]["deps"] for item in findings.values()}
    check("the three font files are counted as dependencies", deps == {3}, str(deps))

    absent = [face for face in BUNDLED if not (FONTS / face).is_file()]
    check("the three bundled faces exist on disk", not absent, str(absent))
    matrix_text = MATRIX.read_text(encoding="utf-8")
    for face, (family, prefix) in PINNED.items():
        digest = sha256(FONTS / face) if (FONTS / face).is_file() else ""
        check(
            f"{face}: bytes match the {family} row in LICENSES_MATRIX.md",
            digest.startswith(prefix) and prefix in matrix_text,
            f"{digest[:16]}... vs matrix {prefix}...",
        )

    return {directory.name: directory for directory in directories}


def run_dynamic(directory: Path) -> None:
    try:
        motion.validate_params(directory, {"font": "inter"})
        motion.validate_params(directory, {"font": "antonio"})
        accepted = True
    except motion.MotionError as exc:
        accepted = False
        print(f"    validate_params: {exc.code}: {exc}")
    check("both font values validate against the declaration", accepted)

    refused = ""
    try:
        motion.validate_params(directory, {"font": "comic"})
    except motion.MotionError as exc:
        refused = exc.code
    check("a third family is refused, never silently applied", refused == "PARAM_RANGE", refused)

    probe = motion.available()
    if not probe["available"]:
        skip("font reaches the frame", str(probe.get("reason") or "no system browser"))
        return

    with tempfile.TemporaryDirectory(
        prefix="vx_template_fonts_", ignore_cleanup_errors=True
    ) as tmp:
        work = Path(tmp)
        antonio_a, first = frame_sha(directory, {"font": "antonio"}, work / "a1")
        antonio_b, _ = frame_sha(directory, {"font": "antonio"}, work / "a2")
        inter, second = frame_sha(directory, {"font": "inter"}, work / "i1")

    check(
        "the antonio render runs on a real Chromium frame",
        first.get("engine") in ("chrome", "msedge"),
        f"engine={first.get('engine')!r}",
    )
    check(
        "the font param reaches the captured parameters",
        first.get("params", {}).get("font") == "antonio"
        and second.get("params", {}).get("font") == "inter",
        f"{first.get('params', {}).get('font')} / {second.get('params', {}).get('font')}",
    )
    check("antonio rendered twice is byte-identical", antonio_a == antonio_b, antonio_a)
    check("antonio and inter produce different frames", antonio_a != inter, f"{antonio_a} / {inter}")

    faces = loaded_faces(directory)
    if faces is None:
        skip("bundled faces load in the browser", "no system browser")
        return
    check("VX Antonio resolves to the bundled regular", bool(faces.get("antonio")), faces)
    check("VX Antonio resolves to the bundled bold", bool(faces.get("antonioBold")), faces)
    check("VX Inter resolves to the bundled variable face", bool(faces.get("inter")), faces)


def main() -> int:
    global FAIL
    try:
        directories = run_static()
        if SAMPLE in directories:
            run_dynamic(directories[SAMPLE])
        else:
            check(f"{SAMPLE} exists", False, "the sample template vanished")
    except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
        print(f"\nFAIL: {type(exc).__name__}: {exc}")
        FAIL += 1
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
