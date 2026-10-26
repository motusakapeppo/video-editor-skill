"""vx.gradeplan — il nodo cromatico scelto dalla luma misurata, non dal gusto.

Perche' esiste: ``grade.py`` sa eseguire un nodo, ma non sa quale nodo serva. La
decisione era implicita nel codice del renderer (un ``if`` sulla luma) e quindi non
lasciava traccia. Qui la decisione e' un oggetto: porta il driver, il valore, la
soglia con la sua provenienza, la confidenza e la frase di inazione.

Le soglie non sono inventate: ``p33`` e ``p67`` vengono da
``registry.THRESHOLDS["grade"]``, cioe' dal corpus misurato. L'unico numero
dichiarato e' ``0.08`` sul cast: e' il p95 del cast sul corpus, non una misura
nuova. Ogni numero porta la sua provenienza.

Regola dell'incertezza: se la confidenza cade in ``[0.2, 0.8]`` il nodo e'
``{"type": "none"}`` e ``applied`` e' ``False``. La confidenza e' la distanza
della misura che ha deciso dalla sua soglia, normalizzata su ``0.12``: una misura
a meta' strada fra due soglie non e' una decisione, e' un dubbio.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import grade
from .registry import THRESHOLDS

# 0.08 e' dichiarato: p95 del cast sul corpus, non una soglia reinventata qui.
CAST_THRESHOLD = 0.08
CAST_PROVENANCE = "declared:p95_cast"

# Le formule delle curve sono la politica, non un numero nudo: alza le ombre e
# abbassa le alte luci con punti dichiarati, riproducibili e verificabili.
LIFT_SHADOWS: tuple[tuple[float, float], ...] = (
    (0.0, 0.03),
    (0.25, 0.30),
    (0.75, 0.78),
    (1.0, 1.0),
)
PULL_HIGHLIGHTS: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (0.25, 0.22),
    (0.75, 0.70),
    (1.0, 0.97),
)

CONFIDENCE_SPAN = 0.12
UNCERTAIN_LOW = 0.2
UNCERTAIN_HIGH = 0.8


def _thresholds() -> Dict[str, Any]:
    entry = THRESHOLDS.get("grade") or {}
    return {
        "p33": entry.get("p33"),
        "p67": entry.get("p67"),
        "provenance": entry.get("provenance") or "unmeasured",
    }


def _scale_curve(gain: float) -> List[List[float]]:
    """Curva identita' scalata: il canale si avvicina o si allontana dal nero.

    ``gain < 1`` abbassa il canale (il cast positivo si neutralizza togliendo),
    ``gain > 1`` lo alza (il cast negativo si neutralizza aggiungendo). Il punto
    a 1.0 resta 1.0 quando si alza, cosi' il bianco non viene clippato.
    """
    gain = max(0.5, min(1.5, float(gain)))
    top = min(1.0, gain)
    return [
        [0.0, 0.0],
        [0.25, 0.25 * gain],
        [0.5, 0.5 * gain],
        [0.75, 0.75 * gain],
        [1.0, top],
    ]


def _none(
    driver: str,
    value: Optional[float],
    provenance: str,
    inaction: str,
    confidence: float,
    applied: bool,
) -> Dict[str, Any]:
    return {
        "node": {"type": "none"},
        "driver": driver,
        "value": value,
        "provenance": provenance,
        "inaction": inaction,
        "confidence": round(float(confidence), 4),
        "applied": bool(applied),
    }


def decide(
    measurements: Dict[str, Any], palette: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Scegli il nodo cromatico dalla luma e dal cast misurati.

    ``palette`` e' accettato per il contratto di W2: quando la palette e' gia'
    monocroma il grade non deve desaturare una seconda volta. Il nodo curve qui
    ha sempre ``desaturate=False``, quindi la palette non cambia il risultato;
    il parametro resta perche' la firma e' quella del verdetto.
    """
    del palette  # la palette non cambia il nodo: il grade non desatura mai qui
    th = _thresholds()
    p33, p67, provenance = th["p33"], th["p67"], th["provenance"]
    luma = measurements.get("luma_p50")
    cast_rg = float(measurements.get("cast_rg") or 0.0)
    cast_by = float(measurements.get("cast_by") or 0.0)

    if luma is None or p33 is None or p67 is None:
        return _none(
            "luma_p50",
            None,
            provenance,
            "luma_p50 non misurata: nessun nodo cromatico applicato.",
            0.0,
            False,
        )

    luma = float(luma)
    p33, p67 = float(p33), float(p67)
    nearest = min((p33, p67), key=lambda threshold: abs(luma - threshold))
    luma_confidence = min(1.0, abs(luma - nearest) / CONFIDENCE_SPAN)

    master: Optional[List[List[float]]] = None
    if luma < p33:
        master = [list(point) for point in LIFT_SHADOWS]
    elif luma > p67:
        master = [list(point) for point in PULL_HIGHLIGHTS]

    channels: Dict[str, List[List[float]]] = {}
    cast_confidence = 0.0
    if abs(cast_rg) > CAST_THRESHOLD:
        cast_confidence = max(
            cast_confidence,
            min(1.0, (abs(cast_rg) - CAST_THRESHOLD) / CONFIDENCE_SPAN),
        )
        if cast_rg > 0:
            channels["r"] = _scale_curve(1.0 - cast_rg)
        else:
            channels["g"] = _scale_curve(1.0 - abs(cast_rg))
    if abs(cast_by) > CAST_THRESHOLD:
        cast_confidence = max(
            cast_confidence,
            min(1.0, (abs(cast_by) - CAST_THRESHOLD) / CONFIDENCE_SPAN),
        )
        if cast_by > 0:
            channels["b"] = _scale_curve(1.0 - cast_by)
        else:
            channels["b"] = _scale_curve(1.0 + abs(cast_by))

    # La confidenza e' quella della misura che ha deciso, non sempre della luma:
    # un cast a 0.20 e' lontano dalla sua soglia anche quando la luma e' a meta'.
    if master is not None:
        driver, value, confidence = "luma_p50", luma, luma_confidence
    elif channels:
        if abs(cast_rg) >= abs(cast_by):
            driver, value = "cast_rg", cast_rg
        else:
            driver, value = "cast_by", cast_by
        confidence = cast_confidence
    else:
        driver, value, confidence = "luma_p50", luma, luma_confidence

    uncertain = UNCERTAIN_LOW <= confidence <= UNCERTAIN_HIGH
    if uncertain:
        return _none(
            driver,
            value,
            provenance,
            (
                f"confidenza {confidence:.3f} in [{UNCERTAIN_LOW}, {UNCERTAIN_HIGH}]: "
                "inazione, nessun nodo cromatico."
            ),
            confidence,
            False,
        )

    if master is None and not channels:
        return _none(
            driver,
            value,
            provenance,
            (
                f"luma_p50 {luma:.4f} fra p33 {p33:.4f} e p67 {p67:.4f}: "
                "nessuna correzione."
            ),
            confidence,
            True,
        )

    node: Dict[str, Any] = {
        "type": "curve",
        "master": master or [],
        "channels": channels,
        "desaturate": False,
        "grain": 0,
        "vignette": 0.0,
    }
    bits: List[str] = []
    if master is not None:
        bits.append("alza le ombre" if luma < p33 else "abbassa le alte luci")
    if channels:
        bits.append("neutralizza il cast su " + ", ".join(sorted(channels)))
    return {
        "node": node,
        "driver": driver,
        "value": value,
        "provenance": provenance,
        "inaction": f"luma_p50 {luma:.4f}: " + "; ".join(bits) + ".",
        "confidence": round(float(confidence), 4),
        "applied": True,
    }


