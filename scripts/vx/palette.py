"""vx.palette — la palette nasce dalla massa hue misurata, non dal gusto.

Perche' esiste: ``aesthetics.DUOTONE`` e' una palette fissa, scelta a mano e
verificata a mano. Va bene per un progetto, non per nove clip diverse. Qui la
famiglia cromatica e i suoi colori sono DERIVATI dalla misura ``hue_mass_top3``
prodotta da ``tests/measure_corpus.py``: gli stessi bin HSV, la stessa maschera
cromatica (S >= 40, V >= 40), nessuna definizione reinventata.

Le soglie non sono inventate: ``p33`` e ``p67`` vengono da
``registry.THRESHOLDS["palette"]``, cioe' dal corpus misurato, e ogni numero
porta la sua provenienza. L'unica cosa dichiarata qui e' la POLITICA di
saturazione e valore (S/V): l'hue non e' mai dichiarato, viene sempre dalla
misura. Un colore senza hue misurato non esiste in questo modulo.

Regola dell'incertezza: se la confidenza cade in ``[0.2, 0.8]`` la famiglia e'
``neutral`` e ``applied`` e' ``False``. La confidenza e' la distanza della massa
dominante dalla soglia piu' vicina, normalizzata su ``0.12``: una misura a meta'
strada fra due soglie non e' una decisione, e' un dubbio.

Il contrasto non si dichiara: si calcola. ``contrast_ratio`` implementa la
formula WCAG (sRGB linearizzato, ``(L1+0.05)/(L2+0.05)``). Se il testo o il deco'
non raggiungono il minimo, il loro valore V viene spostato verso l'estremo
opposto a passi di ``0.05`` finche' il rapporto passa o V tocca il bordo; il
rapporto finale e il motivo della correzione sono registrati, non nascosti.
"""

from __future__ import annotations

import colorsys
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .registry import THRESHOLDS

# --------------------------------------------------------------------------- #
# politica S/V: costanti, non colori. L'hue arriva sempre dalla misura.
# --------------------------------------------------------------------------- #
MONO_BG_S = 0.35
MONO_BG_V = 0.18
ACCENT_S = 0.85
ACCENT_V = 0.95
DECO_S = 0.55
DECO_V = 0.75
NEUTRAL_BG_S = 0.0
NEUTRAL_BG_V = 0.18
NEUTRAL_DECO_S = 0.25
NEUTRAL_DECO_V = 0.55

# Gli stessi due inchiostri neutri che ``board._contrast_ink`` usa gia': la
# palette non inventa un colore di testo, sceglie l'inchiostro che la luma
# misurata chiede. Il valore esatto e' quello del repo, non un nuovo numero.
INK_LIGHT = (250, 250, 248)
INK_DARK = (18, 18, 20)

TEXT_MIN_RATIO = 4.5
DECO_MIN_RATIO = 3.0
V_STEP = 0.05

CONFIDENCE_SPAN = 0.12
CONFIDENCE_FLOOR = 0.05
CONFIDENCE_CEIL = 0.95
UNCERTAIN_LOW = 0.2
UNCERTAIN_HIGH = 0.8

DRIVER = "hue_mass_top3"

_FIXTURE = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "corpus_stats.json"
)


# --------------------------------------------------------------------------- #
# colore: HSV -> hex e contrasto WCAG reale
# --------------------------------------------------------------------------- #
def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def to_hex(h: float, s: float, v: float) -> str:
    """HSV (h in gradi, s/v in 0..1) -> ``#rrggbb``.

    L'hue e' ridotto modulo 360: 358 e 2 sono lo stesso rosso, e un chiamante
    che somma 180 per il complementare non deve preoccuparsi del giro.
    """
    red, green, blue = colorsys.hsv_to_rgb(
        (float(h) % 360.0) / 360.0, _clamp(s), _clamp(v)
    )
    return "#{:02x}{:02x}{:02x}".format(
        int(round(red * 255.0)),
        int(round(green * 255.0)),
        int(round(blue * 255.0)),
    )


def _rgb(hex_color: str) -> Tuple[float, float, float]:
    text = str(hex_color).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"colore non esadecimale: {hex_color!r}")
    return tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    red, green, blue = _rgb(hex_color)
    return 0.2126 * _linear(red) + 0.7152 * _linear(green) + 0.0722 * _linear(blue)


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """Rapporto WCAG fra due colori: 21.0 fra bianco e nero, 1.0 fra uguali."""
    first, second = _luminance(hex_a), _luminance(hex_b)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


