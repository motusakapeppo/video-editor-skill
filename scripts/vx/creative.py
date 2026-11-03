"""vx.creative — lo stile si deriva dal materiale e i piani si scelgono, non si indovinano.

Due parti, con due contratti diversi:

* ``candidates`` / ``score`` / ``choose`` (punto 3 dell'assemblea): due varianti
  lecite dello stesso piano, un punteggio unico e una scelta riproducibile. Sono
  funzioni **pure**: nessun file, nessuna rete, nessun subprocess, nessun orologio.
  Stessa entrata, stessa uscita — il pareggio lo vince sempre il primo candidato.
* ``analyse`` / ``brief`` / ``font_pool`` (sotto): lo stile visivo derivato dalle
  misure. Questa parte tocca il sistema (font, fotogrammi) e resta com'era; il
  modulo pero' non fa I/O all'import: font e numpy si caricano alla prima chiamata.

Il problema che risolve la prima parte: un piano solo e' un piano non discusso.
Qui se ne costruiscono due, entrambi validi, e si sceglie con le metriche che il
giro ha gia' misurato (coerenza, prompt, costo, novita').

Il problema che risolve la seconda: con un solo stile in codice, ogni progetto esce uguale. Qui
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

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "Material",
    "Style",
    "analyse",
    "brief",
    "font_pool",
    "candidates",
    "score",
    "score_variants",
    "choose",
    "WEIGHTS",
]


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


# Le cartelle si cercano alla prima chiamata, non all'import: importare questo
# modulo (per i candidati) non deve scandire il filesystem di nessuno.
_FONT_DIRS: Optional[List[Path]] = None

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
    global _FONT_CACHE, _FONT_DIRS
    if _FONT_DIRS is None:
        _FONT_DIRS = _font_dirs()
    if _FONT_CACHE is None:
        pool = []
        for d in _FONT_DIRS:
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
    import numpy as np  # noqa: PLC0415 - import pigro: il modulo resta puro all'import
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
    import numpy as np  # noqa: PLC0415

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


# --------------------------------------------------------------------------- #
# candidati, punteggio, scelta
# --------------------------------------------------------------------------- #
# Tutto quello che segue e' puro: legge l'oggetto piano e i referti che gli
# passano, restituisce dizionari. Nessun I/O, nessun orologio, nessuna casualita'.

#: I pesi dello score, in un posto solo. Chi vuole cambiarli cambia questo
#: dizionario (o passa ``weights=`` a :func:`score`): la somma viene rinormalizzata,
#: quindi il totale resta in 0..1 anche con pesi diversi.
WEIGHTS: Dict[str, float] = {
    "coherence": 0.40,
    "prompt": 0.30,
    "cost": 0.20,
    "novelty": 0.10,
}

#: Ogni violazione di coerenza toglie questo tanto: quattro violazioni azzerano
#: la componente. Una violazione non e' una sfumatura.
VIOLATION_PENALTY = 0.25

#: Scostamento di durata (frazione della durata chiesta) che azzera l'aderenza.
PROMPT_DURATION_TOLERANCE = 0.25

#: Costo (secondi di render) a cui la componente costo vale 0.5.
COST_CEILING_SEC = 120.0

#: Limite delle varianti di durata: nessuno shot sotto questo. E' piu' severo
#: del ``coherence.MIN_SHOT_SEC`` (0.35): una variante non deve creare shot
#: brevi che poi il referto dovrebbe perdonare.
MIN_SHOT_SEC = 1.0
MAX_TAIL_DELTA_SEC = 0.5


def _get(item: Any, name: str, default: Any = None) -> Any:
    """Legge un campo da un oggetto o da un dizionario, senza indovinare."""
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value:  # NaN: non e' una misura
        return None
    return float(value)


def _clip_duration(clip: Any) -> float:
    start = _number(_get(clip, "dst_in", 0.0))
    end = _number(_get(clip, "dst_out", 0.0))
    if start is None or end is None:
        return 0.0
    return max(0.0, end - start)


def _video_tracks(plan: Any) -> Tuple[Any, ...]:
    return tuple(_get(plan, "video_tracks", ()) or ())


def _audio_tracks(plan: Any) -> Tuple[Any, ...]:
    return tuple(_get(plan, "audio_tracks", ()) or ())


def _layer0_entries(plan: Any) -> List[Tuple[int, int, Any]]:
    """(track, posizione, clip) di tutti i clip di layer 0, in ordine stabile."""
    out: List[Tuple[int, int, Any]] = []
    for track_index, track in enumerate(_video_tracks(plan)):
        for clip_index, clip in enumerate(_get(track, "clips", ()) or ()):
            if int(_number(_get(clip, "layer", 0)) or 0) == 0:
                out.append((track_index, clip_index, clip))
    return out


def _replace_clip(clip: Any, **fields: Any) -> Any:
    if isinstance(clip, Mapping):
        copy = dict(clip)
        copy.update(fields)
        return copy
    return dataclasses.replace(clip, **fields)


def _replace_track(track: Any, edits: Mapping[int, Any]) -> Any:
    clips = list(_get(track, "clips", ()) or ())
    for index, clip in edits.items():
        clips[index] = clip
    if isinstance(track, Mapping):
        copy = dict(track)
        copy["clips"] = clips
        return copy
    return dataclasses.replace(track, clips=tuple(clips))


def _rebuild(
    plan: Any,
    video_edits: Mapping[Tuple[int, int], Any],
    audio_edits: Optional[Mapping[Tuple[int, int], Any]] = None,
    duration: Optional[float] = None,
) -> Any:
    """Copia il piano cambiando solo i clip indicati (e la durata, se chiesto)."""
    if isinstance(plan, Mapping):
        raise TypeError(
            "candidates: serve un piano con video_tracks/audio_tracks (EDLPlan), "
            "non un dizionario"
        )
    video = tuple(
        _replace_track(
            track,
            {
                clip_index: clip
                for (track_index, clip_index), clip in video_edits.items()
                if track_index == index
            },
        )
        for index, track in enumerate(_video_tracks(plan))
    )
    fields: Dict[str, Any] = {"video_tracks": video}
    if audio_edits is not None:
        fields["audio_tracks"] = tuple(
            _replace_track(
                track,
                {
                    clip_index: clip
                    for (track_index, clip_index), clip in audio_edits.items()
                    if track_index == index
                },
            )
            for index, track in enumerate(_audio_tracks(plan))
        )
    if duration is not None:
        fields["duration_sec"] = float(duration)
    return dataclasses.replace(plan, **fields)


def _valid(plan: Any) -> bool:
    validate = getattr(plan, "validate", None)
    if not callable(validate):
        return True
    try:
        return not list(validate())
    except Exception:  # noqa: BLE001 - un piano che non si valida non e' lecito
        return False


def _signature(plan: Any) -> Tuple[Tuple[Any, ...], ...]:
    rows = []
    for _, _, clip in _layer0_entries(plan):
        rows.append(
            (
                round(_number(_get(clip, "dst_in", 0.0)) or 0.0, 6),
                round(_number(_get(clip, "dst_out", 0.0)) or 0.0, 6),
                str(_get(clip, "source_id", "") or ""),
                round(_number(_get(clip, "src_in", 0.0)) or 0.0, 6),
                round(_number(_get(clip, "src_out", 0.0)) or 0.0, 6),
                str(_get(clip, "role", "") or ""),
            )
        )
    return tuple(rows)


def _same_slot(left: Any, right: Any) -> bool:
    """Vero quando due clip occupano lo stesso slot con lo stesso contenuto."""
    def near(name: str) -> bool:
        return abs(
            (_number(_get(left, name, 0.0)) or 0.0)
            - (_number(_get(right, name, 0.0)) or 0.0)
        ) <= 1e-6

    return (
        near("dst_in")
        and near("dst_out")
        and near("src_in")
        and near("src_out")
        and str(_get(left, "source_id", "") or "")
        == str(_get(right, "source_id", "") or "")
    )


def _source_capacity(plan: Any, clip: Any) -> Optional[float]:
    """Quanto può ancora durare il clip nella sua sorgente, se la durata e' nota."""
    sources = _get(plan, "sources", {}) or {}
    try:
        ref = sources.get(str(_get(clip, "source_id", "") or ""))
    except AttributeError:
        return None
    total = _number(_get(ref, "duration", None))
    if total is None:
        return None
    return max(0.0, total - (_number(_get(clip, "src_in", 0.0)) or 0.0))


