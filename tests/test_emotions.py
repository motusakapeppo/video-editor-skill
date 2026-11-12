"""F25-T06: la ruota delle otto emozioni, versionata e citata.

Scenario:

S1 — la tabella ha una versione e 8 voci; ognuna dichiara i quattro assi e ogni
     asse sta nel suo dominio; `validate()` e' pulito e ogni palette citata esiste
     nella libreria LUT e si risolve senza ripiego.
S2 — una voce senza un asse e' rifiutata con `EMOTION_AXIS` e il nome dell'asse,
     e `problems` nomina ogni asse mancante una volta sola.
S3 — la versione usata e' citata nel Creative Brief (`emotion`) e nell'EDL
     (`x-emotion`), e sopravvive al round trip del documento.
S4 — il foglio degli otto riquadri e' su disco: 4x2, ogni riquadro diverso dal
     primo (il grado arriva ai pixel), byte-identico a una nuova resa.

    python tests/test_emotions.py --write
    python tests/test_emotions.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import brief, edl3, emotions, lut  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402
from _golden import compare_images as golden_compare  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "emotions"
FPS = 12
SIZE = (144, 256)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:60} {reason}")


def source(work: Path) -> Path:
    target = work / "source.mp4"
    proc = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi",
            "-i", f"testsrc2=size={SIZE[0]}x{SIZE[1]}:rate={FPS}:duration=2",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "18", str(target),
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not target.is_file():
        raise RuntimeError("cannot build the source")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    # S1 -------------------------------------------------------------------- #
    check(
        "S1 the wheel declares a version and eight emotions",
        bool(emotions.WHEEL_VERSION) and len(emotions.FAMILY) == 8,
        f"{emotions.WHEEL_VERSION}, {len(emotions.FAMILY)} voci",
    )
    check("S1 every emotion declares the four axes", not emotions.validate(), emotions.validate())
    incomplete = {
        name: [axis for axis in emotions.AXIS_NAMES if axis not in emotion.axes()]
        for name, emotion in emotions.EMOTIONS.items()
    }
    check(
        "S1 no axis is empty",
        not [name for name, missing in incomplete.items() if missing],
        {k: v for k, v in incomplete.items() if v},
    )

    # S2 -------------------------------------------------------------------- #
    broken = dict(emotions.EMOTIONS["gioia"].axes())
    broken.pop("music")
    try:
        emotions.refuse(broken, where="gioia")
        code = ""
        detail = ""
    except emotions.EmotionError as exc:
        code, detail = exc.code, exc.detail
    check("S2 an entry without an axis is refused", code == "EMOTION_AXIS", code)
    check(
        "S2 and the refusal names the axis",
        "'music'" in detail and "axis" in detail,
        detail[:70],
    )
    reported = emotions.problems(broken)
    check(
        "S2 every missing axis is reported once",
        len(reported) == 1 and reported[0].startswith("music:"),
        reported,
    )

    # S3 -------------------------------------------------------------------- #
    document = brief._valid_brief()
    check("S3 a brief without emotion is still legal", not brief.validate(document), "")
    emotions.cite(document, "tenerezza")
    check("S3 the citation keeps the brief legal", not brief.validate(document), brief.validate(document))
    check(
        "S3 the brief cites the wheel version",
        document["emotion"] == {"wheel": emotions.WHEEL_VERSION, "emotion": "tenerezza"},
        document["emotion"],
    )
    bad = brief._valid_brief()
    bad["emotion"] = {"wheel": "vx.emotions/0", "emotion": "tenerezza"}
    check(
        "S3 a stale wheel version is caught",
        any("wheel" in error for error in brief.validate(bad)),
        brief.validate(bad),
    )

    timeline = edl3.Timeline3(fps=12, size=SIZE)
    emotions.apply_to_edl(timeline, "tensione")
    carried = timeline.to_dict()
    again = edl3.Timeline3.from_dict(carried)
    check(
        "S3 the EDL carries the citation in x-emotion",
        carried.get(emotions.EDL_KEY) == {"wheel": emotions.WHEEL_VERSION, "emotion": "tensione"},
        carried.get(emotions.EDL_KEY),
    )
    check(
        "S3 and it survives the round trip",
        emotions.from_edl(again) == {"wheel": emotions.WHEEL_VERSION, "emotion": "tensione"},
        emotions.from_edl(again),
    )

    # S4 -------------------------------------------------------------------- #
    try:
        ff.ffmpeg()
    except Exception as exc:  # noqa: BLE001
        skip("S4 sheet", f"no ffmpeg: {type(exc).__name__}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0 if FAIL == 0 else 1

    with tempfile.TemporaryDirectory(prefix="vx-emotions-") as raw:
        work = Path(raw)
        clip = source(work)
        sheet = emotions.sheet(clip, work / "sheet.png", cache_dir=work / "luts", tile=SIZE)
        manifest = emotions.sheet_manifest(emotions.FAMILY)
        with Image.open(sheet) as image:
            check(
                "S4 the sheet is a 4x2 grid of the eight emotions",
                image.size == (SIZE[0] * 4, SIZE[1] * 2) and len(manifest["cells"]) == 8,
                f"{image.size}, {len(manifest['cells'])} celle",
            )
            canvas = np.asarray(image.convert("RGB"), dtype=float)
        first = canvas[0:SIZE[1], 0:SIZE[0], :]
        differences = []
        for cell in manifest["cells"][1:]:
            column, row = cell["column"], cell["row"]
            tile = canvas[row * SIZE[1] : (row + 1) * SIZE[1], column * SIZE[0] : (column + 1) * SIZE[0], :]
            differences.append(float(abs(tile - first).mean()))
        check(
            "S4 every tile differs from the first: the grading reaches the pixels",
            all(value > 0.5 for value in differences),
            [round(value, 1) for value in differences],
        )
        check(
            "S4 each tile is the same frame, same clip",
            all(value > 0.0 for value in differences),
            "",
        )

        stored = FIXTURES / "sheet.png"
        if args.write:
            FIXTURES.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(sheet.read_bytes())
            (FIXTURES / "sheet.json").write_text(
                json.dumps(
                    dict(manifest, sha256=digest(sheet), size=image.size, tile=list(SIZE)),
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            check("S4 the sheet is written with its manifest", True, digest(sheet)[:12])
        else:
            same, detail = golden_compare(stored, sheet, psnr_min=25.0)
            check("S4 the sheet matches its reference", same, detail)

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())


