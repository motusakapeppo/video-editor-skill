"""vx.creative — i parametri visivi si derivano dal materiale, non da un default.

Il problema che risolve: con un solo stile in codice, ogni progetto esce uguale. Qui
il testo (font, corpo, colore, animazione) e il grade nascono da misure sul materiale:

  luma p01/p50/p99 ....... decide se alzare le ombre, proteggere le alte luci, o niente
  saturazione media ..... decide se il colore va ravvivato o lasciato stare
  tinta dominante ........ da' l'accento (complementare), invece di un bianco fisso
  energia di movimento ... decide font (condensed vs grotesque), corpo e velocita' animazione
  densita' di onset ...... decide fade e pop, cioe' il ritmo del testo
  varianza in basso ...... decide se il testo sta in basso o sale

Nessuna di queste regole e' un gusto: ognuna ha una soglia e un perche' scritto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = ["Material", "Style", "analyse", "brief", "font_pool"]


def _font_dirs() -> List[Path]:
    """Cartelle font del sistema in cui si trova la macchina, piu' quelle del progetto.

    Niente percorsi scritti a mano: la skill deve funzionare su un'altra macchina
    senza modifiche. Ordine: progetto, utente, sistema.
    """
    import os  # noqa: PLC0415

    dirs: List[Path] = [Path(__file__).resolve().parents[2] / "assets" / "fonts"]
    home = Path.home()
    dirs += [home / ".fonts", home / ".local" / "share" / "fonts"]
    if os.name == "nt":
        windir = os.environ.get("WINDIR")
        if windir:
            dirs.append(Path(windir) / "Fonts")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    elif os.uname().sysname == "Darwin":
        dirs += [Path("/System/Library/Fonts"), Path("/Library/Fonts")]
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]
    return [d for d in dirs if d.is_dir()]


FONT_DIRS = _font_dirs()

# Nessun registro di quattro voci: si scandisce il sistema e si misura. Il font
# viene scelto per PROPRIETA' misurate (larghezza d'avanzamento e spessore del
# fusto), non per appartenenza a una lista. Con 379 font installati lo spazio e'
# gia' ampio; se ne fornisci altri, entrano allo stesso modo.
_FONT_CACHE: Optional[List[Dict[str, Any]]] = None


def _measure_font(path: Path) -> Optional[Dict[str, Any]]:
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    try:
        f = ImageFont.truetype(str(path), 100)
    except Exception:  # noqa: BLE001
        return None
    try:
        family = f.getname()[0]
    except Exception:  # noqa: BLE001
        family = path.stem
    # Font variabili e file corrotti esistono davvero nel sistema: si scartano,
    # non si lasciano esplodere la scansione.
    try:
        advance = f.getlength("abcdefghij") / 10.0 / 100.0
        img = Image.new("L", (200, 200), 0)
        ImageDraw.Draw(img).text((20, 20), "I", font=f, fill=255)
        box = img.getbbox()
    except Exception:  # noqa: BLE001
        return None
    if not advance or advance <= 0:
        return None
    stem = ((box[2] - box[0]) if box else 10) / 100.0
    return {"name": family, "path": str(path), "advance": advance, "stem": stem}


def font_pool(extra: Sequence[str | Path] = ()) -> List[Dict[str, Any]]:
    """Tutti i font misurabili: di sistema piu' quelli forniti."""
    global _FONT_CACHE
    if _FONT_CACHE is None:
        pool = []
        for d in FONT_DIRS:
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if f.suffix.lower() in (".ttf", ".otf"):
                    m = _measure_font(f)
                    if m:
                        pool.append(m)
        _FONT_CACHE = pool
    out = list(_FONT_CACHE)
    for extra_path in extra:
        m = _measure_font(Path(extra_path))
        if m and not any(f["path"] == m["path"] for f in out):
            out.append(m)
    return out


@dataclass
class Material:
    width: int
    height: int
    duration: float
    fps: float
    luma_p01: float = 0.0
    luma_p50: float = 0.0
    luma_p99: float = 0.0
    saturation: float = 0.0
    dominant_hue: float = 0.0
    motion: float = 0.0
    bottom_variance: float = 0.0
    onsets_per_second: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            k: round(v, 4) if isinstance(v, float) else v
            for k, v in self.__dict__.items()
        }


