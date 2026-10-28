"""vx.fontresolve — turn a template's ``font`` value into a usable, licensed face.

One order, one vocabulary, one record. Bundled first (OFL-1.1, source
``bundled``, no provider constructed, so no socket); then the project's font
system (only ``commercial`` usable, ``personal`` refused unless an explicit
approval object names the family, ``unknown`` discarded); then the template's
default bundled id with ``declared_fallback=True`` and a one-line reason.

The record is a plain ``dict`` so it survives ``json.dump`` into
``capture.json`` and into the render report: ``requested``, ``family``,
``licence``, ``licence_detail``, ``source``, ``path``, ``id``,
``declared_fallback``, ``code``, ``approved``, ``reason``,
``registered_family``, ``page_font``. ``page_font`` is always a bundled id: a
resolved non-bundled face is registered in the page (see :mod:`vx.motion`).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from . import contract
from . import fonts

# allow: SIZE_OK - one resolver, one record schema, one offline provider: the
# licence order, the refusal codes and the record shape are one contract, and
# splitting them would scatter the decision across files that must agree.

__all__ = [
    "BUNDLED_IDS",
    "BUNDLED_LICENCE",
    "CODES",
    "DEFAULT_FAMILY",
    "CatalogueProvider",
    "bundled_file",
    "bundled_ids",
    "bundled_record",
    "fallback_record",
    "is_bundled",
    "resolve",
    "template_default",
    "usage",
]

BUNDLED_IDS: Tuple[str, ...] = (
    "antonio",
    "inter",
    "bebasneue",
    "montserrat",
    "playfairdisplay",
    "jetbrainsmono",
)
BUNDLED_LICENCE = "OFL-1.1"
DEFAULT_FAMILY = "antonio"
CODES = ("FONT_UNLICENSED", "FONT_UNKNOWN", "FONT_FALLBACK")

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_COMMERCIAL = frozenset(
    {
        "commercial",
        "ofl",
        "ofl11",
        "openfontlicense",
        "openfontlicence",
        "apache",
        "apache20",
        "mit",
        "cc0",
        "cc010",
        "publicdomain",
        "bsd3clause",
        "isc",
    }
)
_PERSONAL = frozenset({"personal", "personaluseonly", "noncommercial"})
_IDS: Optional[Dict[str, str]] = None


def _norm(text: Any) -> str:
    return _NON_ALNUM.sub("", str(text or "").lower())


def _record(**fields: Any) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "requested": "",
        "family": "",
        "licence": "",
        "licence_detail": "",
        "source": "",
        "path": None,
        "id": "",
        "declared_fallback": False,
        "code": "",
        "approved": False,
        "reason": "",
        "registered_family": "",
        "page_font": "",
    }
    record.update(fields)
    return record


def bundled_ids() -> Dict[str, str]:
    """``id -> family`` for the six ids, read from the files actually shipped."""
    global _IDS
    if _IDS is None:
        found: Dict[str, str] = {}
        for entry in fonts.discover():
            key = _norm(entry.get("family"))
            if key in BUNDLED_IDS:
                found.setdefault(key, str(entry.get("family") or ""))
        _IDS = found
    missing = [name for name in BUNDLED_IDS if name not in _IDS]
    if missing:
        raise contract.VxError(
            "fontresolve: famiglie impacchettate mancanti in assets/fonts: "
            + ", ".join(missing)
        )
    return dict(_IDS)


def is_bundled(family: Any) -> bool:
    return _norm(family) in bundled_ids()


def bundled_file(family: Any) -> Optional[Path]:
    """The file that paints ``family``: the regular face, else the first."""
    ids = bundled_ids()
    key = _norm(family)
    if key not in ids:
        return None
    path = fonts.font_file(ids[key], "Regular")
    if path is not None and path.is_file():
        return path
    for entry in fonts.discover():
        if _norm(entry.get("family")) == key and Path(str(entry.get("file"))).is_file():
            return Path(str(entry["file"]))
    return None


def bundled_record(
    family: Any,
    *,
    requested: Any = None,
    declared_fallback: bool = False,
    code: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    """The record for a shipped file: OFL-1.1, source ``bundled``, real path."""
    ids = bundled_ids()
    key = _norm(family)
    if key not in ids:
        raise contract.VxError(
            f"fontresolve: {family!r} non e' un font impacchettato; "
            f"validi: {', '.join(BUNDLED_IDS)}"
        )
    path = bundled_file(key)
    if path is None:
        raise contract.VxError(
            f"fontresolve: il font impacchettato {ids[key]!r} non ha un file leggibile"
        )
    return _record(
        requested=str(requested if requested is not None else family or ""),
        family=ids[key],
        licence=BUNDLED_LICENCE,
        licence_detail=BUNDLED_LICENCE,
        source="bundled",
        path=str(path),
        id=key,
        declared_fallback=bool(declared_fallback),
        code=str(code or ""),
        reason=str(reason or ""),
        page_font=key,
    )


def template_default(template: Mapping[str, Any]) -> str:
    """The template's declared ``font`` default when it is a bundled id."""
    params = template.get("params") if isinstance(template, Mapping) else None
    declaration = (params or {}).get("font") if isinstance(params, Mapping) else None
    default = declaration.get("default") if isinstance(declaration, Mapping) else None
    text = str(default or "").strip()
    return text if is_bundled(text) else DEFAULT_FAMILY


