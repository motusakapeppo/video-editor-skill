"""vx.stack — split screen e confronto: due flussi video affiancati, non un browser.

Il confine dichiarato: la **grafica** (testo impaginato, sottopancia, card) la fa
Chromium; la **composizione di flussi video** la fa ffmpeg, che è più veloce e
deterministico. Questo modulo è quella seconda metà.

    [sinistra]  |  [destra]

Ogni lato viene ritagliato al centro e scalato all'altezza d'uscita, poi i due
riquadri si affiancano con ``hstack``. La durata è la più corta delle due (o
quella dichiarata), l'audio si scarta: un confronto non ha una colonna sonora
sua, e fingere un mix sarebbe peggio che dichiararne l'assenza.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from . import ffmpeg as ff

__all__ = ["stack", "SCHEMA"]

SCHEMA = "vx.stack/1"


def _escape(path: Path) -> str:
    """Un percorso dentro un filtergraph: virgolette e backslash."""
    text = str(path).replace("\\", "/")
    return text.replace(":", "\\:").replace("'", "\\'")


def stack(
    left: Any,
    right: Any,
    out: Any,
    *,
    width: int = 1080,
    height: int = 1920,
    duration: Optional[float] = None,
) -> Dict[str, Any]:
    """Affianca due video e scrive il risultato; restituisce i fatti misurati.

    ``width`` deve essere pari: i due riquadri sono metà esatte. Se i sorgenti
    sono più corti della durata chiesta, il risultato dura quanto il più corto e
    il fatto è dichiarato (``duration_sec`` reale contro ``requested_sec``).
    """
    left_path, right_path, target = Path(left), Path(right), Path(out)
    for path in (left_path, right_path):
        if not path.is_file():
            raise ValueError(f"vx stack: sorgente inesistente: {path}")
    even_width = int(width) - (int(width) % 2)
    half = even_width // 2
    height = int(height)
    if half <= 0 or height <= 0:
        raise ValueError("vx stack: larghezza e altezza devono essere positive")
    target.parent.mkdir(parents=True, exist_ok=True)
    graph = (
        f"[0:v]scale={half}:{height}:force_original_aspect_ratio=increase,"
        f"crop={half}:{height},setsar=1[l];"
        f"[1:v]scale={half}:{height}:force_original_aspect_ratio=increase,"
        f"crop={half}:{height},setsar=1[r];"
        f"[l][r]hstack=inputs=2[v]"
    )
    args = [
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(left_path),
        "-i",
        str(right_path),
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-an",
    ]
    if duration is not None and float(duration) > 0:
        args += ["-t", f"{float(duration):.3f}"]
    args += [
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "medium",
        "-crf",
        "18",
        str(target),
    ]
    info = ff.run_ff(args)
    if not info.ok:
        raise ValueError(f"vx stack: ffmpeg ha fallito: {info.tail(6)}")
    measured = ff.run_fp(
        ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(target)]
    )
    try:
        real = float((measured.stdout or "").strip())
    except ValueError:
        real = 0.0
    return {
        "schema": SCHEMA,
        "path": str(target.resolve()),
        "width": even_width,
        "height": height,
        "duration_sec": round(real, 3),
        "requested_sec": None if duration is None else round(float(duration), 3),
        "audio": "nessuno: un confronto non ha colonna sonora sua",
        "engine": "ffmpeg",
        "filter": "scale+crop+hstack",
    }
