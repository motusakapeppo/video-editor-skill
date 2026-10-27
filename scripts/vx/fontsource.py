"""vx.fontsource — where fonts come from, and under which licence.

Two providers, one shape: ``probe()`` says whether the source may be used at
all, ``search(query, limit=...)`` returns :class:`Candidate` objects that carry
the licence *and the page it was read on*. Neither provider downloads a font
file: the candidate exposes ``download_url`` and stops there.

The online provider is gated by the ``font_search`` feature flag (default off)
and by ``robots.txt``. It never impersonates a browser, never rotates a user
agent and never bypasses a disallow: if the search path is disallowed the
answer is ``Health(available=False, reason="robots_disallowed")`` and the search
endpoint is not requested at all. Dafont's own ``robots.txt`` disallows
``/search.php`` — the code says so, and the operator reads that answer.

Fetcher injection is mandatory so tests never touch the network::

    DafontProvider(fetcher=lambda url: canned[url], cache=PageCache(tmp), ...)

The production fetcher is :func:`fetch_text`, a thin ``urllib`` wrapper with a
declared User-Agent, a timeout, and a hard failure on any non-200 response.
"""

from __future__ import annotations

import html as html_mod
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from . import contract
from . import features
from . import fontlib

__all__ = [
    "CACHE_TTL_S",
    "DEFAULT_BASE_URL",
    "FETCH_TIMEOUT_S",
    "ROBOTS_TTL_S",
    "USER_AGENT",
    "Candidate",
    "DafontProvider",
    "FetchError",
    "Health",
    "LocalFolderProvider",
    "PageCache",
    "Provider",
    "RobotsPolicy",
    "candidate_from_page",
    "classify_licence",
    "fetch_text",
    "parse_results",
]

#: Identify the tool honestly. No ``Mozilla``, no browser impersonation.
USER_AGENT = "vx-font-provider/1.0 (video-editor skill; licence-aware font lookup)"

DEFAULT_BASE_URL = "https://www.dafont.com"
SEARCH_PATH = "/search.php"
ROBOTS_PATH = "/robots.txt"
FETCH_TIMEOUT_S = 20.0
#: A page cache entry older than this is refetched. Declared, not magic.
CACHE_TTL_S = 7 * 24 * 60 * 60
#: Robots rules change rarely, but a week is too long for a permission.
ROBOTS_TTL_S = 24 * 60 * 60

_FONT_SUFFIXES = (".ttf", ".otf")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_STYLE_WORDS = (
    "thin",
    "hairline",
    "light",
    "regular",
    "book",
    "medium",
    "semibold",
    "demibold",
    "bold",
    "extrabold",
    "black",
    "heavy",
    "ultra",
    "condensed",
    "narrow",
    "compressed",
    "extended",
    "expanded",
    "wide",
    "italic",
    "oblique",
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm(text: str) -> str:
    return _NON_ALNUM.sub("", str(text or "").lower())


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@dataclass
class Health:
    """Whether a source may be used, and why. ``reason`` is a stable code.

    Codes: ``ok``, ``feature_off``, ``robots_disallowed``, ``missing_dir``,
    ``unreachable``, ``fetch_error``.
    """

    name: str
    available: bool
    reason: str = "ok"
    level: str = "ok"
    last_success: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "reason": self.reason,
            "level": self.level,
            "last_success": self.last_success,
        }


def _health(name: str, available: bool, reason: str, last_success: str = "") -> Health:
    if available:
        level = "ok"
    elif reason in ("feature_off", "robots_disallowed"):
        level = "blocked"
    else:
        level = "error"
    return Health(
        name=name,
        available=available,
        reason=reason,
        level=level,
        last_success=last_success,
    )


class FetchError(contract.VxError):
    """A fetch failed: no network, non-200, or a body that could not be read."""


# ---------------------------------------------------------------------------
# production fetcher — the only place urllib appears
# ---------------------------------------------------------------------------


