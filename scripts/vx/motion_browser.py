"""vx.motion_browser — secondo motore, dietro lo STESSO payload.

Perche' esiste: il contratto `MotionPayload` non dipende da chi disegna. Questo
motore consuma lo stesso JSON del motore Pillow, quindi i Gate 3 e 4 restano
identici e i due rasterizzatori si possono confrontare sullo stesso filmato.

Il layout NON e' delegato al browser: le posizioni, i corpi e le scale arrivano da
`vx.motion`, calcolati una volta in Python. Il browser fa solo il raster: testo,
ombra gaussiana, alpha. Cosi' il confronto misura il motore di rendering e non due
implementazioni di layout diverse.

Estrazione dei pixel: screenshot PNG con `omit_background`, decodificato con
Pillow. E' la via piu' economica che non serializzi 5,9 MB per frame su CDP.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from . import ffmpeg as ff
from . import motion

__all__ = ["BrowserRenderer", "audit_font_in_page", "composite_browser"]

PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html, body {{ margin:0; padding:0; background:transparent; overflow:hidden; }}
  canvas {{ display:block; }}
  @font-face {{
    font-family: 'VxLocal';
    src: url(data:font/ttf;base64,{font_b64}) format('truetype');
    font-weight: 900;
  }}
</style></head>
<body><canvas id="c" width="{w}" height="{h}"></canvas>
<script>
const ctx = document.getElementById('c').getContext('2d');
window.__draw = (ops, blur, shadowAlpha, shadowRGB) => {{
  ctx.clearRect(0, 0, {w}, {h});
  for (const op of ops) {{
    ctx.save();
    ctx.font = op.px + "px 'VxLocal'";
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    ctx.shadowColor = 'rgba(' + shadowRGB.join(',') + ',' + shadowAlpha + ')';
    ctx.shadowBlur = blur;
    ctx.shadowOffsetY = 0;
    ctx.fillStyle = 'rgba(' + op.rgb.join(',') + ',' + op.a + ')';
    ctx.fillText(op.text, op.x, op.y);
    ctx.restore();
  }}
  return ctx.getImageData(0, 0, 1, 1).data[3];
}};
window.__fontCheck = (px) => {{
  const ok = document.fonts.check("900 " + px + "px 'VxLocal'");
  ctx.font = "900 " + px + "px 'VxLocal'";
  const m = ctx.measureText('WWWW MMMM');
  return {{ loaded: ok, width: m.width }};
}};
</script></body></html>"""