def _swap_variant(plan: Any) -> Optional[Dict[str, Any]]:
    """Scambia il contenuto di due clip non consecutivi di pari ruolo e durata.

    Lo slot (dst) resta fermo: cambia cosa si vede in quello slot. La durata e'
    la stessa, quindi la copertura del layer 0 non si muove e ``validate()``
    continua a passare. Se il piano ha anche l'audio dialogo, lo scambio si
    applica agli stessi slot audio: A/V restano insieme.
    """
    best: Optional[Tuple[Tuple[Any, ...], int, int, int, List[Any]]] = None
    for track_index, track in enumerate(_video_tracks(plan)):
        clips = list(_get(track, "clips", ()) or ())
        for i in range(len(clips)):
            for j in range(i + 2, len(clips)):
                left, right = clips[i], clips[j]
                if str(_get(left, "role", "")) != str(_get(right, "role", "")):
                    continue
                if int(_number(_get(left, "layer", 0)) or 0) != int(
                    _number(_get(right, "layer", 0)) or 0
                ):
                    continue
                if abs(_clip_duration(left) - _clip_duration(right)) > 1e-6:
                    continue
                if str(_get(left, "source_id", "")) == str(
                    _get(right, "source_id", "")
                ) and abs(
                    (_number(_get(left, "src_in", 0.0)) or 0.0)
                    - (_number(_get(right, "src_in", 0.0)) or 0.0)
                ) <= 1e-6:
                    continue  # scambiarli non cambierebbe niente
                # Preferisce la coppia piu' distante; a parita', l'indice piu' basso.
                key = (-(j - i), track_index, i, j)
                if best is None or key < best[0]:
                    best = (key, track_index, i, j, clips)
    if best is None:
        return None
    _, track_index, i, j, clips = best
    left, right = clips[i], clips[j]
    new_left = _replace_clip(
        left,
        source_id=_get(right, "source_id"),
        src_in=_get(right, "src_in"),
        src_out=_get(right, "src_out"),
    )
    new_right = _replace_clip(
        right,
        source_id=_get(left, "source_id"),
        src_in=_get(left, "src_in"),
        src_out=_get(left, "src_out"),
    )
    audio_edits: Dict[Tuple[int, int], Any] = {}
    for audio_index, track in enumerate(_audio_tracks(plan)):
        for clip_index, clip in enumerate(_get(track, "clips", ()) or ()):
            if _same_slot(clip, left):
                audio_edits[(audio_index, clip_index)] = _replace_clip(
                    clip,
                    source_id=_get(right, "source_id"),
                    src_in=_get(right, "src_in"),
                    src_out=_get(right, "src_out"),
                )
            elif _same_slot(clip, right):
                audio_edits[(audio_index, clip_index)] = _replace_clip(
                    clip,
                    source_id=_get(left, "source_id"),
                    src_in=_get(left, "src_in"),
                    src_out=_get(left, "src_out"),
                )
    variant = _rebuild(plan, {(track_index, i): new_left, (track_index, j): new_right}, audio_edits)
    role = str(_get(left, "role", "") or "n/d")
    duration = _clip_duration(left)
    return {
        "thesis": f"scambia due blocchi di pari ruolo ({role}) senza toccare la griglia",
        "changes": [
            f"ordine: slot {i} <-> slot {j} (ruolo {role}, {duration:.3f}s)"
        ],
        "plan": variant,
    }


