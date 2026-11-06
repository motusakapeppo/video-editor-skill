"""vx.fontproposal -- licensed fonts proposed for one job, with a specimen.

The chooser knows how to measure a font and rank it against a written
requirement (:mod:`vx.fontchooser`), and the catalogue knows which file belongs
to which licence (:mod:`vx.fontlib`). This module is the bridge between that
evidence and the operator: given a job directory, a free-text requirement and an
energy value, it proposes up to three commercial families, renders a PNG
specimen for each one and writes ``<workdir>/font_proposal.json``.

The pool has two halves:

* **local** - the ``fontlib.Store`` index, the bundled ``assets/fonts`` and the
  fetched cache. Always available, never requires a socket.
* **catalogue** - ``assets/fonts/catalogue.json`` (2,065 families). Downloading
  all of it is impossible, so :data:`INTENT_FAMILIES` maps the words of the
  requirement (Italian and English) to a short list of known families. Every
  name is verified against the catalogue before use; a missing one is discarded
  and the reason is written in the proposal.

With ``allow_network=False`` (the default) the catalogue half is not touched:
``pool`` is ``"local"`` and no request leaves the machine. With
``allow_network=True`` at most :data:`CATALOGUE_LIMIT` selected families are
fetched through :func:`vx.fontfetch.fetch`, measured from their real bytes and
ranked like everything else. A failed download is skipped and declared, never
fatal.

Scores and ``because`` sentences come from :func:`vx.fontchooser.rank`; the
module translates the requirement's intent words into the width/weight tokens
that rank understands before scoring, so "condensato" and "aggressivo" move the
ranking instead of being ignored. The proposal is not wired into the render.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from . import contract
from . import fontchooser
from . import fontlib
from . import fonts
from . import fontsource
from . import fontstore

__all__ = [
    "CATALOGUE_LIMIT",
    "ENV_STORE",
    "INTENT_FAMILIES",
    "INTENT_KEYWORDS",
    "PROPOSAL_NAME",
    "SCHEMA",
    "intent_families",
    "propose",
]

SCHEMA = "vx.fontproposal/1"
PROPOSAL_NAME = "font_proposal.json"
ENV_STORE = "VX_FONT_STORE"

#: Upper bound on families fetched from the catalogue in one proposal.
CATALOGUE_LIMIT = 12

#: Canvas of the specimen. The width is fixed by the contract; the height leaves
#: a large type band and a readable family line underneath.
SAMPLE_WIDTH = 1200
SAMPLE_HEIGHT = 640
MARGIN_X = 70
SAMPLE_TOP = 55
SAMPLE_BOTTOM = 470
LABEL_CENTER_Y = 548
RULE_Y = 492

_FONT_SUFFIXES = (".ttf", ".otf")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

#: Intent -> known catalogue families (ids as ``catalogue.json`` spells them).
#: Verified against the catalogue at use time; a missing name is discarded.
INTENT_FAMILIES: Dict[str, Tuple[str, ...]] = {
    "condensed": (
        "oswald",
        "barlowcondensed",
        "archivonarrow",
        "fjallaone",
        "sairacondensed",
        "teko",
        "antonio",
        "bebasneue",
    ),
    "horror": (
        "rubikglitch",
        "nosifer",
        "creepster",
        "butcherman",
        "metalmania",
        "rubikburned",
        "specialelite",
        "pirataone",
    ),
    "aggressive": (
        "blackopsone",
        "archivoblack",
        "staatliches",
        "squadaone",
        "bigshouldersdisplay",
        "bungee",
    ),
    "elegant": (
        "playfairdisplay",
        "cormorantgaramond",
        "ebgaramond",
        "librebaskerville",
        "dmserifdisplay",
    ),
    # Il tono documentario/sobrio non era in tabella: un prompt che diceva
    # "tono sobrio e documentario" non selezionava niente, e la shortlist cadeva
    # su Archivo Black, Black Ops One e Butcherman (tre display pesanti, tutti a
    # punteggio 3.0). Misurato il 4 novembre su un lavoro vero.
    "editorial": (
        "librebaskerville",
        "ebgaramond",
        "cormorantgaramond",
        "inter",
        "worksans",
        "dmsans",
    ),
    "technical": (
        "jetbrainsmono",
        "spacemono",
        "ibmplexmono",
        "robotomono",
        "sharetechmono",
    ),
    "minimal": (
        "inter",
        "montserrat",
        "worksans",
        "dmsans",
        "manrope",
        "archivo",
    ),
    "vintage": ("abrilfatface", "lobster", "bungee", "alfaslabone"),
    "bold": (
        "archivoblack",
        "antonio",
        "oswald",
        "bebasneue",
        "staatliches",
        "blackopsone",
    ),
}

#: Intent -> normalized word stems that trigger it, Italian and English.
INTENT_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "condensed": ("condens", "narrow", "strett", "compress", "tight", "slim"),
    "horror": ("horror", "terrore", "spavent", "paura", "creep", "eerie", "gotich"),
    "aggressive": (
        "aggress",
        "taglient",
        "sharp",
        "gritty",
        "brutal",
        "raw",
        "grunge",
        "distort",
        "impact",
    ),
    "elegant": ("elegant", "editorial", "raffinat", "lusso", "luxury", "serif", "classic"),
    "editorial": (
        "sobri",
        "documentar",
        "istituzional",
        "giornalis",
        "intervist",
        "testimonianz",
        "reportage",
        "cronaca",
    ),
    "technical": ("tecnic", "mono", "code", "terminal", "digital", "cyber", "futur"),
    "minimal": ("minimal", "pulit", "clean", "modern", "grotesque", "sans", "neutro"),
    "vintage": ("vintage", "retro", "retr", "nostalg", "oldstyle"),
    "bold": ("bold", "black", "heavy", "pesant", "grassett", "massicci", "titolo", "titoli", "title"),
}

#: Families used when no intent word matches: neutral grotesques and one strong.
DEFAULT_FAMILIES: Tuple[str, ...] = ("inter", "montserrat", "archivo", "oswald")

#: Intent stems that also steer :func:`vx.fontchooser.rank`.
_RANK_WORDS: Dict[str, str] = {
    "condens": "condensed",
    "narrow": "condensed",
    "strett": "condensed",
    "compress": "condensed",
    "horror": "black",
    "aggress": "black",
    "taglient": "black",
    "pesant": "black",
    "heavy": "black",
    "grassett": "bold",
    "bold": "bold",
    "black": "black",
    "light": "light",
    "thin": "light",
    "legger": "light",
    # Il tono sobrio/documentario chiede un peso regolare, non un display nero:
    # senza questa riga il ranking ordinava comunque per peso e i primi tre
    # erano le facce piu' pesanti del catalogo.
    "sobri": "regular",
    "documentar": "regular",
    "istituzional": "regular",
    "giornalis": "regular",
    "intervist": "regular",
    "testimonianz": "regular",
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm(text: Any) -> str:
    return _NON_ALNUM.sub("", str(text or "").lower())


def _words(text: Any) -> List[str]:
    return [word for word in _NON_ALNUM.split(str(text or "").lower()) if word]


def _store_root() -> Path:
    """Where the catalogue index lives: ``VX_FONT_STORE`` or the bundled dir."""
    raw = os.environ.get(ENV_STORE)
    return Path(raw).expanduser() if raw else fonts.package_font_dir()


def _licence_text(root: Path, family: str) -> Tuple[str, Optional[Path]]:
    """The shipped licence text for ``family``, matched by file name."""
    wanted = _norm(family)
    best: Optional[Path] = None
    for path in sorted(root.glob("*OFL*.txt")):
        base = _norm(path.stem.replace("OFL", ""))
        if wanted and base == wanted:
            return path.read_text(encoding="utf-8", errors="replace"), path
        if wanted and base and (base.startswith(wanted) or wanted.startswith(base)):
            best = best or path
    if best is not None:
        return best.read_text(encoding="utf-8", errors="replace"), best
    fallback = root / "OFL.txt"
    if fallback.is_file():
        return fallback.read_text(encoding="utf-8", errors="replace"), fallback
    return "", None


def _bundled_records() -> List[fontlib.FontRecord]:
    """The shipped fonts as records, licensed by their own licence files."""
    root = fonts.package_font_dir()
    if not root.is_dir():
        return []
    found: List[fontlib.FontRecord] = []
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.suffix.lower() not in _FONT_SUFFIXES:
            continue
        try:
            names = fontchooser.read_names(path)
        except (OSError, contract.VxError):
            names = {}
        family = str(names.get("family") or "").strip()
        if not family:
            family = path.stem.split("-")[0].replace("[", "").strip() or path.stem
        subfamily = str(names.get("subfamily") or "").strip()
        text, licence_path = _licence_text(root, family)
        licence, _matched = (
            fontsource.classify_licence(text) if text else ("unknown", "")
        )
        found.append(
            fontlib.FontRecord(
                family=family,
                licence=licence,
                file=str(path.resolve()),
                sha256=fontlib.sha256_file(path),
                styles=[subfamily] if subfamily else [],
                licence_url=str(licence_path.resolve().as_uri()) if licence_path else "",
                source_url=path.resolve().as_uri(),
                retrieved_at="",
            )
        )
    return found


def _source_of_local(record: fontlib.FontRecord) -> str:
    """``bundle`` for a shipped file, ``cache`` for anything else local."""
    path = Path(str(record.file or "")).resolve()
    root = fonts.package_font_dir().resolve()
    return "bundle" if path == root or root in path.parents else "cache"


def _load_records() -> List[Tuple[fontlib.FontRecord, str]]:
    """Store records first, then the bundle; one entry per sha256."""
    store = fontlib.Store(_store_root())
    pairs: List[Tuple[fontlib.FontRecord, str]] = []
    try:
        pairs.extend((record, _source_of_local(record)) for record in store.all())
    except (OSError, contract.VxError):
        pass
    pairs.extend((record, "bundle") for record in _bundled_records())
    seen = set()
    unique: List[Tuple[fontlib.FontRecord, str]] = []
    for record, source in pairs:
        key = record.sha256 or record.file
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append((record, source))
    return unique


# ---------------------------------------------------------------------------
# intent -> catalogue families
# ---------------------------------------------------------------------------


def _keyword_hit(word: str, keyword: str) -> bool:
    if len(word) < 3 or len(keyword) < 3:
        return word == keyword
    return keyword in word or word in keyword


def intent_families(
    requirement: str,
    *,
    mapping: Optional[Mapping[str, Sequence[str]]] = None,
    rows: Optional[Sequence[Mapping[str, Any]]] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Translate words to catalogue families, verified against the catalogue.

    Returns ``{"selected": [...], "discarded": [...], "matched": [...]}``.
    ``selected`` holds the catalogue's own family ids, taken round-robin across
    the matched intents so one intent cannot fill the whole shortlist. A name
    that is not in the catalogue goes into ``discarded`` with an Italian reason.
    """
    table = mapping if mapping is not None else INTENT_FAMILIES
    catalogue_rows = (
        list(rows)
        if rows is not None
        else list(fontstore.catalogue().get("families") or [])
    )
    available: Dict[str, Mapping[str, Any]] = {}
    for row in catalogue_rows:
        if isinstance(row, Mapping):
            available.setdefault(_norm(row.get("family")), row)

    words = _words(requirement)
    matched: List[str] = []
    signals: List[str] = []
    for intent in table:
        keywords = INTENT_KEYWORDS.get(intent, ())
        hits = [
            word
            for word in words
            if any(_keyword_hit(word, keyword) for keyword in keywords)
        ]
        if hits:
            matched.append(intent)
            signals.extend(f"{intent}:{word}" for word in dict.fromkeys(hits))
    groups: List[Sequence[str]] = []
    if matched:
        for intent in matched:
            groups.append(table[intent])
    else:
        groups = [DEFAULT_FAMILIES]
        matched = ["default"]

    selected: List[str] = []
    discarded: List[str] = []
    seen: set[str] = set()
    index = 0
    while groups and (limit is None or len(selected) < limit):
        progressed = False
        for group in groups:
            if index >= len(group):
                continue
            progressed = True
            name = str(group[index] or "").strip()
            key = _norm(name)
            if not key:
                continue
            if key in seen:
                continue
            seen.add(key)
            row = available.get(key)
            if row is None:
                discarded.append(
                    f"{name}: famiglia assente dal catalogo, scartata"
                )
                continue
            selected.append(str(row.get("family") or name))
            if limit is not None and len(selected) >= limit:
                break
        if not progressed:
            break
        index += 1
    return {"selected": selected, "discarded": discarded, "matched": matched, "signals": signals}


