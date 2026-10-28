"""F28-T05 gate -- every Motion Studio template exposes all six bundled font families.

    python tests/test_template_fonts.py

Script-style: every check prints PASS/FAIL and the process exits 0 only when all
checks pass; the whole file reports SKIP only when no system browser exists,
which is the same optional-dependency rule the other Motion suites use.

What it proves:

a. no template paints with a Windows system stack: zero ``Segoe UI`` matches in
   the 54 ``index.html`` files, and no ``system-ui`` fallback either;
b. every page declares one ``@font-face`` per bundled file (seven blocks for the
   six families: Antonio ships regular and bold), each naming a bundled file
   through the library's relative convention (``../../fonts/...``);
c. every page declares a ``--vx-font-<id>`` stack for each of the six ids and
   drives the painted family from ``--vx-font-family``, chosen from
   ``VX_FONT_IDS`` by the ``font`` param; an id outside the six falls back to
   the manifest default instead of painting with an unknown family;
d. every ``template.json`` declares ``font`` with exactly the six bundled ids,
   a default inside them, the documented per-family default rule and a ``note``
   naming the six bundled OFL-1.1 faces; the page's own ``DEFAULTS`` agrees with
   the manifest -- a declaration that the picture does not read would be a lie;
e. the static linter admits all 54 templates and counts the seven font files as
   dependencies (``deps == 7``), never as ``DEP_ABSOLUTE``;
f. the bundled bytes are the ones the licence matrix pins: the recorded sha256
   prefixes match ``assets/fonts/Antonio-Regular.ttf`` and
   ``assets/fonts/Inter-Variable.ttf``;
g. the font reaches the frame: for one display and one text template, each of
   the six ids produces a distinct sha256, the same id rendered twice is
   byte-identical, and the capture reports the id it was given -- a family that
   renders identically to another is a defect, not a duplicate;
h. inside the real browser all six bundled families -- and the Antonio bold
   face -- report as loaded, so a missing file cannot hide behind a silent
   fallback.

The six ids and their roles: ``antonio`` condensed display, ``inter`` neutral
text, ``bebasneue`` poster, ``montserrat`` geometric, ``playfairdisplay``
editorial serif, ``jetbrainsmono`` mono/tech. `transition` and `background` are
in neither default group; both declare `inter`, the neutral body face, because
neither carries display typography.

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

#: id -> (@font-face family, bundled files for that family)
FAMILIES = {
    "antonio": ("VX Antonio", ("Antonio-Regular.ttf", "Antonio-Bold.ttf")),
    "inter": ("VX Inter", ("Inter-Variable.ttf",)),
    "bebasneue": ("VX Bebas Neue", ("BebasNeue.ttf",)),
    "montserrat": ("VX Montserrat", ("Montserrat.ttf",)),
    "playfairdisplay": ("VX Playfair Display", ("PlayfairDisplay.ttf",)),
    "jetbrainsmono": ("VX JetBrains Mono", ("JetBrainsMono.ttf",)),
}
BUNDLED = tuple(face for _family, files in FAMILIES.values() for face in files)
PINNED = {
    "Antonio-Regular.ttf": ("Antonio", "27f419ff2c909c2f"),
    "Inter-Variable.ttf": ("Inter", "29160a80ff49ddca"),
}

DISPLAY_SAMPLE = "vx-title-kicker"
TEXT_SAMPLE = "vx-card-quote"
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
                   inter: document.fonts.check('400 40px "VX Inter"'),
                   bebasneue: document.fonts.check('400 40px "VX Bebas Neue"'),
                   montserrat: document.fonts.check('400 40px "VX Montserrat"'),
                   playfairdisplay: document.fonts.check('400 40px "VX Playfair Display"'),
                   jetbrainsmono: document.fonts.check('400 40px "VX JetBrains Mono"')
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
        f"every page names the {len(BUNDLED)} bundled files as ../../fonts/*.ttf",
        not unreferenced,
        str(unreferenced[:4]),
    )
    unmapped_face = [
        name
        for name, text in pages.items()
        if any(f'font-family: "{face}"' not in text for face, _files in FAMILIES.values())
    ]
    check(
        "every page declares the six families by name",
        not unmapped_face,
        str(unmapped_face[:4]),
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
    no_stack = [
        name
        for name, text in pages.items()
        if any(f"--vx-font-{family}:" not in text for family in FAMILIES)
    ]
    check(
        "every page declares one --vx-font-<id> stack per family",
        not no_stack,
        str(no_stack[:4]),
    )
    no_ids: List[str] = []
    for name, text in pages.items():
        declared = re.search(r"var VX_FONT_IDS = \[(.*?)\];", text, re.DOTALL)
        ids = re.findall(r'"([a-z]+)"', declared.group(1)) if declared else []
        if (
            ids != list(FAMILIES)
            or 'setProperty("--vx-font-family"' not in text
            or "VX_FONT_IDS.indexOf(" not in text
            or '"var(--vx-font-" + vxFontId' not in text
        ):
            no_ids.append(name)
    check(
        "every page maps the font param onto that property",
        not no_ids,
        str(no_ids[:4]),
    )

    bad_declarations: List[str] = []
    bad_defaults: List[str] = []
    defaults_disagree: List[str] = []
    bad_notes: List[str] = []
    for directory in directories:
        name = directory.name
        declared = manifest(directory / "template.json").get("params", {}).get("font")
        if (
            not isinstance(declared, dict)
            or declared.get("type") != "string"
            or list(declared.get("values") or []) != list(FAMILIES)
        ):
            bad_declarations.append(name)
            continue
        if declared.get("default") != expected_default(name):
            bad_defaults.append(name)
        note = str(declared.get("note") or "")
        if "OFL-1.1" not in note or "bundled" not in note.lower() or any(
            family not in note for family in FAMILIES
        ):
            bad_notes.append(name)
        page_default = re.search(r'font: "([a-z]+)",', pages[name])
        if page_default is None or page_default.group(1) != declared.get("default"):
            defaults_disagree.append(name)
    check(
        "every template.json lists exactly the six bundled ids",
        not bad_declarations,
        str(bad_declarations[:4]),
    )
    check(
        "the default follows the family rule (display antonio, text inter)",
        not bad_defaults,
        str(bad_defaults[:4]),
    )
    check(
        "the font note names the six bundled OFL-1.1 families",
        not bad_notes,
        str(bad_notes[:4]),
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
    check(
        f"the {len(BUNDLED)} font files are counted as dependencies",
        deps == {len(BUNDLED)},
        str(deps),
    )

    absent = [face for face in BUNDLED if not (FONTS / face).is_file()]
    check(f"the {len(BUNDLED)} bundled faces exist on disk", not absent, str(absent))
    matrix_text = MATRIX.read_text(encoding="utf-8")
    for face, (family, prefix) in PINNED.items():
        digest = sha256(FONTS / face) if (FONTS / face).is_file() else ""
        check(
            f"{face}: bytes match the {family} row in LICENSES_MATRIX.md",
            digest.startswith(prefix) and prefix in matrix_text,
            f"{digest[:16]}... vs matrix {prefix}...",
        )

    return {directory.name: directory for directory in directories}


def run_dynamic(samples: Dict[str, Path]) -> None:
    anchor = samples["display"]
    accepted = True
    for family in FAMILIES:
        try:
            motion.validate_params(anchor, {"font": family})
        except motion.MotionError as exc:
            accepted = False
            print(f"    validate_params {family}: {exc.code}: {exc}")
    check("all six font values validate against the declaration", accepted)

    refused = ""
    try:
        motion.validate_params(anchor, {"font": "comic"})
    except motion.MotionError as exc:
        refused = exc.code
    check("a seventh family is refused, never silently applied", refused == "PARAM_RANGE", refused)

    probe = motion.available()
    if not probe["available"]:
        skip("font reaches the frame", str(probe.get("reason") or "no system browser"))
        return

    with tempfile.TemporaryDirectory(
        prefix="vx_template_fonts_", ignore_cleanup_errors=True
    ) as tmp:
        work = Path(tmp)
        for label, target in samples.items():
            digests: List[str] = []
            engines: List[object] = []
            echoed: List[object] = []
            for family in FAMILIES:
                digest, result = frame_sha(
                    target, {"font": family}, work / f"{label}-{family}"
                )
                digests.append(digest)
                engines.append(result.get("engine"))
                echoed.append(result.get("params", {}).get("font"))
            check(
                f"{label} {target.name}: the render runs on a real Chromium frame",
                all(engine in ("chrome", "msedge") for engine in engines),
                f"engine={engines[0]!r}",
            )
            check(
                f"{label} {target.name}: the font param reaches the frame",
                echoed == list(FAMILIES),
                f"{echoed}",
            )
            check(
                f"{label} {target.name}: six families give six distinct sha256",
                len(set(digests)) == len(FAMILIES),
                " ".join(digest[:12] for digest in digests),
            )
            if label == "display":
                repeat, _ = frame_sha(
                    target, {"font": "antonio"}, work / "display-antonio-again"
                )
                check(
                    "the same family rendered twice is byte-identical",
                    repeat == digests[0],
                    repeat,
                )

    faces = loaded_faces(anchor)
    if faces is None:
        skip("bundled faces load in the browser", "no system browser")
        return
    check("VX Antonio resolves to the bundled regular", bool(faces.get("antonio")), faces)
    check("VX Antonio resolves to the bundled bold", bool(faces.get("antonioBold")), faces)
    for family, (face, _files) in FAMILIES.items():
        if family == "antonio":
            continue
        check(f"{face} resolves to its bundled file", bool(faces.get(family)), family)


def main() -> int:
    global FAIL
    try:
        directories = run_static()
        display = directories.get(DISPLAY_SAMPLE)
        text = directories.get(TEXT_SAMPLE)
        missing = [
            name
            for name, path in ((DISPLAY_SAMPLE, display), (TEXT_SAMPLE, text))
            if path is None
        ]
        check("the display and text sample templates exist", not missing, str(missing))
        if display is not None and text is not None:
            run_dynamic({"display": display, "text": text})
    except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
        print(f"\nFAIL: {type(exc).__name__}: {exc}")
        FAIL += 1
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
