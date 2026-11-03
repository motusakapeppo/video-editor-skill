"""vx.fontfetch -- one catalogue family on demand: licence verified, bytes pinned.

The catalogue names 2,065 Google Fonts families with the directory each lives in
and the licence the repository declares. It does not ship their bytes: six
families are bundled, every other one is fetched only when an operator asks, under
the licence the catalogue already recorded.

The rules, in the order they are applied:

1. **Nothing implicit.** Importing this module opens no socket, reads no
   catalogue, and imports neither ``urllib.request`` nor ``vx.fontapis``.
   :func:`fetch` is the only entry point that may touch the network, and only
   with ``allow_network=True``. A render never calls it (D002).
2. **Cached beats wired.** A family bundled in ``assets/fonts`` or recorded in
   ``fetched.json`` and present under ``cache/`` is returned from disk, with the
   network never consulted.
3. **Licence before bytes.** A row whose ``licenced`` is not ``true`` is refused
   with ``FONT_LICENCE_MISSING`` before any download is attempted.
4. **Bytes are pinned.** A download is accepted only when its first four bytes
   are a TrueType/OpenType magic, and its sha256 is recorded in
   ``assets/fonts/fetched.json``. :func:`check` re-reads every recorded file and
   refuses on drift (``FONT_SHA_MISMATCH``), never warns.

Refusal codes: ``FONT_UNKNOWN_FAMILY``, ``FONT_LICENCE_MISSING``,
``FONT_OFFLINE``, ``FONT_DOWNLOAD_FAILED``, ``FONT_BAD_MAGIC``, ``FONT_NO_FILES``,
``FONT_SHA_MISMATCH``, ``FONT_CATALOGUE_MISSING``, ``FONT_CATALOGUE_INVALID``.
Each is raised as :class:`vx.edl3.EDLError`, so the caller reads ``exc.code``.

Reused machinery, never a second implementation: the binary GET is
:func:`vx.fontapis.fetch_bytes`; the catalogue, the cache, the digests and
``fetched.json`` are :mod:`vx.fontstore`; the render-report provenance is
:mod:`vx.fontreport`. The public API stays here.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from . import fontstore
from .fontreport import annotate, render_section, report_entry
from .fontstore import catalogue, fetched_rows, find, record, refuse

__all__ = [
    "BASE_URL",
    "ENV_BASE_URL",
    "annotate",
    "catalogue",
    "check",
    "fetch",
    "fetched_rows",
    "find",
    "main",
    "record",
    "render_section",
    "report_entry",
]

#: Where the catalogue's ``<directory>/<file>`` pairs are read from.
BASE_URL = "https://raw.githubusercontent.com/google/fonts/main"
ENV_BASE_URL = "VX_FONT_BASE_URL"


def _base_url(base_url: Optional[str] = None) -> str:
    return str(base_url or os.environ.get(ENV_BASE_URL) or BASE_URL).rstrip("/")


def _font_names(row: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for raw in row.get("files") or []:
        name = str(raw or "").strip()
        if not name or Path(name).suffix.lower() not in fontstore.FONT_SUFFIXES:
            continue
        if "/" in name or "\\" in name or ".." in name:
            raise refuse("FONT_CATALOGUE_INVALID", f"nome file non ammesso: {name!r}")
        if name not in out:
            out.append(name)
    return out


def _download(url: str, timeout: Optional[float]) -> bytes:
    from .fontapis import FETCH_TIMEOUT_S, FetchError, fetch_bytes  # noqa: PLC0415

    limit = float(FETCH_TIMEOUT_S if timeout is None else timeout)
    try:
        return fetch_bytes(url, timeout=limit)
    except FetchError as exc:
        raise refuse("FONT_DOWNLOAD_FAILED", str(exc), url)
    except OSError as exc:
        raise refuse("FONT_DOWNLOAD_FAILED", f"lettura fallita: {exc}", url)


def _download_family(
    row: Mapping[str, Any],
    name: str,
    root: Path,
    *,
    base_url: Optional[str],
    timeout: Optional[float],
) -> None:
    names = _font_names(row)
    if not names:
        raise refuse("FONT_NO_FILES", f"{name}: il catalogo non elenca file .ttf/.otf")
    licence = str(row.get("licence") or "").strip()
    if not fontstore.licence_known(licence):
        raise refuse(
            "FONT_LICENCE_MISSING",
            f"{name}: licenza {licence or 'assente'!r} non registrabile; nessun download",
            str(row.get("directory") or ""),
        )
    base = _base_url(base_url)
    directory = str(row.get("directory") or "").strip().strip("/")
    payloads: List[Dict[str, Any]] = []
    for file_name in names:
        url = f"{base}/{directory}/{file_name}"
        data = _download(url, timeout)
        if not fontstore.magic_ok(data):
            raise refuse(
                "FONT_BAD_MAGIC",
                f"{name}/{file_name}: i byte non iniziano con un magic TrueType/OpenType",
                url,
            )
        payloads.append({"name": file_name, "data": data})
    cache = fontstore.cache_dir(root, name)
    for payload in payloads:
        fontstore.write_bytes(cache / payload["name"], payload["data"])
    fontstore.register(
        {
            "family": name,
            "licence": licence,
            "directory": directory,
            "files": [
                {
                    "name": payload["name"],
                    "sha256": hashlib.sha256(payload["data"]).hexdigest(),
                    "bytes": len(payload["data"]),
                }
                for payload in payloads
            ],
            "source": base,
            "licence_url": (
                f"https://github.com/google/fonts/tree/main/{directory}"
                if directory
                else "https://github.com/google/fonts"
            ),
        },
        root=root,
    )


def fetch(
    family: Any,
    *,
    dest: Optional[Union[str, Path]] = None,
    allow_network: bool = False,
    catalogue_path: Optional[Union[str, Path]] = None,
    base_url: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Return one family's record, downloading it only when asked and allowed.

    Decisions in order: bundled, or recorded and intact (no network) -> licence
    (refuse before bytes) -> network gate (``FONT_OFFLINE``) -> download, magic,
    sha256, ``cache/<family>/``, ``fetched.json`` row.
    """
    root = fontstore.store_root(dest)
    loaded = fontstore.record(family, dest=dest, catalogue_path=catalogue_path)
    name = loaded["family"]
    if loaded["status"] == "bundled":
        return loaded
    row = fontstore.find(name, catalogue_path=catalogue_path) or {}
    cache = fontstore.cache_dir(root, name)
    if loaded["status"] == "cached":
        problems = fontstore.verify(root, name)
        if problems:
            raise refuse("FONT_SHA_MISMATCH", "; ".join(problems), str(cache))
        return loaded
    if row.get("licenced") is not True:
        raise refuse(
            "FONT_LICENCE_MISSING",
            f"{name}: il catalogo dichiara licenced=false (licence "
            f"{row.get('licence') or 'n/d'}); nessun download",
            str(row.get("directory") or ""),
        )
    if not allow_network:
        raise refuse(
            "FONT_OFFLINE",
            f"{name}: non e' impacchettato ne' in cache, e la rete e' spenta per "
            f"default (D002). cache: {cache}",
            str(cache),
        )
    _download_family(row, name, root, base_url=base_url, timeout=timeout)
    return fontstore.record(family, dest=dest, catalogue_path=catalogue_path)