def _rank_requirement(requirement: str) -> str:
    """Requirement with intent words translated into rank's width/weight tokens.

    Repeated synonyms collapse to one token: "horror" and "aggressivo" both ask
    for a black weight, and counting that twice would weight the same intent
    twice just because the prompt used two words.
    """
    out: List[str] = []
    seen: set[str] = set()
    for word in _words(requirement):
        translated = word
        for stem, token in _RANK_WORDS.items():
            if _keyword_hit(word, stem):
                translated = token
                break
        if translated in seen:
            continue
        seen.add(translated)
        out.append(translated)
    return " ".join(out)


# ---------------------------------------------------------------------------
# catalogue download
# ---------------------------------------------------------------------------


def _pick_file(loaded: Mapping[str, Any]) -> Optional[Path]:
    items = [
        item
        for item in (loaded.get("files") or ())
        if Path(str(item.get("path") or "")).is_file()
    ]
    if not items:
        return None
    upright = [
        item
        for item in items
        if not any(
            word in str(item.get("name") or "").lower()
            for word in ("italic", "oblique")
        )
    ] or items
    regular = [
        item for item in upright if "regular" in str(item.get("name") or "").lower()
    ]
    return Path(str((regular or upright)[0]["path"]))


def _catalogue_record(
    row: Mapping[str, Any],
) -> Tuple[Optional[fontlib.FontRecord], str, bool, str]:
    """Fetch, license and measure one catalogue family; skip with a reason."""
    from . import fontfetch  # noqa: PLC0415 -- network path, imported on use
    from . import fontresolve  # noqa: PLC0415 -- one licence classifier

    name = str(row.get("family") or "").strip()
    if not name:
        return None, "", False, "famiglia senza nome nel catalogo, scartata"
    try:
        cached_before = fontstore.cached_row(fontstore.store_root(), name) is not None
        loaded = fontfetch.fetch(name, allow_network=True)
    except Exception as exc:
        return None, "", False, f"{name}: prelievo non riuscito ({exc})"

    detail = str(loaded.get("licence") or row.get("licence") or "").strip()
    licence = fontresolve.usage(detail)
    if licence != "commercial":
        return (
            None,
            "",
            False,
            f"{name}: licenza {detail or 'assente'!r} non consente l'uso commerciale",
        )

    path = _pick_file(loaded)
    if path is None:
        return None, "", False, f"{name}: prelievo senza file utilizzabile"
    try:
        names = fontchooser.read_names(path)
    except (OSError, contract.VxError):
        names = {}
    family = str(names.get("family") or "").strip() or name
    subfamily = str(names.get("subfamily") or "").strip()
    directory = str(loaded.get("directory") or row.get("directory") or "").strip("/")
    licence_url = (
        f"https://github.com/google/fonts/tree/main/{directory}"
        if directory
        else "https://github.com/google/fonts"
    )
    record = fontlib.FontRecord(
        family=family,
        licence=licence,
        file=str(path.resolve()),
        sha256=fontlib.sha256_file(path),
        styles=[subfamily] if subfamily else [],
        licence_url=licence_url,
        source_url=path.resolve().as_uri(),
        retrieved_at="",
    )
    downloaded = not cached_before
    return record, "cache" if cached_before else "catalogo", downloaded, ""


