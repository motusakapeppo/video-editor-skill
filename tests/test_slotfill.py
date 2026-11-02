"""F25-T13: riempire gli slot dai ruoli della MaterialMap, e vederlo sul frame.

Scenario:

S1 — una MaterialMap con tre materiali riempie tre slot di `product_card` e
     dichiara i due rimasti vuoti; le proporzioni cambiano i riquadri.
S2 — un materiale del tipo sbagliato e' `SLOT_TYPE` e nomina lo slot, il tipo
     chiesto e quello ricevuto.
S3 — il frame prova che l'asset dell'utente e' nel template: nel riquadro dello
     slot `media` il colore e' quello dell'asset, senza riempimento e' il fondo,
     e il MAD fra i due fotogrammi e' grande - misurato sul frame, non sul JSON.

    python tests/test_slotfill.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import ffmpeg as ff, slotfill, templates  # noqa: E402

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


def solid(path: Path, rgb) -> Path:
    Image.new("RGB", (64, 64), rgb).save(path)
    return path


def main() -> int:
    template = templates.REGISTRY["product_card"]

    with tempfile.TemporaryDirectory(prefix="vx-slots-") as raw:
        work = Path(raw)
        media = solid(work / "media.png", (255, 0, 255))
        logo = solid(work / "logo.png", (255, 255, 255))

        # S1 ---------------------------------------------------------------- #
        materials = (
            slotfill.MaterialMap()
            .add("background", str(media), "image")
            .add("headline", "Officina Vx", "text")
            .add("badge", str(logo), "logo")
        )
        result = slotfill.fill(template, materials, ratio="9:16")
        check(
            "S1 three materials fill three slots",
            result["filled"] == 3,
            [item.slot for item in result["fills"]],
        )
        check(
            "S1 the remaining two slots are declared unfilled, not guessed",
            sorted(item["slot"] for item in result["unfilled"]) == ["body", "cta"],
            result["unfilled"],
        )
        wide = slotfill.fill(template, materials, ratio="16:9")
        portrait_box = next(item.box for item in result["fills"] if item.slot == "media")
        wide_box = next(item.box for item in wide["fills"] if item.slot == "media")
        check(
            "S1 the ratio changes the box, not the slot",
            portrait_box != wide_box,
            f"{portrait_box} vs {wide_box}",
        )

        # S2 ---------------------------------------------------------------- #
        wrong = slotfill.MaterialMap().add("background", "un titolo", "text")
        try:
            slotfill.fill(template, wrong, ratio="9:16")
            code, detail = "", ""
        except slotfill.SlotError as exc:
            code, detail = exc.code, exc.detail
        check("S2 a material of the wrong type is refused", code == "SLOT_TYPE", code)
        check(
            "S2 and the refusal names the slot and both types",
            ".media" in detail and "image" in detail and "text" in detail,
            detail[:80],
        )

        # S3 ---------------------------------------------------------------- #
        try:
            ff.ffmpeg()
        except Exception as exc:  # noqa: BLE001
            skip("S3 frame", f"no ffmpeg: {type(exc).__name__}")
        else:
            only_media = slotfill.MaterialMap().add("background", str(media), "image")
            with_media = slotfill.fill(template, only_media, ratio="9:16")
            without = slotfill.fill(template, slotfill.MaterialMap(), ratio="9:16")
            # ``without`` has nothing to draw: the renderer refuses rather than
            # inventing a frame, so the comparison is against the plain background.
            plain = work / "plain.png"
            subprocess.run(
                [
                    ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=#101010:s=360x640:d=1:r=1",
                    "-frames:v", "1", str(plain),
                ],
                check=True,
                capture_output=True,
            )
            filled = slotfill.render(with_media, size=(360, 640), out=work / "filled.png")
            empty_refused = ""
            try:
                slotfill.render(without, size=(360, 640), out=work / "empty.png")
            except slotfill.SlotError as exc:
                empty_refused = exc.code
            check(
                "S3 a slot with nothing to draw is refused, not faked",
                empty_refused == "SLOT_RENDER",
                empty_refused,
            )

            box = next(item.box for item in with_media["fills"] if item.slot == "media")
            width, height = 360, 640
            x = int(round(box[0] * width))
            y = int(round(box[1] * height))
            w = int(round(box[2] * width))
            h = int(round(box[3] * height))
            region = np.s_[y + 4 : y + h - 4, x + 4 : x + w - 4]

            filled_pixels = np.asarray(Image.open(filled).convert("RGB"), dtype=float)
            plain_pixels = np.asarray(Image.open(plain).convert("RGB"), dtype=float)
            inside = filled_pixels[region].reshape(-1, 3).mean(axis=0)
            background = plain_pixels[region].reshape(-1, 3).mean(axis=0)
            check(
                "S3 the user asset is inside the slot box",
                inside[0] > 200 and inside[1] < 60 and inside[2] > 200,
                f"media {inside.round(1)}",
            )
            check(
                "S3 and the same box without the asset is the background",
                float(background.max()) < 40,
                f"fondo {background.round(1)}",
            )
            mad = float(abs(filled_pixels[region] - plain_pixels[region]).mean())
            check(
                "S3 the MAD on the slot zone is large",
                mad > 30.0,
                f"MAD {mad:.1f}",
            )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