def _tail_variant(plan: Any) -> Optional[Dict[str, Any]]:
    """Porta la coda verso la durata mediana degli shot, entro i limiti.

    Cambia solo l'ultimo blocco di layer 0 (src e dst insieme, cosi' la durata
    della sorgente e quella in timeline restano uguali) e la ``duration_sec``.
    Lo accorcia o lo allunga al massimo di ``MAX_TAIL_DELTA_SEC``, mai sotto
    ``MIN_SHOT_SEC``, mai oltre la sorgente, mai sopra l'ultimo overlay.
    """
    entries = _layer0_entries(plan)
    if len(entries) < 2:
        return None
    total = _number(_get(plan, "duration_sec", 0.0)) or 0.0
    track_index, clip_index, last = max(
        entries,
        key=lambda row: (
            _number(_get(row[2], "dst_out", 0.0)) or 0.0,
            -row[0],
            -row[1],
        ),
    )
    if abs((_number(_get(last, "dst_out", 0.0)) or 0.0) - total) > 1e-6:
        return None  # qualcosa finisce dopo: la coda non e' la coda
    durations = sorted(_clip_duration(entry[2]) for entry in entries)
    middle = len(durations) // 2
    median = (
        durations[middle]
        if len(durations) % 2
        else (durations[middle - 1] + durations[middle]) / 2.0
    )
    current = _clip_duration(last)
    if abs(median - current) < 0.05:
        return None
    delta = median - current
    delta = max(-MAX_TAIL_DELTA_SEC, min(MAX_TAIL_DELTA_SEC, delta))
    new_duration = current + delta
    new_duration = max(new_duration, MIN_SHOT_SEC)
    capacity = _source_capacity(plan, last)
    if capacity is not None:
        new_duration = min(new_duration, capacity)
    if new_duration <= 0:
        return None
    overlay_end = 0.0
    for track in _video_tracks(plan):
        for clip in _get(track, "clips", ()) or ():
            if int(_number(_get(clip, "layer", 0)) or 0) >= 1:
                overlay_end = max(overlay_end, _number(_get(clip, "dst_out", 0.0)) or 0.0)
    dst_in = _number(_get(last, "dst_in", 0.0)) or 0.0
    if overlay_end > 0:
        new_duration = max(new_duration, overlay_end - dst_in)
    for transition_name in ("transition_in", "transition_out"):
        transition = _get(last, transition_name, None)
        fade = _number(_get(transition, "duration_sec", None))
        if fade is not None:
            new_duration = max(new_duration, fade)
    if abs(new_duration - current) < 0.05:
        return None
    new_total = dst_in + new_duration
    new_last = _replace_clip(
        last,
        src_out=(_number(_get(last, "src_in", 0.0)) or 0.0) + new_duration,
        dst_out=new_total,
    )
    audio_edits: Dict[Tuple[int, int], Any] = {}
    for audio_index, track in enumerate(_audio_tracks(plan)):
        for audio_clip_index, clip in enumerate(_get(track, "clips", ()) or ()):
            if (
                abs((_number(_get(clip, "dst_out", 0.0)) or 0.0) - total) <= 1e-6
                and abs((_number(_get(clip, "dst_in", 0.0)) or 0.0) - dst_in) <= 1e-6
            ):
                capacity = _source_capacity(plan, clip)
                length = new_duration
                if capacity is not None:
                    length = min(length, capacity)
                audio_edits[(audio_index, audio_clip_index)] = _replace_clip(
                    clip,
                    src_out=(_number(_get(clip, "src_in", 0.0)) or 0.0) + length,
                    dst_out=dst_in + length,
                )
    variant = _rebuild(plan, {(track_index, clip_index): new_last}, audio_edits, duration=new_total)
    verb = "accorcia" if new_duration < current else "allunga"
    return {
        "thesis": f"{verb} la coda verso la durata mediana degli shot",
        "changes": [
            f"durata: coda {current:.3f}s -> {new_duration:.3f}s "
            f"(mediana {median:.3f}s, timeline {total:.3f}s -> {new_total:.3f}s)"
        ],
        "plan": variant,
    }


