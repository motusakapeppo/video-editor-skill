"""vx.fontchooser - autonomous typography over licensed local fonts, by measurement.

The font is chosen from evidence, not from a name. A filename lies for a living:
``Antonio-Bold.ttf`` may contain a light family, and ``Cool-Font-Condensed.ttf``
may be wide. So this module reads the ``name`` table out of the TTF/OTF binary
with the standard library, then renders two glyphs with Pillow and measures the
shapes it actually got:

* advance of ``H`` per em (the width a line of type really takes);
* cap height and x-height, as fractions of the em;
* stem width, as a fraction of the cap height.

Those three numbers give two classes - ``condensed``/``normal``/``extended`` and
``light``/``regular``/``medium``/``bold``/``black`` - and the classes are what the
requirement tokens are scored against. Nothing here needs a search engine: the
autonomy is lawful because it only ranges over fonts the operator already has,
and the licence is read from a sidecar and never guessed.

The licensing rule is the same as :mod:`vx.fontlib`: only ``commercial`` may be
chosen without the operator; ``personal`` may be proposed but is refused unless
``allow_personal=True``; ``unknown`` is never chosen. When nothing qualifies the
answer is ``None`` plus a reason, never a silent fall back to the bundled font:
a fallback would hide the fact that the job has no usable font.

Thresholds are module constants, each with an explicit range in
:data:`THRESHOLD_RANGES` that mirrors the ``settings.RANGES`` convention. The
effective value is read from ``contract.THRESHOLDS`` (the live snapshot written
by :mod:`vx.settings`), so ``--override thresholds.font_stem_bold_max=...``
works through the normal cascade; an out-of-range value is clamped, not trusted.
"""

from __future__ import annotations

import re
import statistics
import struct
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple, Union

from . import contract
from . import fontlib
from . import fontsource

__all__ = [
    "DEFAULT_METRIC_THRESHOLDS",
    "FONT_SIZE_PX",
    "THRESHOLD_RANGES",
    "WEIGHT_ORDER",
    "WIDTH_CLASSES",
    "decide",
    "install",
    "inventory",
    "measure",
    "rank",
    "read_names",
]

FONT_SIZE_PX = 128
FONT_SUFFIXES = (".ttf", ".otf")
INK_THRESHOLD = 128
#: Scan row, as a fraction of the ink height: above the bars that would merge
#: the stems of ``H`` and ``n`` into one run.
STEM_ROW_FRACTION = 0.25
STEM_MIN_RUN_PX = 2
#: A run wider than this share of the ink belongs to a bar, not to a stem.
STEM_MAX_RUN_SHARE = 0.5

#: Advance of ``H`` per em, below/above which the width class changes.
ADVANCE_CONDENSED_MAX = 0.45
ADVANCE_EXTENDED_MIN = 0.62
#: Stem width over cap height, upper bound of each weight class.
STEM_LIGHT_MAX = 0.09
STEM_REGULAR_MAX = 0.13
STEM_MEDIUM_MAX = 0.145
STEM_BOLD_MAX = 0.21

DEFAULT_METRIC_THRESHOLDS: Dict[str, float] = {
    "font_advance_condensed_max": ADVANCE_CONDENSED_MAX,
    "font_advance_extended_min": ADVANCE_EXTENDED_MIN,
    "font_stem_light_max": STEM_LIGHT_MAX,
    "font_stem_regular_max": STEM_REGULAR_MAX,
    "font_stem_medium_max": STEM_MEDIUM_MAX,
    "font_stem_bold_max": STEM_BOLD_MAX,
}

#: Allowed window for every metric threshold. Same shape as ``settings.RANGES``:
#: a value outside the window is a bug, so it is clamped and the default kept.
THRESHOLD_RANGES: Dict[str, Tuple[float, float]] = {
    "font_advance_condensed_max": (0.10, 0.60),
    "font_advance_extended_min": (0.30, 1.20),
    "font_stem_light_max": (0.01, 0.12),
    "font_stem_regular_max": (0.03, 0.18),
    "font_stem_medium_max": (0.05, 0.22),
    "font_stem_bold_max": (0.08, 0.35),
}

