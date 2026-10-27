"""vx.fontapis: four API providers, injected fetchers, no network.

Run: python tests/test_fontapis.py
"""

from __future__ import annotations

import base64
import io
import json
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import contract, features, fontapis, fontsource, settings  # noqa: E402

PASS = 0
FAIL = 0

OFL_BODY = """SIL Open Font License, Version 1.1
This Font Software is licensed under the SIL Open Font License.
"""


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:66} {detail}")


def enable(on: bool = True) -> None:
    value = "on" if on else "off"
    settings.apply(settings.build(overrides=[f"features.font_search={value}"]))


class FakeFetcher:
    def __init__(self, pages=None):
        self.pages = dict(pages or {})
        self.calls = []
        self.headers = []

    def __call__(self, url: str, headers=None):
        self.calls.append(url)
        self.headers.append(dict(headers or {}))
        if url not in self.pages:
            raise fontsource.FetchError(f"HTTP 404 per {url}")
        value = self.pages[url]
        if isinstance(value, BaseException):
            raise value
        return value

    def hosts(self):
        from urllib.parse import urlsplit

        return sorted({urlsplit(url).hostname or "" for url in self.calls})


def make_tar(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def github_provider(fetcher, **kwargs):
    kwargs.setdefault("rate_limit_s", 0.0)
    kwargs.setdefault("sleep", lambda seconds: None)
    return fontapis.GithubFontProvider(fetcher, **kwargs)


def github_fixtures(provider, rate=None):
    rate = rate or {"resources": {"core": {"limit": 60, "remaining": 59}}}
    provider.fetcher.pages.update(
        {
            provider.rate_limit_url(): json.dumps(rate),
            provider.search_url("inter", 2): json.dumps(
                {
                    "total_count": 2,
                    "items": [
                        {
                            "full_name": "acme/alpha-font",
                            "default_branch": "main",
                            "html_url": "https://github.com/acme/alpha-font",
                        },
                        {
                            "full_name": "acme/beta-font",
                            "default_branch": "trunk",
                            "html_url": "https://github.com/acme/beta-font",
                        },
                    ],
                }
            ),
            provider.tree_url("acme/alpha-font", "main"): json.dumps(
                {
                    "tree": [
                        {"path": "README.md", "type": "blob"},
                        {"path": "fonts/Alpha-Regular.ttf", "type": "blob"},
                    ]
                }
            ),
            provider.tree_url("acme/beta-font", "trunk"): json.dumps(
                {"tree": [{"path": "Beta-Bold.otf", "type": "blob"}]}
            ),
            provider.licence_url("acme/alpha-font"): json.dumps(
                {
                    "content": base64.b64encode(OFL_BODY.encode("utf-8")).decode(),
                    "html_url": "https://github.com/acme/alpha-font/blob/main/LICENSE",
                    "license": {"spdx_id": "OFL-1.1"},
                }
            ),
        }
    )


def test_github() -> None:
    print("\n-- GitHub")
    settings.reset()
    enable(True)
    fetcher = FakeFetcher()
    gh = github_provider(fetcher)
    github_fixtures(gh)

    health = gh.probe()
    check("probe disponibile", health.available is True, health.to_dict())
    check("detail porta remaining/limit", "59/60" in health.detail, health.detail)
    check("reason ok", health.reason == "ok", health.reason)
    check(
        "senza token: nessun Authorization", "Authorization" not in fetcher.headers[0]
    )
    check(
        "Accept applicativo",
        fetcher.headers[0].get("Accept") == "application/vnd.github+json",
        fetcher.headers[0],
    )
    check("token assente -> non autenticato", gh.authenticated is False)

    found = gh.search("inter", limit=2)
    check("due hit", len(found) == 2, [c.family for c in found])
    check("famiglia dal nome file", found[0].family == "Alpha", found[0].family)
    check(
        "LICENSE OFL -> commercial",
        found[0].licence == "commercial",
        (found[0].licence, found[0].matched),
    )
    check(
        "matched dice la frase",
        "sil open font license" in found[0].matched.lower(),
        found[0].matched,
    )
    check(
        "licence_url dal LICENSE",
        found[0].licence_url.endswith("/LICENSE"),
        found[0].licence_url,
    )
    check(
        "raw url col branch di default",
        found[0].download_url
        == "https://raw.githubusercontent.com/acme/alpha-font/main/fonts/Alpha-Regular.ttf",
        found[0].download_url,
    )
    check(
        "senza LICENSE la licenza resta unknown",
        found[1].licence == "unknown",
        (found[1].licence, found[1].matched),
    )
    check(
        "unknown non e' auto-selezionabile",
        found[1].auto_selectable is False,
    )
    check(
        "secondo branch rispettato",
        "/trunk/Beta-Bold.otf" in found[1].download_url,
        found[1].download_url,
    )
    check(
        "solo host API ammessi",
        set(fetcher.hosts()) <= {"api.github.com", "raw.githubusercontent.com"},
        fetcher.hosts(),
    )
    check(
        "nessun HTML", all(not url.endswith((".html", ".php")) for url in fetcher.calls)
    )
    check("search e' memoizzata", gh.search("inter", limit=2) == found)

    limited_fetcher = FakeFetcher()
    limited = github_provider(limited_fetcher)
    limited_fetcher.pages[limited.rate_limit_url()] = json.dumps(
        {"resources": {"core": {"limit": 60, "remaining": 0}}}
    )
    health = limited.probe()
    check("remaining 0 -> rate_limited", health.reason == "rate_limited", health.reason)
    check("rate_limited e' bloccato", health.level == "blocked", health.level)
    check("detail dice 0/60", "0/60" in health.detail, health.detail)
    before = len(limited_fetcher.calls)
    check("search senza hit", limited.search("inter", limit=5) == [])
    check(
        "rate_limited -> zero fetch di ricerca",
        len(limited_fetcher.calls) == before,
        limited_fetcher.calls,
    )

    token = "ghp_FAKE_TOKEN_9876543210"
    tok_fetcher = FakeFetcher()
    tok = github_provider(tok_fetcher, token=token)
    github_fixtures(tok)
    tok_health = tok.probe()
    tok.search("inter", limit=2)
    blob = json.dumps(tok_health.to_dict(), ensure_ascii=False) + repr(tok)
    for candidate in tok.search("inter", limit=2):
        blob += json.dumps(candidate.to_dict(), ensure_ascii=False)
    check("token mai nei report", token not in blob)
    check("token non in repr", "FAKE_TOKEN" not in repr(tok), repr(tok))
    check(
        "token inviato come header",
        tok_fetcher.headers[0].get("Authorization") == f"Bearer {token}",
    )
    check("authenticated True senza esporre il token", tok.authenticated is True)


def test_github_host_guard() -> None:
    print("\n-- GitHub: solo host API")
    settings.reset()
    enable(True)
    fetcher = FakeFetcher({"https://www.dafont.com/search.php": "<html></html>"})
    gh = github_provider(fetcher)
    rejected = False
    try:
        gh._call_fetch("https://www.dafont.com/search.php", {})
    except contract.VxError as exc:
        rejected = "host non ammesso" in str(exc)
    check("host HTML rifiutato", rejected)


def test_fontsource() -> None:
    print("\n-- Fontsource")
    settings.reset()
    enable(True)
    fetcher = FakeFetcher()
    binary = FakeFetcher()
    fs = fontapis.FontsourceProvider(fetcher, byte_fetcher=binary)
    fetcher.pages.update(
        {
            fs.ping_url(): "{}",
            fs.search_url("alpha", 2): json.dumps(
                {
                    "objects": [
                        {
                            "package": {
                                "name": "@fontsource/alpha",
                                "version": "5.0.0",
                                "links": {
                                    "npm": "https://www.npmjs.com/package/@fontsource/alpha"
                                },
                            }
                        },
                        {"package": {"name": "@fontsource/beta", "version": "1.0.0"}},
                        {"package": {"name": "notfontsource/gamma"}},
                    ]
                }
            ),
            fs.latest_url("@fontsource/alpha"): json.dumps(
                {
                    "version": "5.0.0",
                    "license": "OFL-1.1",
                    "dist": {
                        "tarball": "https://registry.npmjs.org/@fontsource/alpha/-/alpha-5.0.0.tgz"
                    },
                }
            ),
            fs.latest_url("@fontsource/beta"): json.dumps(
                {
                    "version": "1.0.0",
                    "license": "proprietary",
                    "dist": {
                        "tarball": "https://registry.npmjs.org/@fontsource/beta/-/beta-1.0.0.tgz"
                    },
                }
            ),
        }
    )

    health = fs.probe()
    check("registry raggiungibile", health.available is True, health.to_dict())
    check("probe non salva nel cache", fs.cache.get(fs.ping_url()) is None)

    found = fs.search("alpha", limit=2)
    check(
        "solo @fontsource",
        [c.family for c in found] == ["Alpha", "Beta"],
        [c.family for c in found],
    )
    check("OFL-1.1 -> commercial", found[0].licence == "commercial", found[0].licence)
    check("proprietary -> unknown", found[1].licence == "unknown", found[1].licence)
    check(
        "download_url e' il tarball",
        found[0].download_url.endswith("alpha-5.0.0.tgz"),
        found[0].download_url,
    )
    check(
        "search non scarica",
        binary.calls == [],
        binary.calls,
    )
    check(
        "record dice quale licenza",
        found[0].matched == "OFL-1.1",
        found[0].matched,
    )
    check(
        "licence_url punta allo SPDX",
        found[0].licence_url == "https://spdx.org/licenses/OFL-1.1.html",
        found[0].licence_url,
    )
    check("unknown senza licence_url", found[1].licence_url == "")

    good = make_tar(
        [
            ("package/README.md", b"readme"),
            ("package/files/Alpha-Regular.ttf", b"FONT-BYTES"),
        ]
    )
    binary.pages[found[0].download_url] = good
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "alpha.ttf"
        path = fs.fetch_font(found[0], dest)
        check("fetch_font restituisce dest", Path(path) == dest, path)
        check("file scritto", dest.is_file(), dest)
        check("contenuto del ttf", dest.read_bytes() == b"FONT-BYTES")
        check("nessun .part residuo", list(Path(tmp).glob("*.part")) == [])
        check("un solo download", len(binary.calls) == 1, binary.calls)

        for label, name in (
            ("traversal ../", "../evil.ttf"),
            ("assoluto", "/abs/evil.ttf"),
            ("unita' Windows", "C:/evil.ttf"),
        ):
            binary.pages[found[0].download_url] = make_tar([(name, b"EVIL")])
            refused = False
            try:
                fs.fetch_font(found[0], Path(tmp) / "evil.ttf")
            except fontapis.ArchiveRejected as exc:
                refused = "membro non sicuro" in str(exc)
            check(f"{label} rifiutato", refused)
            check(
                f"{label} non scritto",
                not (Path(tmp) / "evil.ttf").exists()
                and not (Path(tmp).parent / "evil.ttf").exists(),
            )

        binary.pages[found[0].download_url] = make_tar([("README.md", b"nothing")])
        no_font = False
        try:
            fs.fetch_font(found[0], Path(tmp) / "none.ttf")
        except fontapis.ArchiveRejected as exc:
            no_font = "nessun file" in str(exc)
        check("tarball senza font rifiutato", no_font)

        binary.pages[found[0].download_url] = b"not a tarball"
        broken = False
        try:
            fs.fetch_font(found[0], Path(tmp) / "broken.ttf")
        except fontapis.ArchiveRejected:
            broken = True
        check("tarball illeggibile rifiutato", broken)

    check(
        "ArchiveRejected e' un VxError",
        issubclass(fontapis.ArchiveRejected, contract.VxError),
    )


def test_fontsource_licences() -> None:
    print("\n-- mappa licenze registry")
    for value, expected in (
        ("OFL-1.1", "commercial"),
        ("Apache-2.0", "commercial"),
        ("MIT", "commercial"),
        ("mit", "commercial"),
        ("proprietary", "unknown"),
        ("UNLICENSED", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
    ):
        licence, _spdx = fontapis.licence_from_registry(value)
        check(f"{value!r} -> {expected}", licence == expected, licence)


def test_google() -> None:
    print("\n-- Google Fonts")
    settings.reset()
    enable(True)
    empty = FakeFetcher()
    google = fontapis.GoogleFontsProvider(empty, env={})
    health = google.probe()
    check(
        "senza chiave -> missing_credentials",
        health.reason == "missing_credentials",
        health.reason,
    )
    check("missing_credentials e' bloccato", health.level == "blocked", health.level)
    check(
        "detail nomina la variabile",
        "GOOGLE_FONTS_API_KEY" in health.detail,
        health.detail,
    )
    check("zero fetch senza chiave", empty.calls == [], empty.calls)
    check("search senza chiave vuota", google.search("inter", limit=3) == [])
    check("ancora zero fetch", empty.calls == [], empty.calls)

    key = "FAKE-GOOGLE-KEY-123456"
    boom_calls = []

    def boom(url, headers=None):
        boom_calls.append(url)
        raise fontsource.FetchError(f"HTTP 401 per {url}")

    broken = fontapis.GoogleFontsProvider(boom, api_key=key, env={})
    broken_health = broken.probe()
    dumped = json.dumps(broken_health.to_dict(), ensure_ascii=False)
    check("chiave presente -> tenta la richiesta", len(boom_calls) == 1, boom_calls)
    check(
        "errore dichiarato", broken_health.reason == "unreachable", broken_health.reason
    )
    check(
        "chiave grezza assente dal detail",
        key not in broken_health.detail,
        broken_health.detail,
    )
    check("chiave grezza assente dal report", key not in dumped)
    check("detail mascherato", "***" in broken_health.detail, broken_health.detail)
    check("repr non espone la chiave", key not in repr(broken))
    check("configured True", broken.configured is True)

    catalog = {
        "items": [
            {
                "family": "Inter",
                "category": "sans-serif",
                "variants": ["regular", "700"],
                "files": {
                    "regular": "https://fonts.gstatic.com/s/inter/v1/Inter-Regular.ttf"
                },
            },
            {
                "family": "Lora",
                "category": "serif",
                "variants": ["regular"],
                "files": {
                    "regular": "https://fonts.gstatic.com/s/lora/v1/Lora-Regular.ttf"
                },
            },
        ]
    }
    live = FakeFetcher()
    g = fontapis.GoogleFontsProvider(live, api_key=key, env={})
    live.pages[g.request_url()] = json.dumps(catalog)
    found = g.search("inter", limit=5)
    check(
        "filtro per famiglia",
        [c.family for c in found] == ["Inter"],
        [c.family for c in found],
    )
    check("licenza commercial", found[0].licence == "commercial", found[0].licence)
    check(
        "record dice OFL/Apache",
        "OFL" in found[0].matched and "Apache" in found[0].matched,
        found[0].matched,
    )
    check(
        "download_url dal campo files",
        found[0].download_url.endswith("Inter-Regular.ttf"),
        found[0].download_url,
    )
    check(
        "styles dalle varianti", found[0].styles == ["regular", "700"], found[0].styles
    )
    check(
        "filtro per categoria",
        [c.family for c in g.search("sans", limit=5)] == ["Inter"],
        [c.family for c in g.search("sans", limit=5)],
    )
    check("catalogo in cache: un solo fetch", len(live.calls) == 1, live.calls)
    check(
        "la chiave non finisce nel cache",
        key not in json.dumps(g.cache._load(), ensure_ascii=False),
    )
    check(
        "redact_url maschera key e conserva il resto",
        fontapis.redact_url("https://x/y?a=1&key=SECRET&b=2")
        == "https://x/y?a=1&key=***&b=2",
        fontapis.redact_url("https://x/y?a=1&key=SECRET&b=2"),
    )


def test_fontspace() -> None:
    print("\n-- FontSpace")
    settings.reset()
    enable(True)
    fetcher = FakeFetcher()
    space = fontapis.FontSpaceProvider(fetcher, env={})
    health = space.probe()
    check("not_configured", health.reason == "not_configured", health.reason)
    check("not_configured e' bloccato", health.level == "blocked", health.level)
    check(
        "detail nomina la base URL",
        "FONTSPACE_BASE_URL" in health.detail,
        health.detail,
    )
    check(
        "detail nomina la API key", "FONTSPACE_API_KEY" in health.detail, health.detail
    )
    check("zero fetch", fetcher.calls == [], fetcher.calls)
    check("search vuota", space.search("anything", limit=5) == [])
    check("ancora zero fetch", fetcher.calls == [], fetcher.calls)

    configured = fontapis.FontSpaceProvider(
        fetcher,
        env={
            "FONTSPACE_BASE_URL": "https://api.fontspace.example",
            "FONTSPACE_API_KEY": "k",
        },
    )
    check(
        "configurato ma endpoint ignoto -> not_configured",
        configured.probe().reason == "not_configured",
    )
    check("nessun endpoint inventato", fetcher.calls == [], fetcher.calls)


def test_feature_off() -> None:
    print("\n-- flag font_search spento")
    settings.reset()
    try:
        check("font_search dichiarata", "font_search" in features.FEATURES)
        check("spenta di default", features.enabled("font_search") is False)
        fetcher = FakeFetcher()
        built = fontapis.providers({}, fetcher=fetcher)
        check(
            "ordine deterministico",
            [p.name for p in built]
            == ["github", "fontsource", "google_fonts", "fontspace"],
            [p.name for p in built],
        )
        for provider in built:
            health = provider.probe()
            check(
                f"{provider.name}: feature_off",
                health.reason == "feature_off" and health.available is False,
                health.reason,
            )
        check("flag off -> zero fetch", fetcher.calls == [], fetcher.calls)
        for provider in built:
            if provider.name != "fontspace":
                check(
                    f"{provider.name}: search vuota a flag off",
                    provider.search("x", limit=3) == [],
                )
        check("flag off -> ancora zero fetch", fetcher.calls == [], fetcher.calls)
    finally:
        settings.reset()


def test_health_shape() -> None:
    print("\n-- Health")
    health = fontapis.Health(name="x", available=True, reason="ok", detail="59/60")
    check("e' un Health di fontsource", isinstance(health, fontsource.Health))
    check(
        "to_dict ha i cinque campi piu' detail",
        set(health.to_dict())
        == {"name", "available", "reason", "level", "last_success", "detail"},
        health.to_dict(),
    )
    check(
        "FetchError condiviso",
        issubclass(fontapis.FetchError, fontsource.FetchError),
    )


def main() -> int:
    test_github()
    test_github_host_guard()
    test_fontsource()
    test_fontsource_licences()
    test_google()
    test_fontspace()
    test_feature_off()
    test_health_shape()
    settings.reset()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