def candidates(plan: Any, *, n: int = 2) -> List[Dict[str, Any]]:
    """Fino a ``n`` varianti lecite dello stesso piano.

    La prima e' sempre il piano com'e' (``id: base``); le altre sono costruite
    con trasformazioni che ``EDLPlan.validate()`` accetta:

    * ``swap`` — scambia il contenuto di due clip non consecutivi di pari ruolo
      e pari durata (lo slot resta, cambia cosa ci sta dentro);
    * ``tail`` — accorcia/allunga l'ultimo blocco di layer 0 verso la durata
      mediana, entro i limiti dichiarati.

    Ogni variante porta ``thesis`` (una riga) e ``changes`` (cosa differisce).
    Se una trasformazione non e' lecita su questo piano, si salta: ``n=1``
    restituisce solo la base, senza errori.
    """
    count = int(n)
    if count < 1:
        count = 1
    base = {
        "id": "base",
        "thesis": "tiene ordine e durata del piano com'e'",
        "changes": [],
        "plan": plan,
    }
    out: List[Dict[str, Any]] = [base]
    for name, builder in (("swap", _swap_variant), ("tail", _tail_variant)):
        if len(out) >= count:
            break
        changed = builder(plan)
        if changed is None:
            continue
        if not _valid(changed["plan"]):
            continue
        out.append({"id": name, "plan": changed["plan"], "thesis": changed["thesis"], "changes": changed["changes"]})
    return out


