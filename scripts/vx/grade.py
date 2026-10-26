"""vx.grade — nodi cromatici parametrici, matematica per-pixel.

Il motore non sa cosa sia il look del committente. Riceve un nodo e lo esegue:

    {"type": "none"}
    {"type": "duotone", "shadows": "#000000", "highlights": "#ff3333"}
    {"type": "duotone", "shadows": "#2a1a10", "highlights": "#ffe8c8", "grain": 4}

La mappatura duotone e' una rampa lineare per canale governata dalla luma:

    out_c = shadow_c + (highlight_c - shadow_c) * Y

dove Y e' la luma BT.601 del pixel. Non contiene coordinate: e' la stessa funzione
su 1080x1920, su 1:1 o su 4K, ed e' questo che la rende universale. La stessa
formula esiste in due implementazioni — filtro FFmpeg e numpy/Pillow — e il Gate 5
verifica che le due combacino prima di lasciar partire un render.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import numpy as np
from PIL import Image

__all__ = ["GradeNode", "parse", "ffmpeg_filter", "apply_pillow", "luma_anchors"]

LUMA = (0.299, 0.587, 0.114)


def _hex(value: str) -> Tuple[int, int, int]:
    s = str(value).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"colore non valido: {value!r}")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


@dataclass
class GradeNode:
    type: str = "none"
    shadows: Tuple[int, int, int] = (0, 0, 0)
    highlights: Tuple[int, int, int] = (255, 255, 255)
    grain: int = 0
    # type="curve" riproduce la catena V2: curves=m applicata PER CANALE, poi
    # desaturazione, poi tre curve per canale. L'ordine conta, perche'
    # luma(f(R), f(G), f(B)) non e' f(luma(R, G, B)).
    master: Tuple[Tuple[float, float], ...] = ()
    channels: Dict[str, Tuple[Tuple[float, float], ...]] = field(default_factory=dict)
    desaturate: bool = False
    vignette: float = 0.0

    def is_noop(self) -> bool:
        return self.type in ("none", "", None)

    def to_dict(self) -> Dict[str, Any]:
        if self.is_noop():
            return {"type": "none"}
        if self.type == "curve":
            return {
                "type": "curve",
                "master": [list(pt) for pt in self.master],
                "channels": {
                    k: [list(pt) for pt in v] for k, v in self.channels.items()
                },
                "desaturate": self.desaturate,
                "grain": self.grain,
                "vignette": self.vignette,
            }
        return {
            "type": self.type,
            "shadows": "#%02x%02x%02x" % self.shadows,
            "highlights": "#%02x%02x%02x" % self.highlights,
            "grain": self.grain,
        }


def _points(raw) -> Tuple[Tuple[float, float], ...]:
    out = []
    for pt in raw or ():
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            out.append((float(pt[0]), float(pt[1])))
    return tuple(sorted(out))


def parse(node: Optional[Dict[str, Any]]) -> GradeNode:
    """Costruisce il nodo dal payload. Sconosciuto = errore, mai un default muto."""
    if not node:
        return GradeNode()
    kind = str(node.get("type") or "none").lower()
    if kind == "none":
        return GradeNode()
    if kind == "curve":
        chans = {k.lower(): _points(v) for k, v in (node.get("channels") or {}).items()}
        return GradeNode(
            type="curve",
            master=_points(node.get("master")),
            channels=chans,
            desaturate=bool(node.get("desaturate", True)),
            grain=int(node.get("grain") or 0),
            vignette=float(node.get("vignette") or 0.0),
        )
    if kind != "duotone":
        raise ValueError(f"nodo cromatico non supportato: {kind!r}")
    return GradeNode(
        type="duotone",
        shadows=_hex(node.get("shadows") or "#000000"),
        highlights=_hex(node.get("highlights") or "#ffffff"),
        grain=int(node.get("grain") or 0),
    )


def _curve_pts(points) -> str:
    return " ".join(f"{x:.4f}/{y:.4f}" for x, y in points)


def ffmpeg_filter(node: GradeNode) -> str:
    """La stessa catena che FFmpeg esegue, nello stesso ordine."""
    if node.is_noop():
        return "null"
    if node.type == "curve":
        parts = []
        if node.master:
            parts.append(f"curves=m='{_curve_pts(node.master)}'")
        if node.desaturate:
            parts.append("hue=s=0")
        if node.channels:
            sel = [f"{k}='{_curve_pts(v)}'" for k, v in sorted(node.channels.items())]
            parts.append("curves=" + ":".join(sel))
        if node.grain:
            parts.append(f"noise=alls={int(node.grain)}:allf=t")
        if node.vignette:
            parts.append(f"vignette={node.vignette}")
        return ",".join(parts) or "null"

    sh, hi = node.shadows, node.highlights
    r = f"{sh[0]}+({hi[0]}-{sh[0]})*val/255"
    g = f"{sh[1]}+({hi[1]}-{sh[1]})*val/255"
    b = f"{sh[2]}+({hi[2]}-{sh[2]})*val/255"
    parts = [f"format=gray,lutrgb=r='{r}':g='{g}':b='{b}'"]
    if node.grain:
        parts.append(f"noise=alls={int(node.grain)}:allf=t")
    return ",".join(parts)


def _clip8(v: float) -> int:
    """av_clip_uint8: cast troncante verso zero, poi clamp. Non e' un arrotondamento."""
    i = int(v)
    return 0 if i < 0 else (255 if i > 255 else i)


