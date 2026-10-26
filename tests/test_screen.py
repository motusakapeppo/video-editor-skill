"""Test dell'inquadratura: l'offset del soggetto decide, non il centro.

Le asserzioni sono sul contratto: ``place`` viene chiamato, il volto c'e' o non
c'e', l'offset supera o non supera la soglia dichiarata, la provenienza porta il
prefisso ammesso e la banda di incertezza produce inazione.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import aesthetics, screen  # noqa: E402

CORPUS = ROOT / "tests" / "stock_corpus"
STREET = CORPUS / "real" / "street_169.webm"
BRIGHT = CORPUS / "bright_45.mp4"

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:46} {detail}")


class Proxy:
    """Conta le chiamate a ``place`` e puo' restituire un placement fisso."""

    def __init__(self, module, fixed=None):
        self.module = module
        self.fixed = fixed
        self.calls = 0

    def place(self, *args, **kwargs):
        self.calls += 1
        if self.fixed is not None:
            return self.fixed
        return self.module.place(*args, **kwargs)


def run_with_proxy(fixed=None, **kwargs):
    real = screen.aesthetics
    proxy = Proxy(real, fixed)
    screen.aesthetics = proxy  # type: ignore[assignment]
    try:
        return screen.decide(**kwargs), proxy
    finally:
        screen.aesthetics = real  # type: ignore[assignment]


def synthetic(ox: float, oy: float = 0.0, src_w: int = 1280, src_h: int = 720):
    crop_w, crop_h = 540, 720
    centre_x = (src_w - crop_w) / 2.0
    centre_y = (src_h - crop_h) / 2.0
    return aesthetics.Placement(
        crop_w=crop_w,
        crop_h=crop_h,
        x=int(round(centre_x + ox * src_w)),
        y=int(round(centre_y + oy * src_h)),
        face_found=True,
        reason="synthetic",
    )


def main() -> int:
    print(f"soglia: {screen.OFFSET_THRESHOLD} ({screen.PROVENANCE})")

    print("\nclip reale con volto: place chiamato, crop restituito")
    face_at = None
    for at in (1.5, 2.0, 2.5, 3.0):
        probe = aesthetics.place(STREET, at, src_w=1280, src_h=720)
        if probe.face_found:
            face_at = at
            break
    if face_at is None:
        check("volto trovato nella clip reale", False, "nessun volto in 1.5-3.0 s")
    else:
        real, proxy = run_with_proxy(
            path=str(STREET), at=face_at, src_w=1280, src_h=720
        )
        check("place chiamato una volta", proxy.calls == 1, str(proxy.calls))
        check(
            "calls.place == 1",
            real["calls"]["place"] == 1,
            str(real["calls"]),
        )
        check(
            "crop restituito",
            real["composition"]["crop"]["crop_w"] > 0
            and real["composition"]["crop"]["crop_h"] > 0,
            f"{real['composition']['crop']['crop_w']}x"
            f"{real['composition']['crop']['crop_h']}",
        )
        check("face_found True", real["face_found"] is True, str(real["face_found"]))
        check(
            "provenienza declared:",
            real["composition"]["provenance"].startswith("declared:"),
            real["composition"]["provenance"],
        )
        print(
            f"  volto a {face_at:.1f}s: {real['composition']['name']} "
            f"offset {real['composition']['value']} "
            f"conf {real['composition']['confidence']}"
        )

    print("\nclip senza volto: offset None, centro, inazione")
    no_face, proxy = run_with_proxy(path=str(BRIGHT), at=0.5, src_w=1080, src_h=1350)
    check("place chiamato una volta", proxy.calls == 1, str(proxy.calls))
    check(
        "subject_offset None",
        no_face["composition"]["value"] is None,
        str(no_face["composition"]["value"]),
    )
    check(
        "composizione centre",
        no_face["composition"]["name"] == "centre",
        no_face["composition"]["name"],
    )
    check(
        "applied False",
        no_face["composition"]["applied"] is False,
        str(no_face["composition"]["applied"]),
    )
    check(
        "confidenza 0.5",
        no_face["composition"]["confidence"] == 0.5,
        str(no_face["composition"]["confidence"]),
    )

    print("\nplacement sintetico spostato: offset, applied True")
    shifted, _ = run_with_proxy(
        fixed=synthetic(0.18), path="synthetic", at=0.0, src_w=1280, src_h=720
    )
    check(
        "composizione offset",
        shifted["composition"]["name"] == "offset",
        shifted["composition"]["name"],
    )
    check(
        "applied True",
        shifted["composition"]["applied"] is True,
        str(shifted["composition"]["applied"]),
    )
    check(
        "confidenza 1.0",
        shifted["composition"]["confidence"] == 1.0,
        str(shifted["composition"]["confidence"]),
    )
    check(
        "offset normalizzato",
        abs(shifted["composition"]["value"]["x"] - 0.18) < 0.01,
        str(shifted["composition"]["value"]),
    )

    print("\nplacement sintetico quasi centrato: centro, applied True")
    centred, _ = run_with_proxy(
        fixed=synthetic(0.005), path="synthetic", at=0.0, src_w=1280, src_h=720
    )
    check(
        "composizione centre",
        centred["composition"]["name"] == "centre",
        centred["composition"]["name"],
    )
    check(
        "applied True (decisione sicura)",
        centred["composition"]["applied"] is True,
        f"conf {centred['composition']['confidence']}",
    )
    check(
        "confidenza < 0.2",
        centred["composition"]["confidence"] < 0.2,
        str(centred["composition"]["confidence"]),
    )

    print("\nplacement sintetico nella banda di incertezza: inazione")
    band, _ = run_with_proxy(
        fixed=synthetic(0.02), path="synthetic", at=0.0, src_w=1280, src_h=720
    )
    check(
        "confidenza in [0.2, 0.8]",
        0.2 <= band["composition"]["confidence"] <= 0.8,
        str(band["composition"]["confidence"]),
    )
    check(
        "composizione centre e applied False",
        band["composition"]["name"] == "centre"
        and band["composition"]["applied"] is False,
        f"{band['composition']['name']} applied {band['composition']['applied']}",
    )
    check(
        "inaction opposto di applied",
        isinstance(band["composition"]["inaction"], bool)
        and band["composition"]["inaction"] is True,
        str(band["composition"]["inaction"]),
    )

    print("\nplan: una decisione per istante")
    real = screen.aesthetics
    proxy = Proxy(real, synthetic(0.18))
    screen.aesthetics = proxy  # type: ignore[assignment]
    try:
        decisions = screen.plan("synthetic", [0.0, 1.0, 2.0], 1280, 720)
    finally:
        screen.aesthetics = real  # type: ignore[assignment]
    check("tre decisioni", len(decisions) == 3, str(len(decisions)))
    check("place chiamato tre volte", proxy.calls == 3, str(proxy.calls))
    check(
        "ogni decisione dichiara una chiamata",
        all(item["calls"]["place"] == 1 for item in decisions),
        str([item["calls"]["place"] for item in decisions]),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
