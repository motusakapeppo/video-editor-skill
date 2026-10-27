"""vx.fontapis — font providers reached through channels built for code.

``fontsource.py`` answers "what may I fetch, and under which licence" for a
human-facing site. This module answers the same question for four sources that
publish an interface meant to be called by programs, so autonomy can exist
without impersonating anybody:

===============  ===========================  ==================  ============
provider          channel                      credential          licence
===============  ===========================  ==================  ============
``github``        api.github.com + raw         optional token      read LICENSE
``fontsource``    registry.npmjs.org           none                SPDX field
``google_fonts``  googleapis.com/webfonts/v1   API key             OFL/Apache
``fontspace``     *not declared*               base URL + key      n/a
===============  ===========================  ==================  ============

Three facts shape the code, and none of them is a fallback:

1. **GitHub.** Only ``api.github.com`` and ``raw.githubusercontent.com`` are
   ever contacted; an HTML page is never parsed here. The unauthenticated REST
   budget is 60 requests/hour and the provider reports the remaining figure it
   read from ``/rate_limit``. Code search needs a token, so the code path that
   also runs without one is repository search followed by the git tree: with or
   without a token the provider behaves the same, the token only raises the
   budget and is sent as a header. It is held in a private attribute, never
   copied into a ``Health``, a ``Candidate`` or an error message.
2. **Fontsource.** The npm registry needs no credential. ``search`` reads
   metadata only and exposes the tarball URL; ``fetch_font`` is the single
   place that downloads, and it refuses archives whose members escape the
   destination.
3. **Google Fonts.** The Developer API needs a key. Without one the provider is
   unavailable with ``missing_credentials`` and performs zero requests. With
   one, the key travels in the query string, so every error detail is scrubbed
   through :func:`redact_url` and the cache is keyed on a key-free URL: the key
   is never written down. The API does not expose a per-family SPDX id, so the
   record states the declared set (OFL-1.1 / Apache-2.0) instead of inventing
   one.

``fontspace`` is declared and unconfigured on purpose: its endpoint shape is not
public in a form we can verify, so the provider answers ``not_configured`` and
names exactly what it would need. Guessing an endpoint is how a tool starts
lying quietly.

Everything here reuses ``fontsource``'s contract — :class:`Health`,
:class:`Candidate`, :class:`FetchError`, :class:`PageCache` and
:func:`classify_licence` are imported, not reimplemented. :class:`Health` gains
one field, ``detail``, because a rate limit and a missing key are only useful if
the probe can say what it saw.

Tests inject the fetcher, so nothing below touches the network::

    GithubFontProvider(fake, token=None, rate_limit_s=0)
    FontsourceProvider(fake, byte_fetcher=fake_bytes)
"""

from __future__ import annotations

import base64
import inspect
import io
import json
import os
import re
import shutil
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from . import contract, features
from .fontsource import (
    FETCH_TIMEOUT_S,
    USER_AGENT,
    Candidate,
    FetchError,
    Health as _BaseHealth,
    PageCache,
    classify_licence,
)

__all__ = [
    "ArchiveRejected",
    "Candidate",
    "FONT_SUFFIXES",
    "FETCH_TIMEOUT_S",
    "FetchError",
    "GOOGLE_FONTS_ENV",
    "GOOGLE_WEBFONTS_URL",
    "GITHUB_API_URL",
    "GITHUB_RAW_URL",
    "MAX_FONT_BYTES",
    "NPM_REGISTRY_URL",
    "FontSpaceProvider",
    "FontsourceProvider",
    "GithubFontProvider",
    "GoogleFontsProvider",
    "Health",
    "PageCache",
    "extract_font",
    "family_from_stem",
    "fetch_api",
    "fetch_bytes",
    "licence_from_registry",
    "providers",
    "redact_url",
]

GITHUB_API_URL = "https://api.github.com"
GITHUB_RAW_URL = "https://raw.githubusercontent.com"
NPM_REGISTRY_URL = "https://registry.npmjs.org"
GOOGLE_WEBFONTS_URL = "https://www.googleapis.com/webfonts/v1/webfonts"

#: The only environment name this module reads for Google.
GOOGLE_FONTS_ENV = "GOOGLE_FONTS_API_KEY"
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
FONTSPACE_BASE_ENV = "FONTSPACE_BASE_URL"
FONTSPACE_KEY_ENV = "FONTSPACE_API_KEY"

FONT_SUFFIXES = (".ttf", ".otf")

#: A single font larger than this is refused rather than written to disk.
MAX_FONT_BYTES = 64 * 1024 * 1024

#: Query parameters whose value is a credential and must never be printed.
_SECRET_KEYS = frozenset(("key", "api_key", "apikey", "token", "access_token", "auth"))

_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>]+")