def fallback_record(
    requested: Any,
    *,
    template_default: Any = DEFAULT_FAMILY,
    code: str = "FONT_FALLBACK",
    reason: str = "",
) -> Dict[str, Any]:
    """The declared substitute. No substitution, no fallback flag and no code."""
    wanted = _norm(template_default)
    if wanted not in bundled_ids():
        wanted = DEFAULT_FAMILY
    declared = _norm(requested) != wanted
    return bundled_record(
        wanted,
        requested=requested,
        declared_fallback=declared,
        code=code if declared else "",
        reason=reason,
    )


def usage(licence: Any) -> str:
    """Project licence class for a source licence string (D001)."""
    key = _norm(licence)
    if key in _COMMERCIAL:
        return "commercial"
    if key in _PERSONAL:
        return "personal"
    return "unknown"


class CatalogueProvider:
    """The offline font system: ``catalogue.json`` plus the fetched cache.

    ``lookup`` opens no socket unless ``allow_network=True``, and then only
    through :func:`vx.fontfetch.fetch`, which is a separate declared step. A
    render always asks with ``allow_network=False``.
    """

    name = "fontstore"

    def __init__(
        self,
        *,
        allow_network: bool = False,
        dest: Optional[str | Path] = None,
        catalogue_path: Optional[str | Path] = None,
    ) -> None:
        self.allow_network = bool(allow_network)
        self.dest = dest
        self.catalogue_path = catalogue_path

    def _files(self, loaded: Mapping[str, Any]) -> str:
        for item in loaded.get("files") or ():
            path = str(item.get("path") or "")
            if path:
                return path
        return ""

    def _known(self, family: str) -> Optional[Dict[str, Any]]:
        from . import fontstore  # noqa: PLC0415 -- keeps this module import-cheap
        from .edl3 import EDLError  # noqa: PLC0415

        try:
            loaded = fontstore.record(
                family, dest=self.dest, catalogue_path=self.catalogue_path
            )
        except (EDLError, contract.VxError, OSError, ValueError):
            return None
        return {
            "family": str(loaded.get("family") or family),
            "licence": str(loaded.get("licence") or ""),
            "path": self._files(loaded),
            "source": str(loaded.get("source") or self.name),
        }

    def lookup(self, family: str) -> Optional[Dict[str, Any]]:
        known = self._known(family)
        if known is None or known["path"] or not self.allow_network:
            return known
        from . import fontfetch  # noqa: PLC0415 -- network path, declared
        from .edl3 import EDLError  # noqa: PLC0415

        try:
            loaded = fontfetch.fetch(
                family,
                dest=self.dest,
                allow_network=True,
                catalogue_path=self.catalogue_path,
            )
        except (EDLError, contract.VxError, OSError, ValueError):
            return known
        known["path"] = self._files(loaded) or known["path"]
        known["family"] = str(loaded.get("family") or known["family"])
        return known


