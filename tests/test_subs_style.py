"""Test della tipografia ASS: outline e shadow seguono out_h, non un numero fisso.

Il riferimento misurato e' 7/4 a 1920 di altezza sul verticale e 4/2 a 1080
sull'orizzontale (references/04-specifiche-social.md:85).  Prima della modifica
il verticale usciva con Outline=0 e Shadow=0, quindi queste asserzioni fallivano.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import subs  # noqa: E402
from vx.contract import Cue  # noqa: E402

OUT = ROOT / "tests" / "_out" / "subs_style"

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


def style_line(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Style:"):
            return line
    return ""


def style_fields(path: Path) -> list[str]:
    return style_line(path).split(",")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    vertical = Path(
        subs.write_ass([cue()], OUT / "vertical.ass", aspect="9:16", out_h=1920)
    )
    vline = style_line(vertical)
    check(
        "vertical 1920: BorderStyle/Outline/Shadow = 1,7,4", ",1,7,4," in vline, vline
    )

    horizontal = Path(
        subs.write_ass([cue()], OUT / "horizontal.ass", aspect="16:9", out_h=1080)
    )
    hline = style_line(horizontal)
    check(
        "horizontal 1080: BorderStyle/Outline/Shadow = 1,4,2", ",1,4,2," in hline, hline
    )

    for label, path in (("vertical", vertical), ("horizontal", horizontal)):
        fields = style_fields(path)
        outline = int(fields[16]) if len(fields) > 16 else -1
        shadow = int(fields[17]) if len(fields) > 17 else -1
        check(f"{label}: Outline is not 0", outline != 0, f"outline={outline}")
        check(f"{label}: Shadow is not 0", shadow != 0, f"shadow={shadow}")

    scaled = Path(
        subs.write_ass([cue()], OUT / "vertical_960.ass", aspect="9:16", out_h=960)
    )
    sfields = style_fields(scaled)
    check(
        "vertical 960: outline scales to 4",
        len(sfields) > 16 and int(sfields[16]) == 4,
        f"outline={sfields[16] if len(sfields) > 16 else '?'}",
    )

    print(f"\n{PASS} pass, {FAIL} fail")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
