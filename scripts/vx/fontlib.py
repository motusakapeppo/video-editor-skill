"""vx.fontlib — a licence-aware font catalogue, independent from the renderer.

A font is not a file name: it is a record. The record carries the licence that
was read from the source, the URL that licence was read at, the page the file
came from, when it was retrieved, the family, the style hints and the sha256 of
the bytes. Without that trail a font cannot be used in a paid job, because
nobody can prove afterwards which terms applied at download time.

The catalogue is deliberately conservative:

* ``commercial`` is the only licence the selector may pick on its own;
* ``personal`` is proposed to the operator but never auto-selected;
* ``unknown`` is rejected and stays visible in the matrix as rejected.

Nothing here renders, touches ffmpeg or knows about subtitle styles. The render
wiring is a separate task; this module only answers "which file, under which
licence, may this job use".

Schema ``vx.fontlib/1.0``::

    {"schema": "vx.fontlib/1.0",
     "records": {"<sha256>": {family, licence, licence_url, source_url,
                              retrieved_at, file, sha256, styles}}}

``file`` is absolute inside a ``FontRecord`` and relative to the store root
inside ``index.json``, so the index survives moving the cache directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from . import contract
from . import fonts

__all__ = [
    "AUTO_SELECTABLE",
    "LICENCES",
    "MATRIX_NAME",
    "SCHEMA",
    "FontRecord",
    "Store",
    "check_licence",
    "job_fonts_dir",
    "select",
    "sha256_file",
]

SCHEMA = "vx.fontlib/1.0"
INDEX_NAME = "index.json"
MATRIX_NAME = "LICENSES_MATRIX.md"

#: The only values a record may carry. Anything else is a bug, not a default.
LICENCES = ("commercial", "personal", "unknown")

#: The only licence the selector may choose without asking the operator.
AUTO_SELECTABLE = ("commercial",)

_FONT_SUFFIXES = (".ttf", ".otf")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _now() -> str:
    """UTC timestamp, second precision, explicit offset — never a naive local."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: str | Path) -> str:
    """sha256 of a file, streamed: a font is small, but the rule is the rule."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_licence(licence: Any) -> str:
    """Return the normalised licence or fail naming the accepted values."""
    text = str(licence or "").strip().lower()
    if text not in LICENCES:
        raise contract.VxError(
            f"licence inattesa: {licence!r}; valide: {', '.join(LICENCES)}"
        )
    return text


def _norm(text: str) -> str:
    return _NON_ALNUM.sub("", str(text or "").lower())


@dataclass
class FontRecord:
    """One font file plus the licence trail that makes it usable.

    ``file`` is the absolute path of the file on this machine. A record is
    read-only by convention: build a new one instead of mutating fields that
    are part of its identity.
    """

    family: str
    licence: str
    file: str
    sha256: str
    styles: List[str] = field(default_factory=list)
    licence_url: str = ""
    source_url: str = ""
    retrieved_at: str = ""

    def __post_init__(self) -> None:
        self.family = str(self.family or "").strip()
        if not self.family:
            raise contract.VxError("FontRecord: family vuota")
        self.licence = check_licence(self.licence)
        self.file = str(self.file or "")
        self.sha256 = str(self.sha256 or "").strip().lower()
        if self.sha256 and not _SHA256_HEX.match(self.sha256):
            raise contract.VxError(
                f"FontRecord: sha256 non esadecimale a 64 caratteri: {self.sha256!r}"
            )
        if isinstance(self.styles, str):
            self.styles = [
                part.strip() for part in re.split(r"[;,]", self.styles) if part.strip()
            ]
        else:
            self.styles = [
                str(style).strip() for style in self.styles or [] if str(style).strip()
            ]
        self.licence_url = str(self.licence_url or "").strip()
        self.source_url = str(self.source_url or "").strip()
        self.retrieved_at = str(self.retrieved_at or "").strip()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "family": self.family,
            "licence": self.licence,
            "licence_url": self.licence_url,
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
            "file": self.file,
            "sha256": self.sha256,
            "styles": list(self.styles),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FontRecord":
        if not isinstance(data, Mapping):
            raise contract.VxError(
                f"FontRecord: atteso un oggetto, trovato {type(data)!r}"
            )
        return cls(
            family=data.get("family", ""),
            licence=data.get("licence", ""),
            file=data.get("file", ""),
            sha256=data.get("sha256", ""),
            styles=list(data.get("styles") or []),
            licence_url=data.get("licence_url", ""),
            source_url=data.get("source_url", ""),
            retrieved_at=data.get("retrieved_at", ""),
        )

    @property
    def auto_selectable(self) -> bool:
        """True only for a licence that permits a paid job without asking anyone."""
        return self.licence in AUTO_SELECTABLE


class Store:
    """The user-level font cache: ``<root>/<sha256[:16]>/<name>`` + ``index.json``.

    A store is a plain directory. Two records may share a family (different
    weights); the sha256 of the bytes is the identity, so adding the same file
    twice is one record, not two.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self.index_path = self.root / INDEX_NAME
        self._records: Optional[Dict[str, FontRecord]] = None

    # -- persistence -----------------------------------------------------

    def _load(self) -> Dict[str, FontRecord]:
        if self._records is not None:
            return self._records
        records: Dict[str, FontRecord] = {}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, Mapping):
            raw = data.get("records")
            if isinstance(raw, Mapping):
                for key, entry in raw.items():
                    try:
                        record = FontRecord.from_dict(entry)
                    except contract.VxError:
                        # A corrupt entry is skipped, never fatal: the files on
                        # disk can be re-registered, the catalogue keeps working.
                        continue
                    record.file = self._absolute(record.file)
                    records[record.sha256 or str(key)] = record
        self._records = records
        return records

    def _absolute(self, file: str) -> str:
        path = Path(str(file or ""))
        return str(path if path.is_absolute() else (self.root / path))

    def _relative(self, record: FontRecord) -> Dict[str, Any]:
        data = record.to_dict()
        path = Path(record.file)
        if path.is_absolute():
            try:
                data["file"] = (
                    path.resolve().relative_to(self.root.resolve()).as_posix()
                )
            except ValueError:
                data["file"] = str(path)
        return data

    def _save(self) -> None:
        records = self._load()
        payload = {
            "schema": SCHEMA,
            "records": {
                sha: self._relative(record) for sha, record in sorted(records.items())
            },
        }
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=INDEX_NAME + ".", suffix=".tmp", dir=str(self.root)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, self.index_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- writes ----------------------------------------------------------

    def add_file(
        self,
        path: str | Path,
        *,
        family: str,
        licence: str,
        licence_url: str = "",
        source_url: str = "",
        styles: Optional[Sequence[str]] = None,
        retrieved_at: str = "",
    ) -> FontRecord:
        """Register a file, copy it under ``<root>/<sha[:16]>/`` and return it.

        Deduplication is by content: the same bytes always map to one record.
        On a duplicate the first registration wins for ``family`` and for an
        already-known licence; ``unknown`` may be refined to a known value, and
        empty URLs/styles are filled. A stored licence is never silently
        downgraded or upgraded — reclassifying means removing the record.
        """
        source = Path(path).expanduser()
        if not source.is_file():
            raise contract.VxError(f"add_file: file inesistente: {source}")
        digest = sha256_file(source)
        requested = check_licence(licence)
        records = self._load()

        existing = records.get(digest)
        merged: Optional[FontRecord] = None
        if existing is not None:
            merged = self._merge(
                existing,
                family=str(family or "").strip(),
                licence=requested,
                licence_url=str(licence_url or ""),
                source_url=str(source_url or ""),
                styles=list(styles or []),
            )
            if Path(merged.file).is_file():
                if merged.to_dict() != existing.to_dict():
                    records[digest] = merged
                    self._save()
                return merged

        target_dir = self.root / digest[:16]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        if not (target.is_file() and sha256_file(target) == digest):
            fd, tmp = tempfile.mkstemp(
                prefix=source.name + ".", suffix=".tmp", dir=str(target_dir)
            )
            os.close(fd)
            try:
                shutil.copyfile(source, tmp)
                os.replace(tmp, target)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

        base = merged if merged is not None else None
        record = FontRecord(
            family=base.family if base else family,
            licence=base.licence if base else requested,
            file=str(target.resolve()),
            sha256=digest,
            styles=list(base.styles) if base else list(styles or []),
            licence_url=(base.licence_url if base else "") or licence_url,
            source_url=(base.source_url if base else "") or source_url,
            retrieved_at=(base.retrieved_at if base else retrieved_at) or _now(),
        )
        records[digest] = record
        self._save()
        return record

    @staticmethod
    def _merge(
        existing: FontRecord,
        *,
        family: str,
        licence: str,
        licence_url: str,
        source_url: str,
        styles: Iterable[str],
    ) -> FontRecord:
        final_licence = existing.licence
        if existing.licence == "unknown" and licence != "unknown":
            final_licence = licence
        merged_styles = list(existing.styles)
        for style in styles:
            text = str(style).strip()
            if text and text not in merged_styles:
                merged_styles.append(text)
        return FontRecord(
            family=existing.family or family,
            licence=final_licence,
            file=existing.file,
            sha256=existing.sha256,
            styles=merged_styles,
            licence_url=existing.licence_url or licence_url,
            source_url=existing.source_url or source_url,
            retrieved_at=existing.retrieved_at,
        )

    # -- reads -----------------------------------------------------------

    def all(self) -> List[FontRecord]:
        return sorted(self._load().values(), key=lambda r: (r.family.lower(), r.sha256))

    def get(self, sha: str) -> Optional[FontRecord]:
        """Lookup by full sha256, or by a unique prefix of at least 8 chars."""
        records = self._load()
        key = str(sha or "").strip().lower()
        if not key:
            return None
        if key in records:
            return records[key]
        if len(key) >= 8:
            matches = [
                record for digest, record in records.items() if digest.startswith(key)
            ]
            if len(matches) == 1:
                return matches[0]
        return None

    def find(self, family: str) -> List[FontRecord]:
        """Every record whose family matches, accent/punctuation-insensitive."""
        wanted = _norm(family)
        if not wanted:
            return []
        out = [
            record
            for record in self._load().values()
            if wanted in _norm(record.family) or _norm(record.family) in wanted
        ]
        return sorted(out, key=lambda r: (r.family.lower(), r.sha256))

    def licences_matrix(self) -> List[Dict[str, Any]]:
        """One row per record, sorted, ready for ``LICENSES_MATRIX.md``."""
        rows = []
        for record in self._load().values():
            rows.append(
                {
                    "family": record.family,
                    "styles": list(record.styles),
                    "licence": record.licence,
                    "auto_selectable": record.auto_selectable,
                    "licence_url": record.licence_url,
                    "source_url": record.source_url,
                    "retrieved_at": record.retrieved_at,
                    "sha256": record.sha256,
                    "file": record.file,
                }
            )
        return sorted(
            rows, key=lambda row: (row["licence"], row["family"].lower(), row["sha256"])
        )

    def write_matrix(self, path: str | Path) -> Path:
        """Write the human-readable licence matrix; return the path written."""
        target = Path(path).expanduser()
        rows = self.licences_matrix()
        lines = [
            "# Font licences",
            "",
            "Every font this catalogue knows, with the licence read from its source.",
            "`commercial` is the only value the selector may choose on its own;",
            "`personal` can be proposed to the operator but never auto-selected;",
            "`unknown` is rejected.",
            "",
            "| Family | Styles | Licence | Auto | Licence URL | Source | Retrieved | SHA256 | File |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in rows:
            lines.append(
                "| {family} | {styles} | {licence} | {auto} | {licence_url} |"
                " {source_url} | {retrieved_at} | {sha256} | {file} |".format(
                    family=_md(row["family"]),
                    styles=_md(", ".join(row["styles"])),
                    licence=row["licence"],
                    auto="yes" if row["auto_selectable"] else "no",
                    licence_url=_md(row["licence_url"]),
                    source_url=_md(row["source_url"]),
                    retrieved_at=_md(row["retrieved_at"]),
                    sha256=row["sha256"],
                    file=_md(row["file"]),
                )
            )
        lines.append("")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines), encoding="utf-8")
        return target