def _hsv_of(hex_color: str) -> Tuple[float, float, float]:
    red, green, blue = _rgb(hex_color)
    hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
    return hue * 360.0, saturation, value


def _correct(
    h: float, s: float, v: float, bg_hex: str, minimum: float
) -> Tuple[str, float, str]:
    """Sposta V verso l'estremo opposto al fondo finche' il contrasto passa.

    Ritorna ``(hex, ratio, status)`` con ``status`` in ``{"passed", "bound"}``.
    ``bound`` significa che V ha toccato 0 o 1 senza raggiungere il minimo: e'
    un fatto registrato, non un successo mascherato.
    """
    color = to_hex(h, s, v)
    ratio = contrast_ratio(color, bg_hex)
    if ratio >= minimum:
        return color, ratio, "passed"

    bg_value = _hsv_of(bg_hex)[2]
    step = V_STEP if bg_value < 0.5 else -V_STEP
    current = _clamp(v)
    while True:
        nxt = _clamp(current + step)
        if nxt == current:
            return color, ratio, "bound"
        current = nxt
        color = to_hex(h, s, current)
        ratio = contrast_ratio(color, bg_hex)
        if ratio >= minimum:
            return color, ratio, "passed"
        if current <= 0.0 or current >= 1.0:
            return color, ratio, "bound"


# --------------------------------------------------------------------------- #
# misura -> famiglia
# --------------------------------------------------------------------------- #
def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _thresholds() -> Dict[str, Any]:
    entry = THRESHOLDS.get("palette") or {}
    provenance = str(entry.get("provenance") or "unmeasured")
    if not provenance.startswith("corpus_hash:"):
        provenance = "corpus_hash:unmeasured"
    return {
        "p33": entry.get("p33"),
        "p67": entry.get("p67"),
        "provenance": provenance,
    }