def chain_for_shot(node: Dict[str, Any]) -> str:
    """Il frammento ffmpeg del nodo, pronto per essere giuntato con una virgola.

    ``none`` restituisce la stringa vuota: chi lo inserisce in un grafo non deve
    aggiungere un filtro che non esiste. Il frammento non ha virgole ai bordi,
    quindi ``a + "," + chain_for_shot(node)`` resta un grafo valido.
    """
    parsed = grade.parse(node)
    if parsed.is_noop():
        return ""
    return grade.ffmpeg_filter(parsed).strip(",")


def _selftest() -> int:
    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:44} {detail}")

    th = _thresholds()
    print("soglie del grade, con provenienza:")
    print(f"  p33  = {th['p33']}  ({th['provenance']})")
    print(f"  p67  = {th['p67']}  ({th['provenance']})")
    print(f"  cast = {CAST_THRESHOLD}  ({CAST_PROVENANCE})")

    dark = decide({"luma_p50": 0.10})
    check(
        "scuro -> alza le ombre",
        dark["node"]["type"] == "curve" and bool(dark["node"]["master"]),
        str(dark["node"].get("master")),
    )
    check("scuro -> applied", dark["applied"] is True, f"conf {dark['confidence']}")

    bright = decide({"luma_p50": 0.90})
    check(
        "chiaro -> abbassa le alte luci",
        bright["node"]["type"] == "curve" and bool(bright["node"]["master"]),
        str(bright["node"].get("master")),
    )
    check(
        "chiaro -> applied", bright["applied"] is True, f"conf {bright['confidence']}"
    )

    mid = decide({"luma_p50": 0.40})
    check("medio -> none", mid["node"] == {"type": "none"}, str(mid["node"]))

    uncertain = decide({"luma_p50": 0.35})
    check(
        "incerto -> none", uncertain["node"] == {"type": "none"}, str(uncertain["node"])
    )
    check(
        "incerto -> applied False",
        uncertain["applied"] is False,
        f"conf {uncertain['confidence']}",
    )

    missing = decide({"luma_p50": None})
    check(
        "luma None -> inazione",
        missing["applied"] is False and missing["node"] == {"type": "none"},
        missing["inaction"],
    )

    cast = decide({"luma_p50": 0.10, "cast_rg": 0.20, "cast_by": -0.15})
    check(
        "cast -> neutralizza r e b",
        cast["node"]["type"] == "curve" and set(cast["node"]["channels"]) == {"r", "b"},
        str(sorted(cast["node"].get("channels", {}))),
    )

    fragment = chain_for_shot(dark["node"])
    check("chain non vuota", bool(fragment), fragment[:48])
    check(
        "chain senza virgole ai bordi",
        not fragment.startswith(",") and not fragment.endswith(","),
        f"{fragment[:1]}...{fragment[-1:]}",
    )
    check(
        "chain == grade.ffmpeg_filter",
        fragment == grade.ffmpeg_filter(grade.parse(dark["node"])),
        "",
    )
    check("chain none vuota", chain_for_shot({"type": "none"}) == "", "")

    print(f"{'OK' if failures == 0 else 'FALLITO'} — {failures} fallimenti")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="vx.gradeplan selftest")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    parser.print_help()
    raise SystemExit(2)
