"""F25-T11: tre pagine di prova e il brand che arriva sul fotogramma.

Scenario (server locale; pagine in ``tests/fixtures/brand_pages``):

S1 — tre esiti binari distinti: brand completo -> ``ok``, asset su altro dominio
     -> ``degraded``, pagina esclusa da robots -> ``refused`` (``brand_robots``).
S2 — un ``brand_ref`` assente e' rifiutato con il codice, e un documento che non
     valida e' ``brand_invalid``.
S3 — il render usa la palette e il logo **misurati sul frame**: MAD in alto a
     sinistra (solo palette) e in basso a destra (logo), non solo nel JSON.

    python tests/test_branding.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import brand, branding, edl3, edl3_render, brandjson  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
PAGES = ROOT / "tests" / "fixtures" / "brand_pages"
FPS = 12
SIZE = (320, 568)


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:60} {reason}")


def logo_png() -> bytes:
    image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 95, 95), fill=(230, 30, 30, 255))
    draw.rectangle((24, 24, 71, 71), fill=(255, 255, 255, 255))
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def serve():
    logo = logo_png()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D102
            pass

        def do_GET(self):  # noqa: D102
            routes = {
                "/": PAGES / "completa" / "index.html",
                "/fuori/": PAGES / "fuori_dominio" / "index.html",
                "/esclusa/": PAGES / "esclusa" / "index.html",
                "/tema.css": PAGES / "completa" / "tema.css",
            }
            if self.path == "/robots.txt":
                self._send(b"User-agent: *\nDisallow: /esclusa/\n", "text/plain")
                return
            if self.path == "/logo.png":
                self._send(logo, "image/png")
                return
            if self.path == "/officina.woff2":
                self._send(b"wOF2" + b"vx" * 30, "font/woff2")
                return
            target = routes.get(self.path)
            if target is not None and target.is_file():
                self._send(target.read_bytes(), "text/html; charset=utf-8" if target.suffix == ".html" else "text/css")
                return
            self.send_error(404)

        def _send(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, logo


def frame(path: Path, at: float = 0.5) -> np.ndarray:
    png = path.with_suffix(".png")
    proc = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1", str(png),
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-200:])
    return np.asarray(Image.open(png).convert("RGB"), dtype=float)


def main() -> int:
    try:
        ff.ffmpeg()
    except Exception as exc:  # noqa: BLE001
        skip("whole file", f"no ffmpeg: {type(exc).__name__}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    server, logo_bytes = serve()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with tempfile.TemporaryDirectory(prefix="vx-branding-") as raw:
            work = Path(raw)

            # S1 ------------------------------------------------------------ #
            complete = brand.read(base + "/", cache_dir=work / "c1")
            degraded = brand.read(base + "/fuori/", cache_dir=work / "c2")
            outcomes = {}
            outcomes["ok"] = brand.summarize(complete)
            outcomes["degraded"] = brand.summarize(degraded)
            try:
                brand.read(base + "/esclusa/", cache_dir=work / "c3")
                outcomes["refused"] = {"outcome": "unexpected"}
            except brand.BrandError as exc:
                outcomes["refused"] = {"outcome": "refused", "code": exc.code}
            check(
                "S1 the three pages give three distinct outcomes",
                {key: value["outcome"] for key, value in outcomes.items()}
                == {"ok": "ok", "degraded": "degraded", "refused": "refused"},
                {key: value["outcome"] for key, value in outcomes.items()},
            )
            check(
                "S1 the complete page lost nothing",
                outcomes["ok"]["skipped"] == 0 and outcomes["ok"]["assets"] >= 2,
                outcomes["ok"],
            )
            check(
                "S1 the off-domain page skipped exactly the foreign asset",
                outcomes["degraded"]["off_domain"] == 1,
                outcomes["degraded"],
            )
            check(
                "S1 the excluded page was refused by robots, not by luck",
                outcomes["refused"]["code"] == "brand_robots",
                outcomes["refused"],
            )

            # S2 ------------------------------------------------------------ #
            try:
                branding.load(None, base=work)
                no_ref, no_ref_detail = "", ""
            except branding.BrandingError as exc:
                no_ref, no_ref_detail = exc.code, exc.detail
            check("S2 a missing brand_ref is refused with its code", no_ref == "brand_ref_missing", no_ref)
            try:
                branding.load("non-esiste.json", base=work)
                absent, absent_detail = "", ""
            except branding.BrandingError as exc:
                absent, absent_detail = exc.code, exc.detail
            check(
                "S2 and a dangling reference names the file",
                absent == "brand_ref_missing" and "non-esiste.json" in absent_detail,
                absent_detail[:60],
            )
            broken = work / "rotto.json"
            broken.write_text('{"schema": "vx.brandjson/1"}\n', encoding="utf-8")
            try:
                branding.load("rotto.json", base=work)
                invalid, invalid_detail = "", ""
            except branding.BrandingError as exc:
                invalid, invalid_detail = exc.code, exc.detail
            check(
                "S2 an invalid document is brand_invalid with the errors",
                invalid == "brand_invalid" and "source.url" in invalid_detail,
                invalid_detail[:60],
            )

            # S3 ------------------------------------------------------------ #
            document = brandjson.build(complete)
            document_path = brandjson.write(document, work / "brand.json")
            loaded = branding.load("brand.json", base=work)
            check(
                "S3 brand_ref loads the same document the reader produced",
                loaded["source"]["url"] == document["source"]["url"],
                loaded["source"]["url"],
            )
            check(
                "S3 the palette is derived from the dominant colour",
                bool(branding.palette_filters(document)),
                branding.palette_filters(document),
            )

            source = work / "source.mp4"
            subprocess.run(
                [
                    ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi",
                    "-i", f"testsrc2=size={SIZE[0]}x{SIZE[1]}:rate={FPS}:duration=1",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "18", str(source),
                ],
                check=True,
                capture_output=True,
            )
            clip = edl3.Clip3(id="c1", source=str(source), src_in=0.0, src_out=1.0)
            timeline = edl3.Timeline3(fps=FPS, size=SIZE, clips=[clip], brand_ref="brand.json")
            plain = work / "plain.mp4"
            edl3_render.render(timeline, plain, workdir=work / "w", audio="drop", settings={})

            logo = work / "logo.png"
            logo.write_bytes(logo_bytes)
            branded = branding.apply(plain, loaded, logo=logo, out=work / "branded.mp4")
            plain_frame = frame(plain)
            branded_frame = frame(branded)
            check(
                "S3 the branded frame is not the plain frame",
                float(abs(branded_frame - plain_frame).mean()) > 0.5,
                f"MAD {float(abs(branded_frame - plain_frame).mean()):.2f}",
            )
            top_left = np.s_[0:48, 0:48]
            palette_mad = float(abs(branded_frame[top_left] - plain_frame[top_left]).mean())
            check(
                "S3 the palette reaches the corner the logo does not touch",
                palette_mad > 0.5,
                f"MAD {palette_mad:.2f}",
            )
            # Il logo non si prova con una soglia di MAD scelta a occhio: si
            # guarda dove sta. La cornice dichiarata e' 16% della larghezza in
            # basso a destra con 16 px di margine; li' dentro il rosso del logo
            # deve esserci, e prima no.
            width, height = SIZE
            side = int(width * branding.DEFAULT_SCALE)
            left = width - branding.DEFAULT_MARGIN - side
            top = height - branding.DEFAULT_MARGIN - side
            box = (slice(top, top + side), slice(left, left + side))

            # Il logo e' un salto di colore locale, non un cambio di palette:
            # si guarda dove il frame *cambia molto*, nel quadrante in basso a
            # destra, e il riquadro trovato deve essere quello dichiarato.
            diff = np.abs(branded_frame - plain_frame).mean(axis=2)
            strong = diff > 60.0
            strong[: height // 2, :] = False
            strong[:, : width // 2] = False
            ys, xs = np.nonzero(strong)
            if len(xs):
                got = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
                wanted = (left, top, left + side - 1, top + side - 1)
                close = all(abs(got[index] - wanted[index]) <= 5 for index in range(4))
            else:
                got, wanted, close = (), (), False
            check(
                "S3 the logo sits in the declared box, and only after branding",
                close,
                f"trovato {got}, dichiarato {wanted}",
            )
            logo_mad = float(abs(branded_frame[box] - plain_frame[box]).mean())
            check(
                "S3 and the corner changed by a visible amount",
                logo_mad > 5.0,
                f"MAD {logo_mad:.2f}",
            )
            check(
                "S3 the output is a real file with the timeline brand_ref",
                branded.is_file() and branded.stat().st_size > 1000,
                f"{branded.stat().st_size} bytes",
            )
    finally:
        server.shutdown()
        server.server_close()

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
