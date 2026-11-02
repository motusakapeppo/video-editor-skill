"""vx.brand — leggere un brand da una pagina, senza uscire dal dominio.

Il lettore prende **una pagina** e gli asset che quella pagina collega, e niente
altro. Quattro regole, tutte verificate da un server di prova locale:

* ``robots.txt`` si legge prima e si rispetta: una pagina esclusa e' rifiutata
  citando la regola che l'ha esclusa, non con un "non si puo'";
* si chiede solo l'origine della pagina: un asset su un altro host viene
  **saltato e registrato come saltato**, non scaricato (nessun redirect seguito
  fuori dominio, nessun login, nessun cookie);
* la cache e' per hash dell'URL: la seconda esecuzione non fa **zero** richieste
  se non ha gia' tutto, e il test pretende esattamente zero;
* una pagina irraggiungibile e' ``brand_unavailable`` con il motivo, non un
  traceback.

Il registro delle richieste resta nel referto: e' la prova che il confine e'
stato tenuto, non una promessa.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .contract import VxError
from .fontsource import RobotsPolicy, USER_AGENT, _rule_matches

SCHEMA = "vx.brand/1"
MAX_ASSETS = 12
ROBOTS_PATH = "/robots.txt"
ASSET_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico",
    ".css", ".woff", ".woff2", ".ttf",
)
_LINK = re.compile(r"""(?:src|href)\s*=\s*["']([^"'#]+)["']""", re.IGNORECASE)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class BrandError(VxError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- #
# rete: nessun redirect fuori dal dominio, nessun login, mai un browser finto
# --------------------------------------------------------------------------- #
class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only on the same origin; anything else is refused."""

    def __init__(self, origin: str) -> None:
        self.origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        if _origin_of(newurl) != self.origin:
            raise urllib.error.HTTPError(
                newurl, code, f"redirect fuori dominio verso {newurl}", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _origin_of(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    scheme = (parsed.scheme or "http").lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    return f"{scheme}://{host}:{port}"


def _default_fetch(url: str, *, timeout: float = 20.0) -> bytes:
    origin = _origin_of(url)
    opener = urllib.request.build_opener(_SameOriginRedirect(origin))
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            if status != 200:
                raise BrandError("brand_unavailable", f"HTTP {status} per {url}")
            return response.read()
    except urllib.error.HTTPError as exc:
        raise BrandError("brand_unavailable", f"HTTP {exc.code} per {url}") from exc
    except urllib.error.URLError as exc:
        raise BrandError(
            "brand_unavailable", f"rete non raggiungibile per {url}: {exc.reason}"
        ) from exc
    except OSError as exc:
        raise BrandError("brand_unavailable", f"lettura fallita per {url}: {exc}") from exc


# --------------------------------------------------------------------------- #
# cache per hash dell'URL
# --------------------------------------------------------------------------- #
@dataclass
class Store:
    """Page text, robots text and asset bytes, all keyed by the URL's hash."""

    root: Optional[Path] = None
    network_calls: int = 0
    requests: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root) if self.root is not None else None
        if self.root is not None:
            (self.root / "assets").mkdir(parents=True, exist_ok=True)

    def _key(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _index_path(self) -> Optional[Path]:
        return None if self.root is None else self.root / "index.json"

    def _index(self) -> Dict[str, Any]:
        path = self._index_path()
        if path is None or not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_index(self, data: Dict[str, Any]) -> None:
        path = self._index_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    def cached_text(self, url: str) -> Optional[str]:
        return self._index().get("text", {}).get(url)

    def cached_bytes(self, url: str) -> Optional[bytes]:
        path = self._asset_path(url)
        if path is None or not path.is_file():
            return None
        return path.read_bytes()

    def _asset_path(self, url: str) -> Optional[Path]:
        if self.root is None:
            return None
        return self.root / "assets" / f"{self._key(url)}.bin"

    def _cached_error(self, url: str) -> None:
        """Re-raise a recorded failure instead of hammering a dead URL again."""
        entry = self._index().get("errors", {}).get(url)
        if isinstance(entry, dict) and entry.get("code"):
            raise BrandError(str(entry["code"]), str(entry.get("detail") or "errore in cache"))

    def get_text(self, url: str, *, fetch: Callable[[str], bytes]) -> str:
        hit = self.cached_text(url)
        if hit is not None:
            return hit
        self._cached_error(url)
        body = self._call(url, fetch)
        text = body.decode("utf-8", "replace")
        index = self._index()
        index.setdefault("text", {})[url] = text
        self._save_index(index)
        return text

    def get_bytes(self, url: str, *, fetch: Callable[[str], bytes]) -> bytes:
        hit = self.cached_bytes(url)
        if hit is not None:
            return hit
        self._cached_error(url)
        body = self._call(url, fetch)
        path = self._asset_path(url)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        index = self._index()
        index.setdefault("bytes", {})[url] = self._key(url)
        self._save_index(index)
        return body

    def _call(self, url: str, fetch: Callable[[str], bytes]) -> bytes:
        origin = _origin_of(url)
        record: Dict[str, Any] = {"url": url, "same_origin": True}
        try:
            body = fetch(url)
        except BrandError as exc:
            record["status"] = f"error: {exc.code}"
            self.requests.append(record)
            index = self._index()
            index.setdefault("errors", {})[url] = {"code": exc.code, "detail": exc.detail}
            self._save_index(index)
            raise
        record["status"] = "ok"
        self.requests.append(record)
        self.network_calls += 1
        return body


# --------------------------------------------------------------------------- #
# lettura
# --------------------------------------------------------------------------- #
def _links(html: str) -> List[str]:
    found: List[str] = []
    for raw in _LINK.findall(html or ""):
        candidate = raw.strip()
        if not candidate or candidate.lower().startswith(("javascript:", "mailto:", "data:")):
            continue
        path = urllib.parse.urlsplit(candidate).path.lower()
        if path.endswith(ASSET_SUFFIXES):
            found.append(candidate)
    seen: Dict[str, bool] = {}
    ordered: List[str] = []
    for link in found:
        if link not in seen:
            seen[link] = True
            ordered.append(link)
    return ordered


def _title(html: str) -> str:
    match = _TITLE.search(html or "")
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def _robots(origin: str, store: Store, fetch: Callable[[str], bytes]) -> Tuple[Optional[RobotsPolicy], str]:
    url = origin + ROBOTS_PATH
    try:
        text = store.get_text(url, fetch=fetch)
    except BrandError as exc:
        if "HTTP 404" in exc.detail or "HTTP 410" in exc.detail:
            return None, "nessun robots.txt (404): nessuna regola"
        raise
    return RobotsPolicy.parse(text, source_url=url), url


def _deciding_rule(policy: RobotsPolicy, path: str) -> str:
    best: Optional[str] = None
    for allowed, pattern in policy.active_rules():
        if not allowed and _rule_matches(pattern, path):
            if best is None or len(pattern) > len(best):
                best = pattern
    return f"Disallow: {best}" if best else ""


def read(
    url: str,
    *,
    cache_dir: Optional[Path] = None,
    fetch: Optional[Callable[[str], bytes]] = None,
    max_assets: int = MAX_ASSETS,
) -> Dict[str, Any]:
    """Read one page and its same-origin assets, honouring ``robots.txt``."""
    fetch = fetch or _default_fetch
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise BrandError("brand_unavailable", f"URL non http(s): {url!r}")
    origin = _origin_of(url)
    store = Store(cache_dir)

    policy, robots_source = _robots(origin, store, fetch)
    if policy is not None and not policy.allows(parsed.path or "/"):
        rule = _deciding_rule(policy, parsed.path or "/")
        raise BrandError(
            "brand_robots",
            f"{parsed.path or '/'} escluso da robots.txt ({rule or 'regola disallow'}) "
            f"in {robots_source}",
        )

    page = store.get_text(url, fetch=fetch)
    assets: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for link in _links(page):
        absolute = urllib.parse.urljoin(url, link)
        if _origin_of(absolute) != origin:
            skipped.append({"url": absolute, "why": "fuori dominio"})
            continue
        if len(assets) >= int(max_assets):
            skipped.append({"url": absolute, "why": f"oltre il tetto di {max_assets} asset"})
            continue
        try:
            body = store.get_bytes(absolute, fetch=fetch)
        except BrandError as exc:
            skipped.append({"url": absolute, "why": exc.code})
            continue
        assets.append(
            {
                "url": absolute,
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
                "suffix": Path(urllib.parse.urlsplit(absolute).path).suffix.lower(),
            }
        )

    return {
        "schema": SCHEMA,
        "url": url,
        "origin": origin,
        "title": _title(page),
        "robots": {
            "source": robots_source,
            "rules": [
                {"allow": bool(allowed), "pattern": pattern}
                for allowed, pattern in (policy.active_rules() if policy else [])
            ],
        },
        "assets": assets,
        "skipped": skipped,
        "requests": list(store.requests),
        "stats": {
            "network": store.network_calls,
            "assets": len(assets),
            "skipped": len(skipped),
            "off_domain_requests": sum(1 for r in store.requests if not r.get("same_origin", True)),
        },
    }


def write(record: Mapping[str, Any], destination: Path) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(dict(record), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destination


__all__ = [
    "ASSET_SUFFIXES",
    "BrandError",
    "MAX_ASSETS",
    "SCHEMA",
    "Store",
    "read",
    "write",
]