#: Family-suffix tokens stripped when a file name is turned into a family.
_STYLE_TOKENS = (
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

_FETCHER_URL_ONLY = "url"
_FETCHER_KEYWORD = "keyword"
_FETCHER_POSITIONAL = "positional"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm(text: str) -> str:
    """Lower case, alphanumerics only, single spaces between tokens."""
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


# ---------------------------------------------------------------------------
# health — the fontsource shape plus one line of detail
# ---------------------------------------------------------------------------


@dataclass
class Health(_BaseHealth):
    """A ``fontsource.Health`` carrying what the probe actually saw.

    ``reason`` stays the stable code (``ok``, ``feature_off``, ``rate_limited``,
    ``missing_credentials``, ``not_configured``, ``unreachable``,
    ``fetch_error``); ``detail`` is the human sentence behind it and is already
    redacted by the provider that produced it.
    """

    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data["detail"] = self.detail
        return data


#: Reasons that mean "the source is not usable", as opposed to "it broke".
_BLOCKED_REASONS = frozenset(
    (
        "feature_off",
        "rate_limited",
        "missing_credentials",
        "not_configured",
        "robots_disallowed",
    )
)


def _health(
    name: str, available: bool, reason: str, detail: str = "", last_success: str = ""
) -> Health:
    if available:
        level = "ok"
    elif reason in _BLOCKED_REASONS:
        level = "blocked"
    else:
        level = "error"
    return Health(
        name=name,
        available=available,
        reason=reason,
        level=level,
        last_success=last_success,
        detail=str(detail or ""),
    )


class ArchiveRejected(contract.VxError):
    """A downloaded archive was refused: unsafe member, or no font inside."""


# ---------------------------------------------------------------------------
# redaction — credentials never reach a message
# ---------------------------------------------------------------------------


def redact_url(url: str) -> str:
    """``url`` with every credential-bearing query value replaced by ``***``.

    Everything else — path, other parameters, fragment — is preserved verbatim,
    so the redacted URL is still useful in a report.
    """
    text = str(url or "")
    parts = urllib.parse.urlsplit(text)
    if not parts.query:
        return text
    items: List[str] = []
    for item in parts.query.split("&"):
        key, sep, _value = item.partition("=")
        if key.lower() in _SECRET_KEYS:
            items.append(f"{key}{sep}***")
        else:
            items.append(item)
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, "&".join(items), parts.fragment)
    )


def _redact_text(text: str, secrets: Sequence[str] = ()) -> str:
    """Scrub URLs and literal secrets out of a message."""
    out = _URL_IN_TEXT.sub(lambda match: redact_url(match.group(0)), str(text or ""))
    for secret in secrets:
        if not secret:
            continue
        out = out.replace(secret, "***")
        quoted = urllib.parse.quote(secret, safe="")
        if quoted and quoted != secret:
            out = out.replace(quoted, "***")
    return out


# ---------------------------------------------------------------------------
# production fetchers — the only place urllib appears
# ---------------------------------------------------------------------------


def _read(
    url: str, headers: Optional[Mapping[str, str]], timeout: float
) -> Tuple[bytes, str]:
    merged = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    for key, value in (headers or {}).items():
        merged[str(key)] = str(value)
    request = urllib.request.Request(url, headers=merged)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                getcode = getattr(response, "getcode", None)
                status = getcode() if callable(getcode) else 200
            if int(status) != 200:
                raise FetchError(f"HTTP {status} per {redact_url(url)}")
            body = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} per {redact_url(url)}") from None
    except urllib.error.URLError as exc:
        raise FetchError(
            f"rete non raggiungibile per {redact_url(url)}: {exc.reason}"
        ) from None
    except OSError as exc:
        raise FetchError(f"lettura fallita per {redact_url(url)}: {exc}") from None
    return body, charset


def fetch_api(
    url: str,
    headers: Optional[Mapping[str, str]] = None,
    *,
    timeout: float = FETCH_TIMEOUT_S,
) -> str:
    """GET ``url`` with explicit headers. Non-200 is a hard ``FetchError``."""
    body, charset = _read(url, headers, timeout)
    try:
        return body.decode(charset, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


def fetch_bytes(
    url: str,
    headers: Optional[Mapping[str, str]] = None,
    *,
    timeout: float = FETCH_TIMEOUT_S,
) -> bytes:
    """GET ``url`` as bytes. The only entry point for a binary download."""
    return _read(url, headers, timeout)[0]


# ---------------------------------------------------------------------------
# fetcher injection — a one-argument or a two-argument callable, both supported
# ---------------------------------------------------------------------------


def _fetcher_mode(fetcher: Callable[..., Any]) -> str:
    """How to call ``fetcher``: with ``headers`` by keyword, positionally, or not.

    ``fontsource``'s fetchers take ``(url)``; the production ones here take
    ``(url, headers)``. Reading the signature once lets both be injected without
    guessing at call time and without catching ``TypeError`` from a real failure.
    """
    try:
        params = inspect.signature(fetcher).parameters
    except (TypeError, ValueError):
        return _FETCHER_URL_ONLY
    if "headers" in params:
        return _FETCHER_KEYWORD
    values = list(params.values())
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in values):
        return _FETCHER_KEYWORD
    positional = [
        param
        for param in values
        if param.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) >= 2:
        return _FETCHER_POSITIONAL
    return _FETCHER_URL_ONLY


# ---------------------------------------------------------------------------
# family naming
# ---------------------------------------------------------------------------


def _tidy(token: str) -> str:
    return token.capitalize() if token.islower() else token