def _coherence_part(report: Any) -> float:
    if not isinstance(report, Mapping):
        return 0.5  # referto assente: non e' un via libera
    if report.get("disabled"):
        return 0.5
    if report.get("error"):
        return 0.25
    violations = [
        item for item in report.get("violations") or () if isinstance(item, Mapping)
    ]
    if not violations:
        return 1.0
    return max(0.0, 1.0 - VIOLATION_PENALTY * len(violations))


def _prompt_part(plan: Any, requirements: Any) -> float:
    """Aderenza al prompt: durata chiesta, coro e titolo, se misurati.

    Un fatto non misurabile (``chorus_present``/``titles_present`` a ``None``)
    vale 0.5: ne' premio ne' castigo. Non si inventa una presenza.
    """
    req = requirements if isinstance(requirements, Mapping) else {}
    pieces: List[float] = []
    requested = _number(req.get("duration_sec"))
    if requested is not None and requested > 0:
        delivered = _number(_get(plan, "duration_sec", 0.0)) or 0.0
        miss = abs(delivered - requested) / requested
        pieces.append(max(0.0, min(1.0, 1.0 - miss / PROMPT_DURATION_TOLERANCE)))
    if bool(req.get("chorus_required")):
        present = req.get("chorus_present")
        pieces.append(1.0 if present is True else 0.0 if present is False else 0.5)
    required_titles = int(req.get("titles_required") or 0)
    if required_titles > 0:
        present = req.get("titles_present")
        if isinstance(present, int) and not isinstance(present, bool):
            pieces.append(max(0.0, min(1.0, present / required_titles)))
        else:
            pieces.append(0.5)
    if not pieces:
        return 1.0  # niente chiesto: l'aderenza non discrimina
    return sum(pieces) / len(pieces)


def _cost_part(plan: Any, estimate: Any) -> float:
    """1.0 quando il costo e' zero, 0.5 al tetto, sempre decrescente."""
    info = estimate if isinstance(estimate, Mapping) else {}
    seconds = _number(info.get("seconds"))
    if seconds is None or seconds < 0:
        return 0.5
    output = _number(info.get("output_sec"))
    duration = _number(_get(plan, "duration_sec", 0.0)) or 0.0
    if output is not None and output > 1e-9 and duration > 0:
        seconds = seconds * duration / output
    return 1.0 / (1.0 + seconds / COST_CEILING_SEC)


def _novelty(plan: Any, other: Any) -> float:
    if other is None:
        return 0.0
    left = _signature(plan)
    right = _signature(other)
    total = max(len(left), len(right))
    changed = 0
    for index in range(total):
        if index >= len(left) or index >= len(right) or left[index] != right[index]:
            changed += 1
    slot_distance = changed / total if total else 0.0
    a = _number(_get(plan, "duration_sec", 0.0)) or 0.0
    b = _number(_get(other, "duration_sec", 0.0)) or 0.0
    span = max(a, b)
    duration_distance = min(1.0, abs(a - b) / span) if span > 1e-9 else 0.0
    return min(1.0, 0.8 * slot_distance + 0.2 * duration_distance)


