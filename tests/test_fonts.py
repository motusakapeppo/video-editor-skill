"""Test del font reale e portabile: il bundle vince, la famiglia finta fallisce.

Prima della modifica non esisteva `vx.fonts`, `burn_filter` non passava
`fontsdir` e `verify_fonts` non controllava la dimensione risolta: tutte le
asserzioni sotto fallivano.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import fonts, subs  # noqa: E402
from vx.contract import Cue  # noqa: E402

OUT = ROOT / "tests" / "_out" / "fonts"

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:52} {detail}")


def cue(text: str = "CIAO MONDO") -> Cue:
    return Cue(
        start=0.0,
        end=1.5,
        text=text,
        words=[{"start": 0.0, "end": 1.5, "word": text}],
        style="karaoke",
        line_count=1,
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    font_dir = fonts.package_font_dir()
    check("package_font_dir exists", font_dir.is_dir(), str(font_dir))

    regular = fonts.font_file("Antonio", "Regular")
    check(
        "font_file('Antonio','Regular') is not None", regular is not None, str(regular)
    )
    check(
        "regular file is inside the package",
        regular is not None and font_dir in regular.parents,
        str(regular),
    )

    arg = fonts.fontsdir_arg()
    check("fontsdir_arg contains 'assets'", "assets" in arg, arg)
    check("fontsdir_arg contains 'fonts'", "fonts" in arg, arg)

    discovered = fonts.discover()
    check(
        "discover lists the bundled Antonio",
        any(d.get("family") == "Antonio" for d in discovered),
        str(len(discovered)),
    )

    bundled = Path(
        subs.write_ass([cue()], OUT / "bundled.ass", aspect="9:16", out_h=1920)
    )
    result = subs.verify_fonts(bundled, log_path=OUT / "bundled.log")
    check(
        "verify_fonts ok with bundled font",
        bool(result.get("ok")),
        str(result.get("error", "")),
    )
    bundled = {str(item.get("family") or "").lower() for item in discovered}
    used = [str(name) for name in result.get("fonts_used", [])]
    check(
        "verify_fonts used a bundled family, not a system one",
        bool(used)
        and all(any(family and family in name.lower() for family in bundled) for name in used),
        str(used),
    )
    check(
        "verify_fonts resolved the declared size",
        bool(result.get("size_ok")),
        f"{result.get('declared_size')} vs {result.get('resolved_size')}",
    )

    fake = Path(
        subs.write_ass(
            [cue()], OUT / "fake.ass", aspect="9:16", out_h=1920, font="NoSuchFontXYZ"
        )
    )
    fake_result = subs.verify_fonts(fake, log_path=OUT / "fake.log")
    check(
        "verify_fonts fails on a missing family",
        not bool(fake_result.get("ok")),
        str(fake_result.get("fonts_used")),
    )
    check(
        "missing family is counted as a fallback",
        int(fake_result.get("fallback_count", 0)) > 0,
        str(fake_result.get("fallback_count")),
    )

    print(f"\n{PASS} pass, {FAIL} fail")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