def family_from_stem(stem: str) -> str:
    """``Inter-Regular`` → ``Inter``; ``open-sans`` → ``Open Sans``."""
    base = re.sub(r"\[[^\]]*\]", "", str(stem or ""))
    parts = [part for part in re.split(r"[-_ ]+", base) if part]
    while len(parts) > 1 and parts[-1].lower() in _STYLE_TOKENS:
        parts.pop()
    if not parts:
        return str(stem or "").strip()
    return " ".join(_tidy(part) for part in parts)


# ---------------------------------------------------------------------------
# shared provider behaviour: flag, throttle, cache, JSON
# ---------------------------------------------------------------------------


class _ApiProvider:
    """Flag gate, rate throttle, page cache and JSON reading, in one place."""

    name = "api"

    def __init__(
        self,
        fetcher: Callable[..., Any],
        *,
        rate_limit_s: float = 0.0,
        cache: Optional[PageCache] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        enabled: Optional[Callable[[], bool]] = None,
    ) -> None:
        if not callable(fetcher):
            raise contract.VxError(f"{type(self).__name__}: fetcher non richiamabile")
        self.fetcher = fetcher
        self.rate_limit_s = max(0.0, float(rate_limit_s))
        self.cache = cache if cache is not None else PageCache(None)
        self._clock = clock
        self._sleep = sleep
        self._enabled = enabled or (lambda: features.enabled("font_search"))
        self._mode = _fetcher_mode(fetcher)
        self._last_fetch = 0.0
        self._health: Optional[Health] = None

    # -- flag ------------------------------------------------------------

    def _flag_on(self) -> bool:
        try:
            return bool(self._enabled())
        except contract.VxError:
            # An older checkout without the declared flag is "off", not a crash.
            return False

    def _feature_health(self) -> Health:
        return _health(
            self.name,
            False,
            "feature_off",
            detail="features.font_search e' spenta: nessuna richiesta effettuata",
        )

    # -- secrets ---------------------------------------------------------

    def _secrets(self) -> Tuple[str, ...]:
        return ()

    # -- fetching --------------------------------------------------------

    def _throttle(self) -> None:
        if self.rate_limit_s <= 0 or self._last_fetch <= 0:
            self._last_fetch = self._clock()
            return
        wait = self.rate_limit_s - (self._clock() - self._last_fetch)
        if wait > 0:
            self._sleep(wait)
        self._last_fetch = self._clock()

    def _call_fetch(self, url: str, headers: Mapping[str, str]) -> Any:
        if self._mode == _FETCHER_KEYWORD:
            return self.fetcher(url, headers=dict(headers))
        if self._mode == _FETCHER_POSITIONAL:
            return self.fetcher(url, dict(headers))
        return self.fetcher(url)

    def _get_text(
        self,
        url: str,
        headers: Optional[Mapping[str, str]] = None,
        *,
        cache_key: Optional[str] = None,
        read: bool = True,
        write: bool = True,
    ) -> str:
        key = cache_key or url
        if read:
            hit = self.cache.get(key)
            if hit is not None:
                return hit
        self._throttle()
        try:
            text = self._call_fetch(url, headers or {})
        except FetchError as exc:
            message = _redact_text(str(exc), self._secrets())
            if message != str(exc):
                # The cause still holds the raw credential: drop it.
                raise FetchError(message) from None
            raise
        if not isinstance(text, str):
            raise FetchError(
                f"fetcher ha restituito {type(text)!r} per {redact_url(url)}"
            )
        if write:
            self.cache.put(key, text)
        return text

    def _get_json(
        self,
        url: str,
        headers: Optional[Mapping[str, str]] = None,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        text = self._get_text(url, headers, **kwargs)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FetchError(f"risposta non JSON da {redact_url(url)}: {exc}") from None
        if not isinstance(data, Mapping):
            raise FetchError(
                f"risposta JSON inattesa da {redact_url(url)}: {type(data).__name__}"
            )
        return data


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def _github_budget(data: Mapping[str, Any]) -> Tuple[int, int]:
    """``(remaining, limit)`` from a ``/rate_limit`` payload, 0/0 if absent."""
    resources = data.get("resources")
    if isinstance(resources, Mapping):
        for bucket in ("core", "search", "code_search"):
            entry = resources.get(bucket)
            if isinstance(entry, Mapping):
                try:
                    return int(entry.get("remaining", 0)), int(entry.get("limit", 0))
                except (TypeError, ValueError):
                    continue
    rate = data.get("rate")
    if isinstance(rate, Mapping):
        try:
            return int(rate.get("remaining", 0)), int(rate.get("limit", 0))
        except (TypeError, ValueError):
            return 0, 0
    return 0, 0


class GithubFontProvider(_ApiProvider):
    """Repository search plus the git tree, licence read from the LICENSE file.

    ``probe()`` spends one request on ``/rate_limit`` and reports the remaining
    budget in ``Health.detail``; at zero it is ``rate_limited`` and ``search``
    returns nothing without touching the search endpoint.

    ``search()`` costs 1 request for the repository search, 1 git-tree request
    per repository and 1 licence request per repository (memoised, so two fonts
    from one repository cost one licence request). Only ``api.github.com`` and
    ``raw.githubusercontent.com`` are contacted: an HTML page is never parsed.
    """

    name = "github"

    def __init__(
        self,
        fetcher: Callable[..., Any],
        *,
        token: Optional[str] = None,
        rate_limit_s: float = 1.0,
        cache: Optional[PageCache] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        enabled: Optional[Callable[[], bool]] = None,
    ) -> None:
        super().__init__(
            fetcher,
            rate_limit_s=rate_limit_s,
            cache=cache,
            clock=clock,
            sleep=sleep,
            enabled=enabled,
        )
        # Never copied out of this attribute: not in Health, not in Candidate,
        # not in a message, not on disk.
        self._token = str(token or "").strip()
        self._repo_licence: Dict[str, Tuple[str, str, str]] = {}
        self._memo: Dict[Tuple[str, int], List[Candidate]] = {}

    def __repr__(self) -> str:
        return f"GithubFontProvider(authenticated={self.authenticated})"

    @property
    def authenticated(self) -> bool:
        """Whether a token is held. The token itself is never exposed."""
        return bool(self._token)

    def _secrets(self) -> Tuple[str, ...]:
        return (self._token,) if self._token else ()

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _call_fetch(self, url: str, headers: Mapping[str, str]) -> Any:
        host = urllib.parse.urlsplit(str(url)).hostname or ""
        if host not in ("api.github.com", "raw.githubusercontent.com"):
            raise contract.VxError(
                f"GithubFontProvider: host non ammesso {host!r}; "
                "sono ammessi solo api.github.com e raw.githubusercontent.com"
            )
        return super()._call_fetch(url, headers)

    # -- urls ------------------------------------------------------------

    def rate_limit_url(self) -> str:
        return f"{GITHUB_API_URL}/rate_limit"

    def search_url(self, query: str, limit: int = 10) -> str:
        wanted = str(query or "").strip()
        if "font" not in wanted.lower():
            wanted = f"{wanted} font".strip()
        per_page = min(30, max(5, int(limit)))
        return (
            f"{GITHUB_API_URL}/search/repositories"
            f"?q={urllib.parse.quote_plus(wanted)}&per_page={per_page}"
            "&sort=stars&order=desc"
        )

    def tree_url(self, full_name: str, branch: str) -> str:
        ref = urllib.parse.quote(str(branch or "HEAD"), safe="")
        return f"{GITHUB_API_URL}/repos/{full_name}/git/trees/{ref}?recursive=1"

    def licence_url(self, full_name: str) -> str:
        return f"{GITHUB_API_URL}/repos/{full_name}/license"

    @staticmethod
    def raw_url(full_name: str, branch: str, path: str) -> str:
        encoded = "/".join(urllib.parse.quote(part) for part in path.split("/"))
        return f"{GITHUB_RAW_URL}/{full_name}/{branch}/{encoded}"

    # -- probe -----------------------------------------------------------

    def probe(self, *, refresh: bool = False) -> Health:
        if not self._flag_on():
            return self._feature_health()
        if self._health is not None and not refresh:
            return self._health
        try:
            data = self._get_json(
                self.rate_limit_url(),
                self._headers(),
                read=False,
                write=False,
            )
        except FetchError as exc:
            health = _health(
                self.name, False, "unreachable", detail=_redact_text(str(exc))
            )
        else:
            remaining, limit = _github_budget(data)
            if limit <= 0 or remaining <= 0:
                health = _health(
                    self.name,
                    False,
                    "rate_limited",
                    detail=(
                        f"{remaining}/{limit} richieste residue: "
                        "la ricerca non viene tentata"
                    ),
                )
            else:
                health = _health(
                    self.name,
                    True,
                    "ok",
                    detail=f"{remaining}/{limit} richieste residue",
                    last_success=_now(),
                )
        self._health = health
        return health

    # -- licence ---------------------------------------------------------

    def _licence_for(self, full_name: str) -> Tuple[str, str, str]:
        """``(licence, matched, licence_url)`` for one repository, memoised."""
        if full_name in self._repo_licence:
            return self._repo_licence[full_name]
        result: Tuple[str, str, str] = ("unknown", "", "")
        try:
            data = self._get_json(self.licence_url(full_name), self._headers())
        except FetchError:
            data = {}
        content = data.get("content") if isinstance(data, Mapping) else None
        if isinstance(content, str) and content.strip():
            try:
                body = base64.b64decode(content, validate=False).decode(
                    "utf-8", "replace"
                )
            except (ValueError, TypeError):
                body = ""
            if body:
                licence, matched = classify_licence(body)
                html_url = str(data.get("html_url") or "").strip()
                result = (licence, matched, html_url)
        self._repo_licence[full_name] = result
        return result

    # -- search ----------------------------------------------------------

    def _font_path(self, full_name: str, branch: str) -> str:
        """The first ``.ttf``/``.otf`` blob in the repository tree, or ``""``."""
        try:
            data = self._get_json(self.tree_url(full_name, branch), self._headers())
        except FetchError:
            return ""
        tree = data.get("tree")
        if not isinstance(tree, Sequence):
            return ""
        for entry in tree:
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("type") or "") != "blob":
                continue
            path = str(entry.get("path") or "").strip()
            if path and PurePosixPath(path).suffix.lower() in FONT_SUFFIXES:
                return path
        return ""

    def search(self, query: str, *, limit: int = 10) -> List[Candidate]:
        if not self._flag_on():
            return []
        count = max(0, int(limit))
        if count == 0:
            return []
        key = (_norm(query), count)
        if key in self._memo:
            return list(self._memo[key])
        if not self.probe().available:
            self._memo[key] = []
            return []

        try:
            data = self._get_json(self.search_url(query, count), self._headers())
        except FetchError:
            self._health = _health(
                self.name, False, "fetch_error", detail="ricerca GitHub fallita"
            )
            self._memo[key] = []
            return []

        items = data.get("items")
        if not isinstance(items, Sequence):
            self._memo[key] = []
            return []

        out: List[Candidate] = []
        for item in items:
            if len(out) >= count:
                break
            if not isinstance(item, Mapping):
                continue
            full_name = str(item.get("full_name") or "").strip()
            if not full_name:
                continue
            branch = str(item.get("default_branch") or "").strip() or "HEAD"
            path = self._font_path(full_name, branch)
            if not path:
                continue
            licence, matched, licence_url = self._licence_for(full_name)
            stem = PurePosixPath(path).stem
            family = family_from_stem(stem) or full_name.split("/")[-1]
            source_url = str(item.get("html_url") or "").strip() or (
                f"https://github.com/{full_name}"
            )
            out.append(
                Candidate(
                    family=family,
                    licence=licence,
                    licence_url=licence_url,
                    source_url=source_url,
                    download_url=self.raw_url(full_name, branch, path),
                    matched=matched,
                    retrieved_at=_now(),
                )
            )
        self._memo[key] = list(out)
        return out