def _catalogue_entries(
    requirement: str, local_families: set[str]
) -> Tuple[List[Tuple[fontlib.FontRecord, str, bool]], List[str], List[str], List[str], bool]:
    """Selected catalogue records, discarded reasons, matched intents, signals, usable."""
    try:
        rows = list(fontstore.catalogue().get("families") or [])
    except Exception as exc:
        return [], [f"catalogo non leggibile: {exc}"], [], [], False
    intent = intent_families(requirement, rows=rows)
    discarded = list(intent["discarded"])
    found: List[Tuple[fontlib.FontRecord, str, bool]] = []
    for name in intent["selected"]:
        if len(found) >= CATALOGUE_LIMIT:
            break
        if _norm(name) in local_families:
            continue
        row = fontstore.find(name)
        if row is None:
            discarded.append(f"{name}: famiglia assente dal catalogo, scartata")
            continue
        record, source, downloaded, reason = _catalogue_record(row)
        if record is None:
            discarded.append(reason)
            continue
        found.append((record, source, downloaded))
    return (
        found,
        discarded,
        list(intent["matched"]),
        list(intent.get("signals") or []),
        True,
    )


# ---------------------------------------------------------------------------
# local files and measurement
# ---------------------------------------------------------------------------


def _resolve_file(
    record: fontlib.FontRecord, allow_network: bool
) -> Tuple[Optional[Path], str, str]:
    """The local file for ``record``, or ``(None, source, reason)``.

    ``fontfetch.fetch`` is the only door to the cache and to the network, and
    ``allow_network`` is passed through untouched: with ``False`` it can only
    return an already cached family or raise ``FONT_OFFLINE``.
    """
    path = Path(str(record.file or ""))
    if path.is_file():
        return path, "", ""
    reason = f"{record.family}: nessun file locale ({path})"
    try:
        from . import fontfetch  # noqa: PLC0415 -- network path, imported on use

        loaded = fontfetch.fetch(record.family, allow_network=allow_network)
    except Exception as exc:  # EDLError, VxError, OSError all mean "skip"
        return None, "", f"{reason}; risoluzione fallita: {exc}"
    for item in loaded.get("files") or ():
        candidate = Path(str(item.get("path") or ""))
        if candidate.is_file():
            source = "bundle" if loaded.get("status") == "bundled" else "cache"
            return candidate, source, ""
    return None, "", reason


