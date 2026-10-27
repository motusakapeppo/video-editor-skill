"""vx.fontsource: licences from page text, robots honoured, cache, flag off.

No network: every provider receives a fake fetcher.

Run: python tests/test_fontsource.py
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import contract, fontsource, features, settings  # noqa: E402

PASS = 0
FAIL = 0

BASE = "https://fonts.example"
ROBOTS_ALLOW = "User-agent: *\nDisallow: /private/\n"
ROBOTS_DENY = "User-agent: *\nDisallow: /search.php\n"

RESULTS = """
<html><body>
<div class="preview"><a href="/alpha-condensed.font"><span class="name">Alpha Condensed</span></a></div>
<div class="preview"><a href="/beta-soft.font" title="Download Beta Soft font">Beta Soft</a></div>
<div class="preview"><a href="/gamma-maybe.font">Gamma Maybe</a></div>
<a href="/dl.php?f=999">scarica</a>
</body></html>
"""
PAGE_COMMERCIAL = """
<html><body><h1>Alpha Condensed</h1>
<p>Licence: Free for commercial use.</p>
<a href="/licence.html">Licence</a>
<a class="dl" href="/dl.php?f=1&amp;n=alpha">Download</a>
</body></html>
"""
PAGE_PERSONAL = """
<html><body><h1>Beta Soft</h1>
<p>Free for personal use only. Commercial licences on request.</p>
<a href="/dl.php?f=2">Download</a>
</body></html>
"""
PAGE_UNKNOWN = """
<html><body><h1>Gamma Maybe</h1>
<p>Read the author page for terms.</p>
</body></html>
"""


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:64} {detail}")


def enable(on: bool = True) -> None:
    value = "on" if on else "off"
    settings.apply(settings.build(overrides=[f"features.font_search={value}"]))


class FakeFetcher:
    def __init__(self, pages):
        self.pages = dict(pages)
        self.calls = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if url not in self.pages:
            raise fontsource.FetchError(f"nessun fixture per {url}")
        return self.pages[url]

    def hits(self, needle: str):
        return [url for url in self.calls if needle in url]


def pages(robots: str = ROBOTS_ALLOW) -> dict:
    return {
        BASE + "/robots.txt": robots,
        BASE + "/search.php?q=" + quote_plus("condensed bold"): RESULTS,
        BASE + "/alpha-condensed.font": PAGE_COMMERCIAL,
        BASE + "/beta-soft.font": PAGE_PERSONAL,
        BASE + "/gamma-maybe.font": PAGE_UNKNOWN,
    }


def provider(fetcher, **kwargs) -> fontsource.DafontProvider:
    kwargs.setdefault("rate_limit_s", 0.0)
    kwargs.setdefault("cache", fontsource.PageCache(None))
    return fontsource.DafontProvider(fetcher, base_url=BASE, **kwargs)


def test_classification() -> None:
    print("\n-- classify_licence")
    cases = [
        ("Licence: Free for commercial use.", "commercial"),
        ("Free for personal and commercial use", "commercial"),
        ("Released under the SIL Open Font License", "commercial"),
        ("This is public domain", "commercial"),
        ("Licensed CC0", "commercial"),
        ("Free for personal use", "personal"),
        ("Personal use only", "personal"),
        ("Not for commercial use", "personal"),
        ("Commercial use is not allowed", "personal"),
        ("Non-commercial use only", "personal"),
        ("Donationware", "unknown"),
        ("No terms on this page", "unknown"),
        ("", "unknown"),
    ]
    for text, expected in cases:
        got, matched = fontsource.classify_licence(text)
        check(f"{text[:36]!r} -> {expected}", got == expected, f"{got} via {matched!r}")
    got, matched = fontsource.classify_licence("Donationware")
    check(
        "donationware registra la frase",
        got == "unknown" and matched == "donationware",
        matched,
    )
    got, matched = fontsource.classify_licence("No terms on this page")
    check("unknown non inventa una frase", matched == "", matched)


def test_search_classifies() -> None:
    print("\n-- DafontProvider.search")
    enable(True)
    try:
        fetcher = FakeFetcher(pages())
        found = provider(fetcher).search("condensed bold", limit=10)
        check("tre risultati", len(found) == 3, [c.family for c in found])
        check(
            "famiglie in ordine di pagina",
            [c.family for c in found]
            == ["Alpha Condensed", "Beta Soft", "Gamma Maybe"],
            [c.family for c in found],
        )
        check(
            "le tre licence sono classificate",
            [c.licence for c in found] == ["commercial", "personal", "unknown"],
            [c.licence for c in found],
        )
        commercial = found[0]
        check(
            "licence_url estratta",
            commercial.licence_url.endswith("/licence.html"),
            commercial.licence_url,
        )
        check(
            "download_url esposta",
            "/dl.php?f=1" in commercial.download_url,
            commercial.download_url,
        )
        check(
            "source_url e' la pagina del font",
            commercial.source_url.endswith(".font"),
            commercial.source_url,
        )
        check(
            "nessun download durante la ricerca",
            fetcher.hits("/dl.php") == [],
            fetcher.hits("/dl.php"),
        )
        check(
            "limite rispettato",
            len(provider(FakeFetcher(pages())).search("condensed bold", limit=1)) == 1,
        )
        rec = commercial.to_record("/fonts/alpha.ttf", "0" * 64)
        check("to_record conserva la licence", rec.licence == "commercial")
        check("to_record conserva source_url", rec.source_url == commercial.source_url)
        check("auto_selectable solo per commercial", commercial.auto_selectable is True)
        check("personal non auto_selectable", found[1].auto_selectable is False)

        try:
            fontsource.Candidate(family="X", licence="gpl")
            check("Candidate rifiuta licence ignota", False, "nessuna eccezione")
        except contract.VxError:
            check("Candidate rifiuta licence ignota", True)

        fetcher = FakeFetcher(pages())
        none = provider(fetcher).search("condensed bold", limit=0)
        check("limit=0 non cerca", none == [] and fetcher.calls == [])
    finally:
        settings.reset()


def test_robots() -> None:
    print("\n-- robots.txt")
    enable(True)
    try:
        fetcher = FakeFetcher({BASE + "/robots.txt": ROBOTS_DENY})
        prov = provider(fetcher)
        health = prov.probe()
        check(
            "robots disallow -> non disponibile",
            health.available is False,
            health.to_dict(),
        )
        check(
            "reason robots_disallowed",
            health.reason == "robots_disallowed",
            health.reason,
        )
        check("level blocked", health.level == "blocked", health.level)
        check("robots fetched una volta sola", len(fetcher.hits("/robots.txt")) == 1)
        out = prov.search("condensed bold", limit=5)
        check("search vuota", out == [], out)
        check(
            "ZERO richieste all'endpoint di ricerca",
            fetcher.hits("/search.php") == [],
            fetcher.hits("/search.php"),
        )
        check(
            "solo robots e' stato letto",
            fetcher.calls == [BASE + "/robots.txt"],
            fetcher.calls,
        )

        policy = fontsource.RobotsPolicy.parse(ROBOTS_ALLOW)
        check("robots permissivo: font page ok", policy.allows("/alpha.font") is True)
        check("robots permissivo: search ok", policy.allows("/search.php?q=x") is True)

        wildcard = fontsource.RobotsPolicy.parse(
            "User-agent: *\nDisallow: /private/\nAllow: /private/ok/\n"
        )
        check("Allow piu' lungo vince", wildcard.allows("/private/ok/x") is True)
        check("Disallow piu' lungo vince", wildcard.allows("/private/x") is False)

        specific = fontsource.RobotsPolicy.parse(
            "User-agent: vx-font-provider\nAllow: /\n\nUser-agent: *\nDisallow: /\n"
        )
        check(
            "il gruppo del nostro UA vince su *", specific.allows("/search.php") is True
        )

        empty = fontsource.RobotsPolicy.parse("User-agent: *\nDisallow:\n")
        check("Disallow vuoto = tutto permesso", empty.allows("/anywhere") is True)
        check(
            "query string ignorata dal match", policy.allows("/search.php?q=1") is True
        )
    finally:
        settings.reset()


def test_cache() -> None:
    print("\n-- PageCache")
    enable(True)
    try:
        fetcher = FakeFetcher(pages())
        prov = provider(fetcher)
        first = prov.search("condensed bold", limit=10)
        after_first = len(fetcher.calls)
        second = prov.search("condensed bold", limit=10)
        check(
            "stessa ricerca -> stessi risultati",
            [c.to_dict() for c in first] == [c.to_dict() for c in second],
        )
        check(
            "la seconda ricerca non fa fetch",
            len(fetcher.calls) == after_first,
            fetcher.calls[after_first:],
        )
        check(
            "la pagina di ricerca e' stata letta una volta sola",
            len(fetcher.hits("/search.php")) == 1,
            fetcher.hits("/search.php"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            first_fetcher = FakeFetcher(pages())
            provider(first_fetcher, cache=fontsource.PageCache(tmp)).search(
                "condensed bold", limit=10
            )
            check("cache su disco scritta", (Path(tmp) / "pages.json").is_file())
            second_fetcher = FakeFetcher(pages())
            again = provider(second_fetcher, cache=fontsource.PageCache(tmp)).search(
                "condensed bold", limit=10
            )
            check(
                "nuova istanza, zero fetch dalla cache JSON",
                second_fetcher.calls == [],
                second_fetcher.calls,
            )
            check(
                "nuova istanza, stesse licenze",
                [c.licence for c in again] == ["commercial", "personal", "unknown"],
            )

            expired = fontsource.PageCache(tmp, ttl_s=0)
            check(
                "TTL 0 considera scaduta la pagina",
                expired.get(BASE + "/alpha-condensed.font") is None,
            )
            fresh = fontsource.PageCache(tmp)
            check(
                "TTL 7 giorni considera valida la pagina",
                fresh.get(BASE + "/alpha-condensed.font") is not None,
            )
        check("TTL dichiarato a 7 giorni", fontsource.CACHE_TTL_S == 7 * 24 * 60 * 60)
    finally:
        settings.reset()


def test_feature_off() -> None:
    print("\n-- feature font_search")
    settings.reset()
    try:
        check("font_search dichiarata", "font_search" in features.FEATURES)
        check(
            "font_search spenta di default",
            features.FEATURES["font_search"].default is False,
        )
        check("font_search effettiva spenta", features.enabled("font_search") is False)

        fetcher = FakeFetcher(pages())
        prov = provider(fetcher)
        health = prov.probe()
        check(
            "flag off -> non disponibile", health.available is False, health.to_dict()
        )
        check("reason feature_off", health.reason == "feature_off", health.reason)
        check("level blocked", health.level == "blocked", health.level)
        check("flag off -> nessun fetch", fetcher.calls == [], fetcher.calls)
        check("flag off -> search vuota", prov.search("condensed bold", limit=5) == [])
        check("flag off -> ancora nessun fetch", fetcher.calls == [], fetcher.calls)

        enable(True)
        check("dopo --override il flag e' on", features.enabled("font_search") is True)
        health = prov.probe(refresh=True)
        check(
            "flag on e robots ok -> disponibile",
            health.available is True,
            health.to_dict(),
        )
        check("last_success popolato", bool(health.last_success), health.last_success)
        check(
            "flag on -> la ricerca parte",
            len(prov.search("condensed bold", limit=3)) == 3,
        )
    finally:
        settings.reset()


def test_local_folder() -> None:
    print("\n-- LocalFolderProvider")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        alpha = root / "Alpha-Condensed.ttf"
        alpha.write_bytes(b"a" * 128)
        (root / "Alpha-Condensed.licence.txt").write_text(
            "Free for commercial use. Terms: https://example.test/alpha-terms\n",
            encoding="utf-8",
        )
        beta = root / "Beta.ttf"
        beta.write_bytes(b"b" * 128)
        (root / "notes.txt").write_text("not a font", encoding="utf-8")

        prov = fontsource.LocalFolderProvider(root)
        health = prov.probe()
        check("cartella locale disponibile", health.available is True, health.to_dict())
        check("reason ok", health.reason == "ok", health.reason)

        found = prov.search("alpha", limit=5)
        check("trova il font per nome", len(found) == 1, [c.family for c in found])
        check("licence dal sidecar", found[0].licence == "commercial", found[0].licence)
        check(
            "licence_url dal sidecar",
            found[0].licence_url.startswith("https://example.test"),
            found[0].licence_url,
        )
        check(
            "sha256 calcolato",
            found[0].sha256 == hashlib.sha256(alpha.read_bytes()).hexdigest(),
        )
        check("local_file assoluto", Path(found[0].local_file).is_absolute())

        no_sidecar = prov.search("beta", limit=5)
        check(
            "senza sidecar la licence e' unknown",
            [c.licence for c in no_sidecar] == ["unknown"],
        )
        check(
            "solo estensioni font",
            all(c.family != "notes" for c in prov.search("", limit=10)),
            [c.family for c in prov.search("", limit=10)],
        )
        check(
            "probe su cartella assente",
            fontsource.LocalFolderProvider(root / "nope").probe().available is False,
        )


def test_health_and_ua() -> None:
    print("\n-- Health e User-Agent")
    health = fontsource.Health(
        name="x",
        available=True,
        reason="ok",
        level="ok",
        last_success="2026-10-27T00:00:00+00:00",
    )
    check(
        "Health.to_dict espone i cinque campi",
        set(health.to_dict())
        == {"name", "available", "reason", "level", "last_success"},
        health.to_dict(),
    )
    check("User-Agent dichiara il tool", "vx-font-provider" in fontsource.USER_AGENT)
    check(
        "User-Agent non impersona un browser",
        "mozilla" not in fontsource.USER_AGENT.lower(),
    )
    check("timeout dichiarato", fontsource.FETCH_TIMEOUT_S > 0)


def main() -> int:
    test_classification()
    test_search_classifies()
    test_robots()
    test_cache()
    test_feature_off()
    test_local_folder()
    test_health_and_ua()
    settings.reset()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