# ---------------------------------------------------------------------------
# Fontsource (npm registry)
# ---------------------------------------------------------------------------

#: SPDX ids the npm registry publishes that grant use in paid work. Anything
#: else — ``proprietary``, ``UNLICENSED``, an empty field — stays ``unknown``.
_REGISTRY_LICENCES = {
    "ofl-1.1": "commercial",
    "ofl": "commercial",
    "apache-2.0": "commercial",
    "apache": "commercial",
    "mit": "commercial",
}

#: The SPDX page that states the licence, so a record can say which one.
_SPDX_URLS = {
    "ofl-1.1": "https://spdx.org/licenses/OFL-1.1.html",
    "apache-2.0": "https://spdx.org/licenses/Apache-2.0.html",
    "mit": "https://spdx.org/licenses/MIT.html",
}


def licence_from_registry(value: Any) -> Tuple[str, str]:
    """``(licence, spdx_id)`` from an npm ``license`` field.

    ``OFL-1.1``, ``Apache-2.0`` and ``MIT`` map to ``commercial``; everything
    else — including ``proprietary`` and a missing field — is ``unknown``. The
    second element is the normalised SPDX id, or ``""`` when not recognised.
    """
    text = str(value or "").strip()
    key = text.lower()
    licence = _REGISTRY_LICENCES.get(key, "unknown")
    return licence, (key if licence != "unknown" else "")