@dataclass
class Style:
    font_name: str
    font_path: str
    font_px: int
    ink: Tuple[int, int, int]
    accent: Tuple[int, int, int]
    fade_ms: int
    pop_ms: int
    bottom_limit: int
    grade: Dict[str, Any]
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "font": self.font_name,
            "font_px": self.font_px,
            "ink": "#%02x%02x%02x" % self.ink,
            "accent": "#%02x%02x%02x" % self.accent,
            "fade_ms": self.fade_ms,
            "pop_ms": self.pop_ms,
            "bottom_limit": self.bottom_limit,
            "grade": self.grade,
            "reasons": self.reasons,
        }


def analyse(video: str | Path, *, samples: int = 12) -> Material:
    """Misure sul materiale. Solo cio' che serve a decidere, niente di piu'."""
    import cv2  # noqa: PLC0415

    from . import grammar, motion  # noqa: PLC0415

    frames, dur, fps = motion.expected_frames(video)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return Material(width=0, height=0, duration=dur, fps=fps)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    lumas, sats, hues, diffs, bottom = [], [], [], [], []
    prev = None
    n = 0
    total = max(1, frames)
    step = max(1, total // samples)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if n % step == 0:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            y = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
            lumas.append(np.percentile(y, [1, 50, 99]))
            hi, lo = rgb.max(axis=2), rgb.min(axis=2)
            sats.append(
                float(np.mean(np.where(hi > 1e-6, (hi - lo) / np.maximum(hi, 1e-6), 0)))
            )
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0], None, [36], [0, 180]).ravel()
            hues.append(float(np.argmax(hist)) * 5.0)
            band = y[int(h * 0.72) : int(h * 0.92), :]
            bottom.append(float(band.std()))
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
            if prev is not None:
                diffs.append(float(np.abs(gray - prev).mean()))
            prev = gray
        n += 1
    cap.release()

    m = Material(width=w, height=h, duration=dur, fps=fps)
    if lumas:
        arr = np.array(lumas)
        m.luma_p01, m.luma_p50, m.luma_p99 = (
            float(arr[:, 0].mean()),
            float(arr[:, 1].mean()),
            float(arr[:, 2].mean()),
        )
        m.saturation = float(np.mean(sats))
        m.dominant_hue = float(np.mean(hues))
        m.bottom_variance = float(np.mean(bottom))
    m.motion = float(np.mean(diffs)) if diffs else 0.0
    try:
        grid = grammar.beat_grid(str(video), max_seconds=min(60.0, max(dur, 1.0)))
        onsets = list(getattr(grid, "onsets", []) or [])
        m.onsets_per_second = len(onsets) / dur if dur > 0 else 0.0
    except Exception:  # noqa: BLE001
        pass
    return m


def _hex(rgb: Sequence[int]) -> Tuple[int, int, int]:
    return (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def _accent_from_hue(hue_open_cv: float, sat: float) -> Tuple[int, int, int]:
    """Accento = complementare della tinta dominante, saturo ma non fluorescente."""
    comp = (hue_open_cv + 90.0) % 180.0
    hsv = np.uint8([[[comp / 2.0, int(190 + 40 * min(1.0, sat)), 235]]])
    import cv2  # noqa: PLC0415

    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)[0][0]
    return _hex(bgr)


