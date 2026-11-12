"""Confronto di immagini golden con tolleranza: portabile, non indulgente.

Perche' esiste: le golden nascono su **una** macchina (ffmpeg, x264, CPU,
rasterizzazione dei font). Su un runner GitHub gli stessi pixel non escono byte
per byte identici, e un test che pretende l'uguaglianza binaria diventa un test
che fallisce per l'ambiente invece che per il codice.

La tolleranza qui non e' "piu' o meno": e' la stessa regola gia' usata da
`test_motion_golden` — **delta massimo per canale** e **quota massima di pixel
diversi**. Un frame sbagliato (testo spostato, colore invertito, cornice nera)
sfora subito; un arrotondamento di compositing no. I valori di default sono
quelli dei golden esistenti (8/255, 0,02%).
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

from PIL import Image, ImageChops

#: Delta massimo per canale accettato su un singolo pixel.
MAX_DELTA = 8
#: Quota massima di pixel che possono differire (0,02%).
MAX_SHARE = 0.02


def compare_images(
    reference: str | Path,
    candidate: str | Path,
    *,
    max_delta: int = MAX_DELTA,
    max_share: float = MAX_SHARE,
) -> Tuple[bool, str]:
    """``(ok, dettaglio)`` fra due immagini. Dimensioni diverse sono un no secco."""
    ref_path, got_path = Path(reference), Path(candidate)
    if not ref_path.is_file():
        return False, f"golden mancante: {ref_path.name}"
    if not got_path.is_file():
        return False, f"immagine mancante: {got_path.name}"
    try:
        ref = Image.open(ref_path).convert("RGB")
        got = Image.open(got_path).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - un PNG illeggibile e' un no
        return False, f"{type(exc).__name__}: {exc}"
    if ref.size != got.size:
        return False, f"dimensioni {got.size} invece di {ref.size}"

    diff = ImageChops.difference(ref, got)
    histogram = diff.convert("L").histogram()
    total = ref.size[0] * ref.size[1]
    changed = sum(count for value, count in enumerate(histogram) if value > 0)
    worst = max((value for value, count in enumerate(histogram) if count), default=0)
    share = 100.0 * changed / total if total else 0.0
    detail = f"delta={worst}/255, {changed}/{total} px ({share:.5f}%)"
    ok = worst <= int(max_delta) and share <= float(max_share)
    return ok, detail


__all__ = ["MAX_DELTA", "MAX_SHARE", "compare_images"]