WEIGHT_ORDER = ("light", "regular", "medium", "bold", "black")
WIDTH_CLASSES = ("condensed", "normal", "extended")
LICENCE_ORDER: Dict[str, int] = {"unknown": 0, "personal": 1, "commercial": 2}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_VARIABLE = re.compile(r"\[[^\]]*\]")
_URL = re.compile(r"https?://\S+")
_STYLE_WORDS = (
    "thin",
    "hairline",
    "light",
    "regular",
    "book",
    "medium",
    "semibold",
    "demibold",
    "bold",
    "extrabold",
    "black",
    "heavy",
    "ultra",
    "condensed",
    "narrow",
    "compressed",
    "extended",
    "expanded",
    "wide",
    "italic",
    "oblique",
)

#: Requirement token -> measured width class it asks for.
_WIDTH_TOKENS: Dict[str, str] = {
    "condensed": "condensed",
    "narrow": "condensed",
    "compressed": "condensed",
    "extended": "extended",
    "expanded": "extended",
    "wide": "extended",
}

#: Requirement token -> measured weight class it asks for.
_WEIGHT_TOKENS: Dict[str, str] = {
    "thin": "light",
    "light": "light",
    "hairline": "light",
    "regular": "regular",
    "book": "regular",
    "normal": "regular",
    "medium": "medium",
    "semi": "bold",
    "demibold": "bold",
    "semibold": "bold",
    "bold": "bold",
    "heavy": "black",
    "extrabold": "black",
    "black": "black",
    "ultra": "black",
}


# ---------------------------------------------------------------------------
# thresholds
# ---------------------------------------------------------------------------