def score(
    plan: Any,
    coherence_report: Any = None,
    estimate: Any = None,
    prompt_requirements: Any = None,
    *,
    other: Any = None,
    weights: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    """Punteggio unico del piano, con le componenti dichiarate.

    ``total`` e' la media pesata delle quattro parti (0..1); i pesi sono in
    :data:`WEIGHTS` e si possono sostituire con ``weights=``. ``novelty`` e'
    la distanza da ``other``: senza un altro candidato vale 0, non un premio.
    """
    active = dict(WEIGHTS)
    for key, value in (weights or {}).items():
        if key in active:
            number = _number(value)
            if number is not None:
                active[key] = number
    parts = {
        "coherence": _coherence_part(coherence_report),
        "prompt": _prompt_part(plan, prompt_requirements),
        "cost": _cost_part(plan, estimate),
        "novelty": _novelty(plan, other),
    }
    total_weight = sum(active[key] for key in parts)
    total = (
        sum(active[key] * parts[key] for key in parts) / total_weight
        if total_weight > 0
        else 0.0
    )
    return {
        "total": round(total, 6),
        "parts": {key: round(value, 6) for key, value in parts.items()},
        "weights": active,
    }


def score_variants(
    variants: Sequence[Mapping[str, Any]],
    coherence_report: Any = None,
    estimate: Any = None,
    prompt_requirements: Any = None,
    *,
    weights: Optional[Mapping[str, float]] = None,
) -> List[Dict[str, Any]]:
    """Valuta ogni variante e le restituisce con ``score`` e ``total`` attaccati.

    La novita' si misura contro l'*altro* candidato: con due candidati e' la
    distanza dall'altro, con uno solo e' 0. L'ordine non cambia mai.
    """
    items = [dict(item) for item in variants or ()]
    for index, item in enumerate(items):
        other = next(
            (
                items[other_index].get("plan")
                for other_index in range(len(items))
                if other_index != index
            ),
            None,
        )
        result = score(
            item.get("plan"),
            coherence_report,
            estimate,
            prompt_requirements,
            other=other,
            weights=weights,
        )
        item["score"] = result
        item["total"] = result["total"]
    return items


def _total(item: Any) -> float:
    if not isinstance(item, Mapping):
        return 0.0
    value = item.get("score")
    if isinstance(value, Mapping):
        number = _number(value.get("total"))
        if number is not None:
            return number
    number = _number(item.get("total"))
    return number if number is not None else 0.0


def _parts(item: Any) -> Dict[str, float]:
    if not isinstance(item, Mapping):
        return {}
    value = item.get("score")
    if not isinstance(value, Mapping):
        return {}
    parts = value.get("parts")
    return dict(parts) if isinstance(parts, Mapping) else {}


def choose(variants: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Sceglie il candidato col punteggio piu' alto; a parita', il primo.

    ``why`` dice quali componenti hanno deciso (o che e' stata una parita').
    Nessun riordino, nessun timer: la stessa lista produce sempre la stessa scelta.
    """
    items = list(variants or ())
    if not items:
        raise ValueError("choose: serve almeno un candidato")
    totals = [_total(item) for item in items]
    best = 0
    for index in range(1, len(items)):
        if totals[index] > totals[best]:
            best = index
    chosen_id = str(items[best].get("id") or f"candidate-{best}")
    scores: Dict[str, Dict[str, Any]] = {}
    for index, item in enumerate(items):
        item_id = str(item.get("id") or f"candidate-{index}")
        scores[item_id] = {
            "total": round(totals[index], 6),
            "parts": {
                key: round(value, 6) for key, value in _parts(item).items()
            },
        }
    runner: Optional[int] = None
    for index in range(len(items)):
        if index == best:
            continue
        if runner is None or totals[index] > totals[runner] + 1e-12:
            runner = index
    if runner is None:
        why = f"un solo candidato ({chosen_id}): nessun confronto, vince per definizione"
    else:
        chosen_parts = _parts(items[best])
        other_parts = _parts(items[runner])
        winners = [
            key
            for key in WEIGHTS
            if round(chosen_parts.get(key, 0.0) - other_parts.get(key, 0.0), 6) > 0
        ]
        if winners:
            detail = ", ".join(
                f"{key} {chosen_parts.get(key, 0.0):.2f} vs "
                f"{other_parts.get(key, 0.0):.2f}"
                for key in winners
            )
            why = (
                f"{chosen_id} vince su {str(items[runner].get('id') or runner)} "
                f"({totals[best]:.3f} vs {totals[runner]:.3f}) per {detail}"
            )
        elif abs(totals[best] - totals[runner]) > 1e-9:
            why = (
                f"{chosen_id} vince su {str(items[runner].get('id') or runner)} per il "
                f"totale ({totals[best]:.3f} vs {totals[runner]:.3f}); nessuna "
                "componente dichiarata e' superiore"
            )
        else:
            why = (
                f"parita' di punteggio ({totals[best]:.3f}): vince il primo "
                f"candidato ({chosen_id})"
            )
    return {"chosen": chosen_id, "why": why, "scores": scores}