def check(dest: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Refuse the cache when any recorded file drifted: a mismatch is not a warning."""
    root = fontstore.store_root(dest)
    rows = fontstore.fetched_rows(root)
    files = 0
    for row in rows:
        family = str(row.get("family") or "")
        problems = fontstore.verify(root, family)
        if problems:
            raise refuse("FONT_SHA_MISMATCH", "; ".join(problems), str(fontstore.cache_dir(root, family)))
        files += len(row.get("files") or [])
    return {
        "schema": fontstore.SCHEMA,
        "families": len(rows),
        "files": files,
        "ok": True,
        "dest": str(root),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``--check [dest]`` | ``--record family`` | ``--fetch family [--network]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    dest = os.environ.get("VX_FONT_DEST") or None
    try:
        if "--check" in args:
            print(json.dumps(check(dest), indent=2))
            return 0
        if "--record" in args:
            print(json.dumps(record(args[args.index("--record") + 1], dest=dest), indent=2))
            return 0
        if "--fetch" in args:
            family = args[args.index("--fetch") + 1]
            network = "--network" in args
            print(json.dumps(fetch(family, dest=dest, allow_network=network), indent=2))
            return 0
    except Exception as exc:
        print(f"{getattr(exc, 'code', 'ERROR')}: {exc}", file=sys.stderr)
        return 1
    print(__doc__.strip().splitlines()[0])
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
