"""F25-T09: il lettore di brand, con robots rispettato e cache per hash dell'URL.

Scenario (server di prova locale, nessuna rete esterna):

S1 — una pagina piu' gli asset collegati: il registro delle richieste mostra
     zero richieste fuori dal dominio, l'asset su un altro host e' saltato e
     registrato come saltato, i due asset veri sono su disco con sha256.
S2 — una pagina esclusa da `robots.txt` e' `brand_robots` e cita la regola che
     l'ha esclusa.
S3 — la seconda esecuzione sulla stessa cache fa **zero** richieste di rete.
S4 — una pagina irraggiungibile e' `brand_unavailable` con il motivo, non un
     traceback.

    python tests/test_brand.py
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import brand  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0

LOGO = b"\x89PNG\r\n\x1a\n" + b"vx" * 40
CSS = b"body{color:#111}" * 10

PAGE = """<!doctype html><html><head><title>Studio  Vx</title>
<link rel="stylesheet" href="/tema.css"></head>
<body><img src="/logo.png">
<img src="http://127.0.0.1:{far}/lontano.png">
<img src="/manca.png">
<a href="/contatti">contatti</a></body></html>
"""


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:60} {reason}")


def serve(far_port: int):
    hits: list = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D102
            pass

        def do_GET(self):  # noqa: D102
            hits.append(self.path)
            if self.path == "/robots.txt":
                self._send(b"User-agent: *\nDisallow: /privato\n", "text/plain")
            elif self.path == "/":
                self._send(PAGE.format(far=far_port).encode(), "text/html; charset=utf-8")
            elif self.path == "/privato/":
                self._send(b"<html><title>privato</title></html>", "text/html")
            elif self.path == "/logo.png":
                self._send(LOGO, "image/png")
            elif self.path == "/tema.css":
                self._send(CSS, "text/css")
            else:
                self.send_error(404)

        def _send(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, hits


def main() -> int:
    server, hits = serve(1)
    base = f"http://127.0.0.1:{server.server_address[1]}"

    with tempfile.TemporaryDirectory(prefix="vx-brand-") as raw:
        cache = Path(raw) / "cache"
        try:
            # S1 ------------------------------------------------------------ #
            record = brand.read(base + "/", cache_dir=cache)
            check(
                "S1 one page and its same-origin assets",
                record["title"] == "Studio Vx" and len(record["assets"]) == 2,
                f"{record['title']!r}, {[a['suffix'] for a in record['assets']]}",
            )
            check(
                "S1 zero requests outside the domain",
                record["stats"]["off_domain_requests"] == 0
                and not [hit for hit in hits if hit.startswith("/lontano")],
                f"{len(record['requests'])} requests, {hits}",
            )
            check(
                "S1 the off-domain asset is recorded as skipped",
                any("fuori dominio" in item["why"] for item in record["skipped"]),
                record["skipped"][:1],
            )
            on_disk = sorted(p.name for p in (cache / "assets").glob("*.bin"))
            check(
                "S1 the assets are on disk, named by the URL hash",
                len(on_disk) == 2
                and hashlib.sha256(f"{base}/logo.png".encode()).hexdigest() + ".bin" in on_disk
                and hashlib.sha256(f"{base}/tema.css".encode()).hexdigest() + ".bin" in on_disk,
                on_disk,
            )
            check(
                "S1 a missing asset is skipped, not fatal",
                any("brand_unavailable" in item["why"] for item in record["skipped"]),
                [item["why"] for item in record["skipped"]],
            )

            # S2 ------------------------------------------------------------ #
            try:
                brand.read(base + "/privato/", cache_dir=cache)
                code, detail = "", ""
            except brand.BrandError as exc:
                code, detail = exc.code, exc.detail
            check("S2 a page disallowed by robots.txt is refused", code == "brand_robots", code)
            check(
                "S2 and the refusal cites the rule",
                "Disallow: /privato" in detail,
                detail[:80],
            )
            check(
                "S2 and the disallowed path is never requested",
                "/privato/" not in hits,
                hits,
            )

            # S3 ------------------------------------------------------------ #
            before = len(hits)
            again = brand.read(base + "/", cache_dir=cache)
            check(
                "S3 the second run makes zero network requests",
                again["stats"]["network"] == 0 and len(hits) == before,
                f"{again['stats']['network']} chiamate, {len(hits) - before} richieste al server",
            )
            check(
                "S3 and answers from the same record",
                [a["sha256"] for a in again["assets"]] == [a["sha256"] for a in record["assets"]],
                "",
            )

            # S4 ------------------------------------------------------------ #
            closed = "http://127.0.0.1:9/"
            try:
                brand.read(closed, cache_dir=Path(raw) / "vuota")
                unreachable_code, unreachable_detail = "", ""
            except brand.BrandError as exc:
                unreachable_code, unreachable_detail = exc.code, exc.detail
            check(
                "S4 an unreachable page is brand_unavailable",
                unreachable_code == "brand_unavailable",
                unreachable_code,
            )
            check(
                "S4 with a reason, not a traceback",
                "raggiungibile" in unreachable_detail,
                unreachable_detail[:70],
            )
        finally:
            server.shutdown()
            server.server_close()

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