def _usable(record: fontlib.FontRecord) -> Tuple[bool, str]:
    """Whether the file can actually be measured, and why when it cannot."""
    try:
        fontchooser.measure(record)
    except (OSError, contract.VxError) as exc:
        return False, f"{record.family}: file non misurabile ({exc})"
    return True, ""


# ---------------------------------------------------------------------------
# specimen
# ---------------------------------------------------------------------------


def _fit_font(
    draw: Any,
    text: str,
    path: Path,
    max_w: int,
    max_h: int,
    start: int,
    image_font: Any,
    minimum: int = 14,
) -> Tuple[Any, Any]:
    """Largest size whose multiline ink fits the box, with its ink bbox."""
    size = max(int(start), minimum)
    font = image_font.truetype(str(path), size)
    box = draw.multiline_textbbox((0, 0), text, font=font, align="center")
    while size > minimum and (box[2] - box[0] > max_w or box[3] - box[1] > max_h):
        size = max(minimum, int(size * 0.92))
        font = image_font.truetype(str(path), size)
        box = draw.multiline_textbbox((0, 0), text, font=font, align="center")
    return font, box


def _render_sample(path: Path, family: str, sample: str, target: Path) -> None:
    """Render the dark specimen card, sample large, family readable below."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover - Pillow is a declared dep
        raise contract.VxError(
            "fontproposal: richiede Pillow per disegnare il campione"
        ) from exc
    try:
        image = Image.new("RGB", (SAMPLE_WIDTH, SAMPLE_HEIGHT), (14, 14, 17))
        draw = ImageDraw.Draw(image)
        for y in range(SAMPLE_HEIGHT):
            shade = 14 + int(18 * y / max(1, SAMPLE_HEIGHT - 1))
            draw.line((0, y, SAMPLE_WIDTH, y), fill=(shade, shade, shade + 3))

        box_w = SAMPLE_WIDTH - 2 * MARGIN_X
        box_h = SAMPLE_BOTTOM - SAMPLE_TOP
        main_font, main_box = _fit_font(
            draw, sample, path, box_w, box_h, int(box_h * 0.82), ImageFont
        )
        ink_w = main_box[2] - main_box[0]
        ink_h = main_box[3] - main_box[1]
        x = (SAMPLE_WIDTH - ink_w) / 2 - main_box[0]
        y = SAMPLE_TOP + (box_h - ink_h) / 2 - main_box[1]
        draw.multiline_text(
            (x, y), sample, font=main_font, fill=(245, 244, 240), align="center"
        )

        draw.line(
            (MARGIN_X, RULE_Y, SAMPLE_WIDTH - MARGIN_X, RULE_Y),
            fill=(62, 62, 70),
            width=2,
        )
        label_font, label_box = _fit_font(
            draw,
            family,
            path,
            box_w,
            SAMPLE_HEIGHT - LABEL_CENTER_Y - 20,
            46,
            ImageFont,
            minimum=20,
        )
        label_w = label_box[2] - label_box[0]
        label_h = label_box[3] - label_box[1]
        draw.multiline_text(
            (
                (SAMPLE_WIDTH - label_w) / 2 - label_box[0],
                LABEL_CENTER_Y - label_h / 2 - label_box[1],
            ),
            family,
            font=label_font,
            fill=(206, 206, 214),
            align="center",
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(target), format="PNG")
    except (OSError, ValueError) as exc:
        raise contract.VxError(
            f"fontproposal: campione non disegnato per {family!r}: {exc}"
        ) from exc


def _sample_name(family: str) -> str:
    stem = _SAFE_NAME.sub("-", str(family or "")).strip("-._") or "font"
    return f"{stem}-sample.png"


# ---------------------------------------------------------------------------
# proposal
# ---------------------------------------------------------------------------


def _validate(workdir: Union[str, Path], count: int, sample: str) -> Path:
    root = Path(workdir).expanduser()
    if root.exists() and not root.is_dir():
        raise contract.VxError(f"fontproposal: workdir non e' una directory: {root}")
    root.mkdir(parents=True, exist_ok=True)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise contract.VxError(
            f"fontproposal: count deve essere un intero >= 1, ricevuto {count!r}"
        )
    if not str(sample or "").strip():
        raise contract.VxError("fontproposal: sample non puo' essere vuoto")
    return root


def _reason_if_none(records: int, ranked: int, discarded: Sequence[str]) -> str:
    if not records:
        return (
            "nessun font disponibile: store, bundle e catalogo non offrono font "
            "utilizzabili"
        )
    if not ranked:
        return (
            "nessun font con licenza commerciale selezionabile: 'personal' e "
            "'unknown' non vengono mai proposti"
        )
    detail = "; ".join(list(discarded)[:5]) or "nessun file locale utilizzabile"
    return f"nessun font utilizzabile per il campione: {detail}"


def propose(
    workdir: Union[str, Path],
    requirement: str,
    energy: float,
    *,
    count: int = 3,
    allow_network: bool = False,
    sample: str = "PROVA",
) -> Dict[str, Any]:
    """Propose up to ``count`` commercial fonts, render them, write the json.

    The pool is local (store + bundle + cache) and, only with
    ``allow_network=True``, a shortlist verified against the catalogue and then
    fetched. Every candidate needs a measurable local file, a commercial licence
    and a distinct family. ``score`` and ``because`` are copied from
    :func:`vx.fontchooser.rank`.
    """
    root = _validate(workdir, count, sample)
    text = str(requirement or "").strip()
    wanted = str(sample).strip()
    rank_text = _rank_requirement(text)

    entries: List[Tuple[fontlib.FontRecord, str, bool]] = []
    discarded: List[str] = []
    for record, source in _load_records():
        path, file_source, reason = _resolve_file(record, bool(allow_network))
        if path is None:
            discarded.append(reason)
            continue
        if str(path) != str(record.file):
            record = fontlib.FontRecord(
                family=record.family,
                licence=record.licence,
                file=str(path),
                sha256=record.sha256,
                styles=list(record.styles),
                licence_url=record.licence_url,
                source_url=record.source_url,
                retrieved_at=record.retrieved_at,
            )
            source = file_source or source
        entries.append((record, source, False))

    pool = "local"
    matched_intents: List[str] = []
    signals: List[str] = []
    if allow_network:
        local_families = {_norm(record.family) for record, _s, _d in entries}
        catalogue, reasons, matched_intents, signals, readable = _catalogue_entries(
            text, local_families
        )
        discarded.extend(reasons)
        entries.extend(catalogue)
        pool = "local+catalogo" if readable else "local"
    if not matched_intents:
        # Anche senza catalogo il brief va letto: il report deve poter dire
        # quale parola ha orientato la proposta (o che nessuna l'ha fatto).
        # quale parola ha orientato la proposta (o che nessuna l'ha fatto).
        declared = intent_families(text)
        matched_intents = list(declared["matched"])
        signals = list(declared.get("signals") or [])

    usable: List[Tuple[fontlib.FontRecord, str, bool]] = []
    for record, source, downloaded in entries:
        ok, why = _usable(record)
        if not ok:
            discarded.append(why)
            continue
        usable.append((record, source, downloaded))

    ranked = (
        fontchooser.rank(
            [record for record, _source, _down in usable],
            requirement=rank_text,
            energy=energy,
            licence_floor="commercial",
        )
        if usable
        else []
    )
    provenance = {
        record.sha256 or record.file: (source, downloaded)
        for record, source, downloaded in usable
    }

    # Il brief deve pesare anche sul pool locale: senza catalogo, il ranking
    # ordina per peso ma non sa che "documentario" vuole un editoriale. Le
    # famiglie dell'intento riconosciuto vanno davanti, il resto resta in coda
    # nell'ordine che il ranking aveva dato.
    preferred = {
        _norm(name)
        for intent in matched_intents
        if intent in INTENT_FAMILIES
        for name in INTENT_FAMILIES[intent]
    }
    if preferred:
        ranked = [item for item in ranked if _norm(item["record"].family) in preferred] + [
            item for item in ranked if _norm(item["record"].family) not in preferred
        ]

    eligible: List[Tuple[Dict[str, Any], str, bool]] = []
    used_families: set[str] = set()
    for item in ranked:
        record = item["record"]
        key = str(record.family or "").strip().lower()
        if not key or key in used_families:
            continue
        if not record.auto_selectable or record.licence != "commercial":
            continue
        used_families.add(key)
        source, downloaded = provenance.get(
            record.sha256 or record.file, ("cache", False)
        )
        eligible.append((item, source, downloaded))

    selected = eligible[:count]
    if allow_network:
        online = ("catalogo", "cache")
        if not any(source in online for _item, source, _down in selected):
            extra = next((entry for entry in eligible if entry[1] in online), None)
            if extra is not None:
                if len(selected) >= count:
                    selected = selected[: count - 1] + [extra]
                else:
                    selected.append(extra)

    fonts_dir = root / "fonts"
    candidates: List[Dict[str, Any]] = []
    used_files: set[str] = set()
    for item, source, downloaded in selected:
        record = item["record"]
        target = fonts_dir / _sample_name(record.family)
        if target.name.lower() in used_files:
            target = fonts_dir / (
                f"{_sample_name(record.family)[:-4]}-{len(used_files)}.png"
            )
        try:
            _render_sample(Path(record.file), record.family, wanted, target)
        except contract.VxError as exc:
            discarded.append(str(exc))
            continue
        used_files.add(target.name.lower())
        candidates.append(
            {
                "family": record.family,
                "licence": record.licence,
                "licence_url": record.licence_url,
                "score": item["score"],
                "because": item["because"],
                "file": str(Path(record.file).resolve()),
                "sample_png": str(target.resolve()),
                "source": source,
                "downloaded": bool(downloaded),
            }
        )

    payload: Dict[str, Any] = {
        "schema": SCHEMA,
        "requirement": text,
        "rank_requirement": rank_text,
        "sample": wanted,
        "network": bool(allow_network),
        "pool": pool,
        "intents": matched_intents,
        "signals": signals,
        "created_utc": _now(),
        "chosen": candidates[0]["family"] if candidates else None,
        "reason_if_none": (
            "" if candidates else _reason_if_none(len(entries), len(ranked), discarded)
        ),
        "discarded": list(dict.fromkeys(discarded)),
        "candidates": candidates,
    }
    target = root / PROPOSAL_NAME
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload
