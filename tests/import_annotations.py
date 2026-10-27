"""Import the T09 annotation module (.xlsx) into a canonical JSON corpus.

Reader: openpyxl when it is already importable (checked, never installed here);
otherwise a stdlib-only reader built on ``zipfile`` + ``xml.etree.ElementTree``
that reads ``xl/workbook.xml``, its relationships, ``sharedStrings.xml`` and the
sheet XML directly. ``reader_name()`` reports which path ran, and the CLI prints
it, so the provenance of a parse is never guessed.

The module is the human judgment layer: the importer validates it and translates
the Italian labels into the canonical tokens declared by ``vx.registry``. Times
are ``mm:ss``; in the output, ``duration_sec``, ``moments[].start/end`` and
``topic_changes[].at`` are integer seconds.

Invariant: ``corpus_annotations.json`` may only be written from a filled module.
An empty module and a ``sample`` corpus are both refused, so a sample can never
be mistaken for real annotations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from vx.registry import ANNOTATION_LEVELS  # noqa: E402

try:
    import openpyxl  # noqa: F401
except ImportError:
    openpyxl = None

FIXTURES = ROOT / "tests" / "fixtures"
_ENV_MODULE = os.environ.get("VX_T09_MODULE")
_TEMPLATE = FIXTURES / "T09_annotation_template.xlsx"
DEFAULT_MODULE = Path(_ENV_MODULE) if _ENV_MODULE else (_TEMPLATE if _TEMPLATE.is_file() else None)
DEFAULT_OUTPUT = FIXTURES / "corpus_annotations.json"

TIME_RE = re.compile(r"^[0-9]{2}:[0-9]{2}$")
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

H_CLIP_ID = "clip_id"
H_FILE = "nome file"
H_DURATION = "durata (mm:ss)"
H_TYPE = "tipo"
H_LANGUAGE = "lingua parlata"
H_HOOK = "hook (primi 3 s)"
H_BPM = "bpm (se noto)"
H_BEAT = "primo battere (s)"
H_PUBLISHABLE = "pubblicabile cosi?"
H_NOTES = "note"
H_COMPLETE = "completa?"

AXIS_HEADERS: Dict[str, str] = {
    "shake": "tremolio",
    "exposure": "esposizione",
    "cast": "dominante colore",
    "noise": "rumore audio",
    "volume": "volume",
    "silences": "silenzi e tempi morti",
    "music": "musica sotto il parlato",
}

M_CLIP_ID = "clip_id"
M_POSITION = "posizione"
M_START = "inizio (mm:ss)"
M_END = "fine (mm:ss)"
M_WHY = "perche e un momento forte"

T_CLIP_ID = "clip_id"
T_AT = "istante (mm:ss)"
T_WHAT = "che cosa cambia"

INCOMPLETE_MARKERS = {"no", "n", "incompleta", "incompleto", "non completa"}
PUBLISHABLE_TOKENS = {"si": "yes", "no": "no", "quasi": "almost"}

CLIP_HEADERS: Tuple[str, ...] = (
    H_CLIP_ID,
    H_FILE,
    H_DURATION,
    H_TYPE,
    H_LANGUAGE,
    *AXIS_HEADERS.values(),
    H_HOOK,
    H_BPM,
    H_BEAT,
    H_PUBLISHABLE,
    H_NOTES,
    H_COMPLETE,
)


class AnnotationError(ValueError):
    """A module row that cannot be trusted, named by row and value."""

    def __init__(
        self,
        row: Optional[int],
        field: str,
        value: Any,
        message: str,
    ) -> None:
        self.row = row
        self.field = field
        self.value = value
        where = f"row {row}" if row is not None else "module"
        super().__init__(f"{message} ({where}, field {field!r}, value {value!r})")


def _norm(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    return " ".join(text.strip().lower().split())


def reader_name() -> str:
    """Which reader will be used: ``openpyxl`` or ``stdlib-xml``."""
    return "openpyxl" if openpyxl is not None else "stdlib-xml"


def level_table() -> Dict[str, Dict[str, str]]:
    """The enforced level table: canonical axis -> {normalized label: token}."""
    return {axis: dict(labels) for axis, labels in ANNOTATION_LEVELS.items()}


def _read_openpyxl(path: Path) -> Dict[str, List[List[Any]]]:
    book = openpyxl.load_workbook(filename=str(path), read_only=True, data_only=True)
    try:
        return {
            sheet.title: [list(row) for row in sheet.iter_rows(values_only=True)]
            for sheet in book.worksheets
        }
    finally:
        book.close()


def _text_of(element: Any) -> str:
    if element is None:
        return ""
    return "".join(node.text or "" for node in element.iter(f"{{{MAIN_NS}}}t"))


def _column_index(reference: str) -> Optional[int]:
    letters = ""
    for char in reference:
        if char.isalpha():
            letters += char
        else:
            break
    if not letters:
        return None
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _sheet_rows(payload: bytes, tree: Any, shared: Sequence[str]) -> List[List[Any]]:
    root = tree.fromstring(payload)
    by_row: Dict[int, List[Any]] = {}
    for row_element in root.iter(f"{{{MAIN_NS}}}row"):
        number = int(row_element.get("r") or (len(by_row) + 1))
        row: List[Any] = []
        for cell in row_element.findall(f"{{{MAIN_NS}}}c"):
            column = _column_index(cell.get("r") or "")
            kind = cell.get("t")
            value: Any = None
            if kind == "inlineStr":
                value = _text_of(cell.find(f"{{{MAIN_NS}}}is"))
            else:
                raw = cell.find(f"{{{MAIN_NS}}}v")
                if raw is not None and raw.text is not None:
                    if kind == "s":
                        position = int(raw.text)
                        value = (
                            shared[position] if 0 <= position < len(shared) else None
                        )
                    elif kind == "b":
                        value = raw.text == "1"
                    else:
                        value = raw.text
            if column is None:
                column = len(row)
            while len(row) <= column:
                row.append(None)
            row[column] = value
        by_row[number] = row
    if not by_row:
        return []
    return [by_row.get(number, []) for number in range(1, max(by_row) + 1)]


def _read_stdlib(path: Path) -> Dict[str, List[List[Any]]]:
    import xml.etree.ElementTree as ElementTree
    import zipfile

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        shared: List[str] = []
        if "xl/sharedStrings.xml" in names:
            shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [_text_of(item) for item in shared_root.iter(f"{{{MAIN_NS}}}si")]
        relationships: Dict[str, str] = {}
        rels_name = "xl/_rels/workbook.xml.rels"
        if rels_name in names:
            rels_root = ElementTree.fromstring(archive.read(rels_name))
            for relationship in rels_root.iter():
                identifier = relationship.get("Id")
                target = relationship.get("Target")
                if identifier and target:
                    relationships[identifier] = target
        workbook_root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        sheets: Dict[str, List[List[Any]]] = {}
        for sheet in workbook_root.iter(f"{{{MAIN_NS}}}sheet"):
            name = sheet.get("name") or ""
            identifier = sheet.get(f"{{{REL_NS}}}id") or ""
            target = relationships.get(identifier)
            if not target:
                continue
            member = target.lstrip("/")
            if member not in names:
                member = "xl/" + member
            if member not in names:
                continue
            sheets[name] = _sheet_rows(archive.read(member), ElementTree, shared)
    return sheets


def read_module(module_path: Path) -> Dict[str, List[List[Any]]]:
    """Raw sheets of the module: ``{sheet name: [row values]}``."""
    path = Path(module_path)
    if not path.is_file():
        raise AnnotationError(None, "module", str(path), "module not found")
    if openpyxl is not None:
        return _read_openpyxl(path)
    return _read_stdlib(path)


def _sheet(sheets: Dict[str, List[List[Any]]], wanted: str) -> List[List[Any]]:
    for name, rows in sheets.items():
        if _norm(name) == _norm(wanted):
            return rows
    raise AnnotationError(None, wanted, None, "sheet not found")


def _header(rows: List[List[Any]], sheet_name: str) -> Tuple[int, Dict[str, int]]:
    for index, row in enumerate(rows[:12]):
        cells = [_norm(cell) for cell in row]
        if H_CLIP_ID in cells:
            columns = {cell: position for position, cell in enumerate(cells) if cell}
            return index, columns
    raise AnnotationError(None, sheet_name, None, "header row with clip_id not found")


def _cell(row: Sequence[Any], columns: Dict[str, int], header: str) -> Any:
    position = columns.get(header)
    if position is None or position >= len(row):
        return None
    return row[position]


def _raw_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_time(raw: Any, row: int, field: str) -> int:
    text = "" if raw is None else str(raw).strip()
    if not TIME_RE.match(text):
        raise AnnotationError(row, field, raw, "time must be mm:ss with two digits")
    minutes = int(text[:2])
    seconds = int(text[3:])
    if seconds > 59:
        raise AnnotationError(row, field, raw, "seconds must be 00..59")
    return minutes * 60 + seconds


def _level(axis: str, raw: Any, row: int, field: str) -> str:
    text = "" if raw is None else str(raw).strip()
    if not text:
        raise AnnotationError(row, field, raw, f"{axis}: missing level")
    token = ANNOTATION_LEVELS[axis].get(_norm(text))
    if token is None:
        raise AnnotationError(row, field, raw, f"{axis}: unknown level")
    return token


def _publishable(raw: Any) -> Optional[str]:
    text = _raw_text(raw)
    if text is None:
        return None
    return PUBLISHABLE_TOKENS.get(_norm(text), text)


def _parse_clips(
    rows: List[List[Any]], skipped: List[Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    header_index, columns = _header(rows, "Clip")
    missing = [header for header in CLIP_HEADERS if header not in columns]
    if missing:
        raise AnnotationError(None, "Clip", missing, "missing required columns")
    clips: Dict[str, Dict[str, Any]] = {}
    for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        clip_id = _norm(_cell(row, columns, H_CLIP_ID))
        if not clip_id or clip_id == "esempio":
            continue
        values = {header: _cell(row, columns, header) for header in CLIP_HEADERS}
        if not any(
            _norm(value)
            for header, value in values.items()
            if header not in (H_CLIP_ID, H_COMPLETE)
        ):
            continue
        if _norm(values[H_COMPLETE]) in INCOMPLETE_MARKERS:
            skipped.append({"clip_id": clip_id, "row": offset, "reason": "incomplete"})
            continue
        if clip_id in clips:
            raise AnnotationError(offset, H_CLIP_ID, clip_id, "duplicate clip_id")
        duration = _parse_time(values[H_DURATION], offset, H_DURATION)
        axes = {
            axis: _level(axis, values[header], offset, header)
            for axis, header in AXIS_HEADERS.items()
        }
        clips[clip_id] = {
            "file": _raw_text(values[H_FILE]),
            "duration_sec": duration,
            "type": _raw_text(values[H_TYPE]),
            "axes": axes,
            "hook": _level("hook", values[H_HOOK], offset, H_HOOK),
            "publishable": _publishable(values[H_PUBLISHABLE]),
            "notes": _raw_text(values[H_NOTES]),
            "moments": [],
            "topic_changes": [],
        }
    return clips


def _parse_moments(
    rows: List[List[Any]],
    clips: Dict[str, Dict[str, Any]],
    durations: Dict[str, int],
    skipped_ids: set[str],
) -> None:
    header_index, columns = _header(rows, "Momenti migliori")
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        clip_id = _norm(_cell(row, columns, M_CLIP_ID))
        if clip_id == "esempio" or clip_id in skipped_ids:
            continue
        start_raw = _cell(row, columns, M_START)
        end_raw = _cell(row, columns, M_END)
        why_raw = _cell(row, columns, M_WHY)
        if not (_norm(start_raw) or _norm(end_raw) or _norm(why_raw)):
            continue
        if not clip_id:
            raise AnnotationError(offset, M_CLIP_ID, None, "moment without clip_id")
        if clip_id not in clips:
            raise AnnotationError(offset, M_CLIP_ID, clip_id, "moment for unknown clip")
        position_text = _norm(_cell(row, columns, M_POSITION))
        if not position_text:
            raise AnnotationError(offset, M_POSITION, None, "moment without position")
        try:
            position = int(position_text)
        except ValueError:
            raise AnnotationError(
                offset, M_POSITION, position_text, "position must be an integer"
            ) from None
        if not 1 <= position <= 3:
            raise AnnotationError(offset, M_POSITION, position, "position must be 1..3")
        start = _parse_time(start_raw, offset, M_START)
        end = _parse_time(end_raw, offset, M_END)
        if end <= start:
            raise AnnotationError(
                offset, M_END, end_raw, "moment end must follow start"
            )
        length = end - start
        if not 3 <= length <= 30:
            raise AnnotationError(
                offset, M_END, end_raw, f"moment length {length}s outside 3..30s"
            )
        if end > durations[clip_id]:
            raise AnnotationError(
                offset, M_END, end_raw, "moment ends outside the clip duration"
            )
        grouped.setdefault(clip_id, []).append(
            {
                "position": position,
                "start": start,
                "end": end,
                "why": _raw_text(why_raw) or "",
                "row": offset,
            }
        )
    for clip_id, moments in grouped.items():
        if len(moments) > 3:
            raise AnnotationError(
                moments[3]["row"], M_POSITION, len(moments), "more than 3 moments"
            )
        positions = [moment["position"] for moment in moments]
        if len(set(positions)) != len(positions):
            duplicate = next(
                moment for moment in moments if positions.count(moment["position"]) > 1
            )
            raise AnnotationError(
                duplicate["row"],
                M_POSITION,
                duplicate["position"],
                "duplicate position",
            )
        ordered = sorted(moments, key=lambda moment: (moment["start"], moment["end"]))
        previous_end: Optional[int] = None
        for moment in ordered:
            if previous_end is not None and moment["start"] < previous_end:
                raise AnnotationError(
                    moment["row"], M_START, moment["start"], "overlapping moments"
                )
            previous_end = moment["end"]
        clips[clip_id]["moments"] = [
            {"start": moment["start"], "end": moment["end"], "why": moment["why"]}
            for moment in ordered
        ]


def _parse_topic_changes(
    rows: List[List[Any]],
    clips: Dict[str, Dict[str, Any]],
    durations: Dict[str, int],
    skipped_ids: set[str],
) -> None:
    header_index, columns = _header(rows, "Cambi di argomento")
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        clip_id = _norm(_cell(row, columns, T_CLIP_ID))
        if clip_id == "esempio" or clip_id in skipped_ids:
            continue
        at_raw = _cell(row, columns, T_AT)
        what_raw = _cell(row, columns, T_WHAT)
        if not (_norm(at_raw) or _norm(what_raw)):
            continue
        if not clip_id:
            raise AnnotationError(
                offset, T_CLIP_ID, None, "topic change without clip_id"
            )
        if clip_id not in clips:
            raise AnnotationError(
                offset, T_CLIP_ID, clip_id, "topic change for unknown clip"
            )
        at = _parse_time(at_raw, offset, T_AT)
        if at > durations[clip_id]:
            raise AnnotationError(
                offset, T_AT, at_raw, "topic change after the clip duration"
            )
        grouped.setdefault(clip_id, []).append(
            {"at": at, "what": _raw_text(what_raw) or "", "row": offset}
        )
    for clip_id, changes in grouped.items():
        ordered = sorted(changes, key=lambda change: (change["at"], change["row"]))
        clips[clip_id]["topic_changes"] = [
            {"at": change["at"], "what": change["what"]} for change in ordered
        ]


def list_clip_ids(module_path: Path) -> List[str]:
    """Every non-ESEMPIO clip_id in the Clip sheet, with or without data."""
    sheets = read_module(Path(module_path))
    rows = _sheet(sheets, "Clip")
    header_index, columns = _header(rows, "Clip")
    identifiers: List[str] = []
    for row in rows[header_index + 1 :]:
        clip_id = _norm(_cell(row, columns, H_CLIP_ID))
        if clip_id and clip_id != "esempio":
            identifiers.append(clip_id)
    return identifiers


def import_module(module_path: Path, sample: bool = False) -> Dict[str, Any]:
    """Parse and validate a module into the canonical corpus object.

    ``sample=True`` adds ``"sample": true`` at the top level; it never changes
    the clip payload, so a sample has the same shape as a real corpus and still
    cannot be confused with one by accident.
    """
    path = Path(module_path)
    sheets = read_module(path)
    skipped: List[Dict[str, Any]] = []
    clips = _parse_clips(_sheet(sheets, "Clip"), skipped)
    durations = {clip_id: clip["duration_sec"] for clip_id, clip in clips.items()}
    skipped_ids = {entry["clip_id"] for entry in skipped}
    _parse_moments(_sheet(sheets, "Momenti migliori"), clips, durations, skipped_ids)
    _parse_topic_changes(
        _sheet(sheets, "Cambi di argomento"), clips, durations, skipped_ids
    )
    data: Dict[str, Any] = {
        "module_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "imported_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "clips": clips,
        "skipped": skipped,
    }
    if sample:
        data["sample"] = True
    return data


def write_corpus(data: Dict[str, Any], out_path: Path) -> Path:
    """Write a validated corpus, refusing empty data and sample masquerades."""
    clips = data.get("clips")
    if not isinstance(clips, dict) or not clips:
        raise AnnotationError(None, "clips", 0, "refusing to write an empty corpus")
    out = Path(out_path)
    if out.name == "corpus_annotations.json" and data.get("sample") is True:
        raise AnnotationError(
            None, "sample", True, "sample data cannot become corpus_annotations.json"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Import the T09 annotation module")
    parser.add_argument(
        "module", nargs="?", default=str(DEFAULT_MODULE) if DEFAULT_MODULE else None
    )
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--dump-levels", action="store_true")
    args = parser.parse_args(argv)
    if args.module is None and not args.sample:
        parser.error("serve il percorso del modulo T09 (argomento o VX_T09_MODULE)")
    if args.dump_levels:
        for axis, labels in level_table().items():
            print(axis, labels)
        return 0
    try:
        data = import_module(Path(args.module), sample=args.sample)
    except AnnotationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(
        f"reader={reader_name()} clips={len(data['clips'])} skipped={len(data['skipped'])}"
    )
    if not data["clips"] and not args.allow_empty:
        print(
            "ERROR: module has no data rows; refusing to write the corpus "
            "(pass --allow-empty only for a deliberate empty fixture)",
            file=sys.stderr,
        )
        return 3
    try:
        out = write_corpus(data, Path(args.out))
    except AnnotationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
