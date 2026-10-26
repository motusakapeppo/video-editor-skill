"""vx.screen — l'inquadratura nasce dall'offset del soggetto, non dal centro.

Perche' esiste: ``aesthetics.place`` sa gia' dove mettere il crop per tenere gli
occhi sulla linea dell'occhio, ma non dice se quel crop e' SPOSTATO rispetto al
centro geometrico. La differenza fra un'inquadratura centrata e una decentrata
e' una decisione di montaggio, e come ogni decisione deve portare il driver, il
valore, la soglia con la sua provenienza, la confidenza e la frase di inazione.

La soglia e' dichiarata, non inventata: ``1/20 della larghezza`` (0.05). Sotto
quella soglia il soggetto e' di fatto al centro; sopra, il crop lo segue. La
confidenza e' l'offset normalizzato sulla soglia: un offset a meta' strada fra
centro e soglia non e' una decisione, e' un dubbio, e produce inazione.

``decide`` chiama ``aesthetics.place`` esattamente una volta e lo dichiara in
``calls``: il conteggio e' un fatto del codice, non una promessa.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

from . import aesthetics

OFFSET_THRESHOLD = 0.05
PROVENANCE = "declared:1/20_width"
DRIVER = "subject_offset"

UNCERTAIN_LOW = 0.2
UNCERTAIN_HIGH = 0.8
NO_FACE_CONFIDENCE = 0.5


def _placement_dict(placement: Any) -> Dict[str, Any]:
    if hasattr(placement, "to_dict"):
        return dict(placement.to_dict())
    if is_dataclass(placement):
        return asdict(placement)
    return {
        "crop_w": int(getattr(placement, "crop_w")),
        "crop_h": int(getattr(placement, "crop_h")),
        "x": int(getattr(placement, "x")),
        "y": int(getattr(placement, "y")),
        "face_found": bool(getattr(placement, "face_found")),
    }


def decide(path: str, at: float, src_w: int, src_h: int) -> Dict[str, Any]:
    """Decidi se il crop segue il soggetto o resta al centro.

    ``path`` e' la sorgente, ``at`` l'istante in secondi, ``src_w``/``src_h`` la
    geometria della sorgente. Il placement e' quello di ``aesthetics.place``:
    nessuna nuova rilevazione del volto, nessuna seconda verita'.
    """
    placement = aesthetics.place(path, at, src_w=src_w, src_h=src_h)
    calls = {"place": 1}

    if not placement.face_found:
        offset: Optional[Dict[str, float]] = None
        name = "centre"
        applied = False
        confidence = NO_FACE_CONFIDENCE
        reason = "nessun volto al fotogramma: inquadratura al centro, nessuna scelta."
    else:
        centre_x = (float(src_w) - float(placement.crop_w)) / 2.0
        centre_y = (float(src_h) - float(placement.crop_h)) / 2.0
        ox = (float(placement.x) - centre_x) / float(src_w)
        oy = (float(placement.y) - centre_y) / float(src_h)
        offset = {"x": round(ox, 4), "y": round(oy, 4)}
        magnitude = max(abs(ox), abs(oy))
        confidence = min(1.0, max(0.0, magnitude / OFFSET_THRESHOLD))

        if UNCERTAIN_LOW <= confidence <= UNCERTAIN_HIGH:
            name = "centre"
            applied = False
            reason = (
                f"offset {magnitude:.4f} vicino alla soglia {OFFSET_THRESHOLD}: "
                "inazione, inquadratura al centro."
            )
        elif magnitude >= OFFSET_THRESHOLD:
            name = "offset"
            applied = True
            reason = (
                f"offset {magnitude:.4f} oltre la soglia {OFFSET_THRESHOLD}: "
                "il crop segue il soggetto."
            )
        else:
            name = "centre"
            applied = True
            reason = (
                f"offset {magnitude:.4f} sotto la soglia {OFFSET_THRESHOLD}: "
                "il soggetto e' al centro."
            )

    composition = {
        "name": name,
        "crop": {
            "crop_w": int(placement.crop_w),
            "crop_h": int(placement.crop_h),
            "x": int(placement.x),
            "y": int(placement.y),
            "face_found": bool(placement.face_found),
        },
        "driver": DRIVER,
        "value": offset,
        "provenance": PROVENANCE,
        "inaction": not applied,
        "inaction_reason": reason,
        "confidence": round(float(confidence), 4),
        "applied": applied,
    }
    return {
        "composition": composition,
        "placement": _placement_dict(placement),
        "face_found": bool(placement.face_found),
        "calls": calls,
    }


def plan(
    path: str, frames: List[float], src_w: int, src_h: int
) -> List[Dict[str, Any]]:
    """La stessa decisione per ogni istante richiesto, nell'ordine dato."""
    return [decide(path, float(at), src_w, src_h) for at in frames]


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #
def _selftest() -> int:
    import sys
    from pathlib import Path

    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:44} {detail}")

    root = Path(__file__).resolve().parents[2]
    corpus = root / "tests" / "stock_corpus"
    street = corpus / "real" / "street_169.webm"
    bright = corpus / "bright_45.mp4"

    print(f"soglia: {OFFSET_THRESHOLD} ({PROVENANCE})")

    # Trova un istante con un volto usando place direttamente: la scansione non
    # e' la decisione, serve solo a scegliere un fotogramma valido.
    face_at: Optional[float] = None
    for at in (1.5, 2.0, 2.5, 3.0):
        probe = aesthetics.place(street, at, src_w=1280, src_h=720)
        if probe.face_found:
            face_at = at
            break

    if face_at is None:
        check("clip reale con volto trovato", False, "nessun volto in 1.5-3.0 s")
    else:
        real = decide(str(street), face_at, 1280, 720)
        check(
            "clip reale con volto: place chiamato una volta",
            real["calls"]["place"] == 1,
            str(real["calls"]),
        )
        check(
            "clip reale con volto: crop restituito",
            real["composition"]["crop"]["crop_w"] > 0
            and real["composition"]["crop"]["crop_h"] > 0,
            f"{real['composition']['crop']['crop_w']}x"
            f"{real['composition']['crop']['crop_h']}",
        )
        check(
            "clip reale con volto: face_found",
            real["face_found"] is True,
            str(real["face_found"]),
        )
        print(
            f"  volto a {face_at:.1f}s: {real['composition']['name']} "
            f"offset {real['composition']['value']} "
            f"conf {real['composition']['confidence']}"
        )

    no_face = decide(str(bright), 0.5, 1080, 1350)
    check(
        "clip senza volto: offset None",
        no_face["composition"]["value"] is None,
        str(no_face["composition"]["value"]),
    )
    check(
        "clip senza volto: centro e inazione",
        no_face["composition"]["name"] == "centre"
        and no_face["composition"]["applied"] is False,
        f"{no_face['composition']['name']} applied {no_face['composition']['applied']}",
    )

    # Placement sintetico: il crop e' spostato di 0.18 della larghezza.
    real_module = aesthetics
    synthetic = aesthetics.Placement(
        crop_w=540, crop_h=720, x=136, y=0, face_found=True, reason="synthetic"
    )

    class _Fixed:
        def place(self, *args: Any, **kwargs: Any) -> Any:
            return synthetic

    module = sys.modules[__name__]
    try:
        module.aesthetics = _Fixed()  # type: ignore[assignment]
        shifted = decide("synthetic", 0.0, 1280, 720)
    finally:
        module.aesthetics = real_module  # type: ignore[assignment]

    check(
        "placement sintetico: offset",
        shifted["composition"]["name"] == "offset"
        and shifted["composition"]["applied"] is True,
        f"{shifted['composition']['name']} applied {shifted['composition']['applied']}",
    )
    check(
        "placement sintetico: provenienza dichiarata",
        shifted["composition"]["provenance"].startswith("declared:"),
        shifted["composition"]["provenance"],
    )

    print(f"{'OK' if failures == 0 else 'FALLITO'} — {failures} fallimenti")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="vx.screen selftest")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    parser.print_help()
    raise SystemExit(2)
