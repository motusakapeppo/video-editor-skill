"""F25-T05: la libreria delle palette LUT 3D, con attribuzione, ripiego e pelle.

Scenario:

S1 — il registro dichiara almeno 50 palette, ognuna con categoria, licenza,
     autore e fonte non vuoti; ogni ricetta sta nel suo dominio; `validate()` e'
     pulito e le categorie non sono un'etichetta sola.
S2 — il file `.cube` scritto dal registro ha l'intestazione dichiarata, 17^3
     righe, e i punti campionati coincidono con la ricetta (l'ordine degli assi e'
     quello della spec, non quello che capita).
S3 — una LUT assente dal disco produce `fallback=True` con `eq` + `colorbalance`,
     e quel filtro **gira davvero**: un fotogramma esce, diverso dal non graduato.
S4 — `protect_skin` misura il delta E su una toppa presa da un fotogramma vero e
     abbassa la forza finche' rientra; la tolleranza si dichiara provvisoria.
S5 — ogni palette ha un frame di riferimento, byte-identico a una nuova resa.

    python tests/test_lut_library.py --write
    python tests/test_lut_library.py
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

from vx import ffmpeg as ff, lut  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "lut"
FPS = 12
SIZE = (144, 256)
AT = 0.5

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:62} {reason}")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(work: Path) -> Path:
    target = work / "source.mp4"
    if target.is_file():
        return target
    proc = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi",
            "-i", f"testsrc2=size={SIZE[0]}x{SIZE[1]}:rate={FPS}:duration=1",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "18", str(target),
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not target.is_file():
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-300:])
    return target


def shot(src: Path, out: Path, filters: str) -> Path:
    proc = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{AT:.3f}", "-i", str(src), "-vf", filters,
            "-frames:v", "1", str(out),
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-300:])
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

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

    # S1 -------------------------------------------------------------------- #
    check(
        "S1 the registry declares at least 50 palettes",
        lut.count() >= lut.MIN_PALETTES,
        f"{lut.count()} palettes",
    )
    check("S1 every entry is valid", not lut.validate(), lut.validate())
    grouped = lut.by_category()
    check(
        "S1 categories are not a single label",
        sum(1 for names in grouped.values() if names) >= 5,
        {k: len(v) for k, v in grouped.items() if v},
    )
    incomplete = [
        name
        for name in lut.FAMILY
        if any(
            not str(getattr(lut.PALETTES[name].attribution, field) or "").strip()
            for field in ("license", "author", "source")
        )
    ]
    check("S1 every palette carries license, author and source", not incomplete, incomplete[:3])

    # S2 -------------------------------------------------------------------- #
    with tempfile.TemporaryDirectory(prefix="vx-lut-") as raw:
        work = Path(raw)
        target = lut.ensure("cinema_teal_orange", work / "luts")
        lines = target.read_text(encoding="utf-8").splitlines()
        body = [line for line in lines if not line.startswith("#") and not line[:1].isalpha()]
        header = [line for line in lines if line.startswith("LUT_3D_SIZE")]
        check(
            "S2 the cube declares size 17 and 17^3 entries",
            header == [f"LUT_3D_SIZE {lut.CUBE_SIZE}"]
            and len(body) == lut.CUBE_SIZE ** 3,
            f"{len(body)} entries",
        )
        palette = lut.require("cinema_teal_orange")
        exact = True
        for red_i, green_i, blue_i in ((0, 0, 0), (16, 16, 16), (4, 9, 15), (16, 0, 8)):
            index = (blue_i * lut.CUBE_SIZE + green_i) * lut.CUBE_SIZE + red_i
            stored = tuple(float(v) for v in body[index].split())
            expected = lut.transform(
                (red_i / 16.0, green_i / 16.0, blue_i / 16.0), palette.recipe
            )
            exact = exact and all(abs(stored[i] - expected[i]) < 2e-6 for i in range(3))
        check("S2 the lattice points match the recipe, axis order included", exact, "")

        first = lut.palette_cube_text(palette)
        again = lut.palette_cube_text(lut.require("cinema_teal_orange"))
        check("S2 generation is deterministic", first == again, "")

        # S3 ---------------------------------------------------------------- #
        missing = work / "empty"
        missing.mkdir()
        plan = lut.grade("cinema_teal_orange", cache_dir=missing)
        joined = " ".join(plan["filters"])
        check(
            "S3 a LUT missing from disk falls back to eq + colorbalance",
            plan["fallback"] is True and "eq=" in joined and "colorbalance=" in joined,
            joined[:52],
        )
        check(
            "S3 and the report says why",
            "disco" in str(plan["why"]) and plan["fidelity"] == "approx",
            str(plan["why"])[:52],
        )

        src = source(work)
        plain = shot(src, work / "plain.png", "null")
        fell = shot(src, work / "fallback.png", lut.filter_string(plan))
        base = np.asarray(Image.open(plain).convert("RGB"), dtype=float)
        alt = np.asarray(Image.open(fell).convert("RGB"), dtype=float)
        check(
            "S3 the fallback filter renders a frame that differs",
            float(abs(alt - base).mean()) > 0.5,
            f"MAD {float(abs(alt - base).mean()):.2f}",
        )

        # S4 ---------------------------------------------------------------- #
        patch = base[96:160, 48:96, :].reshape(-1, 3).mean(axis=0) / 255.0
        skin = lut.protect_skin("noir_bianco_nero", patch)
        check(
            "S4 the skin distance is measured on a real patch",
            skin["delta_e_full"] > 0.0 and len(skin["patch"]) == 3,
            f"dE full {skin['delta_e_full']:.2f}",
        )
        check(
            "S4 strength is lowered until the distance is inside",
            skin["inside"] is True and skin["strength"] < 1.0 and skin["steps"] > 0,
            f"strength {skin['strength']:.2f} after {skin['steps']} steps, "
            f"dE {skin['delta_e']:.2f} <= {skin['tolerance']:.1f}",
        )
        check(
            "S4 the tolerance is declared provisional, not calibrated",
            skin["tolerance_status"] == "provisional"
            and "F6" in skin["tolerance_note"],
            skin["tolerance_status"],
        )

        # S5 ---------------------------------------------------------------- #
        FIXTURES.mkdir(parents=True, exist_ok=True)
        rendered = 0
        for name in lut.FAMILY:
            cube = lut.ensure(name, work / "luts")
            plan = lut.grade(name, cache_dir=work / "luts")
            try:
                png = shot(src, work / f"{name}.png", lut.filter_string(plan))
            except Exception as exc:  # noqa: BLE001
                check(f"S5 {name}: renders", False, f"{type(exc).__name__}: {exc}")
                continue
            rendered += 1
            stored = FIXTURES / f"{name}.png"
            if args.write:
                stored.write_bytes(png.read_bytes())
                (FIXTURES / f"{name}.json").write_text(
                    json.dumps(
                        {
                            "palette": name,
                            "category": lut.PALETTES[name].category,
                            "fps": FPS,
                            "size": list(SIZE),
                            "at": AT,
                            "sha256": digest(png),
                            "fidelity": "lut3d" if not plan["fallback"] else "approx",
                            "attribution": lut.PALETTES[name].attribution.as_dict(),
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            else:
                check(
                    f"S5 {name}: reference frame is byte-identical",
                    stored.is_file() and digest(stored) == digest(png),
                    digest(png)[:12],
                )
                check(
                    f"S5 {name}: the palette reaches the pixels",
                    digest(png) != digest(plain),
                    "",
                )
            cube.unlink(missing_ok=True)
        if args.write:
            check("S5 every palette has a reference frame", rendered == lut.count(), rendered)

    if not args.write:
        on_disk = sorted(p.stem for p in FIXTURES.glob("*.png")) if FIXTURES.is_dir() else []
        check(
            "S5 one reference frame per palette, no more and no fewer",
            on_disk == sorted(lut.FAMILY),
            f"{len(on_disk)} frames",
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
