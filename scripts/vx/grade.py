"""vx.grade — nodi cromatici parametrici, matematica per-pixel.

Il motore non sa cosa sia il look di Motus. Riceve un nodo e lo esegue:

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
                "channels": {k: [list(pt) for pt in v] for k, v in self.channels.items()},
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


def _spline(points):
    """Spline cubica naturale sugli anchor, come il filtro curves di FFmpeg."""
    from scipy.interpolate import CubicSpline  # noqa: PLC0415

    xs = np.array([p[0] for p in points], dtype=np.float64)
    ys = np.array([p[1] for p in points], dtype=np.float64)
    if len(xs) == 2:
        return lambda v: np.interp(v, xs, ys)
    cs = CubicSpline(xs, ys, bc_type="natural")
    return lambda v: np.clip(cs(np.clip(v, xs[0], xs[-1])), 0.0, 1.0)


def apply_pillow(img: Image.Image, node: GradeNode) -> Image.Image:
    """La stessa catena, in numpy. Nessuna coordinata: solo il valore del pixel."""
    if node.is_noop():
        return img
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0

    if node.type == "curve":
        if node.master:
            f = _spline(node.master)
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
                rgb[:, :, i] = _spline(pts)(rgb[:, :, i])
        if node.vignette:
            h, w = rgb.shape[:2]
            yy, xx = np.mgrid[0:h, 0:w]
            cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
            r = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2) / np.sqrt(2)
            rgb = rgb * np.clip(1.0 - node.vignette * r ** 2, 0.0, 1.0)[:, :, None]
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