def _entry(provider: Any, family: str) -> Optional[Dict[str, Any]]:
    """Ask a provider with ``lookup()`` or, failing that, with ``search()``."""
    lookup = getattr(provider, "lookup", None)
    if callable(lookup):
        found = lookup(family)
        return dict(found) if isinstance(found, Mapping) else None
    search = getattr(provider, "search", None)
    if not callable(search):
        return None
    wanted = _norm(family)
    for candidate in search(family, limit=10) or ():
        data = candidate if isinstance(candidate, Mapping) else vars(candidate)
        name = str(data.get("family") or "")
        key = _norm(name)
        if not key or not (key == wanted or wanted in key or key in wanted):
            continue
        return {
            "family": name,
            "licence": str(data.get("licence") or ""),
            "licence_detail": str(data.get("licence_detail") or data.get("licence") or ""),
            "path": str(data.get("path") or data.get("local_file") or ""),
            "source": str(data.get("source") or getattr(provider, "name", "provider")),
        }
    return None


def _approval(approval: Any, family: str) -> Tuple[bool, str]:
    """Whether an explicit approval object names this family, and who signed it."""
    if approval is None or approval is False:
        return False, ""
    if isinstance(approval, Mapping):
        named = _norm(approval.get("family"))
        if named not in ("", "*") and named != _norm(family):
            return False, ""
        by = approval.get("by") or approval.get("approved_by")
        return True, str(by or approval.get("note") or approval.get("reason") or "").strip()
    named = _norm(getattr(approval, "family", ""))
    if named not in ("", "*") and named != _norm(family):
        return False, ""
    by = getattr(approval, "by", "") or getattr(approval, "approved_by", "")
    return True, str(by or "").strip()


def _resolved(requested: str, found: Mapping[str, Any], licence: str, **extra: Any):
    detail = str(found.get("licence_detail") or found.get("licence") or "")
    path = str(found.get("path") or "")
    return _record(
        requested=requested,
        family=str(found.get("family") or requested),
        licence=licence,
        licence_detail=detail or licence,
        source=str(found.get("source") or "font system"),
        path=str(Path(path).resolve()) if path else None,
        approved=bool(extra.pop("approved", False)),
        reason=str(extra.pop("reason", "")),
        **extra,
    )


def resolve(
    family: Any,
    *,
    template_default: Any = DEFAULT_FAMILY,
    allow_network: bool = False,
    provider: Any = None,
    approval: Any = None,
) -> Dict[str, Any]:
    """Resolve ``family`` to a record; bundled first, fallback declared last."""
    requested = str(family or "").strip()
    ids = bundled_ids()
    if _norm(requested) in ids:
        return bundled_record(_norm(requested), requested=requested)

    lookup = provider if provider is not None else CatalogueProvider(
        allow_network=allow_network
    )
    found = _entry(lookup, requested) if requested else None
    if found is None:
        return fallback_record(
            requested,
            template_default=template_default,
            code="FONT_UNKNOWN",
            reason=(
                f"family {requested!r} is neither bundled nor known to the font system"
                if requested
                else "no font family was requested"
            ),
        )

    detail = str(found.get("licence_detail") or found.get("licence") or "")
    licence = usage(found.get("licence") or detail)
    name = str(found.get("family") or requested)
    if licence == "personal":
        allowed, signer = _approval(approval, name)
        if not allowed:
            return fallback_record(
                requested,
                template_default=template_default,
                code="FONT_UNLICENSED",
                reason=(
                    f"{name!r} is under a personal licence; a paid job needs an "
                    "explicit approval object naming the family"
                ),
            )
        return _resolved(
            requested,
            found,
            "personal",
            approved=True,
            reason="personal licence approved explicitly" + (f" by {signer}" if signer else ""),
        )
    if licence == "unknown":
        return fallback_record(
            requested,
            template_default=template_default,
            code="FONT_UNKNOWN",
            reason=(
                f"{name!r} carries licence {detail or 'nothing'!r}: nobody verified "
                "its terms, so it cannot enter a job"
            ),
        )
    if not str(found.get("path") or "") or not Path(str(found["path"])).is_file():
        return fallback_record(
            requested,
            template_default=template_default,
            code="FONT_FALLBACK",
            reason=(
                f"{name!r} is commercially licensed but no local file is registered; "
                "a render never downloads, so the bundled face is declared instead"
            ),
        )
    return _resolved(requested, found, "commercial")
