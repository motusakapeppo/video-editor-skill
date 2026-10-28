"""vx.fontreport -- how a font is described in a render report.

A render report names every family the frames were drawn with, together with the
licence it was used under, the source its bytes came from, and the sha256 of the
exact file. Without that trail a frame cannot be defended after the fact, and a
fetched family is indistinguishable from a guessed one.

Read-only by construction: nothing in this module fetches or writes, so wiring
it into a render cannot reach the network (D002).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from . import fontstore

__all__ = ["annotate", "render_section", "report_entry"]


def report_entry(
    family: Any,
    *,
    file: Optional[str] = None,
    dest: Optional[Union[str, Path]] = None,
    catalogue_path: Optional[Union[str, Path]] = None,
) -> Optional[Dict[str, Any]]:
    """``{family, licence, source, sha256, file}`` for one family, or None.

    ``sha256`` is the digest of the file the render used, else of the family's
    first known file. An unknown family answers None: a report is not a place
    to raise.
    """
    try:
        loaded = fontstore.record(family, dest=dest, catalogue_path=catalogue_path)
    except Exception as exc:
        if getattr(exc, "code", "") == "FONT_UNKNOWN_FAMILY":
            return None
        raise
    files = loaded.get("files") or []
    if not files:
        return None
    chosen = files[0]
    if file:
        chosen = next((item for item in files if str(item.get("name")) == str(file)), chosen)
    return {
        "family": loaded["family"],
        "licence": loaded["licence"],
        "source": loaded["source"],
        "sha256": chosen["sha256"],
        "file": chosen["name"],
    }


def render_section(
    fonts_used: Union[str, Path, Iterable[Any]],
    *,
    dest: Optional[Union[str, Path]] = None,
    catalogue_path: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Report entries for the fonts one render actually used, in order, unique."""
    items: Sequence[Any] = (
        [fonts_used] if isinstance(fonts_used, (str, Path)) else list(fonts_used or [])
    )
    out: List[Dict[str, Any]] = []
    for item in items:
        if not item:
            continue
        path = Path(str(item))
        if path.suffix.lower() in fontstore.FONT_SUFFIXES:
            family, file_name = fontstore.family_of_stem(path.stem), path.name
        else:
            family, file_name = str(item), None
        entry = report_entry(family, file=file_name, dest=dest, catalogue_path=catalogue_path)
        if entry is not None and entry not in out:
            out.append(entry)
    return out


def annotate(
    report: Dict[str, Any],
    fonts_used: Union[str, Path, Iterable[Any]],
    *,
    dest: Optional[Union[str, Path]] = None,
    catalogue_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Put font provenance into ``report["render"]["fonts"]`` and return the report.

    Called by the render report writer once the frames exist. A report whose
    ``render`` section is not an object is left untouched rather than replaced.
    """
    if not isinstance(report, dict):
        return report
    entries = render_section(fonts_used, dest=dest, catalogue_path=catalogue_path)
    section = report.get("render")
    if isinstance(section, dict) and entries:
        section["fonts"] = entries
    return report
