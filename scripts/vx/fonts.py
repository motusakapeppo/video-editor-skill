"""vx.fonts — the bundled font, resolved from the package, never from the machine.

libass substitutes a missing family silently, so the only portable answer is to
ship the font and point ffmpeg at it with ``fontsdir=``.  Everything here is
derived from ``__file__``: no absolute machine path, no assumption that the
font is installed system-wide.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from .winpath import ass_filter_path

__all__ = [
    "package_font_dir",
    "font_file",
    "fontsdir_arg",
    "discover",
]

_FONT_SUFFIXES = (".ttf", ".otf")
_VARIABLE = re.compile(r"\[[^\]]*\]")


def package_font_dir() -> Path:
    """``<repo>/assets/fonts`` — resolved from this file, not from the cwd."""
    return (Path(__file__).resolve().parents[2] / "assets" / "fonts").resolve()


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _family(stem: str) -> str:
    """``Antonio-Bold`` -> ``Antonio``; ``Antonio[wght]`` -> ``Antonio``."""
    base = _VARIABLE.sub("", stem)
    return base.split("-")[0].strip() or stem


def _font_files() -> List[Path]:
    root = package_font_dir()
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.iterdir() if p.is_file() and p.suffix.lower() in _FONT_SUFFIXES
    )


def font_file(name: str = "Antonio", weight: str = "Regular") -> Optional[Path]:
    """The bundled file for `name`/`weight`, or None when it is not shipped.

    A variable font (``Antonio[wght].ttf``) satisfies any weight: the axis is
    there, the file is the same.  An exact ``-Regular``/``-Bold`` file wins.
    """
    wanted = _norm(name)
    if not wanted:
        return None
    candidates = [
        p
        for p in _font_files()
        if _norm(_family(p.stem)) == wanted or _norm(p.stem).startswith(wanted)
    ]
    if not candidates:
        return None

    weight_norm = _norm(weight)
    for path in candidates:
        if weight_norm and weight_norm in _norm(path.stem):
            return path
    for path in candidates:
        if _VARIABLE.search(path.stem) or "wght" in path.stem.lower():
            return path
    return candidates[0]


def fontsdir_arg() -> str:
    """Escaped, quoted ``fontsdir`` value for the ffmpeg filtergraph."""
    return ass_filter_path(package_font_dir())


def discover() -> List[Dict[str, str]]:
    """Every bundled font as ``{"family": ..., "file": ...}``."""
    out: List[Dict[str, str]] = []
    for path in _font_files():
        out.append({"family": _family(path.stem), "file": str(path)})
    return out