class FontsourceProvider(_ApiProvider):
    """``@fontsource`` packages on the npm registry: no credential needed.

    ``search()`` reads metadata only and stops at ``download_url`` (the packaged
    tarball). ``fetch_font()`` is the only method that downloads bytes, and it
    refuses a tarball whose members are absolute or contain ``..``.
    """

    name = "fontsource"

    def __init__(
        self,
        fetcher: Callable[..., Any],
        *,
        cache: Optional[PageCache] = None,
        byte_fetcher: Optional[Callable[..., Any]] = None,
        rate_limit_s: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        enabled: Optional[Callable[[], bool]] = None,
    ) -> None:
        super().__init__(
            fetcher,
            rate_limit_s=rate_limit_s,
            cache=cache,
            clock=clock,
            sleep=sleep,
            enabled=enabled,
        )
        self._byte_fetcher = byte_fetcher or fetch_bytes
        self._byte_mode = _fetcher_mode(self._byte_fetcher)
        self._memo: Dict[Tuple[str, int], List[Candidate]] = {}

    # -- urls ------------------------------------------------------------

    def ping_url(self) -> str:
        return f"{NPM_REGISTRY_URL}/-/ping"

    def search_url(self, query: str, limit: int = 10) -> str:
        text = f"@fontsource {str(query or '').strip()}".strip()
        size = min(20, max(1, int(limit)))
        return (
            f"{NPM_REGISTRY_URL}/-/v1/search"
            f"?text={urllib.parse.quote_plus(text)}&size={size}"
        )

    @staticmethod
    def latest_url(name: str) -> str:
        return f"{NPM_REGISTRY_URL}/{urllib.parse.quote(name, safe='@/')}/latest"

    @staticmethod
    def package_url(name: str) -> str:
        return f"https://www.npmjs.com/package/{name}"

    # -- probe -----------------------------------------------------------

    def probe(self, *, refresh: bool = False) -> Health:
        if not self._flag_on():
            return self._feature_health()
        if self._health is not None and not refresh:
            return self._health
        try:
            self._get_text(self.ping_url(), read=False, write=False)
        except FetchError as exc:
            health = _health(
                self.name, False, "unreachable", detail=_redact_text(str(exc))
            )
        else:
            health = _health(
                self.name,
                True,
                "ok",
                detail="registry npm raggiungibile; nessuna chiave richiesta",
                last_success=_now(),
            )
        self._health = health
        return health

    # -- search ----------------------------------------------------------

    def _latest(self, name: str) -> Optional[Mapping[str, Any]]:
        try:
            return self._get_json(self.latest_url(name))
        except FetchError:
            return None

    def search(self, query: str, *, limit: int = 10) -> List[Candidate]:
        if not self._flag_on():
            return []
        count = max(0, int(limit))
        if count == 0:
            return []
        key = (_norm(query), count)
        if key in self._memo:
            return list(self._memo[key])
        if not self.probe().available:
            self._memo[key] = []
            return []

        try:
            data = self._get_json(self.search_url(query, count))
        except FetchError:
            self._health = _health(
                self.name, False, "fetch_error", detail="ricerca npm fallita"
            )
            self._memo[key] = []
            return []

        objects = data.get("objects")
        if not isinstance(objects, Sequence):
            self._memo[key] = []
            return []

        out: List[Candidate] = []
        for entry in objects:
            if len(out) >= count:
                break
            if not isinstance(entry, Mapping):
                continue
            package = entry.get("package")
            if not isinstance(package, Mapping):
                continue
            name = str(package.get("name") or "").strip()
            if not name.startswith("@fontsource/"):
                continue
            latest = self._latest(name)
            if latest is None:
                # No metadata, no candidate: a tarball URL is not guessed.
                continue
            dist = latest.get("dist")
            tarball = ""
            if isinstance(dist, Mapping):
                tarball = str(dist.get("tarball") or "").strip()
            if not tarball:
                continue
            licence, spdx = licence_from_registry(latest.get("license"))
            links = package.get("links")
            npm_page = self.package_url(name)
            if isinstance(links, Mapping):
                npm_page = str(links.get("npm") or "").strip() or npm_page
            slug = name[len("@fontsource/") :]
            out.append(
                Candidate(
                    family=re.sub(r"[-_]+", " ", slug).strip().title() or slug,
                    licence=licence,
                    licence_url=_SPDX_URLS.get(spdx, ""),
                    source_url=npm_page,
                    download_url=tarball,
                    matched=str(latest.get("license") or "").strip(),
                    retrieved_at=_now(),
                )
            )
        self._memo[key] = list(out)
        return out

    # -- download --------------------------------------------------------

    def _download(self, url: str) -> bytes:
        scheme = urllib.parse.urlsplit(str(url)).scheme.lower()
        if scheme not in ("http", "https"):
            raise FetchError(f"download_url non http(s): {redact_url(url)}")
        self._throttle()
        if self._byte_mode == _FETCHER_KEYWORD:
            data = self._byte_fetcher(url, headers={})
        elif self._byte_mode == _FETCHER_POSITIONAL:
            data = self._byte_fetcher(url, {})
        else:
            data = self._byte_fetcher(url)
        if not isinstance(data, (bytes, bytearray)):
            raise FetchError(
                f"byte_fetcher ha restituito {type(data)!r} per {redact_url(url)}"
            )
        return bytes(data)

    def fetch_font(self, candidate: Candidate, dest: str | Path) -> Path:
        """Download ``candidate.download_url`` and write its first font file.

        The archive is validated before anything is extracted: an absolute
        member, a Windows drive, a backslash or a ``..`` component rejects the
        whole tarball with :class:`ArchiveRejected`.
        """
        if not self._flag_on():
            raise contract.VxError(
                "font_search e' spenta: il download dei font non e' abilitato"
            )
        url = str(getattr(candidate, "download_url", "") or "").strip()
        if not url:
            raise FetchError("candidate senza download_url: nulla da scaricare")
        data = self._download(url)
        return extract_font(data, Path(dest))