class BrowserRenderer:
    """Chromium headless, viewport esatta, sfondo trasparente, frame on demand."""

    def __init__(self, payload: motion.MotionPayload, *, headless: bool = True):
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        self.payload = payload
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=headless,
            args=[
                "--force-color-profile=srgb",
                "--disable-lcd-text",
                "--font-render-hinting=none",
                "--disable-features=PaintHolding",
            ],
        )
        self._page = self._browser.new_page(
            viewport={"width": payload.width, "height": payload.height},
            device_scale_factor=1,
        )
        font_b64 = base64.b64encode(Path(payload.font_path).read_bytes()).decode(
            "ascii"
        )
        self._page.set_content(
            PAGE.format(font_b64=font_b64, w=payload.width, h=payload.height)
        )
        self._page.evaluate(
            "async (px) => { await document.fonts.load('900 ' + px + \"px 'VxLocal'\"); "
            "await document.fonts.ready; "
            "return document.fonts.check('900 ' + px + \"px 'VxLocal'\"); }",
            payload.font_px,
        )
        self._font = ImageFont.truetype(payload.font_path, payload.font_px)
        self._ascent = self._font.getmetrics()[0]
        self._draw_js = self._page.evaluate("typeof window.__draw")

    def font_check(self) -> Dict[str, Any]:
        return self._page.evaluate("window.__fontCheck", self.payload.font_px)

    def _ops(self, t: float) -> List[Dict[str, Any]]:
        """Stesse posizioni e stesse scale del motore Pillow, convertite al canvas."""
        active = next((c for c in self.payload.cues if c.start <= t <= c.end), None)
        if active is None:
            return []
        rows = motion._layout(self.payload, active, self._font)
        base = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        del base
        light_bg = False
        ink = (255, 255, 255) if not light_bg else (16, 16, 16)
        ops: List[Dict[str, Any]] = []
        for word, cx, y, wd in rows:
            if t < word.t_start:
                a, colour = 110 / 255.0, ink
            elif t <= word.t_end:
                a, colour = 1.0, ink
            else:
                a, colour = 232 / 255.0, ink
            scale = motion._word_scale(word, t)
            px = max(8, int(round(self.payload.font_px * scale)))
            if word.beat and 0.0 <= (t - word.t_start) < motion.BEAT_FLASH_MS / 1000.0:
                colour = (255, 60, 60)
            ops.append(
                {
                    "text": word.text,
                    "x": round(cx, 2),
                    "y": round(y + self._ascent, 2),
                    "px": px,
                    "rgb": list(colour),
                    "a": round(a, 3),
                }
            )
        return ops

    def frame_png(self, t: float) -> bytes:
        ops = self._ops(t)
        self._page.evaluate(
            "([ops, blur, sa, srgb]) => window.__draw(ops, blur, sa, srgb)",
            [
                ops,
                motion.SHADOW_BLUR,
                motion.SHADOW_ALPHA,
                [255, 255, 255] if False else [0, 0, 0],
            ],
        )
        return self._page.screenshot(omit_background=True, type="png")

    def frame_rgba(self, t: float) -> Image.Image:
        return Image.open(_BytesIO(self.frame_png(t))).convert("RGBA")

    def close(self) -> None:
        try:
            self._page.close()
            self._browser.close()
        finally:
            self._pw.stop()


class _BytesIO:
    """Sottile involucro per non importare io a livello di modulo."""

    def __init__(self, data: bytes):
        import io  # noqa: PLC0415

        self._buf = io.BytesIO(data)

    def __getattr__(self, item):
        return getattr(self._buf, item)


def audit_font_in_page(renderer: BrowserRenderer) -> Dict[str, Any]:
    """Il font e' davvero quello, dentro il browser? Nessun fallback muto."""
    info = renderer.font_check()
    info["ok"] = bool(info.get("loaded")) and float(info.get("width") or 0) > 0
    return info


def composite_browser(
    master: str | Path,
    payload: motion.MotionPayload,
    out: str | Path,
    renderer: BrowserRenderer,
    *,
    crf: int = 20,
    preset: str = "medium",
) -> Dict[str, Any]:
    """Come `motion.composite`, ma il layer arriva dal browser. Stessi invarianti."""
    src, dst = Path(master), Path(out)
    declare, dur, fps = motion.expected_frames(src)
    w, h = payload.width, payload.height
    dec = subprocess.Popen(
        [
            ff.ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-an",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{w}x{h}",
            "-r",
            str(fps),
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    enc = subprocess.Popen(
        [
            ff.ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{w}x{h}",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
            "-i",
            str(src),
            "-map",
            "0:v",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-crf",
            str(crf),
            "-preset",
            preset,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(dst),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    frame_bytes = w * h * 3
    n = 0
    t0 = time.perf_counter()
    try:
        while True:
            buf = dec.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            base = Image.frombytes("RGB", (w, h), buf)
            layer = renderer.frame_rgba(n / fps)
            merged = Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")
            enc.stdin.write(merged.tobytes())
            n += 1
    finally:
        try:
            enc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        enc.wait()
        dec.stdout.close()
        dec.wait()
    return {
        "path": str(dst),
        "frames_written": n,
        "frames_declared": declare,
        "frames_match": n == declare,
        "seconds": round(time.perf_counter() - t0, 2),
        "encoder_rc": enc.returncode,
    }