def _threshold(name: str) -> float:
    """Effective value of one metric threshold, clamped to its declared range."""
    default = DEFAULT_METRIC_THRESHOLDS[name]
    low, high = THRESHOLD_RANGES[name]
    raw = contract.THRESHOLDS.get(name, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = float(default)
    return min(high, max(low, value))


def _metric_thresholds() -> Dict[str, float]:
    values = {name: _threshold(name) for name in DEFAULT_METRIC_THRESHOLDS}
    if values["font_advance_condensed_max"] >= values["font_advance_extended_min"]:
        # An inverted pair would make "normal" unreachable: the declared
        # defaults are the answer, not a guessed midpoint.
        for name, value in DEFAULT_METRIC_THRESHOLDS.items():
            values[name] = float(value)
    return values


# ---------------------------------------------------------------------------
# name table — standard library, no guess from the filename
# ---------------------------------------------------------------------------


def _table_directory(handle: Any) -> Dict[str, Tuple[int, int]]:
    """``tag -> (offset, length)`` from the sfnt header, TTC-aware."""
    handle.seek(0)
    head = handle.read(12)
    if len(head) < 12:
        raise contract.VxError("font troncato: header sfnt assente")
    base = 0
    if head[:4] == b"ttcf":
        offsets = handle.read(4)
        if len(offsets) < 4:
            raise contract.VxError("collezione TTC troncata: nessun offset")
        base = struct.unpack(">I", offsets)[0]
        handle.seek(base)
        head = handle.read(12)
        if len(head) < 12:
            raise contract.VxError("collezione TTC: font interno troncato")
    handle.seek(base + 4)
    raw_count = handle.read(2)
    if len(raw_count) < 2:
        raise contract.VxError("font troncato: numero di tabelle assente")
    count = struct.unpack(">H", raw_count)[0]
    handle.seek(base + 12)
    raw = handle.read(count * 16)
    if len(raw) < count * 16:
        raise contract.VxError("font troncato: directory delle tabelle incompleta")
    tables: Dict[str, Tuple[int, int]] = {}
    for index in range(count):
        record = raw[index * 16 : (index + 1) * 16]
        tag = record[:4].decode("latin-1")
        offset, length = struct.unpack(">II", record[8:16])
        tables[tag] = (offset, length)
    return tables


def _decode_name(raw: bytes, platform: int, encoding: int) -> str:
    if platform == 1:
        return raw.decode("mac_roman", "replace")
    if platform in (0, 3):
        return raw.decode("utf-16-be", "replace")
    if platform == 2:
        return raw.decode("latin-1", "replace")
    return raw.decode("utf-8", "replace")


def _name_score(platform: int, language: int) -> int:
    """Higher is better: Windows/Unicode English first, then any Windows, Mac."""
    if platform == 0:
        return 3
    if platform == 3:
        if language in (0x0409, 0x0809):
            return 3
        if language & 0xFF == 0x09:
            return 2
        return 1
    if platform == 1:
        return 3 if language == 0 else 1
    return 0


def _parse_name_table(block: bytes) -> Dict[str, str]:
    if len(block) < 6:
        raise contract.VxError("tabella 'name' troncata")
    _format, count, string_offset = struct.unpack(">HHH", block[:6])
    best: Dict[str, Tuple[Tuple[int, int], str]] = {}
    for index in range(count):
        pos = 6 + index * 12
        if pos + 12 > len(block):
            break
        platform, encoding, language, name_id, length, offset = struct.unpack(
            ">HHHHHH", block[pos : pos + 12]
        )
        start = string_offset + offset
        end = start + length
        if start > end or end > len(block):
            continue
        try:
            text = _decode_name(block[start:end], platform, encoding)
        except LookupError:
            continue
        text = text.replace("\x00", "").strip()
        if not text:
            continue
        field = ""
        preference = 0
        if name_id == 16:
            field, preference = "family", 2
        elif name_id == 1:
            field, preference = "family", 1
        elif name_id == 17:
            field, preference = "subfamily", 2
        elif name_id == 2:
            field, preference = "subfamily", 1
        if not field:
            continue
        rank = (preference, _name_score(platform, language))
        current = best.get(field)
        if current is None or rank > current[0]:
            best[field] = (rank, text)
    return {field: value for field, (_rank, value) in best.items()}


def read_names(path: Union[str, Path]) -> Dict[str, str]:
    """Read ``family``/``subfamily`` from the binary ``name`` table.

    Uses nameID 16/17 (typographic) when present, then 1/2 (legacy). An
    unreadable table raises :class:`vx.contract.VxError`; the caller decides
    whether a filename fallback is honest.
    """
    target = Path(path)
    with open(target, "rb") as handle:
        tables = _table_directory(handle)
        entry = tables.get("name")
        if entry is None:
            raise contract.VxError(f"{target}: tabella 'name' assente")
        offset, length = entry
        if length <= 0 or length > 4 * 1024 * 1024:
            raise contract.VxError(f"{target}: lunghezza 'name' implausibile: {length}")
        handle.seek(offset)
        block = handle.read(length)
    if len(block) < length:
        raise contract.VxError(f"{target}: tabella 'name' troncata")
    return _parse_name_table(block)


def _family_from_stem(stem: str) -> str:
    base = _VARIABLE.sub("", str(stem or ""))
    parts = [part for part in re.split(r"[-_ ]+", base) if part]
    while len(parts) > 1 and parts[-1].lower() in _STYLE_WORDS:
        parts.pop()
    return " ".join(parts).strip() or base.strip() or "Senza nome"


def _styles_from_stem(stem: str) -> List[str]:
    out: List[str] = []
    for part in re.split(r"[-_ ]+", str(stem or "")):
        title = part.strip().title()
        if part.strip().lower() in _STYLE_WORDS and title not in out:
            out.append(title)
    return out


def _sidecar_licence(path: Path) -> Tuple[str, str]:
    sidecar = path.with_name(path.stem + ".licence.txt")
    if not sidecar.is_file():
        return "unknown", ""
    try:
        text = sidecar.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unknown", ""
    licence, _matched = fontsource.classify_licence(text)
    found = _URL.search(text)
    url = found.group(0).rstrip(").,;") if found else ""
    return licence, url


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------


def _as_paths(paths: Union[str, Path, Sequence[Union[str, Path]]]) -> List[Path]:
    if isinstance(paths, (str, Path)):
        return [Path(paths).expanduser()]
    return [Path(item).expanduser() for item in paths]


def _font_files(paths: Union[str, Path, Sequence[Union[str, Path]]]) -> List[Path]:
    found: Dict[str, Path] = {}
    for root in _as_paths(paths):
        if root.is_file():
            candidates = [root]
        elif root.is_dir():
            candidates = sorted(root.rglob("*"))
        else:
            continue
        for candidate in candidates:
            if candidate.is_file() and candidate.suffix.lower() in FONT_SUFFIXES:
                found.setdefault(str(candidate.resolve()), candidate)
    return [found[key] for key in sorted(found)]


def inventory(
    paths: Union[str, Path, Sequence[Union[str, Path]]],
) -> List[fontlib.FontRecord]:
    """Scan directories/files and return one record per font, metadata included.

    The family and subfamily come from the binary ``name`` table; the filename
    is used only when that table cannot be read, and the fallback is declared by
    :func:`read_names` raising. The licence comes from a ``<stem>.licence.txt``
    sidecar through :func:`vx.fontsource.classify_licence`; with no sidecar the
    licence is ``unknown`` - a local file is not evidence of anything.
    """
    records: List[fontlib.FontRecord] = []
    for path in _font_files(paths):
        try:
            names = read_names(path)
        except (OSError, struct.error, contract.VxError):
            names = {}
        family = str(names.get("family") or "").strip() or _family_from_stem(path.stem)
        subfamily = str(names.get("subfamily") or "").strip()
        styles = [subfamily] if subfamily else _styles_from_stem(path.stem)
        licence, licence_url = _sidecar_licence(path)
        absolute = path.resolve()
        records.append(
            fontlib.FontRecord(
                family=family,
                licence=licence,
                file=str(absolute),
                sha256=fontlib.sha256_file(absolute),
                styles=styles,
                licence_url=licence_url,
                source_url=absolute.as_uri(),
                retrieved_at="",
            )
        )
    return records


# ---------------------------------------------------------------------------
# measurement — rendered ink, not declared metadata
# ---------------------------------------------------------------------------


def _render(font: Any, character: str, image_module: Any, draw_module: Any) -> Any:
    canvas = image_module.new("L", (FONT_SIZE_PX * 3, FONT_SIZE_PX * 3), 0)
    draw_module.Draw(canvas).text(
        (FONT_SIZE_PX, FONT_SIZE_PX), character, font=font, fill=255
    )
    return canvas


def _ink_box(canvas: Any) -> Tuple[int, int, int, int]:
    box = canvas.getbbox()
    if box is None:
        return (0, 0, 0, 0)
    return (int(box[0]), int(box[1]), int(box[2]), int(box[3]))


def _runs(values: bytes, threshold: int = INK_THRESHOLD) -> List[int]:
    runs: List[int] = []
    length = 0
    for value in values:
        if value >= threshold:
            length += 1
        elif length:
            runs.append(length)
            length = 0
    if length:
        runs.append(length)
    return runs


def _stem_run(canvas: Any, box: Tuple[int, int, int, int]) -> List[int]:
    """Stem widths, in px, from one scan row above the bars of ``H``/``n``."""
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return []
    row = top + max(1, int(height * STEM_ROW_FRACTION))
    values = canvas.crop((left, row, right, row + 1)).tobytes()
    runs = [run for run in _runs(values) if run >= STEM_MIN_RUN_PX]
    limit = max(STEM_MIN_RUN_PX, int(width * STEM_MAX_RUN_SHARE))
    stems = [run for run in runs if run <= limit]
    return stems or runs


def _weight_class(ratio: float, limits: Mapping[str, float]) -> str:
    if ratio <= limits["font_stem_light_max"]:
        return "light"
    if ratio <= limits["font_stem_regular_max"]:
        return "regular"
    if ratio <= limits["font_stem_medium_max"]:
        return "medium"
    if ratio <= limits["font_stem_bold_max"]:
        return "bold"
    return "black"


def _width_class(advance: float, limits: Mapping[str, float]) -> str:
    if advance <= limits["font_advance_condensed_max"]:
        return "condensed"
    if advance >= limits["font_advance_extended_min"]:
        return "extended"
    return "normal"


def measure(record: fontlib.FontRecord) -> Dict[str, Any]:
    """Measure one record from the font file itself; no network, no metadata trust.

    Formulae, all normalised by the em box (``FONT_SIZE_PX = 128``):

    * ``advance = getlength("H") / 128`` - the advance of ``H`` per em;
    * ``cap_height = ink_height("H") / 128`` and
      ``x_height = ink_height("n") / 128``;
    * ``stem_width = median_stem("H", "n") / 128`` and
      ``stem_ratio = median_stem / ink_height("H")`` - the weight signal is the
      stem relative to the cap height, so a font with a huge cap is not called
      bold just because its pixels are big.
    """
    path = Path(str(record.file or ""))
    if not path.is_file():
        raise contract.VxError(f"measure: file del font assente: {path}")
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover - Pillow is a declared dep
        raise contract.VxError("measure: richiede Pillow per misurare i glifi") from exc
    try:
        font = ImageFont.truetype(str(path), FONT_SIZE_PX)
    except OSError as exc:
        raise contract.VxError(f"measure: Pillow non apre {path}: {exc}") from exc

    cap_canvas = _render(font, "H", Image, ImageDraw)
    cap_box = _ink_box(cap_canvas)
    cap_px = cap_box[3] - cap_box[1]
    if cap_px <= 0:
        raise contract.VxError(f"measure: il glifo 'H' non ha inchiostro in {path}")

    x_canvas = _render(font, "n", Image, ImageDraw)
    x_box = _ink_box(x_canvas)
    x_px = x_box[3] - x_box[1]

    stems = _stem_run(cap_canvas, cap_box) + _stem_run(x_canvas, x_box)
    stem_px = float(statistics.median(stems)) if stems else 0.0

    limits = _metric_thresholds()
    advance = float(font.getlength("H")) / FONT_SIZE_PX
    cap_height = cap_px / FONT_SIZE_PX
    x_height = x_px / FONT_SIZE_PX
    stem_ratio = (stem_px / cap_px) if cap_px else 0.0

    return {
        "advance": advance,
        "cap_height": cap_height,
        "x_height": x_height,
        "stem_width": stem_px / FONT_SIZE_PX,
        "stem_ratio": stem_ratio,
        "width_class": _width_class(advance, limits),
        "weight_class": _weight_class(stem_ratio, limits),
        "font_size_px": FONT_SIZE_PX,
        "thresholds": dict(limits),
    }


# ---------------------------------------------------------------------------
# ranking — measured classes against requirement tokens and energy
# ---------------------------------------------------------------------------


def _tokens(text: str) -> List[str]:
    return [token for token in _NON_ALNUM.split(str(text or "").lower()) if token]


def rank(
    records: Sequence[fontlib.FontRecord],
    *,
    requirement: str,
    energy: float,
    licence_floor: str = "commercial",
) -> List[Dict[str, Any]]:
    """Deterministic ranking over records at or above the licence floor.

    Scoring, in points:

    * width token (``condensed``/``narrow``/``wide``/...) matching the measured
      width class: ``+3``;
    * weight token matching the measured weight class exactly: ``+3``, one class
      away: ``+1`` (a bold request accepts a black candidate as a fallback);
    * energy, clamped to ``[0, 1]``, asks for a weight index
      ``round(energy * 4)``; the candidate loses one point per class it is away
      from that target. High energy therefore prefers bold/black, low energy
      light/regular, without forbidding anything.

    Ties break on lower-case family, then sha256: same inputs, same order.
    Every entry carries a one-line ``because`` in plain language that names the
    measured classes, so the proposal can be read by a non-technical operator.
    """
    floor = str(licence_floor or "").strip().lower()
    if floor not in fontlib.LICENCES:
        raise contract.VxError(
            f"rank: licence_floor inattesa: {licence_floor!r}; "
            f"valide: {', '.join(fontlib.LICENCES)}"
        )
    try:
        value = float(energy)
    except (TypeError, ValueError):
        raise contract.VxError(f"rank: energy non numerica: {energy!r}") from None
    value = min(1.0, max(0.0, value))
    target = int(round(value * 4))
    floor_rank = LICENCE_ORDER[floor]
    tokens = _tokens(requirement)

    ranked: List[Dict[str, Any]] = []
    for record in records:
        if LICENCE_ORDER[record.licence] < floor_rank:
            continue
        metrics = measure(record)
        width = str(metrics["width_class"])
        weight = str(metrics["weight_class"])
        weight_index = WEIGHT_ORDER.index(weight)
        points = 0
        reasons: List[str] = []
        for token in tokens:
            wanted_width = _WIDTH_TOKENS.get(token)
            if wanted_width is not None and wanted_width == width:
                points += 3
                reasons.append(f"+3 {token} (larghezza {width} misurata)")
            wanted_weight = _WEIGHT_TOKENS.get(token)
            if wanted_weight is not None:
                distance = abs(WEIGHT_ORDER.index(wanted_weight) - weight_index)
                if distance == 0:
                    points += 3
                    reasons.append(f"+3 {token} (peso {weight} misurato)")
                elif distance == 1:
                    points += 1
                    reasons.append(f"+1 {token} (~ peso {weight} misurato)")
        penalty = -abs(weight_index - target)
        score = float(points + penalty)
        style = ", ".join(record.styles) or "stile non dichiarato"
        detail = (
            "; ".join(reasons)
            or "nessun token del requisito corrisponde alle classi misurate"
        )
        because = (
            f"{record.family} ({style}): larghezza {width} "
            f"(avanzamento {metrics['advance']:.3f} em), peso {weight} "
            f"(asta {metrics['stem_ratio']:.3f} dell'altezza maiuscola); "
            f"{detail}; energia {value:.2f} chiede {WEIGHT_ORDER[target]} "
            f"({penalty:+d}); punteggio {score:+.1f}"
        )
        ranked.append({"record": record, "score": score, "because": because})

    ranked.sort(
        key=lambda item: (
            -item["score"],
            item["record"].family.lower(),
            item["record"].sha256,
        )
    )
    return ranked


def _why_none(records: Sequence[fontlib.FontRecord], allow_personal: bool) -> str:
    if not records:
        return "inventario vuoto: nessun font locale da proporre"
    counts = {licence: 0 for licence in fontlib.LICENCES}
    for record in records:
        counts[record.licence] = counts.get(record.licence, 0) + 1
    detail = ", ".join(
        f"{licence}={counts[licence]}"
        for licence in fontlib.LICENCES
        if counts[licence]
    )
    if allow_personal:
        return (
            f"nessun font selezionabile: licenze presenti ({detail}); "
            "'unknown' non e' una licenza d'uso e non viene mai scelta"
        )
    return (
        f"nessun font con licenza commerciale: licenze presenti ({detail}); "
        "senza allow_personal=True la licenza 'personal' non puo' essere "
        "scelta automaticamente"
    )


def decide(
    records: Sequence[fontlib.FontRecord],
    *,
    requirement: str,
    energy: float,
    allow_personal: bool = False,
) -> Dict[str, Any]:
    """The autonomous choice: one record, its alternatives, and the words why.

    Only ``commercial`` is chosen unless ``allow_personal`` is explicitly true;
    ``unknown`` is never chosen. With nothing eligible ``chosen`` is ``None`` and
    ``reason_if_none`` names the licences found - the bundled font is not a
    silent substitute.
    """
    floor = "personal" if allow_personal else "commercial"
    ranked = rank(records, requirement=requirement, energy=energy, licence_floor=floor)
    if not ranked:
        return {
            "chosen": None,
            "alternatives": [],
            "because": "",
            "reason_if_none": _why_none(records, allow_personal),
        }
    top = ranked[0]
    return {
        "chosen": top["record"],
        "alternatives": ranked[1:],
        "because": str(top["because"]),
        "reason_if_none": "",
    }


# ---------------------------------------------------------------------------
# install — one record into the catalogue and the job's fonts dir
# ---------------------------------------------------------------------------


def install(
    store: fontlib.Store, record: fontlib.FontRecord, workdir: Union[str, Path]
) -> Path:
    """Register ``record`` in ``store`` and copy it into ``<workdir>/fonts``.

    Returns the job fonts directory. The store is content-addressed, so a second
    install of the same bytes changes no index and no mtime.
    """
    if not isinstance(store, fontlib.Store):
        raise contract.VxError("install: atteso un fontlib.Store")
    if not isinstance(record, fontlib.FontRecord):
        raise contract.VxError("install: atteso un fontlib.FontRecord")
    stored = store.add_file(
        record.file,
        family=record.family,
        licence=record.licence,
        licence_url=record.licence_url,
        source_url=record.source_url,
        styles=record.styles,
        retrieved_at=record.retrieved_at,
    )
    return fontlib.job_fonts_dir(workdir, [stored])
