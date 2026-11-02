"""vx.brandjson — estrarre ``brand.json`` da una pagina letta, con provenienza.

Il documento non e' un riassunto: e' un atto di attribuzione. Ogni campo che c'e'
porta **da dove viene**:

* i colori, con la quota di occorrenze e il file (o l'inline) che li dichiara;
* il logo, con il selettore che l'ha trovato;
* i font, con il CSS che li dichiara (``font-family`` o ``@font-face``);
* i testi, con il selettore;
* i prezzi, con il selettore o il dato strutturato (JSON-LD).

Un colore senza provenienza fa fallire :func:`validate`: senza il posto da cui
viene, un colore e' un'opinione. Lo schema e' ``vx.brandjson/1``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .contract import VxError

SCHEMA = "vx.brandjson/1"
MAX_COLORS = 12
MAX_TEXTS = 20

_HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_FONT = re.compile(r"font-family\s*:\s*([^;}\n]+)", re.IGNORECASE)
_FACE = re.compile(r"@font-face\s*\{[^}]*\}", re.IGNORECASE)
_TAG = re.compile(r"<(h1|h2|h3|p|li|button|span)\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_PRICE = re.compile(r"(?:€|\$|EUR|USD)\s?([0-9]+(?:[.,][0-9]{2})?)|([0-9]+(?:[.,][0-9]{2})?)\s?(?:€|\$|EUR|USD)", re.IGNORECASE)
_JSONLD = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")


class BrandJsonError(VxError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def normalise_hex(value: str) -> str:
    text = value.strip().lstrip("#").lower()
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    return "#" + text


def _text(raw: str) -> str:
    return re.sub(r"\s+", " ", _TAGS.sub(" ", raw)).strip()


# --------------------------------------------------------------------------- #
# colori: occorrenze, quota, sorgente
# --------------------------------------------------------------------------- #
def _colors(page: str, styles: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    sources: Dict[str, Dict[str, Any]] = {}

    def note(hex_value: str, source: Dict[str, Any]) -> None:
        key = normalise_hex(hex_value)
        counts[key] = counts.get(key, 0) + 1
        sources.setdefault(key, source)

    for match in _HEX.finditer(page or ""):
        note(match.group(1), {"kind": "inline", "file": "", "selector": "html"})
    for asset in styles:
        url = str(asset.get("url") or "")
        css = str(asset.get("text") or "")
        for match in _HEX.finditer(css):
            note(match.group(1), {"kind": "css", "file": url, "selector": "stylesheets"})

    total = sum(counts.values()) or 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:MAX_COLORS]
    return [
        {
            "hex": key,
            "count": count,
            "share": round(count / total, 6),
            "provenance": dict(sources[key]),
        }
        for key, count in ordered
    ]


def _fonts(styles: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    for asset in styles:
        url = str(asset.get("url") or "")
        css = str(asset.get("text") or "")
        for match in _FONT.finditer(css):
            for family in match.group(1).split(","):
                name = family.strip().strip("'\"").strip()
                if not name or name.startswith("var(") or name.lower() in ("sans-serif", "serif", "monospace", "system-ui"):
                    continue
                found.setdefault(
                    name,
                    {"family": name, "provenance": {"kind": "css", "file": url, "selector": "font-family"}},
                )
        for block in _FACE.finditer(css):
            family = re.search(r"font-family\s*:\s*([^;}]+)", block.group(0), re.IGNORECASE)
            if family:
                name = family.group(1).strip().strip("'\"").strip()
                if name:
                    found[name] = {
                        "family": name,
                        "provenance": {"kind": "css", "file": url, "selector": "@font-face"},
                    }
    return [found[name] for name in sorted(found)]


def _logo(page: str, base_url: str) -> Optional[Dict[str, Any]]:
    patterns = (
        (r'<img[^>]+alt=["\'][^"\']*logo[^"\']*["\'][^>]+src=["\']([^"\']+)["\']', "img[alt*=logo]"),
        (r'<img[^>]+src=["\'][^"\']*logo[^"\']*["\']', "img[src*=logo]"),
        (r'<link[^>]+rel=["\'][^"\']*icon[^"\']*["\'][^>]+href=["\']([^"\']+)["\']', "link[rel*=icon]"),
    )
    for pattern, selector in patterns:
        match = re.search(pattern, page or "", re.IGNORECASE)
        if match:
            raw = match.group(1) if match.groups() else match.group(0)
            if match.groups():
                return {
                    "url": _absolute(raw, base_url),
                    "provenance": {"kind": "selector", "selector": selector, "file": ""},
                }
    return None


def _absolute(url: str, base_url: str) -> str:
    import urllib.parse

    return urllib.parse.urljoin(base_url, url)


def _texts(page: str) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    for tag, inner in _TAG.findall(page or ""):
        value = _text(inner)
        if not value:
            continue
        found.append(
            {
                "tag": tag.lower(),
                "text": value[:280],
                "provenance": {"kind": "selector", "selector": tag.lower(), "file": ""},
            }
        )
        if len(found) >= MAX_TEXTS:
            break
    return found


def _prices(page: str) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    for match in _PRICE.finditer(_TAGS.sub(" ", page or "")):
        raw = match.group(1) or match.group(2) or ""
        amount = raw.replace(".", "").replace(",", ".") if "," in raw else raw
        try:
            value = float(amount)
        except ValueError:
            continue
        currency = "EUR" if "€" in match.group(0).upper() or "EUR" in match.group(0).upper() else "USD"
        found.append(
            {
                "amount": round(value, 2),
                "currency": currency,
                "provenance": {"kind": "text", "selector": "price", "file": ""},
            }
        )
    for block in _JSONLD.finditer(page or ""):
        import json

        try:
            data = json.loads(block.group(1))
        except ValueError:
            continue
        for offer in _walk_offers(data):
            found.append(
                {
                    "amount": float(offer.get("price")),
                    "currency": str(offer.get("priceCurrency") or "EUR"),
                    "provenance": {"kind": "structured", "selector": "application/ld+json", "file": ""},
                }
            )
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for item in found:
        key = (item["amount"], item["currency"], item["provenance"]["kind"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _walk_offers(node: Any) -> List[Mapping[str, Any]]:
    out: List[Mapping[str, Any]] = []
    if isinstance(node, Mapping):
        if "price" in node:
            try:
                float(node["price"])
            except (TypeError, ValueError):
                pass
            else:
                out.append(node)
        for value in node.values():
            out.extend(_walk_offers(value))
    elif isinstance(node, list):
        for value in node:
            out.extend(_walk_offers(value))
    return out


# --------------------------------------------------------------------------- #
# costruzione e validazione
# --------------------------------------------------------------------------- #
def build(record: Mapping[str, Any]) -> Dict[str, Any]:
    page = str(record.get("page") or "")
    url = str(record.get("url") or "")
    styles = [asset for asset in record.get("assets") or [] if asset.get("suffix") == ".css"]
    return {
        "schema": SCHEMA,
        "source": {
            "url": url,
            "origin": str(record.get("origin") or ""),
            "title": str(record.get("title") or ""),
        },
        "colors": _colors(page, styles),
        "logo": _logo(page, url),
        "fonts": _fonts(styles),
        "texts": _texts(page),
        "prices": _prices(page),
        "attribution": {
            "page_rules": record.get("robots") or {},
            "requests": list(record.get("requests") or []),
        },
    }


def _provenance_problem(value: Any, where: str) -> List[str]:
    if not isinstance(value, Mapping):
        return [f"{where}: provenance missing"]
    kind = str(value.get("kind") or "")
    if kind not in ("inline", "css", "selector", "text", "structured"):
        return [f"{where}: provenance.kind {kind!r} is not declared"]
    if kind in ("css", "inline") and not (value.get("file") or value.get("selector")):
        return [f"{where}: provenance names neither file nor selector"]
    if kind in ("selector", "text") and not value.get("selector"):
        return [f"{where}: provenance names no selector"]
    return []


def validate(document: Mapping[str, Any]) -> List[str]:
    """Return every contract violation; an empty list means the document is legal."""
    if not isinstance(document, Mapping):
        return ["brand.json non e' un oggetto"]
    errors: List[str] = []
    if document.get("schema") != SCHEMA:
        errors.append(f"schema deve essere {SCHEMA}")
    source = document.get("source")
    if not isinstance(source, Mapping) or not str(source.get("url") or "").startswith(("http://", "https://")):
        errors.append("source.url mancante o non http(s)")

    colors = document.get("colors")
    if not isinstance(colors, list):
        errors.append("colors non e' una lista")
        colors = []
    for index, color in enumerate(colors):
        where = f"colors[{index}]"
        if not isinstance(color, Mapping):
            errors.append(f"{where}: non e' un oggetto")
            continue
        if not re.fullmatch(r"#[0-9a-f]{6}", str(color.get("hex") or "")):
            errors.append(f"{where}.hex non e' #rrggbb")
        share = color.get("share")
        if not isinstance(share, (int, float)) or isinstance(share, bool) or not 0.0 <= float(share) <= 1.0:
            errors.append(f"{where}.share fuori da [0, 1]")
        errors.extend(_provenance_problem(color.get("provenance"), f"{where}.provenance"))

    logo = document.get("logo")
    if logo is not None:
        if not isinstance(logo, Mapping):
            errors.append("logo non e' un oggetto")
        else:
            errors.extend(_provenance_problem(logo.get("provenance"), "logo.provenance"))

    for index, font in enumerate(document.get("fonts") or []):
        where = f"fonts[{index}]"
        if not isinstance(font, Mapping) or not font.get("family"):
            errors.append(f"{where}: family mancante")
            continue
        errors.extend(_provenance_problem(font.get("provenance"), f"{where}.provenance"))

    for index, item in enumerate(document.get("texts") or []):
        where = f"texts[{index}]"
        if not isinstance(item, Mapping) or not item.get("text"):
            errors.append(f"{where}: text mancante")
            continue
        errors.extend(_provenance_problem(item.get("provenance"), f"{where}.provenance"))

    for index, item in enumerate(document.get("prices") or []):
        where = f"prices[{index}]"
        if not isinstance(item, Mapping):
            errors.append(f"{where}: non e' un oggetto")
            continue
        amount = item.get("amount")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or float(amount) < 0.0:
            errors.append(f"{where}.amount non valido")
        errors.extend(_provenance_problem(item.get("provenance"), f"{where}.provenance"))
    return errors


def write(document: Mapping[str, Any], destination) -> Any:
    import json
    from pathlib import Path

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(dict(document), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destination


__all__ = [
    "BrandJsonError",
    "MAX_COLORS",
    "MAX_TEXTS",
    "SCHEMA",
    "build",
    "normalise_hex",
    "validate",
    "write",
]
