"""F25-T04: lenti con schema, tetto e ripiego dichiarato.

Scenario:

S1 — quattro lenti dichiarate, ognuna con parametri e dominio, `validate()` pulito.
S2 — bokeh **senza** mappa di profondita' non solleva: restituisce il ripiego
     (`soft_focus`) con `fallback=True` e il motivo. Con la mappa, l'effetto
     richiesto.
S3 — due lenti in pila passano; due **distorsioni** sono `LENS_CONFLICT` con i due
     nomi; un parametro fuori dominio e' `LENS_RANGE` col nome.
S4 — ogni lente arriva ai pixel: il frame con la lente differisce da quello senza,
     e la catena viene accettata dal compilatore.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, edl3_render, lenses  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402
from PIL import Image  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FPS = 12
SIZE = (320, 568)


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:58} {reason}")


def frame(effects, work: Path, name: str):
    import numpy as np

    clip = edl3.Clip3(
        id="c1",
        source=str(work / "source.mp4"),
        src_in=0.0,
        src_out=1.0,
        effects=effects,
    )
    document = edl3.Timeline3(fps=FPS, size=SIZE, clips=[clip])
    video = work / f"{name}.mp4"
    edl3_render.render(document, video, workdir=work / f"w-{name}", audio="drop", settings={})
    png = work / f"{name}.png"
    proc = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", "0.500", "-i", str(video), "-frames:v", "1", str(png),
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not png.is_file():
        raise RuntimeError("cannot extract the frame")
    return np.asarray(Image.open(png).convert("RGB"), dtype=float)


def main() -> int:
    try:
        binary = ff.ffmpeg()
    except Exception as exc:  # noqa: BLE001
        skip("whole file", f"no ffmpeg: {type(exc).__name__}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0
    if not binary:
        skip("whole file", "no ffmpeg")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    check(
        "S1 the four lenses are declared",
        set(lenses.FAMILY) == {"vignette", "chromatic_aberration", "bokeh", "fisheye"},
        lenses.FAMILY,
    )
    check("S1 every lens declares schema and trigger", not lenses.validate(), lenses.validate())

    flat = lenses.build("bokeh")
    check(
        "S2 bokeh without a depth map is a declared fallback, not an error",
        flat["fallback"] is True
        and flat["effects"][0].kind == "soft_focus"
        and "profondita'" in str(flat["why"]),
        flat["effects"][0].kind,
    )
    shaped = lenses.build("bokeh", depth_map="depth.png", radius=12.0)
    check(
        "S2 with a depth map it is the bokeh it claims",
        shaped["fallback"] is False and shaped["effects"][0].kind == "bokeh",
        shaped["effects"][0].kind,
    )

    pair = lenses.stack(["vignette", "chromatic_aberration"])
    check("S3 two lenses may ride the same clip", len(pair) == 2, [l.name for l in pair])
    try:
        lenses.stack(["fisheye", "fisheye"])
        conflict = ""
    except lenses.LensError as exc:
        conflict = exc.code
        names = exc.detail
    check("S3 two distortions are refused", conflict == "LENS_CONFLICT", conflict)
    check(
        "S3 and the refusal names both",
        conflict == "LENS_CONFLICT" and names.count("fisheye") >= 2,
        names[:80] if conflict else "",
    )
    try:
        lenses.build("vignette", angle=9.0)
        out_of_range = ""
    except lenses.LensError as exc:
        out_of_range = exc.code
    check("S3 a parameter outside its domain is refused", out_of_range == "LENS_RANGE", out_of_range)

    with tempfile.TemporaryDirectory(prefix="vx-lenses-") as raw:
        work = Path(raw)
        proc = subprocess.run(
            [
                ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"testsrc2=size={SIZE[0]}x{SIZE[1]}:rate={FPS}:duration=1",
                "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "18",
                str(work / "source.mp4"),
            ],
            capture_output=True,
        )
        if proc.returncode != 0:
            check("S4 the fixture builds", False, proc.stderr[-200:])
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 1
        base = frame([], work, "plain")
        for name in ("vignette", "chromatic_aberration", "fisheye", "bokeh"):
            chain = lenses.build(name)["effects"]
            try:
                shot = frame(chain, work, name)
            except Exception as exc:  # noqa: BLE001
                check(f"S4 {name}: renders", False, f"{type(exc).__name__}: {exc}")
                continue
            mad = float(abs(shot - base).mean())
            check(f"S4 {name}: reaches the pixels", mad > 0.5, f"MAD {mad:.2f}")

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