def fetch_text(url: str, *, timeout: float = FETCH_TIMEOUT_S) -> str:
    """GET ``url`` as text. Non-200 is a hard failure, never a silent body."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                try:
                    status = response.getcode()
                except AttributeError:
                    status = 200
            if int(status) != 200:
                raise FetchError(f"HTTP {status} per {url}")
            body = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} per {url}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"rete non raggiungibile per {url}: {exc.reason}") from exc
    except OSError as exc:
        raise FetchError(f"lettura fallita per {url}: {exc}") from exc
    try:
        return body.decode(charset, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# robots.txt — parse, honour, never work around
# ---------------------------------------------------------------------------


class RobotsPolicy:
    """A parsed ``robots.txt``: ``Allow``/``Disallow`` with longest-match rules.

    Group selection follows the published convention: the most specific
    user-agent token that occurs in our User-Agent wins, otherwise ``*``.
    Matching is on the path only (query strings are not part of ``robots``
    scope); ``*`` is a wildcard and a trailing ``$`` anchors the end.
    """

    def __init__(
        self,
        groups: Sequence[Mapping[str, Any]],
        *,
        source_url: str = "",
    ) -> None:
        self.groups = [dict(group) for group in groups]
        self.source_url = source_url

    @classmethod
    def parse(cls, text: str, *, source_url: str = "") -> "RobotsPolicy":
        groups: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        fresh_agent_block = True
        for raw in str(text or "").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                if current is None or not fresh_agent_block:
                    current = {"agents": [], "rules": []}
                    groups.append(current)
                current["agents"].append(value.lower())
                fresh_agent_block = True
            elif key in ("allow", "disallow"):
                if current is None:
                    current = {"agents": ["*"], "rules": []}
                    groups.append(current)
                # an empty Disallow means "nothing is disallowed": no rule
                if value:
                    current["rules"].append((key == "allow", value))
                fresh_agent_block = False
        return cls(groups, source_url=source_url)

    def active_rules(self, user_agent: str = USER_AGENT) -> List[Tuple[bool, str]]:
        ua = str(user_agent or "").lower()
        specific = ""
        for group in self.groups:
            for agent in group.get("agents", []):
                agent = str(agent).lower()
                if (
                    agent
                    and agent != "*"
                    and agent in ua
                    and len(agent) > len(specific)
                ):
                    specific = agent
        wanted = specific or "*"
        rules: List[Tuple[bool, str]] = []
        for group in self.groups:
            if wanted in [str(agent).lower() for agent in group.get("agents", [])]:
                rules.extend(
                    (bool(allow), str(pattern))
                    for allow, pattern in group.get("rules", [])
                )
        return rules

    def allows(self, path: str, user_agent: str = USER_AGENT) -> bool:
        target = str(path or "/").split("#", 1)[0].split("?", 1)[0] or "/"
        matched: List[Tuple[int, bool, str]] = []
        for allow, pattern in self.active_rules(user_agent):
            if _rule_matches(pattern, target):
                matched.append((len(pattern), allow, pattern))
        if not matched:
            return True
        # Longest pattern wins; on a tie Allow wins (the published rule).
        matched.sort(key=lambda item: (item[0], item[1]))
        return matched[-1][1]


def _rule_matches(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    core = pattern[:-1] if anchored else pattern
    expression = re.escape(core).replace(r"\*", ".*")
    if anchored:
        return re.match("^" + expression + "$", path) is not None
    return re.match("^" + expression, path) is not None


# ---------------------------------------------------------------------------
# page cache — JSON on disk when a root is given, memory when it is not
# ---------------------------------------------------------------------------


class PageCache:
    """A TTL cache of fetched pages, so the same search is not fetched twice.

    ``PageCache(root)`` persists to ``<root>/pages.json`` atomically;
    ``PageCache(None)`` keeps the same contract in memory only. Both honour
    ``ttl_s``, so a run tomorrow does not trust today's HTML.
    """

    SCHEMA = "vx.fontsource.cache/1"

    def __init__(
        self,
        root: Optional[str | Path] = None,
        *,
        ttl_s: float = CACHE_TTL_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root).expanduser() if root is not None else None
        self.path = (self.root / "pages.json") if self.root is not None else None
        self.ttl_s = float(ttl_s)
        self.clock = clock
        self._entries: Optional[Dict[str, Dict[str, Any]]] = None

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if self._entries is not None:
            return self._entries
        entries: Dict[str, Dict[str, Any]] = {}
        if self.path is not None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = None
            if isinstance(data, Mapping) and isinstance(data.get("entries"), Mapping):
                for url, entry in data["entries"].items():
                    if isinstance(entry, Mapping) and isinstance(
                        entry.get("text"), str
                    ):
                        try:
                            entries[str(url)] = {
                                "t": float(entry.get("t")),
                                "text": entry["text"],
                            }
                        except (TypeError, ValueError):
                            continue
        self._entries = entries
        return entries

    def _save(self) -> None:
        if self.path is None:
            return
        assert self.root is not None
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"schema": self.SCHEMA, "entries": self._load()}
        fd, tmp = tempfile.mkstemp(prefix="pages.", suffix=".tmp", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _prune(self, entries: Dict[str, Dict[str, Any]]) -> None:
        now = self.clock()
        for url in [
            url for url, entry in entries.items() if now - entry["t"] > self.ttl_s
        ]:
            entries.pop(url, None)

    def get(self, url: str, *, ttl_s: Optional[float] = None) -> Optional[str]:
        entries = self._load()
        entry = entries.get(url)
        if not isinstance(entry, Mapping):
            return None
        ttl = self.ttl_s if ttl_s is None else float(ttl_s)
        if ttl >= 0 and self.clock() - float(entry.get("t", 0.0)) > ttl:
            entries.pop(url, None)
            return None
        text = entry.get("text")
        return text if isinstance(text, str) else None

    def put(self, url: str, text: str) -> None:
        entries = self._load()
        entries[url] = {"t": self.clock(), "text": str(text)}
        self._prune(entries)
        self._save()

    def clear(self) -> None:
        self._entries = {}
        if self.path is not None and self.path.is_file():
            try:
                self.path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """A font the catalogue may take, with the licence read from its page.

    A candidate is not a record yet: it has no file and no sha256, because
    ``search`` never downloads. ``to_record`` turns it into one once a file
    exists, and refuses to invent a licence.
    """

    family: str
    licence: str
    licence_url: str = ""
    source_url: str = ""
    download_url: str = ""
    styles: List[str] = field(default_factory=list)
    matched: str = ""
    retrieved_at: str = ""
    local_file: str = ""
    sha256: str = ""

    def __post_init__(self) -> None:
        self.family = str(self.family or "").strip()
        if not self.family:
            raise contract.VxError("Candidate: family vuota")
        self.licence = fontlib.check_licence(self.licence)
        self.licence_url = str(self.licence_url or "").strip()
        self.source_url = str(self.source_url or "").strip()
        self.download_url = str(self.download_url or "").strip()
        self.matched = str(self.matched or "").strip()
        self.retrieved_at = str(self.retrieved_at or "").strip()
        self.local_file = str(self.local_file or "").strip()
        self.sha256 = str(self.sha256 or "").strip().lower()
        if isinstance(self.styles, str):
            self.styles = [
                part.strip() for part in re.split(r"[;,]", self.styles) if part.strip()
            ]
        else:
            self.styles = [
                str(style).strip() for style in self.styles or [] if str(style).strip()
            ]

    @property
    def auto_selectable(self) -> bool:
        return self.licence in fontlib.AUTO_SELECTABLE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "family": self.family,
            "licence": self.licence,
            "licence_url": self.licence_url,
            "source_url": self.source_url,
            "download_url": self.download_url,
            "styles": list(self.styles),
            "matched": self.matched,
            "retrieved_at": self.retrieved_at,
            "local_file": self.local_file,
            "sha256": self.sha256,
            "auto_selectable": self.auto_selectable,
        }

    def to_record(
        self, file: str | Path, sha256: Optional[str] = None
    ) -> fontlib.FontRecord:
        """Build the catalogue record for a file this candidate points at."""
        return fontlib.FontRecord(
            family=self.family,
            licence=self.licence,
            file=str(file),
            sha256=sha256 or self.sha256,
            styles=list(self.styles),
            licence_url=self.licence_url,
            source_url=self.source_url or self.download_url,
            retrieved_at=self.retrieved_at or _now(),
        )


class Provider(Protocol):
    """The shape every font source has: health first, then candidates."""

    name: str

    def probe(self) -> Health:  # pragma: no cover - protocol
        ...

    def search(
        self, query: str, *, limit: int = 10
    ) -> List[Candidate]:  # pragma: no cover
        ...


# ---------------------------------------------------------------------------
# licence classification — the strings are the contract
# ---------------------------------------------------------------------------

#: ``commercial``: the page grants use in paid work, or names a licence that
#: does (OFL/Apache/CC0). Checked first, so "free for personal and commercial
#: use" is read as what it says.
_COMMERCIAL_STRONG = (
    "free for personal and commercial use",
    "free for commercial and personal use",
    "free for commercial use",
    "free for commercial purposes",
    "free for any use",
    "free for all uses",
    "free for all purposes",
    "commercial use is allowed",
    "commercial use allowed",
    "allowed for commercial use",
    "may be used for commercial",
    "may be used commercially",
    "public domain",
    "sil open font license",
    "sil open font licence",
    "open font license",
    "open font licence",
    "apache license",
    "apache licence",
    "creative commons zero",
    "creative commons cc0",
    "cc0",
)

#: ``personal``: an explicit denial of commercial use. Checked first so a page
#: that also advertises a paid commercial licence cannot launder the free file.
_COMMERCIAL_DENIAL = (
    "not free for commercial",
    "not for commercial use",
    "not for commercial purposes",
    "commercial use is not allowed",
    "commercial use not allowed",
    "commercial use prohibited",
    "may not be used commercially",
    "no commercial use",
)

#: ``personal``: a personal-only grant.
_PERSONAL_GRANT = (
    "free for personal use",
    "for personal use only",
    "personal use only",
    "for personal use",
    "non-commercial use only",
    "non-commercial",
    "noncommercial",
)

#: Recognised, but not a grant of commercial rights: rejected as ``unknown``
#: because the terms ask for payment or are silent about commercial use.
_NOT_A_GRANT = ("donationware",)

_SPACES = re.compile(r"\s+")


def classify_licence(text: str) -> Tuple[str, str]:
    """Return ``(licence, matched_phrase)`` for a page's licence text.

    The rules, in the order they are applied:

    1. a denial of commercial use ("not for commercial use", "commercial use
       prohibited", ...) → ``personal``;
    2. an explicit grant ("free for commercial use", "free for personal and
       commercial use", "public domain", "OFL", "Apache licence", "CC0") →
       ``commercial``;
    3. a personal-only grant ("free for personal use", "personal use only",
       "non-commercial") → ``personal``;
    4. "donationware" → ``unknown`` with the phrase recorded: payment is
       required, so it is not a commercial grant;
    5. anything else → ``unknown`` with an empty phrase.

    Matching is case-insensitive and whitespace-normalised. Nothing is inferred
    from the absence of a sentence: silence is ``unknown``, never ``commercial``.
    """
    flat = _SPACES.sub(
        " ",
        html_mod.unescape(str(text or ""))
        .replace("\xa0", " ")
        .replace("\u2019", "'")
        .lower(),
    )
    for phrase in _COMMERCIAL_DENIAL:
        if phrase in flat:
            return "personal", phrase
    for phrase in _COMMERCIAL_STRONG:
        if phrase in flat:
            return "commercial", phrase
    for phrase in _PERSONAL_GRANT:
        if phrase in flat:
            return "personal", phrase
    for phrase in _NOT_A_GRANT:
        if phrase in flat:
            return "unknown", phrase
    return "unknown", ""


# ---------------------------------------------------------------------------
# HTML parsing — standard library only
# ---------------------------------------------------------------------------


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: List[Dict[str, str]] = []
        self._href: Optional[str] = None
        self._title = ""
        self._text: List[str] = []

    def handle_starttag(
        self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]
    ) -> None:
        if tag.lower() == "a":
            values = {key.lower(): (value or "") for key, value in attrs}
            self._href = values.get("href")
            self._title = values.get("title", "")
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append(
                {
                    "href": self._href,
                    "title": self._title,
                    "text": " ".join("".join(self._text).split()),
                }
            )
            self._href = None
            self._title = ""
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)


_FONT_PAGE = re.compile(r"\.font$|/(?:fonts?|font)/[\w.\-%]+\.(?:html?|php)$", re.I)
_NOT_FONT_PAGE = re.compile(r"/dl\.php|/download/|/search\.php|/alpha\.php", re.I)


def _anchors(text: str) -> List[Dict[str, str]]:
    parser = _AnchorParser()
    try:
        parser.feed(str(text or ""))
        parser.close()
    except Exception:  # a malformed page is "no links", not a crash
        return parser.anchors
    return parser.anchors


def _is_font_page(path: str) -> bool:
    if not path or path == "/":
        return False
    if _NOT_FONT_PAGE.search(path):
        return False
    return bool(_FONT_PAGE.search(path))


def _family_from_anchor(anchor: Mapping[str, str], path: str) -> str:
    for key in ("text", "title"):
        value = str(anchor.get(key) or "").strip()
        value = re.sub(r"^(?:download|scarica)\s+", "", value, flags=re.I)
        value = re.sub(r"\s+font$", "", value, flags=re.I).strip()
        if value:
            return value
    slug = urllib.parse.unquote(Path(path).stem)
    return re.sub(r"[-_]+", " ", slug).strip().title() or slug


def parse_results(text: str, base_url: str) -> List[Tuple[str, str]]:
    """``[(family, absolute_page_url), ...]`` from a search results page.

    Only links whose path looks like a font page are kept; download endpoints
    are never treated as results. Order is page order, duplicates removed.
    """
    out: List[Tuple[str, str]] = []
    seen = set()
    base = str(base_url or "").rstrip("/") + "/"
    for anchor in _anchors(text):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        absolute = urllib.parse.urljoin(base, href.split("#", 1)[0])
        path = urllib.parse.urlparse(absolute).path
        if not _is_font_page(path) or absolute in seen:
            continue
        seen.add(absolute)
        out.append((_family_from_anchor(anchor, path), absolute))
    return out


def candidate_from_page(family: str, page_url: str, text: str) -> Candidate:
    """Classify one font page and expose its links; never fetch the file."""
    licence, matched = classify_licence(text)
    licence_url = ""
    download_url = ""
    for anchor in _anchors(text):
        href = str(anchor.get("href") or "").strip()
        if not href:
            continue
        absolute = urllib.parse.urljoin(page_url, href.split("#", 1)[0])
        parsed = urllib.parse.urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        label = f"{anchor.get('text', '')} {href}"
        if not licence_url and re.search(r"licen[cs]e", label, re.I):
            licence_url = absolute
        if not download_url and re.search(r"/dl\.php|/download/", parsed.path, re.I):
            download_url = absolute
    return Candidate(
        family=family,
        licence=licence,
        licence_url=licence_url,
        source_url=page_url,
        download_url=download_url,
        matched=matched,
        retrieved_at=_now(),
    )


# ---------------------------------------------------------------------------
# Dafont
# ---------------------------------------------------------------------------


class DafontProvider:
    """Search Dafont, classify the licence, expose the download URL only.

    ``probe()`` checks the feature flag first (off → no fetch at all), then
    fetches and parses ``robots.txt``. If ``/search.php`` is disallowed the
    provider is unavailable with ``reason="robots_disallowed"`` and ``search``
    returns an empty list without requesting the endpoint. That is the honest
    answer for Dafont, whose ``robots.txt`` disallows ``/search.php``,
    ``/dl.php`` and ``/download/``.
    """

    name = "dafont"

    def __init__(
        self,
        fetcher: Callable[[str], str],
        *,
        base_url: str = DEFAULT_BASE_URL,
        rate_limit_s: float = 1.0,
        cache: Optional[PageCache] = None,
        robots_ttl_s: float = ROBOTS_TTL_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        enabled: Optional[Callable[[], bool]] = None,
    ) -> None:
        if not callable(fetcher):
            raise contract.VxError("DafontProvider: fetcher non richiamabile")
        self.fetcher = fetcher
        self.base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        self.rate_limit_s = max(0.0, float(rate_limit_s))
        self.cache = cache if cache is not None else PageCache(None)
        self.robots_ttl_s = float(robots_ttl_s)
        self._clock = clock
        self._sleep = sleep
        self._enabled = enabled or (lambda: features.enabled("font_search"))
        self._robots: Optional[RobotsPolicy] = None
        self._health: Optional[Health] = None
        self._last_fetch = 0.0
        self._memo: Dict[Tuple[str, int], List[Candidate]] = {}

    # -- flag ------------------------------------------------------------

    def _flag_on(self) -> bool:
        try:
            return bool(self._enabled())
        except contract.VxError:
            # An older checkout without the declared flag is "off", not a crash.
            return False

    # -- fetching --------------------------------------------------------

    def _throttle(self) -> None:
        if self.rate_limit_s <= 0 or self._last_fetch <= 0:
            self._last_fetch = self._clock()
            return
        wait = self.rate_limit_s - (self._clock() - self._last_fetch)
        if wait > 0:
            self._sleep(wait)
        self._last_fetch = self._clock()

    def _fetch(self, url: str) -> str:
        self._throttle()
        text = self.fetcher(url)
        if not isinstance(text, str):
            raise FetchError(f"fetcher ha restituito {type(text)!r} per {url}")
        self.cache.put(url, text)
        return text

    def _cached(self, url: str, *, ttl_s: Optional[float] = None) -> Optional[str]:
        return self.cache.get(url, ttl_s=ttl_s)

    # -- robots ----------------------------------------------------------

    def _robots_policy(self, *, refresh: bool = False) -> RobotsPolicy:
        url = self.base_url + ROBOTS_PATH
        if not refresh and self._robots is not None:
            return self._robots
        text: Optional[str] = (
            None if refresh else self._cached(url, ttl_s=self.robots_ttl_s)
        )
        if text is None:
            text = self._fetch(url)
        self._robots = RobotsPolicy.parse(text, source_url=url)
        return self._robots

    def probe(self, *, refresh: bool = False) -> Health:
        """Health of the source. Never fetches when the feature flag is off."""
        if not self._flag_on():
            return _health(self.name, False, "feature_off")
        if self._health is not None and not refresh:
            return self._health
        try:
            policy = self._robots_policy(refresh=refresh)
        except FetchError:
            health = _health(self.name, False, "unreachable")
        except contract.VxError:
            health = _health(self.name, False, "fetch_error")
        else:
            if policy.allows(SEARCH_PATH):
                health = _health(self.name, True, "ok", last_success=_now())
            else:
                health = _health(self.name, False, "robots_disallowed")
        self._health = health
        return health

    # -- search ----------------------------------------------------------

    def search_path(self, query: str) -> str:
        return f"{SEARCH_PATH}?q={urllib.parse.quote_plus(str(query or ''))}"

    def search_url(self, query: str) -> str:
        return self.base_url + self.search_path(query)

    def search(self, query: str, *, limit: int = 10) -> List[Candidate]:
        """Return up to ``limit`` candidates; never downloads a font file."""
        if not self._flag_on():
            return []
        count = max(0, int(limit))
        if count == 0:
            return []
        key = (str(query or "").strip().lower(), count)
        if key in self._memo:
            return list(self._memo[key])
        if not self.probe().available:
            self._memo[key] = []
            return []

        path = self.search_path(query)
        try:
            if not self._robots_policy().allows(path):
                self._memo[key] = []
                return []
            url = self.base_url + path
            text = self._cached(url)
            if text is None:
                text = self._fetch(url)
        except FetchError:
            self._health = _health(self.name, False, "fetch_error")
            self._memo[key] = []
            return []

        candidates: List[Candidate] = []
        policy = self._robots_policy()
        for family, page_url in parse_results(text, self.base_url):
            if len(candidates) >= count:
                break
            page_path = urllib.parse.urlparse(page_url).path
            if not policy.allows(page_path):
                continue
            page_text = self._cached(page_url)
            if page_text is None:
                try:
                    page_text = self._fetch(page_url)
                except FetchError:
                    continue
            candidates.append(candidate_from_page(family, page_url, page_text))
        self._memo[key] = list(candidates)
        return candidates


# ---------------------------------------------------------------------------
# local folder
# ---------------------------------------------------------------------------


def _family_from_stem(stem: str) -> str:
    base = re.sub(r"\[[^\]]*\]", "", str(stem or ""))
    parts = [part for part in re.split(r"[-_ ]+", base) if part]
    while len(parts) > 1 and parts[-1].lower() in _STYLE_WORDS:
        parts.pop()
    return (parts[0] if parts else base).strip() or str(stem or "")


def _styles_from_stem(stem: str) -> List[str]:
    out: List[str] = []
    for part in re.split(r"[-_ ]+", str(stem or "")):
        if part.lower() in _STYLE_WORDS and part.title() not in out:
            out.append(part.title())
    return out


class LocalFolderProvider:
    """Fonts the operator already has. Licence from a sidecar, else ``unknown``.

    Sidecar candidates, first hit wins: ``<name>.licence.txt`` (dafont-style
    stem), ``<full-file-name>.licence.txt`` and ``<name>.license.txt``. The
    sidecar is classified with the same rules as a page; with no sidecar the
    licence is ``unknown`` — a local file is not evidence of anything.
    """

    name = "local_folder"

    def __init__(self, root: str | Path, *, recursive: bool = True) -> None:
        self.root = Path(root).expanduser()
        self.recursive = bool(recursive)

    def probe(self) -> Health:
        if self.root.is_dir():
            return _health(self.name, True, "ok", last_success=_now())
        return _health(self.name, False, "missing_dir")

    def _sidecar(self, path: Path) -> Optional[Path]:
        for candidate in (
            path.with_name(path.stem + ".licence.txt"),
            path.with_name(path.name + ".licence.txt"),
            path.with_name(path.stem + ".license.txt"),
        ):
            if candidate.is_file():
                return candidate
        return None

    def _licence(self, path: Path) -> Tuple[str, str, str]:
        sidecar = self._sidecar(path)
        if sidecar is None:
            return "unknown", "", ""
        try:
            text = sidecar.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "unknown", "", ""
        licence, matched = classify_licence(text)
        url = ""
        found = re.search(r"https?://\S+", text)
        if found:
            url = found.group(0).rstrip(").,;")
        return licence, matched, url

    def search(self, query: str, *, limit: int = 10) -> List[Candidate]:
        if not self.root.is_dir():
            return []
        count = max(0, int(limit))
        if count == 0:
            return []
        wanted = _norm(query)
        files = (
            sorted(self.root.rglob("*"))
            if self.recursive
            else sorted(self.root.iterdir())
        )
        out: List[Candidate] = []
        for path in files:
            if len(out) >= count:
                break
            if not path.is_file() or path.suffix.lower() not in _FONT_SUFFIXES:
                continue
            family = _family_from_stem(path.stem)
            stem_norm = _norm(path.stem)
            if wanted and wanted not in stem_norm and wanted not in _norm(family):
                continue
            licence, matched, licence_url = self._licence(path)
            absolute = path.resolve()
            out.append(
                Candidate(
                    family=family,
                    licence=licence,
                    licence_url=licence_url,
                    source_url=absolute.as_uri(),
                    download_url=absolute.as_uri(),
                    styles=_styles_from_stem(path.stem),
                    matched=matched,
                    retrieved_at=_now(),
                    local_file=str(absolute),
                    sha256=fontlib.sha256_file(absolute),
                )
            )
        return out