def brief(m: Material, *, request: str = "", words: int = 0) -> Style:
    """Deriva lo stile dalle misure. Ogni scelta porta la sua ragione."""
    reasons: List[str] = []

    # 1. energia -> voce tipografica e corpo
    # Mappatura satura, non lineare a tratti: motion/(motion+k) resta continua su
    # qualunque valore e non schiaccia tutto a 1.0 (era il difetto: 0.20 e 0.30 di
    # moto finivano entrambi a energia 1.0, quindi lo stesso font per ogni video).
    motion_term = m.motion / (m.motion + 0.15) if m.motion else 0.0
    onset_term = (
        m.onsets_per_second / (m.onsets_per_second + 2.0)
        if m.onsets_per_second
        else 0.0
    )
    energy = 0.6 * motion_term + 0.4 * onset_term
    pool = font_pool()
    if not pool:
        raise RuntimeError("nessun font misurabile: impossibile derivare lo stile")
    # Rango percentile, non "piu' vicino": la distribuzione reale dei font va da
    # larghi e sottili a stretti e pesanti, e l'energia decide DOVE cadere. Il
    # nearest-point collassava sullo stesso font per materiali molto diversi,
    # perche' il centro della distribuzione e' affollato.
    ranked = sorted(pool, key=lambda f: f["advance"] + 0.6 * f["stem"])
    pos = int(round((1.0 - energy) * (len(ranked) - 1)))
    pos = max(0, min(len(ranked) - 1, pos))
    font = ranked[pos]
    reasons.append(
        f"energia {energy:.3f} -> percentile {1.0 - energy:.2f} della distribuzione "
        f"({len(ranked)} font) -> {font['name']} "
        f"(avanz. {font['advance']:.2f}, fusto {font['stem']:.3f})"
    )

    # Il corpo non sta in una banda: si deriva dallo spazio. Si misura la riga piu'
    # larga col font scelto e la si porta a occupare la frazione di larghezza che
    # l'energia chiede. Il limite superiore e' solo geometrico (il canvas).
    target_frac = 0.68 + 0.22 * energy
    try:
        from PIL import ImageFont  # noqa: PLC0415

        probe = ImageFont.truetype(font["path"], 100)
        widest = max(
            probe.getlength(s)
            for s in (
                "La città non dorme mai.",
                "Ascolta quello che dice.",
                "FUORI SCENA",
                "Dal 12 novembre.",
            )
        )
        font_px = int(round((m.width * target_frac) / (widest / 100.0)))
        font_px = max(12, min(font_px, int(m.height * 0.14)))
        reasons.append(
            f"corpo {font_px}px: riga piu' larga a {target_frac:.2f} della "
            f"larghezza con {font['name']} (misurata, non stimata)"
        )
    except Exception:  # noqa: BLE001
        font_px = int(round(0.06 * m.height))
        reasons.append(f"corpo {font_px}px (fallback geometrico)")

    # 2. colore: inchiostro in base alla luma della fascia bassa, accento dalla tinta
    bottom_luma = m.luma_p50
    ink = (250, 250, 248) if bottom_luma < 0.55 else (18, 18, 20)
    reasons.append(
        f"luma mediana {bottom_luma:.2f} -> inchiostro "
        f"{'chiaro' if bottom_luma < 0.55 else 'scuro'}"
    )
    accent = _accent_from_hue(m.dominant_hue, m.saturation)
    reasons.append(
        f"tinta dominante {m.dominant_hue:.0f}° -> accento "
        f"#{accent[0]:02x}{accent[1]:02x}{accent[2]:02x} (complementare)"
    )

    # 3. ritmo: tempo alto -> animazione corta
    fade = int(round(160 - 90 * energy))
    pop = int(round(280 - 130 * energy))
    reasons.append(f"fade {fade}ms / pop {pop}ms derivati dall'energia")

    # 4. posizione: se la fascia bassa e' caotica il testo sale
    bottom_limit = int(
        round(m.height * (0.86 - 0.06 * min(1.0, m.bottom_variance / 0.25)))
    )
    reasons.append(
        f"varianza in basso {m.bottom_variance:.3f} -> limite testo "
        f"{bottom_limit}px ({bottom_limit / m.height:.2f} dell'altezza)"
    )

    # 5. grade: si corregge solo cio' che il materiale chiede
    master_pts = [[0.0, 0.0]]
    if m.luma_p01 < 0.02:
        master_pts.append([0.10, 0.13])
        reasons.append("neri schiacciati (p01 < 0.02) -> alzo le ombre")
    if m.luma_p99 < 0.82:
        master_pts.append([0.90, 0.93])
        reasons.append("alte luci basse (p99 < 0.82) -> le spingo")
    if m.luma_p50 < 0.38:
        master_pts.append([0.50, 0.55])
        reasons.append("immagine scura (p50 < 0.38) -> alzo i mezzitoni")
    elif m.luma_p50 > 0.62:
        master_pts.append([0.50, 0.47])
        reasons.append("immagine chiara (p50 > 0.62) -> abbasso i mezzitoni")
    master_pts.append([1.0, 1.0])
    channels: Dict[str, List[List[float]]] = {}
    if m.saturation < 0.20:
        gain = min(1.25, 1.0 + (0.20 - m.saturation) * 1.5)
        channels = {
            "r": [[0, 0], [0.5, min(1.0, 0.5 * gain + 0.03)], [1, 1]],
            "b": [[0, 0], [0.5, max(0.0, 0.5 * gain - 0.03)], [1, 1]],
        }
        reasons.append(
            f"saturazione bassa ({m.saturation:.2f}) -> correzione di canale"
        )
    grade: Dict[str, Any] = {"type": "none"}
    if len(master_pts) > 2 or channels:
        grade = {
            "type": "curve",
            "master": master_pts,
            "channels": channels,
            "desaturate": False,
        }

    return Style(
        font_name=font["name"],
        font_path=str(font["path"]),
        font_px=font_px,
        ink=ink,
        accent=accent,
        fade_ms=fade,
        pop_ms=pop,
        bottom_limit=bottom_limit,
        grade=grade,
        reasons=reasons,
    )