def _ffmpeg_lut(points) -> "np.ndarray":
    """Port 1:1 di `interpolate()` in libavfilter/vf_curves.c.

    Tre dettagli decidono la fedelta', e nessuno dei tre e' la matematica della
    spline in se':
      * la variabile locale e' xx = (x - x_start) / scale, con scale = 255, NON
        (x - x_start) / h: l'inizio dell'intervallo e' gia' quantizzato a intero;
      * i valori sono troncati (av_clip_uint8 fa cast a int), non arrotondati;
      * fuori dal primo e dall'ultimo punto la LUT e' piatta sul valore del punto.
    """
    n = len(points)
    lut = np.zeros(256, dtype=np.uint8)
    scale = 255
    if n == 0:
        return np.arange(256, dtype=np.uint8)
    if n == 1:
        lut[:] = _clip8(points[0][1] * scale)
        return lut

    xs = [float(pt[0]) for pt in points]
    ys = [float(pt[1]) for pt in points]
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]

    r = [0.0] * n
    for i in range(1, n - 1):
        r[i] = 6.0 * ((ys[i + 1] - ys[i]) / h[i] - (ys[i] - ys[i - 1]) / h[i - 1])

    bd = [0.0] * n
    md = [0.0] * n
    ad = [0.0] * n
    md[0] = md[n - 1] = 1.0
    for i in range(1, n - 1):
        bd[i] = h[i - 1]
        md[i] = 2.0 * (h[i - 1] + h[i])
        ad[i] = h[i]

    for i in range(1, n):
        den = md[i] - bd[i] * ad[i - 1]
        k = 1.0 / den if den else 1.0
        ad[i] *= k
        r[i] = (r[i] - bd[i] * r[i - 1]) * k
    for i in range(n - 2, -1, -1):
        r[i] = r[i] - ad[i] * r[i + 1]

    first = int(xs[0] * scale)
    if first > 0:
        lut[:first] = _clip8(ys[0] * scale)

    for i in range(n - 1):
        yc, yn = ys[i], ys[i + 1]
        a = yc
        b = (yn - yc) / h[i] - h[i] * r[i] / 2.0 - h[i] * (r[i + 1] - r[i]) / 6.0
        c = r[i] / 2.0
        d = (r[i + 1] - r[i]) / (6.0 * h[i])
        x0 = int(xs[i] * scale)
        x1 = int(xs[i + 1] * scale)
        for x in range(x0, x1 + 1):
            xx = (x - x0) / scale
            yy = a + b * xx + c * xx * xx + d * xx * xx * xx
            lut[x] = _clip8(yy * scale)

    last = int(xs[-1] * scale)
    if last < scale:
        lut[last:] = _clip8(ys[-1] * scale)
    return lut


def _curve_fn(points):
    """Applica la LUT sull'indice a 8 bit, come fa il filtro frame per frame."""
    lut = _ffmpeg_lut(points).astype(np.float32) / 255.0

    def apply(v):
        idx = np.clip(np.rint(v * 255.0), 0, 255).astype(np.intp)
        return lut[idx]

    return apply


def apply_pillow(img: Image.Image, node: GradeNode) -> Image.Image:
    """La stessa catena, in numpy. Nessuna coordinata: solo il valore del pixel."""
    if node.is_noop():
        return img
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0

    if node.type == "curve":
        if node.master:
            f = _curve_fn(node.master)
            # curves=m agisce su OGNI canale, non sulla luma: f(R), f(G), f(B).
            rgb = np.stack([f(rgb[:, :, i]) for i in range(3)], axis=2)
        if node.desaturate:
            # Misurato: il round-trip YUV intero peggiora p01 (0.0176 contro 0.0137)
            # perche' quantizza Y a 8 bit. La replicazione in float e' piu' fedele.
            y = LUMA[0] * rgb[:, :, 0] + LUMA[1] * rgb[:, :, 1] + LUMA[2] * rgb[:, :, 2]
            rgb = np.stack([y, y, y], axis=2)
        for i, key in enumerate("rgb"):
            pts = node.channels.get(key)
            if pts:
                rgb[:, :, i] = _curve_fn(pts)(rgb[:, :, i])
        if node.vignette:
            h, w = rgb.shape[:2]
            yy, xx = np.mgrid[0:h, 0:w]
            cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
            r = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2) / np.sqrt(2)
            rgb = rgb * np.clip(1.0 - node.vignette * r**2, 0.0, 1.0)[:, :, None]
        return Image.fromarray(np.clip(rgb * 255.0, 0, 255).astype(np.uint8), "RGB")

    y = LUMA[0] * rgb[:, :, 0] + LUMA[1] * rgb[:, :, 1] + LUMA[2] * rgb[:, :, 2]
    sh = np.array(node.shadows, dtype=np.float32) / 255.0
    hi = np.array(node.highlights, dtype=np.float32) / 255.0
    out = sh[None, None, :] + (hi - sh)[None, None, :] * y[:, :, None]
    return Image.fromarray(np.clip(out * 255.0, 0, 255).astype(np.uint8), "RGB")


def luma_anchors(
    image: Image.Image, levels=(0.0, 0.25, 0.5, 0.75, 1.0)
) -> Dict[float, float]:
    """Luma misurata per livello di ingresso: serve al diff fra i due motori."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    y = LUMA[0] * rgb[:, :, 0] + LUMA[1] * rgb[:, :, 1] + LUMA[2] * rgb[:, :, 2]
    out: Dict[float, float] = {}
    for lv in levels:
        lo, hi = max(0.0, lv - 0.02), min(1.0, lv + 0.02)
        band = (y >= lo) & (y <= hi)
        if not np.any(band):
            continue
        out[lv] = float(np.mean(y[band]))
    return out