def _member_problem(name: str) -> str:
    """Why a tar member name is unsafe, or ``""`` when it is safe."""
    raw = str(name or "")
    if not raw:
        return "nome vuoto"
    if "\\" in raw:
        return "separatore Windows"
    if raw.startswith("/"):
        return "percorso assoluto"
    if re.match(r"^[A-Za-z]:", raw):
        return "unita' Windows"
    parts = PurePosixPath(raw).parts
    if PurePosixPath(raw).is_absolute():
        return "percorso assoluto"
    if any(part == ".." for part in parts):
        return "componente '..'"
    return ""


def extract_font(data: bytes, dest: Path) -> Path:
    """Write the first ``.ttf``/``.otf`` of a tarball to ``dest``.

    Every member is checked before the first byte is written, so a single
    hostile name rejects the archive; nothing is extracted partially, and the
    file is written to a ``.part`` sibling then renamed.
    """
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.TarError as exc:
        raise ArchiveRejected(f"tarball illeggibile: {exc}") from None
    with archive:
        try:
            members = archive.getmembers()
        except (tarfile.TarError, OSError) as exc:
            raise ArchiveRejected(f"indice del tarball illeggibile: {exc}") from None
        for member in members:
            problem = _member_problem(member.name)
            if problem:
                raise ArchiveRejected(
                    f"membro non sicuro nel tarball ({problem}): {member.name!r}"
                )
        fonts = [
            member
            for member in members
            if member.isfile()
            and PurePosixPath(member.name).suffix.lower() in FONT_SUFFIXES
        ]
        if not fonts:
            raise ArchiveRejected(
                "nessun file .ttf/.otf nel tarball: niente da estrarre"
            )
        chosen = fonts[0]
        if chosen.size > MAX_FONT_BYTES:
            raise ArchiveRejected(
                f"font troppo grande: {chosen.size} byte (massimo {MAX_FONT_BYTES})"
            )
        target = Path(dest)
        if target.is_dir():
            target = target / PurePosixPath(chosen.name).name
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = archive.extractfile(chosen)
        if handle is None:
            raise ArchiveRejected(f"membro non leggibile: {chosen.name!r}")
        partial = target.with_name(target.name + ".part")
        try:
            with open(partial, "wb") as out:
                shutil.copyfileobj(handle, out)
            os.replace(partial, target)
        except OSError as exc:
            try:
                partial.unlink()
            except OSError:
                pass
            raise ArchiveRejected(f"scrittura fallita per {target}: {exc}") from None
    return target


