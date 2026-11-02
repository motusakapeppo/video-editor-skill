"""F25-T14: piu' proporzioni da un progetto, con degradazione dichiarata e cache.

Scenario:

S1 — da un progetto escono tre proporzioni: tre fotogrammi su disco, ognuno alla
     sua dimensione, con i riquadri che cambiano.
S2 — un template che esiste solo in orizzontale dichiara la degradazione
     (`layout_missing`, con il ripiego proposto) invece di ritagliare in
     silenzio: nessun fotogramma per quella proporzione.
S3 — la cache e' condivisa fra le proporzioni: un asset preparato una volta
     (uno stadio eseguito) e riusato (due riusi) su tre formati.

    python tests/test_multiratio.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import multiratio, slotfill, templates  # noqa: E402

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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-multiratio-") as raw:
        work = Path(raw)
        media = work / "media.png"
        Image.new("RGB", (48, 48), (255, 0, 255)).save(media)
        materials = slotfill.MaterialMap().add("background", str(media), "image")

        # S1 ---------------------------------------------------------------- #
        result = multiratio.render_all(
            templates.REGISTRY["product_card"],
            materials,
            ratios=("16:9", "9:16", "1:1"),
            sizes={"16:9": (640, 360), "9:16": (360, 640), "1:1": (480, 480)},
            out_dir=work / "out",
        )
        produced = [item for item in result["frames"] if item["frame"]]
        check(
            "S1 one project gives three ratios",
            len(produced) == 3 and len({item["ratio"] for item in produced}) == 3,
            [item["ratio"] for item in produced],
        )
        check(
            "S1 each frame is on disk at its own size",
            all(
                Image.open(item["frame"]).size == tuple(item["size"])
                for item in produced
            ),
            [Image.open(item["frame"]).size for item in produced],
        )
        check(
            "S1 the boxes change with the ratio",
            len({tuple(round(v, 3) for v in item["size"]) for item in produced}) == 3,
            "",
        )

        # S2 ---------------------------------------------------------------- #
        narrow = multiratio.render_all(
            templates.REGISTRY["banner"],
            slotfill.MaterialMap().add("background", str(media), "image"),
            ratios=("16:9", "9:16"),
            sizes={"16:9": (640, 360), "9:16": (360, 640)},
            out_dir=work / "out-banner",
        )
        degradation = narrow["degradations"][0]
        check(
            "S2 a landscape-only template declares the degradation",
            degradation["ratio"] == "9:16"
            and degradation["code"] == "layout_missing"
            and degradation["declared"] == ["16:9"],
            degradation,
        )
        check(
            "S2 it proposes a fallback instead of cropping silently",
            "pillarbox" in degradation["fallback"] and degradation["frame"] is None,
            degradation["fallback"],
        )
        check(
            "S2 the ratio that does exist still renders",
            any(item["ratio"] == "16:9" and item["frame"] for item in narrow["frames"]),
            [item["ratio"] for item in narrow["frames"] if item["frame"]],
        )

        # S3 ---------------------------------------------------------------- #
        cache = multiratio.SharedCache(work / "cache")
        reused = multiratio.render_all(
            templates.REGISTRY["product_card"],
            materials,
            ratios=("16:9", "9:16", "1:1"),
            sizes={"16:9": (640, 360), "9:16": (360, 640), "1:1": (480, 480)},
            out_dir=work / "out-cache",
            cache=cache,
        )
        check(
            "S3 the asset is prepared once and reused between ratios",
            reused["cache"] == {"stages": 1, "hits": 2},
            reused["cache"],
        )
        check(
            "S3 and the reuse still produces three frames",
            all(item["frame"] for item in reused["frames"]),
            len([item for item in reused["frames"] if item["frame"]]),
        )

        second = multiratio.render_all(
            templates.REGISTRY["product_card"],
            materials,
            ratios=("16:9",),
            sizes={"16:9": (640, 360)},
            out_dir=work / "out-cache2",
            cache=cache,
        )
        check(
            "S3 a second project run pays no stage at all",
            second["cache"] == {"stages": 1, "hits": 3},
            second["cache"],
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
