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

import math
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
    psnr_min: float = 0.0,
) -> Tuple[bool, str]:
    """``(ok, dettaglio)`` fra due immagini. Dimensioni diverse sono un no secco.

    Due criteri, e vince il migliore: la tolleranza stretta (delta e quota
    pixel) oppure, se ``psnr_min`` > 0, un PSNR almeno pari a quella soglia.
    Il secondo serve per i rasterizzatori che cambiano **build**: lo stesso
    disegno esce con un altro bilanciamento di colore (delta ~20/255 diffuso),
    che non e' un difetto del codice ma non passa la tolleranza stretta. Il
    dettaglio dice sempre quale criterio ha deciso, cosi' non si finge.
    """
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
    if worst <= int(max_delta) and share <= float(max_share):
        return True, detail + " [stretta]"

    if psnr_min > 0:
        # PSNR sui tre canali: MSE dai conteggi dell'istogramma in scala di
        # grigio sarebbe una approssimazione; si usa il canale peggiore.
        worst_psnr = 99.0
        for channel in range(3):
            channel_hist = diff.getchannel(channel).histogram()
            squared = sum(value * value * count for value, count in enumerate(channel_hist))
            mse = squared / total if total else 0.0
            if mse <= 0:
                continue
            worst_psnr = min(worst_psnr, 10.0 * math.log10((255.0 * 255.0) / mse))
        if worst_psnr >= float(psnr_min):
            return True, detail + f" [psnr {worst_psnr:.1f} dB]"
        return False, detail + f" [psnr {worst_psnr:.1f} dB < {psnr_min:g}]"
    return False, detail


__all__ = ["MAX_DELTA", "MAX_SHARE", "compare_images"]