# ---------------------------------------------------------------------------
# Google Fonts
# ---------------------------------------------------------------------------

#: The API publishes no per-family SPDX id; the record states the declared set.
GOOGLE_LICENCE_MATCH = "OFL-1.1 / Apache-2.0 (Google Fonts Developer API)"

#: Cache key for the catalogue: the same URL the request uses, minus the key.
_GOOGLE_CACHE_KEY = f"{GOOGLE_WEBFONTS_URL}?key=***"


class GoogleFontsProvider(_ApiProvider):
    """The official Developer API, with an honest answer when the key is absent.

    Without a key (constructor, ``env``, or ``GOOGLE_FONTS_API_KEY``) ``probe()``
    is ``missing_credentials`` and makes zero requests. With a key the catalogue
    is fetched once and cached under a key-free URL; errors are redacted before
    they become a ``Health.detail`` or an exception message.
    """

    name = "google_fonts"

    def __init__(
        self,
        fetcher: Callable[..., Any],
        *,
        api_key: Optional[str] = None,
        cache: Optional[PageCache] = None,
        env: Optional[Mapping[str, str]] = None,
        rate_limit_s: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        enabled: Optional[Callable[[], bool]] = None,
    ) -> None:
        super().__init__(
            fetcher,
            rate_limit_s=rate_limit_s,
            cache=cache,
            clock=clock,
            sleep=sleep,
            enabled=enabled,
        )
        self._key = self._resolve_key(api_key, env)
        self._catalog: Optional[Mapping[str, Any]] = None

    @staticmethod
    def _resolve_key(api_key: Optional[str], env: Optional[Mapping[str, str]]) -> str:
        explicit = str(api_key or "").strip()
        if explicit:
            return explicit
        if env is not None:
            return str(env.get(GOOGLE_FONTS_ENV) or "").strip()
        return str(os.environ.get(GOOGLE_FONTS_ENV) or "").strip()

    def __repr__(self) -> str:
        return f"GoogleFontsProvider(configured={self.configured})"

    @property
    def configured(self) -> bool:
        """Whether a key is held. The key itself is never exposed."""
        return bool(self._key)

    def _secrets(self) -> Tuple[str, ...]:
        return (self._key,) if self._key else ()

    # -- urls ------------------------------------------------------------

    def request_url(self) -> str:
        return f"{GOOGLE_WEBFONTS_URL}?key={urllib.parse.quote(self._key, safe='')}"

    @staticmethod
    def specimen_url(family: str) -> str:
        return f"https://fonts.google.com/specimen/{urllib.parse.quote(family)}"

    # -- probe -----------------------------------------------------------

    def probe(self, *, refresh: bool = False) -> Health:
        if not self._flag_on():
            return self._feature_health()
        if not self._key:
            return _health(
                self.name,
                False,
                "missing_credentials",
                detail=(
                    f"{GOOGLE_FONTS_ENV} non impostata: la ricerca Google Fonts "
                    "resta spenta e nessuna richiesta viene effettuata"
                ),
            )
        if self._health is not None and not refresh:
            return self._health
        try:
            self.catalog(refresh=refresh)
        except FetchError as exc:
            health = _health(
                self.name,
                False,
                "unreachable",
                detail=_redact_text(str(exc), self._secrets()),
            )
        else:
            health = _health(
                self.name,
                True,
                "ok",
                detail=(
                    "chiave presente; catalogo webfonts letto "
                    f"(licenza dichiarata: {GOOGLE_LICENCE_MATCH})"
                ),
                last_success=_now(),
            )
        self._health = health
        return health

    # -- catalogue -------------------------------------------------------

    def catalog(self, *, refresh: bool = False) -> Mapping[str, Any]:
        if self._catalog is not None and not refresh:
            return self._catalog
        data = self._get_json(
            self.request_url(),
            {"Accept": "application/json"},
            cache_key=_GOOGLE_CACHE_KEY,
            read=not refresh,
            write=True,
        )
        self._catalog = data
        return data

    # -- search ----------------------------------------------------------

    def search(self, query: str, *, limit: int = 10) -> List[Candidate]:
        if not self._flag_on():
            return []
        count = max(0, int(limit))
        if count == 0:
            return []
        if not self._key:
            self._health = self.probe()
            return []
        try:
            catalog = self.catalog()
        except FetchError:
            self._health = _health(
                self.name, False, "unreachable", detail="catalogo webfonts non letto"
            )
            return []

        tokens = _norm(query).split()
        items = catalog.get("items")
        if not isinstance(items, Sequence):
            return []
        out: List[Candidate] = []
        for item in items:
            if len(out) >= count:
                break
            if not isinstance(item, Mapping):
                continue
            family = str(item.get("family") or "").strip()
            if not family:
                continue
            category = str(item.get("category") or "")
            haystack = f"{_norm(family)} {_norm(category)}".strip()
            if tokens and not all(token in haystack for token in tokens):
                continue
            files = item.get("files")
            download_url = ""
            if isinstance(files, Mapping):
                download_url = str(
                    files.get("regular") or next(iter(files.values()), "")
                ).strip()
            variants = item.get("variants")
            styles = (
                [str(style) for style in variants]
                if isinstance(variants, Sequence) and not isinstance(variants, str)
                else []
            )
            out.append(
                Candidate(
                    family=family,
                    licence="commercial",
                    licence_url="",
                    source_url=self.specimen_url(family),
                    download_url=download_url,
                    styles=styles,
                    matched=GOOGLE_LICENCE_MATCH,
                    retrieved_at=_now(),
                )
            )
        return out


