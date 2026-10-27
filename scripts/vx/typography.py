"""vx.typography — the chosen font, from a picture to the burnt-in frame.

Three jobs live here, one mechanism:

* :func:`preview` and :func:`preview_sheet` render the real font file with
  Pillow, so a non-technical operator can compare candidates as pixels instead of
  as file names. No fallback to a blank image: if Pillow is absent this module
  refuses with :class:`vx.contract.MissingDependency`.
* :func:`apply` is the injection point. It registers the record in the store,
  builds ``<workdir>/fonts`` (chosen font plus the bundled one) through
  :func:`vx.fontlib.job_fonts_dir`, generates ``LICENSES_MATRIX.md`` through
  :func:`vx.fontlib.write_matrix`, and returns the family string the ASS must
  carry as ``Fontname``. The renderer then points libass at that directory with
  the ``fontsdir`` option (see :func:`vx.render.render_edl`).

The licence rule is the whole reason the matrix exists: ``personal`` and
``unknown`` are refused for a job unless the operator says ``allow_personal=True``,
and even then the matrix carries a visible warning. Nothing here renders video,
touches ffmpeg or knows about subtitle styles.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import contract
from . import fontchooser
from . import fontlib
from . import fonts

__all__ = ["apply", "preview", "preview_sheet"]

#: Licence of the font shipped in ``assets/fonts`` (SIL OFL 1.1, commercial use).
BUNDLED_LICENCE = "commercial"
BUNDLED_LICENCE_URL = "http://scripts.sil.org/OFL"

_FONT_SUFFIXES = (".ttf", ".otf")
_REFUSED = ("personal", "unknown")


# --------------------------------------------------------------------------- #
# Pillow, declared not guessed
# --------------------------------------------------------------------------- #


def _pillow() -> Tuple[Any, Any, Any]:
    """Import Pillow, or refuse naming the dependency the operator must install."""
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - exercised only without Pillow
        raise contract.MissingDependency(
            "typography: Pillow non e' installato; serve per l'anteprima del font "
            "(pip install Pillow)"
        ) from exc
    return Image, ImageDraw, ImageFont


def _load_font(path: Any, size: int, what: str) -> Any:
    _, _, ImageFont = _pillow()
    text = str(path or "").strip()
    if not text:
        raise contract.VxError(f"typography: {what} senza file")
    target = Path(text)
    if not target.is_file():
        raise contract.VxError(f"typography: {what}: file mancante: {target}")
    try:
        return ImageFont.truetype(str(target), int(size))
    except OSError as exc:
        raise contract.VxError(
            f"typography: {what}: font non leggibile: {target}: {exc}"
        ) from exc


def _check_record(record: Any) -> fontlib.FontRecord:
    if not isinstance(record, fontlib.FontRecord):
        raise contract.VxError("typography: atteso un fontlib.FontRecord")
    return record


def _lines(draw: Any, text: str, font: Any, max_width: float) -> List[str]:
    """Greedy wrap by measured advance, so the canvas follows the real glyphs."""
    words = str(text or "").split()
    if not words:
        return [str(text or " ")]
    out: List[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if not current or draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            out.append(current)
            current = word
    out.append(current)
    return out


# --------------------------------------------------------------------------- #
# FX-T03 — rendered preview
# --------------------------------------------------------------------------- #


def preview(
    record: fontlib.FontRecord,
    text: str,
    out_png: str | Path,
    *,
    size: int = 96,
    width: int = 1600,
    background: Tuple[int, int, int, int] = (12, 12, 12, 255),
    foreground: Tuple[int, int, int, int] = (255, 255, 255, 255),
) -> Path:
    """Render ``text`` with the real font file and write a PNG.

    The canvas is sized from the measured bbox of the wrapped lines, not from a
    guess. Same record, text, size and colours produce a byte-identical file.
    """
    record = _check_record(record)
    Image, ImageDraw, _ = _pillow()
    font = _load_font(record.file, size, f"record {record.family!r}")

    pad = max(8, int(size) // 4)
    limit = max(1.0, float(width) - 2 * pad)
    draw = ImageDraw.Draw(Image.new("L", (8, 8), 0))
    lines = _lines(draw, text, font, limit)
    widest = max((draw.textlength(line, font=font) for line in lines), default=0.0)
    ascent, descent = font.getmetrics()
    line_h = max(1, ascent + descent)
    canvas_w = max(1, min(int(width), int(math.ceil(widest)) + 2 * pad))
    canvas_h = max(1, line_h * len(lines) + 2 * pad)

    image = Image.new("RGBA", (canvas_w, canvas_h), background)
    painter = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        painter.text((pad, pad + index * line_h), line, font=font, fill=foreground)

    target = Path(out_png)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG")
    return target.resolve()


def preview_sheet(
    records: Sequence[fontlib.FontRecord],
    text: str,
    out_png: str | Path,
    *,
    size: int = 72,
    width: int = 1600,
    background: Tuple[int, int, int, int] = (12, 12, 12, 255),
    foreground: Tuple[int, int, int, int] = (255, 255, 255, 255),
) -> Path:
    """One PNG, one row per record, family name rendered in its own font.

    The row is: the family in its own font, then a neutral label (family,
    licence, sha prefix) in the bundled Antonio. Row height is constant so the
    sheet's height is exactly ``2*pad + len(records)*row_h``: n records, n rows.
    Input order is kept; nothing is sorted, so the caller's ranking stays visible.
    """
    items = list(records)
    if not items:
        raise contract.VxError("typography.preview_sheet: nessun record da mostrare")
    for record in items:
        _check_record(record)

    Image, ImageDraw, ImageFont = _pillow()
    pad = max(8, int(size) // 3)
    label_size = max(12, int(size * 0.42))
    neutral_path = fonts.font_file("Antonio", "Regular")
    if neutral_path is not None:
        label_font = _load_font(neutral_path, label_size, "etichetta neutra")
    else:  # pragma: no cover - the bundle ships Antonio
        label_font = ImageFont.load_default(size=label_size)

    loaded = [
        (
            record,
            _load_font(record.file, size, f"record {record.family!r}"),
            f"{record.family}  -  {record.licence}  -  {record.sha256[:8]}",
        )
        for record in items
    ]

    draw = ImageDraw.Draw(Image.new("L", (8, 8), 0))
    row_h = 0
    for _record, own_font, label in loaded:
        own_ascent, own_descent = own_font.getmetrics()
        label_ascent, label_descent = label_font.getmetrics()
        row_h = max(
            row_h,
            max(1, own_ascent + own_descent)
            + max(1, label_ascent + label_descent)
            + pad,
        )
    row_h += pad

    canvas_w = max(1, int(width))
    canvas_h = max(1, 2 * pad + row_h * len(loaded))
    image = Image.new("RGBA", (canvas_w, canvas_h), background)
    painter = ImageDraw.Draw(image)

    for index, (_record, own_font, label) in enumerate(loaded):
        top = pad + index * row_h
        painter.text((pad, top), _record.family, font=own_font, fill=foreground)
        own_w = int(math.ceil(draw.textlength(_record.family, font=own_font)))
        painter.text(
            (pad + own_w + pad, top + max(0, size - label_size)),
            label,
            font=label_font,
            fill=foreground,
        )

    target = Path(out_png)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG")
    return target.resolve()


# --------------------------------------------------------------------------- #
# FX-T06 — licence gate and matrix
# --------------------------------------------------------------------------- #


def _refusal(record: fontlib.FontRecord) -> str:
    if record.licence == "personal":
        return (
            f"typography: il font {record.family!r} ha licenza 'personal': non puo' "
            "entrare in un lavoro senza un consenso esplicito "
            "(apply(..., allow_personal=True))"
        )
    return (
        f"typography: il font {record.family!r} ha licenza 'unknown': nessuno ha "
        "verificato i termini d'uso, quindi non puo' entrare in un lavoro; "
        "registra la licenza o passa allow_personal=True per un uso privato"
    )


def _warning(record: fontlib.FontRecord) -> str:
    return (
        f"> WARNING: this job uses the font {record.family!r} under the licence "
        f"'{record.licence}', which is NOT cleared for commercial use. "
        "The operator accepted it explicitly with allow_personal=True."
    )


def _ass_family(family: str) -> str:
    """A family that can sit in an ASS ``Style:`` line without moving a field."""
    text = str(family or "").strip()
    if not text:
        raise contract.VxError("typography: family vuota, nessun Fontname possibile")
    if any(bad in text for bad in (",", "\n", "\r")):
        raise contract.VxError(
            f"typography: family {text!r} contiene una virgola o un a capo: "
            "non e' un Fontname ASS valido"
        )
    return text


def _bundled_records() -> List[fontlib.FontRecord]:
    """The shipped fonts as records: OFL 1.1, commercial use, no retrieval date."""
    out: List[fontlib.FontRecord] = []
    root = fonts.package_font_dir()
    if not root.is_dir():
        return out
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.suffix.lower() not in _FONT_SUFFIXES:
            continue
        try:
            names = fontchooser.read_names(path)
        except (OSError, contract.VxError):
            names = {}
        family = str(names.get("family") or "").strip() or path.stem.split("-")[0]
        subfamily = str(names.get("subfamily") or "").strip()
        out.append(
            fontlib.FontRecord(
                family=family,
                licence=BUNDLED_LICENCE,
                file=str(path.resolve()),
                sha256=fontlib.sha256_file(path),
                styles=[subfamily] if subfamily else [],
                licence_url=BUNDLED_LICENCE_URL,
                source_url=path.resolve().as_uri(),
                retrieved_at="",
            )
        )
    return out


class _ScopedStore(fontlib.Store):
    """A read-only Store view over the records one job actually uses.

    ``fontlib.write_matrix`` stays the only generator; this subclass only changes
    what it sees, from the whole user cache to the job's own fonts. Nothing is
    ever written through it.
    """

    def __init__(self, records: Sequence[fontlib.FontRecord]) -> None:
        super().__init__(Path("."))
        self._records: Optional[Dict[str, fontlib.FontRecord]] = {
            record.sha256: record for record in records
        }

    def _save(self) -> None:
        return None


def apply(
    store: fontlib.Store,
    record: fontlib.FontRecord,
    workdir: str | Path,
    *,
    allow_personal: bool = False,
) -> Dict[str, Any]:
    """Inject ``record`` into one job: cache, fonts dir, matrix, ASS family.

    Returns ``{fonts_dir, family, ass_fontname, licence, matrix_path}``. The
    licence gate runs before any copy into the job: ``personal`` and ``unknown``
    raise :class:`vx.contract.VxError` naming the licence unless the operator
    passed ``allow_personal=True``; in that case the matrix carries a warning.
    """
    if not isinstance(store, fontlib.Store):
        raise contract.VxError("typography.apply: atteso un fontlib.Store")
    record = _check_record(record)
    family = _ass_family(record.family)

    if record.licence in _REFUSED and not allow_personal:
        raise contract.VxError(_refusal(record))

    stored = store.add_file(
        record.file,
        family=record.family,
        licence=record.licence,
        licence_url=record.licence_url,
        source_url=record.source_url,
        styles=record.styles,
        retrieved_at=record.retrieved_at,
    )
    # The cache wins on a duplicate: the effective licence is the stored one.
    if stored.licence in _REFUSED and not allow_personal:
        raise contract.VxError(_refusal(stored))

    fonts_dir = fontlib.job_fonts_dir(workdir, [stored])
    used = [stored, *_bundled_records()]
    matrix = _ScopedStore(used).write_matrix(Path(workdir) / fontlib.MATRIX_NAME)
    warning = ""
    if stored.licence in _REFUSED or record.licence in _REFUSED:
        warning = _warning(stored if stored.licence in _REFUSED else record)
    if warning:
        text = matrix.read_text(encoding="utf-8")
        lines = text.splitlines()
        lines.insert(1, "")
        lines.insert(2, warning)
        matrix.write_text(
            "\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8"
        )

    return {
        "fonts_dir": str(fonts_dir),
        "family": stored.family,
        "ass_fontname": family,
        "licence": stored.licence,
        "matrix_path": str(matrix),
    }
