"""F25-T10: `brand.json` con colori, logo, font, testi, prezzi e provenienza.

Scenario (server di prova locale, pagina e CSS noti):

S1 — il documento valida contro lo schema `vx.brandjson/1`.
S2 — la pagina di prova produce i colori e i font attesi: `#0a5cff` (due
     occorrenze, quota misurata), `#ff6600`, `#112233`; famiglia `Inter`.
S3 — ogni campo presente ha la sua provenienza (file CSS, selettore, dato
     strutturato), logo compreso.
S4 — un colore senza provenienza fa fallire la validazione, e l'errore dice
     quale colore.
S5 — il documento scritto su disco è rileggibile e valida ancora.

    python tests/test_brandjson.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import brand, brandjson  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0

CSS = """@font-face { font-family: 'Inter'; src: url(/inter.woff2) format('woff2'); }
body { font-family: 'Inter', sans-serif; color: #0a5cff; }
.brand { --accent: #ff6600; background: #0a5cff; }
"""

PAGE = """<!doctype html><html><head><title>Vx Studio</title>
<link rel="icon" href="/favicon.png">
<link rel="stylesheet" href="/tema.css">
<style>.inline { color: #112233; }</style></head>
<body><h1>Montaggio</h1><p>Dal 2014, tagli che tengono.</p>
<span class="inline">nota</span>
<img alt="Vx Studio logo" src="/logo.svg">
<div>Prezzo: \u20ac 49,00</div>
<script type="application/ld+json">{"@type":"Offer","price":"120.00","priceCurrency":"EUR"}</script>
</body></html>
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


def serve():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D102
            pass

        def do_GET(self):  # noqa: D102
            if self.path == "/robots.txt":
                self._send(b"User-agent: *\n", "text/plain")
            elif self.path == "/":
                self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/tema.css":
                self._send(CSS.encode("utf-8"), "text/css")
            elif self.path in ("/logo.svg", "/favicon.png", "/inter.woff2"):
                self._send(b"vx" * 20, "application/octet-stream")
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
    return server


def main() -> int:
    server = serve()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with tempfile.TemporaryDirectory(prefix="vx-brandjson-") as raw:
            record = brand.read(base + "/", cache_dir=Path(raw) / "cache")
            document = brandjson.build(record)

            # S1 ------------------------------------------------------------ #
            check("S1 brand.json validates against the schema", not brandjson.validate(document), brandjson.validate(document))

            # S2 ------------------------------------------------------------ #
            colours = {item["hex"]: item for item in document["colors"]}
            check(
                "S2 the known page produces the expected colours",
                {"#0a5cff", "#ff6600", "#112233"} <= set(colours),
                sorted(colours),
            )
            check(
                "S2 and the share is measured, not guessed",
                colours["#0a5cff"]["count"] == 2
                and colours["#0a5cff"]["share"] > colours["#ff6600"]["share"],
                f"#0a5cff {colours['#0a5cff']['count']} volte, share {colours['#0a5cff']['share']:.3f}",
            )
            check(
                "S2 the CSS font is read from the stylesheet",
                [font["family"] for font in document["fonts"]] == ["Inter"],
                [font["family"] for font in document["fonts"]],
            )

            # S3 ------------------------------------------------------------ #
            check(
                "S3 every colour carries the file or the inline selector",
                all(item["provenance"].get("file") or item["provenance"].get("selector") for item in document["colors"]),
                [item["provenance"]["kind"] for item in document["colors"]],
            )
            check(
                "S3 the logo carries its selector",
                document["logo"] is not None
                and document["logo"]["url"].endswith("/logo.svg")
                and document["logo"]["provenance"]["selector"] == "img[alt*=logo]",
                document["logo"],
            )
            check(
                "S3 fonts carry the stylesheet file",
                document["fonts"][0]["provenance"]["file"].endswith("/tema.css"),
                document["fonts"][0]["provenance"],
            )
            check(
                "S3 texts carry their tag",
                any(item["text"] == "Montaggio" and item["provenance"]["selector"] == "h1" for item in document["texts"]),
                [item["text"] for item in document["texts"]][:3],
            )
            check(
                "S3 prices come from the text and from structured data",
                {(item["amount"], item["provenance"]["kind"]) for item in document["prices"]}
                == {(49.0, "text"), (120.0, "structured")},
                [(item["amount"], item["provenance"]["kind"]) for item in document["prices"]],
            )

            # S4 ------------------------------------------------------------ #
            broken = json.loads(json.dumps(document))
            broken["colors"][0]["provenance"] = {}
            errors = brandjson.validate(broken)
            check(
                "S4 a colour without provenance fails validation",
                any("colors[0].provenance" in error for error in errors),
                errors[:1],
            )
            missing = json.loads(json.dumps(document))
            del missing["colors"][1]["provenance"]
            check(
                "S4 and so does a missing provenance field",
                any("colors[1].provenance" in error for error in brandjson.validate(missing)),
                brandjson.validate(missing)[:1],
            )

            # S5 ------------------------------------------------------------ #
            target = brandjson.write(document, Path(raw) / "brand.json")
            reloaded = json.loads(target.read_text(encoding="utf-8"))
            check(
                "S5 the written brand.json is readable and still legal",
                target.is_file() and not brandjson.validate(reloaded),
                f"{target.stat().st_size} bytes",
            )
    finally:
        server.shutdown()
        server.server_close()

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