def _md(text: Any) -> str:
    """Escape the two characters that would break a Markdown table."""
    return str(text or "").replace("|", "\\|").replace("\n", " ")


# -- per-job directory ---------------------------------------------------


def _inside(root: Path, candidate: Path) -> bool:
    """True when ``candidate`` resolves inside ``root`` (or is ``root``)."""
    resolved = candidate.resolve()
    base = root.resolve()
    return resolved == base or base in resolved.parents


def job_fonts_dir(
    workdir: str | Path,
    records: Sequence[FontRecord],
    *,
    bundled: bool = True,
) -> Path:
    """Build ``<workdir>/fonts/`` from the chosen records plus the bundle.

    Idempotent: a destination whose sha256 already matches the source is left
    untouched, so a second call changes no mtime. Every write is checked to be
    inside ``workdir``; a record whose ``file`` is relative is refused because
    there is no base to resolve it against that would not be a guess.
    """
    root = Path(workdir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    target = root / "fonts"
    if not _inside(root, target):
        raise contract.VxError(f"job_fonts_dir: {target} esce da {root}")
    target.mkdir(parents=True, exist_ok=True)

    sources: List[Path] = []
    for record in records:
        candidate = Path(str(record.file or ""))
        if not str(record.file or "").strip():
            raise contract.VxError(
                f"job_fonts_dir: record {record.family!r} senza file"
            )
        if not candidate.is_absolute():
            raise contract.VxError(
                f"job_fonts_dir: file relativo non risolvibile per {record.family!r}: "
                f"{record.file!r}"
            )
        if not candidate.is_file():
            raise contract.VxError(
                f"job_fonts_dir: file mancante per {record.family!r}: {candidate}"
            )
        sources.append(candidate)

    if bundled:
        bundle = fonts.package_font_dir()
        if bundle.is_dir():
            sources.extend(
                sorted(
                    path
                    for path in bundle.iterdir()
                    if path.is_file() and path.suffix.lower() in _FONT_SUFFIXES
                )
            )

    copied: Dict[str, str] = {}  # sha256 -> destination name
    used: Dict[str, str] = {}  # lower-case destination name -> sha256
    for source in sources:
        digest = sha256_file(source)
        if digest in copied:
            continue
        name = source.name
        if name.lower() in used and used[name.lower()] != digest:
            name = f"{source.stem}-{digest[:8]}{source.suffix}"
        destination = target / name
        if not _inside(root, destination):
            raise contract.VxError(f"job_fonts_dir: {destination} esce da {root}")
        if not (destination.is_file() and sha256_file(destination) == digest):
            fd, tmp = tempfile.mkstemp(
                prefix=name + ".", suffix=".tmp", dir=str(target)
            )
            os.close(fd)
            try:
                shutil.copyfile(source, tmp)
                os.replace(tmp, destination)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        copied[digest] = name
        used[name.lower()] = digest

    return target


# -- deterministic selection ---------------------------------------------

_WIDTH_WORDS = (
    ("condensed", ("condensed", "narrow", "compressed")),
    ("extended", ("extended", "expanded", "wide")),
)
_STEM_WORDS = (
    (4, ("black", "ultra", "heavy", "extrabold")),
    (3, ("bold", "semibold", "demibold")),
    (2, ("medium", "semi", "demi")),
    (0, ("thin", "light", "hairline")),
    (1, ("regular", "book", "normal")),
)
#: requirement token -> stem index it asks for
_STEM_TOKENS = {
    "thin": 0,
    "light": 0,
    "hairline": 0,
    "regular": 1,
    "book": 1,
    "medium": 2,
    "semi": 3,
    "demibold": 3,
    "semibold": 3,
    "bold": 3,
    "heavy": 4,
    "black": 4,
    "ultra": 4,
}
#: requirement token -> width class it asks for
_WIDTH_TOKENS = {
    "condensed": "condensed",
    "narrow": "condensed",
    "compressed": "condensed",
    "extended": "extended",
    "expanded": "extended",
    "wide": "extended",
}
_DISPLAY_WORDS = ("display", "headline", "poster", "title")
_DISPLAY_TOKENS = ("display", "headline", "poster")
_FAMILY_TOKENS = ("sans", "serif", "mono", "slab", "script", "handwritten", "rounded")


def _hints(record: FontRecord) -> str:
    """Everything a record declares about its shape, normalised for matching.

    Declared ``styles`` come first because they are a statement, while the
    file stem is only a convention: ``Antonio-Bold`` says Bold, and so does a
    style hint ``Bold``. Both are read, in the same lower-case text.
    """
    stem = Path(record.file).stem if record.file else ""
    return " ".join([record.family, stem, *record.styles]).lower()


def _width_class(hints: str) -> str:
    for name, words in _WIDTH_WORDS:
        if any(word in hints for word in words):
            return name
    return "normal"


def _stem_index(hints: str) -> int:
    for index, words in _STEM_WORDS:
        if any(word in hints for word in words):
            return index
    return 1


def _tokens(text: str) -> List[str]:
    return [token for token in _NON_ALNUM.split(str(text or "").lower()) if token]


def select(
    records: Sequence[FontRecord],
    *,
    requirement: str,
    energy: float,
) -> Optional[FontRecord]:
    """Pick one font, deterministically, and only under a commercial licence.

    The rule, in order:

    1. ``personal`` and ``unknown`` never enter the ranking. If no commercial
       record exists the answer is ``None`` — the operator decides, the code
       does not guess a licence.
    2. Each record gets a *width class* (condensed / normal / extended) and a
       *stem index* (0 light, 1 regular, 2 medium, 3 bold, 4 black), read from
       the declared ``styles`` and then from the family and file stem.
    3. Each requirement token that names a width class the record has scores
       ``+3``; each token that names the record's stem index scores ``+3``;
       ``display``/``headline``/``poster`` scores ``+3`` when the record says it
       is one; ``sans``/``serif``/``mono``/``slab``/``script``/``handwritten``/
       ``rounded`` scores ``+2`` when the record carries it.
    4. ``energy`` — clamped to ``[0, 1]`` — maps to a target stem index
       ``round(energy * 4)``; the record loses one point per step it is away
       from that target. High energy therefore prefers black/bold, low energy
       prefers light/regular, without forbidding the rest.
    5. The winner is the highest score; ties break on lower-case family, then
       sha256. Same inputs, same answer on every machine.
    """
    candidates = [record for record in records if record.licence in AUTO_SELECTABLE]
    if not candidates:
        return None
    try:
        value = float(energy)
    except (TypeError, ValueError):
        raise contract.VxError(f"select: energy non numerica: {energy!r}")
    value = min(1.0, max(0.0, value))
    target_stem = int(round(value * 4))
    tokens = _tokens(requirement)

    ranked = []
    for record in candidates:
        hints = _hints(record)
        width = _width_class(hints)
        stem = _stem_index(hints)
        score = 0
        for token in tokens:
            if token in _WIDTH_TOKENS and _WIDTH_TOKENS[token] == width:
                score += 3
            if token in _STEM_TOKENS and _STEM_TOKENS[token] == stem:
                score += 3
            if token in _DISPLAY_TOKENS and any(
                word in hints for word in _DISPLAY_WORDS
            ):
                score += 3
            if token in _FAMILY_TOKENS and token in hints:
                score += 2
        score -= abs(stem - target_stem)
        ranked.append((-score, record.family.lower(), record.sha256, record))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    return ranked[0][3]