def _pairs(measurements: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Le tre coppie hue/mass, o ``None`` quando la misura non esiste.

    Una lista di tre coppie con massa zero NON e' una misura mancante: e' la
    misura di una clip acromatica, e va trattata come tale. Solo l'assenza
    strutturale (chiave mancante, lista vuota, voci non numeriche) e' ``None``.
    """
    raw = measurements.get("hue_mass_top3")
    if not isinstance(raw, list) or not raw:
        return None
    pairs: List[Dict[str, Any]] = []
    for item in raw[:3]:
        if not isinstance(item, dict):
            continue
        hue, mass = item.get("hue"), item.get("mass")
        if not _is_number(hue) or not _is_number(mass):
            continue
        pairs.append(
            {
                "hue": int(round(float(hue))) % 360,
                "mass": _clamp(float(mass)),
            }
        )
    if not pairs:
        return None
    while len(pairs) < 3:
        pairs.append({"hue": 0, "mass": 0.0})
    return pairs


def _family(mass: float, p33: float, p67: float) -> str:
    if mass >= p67:
        return "mono"
    if mass >= p33:
        return "dual"
    return "neutral"


def _confidence(mass: float, p33: float, p67: float) -> float:
    nearest = min((p33, p67), key=lambda threshold: abs(mass - threshold))
    raw = abs(mass - nearest) / CONFIDENCE_SPAN
    return _clamp(raw, CONFIDENCE_FLOOR, CONFIDENCE_CEIL)


def _ink(luma: Optional[float]) -> Tuple[float, float, float]:
    """L'inchiostro neutro che la luma chiede, come tripletta HSV."""
    value = 0.5 if luma is None else float(luma)
    rgb = INK_LIGHT if value < 0.5 else INK_DARK
    hue, saturation, brightness = colorsys.rgb_to_hsv(
        rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
    )
    return hue * 360.0, saturation, brightness


def _build(
    family: str,
    hues: Tuple[int, int, int],
    luma: Optional[float],
    *,
    measured: bool = True,
) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """Costruisce i quattro colori dalla famiglia e dagli hue misurati.

    ``measured=False`` e' il caso in cui nessun hue e' stato misurato: la palette
    resta acromatica (S=0) invece di inventare un hue. Un colore senza misura non
    esiste in questo modulo.
    """
    h0, h1, _h2 = hues
    if not measured:
        bg = to_hex(0, NEUTRAL_BG_S, NEUTRAL_BG_V)
        accent = to_hex(0, 0.0, ACCENT_V)
        deco_hue, deco_s, deco_v = 0, 0.0, NEUTRAL_DECO_V
    elif family == "mono":
        bg = to_hex(h0, MONO_BG_S, MONO_BG_V)
        accent = to_hex(h0, ACCENT_S, ACCENT_V)
        deco_hue, deco_s, deco_v = (h0 + 180) % 360, DECO_S, DECO_V
    elif family == "dual":
        bg = to_hex(h0, MONO_BG_S, MONO_BG_V)
        accent = to_hex(h0, ACCENT_S, ACCENT_V)
        deco_hue, deco_s, deco_v = h1, DECO_S, DECO_V
    else:
        bg = to_hex(h0, NEUTRAL_BG_S, NEUTRAL_BG_V)
        accent = to_hex(h0, ACCENT_S, ACCENT_V)
        deco_hue, deco_s, deco_v = h0, NEUTRAL_DECO_S, NEUTRAL_DECO_V

    text_h, text_s, text_v = _ink(luma)
    text, text_ratio, text_status = _correct(text_h, text_s, text_v, bg, TEXT_MIN_RATIO)
    deco, deco_ratio, deco_status = _correct(
        deco_hue, deco_s, deco_v, bg, DECO_MIN_RATIO
    )

    colors = {"bg": bg, "accent": accent, "text": text, "deco": deco}
    contrast = {
        "text_vs_bg": round(text_ratio, 4),
        "deco_vs_bg": round(deco_ratio, 4),
        "text_correction": text_status,
        "deco_correction": deco_status,
    }
    return colors, contrast


def _sentence(
    family: str,
    mass: float,
    p33: float,
    p67: float,
    contrast: Dict[str, Any],
    applied: bool,
) -> str:
    if not applied:
        return (
            f"massa dominante {mass:.4f} a meta' strada fra p33 {p33:.4f} e "
            f"p67 {p67:.4f}: inazione, famiglia neutra."
        )
    bits = [f"massa dominante {mass:.4f} -> famiglia {family}"]
    if contrast["text_correction"] == "bound":
        bits.append("testo fermato al bordo V senza raggiungere 4.5:1")
    if contrast["deco_correction"] == "bound":
        bits.append("deco' fermato al bordo V senza raggiungere 3.0:1")
    return "; ".join(bits) + "."


def decide(measurements: Dict[str, Any]) -> Dict[str, Any]:
    """Scegli famiglia e colori dalla massa hue misurata.

    ``measurements`` e' il blocco ``measurements`` del contratto
    ``vx.visual/1.0``: qui servono ``hue_mass_top3`` e ``luma_p50``. Il resto
    non entra nella decisione cromatica.
    """
    thresholds = _thresholds()
    p33, p67 = thresholds["p33"], thresholds["p67"]
    provenance = thresholds["provenance"]
    luma = measurements.get("luma_p50")
    luma = float(luma) if _is_number(luma) else None

    pairs = _pairs(measurements)
    if pairs is None or p33 is None or p67 is None:
        hues = (0, 0, 0)
        colors, contrast = _build("neutral", hues, luma, measured=False)
        reason = (
            "hue_mass_top3 non misurata: palette neutra, nessuna scelta cromatica."
            if pairs is None
            else "soglie palette non misurate: palette neutra, nessuna scelta."
        )
        return {
            "family": "neutral",
            "colors": colors,
            "hues": {"h0": 0, "h1": 0, "h2": 0},
            "contrast": contrast,
            "driver": DRIVER,
            "value": None,
            "provenance": provenance,
            "inaction": True,
            "inaction_reason": reason,
            "confidence": 0.5,
            "applied": False,
            "note": "masses 0.0000/0.0000/0.0000",
        }

    p33, p67 = float(p33), float(p67)
    hues = (pairs[0]["hue"], pairs[1]["hue"], pairs[2]["hue"])
    mass = pairs[0]["mass"]
    family = _family(mass, p33, p67)
    confidence = _confidence(mass, p33, p67)

    uncertain = UNCERTAIN_LOW <= confidence <= UNCERTAIN_HIGH
    applied = not uncertain
    if uncertain:
        family = "neutral"

    colors, contrast = _build(family, hues, luma)
    reason = _sentence(family, mass, p33, p67, contrast, applied)
    return {
        "family": family,
        "colors": colors,
        "hues": {"h0": hues[0], "h1": hues[1], "h2": hues[2]},
        "contrast": contrast,
        "driver": DRIVER,
        "value": round(mass, 4),
        "provenance": provenance,
        "inaction": not applied,
        "inaction_reason": reason,
        "confidence": round(confidence, 4),
        "applied": applied,
        "note": "masses {:.4f}/{:.4f}/{:.4f}".format(
            pairs[0]["mass"], pairs[1]["mass"], pairs[2]["mass"]
        ),
    }


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #
def _hue_distance(first: float, second: float) -> float:
    delta = abs((float(first) - float(second)) % 360.0)
    return min(delta, 360.0 - delta)


def _load_clip(name: str) -> Dict[str, Any]:
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    for clip in data.get("clips") or []:
        if clip.get("name") == name:
            return clip
    raise KeyError(name)


def _selftest() -> int:
    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:44} {detail}")

    thresholds = _thresholds()
    print("soglie della palette, con provenienza:")
    print(f"  p33 = {thresholds['p33']}  ({thresholds['provenance']})")
    print(f"  p67 = {thresholds['p67']}  ({thresholds['provenance']})")

    check("to_hex bianco", to_hex(0, 0, 1) == "#ffffff", to_hex(0, 0, 1))
    check("to_hex nero", to_hex(0, 0, 0) == "#000000", to_hex(0, 0, 0))
    check(
        "contrasto bianco/nero = 21.0",
        abs(contrast_ratio("#ffffff", "#000000") - 21.0) < 1e-9,
        f"{contrast_ratio('#ffffff', '#000000'):.6f}",
    )
    check(
        "contrasto identita' = 1.0",
        abs(contrast_ratio("#123456", "#123456") - 1.0) < 1e-9,
        f"{contrast_ratio('#123456', '#123456'):.6f}",
    )

    bright = decide(_load_clip("bright_45"))
    dark = decide(_load_clip("dark_916"))
    print(
        "bright_45: "
        f"{bright['family']} accent {bright['colors']['accent']} "
        f"h0 {bright['hues']['h0']} conf {bright['confidence']}"
    )
    print(
        "dark_916:  "
        f"{dark['family']} accent {dark['colors']['accent']} "
        f"h0 {dark['hues']['h0']} conf {dark['confidence']}"
    )
    distance = _hue_distance(bright["hues"]["h0"], dark["hues"]["h0"])
    check(
        "accent bright vs dark > 30 gradi",
        distance > 30.0,
        f"{distance:.1f} gradi",
    )

    for label, result in (("bright_45", bright), ("dark_916", dark)):
        text_ok = (
            result["contrast"]["text_vs_bg"] >= TEXT_MIN_RATIO
            or result["contrast"]["text_correction"] == "bound"
        )
        deco_ok = (
            result["contrast"]["deco_vs_bg"] >= DECO_MIN_RATIO
            or result["contrast"]["deco_correction"] == "bound"
        )
        check(
            f"{label}: testo >= 4.5 o bordo V",
            text_ok,
            f"{result['contrast']['text_vs_bg']:.2f}:1 "
            f"({result['contrast']['text_correction']})",
        )
        check(
            f"{label}: deco' >= 3.0 o bordo V",
            deco_ok,
            f"{result['contrast']['deco_vs_bg']:.2f}:1 "
            f"({result['contrast']['deco_correction']})",
        )
        check(
            f"{label}: provenienza corpus_hash",
            result["provenance"].startswith("corpus_hash:"),
            result["provenance"][:24],
        )

    empty = decide({})
    check(
        "misura vuota -> neutra e inazione",
        empty["family"] == "neutral"
        and empty["applied"] is False
        and empty["confidence"] == 0.5,
        f"{empty['family']} conf {empty['confidence']}",
    )
    check(
        "inaction e' un booleano",
        isinstance(empty["inaction"], bool) and empty["inaction"] is True,
        str(empty["inaction"]),
    )

    check(
        "deterministico",
        decide(_load_clip("bright_45")) == bright,
        "stesso input, stesso output",
    )

    print(f"{'OK' if failures == 0 else 'FALLITO'} — {failures} fallimenti")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="vx.palette selftest")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    parser.print_help()
    raise SystemExit(2)
