"""F4-T15: il ripristino arriva ai pixel, e non inventa.

Scenari (contratto, scritti prima del codice):

S1 — un clip morbido, con `restore_soft` ai valori dichiarati, deve **misurare**
     piu' nitidezza dello stesso clip senza effetto: la varianza del laplaciano
     sulla cornice resa sale, misurata sullo stesso istante.
S2 — un clip scuro, con `restore_lowlight`, deve alzare la luma media e **non**
     bruciare l'alta luce oltre un punto percentuale.
S3 — il frame di riferimento di ogni voce esiste su disco e il gate della
     libreria lo verifica (questo lo fa `test_edl3_kinds.py`, che qui viene
     invocato in verifica, non riscritto).

python tests/test_restore.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import golden_edl3 as g  # noqa: E402
from vx import edl3, edl3_render  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402

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


def make_clip(path: Path, *, blur: int, brightness: float) -> Path:
    chain = [f"eq=brightness={brightness}"]
    if blur:
        chain = [f"boxblur={blur}:{blur}"] + chain
    proc = subprocess.run(
        [
            ff.ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={g.WIDTH}x{g.HEIGHT}:rate={g.FPS}:duration=1",
            "-vf",
            ",".join(chain),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 or not path.is_file():
        raise RuntimeError(proc.stderr[-300:] or "ffmpeg could not build the clip")
    return path


def frame_of(path: Path, at: float):
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, at * 1000.0)
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok or frame is None:
        raise RuntimeError("cannot decode the rendered frame")
    return frame


def measure(frame) -> dict:
    import cv2
    import numpy as np

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return {
        "sharpness": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        "luma": float(gray.mean()),
        "clip_high": float((gray >= 250).mean() * 100.0),
    }


def render(doc: edl3.Timeline3, out: Path, work: Path) -> Path:
    edl3_render.render(doc, out, workdir=work, audio="drop", settings={})
    return out


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

    catalog = edl3.EFFECT_REGISTRY
    check(
        "the two restore voices are in the library",
        "restore_soft" in catalog and "restore_lowlight" in catalog,
        [k for k in catalog if k.startswith("restore")],
    )
    check(
        "their declared family is restore",
        catalog["restore_soft"].family == "restore"
        and catalog["restore_lowlight"].family == "restore",
        catalog["restore_soft"].family,
    )

    with tempfile.TemporaryDirectory(prefix="vx-restore-") as raw:
        work = Path(raw)
        try:
            soft = make_clip(work / "soft.mp4", blur=3, brightness=0.0)
            dark = make_clip(work / "dark.mp4", blur=0, brightness=-0.75)
        except Exception as exc:  # noqa: BLE001
            check("the fixtures build", False, f"{type(exc).__name__}: {exc}")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 1

        # S1: restore_soft raises the measured sharpness of the same frame.
        plain = render(
            g._document([g._clip("c1", soft, src_out=1.0)]),
            work / "soft_plain.mp4",
            work / "w1",
        )
        restored = render(
            g._document(
                [g._clip("c1", soft, src_out=1.0, effects=[g._effect("restore_soft")])]
            ),
            work / "soft_restore.mp4",
            work / "w2",
        )
        before = measure(frame_of(plain, 0.5))
        after = measure(frame_of(restored, 0.5))
        check(
            "S1 restore_soft raises the measured sharpness",
            after["sharpness"] > before["sharpness"] * 1.10,
            f"{before['sharpness']:.1f} -> {after['sharpness']:.1f}",
        )
        check(
            "S1 and the frames are not the same picture",
            abs(after["sharpness"] - before["sharpness"]) > 1e-6,
            f"{before['sharpness']:.3f} vs {after['sharpness']:.3f}",
        )

        # S2: restore_lowlight lifts the shadows without burning the highlights.
        dark_plain = render(
            g._document([g._clip("c1", dark, src_out=1.0)]),
            work / "dark_plain.mp4",
            work / "w3",
        )
        dark_lifted = render(
            g._document(
                [
                    g._clip(
                        "c1", dark, src_out=1.0, effects=[g._effect("restore_lowlight")]
                    )
                ]
            ),
            work / "dark_restore.mp4",
            work / "w4",
        )
        d0 = measure(frame_of(dark_plain, 0.5))
        d1 = measure(frame_of(dark_lifted, 0.5))
        check(
            "S2 restore_lowlight lifts the shadows",
            d1["luma"] > d0["luma"] + 1.0,
            f"luma {d0['luma']:.1f} -> {d1['luma']:.1f}",
        )
        check(
            "S2 and does not burn the highlights",
            d1["clip_high"] <= d0["clip_high"] + 1.0,
            f"clipped {d0['clip_high']:.2f}% -> {d1['clip_high']:.2f}%",
        )

        # S3: the library gate holds for the two new kinds.
        kinds = subprocess.run(
            [sys.executable, str(ROOT / "tests" / "test_edl3_kinds.py")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
        )
        check(
            "S3 the library gate passes, the two reference frames included",
            kinds.returncode == 0 and "PASS / 0 FAIL" in kinds.stdout,
            kinds.stdout.strip().splitlines()[-1] if kinds.stdout else kinds.stderr[-200:],
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
