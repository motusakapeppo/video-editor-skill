"""vx.branding — il brand entra nel render: palette sui pixel, logo in un angolo.

Il documento ``brand.json`` da solo non cambia un fotogramma. Qui diventa due
passaggi reali su ffmpeg:

* la **palette** — il colore dominante del brand diventa uno sbilanciamento di
  ``colorbalance`` sui mezzitoni, con forza dichiarata (0,25 di default): il
  colore del brand sposta l'immagine senza tingere tutto;
* il **logo** — un PNG, scalato a una frazione dichiarata della larghezza e
  messo in un angolo con un margine in pixel.

Il test misura il MAD sul fotogramma (angolo in alto a sinistra per la palette,
angolo in basso a destra per il logo), non sul JSON. Un ``brand_ref`` che punta a
un file assente e' ``brand_ref_missing``; un documento che non valida e'
``brand_invalid`` con gli errori.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import brandjson
from . import ffmpeg as ff
from .contract import VxError

DEFAULT_STRENGTH = 0.25
DEFAULT_SCALE = 0.16
DEFAULT_MARGIN = 16
CORNER = "bottom_right"


class BrandingError(VxError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _rgb(hex_value: str) -> Tuple[float, float, float]:
    text = hex_value.lstrip("#")
    return tuple(int(text[index : index + 2], 16) / 255.0 for index in (0, 2, 4))  # type: ignore[return-value]


def palette_filters(document: Mapping[str, Any], *, strength: float = DEFAULT_STRENGTH) -> List[str]:
    """The brand's dominant colour as a midtone balance, at the declared strength."""
    colors = document.get("colors") or []
    if not colors:
        return []
    weight = max(0.0, min(1.0, float(strength)))
    red, green, blue = _rgb(str(colors[0].get("hex") or "#808080"))
    luma = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    channels = [
        max(-0.5, min(0.5, (value - luma) * 2.0 * weight))
        for value in (red, green, blue)
    ]
    if all(abs(value) < 1e-4 for value in channels):
        return []
    return [
        "colorbalance=rm={:.4f}:gm={:.4f}:bm={:.4f}".format(*channels)
    ]


def _escape(path: Path) -> str:
    text = str(Path(path).resolve()).replace("\\", "/")
    return text.replace(":", "\\:")


def apply(
    video: Path,
    document: Mapping[str, Any],
    *,
    logo: Path,
    out: Path,
    strength: float = DEFAULT_STRENGTH,
    scale: float = DEFAULT_SCALE,
    margin: int = DEFAULT_MARGIN,
) -> Path:
    """Render the brand onto an existing video: palette first, logo on top."""
    errors = brandjson.validate(document)
    if errors:
        raise BrandingError("brand_invalid", "; ".join(errors[:4]))
    logo = Path(logo)
    if not logo.is_file():
        raise BrandingError("brand_logo_missing", f"logo non trovato: {logo}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    filters = palette_filters(document, strength=strength)
    base = ",".join(filters) if filters else "null"
    # ``iw`` dentro ``scale`` e' la larghezza del logo, non del video: il logo
    # va dimensionato sulla larghezza misurata del video, arrotondata a un
    # numero pari, altrimenti il 16% di 96 px diventa 14 px.
    overlay_scale = max(0.02, min(0.6, float(scale)))
    video_width = _width(video)
    logo_width = max(2, int(round(video_width * overlay_scale / 2.0)) * 2)
    filter_complex = (
        f"[0:v]{base}[bg];"
        f"[1:v]scale={logo_width}:-1[logo];"
        f"[bg][logo]overlay=W-w-{int(margin)}:H-h-{int(margin)}"
    )
    audio = ["-c:a", "copy"] if _has_audio(video) else ["-an"]
    proc = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video), "-i", str(logo),
            "-filter_complex", filter_complex,
            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            *audio,
            str(out),
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not out.is_file():
        raise BrandingError(
            "brand_render",
            proc.stderr.decode("utf-8", "replace")[-300:],
        )
    return out


def _width(video: Path) -> int:
    proc = subprocess.run(
        [
            ff.ffprobe(), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width", "-of", "csv=p=0", str(video),
        ],
        capture_output=True,
    )
    try:
        return max(1, int(proc.stdout.decode().strip().splitlines()[0]))
    except (IndexError, ValueError) as exc:
        raise BrandingError("brand_render", f"larghezza non leggibile per {video}") from exc


def _has_audio(video: Path) -> bool:
    proc = subprocess.run(
        [
            ff.ffprobe(), "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=index", "-of", "csv=p=0", str(video),
        ],
        capture_output=True,
    )
    return bool(proc.stdout.strip())


# --------------------------------------------------------------------------- #
# brand_ref: caricare il documento che l'EDL cita
# --------------------------------------------------------------------------- #
def resolve(reference: Optional[str], *, base: Path) -> Path:
    if not reference:
        raise BrandingError(
            "brand_ref_missing", "la timeline non dichiara brand_ref"
        )
    candidate = Path(reference)
    if not candidate.is_absolute():
        candidate = Path(base) / candidate
    if not candidate.is_file():
        raise BrandingError("brand_ref_missing", f"brand_ref non trovato: {candidate}")
    return candidate


def load(reference: Optional[str], *, base: Path) -> Dict[str, Any]:
    path = resolve(reference, base=base)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BrandingError("brand_invalid", f"{path}: {exc}") from exc
    errors = brandjson.validate(document)
    if errors:
        raise BrandingError("brand_invalid", "; ".join(errors[:4]))
    return dict(document, _path=str(path))


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


__all__ = [
    "BrandingError",
    "CORNER",
    "DEFAULT_MARGIN",
    "DEFAULT_SCALE",
    "DEFAULT_STRENGTH",
    "apply",
    "digest",
    "load",
    "palette_filters",
    "resolve",
]
