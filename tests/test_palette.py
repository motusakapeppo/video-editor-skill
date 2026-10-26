"""Test della palette: la famiglia e i colori vengono dalla massa hue misurata.

Le asserzioni sono sul contratto: famiglia, hue, contrasto reale, provenienza,
confidenza, inazione. Nessun colore atteso e' scritto a mano: si verifica che
l'hue dell'accento sia quello misurato e che il contrasto sia quello calcolato.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import palette, registry  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "corpus_stats.json"

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:46} {detail}")


def hue_distance(first: float, second: float) -> float:
    delta = abs((float(first) - float(second)) % 360.0)
    return min(delta, 360.0 - delta)


def clip(name: str) -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for item in data["clips"]:
        if item["name"] == name:
            return item
    raise KeyError(name)


def is_hex(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        return False
    try:
        int(value[1:], 16)
    except ValueError:
        return False
    return True


def main() -> int:
    thresholds = registry.THRESHOLDS["palette"]
    print(
        f"soglie palette: p33={thresholds['p33']} p67={thresholds['p67']} "
        f"({thresholds['provenance']})"
    )

    print("\nbright_45 vs dark_916: hue dell'accento")
    bright = palette.decide(clip("bright_45"))
    dark = palette.decide(clip("dark_916"))
    distance = hue_distance(bright["hues"]["h0"], dark["hues"]["h0"])
    check(
        "distanza circolare > 30 gradi",
        distance > 30.0,
        f"{distance:.1f} gradi",
    )
    check(
        "accento bright = hue misurato",
        hue_distance(
            palette._hsv_of(bright["colors"]["accent"])[0], bright["hues"]["h0"]
        )
        <= 2.0,
        f"{bright['colors']['accent']} h0 {bright['hues']['h0']}",
    )
    check(
        "accento dark = hue misurato",
        hue_distance(palette._hsv_of(dark["colors"]["accent"])[0], dark["hues"]["h0"])
        <= 2.0,
        f"{dark['colors']['accent']} h0 {dark['hues']['h0']}",
    )
    check(
        "dark e' mono (massa >= p67)",
        dark["family"] == "mono",
        f"{dark['family']} massa {dark['value']}",
    )
    check(
        "bright e' neutral (massa < p33)",
        bright["family"] == "neutral",
        f"{bright['family']} massa {bright['value']}",
    )

    print("\ncontrasto reale, non dichiarato")
    for label, result in (("bright_45", bright), ("dark_916", dark)):
        text_ok = (
            result["contrast"]["text_vs_bg"] >= palette.TEXT_MIN_RATIO
            or result["contrast"]["text_correction"] == "bound"
        )
        deco_ok = (
            result["contrast"]["deco_vs_bg"] >= palette.DECO_MIN_RATIO
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
            f"{label}: rapporto ricalcolato coincide",
            abs(
                palette.contrast_ratio(result["colors"]["text"], result["colors"]["bg"])
                - result["contrast"]["text_vs_bg"]
            )
            < 1e-3,
            f"{palette.contrast_ratio(result['colors']['text'], result['colors']['bg']):.4f}",
        )

    print("\ncolori e provenienza")
    for label, result in (("bright_45", bright), ("dark_916", dark)):
        check(
            f"{label}: quattro colori esadecimali",
            all(
                is_hex(result["colors"][key])
                for key in ("bg", "accent", "text", "deco")
            ),
            str(result["colors"]),
        )
        check(
            f"{label}: provenienza corpus_hash",
            result["provenance"].startswith("corpus_hash:"),
            result["provenance"][:24],
        )
        check(
            f"{label}: driver hue_mass_top3",
            result["driver"] == "hue_mass_top3",
            result["driver"],
        )
        check(
            f"{label}: inaction opposto di applied",
            isinstance(result["inaction"], bool)
            and result["inaction"] == (not result["applied"]),
            f"inaction {result['inaction']} applied {result['applied']}",
        )

    print("\nmisura vuota: palette neutra e inazione")
    for label, measurements in (
        ("dict vuoto", {}),
        ("lista vuota", {"hue_mass_top3": []}),
        ("voci non numeriche", {"hue_mass_top3": [{"hue": "x", "mass": None}]}),
    ):
        empty = palette.decide(measurements)
        check(
            f"{label}: neutral, applied False, conf 0.5",
            empty["family"] == "neutral"
            and empty["applied"] is False
            and empty["confidence"] == 0.5,
            f"{empty['family']} conf {empty['confidence']}",
        )
        check(
            f"{label}: inaction booleano",
            isinstance(empty["inaction"], bool) and empty["inaction"] is True,
            str(empty["inaction"]),
        )

    print("\nconfidenza nella banda di incertezza: inazione")
    p33 = float(thresholds["p33"])
    band = palette.decide(
        {
            "hue_mass_top3": [
                {"hue": 200, "mass": p33 + 0.03},
                {"hue": 20, "mass": 0.10},
                {"hue": 120, "mass": 0.05},
            ],
            "luma_p50": 0.40,
        }
    )
    check(
        "confidenza in [0.2, 0.8]",
        0.2 <= band["confidence"] <= 0.8,
        str(band["confidence"]),
    )
    check(
        "applied False e famiglia neutral",
        band["applied"] is False and band["family"] == "neutral",
        f"{band['family']} applied {band['applied']}",
    )
    check(
        "confidenza conservata",
        band["confidence"] == round(abs(p33 + 0.03 - p33) / 0.12, 4),
        str(band["confidence"]),
    )

    print("\ndeterminismo")
    again = palette.decide(clip("bright_45"))
    check("stesso input, stesso output", again == bright, "confronto dict")

    print("\nhelper pubblici")
    check(
        "to_hex bianco", palette.to_hex(0, 0, 1) == "#ffffff", palette.to_hex(0, 0, 1)
    )
    check("to_hex nero", palette.to_hex(0, 0, 0) == "#000000", palette.to_hex(0, 0, 0))
    check(
        "contrasto bianco/nero = 21.0",
        abs(palette.contrast_ratio("#ffffff", "#000000") - 21.0) < 1e-9,
        f"{palette.contrast_ratio('#ffffff', '#000000'):.6f}",
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