# ---------------------------------------------------------------------------
# FontSpace — declared, not configured
# ---------------------------------------------------------------------------


class FontSpaceProvider:
    """Named, unusable, and honest about it: the endpoint shape is not declared.

    ``probe()`` always answers ``not_configured`` and makes zero requests even
    when ``FONTSPACE_BASE_URL`` and ``FONTSPACE_API_KEY`` are set: without the
    documented path and parameters a request would be a guess. The fetcher is
    held only so every provider has the same constructor shape; it is never
    called.
    """

    name = "fontspace"

    def __init__(
        self,
        fetcher: Optional[Callable[..., Any]] = None,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        enabled: Optional[Callable[[], bool]] = None,
    ) -> None:
        table: Mapping[str, str] = env if env is not None else os.environ
        self.fetcher = fetcher
        self._base = str(base_url or table.get(FONTSPACE_BASE_ENV) or "").strip()
        self._key = str(api_key or table.get(FONTSPACE_KEY_ENV) or "").strip()
        self._enabled = enabled or (lambda: features.enabled("font_search"))

    def _flag_on(self) -> bool:
        try:
            return bool(self._enabled())
        except contract.VxError:
            return False

    def probe(self, *, refresh: bool = False) -> Health:
        if not self._flag_on():
            return _health(
                self.name,
                False,
                "feature_off",
                detail="features.font_search e' spenta: nessuna richiesta effettuata",
            )
        missing = [
            name
            for name, value in (
                (FONTSPACE_BASE_ENV, self._base),
                (FONTSPACE_KEY_ENV, self._key),
            )
            if not value
        ]
        if missing:
            detail = (
                "serve una base URL e una API key: "
                f"{FONTSPACE_BASE_ENV} e {FONTSPACE_KEY_ENV} mancanti "
                f"({', '.join(missing)})"
            )
        else:
            detail = (
                f"{FONTSPACE_BASE_ENV} e {FONTSPACE_KEY_ENV} sono impostate, ma lo "
                "schema dell'endpoint non e' dichiarato: servono percorso e "
                "parametri dalla documentazione FontSpace, e nessun endpoint "
                "viene inventato"
            )
        return _health(self.name, False, "not_configured", detail=detail)

    def search(self, query: str, *, limit: int = 10) -> List[Candidate]:
        return []


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------


def providers(
    env: Optional[Mapping[str, str]] = None,
    *,
    fetcher: Optional[Callable[..., Any]] = None,
    byte_fetcher: Optional[Callable[..., Any]] = None,
    cache: Optional[PageCache] = None,
    rate_limit_s: float = 1.0,
) -> List[Any]:
    """The four providers, deterministic order, no-credential sources first.

    GitHub and Fontsource need nothing, so they come first; Google follows and
    degrades by itself when the key is absent; FontSpace is last because it is
    declared and unconfigured. The same ``PageCache`` is shared, keyed by URL,
    and Google's entry is key-free.
    """
    table: Mapping[str, str] = env if env is not None else os.environ
    text_fetcher = fetcher or fetch_api
    binary_fetcher = byte_fetcher or fetch_bytes
    store = cache if cache is not None else PageCache(None)
    return [
        GithubFontProvider(
            text_fetcher,
            token=table.get(GITHUB_TOKEN_ENV),
            rate_limit_s=rate_limit_s,
            cache=store,
        ),
        FontsourceProvider(
            text_fetcher,
            cache=store,
            byte_fetcher=binary_fetcher,
        ),
        GoogleFontsProvider(
            text_fetcher,
            api_key=table.get(GOOGLE_FONTS_ENV),
            cache=store,
            env={},
        ),
        FontSpaceProvider(text_fetcher, env=table),
    ]
