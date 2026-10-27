"""vx.fontchooser: the family comes from the binary, the choice from measurement.

No network. Four synthetic TTF files are built with fontTools when it is present;
without fontTools the two real Antonio files are used, one renamed so that the
filename contradicts the name table. Run: python tests/test_fontchooser.py
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import contract, fontchooser, fontlib, settings  # noqa: E402

try:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    HAVE_FONTTOOLS = True
except ImportError:  # the fallback below uses the real Antonio files
    FontBuilder = None
    TTGlyphPen = None
    HAVE_FONTTOOLS = False

PASS = 0
FAIL = 0
SKIP = 0

COMMERCIAL_TERMS = "Free for commercial use. Terms: https://example.test/terms\n"
PERSONAL_TERMS = "Free for personal use only.\n"


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:70} {detail}")


def skip(label: str, detail: str = "") -> None:
    global SKIP
    SKIP += 1
    print(f"{'SKIP':4} {label:70} {detail}")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _rect(pen: Any, x0: int, y0: int, x1: int, y1: int) -> None:
    pen.moveTo((x0, y0))
    pen.lineTo((x0, y1))
    pen.lineTo((x1, y1))
    pen.lineTo((x1, y0))
    pen.closePath()


def _glyph_h(stem: int, advance: int, cap: int = 700) -> Any:
    pen = TTGlyphPen(None)
    _rect(pen, 40, 0, 40 + stem, cap)
    _rect(pen, advance - 40 - stem, 0, advance - 40, cap)
    _rect(pen, 40, cap // 2 - stem // 2, advance - 40, cap // 2 + stem // 2)
    return pen.glyph()


def _glyph_n(stem: int, advance: int, x_height: int = 500) -> Any:
    pen = TTGlyphPen(None)
    _rect(pen, 40, 0, 40 + stem, x_height)
    _rect(pen, advance - 40 - stem, 0, advance - 40, x_height)
    _rect(pen, 40, x_height - stem, advance - 40, x_height)
    return pen.glyph()


def _build_font(path: Path, family: str, style: str, stem: int, advance: int) -> None:
    """A minimal valid TTF: .notdef, space, H, n; real contours, real name table."""
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "space", "H", "n"])
    builder.setupCharacterMap({32: "space", 72: "H", 110: "n"})
    builder.setupGlyf(
        {
            ".notdef": TTGlyphPen(None).glyph(),
            "space": TTGlyphPen(None).glyph(),
            "H": _glyph_h(stem, advance),
            "n": _glyph_n(stem, advance),
        }
    )
    builder.setupHorizontalMetrics(
        {
            ".notdef": (advance, 0),
            "space": (advance, 0),
            "H": (advance, 0),
            "n": (advance, 0),
        }
    )
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": family, "styleName": style})
    builder.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
        sxHeight=500,
        sCapHeight=700,
    )
    builder.setupPost()
    builder.save(str(path))


def _sidecar(path: Path, text: str) -> None:
    path.with_name(path.stem + ".licence.txt").write_text(text, encoding="utf-8")


def _make_synthetic(fonts: Path) -> Dict[str, Any]:
    specs = (
        # filename, family (name table), style, stem units, advance units, terms
        (
            "GridGrotesk-Regular.ttf",
            "Grid Grotesk",
            "Regular",
            80,
            520,
            COMMERCIAL_TERMS,
        ),
        # The filename says Condensed Black, the name table says Grid Narrow
        # Regular: inventory must believe the binary.
        (
            "Lying-Name-Condensed-Black.ttf",
            "Grid Narrow",
            "Regular",
            80,
            360,
            COMMERCIAL_TERMS,
        ),
        ("GridGrotesk-Bold.ttf", "Grid Grotesk", "Bold", 130, 520, COMMERCIAL_TERMS),
        ("Solo-Display.ttf", "Solo Display", "Black", 210, 720, PERSONAL_TERMS),
        ("Mystery-Sans.ttf", "Mystery Sans", "Regular", 90, 540, ""),
    )
    for filename, family, style, stem, advance, terms in specs:
        path = fonts / filename
        _build_font(path, family, style, stem, advance)
        if terms:
            _sidecar(path, terms)
    (fonts / "notes.txt").write_text("not a font", encoding="utf-8")
    return {
        "fonts": fonts,
        "synthetic": True,
        "regular": "GridGrotesk-Regular.ttf",
        "bold": "GridGrotesk-Bold.ttf",
        "condensed": "Lying-Name-Condensed-Black.ttf",
        "extended": "Solo-Display.ttf",
        "personal": "Solo-Display.ttf",
        "unknown": "Mystery-Sans.ttf",
        "expected_family": "Grid Narrow",
        "expected_style": "Regular",
    }


def _make_fallback(fonts: Path) -> Dict[str, Any]:
    assets = ROOT / "assets" / "fonts"
    regular = fonts / "Antonio-Regular.ttf"
    shutil.copyfile(assets / "Antonio-Regular.ttf", regular)
    _sidecar(regular, COMMERCIAL_TERMS)
    bold = fonts / "Lying-Comic-Thin.ttf"
    shutil.copyfile(assets / "Antonio-Bold.ttf", bold)
    _sidecar(bold, COMMERCIAL_TERMS)
    personal = fonts / "Solo-Display.ttf"
    shutil.copyfile(assets / "Antonio-Bold.ttf", personal)
    _sidecar(personal, PERSONAL_TERMS)
    return {
        "fonts": fonts,
        "synthetic": False,
        "regular": "Antonio-Regular.ttf",
        "bold": "Lying-Comic-Thin.ttf",
        "condensed": None,
        "extended": None,
        "personal": "Solo-Display.ttf",
        "unknown": None,
        "expected_family": "Antonio",
        "expected_style": "Bold",
    }


def make_fixtures(root: Path) -> Dict[str, Any]:
    fonts = root / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    return _make_synthetic(fonts) if HAVE_FONTTOOLS else _make_fallback(fonts)


def by_name(
    records: List[fontlib.FontRecord], name: Optional[str]
) -> Optional[fontlib.FontRecord]:
    if not name:
        return None
    for record in records:
        if Path(record.file).name == name:
            return record
    return None


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_inventory(
    records: List[fontlib.FontRecord], meta: Dict[str, Any], root: Path
) -> None:
    print("\n-- inventory: name table, not filename")
    expected_count = 5 if meta["synthetic"] else 3
    check(
        "una record per font, il .txt e' ignorato",
        len(records) == expected_count,
        len(records),
    )
    check("tutte le famiglie sono non vuote", all(r.family for r in records))

    lying = by_name(records, meta["condensed"] if meta["synthetic"] else meta["bold"])
    check("il file rinominato e' nell'inventario", lying is not None)
    if lying is not None:
        check(
            "family dalla tabella name, non dal filename",
            lying.family == meta["expected_family"],
            lying.family,
        )
        check(
            "subfamily dalla tabella name",
            lying.styles == [meta["expected_style"]],
            lying.styles,
        )

    regular = by_name(records, meta["regular"])
    personal = by_name(records, meta["personal"])
    bold = by_name(records, meta["bold"])
    check(
        "licenza commercial dal sidecar",
        regular is not None and regular.licence == "commercial",
    )
    check(
        "licenza personal dal sidecar",
        personal is not None and personal.licence == "personal",
    )
    check(
        "sidecar commercial conserva l'URL",
        regular is not None and regular.licence_url.startswith("https://example.test"),
    )
    if meta["unknown"]:
        unknown = by_name(records, meta["unknown"])
        check(
            "senza sidecar la licenza e' unknown",
            unknown is not None and unknown.licence == "unknown",
        )
    else:
        skip("senza sidecar la licenza e' unknown", "fixture senza fontTools")

    if regular is not None:
        payload = Path(regular.file).read_bytes()
        check(
            "sha256 calcolato dal contenuto",
            regular.sha256 == hashlib.sha256(payload).hexdigest(),
        )
        check("file assoluto", Path(regular.file).is_absolute(), regular.file)
        check("source_url e' il file locale", regular.source_url.startswith("file:"))
    check(
        "stessa directory due volte non duplica",
        len(fontchooser.inventory([meta["fonts"], meta["fonts"]])) == expected_count,
    )
    check(
        "directory inesistente -> lista vuota",
        fontchooser.inventory(root / "nope") == [],
    )
    check(
        "un singolo file basta",
        len(fontchooser.inventory(Path(bold.file if bold else regular.file))) == 1,
    )

    broken_dir = root / "broken"
    broken_dir.mkdir()
    broken = broken_dir / "Fallback-Family-Bold.ttf"
    broken.write_bytes(b"not a real font")
    broken_records = fontchooser.inventory(broken_dir)
    check("tabella illeggibile: la record esiste", len(broken_records) == 1)
    if broken_records:
        check(
            "tabella illeggibile: family dal filename senza stile",
            broken_records[0].family == "Fallback Family",
            broken_records[0].family,
        )
        check(
            "tabella illeggibile: stile dal filename",
            broken_records[0].styles == ["Bold"],
        )


def test_measure(
    records: List[fontlib.FontRecord], meta: Dict[str, Any], root: Path
) -> None:
    print("\n-- measure: plausible numbers from rendered ink")
    metrics: Dict[str, Dict[str, Any]] = {}
    for record in records:
        values = fontchooser.measure(record)
        metrics[Path(record.file).name] = values
        check(
            f"{record.family}: avanzamento per em in [0.2, 1.5]",
            0.2 <= values["advance"] <= 1.5,
            round(values["advance"], 3),
        )
        check(
            f"{record.family}: asta/altezza maiuscola in [0.02, 0.30]",
            0.02 <= values["stem_ratio"] <= 0.30,
            round(values["stem_ratio"], 3),
        )
        check(
            f"{record.family}: 0 < x-height < cap height",
            0.0 < values["x_height"] < values["cap_height"],
            f"{values['x_height']:.3f} < {values['cap_height']:.3f}",
        )
        check(
            f"{record.family}: classi dichiarate",
            values["width_class"] in fontchooser.WIDTH_CLASSES
            and values["weight_class"] in fontchooser.WEIGHT_ORDER,
            f"{values['width_class']}/{values['weight_class']}",
        )

    regular = metrics.get(meta["regular"], {})
    bold = metrics.get(meta["bold"], {})
    check(
        "il bold ha l'asta piu' spessa del regular",
        regular and bold and bold["stem_ratio"] > regular["stem_ratio"],
        f"{regular.get('stem_ratio')} -> {bold.get('stem_ratio')}",
    )
    if meta["synthetic"]:
        check(
            "regular misurato regular",
            regular.get("weight_class") == "regular",
            regular.get("weight_class"),
        )
        check(
            "bold misurato bold",
            bold.get("weight_class") == "bold",
            bold.get("weight_class"),
        )
        check(
            "condensed misurato condensed",
            metrics.get(meta["condensed"], {}).get("width_class") == "condensed",
            metrics.get(meta["condensed"], {}).get("width_class"),
        )
        check(
            "normal misurato normal",
            regular.get("width_class") == "normal",
            regular.get("width_class"),
        )
        check(
            "black esteso misurato black/extended",
            metrics.get(meta["extended"], {}).get("weight_class") == "black"
            and metrics.get(meta["extended"], {}).get("width_class") == "extended",
            f"{metrics.get(meta['extended'], {}).get('weight_class')}/"
            f"{metrics.get(meta['extended'], {}).get('width_class')}",
        )
    else:
        skip("classi sintetiche condensed/extended", "fixture senza fontTools")

    missing = fontlib.FontRecord(
        family="Missing",
        licence="commercial",
        file=str(root / "missing.ttf"),
        sha256="0" * 64,
    )
    try:
        fontchooser.measure(missing)
        check("measure su file assente: VxError", False, "nessuna eccezione")
    except contract.VxError:
        check("measure su file assente: VxError", True)

    check(
        "soglie ordinate: condensed < extended",
        fontchooser.ADVANCE_CONDENSED_MAX < fontchooser.ADVANCE_EXTENDED_MIN,
    )
    check(
        "soglie ordinate: light < regular < medium < bold",
        fontchooser.STEM_LIGHT_MAX
        < fontchooser.STEM_REGULAR_MAX
        < fontchooser.STEM_MEDIUM_MAX
        < fontchooser.STEM_BOLD_MAX,
    )
    check(
        "ogni soglia ha una range dichiarata",
        set(fontchooser.THRESHOLD_RANGES) == set(fontchooser.DEFAULT_METRIC_THRESHOLDS),
        sorted(fontchooser.THRESHOLD_RANGES),
    )
    check(
        "ogni range e' ben formata e contiene il default",
        all(
            low < fontchooser.DEFAULT_METRIC_THRESHOLDS[key] <= high
            for key, (low, high) in fontchooser.THRESHOLD_RANGES.items()
        ),
    )


def test_thresholds_from_settings(
    records: List[fontlib.FontRecord], meta: Dict[str, Any]
) -> None:
    print("\n-- thresholds: live snapshot, declared range, clamp")
    bold = by_name(records, meta["bold"])
    if bold is None:
        return
    settings.reset()
    try:
        check(
            "default: il bold resta bold",
            fontchooser.measure(bold)["weight_class"] == "bold",
            fontchooser.measure(bold)["weight_class"],
        )
        settings.apply(settings.build(overrides=["thresholds.font_stem_bold_max=0.10"]))
        check(
            "override in range cambia la classe misurata",
            fontchooser.measure(bold)["weight_class"] == "black",
            fontchooser.measure(bold)["weight_class"],
        )
        try:
            settings.apply(
                settings.build(overrides=["thresholds.font_stem_light_max=0.90"])
            )
            check(
                "override fuori range viene rifiutato dal cancello",
                False,
                "nessuna eccezione",
            )
        except contract.VxError as exc:
            check(
                "override fuori range viene rifiutato dal cancello",
                "font_stem_light_max" in str(exc),
                str(exc)[:80],
            )
    finally:
        settings.reset()
    check(
        "reset riporta la soglia al default",
        fontchooser.measure(bold)["weight_class"] == "bold",
        fontchooser.measure(bold)["weight_class"],
    )


def test_rank(records: List[fontlib.FontRecord], meta: Dict[str, Any]) -> None:
    print("\n-- rank: measured classes against requirement and energy")
    ranked = fontchooser.rank(records, requirement="bold", energy=0.9)
    check("rank restituisce candidati", bool(ranked), len(ranked))
    check(
        "solo licenze commerciali con floor commercial",
        all(item["record"].licence == "commercial" for item in ranked),
        [item["record"].licence for item in ranked],
    )
    check(
        "entry con record/score/because",
        all(set(item) == {"record", "score", "because"} for item in ranked),
        sorted(ranked[0]) if ranked else [],
    )
    check(
        "bold + energia alta mette il bold primo",
        bool(ranked) and Path(ranked[0]["record"].file).name == meta["bold"],
        Path(ranked[0]["record"].file).name if ranked else None,
    )
    check(
        "punteggi non crescenti",
        all(
            ranked[index]["score"] >= ranked[index + 1]["score"]
            for index in range(len(ranked) - 1)
        ),
        [item["score"] for item in ranked],
    )
    top_because = str(ranked[0]["because"]) if ranked else ""
    check("because e' una riga sola", "\n" not in top_because)
    check(
        "because nomina la larghezza misurata", "larghezza" in top_because, top_because
    )
    check("because nomina il peso misurato", "peso" in top_because)
    check(
        "because contiene la classe di peso misurata",
        fontchooser.measure(ranked[0]["record"])["weight_class"] in top_because,
        top_because,
    )
    check(
        "because contiene la classe di larghezza misurata",
        fontchooser.measure(ranked[0]["record"])["width_class"] in top_because,
    )

    again = fontchooser.rank(records, requirement="bold", energy=0.9)
    check(
        "deterministico: stesso ordine e stesse parole",
        [item["record"].sha256 for item in ranked]
        == [item["record"].sha256 for item in again]
        and [item["because"] for item in ranked] == [item["because"] for item in again],
    )

    if meta["synthetic"]:
        condensed = fontchooser.rank(records, requirement="condensed", energy=0.3)
        check(
            "requisito condensed mette il condensed primo",
            bool(condensed)
            and Path(condensed[0]["record"].file).name == meta["condensed"],
            Path(condensed[0]["record"].file).name if condensed else None,
        )
    else:
        skip("requisito condensed", "fixture senza fontTools")

    wide_floor = fontchooser.rank(
        records, requirement="bold", energy=0.9, licence_floor="personal"
    )
    check(
        "floor personal ammette anche i personal",
        any(item["record"].licence == "personal" for item in wide_floor),
    )
    every = fontchooser.rank(
        records, requirement="bold", energy=0.9, licence_floor="unknown"
    )
    check("floor unknown ammette tutto", len(every) == len(records), len(every))

    for bad_floor in ("mit", "", None):
        try:
            fontchooser.rank(
                records, requirement="bold", energy=0.5, licence_floor=bad_floor
            )
            check(f"floor {bad_floor!r} rifiutato", False, "nessuna eccezione")
        except contract.VxError:
            check(f"floor {bad_floor!r} rifiutato", True)

    try:
        fontchooser.rank(records, requirement="bold", energy="molta")
        check("energy non numerica: VxError", False, "nessuna eccezione")
    except contract.VxError:
        check("energy non numerica: VxError", True)


def test_decide(records: List[fontlib.FontRecord], meta: Dict[str, Any]) -> None:
    print("\n-- decide: licence floor or an explicit reason")
    decision = fontchooser.decide(records, requirement="black", energy=1.0)
    check(
        "decisione con le quattro chiavi",
        set(decision) == {"chosen", "alternatives", "because", "reason_if_none"},
        sorted(decision),
    )
    chosen = decision["chosen"]
    check("sceglie qualcosa", chosen is not None)
    check(
        "non sceglie mai un personal senza permesso",
        chosen is not None and chosen.licence == "commercial",
        chosen.licence if chosen else None,
    )
    check(
        "scarta il black personal anche se il requisito lo preferirebbe",
        chosen is not None and Path(chosen.file).name != meta["personal"],
        Path(chosen.file).name if chosen else None,
    )
    check("reason_if_none vuoto quando sceglie", decision["reason_if_none"] == "")
    check(
        "because della scelta e' quello del primo in classifica",
        decision["because"]
        == fontchooser.rank(records, requirement="black", energy=1.0)[0]["because"],
    )
    check("alternatives e' una lista", isinstance(decision["alternatives"], list))

    personal_only = [by_name(records, meta["personal"])]
    only_personal: List[fontlib.FontRecord] = [
        r for r in personal_only if r is not None
    ]
    refused = fontchooser.decide(only_personal, requirement="black", energy=1.0)
    check("solo personal -> chosen None", refused["chosen"] is None, refused["chosen"])
    reason = str(refused["reason_if_none"]).lower()
    check(
        "il motivo nomina la licenza personal",
        "personal" in reason,
        refused["reason_if_none"],
    )
    check("il motivo parla di licenza", "licenz" in reason, refused["reason_if_none"])
    check("solo personal -> nessuna alternativa", refused["alternatives"] == [])

    allowed = fontchooser.decide(
        only_personal, requirement="black", energy=1.0, allow_personal=True
    )
    check(
        "allow_personal=True ammette il personal",
        allowed["chosen"] is not None and allowed["chosen"].licence == "personal",
        allowed["chosen"].licence if allowed["chosen"] else None,
    )

    empty = fontchooser.decide([], requirement="bold", energy=0.5)
    check("inventario vuoto -> chosen None", empty["chosen"] is None)
    check(
        "inventario vuoto -> motivo non vuoto",
        bool(empty["reason_if_none"]),
        empty["reason_if_none"],
    )

    if meta["unknown"]:
        unknown_only = [by_name(records, meta["unknown"])]
        unknown_records = [r for r in unknown_only if r is not None]
        unknown_decision = fontchooser.decide(
            unknown_records, requirement="bold", energy=0.5, allow_personal=True
        )
        check(
            "unknown non viene mai scelto",
            unknown_decision["chosen"] is None,
            unknown_decision["chosen"],
        )
        check(
            "il motivo nomina unknown",
            "unknown" in str(unknown_decision["reason_if_none"]).lower(),
            unknown_decision["reason_if_none"],
        )
    else:
        skip("unknown non viene mai scelto", "fixture senza fontTools")


def test_install(
    root: Path, meta: Dict[str, Any], records: List[fontlib.FontRecord]
) -> None:
    print("\n-- install: catalogue + job fonts dir")
    bold = by_name(records, meta["bold"])
    check("record bold disponibile", bold is not None)
    if bold is None:
        return

    store = fontlib.Store(root / "store")
    workdir = root / "job"
    workdir.mkdir()
    target = fontchooser.install(store, bold, workdir)
    check("ritorna <workdir>/fonts", target == workdir / "fonts", target)
    installed = target / Path(bold.file).name
    check("copia il file scelto nella job dir", installed.is_file(), installed.name)
    check(
        "byte identici al sorgente",
        installed.is_file()
        and hashlib.sha256(installed.read_bytes()).hexdigest() == bold.sha256,
    )
    stored = store.get(bold.sha256)
    check("lo store indicizza la record", stored is not None)
    check(
        "lo store conserva famiglia e licenza",
        stored is not None
        and stored.family == bold.family
        and stored.licence == "commercial",
        stored.to_dict() if stored else None,
    )
    index = root / "store" / "index.json"
    check("index.json scritto", index.is_file())
    check(
        "index.json nomina lo sha del font",
        index.is_file() and bold.sha256 in index.read_text(encoding="utf-8"),
    )
    check("un solo record nello store", len(store.all()) == 1, len(store.all()))
    fontchooser.install(store, bold, workdir)
    check("secondo install non duplica", len(store.all()) == 1, len(store.all()))

    try:
        fontchooser.install(object(), bold, workdir)
        check("install rifiuta uno store non-valido", False, "nessuna eccezione")
    except contract.VxError:
        check("install rifiuta uno store non-valido", True)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        meta = make_fixtures(root)
        if HAVE_FONTTOOLS:
            print("fixtures: 4 TTF sintetici generati con fontTools")
        else:
            print("fixtures: Antonio reale + copia rinominata (fontTools assente)")
        records = fontchooser.inventory([meta["fonts"]])
        test_inventory(records, meta, root)
        test_measure(records, meta, root)
        test_thresholds_from_settings(records, meta)
        test_rank(records, meta)
        test_decide(records, meta)
        test_install(root, meta, records)
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
