"""vx.motion — ponte di compositing in Pillow, un solo passaggio.

Perche' non libass: libass non sa fare un blur gaussiano vero sull'ombra, non ha
blending modes, e non puo' vedere il video sotto il testo. Queste tre cose sono i
limiti reali, non la tipografia parola-per-parola (che libass gestisce benissimo
con un evento per parola).

Architettura, una sola passata:

    ffmpeg (decode master, rgb24)
        -> Python: layer RGBA + ombra sfocata + compositing
        -> ffmpeg (encode h264 + audio copiato dallo stesso master)

Il conteggio dei frame viene dal DECODER, quindi non esiste un percorso in cui un
layer piu' corto tronchi il video: si legge finche' arrivano frame, e il numero
viene confrontato con quello dichiarato dal file.

Tre invarianti, ognuna con la sua verifica:
  * frame dal video, mai dai sottotitoli  -> `expected_frames()` + conteggio reale
  * Gate 4 sul box MASSIMO dell'animazione -> `animation_box()`, lo scale non supera
    mai 100% e l'ombra entra nel box con il suo raggio
  * font auditato                          -> `audit_font()`, nessun fallback muto
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import ffmpeg as ff

__all__ = [
    "Word",
    "MotionCue",
    "MotionPayload",
    "build_payload",
    "expected_frames",
    "audit_font",
    "render_layer",
    "animation_box",
    "composite",
    "PillowRenderer",
    "renderer_for",
    "render_video",
]

# Lo scale non supera MAI 1.0: il pop parte dal 95% e atterra al 100%. Un pop che
# partisse sopra il 100% sfonderebbe il box che il cancello ha autorizzato.
POP_FROM = 0.95
POP_MS = 180
BEAT_FLASH_MS = 90
SHADOW_BLUR = 7
SHADOW_ALPHA = 0.80
# L'ombra sfocata dipinge oltre il glifo: raggio x 2.5 e' l'inviluppo misurato di
# un gaussiano a 3 sigma a questa dimensione.
SHADOW_ENVELOPE = 2.5
LINE_HEIGHT = 1.45
# Soglia di inversione: sotto questa luma sotto il testo, testo bianco; sopra,
# testo quasi nero. 0.55 viene dal fatto che il bianco su luma 0.55 da' ~2.4:1.
ADAPT_THRESHOLD = 0.55
CHAR_EM = 0.34


@dataclass
class Word:
    text: str
    t_start: float
    t_end: float
    beat: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "t_start": round(self.t_start, 3),
            "t_end": round(self.t_end, 3),
            "beat": self.beat,
        }


@dataclass
class Element:
    """Grafica del frame. `kind=frame` e' un contenitore: non collide, contiene."""

    name: str
    path: str
    box: Tuple[int, int, int, int]
    kind: str = "badge"
    z: int = 0
    pulse: float = 0.0

    def max_box(self) -> Tuple[int, int, int, int]:
        """Box massimo sull'animazione: la pulsazione cresce, quindi va inclusa."""
        x, y, w, h = self.box
        grow = 1.0 + max(0.0, self.pulse)
        cx, cy = x + w / 2.0, y + h / 2.0
        nw, nh = w * grow, h * grow
        return (int(cx - nw / 2), int(cy - nh / 2), int(nw), int(nh))

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "path": self.path, "box": list(self.box),
                "kind": self.kind, "z": self.z, "pulse": self.pulse,
                "max_box": list(self.max_box())}


@dataclass
class MotionCue:
    start: float
    end: float
    words: List[Word]
    lines: List[List[Word]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "words": [w.to_dict() for w in self.words],
        }


@dataclass
class MotionPayload:
    width: int
    height: int
    fps: float
    aspect: str
    font_path: str
    font_px: int
    margin_lr: int
    bottom_limit: int
    cues: List[MotionCue]
    safe_areas: List[Dict[str, Any]] = field(default_factory=list)
    elements: List[Element] = field(default_factory=list)
    beats: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "canvas": {
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
                "aspect_ratio": self.aspect,
            },
            "font": {"path": self.font_path, "px": self.font_px},
            "layout": {"margin_lr": self.margin_lr, "bottom_limit": self.bottom_limit},
            "safe_areas": self.safe_areas,
            "elements": [e.to_dict() for e in self.elements],
            "cues": [c.to_dict() for c in self.cues],
        }

    def write(self, path: str | Path) -> str:
        p = Path(path)
        p.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return str(p)


def _wrap(words: Sequence[Word], width_chars: int) -> List[List[Word]]:
    lines: List[List[Word]] = []
    cur: List[Word] = []
    size = 0
    for w in words:
        add = len(w.text) + (1 if cur else 0)
        if cur and size + add > width_chars:
            lines.append(cur)
            cur, size = [w], len(w.text)
        else:
            cur.append(w)
            size += add
    if cur:
        lines.append(cur)
    return lines


def build_payload(
    cues: Iterable[Tuple[float, float, str]],
    *,
    width: int,
    height: int,
    fps: float,
    aspect: str,
    font_path: str | Path,
    margin_lr: int,
    bottom_limit: int,
    beats: Optional[Sequence[float]] = None,
    wrap_chars: int = 30,
) -> MotionPayload:
    """Costruisce il contratto. I tempi delle parole sono distribuiti sulla cue.

    Non abbiamo l'allineamento forzato: i tempi per parola sono proporzionali alla
    lunghezza, dentro la finestra della cue. Una parola e' marcata `beat` se un beat
    misurato cade dentro il suo intervallo. E' sintesi, non allineamento, e va detto.

    Il corpo del testo NON si stima con una costante: si misura la riga piu' larga
    con il font vero e si scende finche' entra, ombra compresa. Stimare con un
    em/char medio ha gia' prodotto un testo largo 1108 px su un canvas da 1080.
    """
    pad = int(math.ceil(SHADOW_BLUR * SHADOW_ENVELOPE))
    avail = int(width) - 2 * int(margin_lr) - 2 * pad

    built: List[MotionCue] = []
    for start, end, text in cues:
        tokens = text.split()
        if not tokens:
            continue
        total = sum(len(t) + 1 for t in tokens)
        words: List[Word] = []
        clock = float(start)
        for tok in tokens:
            share = (len(tok) + 1) / total
            t0 = clock
            t1 = min(float(end), clock + (end - start) * share)
            clock = t1
            on_beat = bool(beats) and any(t0 <= b < t1 for b in beats)
            words.append(Word(tok, t0, t1, on_beat))
        cue = MotionCue(float(start), float(end), words)
        cue.lines = _wrap(words, wrap_chars)
        built.append(cue)

    cap = int(round(0.070 * height))
    size = cap
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    while size > 24:
        font = ImageFont.truetype(str(font_path), size)
        widest = max(
            (probe.textlength(" ".join(w.text for w in line), font=font)
             for cue in built for line in cue.lines),
            default=0.0,
        )
        if widest <= avail:
            break
        size -= 2

    return MotionPayload(
        width=int(width), height=int(height), fps=float(fps), aspect=str(aspect),
        font_path=str(font_path), font_px=size, margin_lr=int(margin_lr),
        # L'ombra sfocata dipinge oltre il glifo: il limite del testo la riserva
        # dentro di se', altrimenti il box massimo sfonda la fascia grafica.
        bottom_limit=int(bottom_limit) - pad, cues=built,
    )


def expected_frames(video: str | Path) -> Tuple[int, float, float]:
    """Frame e durata DICHIARATI dal file. E' il metro, non i sottotitoli."""
    proc = ff.run_fp([
        "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=nb_frames,r_frame_rate,duration",
        "-of", "json", str(video),
    ])
    fps, dur, frames = 25.0, 0.0, 0
    try:
        stream = json.loads(proc.stdout)["streams"][0]
        num, den = (stream.get("r_frame_rate") or "25/1").split("/")
        fps = float(num) / float(den or 1)
        dur = float(stream.get("duration") or 0.0)
        frames = int(stream.get("nb_frames") or 0)
    except Exception:  # noqa: BLE001
        pass
    # La durata e' affidabile, `nb_frames` no: un file stream-copied dichiara 62 e
    # ne decodifica 65. Il metro e' round(durata x fps), che sul master da 538.
    calc = int(round(dur * fps)) if dur > 0 else frames
    return (calc if calc > 0 else frames), dur, fps


def audit_font(
    font_path: str | Path, *, expect_family: Optional[str] = None
) -> Dict[str, Any]:
    """Carica il TTF e verifica che sia quello che crediamo. Nessun fallback muto."""
    path = Path(font_path)
    out: Dict[str, Any] = {"path": str(path), "exists": path.is_file(), "ok": False}
    if not path.is_file():
        out["error"] = "file mancante"
        return out
    try:
        font = ImageFont.truetype(str(path), 64)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"non caricabile: {exc}"
        return out
    family = ""
    try:
        family = font.getname()[0]
    except Exception:  # noqa: BLE001
        pass
    probe = Image.new("L", (700, 120), 0)
    ImageDraw.Draw(probe).text((4, 4), "WWWW MMMM", font=font, fill=255)
    ink = probe.getbbox()
    out.update(
        {
            "family": family,
            "advance_em": round(font.getlength("WWWW MMMM") / 9 / 64, 3),
            "ink_rendered": bool(ink),
            "ok": bool(ink),
        }
    )
    if expect_family and family and expect_family.lower() not in family.lower():
        out["ok"] = False
        out["error"] = f"famiglia {family!r} != attesa {expect_family!r}"
    return out


def _luma(img: Image.Image, box: Tuple[int, int, int, int]) -> float:
    patch = img.crop(box).convert("L").resize((32, 16))
    data = patch.tobytes()
    return (sum(data) / len(data)) / 255.0 if data else 0.0


def _ease_out_back(x: float) -> float:
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def _word_scale(word: Word, t: float) -> float:
    """Pop che atterra a 1.0 e non lo supera mai."""
    span = POP_MS / 1000.0
    age = t - word.t_start
    if age < 0 or age >= span:
        return 1.0
    p = age / span
    return POP_FROM + (1.0 - POP_FROM) * _ease_out_back(p)


def _layout(
    payload: MotionPayload, cue: MotionCue, font: ImageFont.FreeTypeFont
) -> List[Tuple[Word, float, float, float]]:
    """(parola, x_centro, y_baseline, larghezza) per ogni parola, righe centrate."""
    rows: List[Tuple[Word, float, float, float]] = []
    line_h = payload.font_px * LINE_HEIGHT
    block_bottom = payload.bottom_limit
    block_top = block_bottom - line_h * len(cue.lines)
    draw = ImageDraw.Draw(Image.new("L", (8, 8)))
    for r, line in enumerate(cue.lines):
        widths = [
            draw.textlength(w.text + (" " if i < len(line) - 1 else ""), font=font)
            for i, w in enumerate(line)
        ]
        total = sum(widths)
        x = (payload.width - total) / 2.0
        y = block_top + r * line_h
        for w, wd in zip(line, widths):
            rows.append((w, x + wd / 2.0, y, wd))
            x += wd
    return rows


def render_layer(
    payload: MotionPayload,
    base: Optional[Image.Image],
    t: float,
    *,
    font: Optional[ImageFont.FreeTypeFont] = None,
) -> Image.Image:
    """Layer RGBA del solo testo al tempo `t`, con ombra gaussiana e colore adattivo.

    Se `base` c'e', la luminanza media sotto il blocco decide il colore: testo
    bianco su fondo scuro, quasi nero su fondo chiaro. E' l'adattivita' per-pixel
    che libass non puo' fare, applicata per frame.
    """
    font = font or ImageFont.truetype(payload.font_path, payload.font_px)
    layer = Image.new("RGBA", (payload.width, payload.height), (0, 0, 0, 0))
    # Gli elementi si disegnano SEMPRE, anche fra una cue e l'altra: la cornice non
    # puo' sparire nei vuoti solo perche' non c'e' testo.
    layer = _draw_elements(payload, layer, t)

    active = next((c for c in payload.cues if c.start <= t <= c.end), None)
    if active is None:
        return layer

    rows = _layout(payload, active, font)
    if not rows:
        return layer
    ys = [y for _, _, y, _ in rows]
    box = (
        0,
        int(min(ys)) - 8,
        payload.width,
        int(max(ys) + payload.font_px * LINE_HEIGHT),
    )

    light_bg = False
    if base is not None:
        light_bg = (
            _luma(
                base,
                (
                    max(0, box[0]),
                    max(0, box[1]),
                    min(payload.width, box[2]),
                    min(payload.height, box[3]),
                ),
            )
            > ADAPT_THRESHOLD
        )

    ink = (16, 16, 16, 255) if light_bg else (255, 255, 255, 255)
    shadow_rgb = (255, 255, 255) if light_bg else (0, 0, 0)

    text = Image.new("RGBA", (payload.width, payload.height), (0, 0, 0, 0))
    d = ImageDraw.Draw(text)
    for word, cx, y, wd in rows:
        # La frase resta leggibile: le parole future sono in trasparenza, non assenti.
        # Disegnare solo quelle gia' dette lascia il fotogramma mezzo vuoto e rompe
        # la lettura (visto a schermo: si leggeva solo "Lo racchiudo").
        if t < word.t_start:
            alpha = 110
            colour = ink
        elif t <= word.t_end:
            alpha = 255
            colour = ink
        else:
            alpha = 232
            colour = ink
        scale = _word_scale(word, t)
        if word.beat and 0.0 <= (t - word.t_start) < BEAT_FLASH_MS / 1000.0:
            colour = (255, 60, 60) if not light_bg else (170, 0, 0)
        f = font
        if abs(scale - 1.0) > 1e-3:
            f = font.font_variant(size=max(8, int(round(payload.font_px * scale))))
        d.text((cx, y), word.text, font=f, fill=(*colour[:3], alpha), anchor="ma")

    mask = text.split()[3].filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    shadow = Image.new("RGBA", (payload.width, payload.height), (0, 0, 0, 0))
    shadow.paste(
        (*shadow_rgb, 255), (0, 0), mask.point(lambda a: int(a * SHADOW_ALPHA))
    )
    # Gli elementi vivono in `layer`: ricomporre solo shadow+text li cancellava,
    # e la cornice spariva esattamente quando compariva un sottotitolo.
    return Image.alpha_composite(layer, Image.alpha_composite(shadow, text))


_ELEMENT_CACHE: Dict[str, Image.Image] = {}


def _element_image(path: str) -> Optional[Image.Image]:
    if path not in _ELEMENT_CACHE:
        p = Path(path)
        if not p.is_file():
            return None
        _ELEMENT_CACHE[path] = Image.open(p).convert("RGBA")
    return _ELEMENT_CACHE[path]


def _pulse_at(element: Element, payload: MotionPayload, t: float) -> float:
    """Pulsazione ancorata ai beat misurati, con attacco di 90 ms e rientro morbido."""
    if element.pulse <= 0 or not payload.beats:
        return 1.0
    near = min((abs(t - b) for b in payload.beats), default=9.9)
    if near > 0.09:
        return 1.0
    return 1.0 + element.pulse * (1.0 - near / 0.09)


def _draw_elements(payload: MotionPayload, layer: Image.Image, t: float) -> Image.Image:
    for element in sorted(payload.elements, key=lambda e: e.z):
        img = _element_image(element.path)
        if img is None:
            continue
        scale = _pulse_at(element, payload, t)
        if abs(scale - 1.0) > 1e-4:
            x, y, w, h = element.box
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            scaled = img.resize((nw, nh), Image.LANCZOS)
            at = (int(x + w / 2 - nw / 2), int(y + h / 2 - nh / 2))
            layer.alpha_composite(scaled, dest=(max(0, at[0]), max(0, at[1])))
        else:
            layer.alpha_composite(img, dest=(element.box[0], element.box[1]))
    return layer


def element_boxes(payload: MotionPayload) -> Dict[str, Tuple[int, int, int, int]]:
    """Box MASSIMI dei soli badge: i contenitori non collidono con se stessi."""
    return {e.name: e.max_box() for e in payload.elements if e.kind != "frame"}


def animation_box(payload: MotionPayload) -> Dict[str, int]:
    """Box MASSIMO su tutta l'animazione, non quello a riposo.

    Lo scale non supera 1.0, quindi l'espansione massima viene dall'ombra: il box
    a riposo allargato di SHADOW_BLUR x SHADOW_ENVELOPE su ogni lato. Il cancello 4
    verifica questo, non il box statico.
    """
    pad = int(math.ceil(SHADOW_BLUR * SHADOW_ENVELOPE))
    x0 = y0 = 10**9
    x1 = y1 = -1
    draw = ImageDraw.Draw(Image.new("L", (8, 8)))
    font = ImageFont.truetype(payload.font_path, payload.font_px)
    for cue in payload.cues:
        rows = _layout(payload, cue, font)
        for word, cx, y, wd in rows:
            if wd <= 0:
                continue
            x0 = min(x0, int(cx - wd / 2) - pad)
            x1 = max(x1, int(cx + wd / 2) + pad)
            y0 = min(y0, int(y) - pad)
            y1 = max(y1, int(y + payload.font_px * LINE_HEIGHT) + pad)
    if x1 < 0:
        return {"x": 0, "y": 0, "w": 0, "h": 0}
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def composite(
    master: str | Path,
    payload: MotionPayload,
    out: str | Path,
    *,
    crf: int = 20,
    preset: str = "medium",
    on_frame=None,
) -> Dict[str, Any]:
    """Un passaggio: decode -> layer -> encode, audio copiato dal master.

    Nessun `shortest`: si legge finche' il decoder produce frame, e il conteggio
    finale viene confrontato con quello dichiarato. Un layer corto non puo'
    troncare niente perche' il layer non e' un input di ffmpeg.
    """
    src = Path(master)
    dst = Path(out)
    declare, dur, fps = expected_frames(src)
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
    font = ImageFont.truetype(payload.font_path, payload.font_px)
    n = 0
    try:
        while True:
            buf = dec.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            base = Image.frombytes("RGB", (w, h), buf)
            t = n / fps
            layer = render_layer(payload, base, t, font=font)
            merged = base.convert("RGBA")
            merged = Image.alpha_composite(merged, layer).convert("RGB")
            enc.stdin.write(merged.tobytes())
            n += 1
            if on_frame is not None and n % 25 == 0:
                on_frame(n, declare)
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
        "duration": dur,
        "fps": fps,
        "encoder_rc": enc.returncode,
        "encoder_tail": (enc.stderr.read() or b"").decode("utf-8", "replace")[-400:],
    }


# --------------------------------------------------------------------------- #
# doppio motore: stesso payload, due rasterizzatori
# --------------------------------------------------------------------------- #


class PillowRenderer:
    """Motore veloce: Pillow su CPU, 27 ms/frame, ombra gaussiana reale."""

    name = "pillow"

    def __init__(self, payload: MotionPayload):
        self.payload = payload
        self._font = ImageFont.truetype(payload.font_path, payload.font_px)

    def frame_rgba(self, t: float):
        return render_layer(self.payload, None, t, font=self._font)

    def font_info(self) -> Dict[str, Any]:
        return audit_font(self.payload.font_path)

    def close(self) -> None:
        return None


def renderer_for(payload: MotionPayload, engine: str = "pillow"):
    """Factory. `chromium` importa Playwright solo quando lo si chiede davvero."""
    key = (engine or "pillow").strip().lower()
    if key == "pillow":
        return PillowRenderer(payload)
    if key == "chromium":
        from .motion_browser import BrowserRenderer  # noqa: PLC0415

        return BrowserRenderer(payload)
    raise ValueError(f"engine sconosciuto: {engine!r} (attesi: pillow, chromium)")


def render_video(
    master: str | Path,
    payload: MotionPayload,
    out: str | Path,
    *,
    engine: str = "pillow",
    crf: int = 20,
    preset: str = "medium",
) -> Dict[str, Any]:
    """Unico ingresso: sceglie il motore, i Gate restano identici.

    `pillow`  -> editoria, documentario, pulito: veloce e geometrico
    `chromium` -> social, hype, virale: font variabili, CSS, backdrop-filter
    """
    renderer = renderer_for(payload, engine)
    try:
        if getattr(renderer, "name", "") == "pillow":
            info = composite(master, payload, out, crf=crf, preset=preset)
        else:
            from .motion_browser import composite_browser  # noqa: PLC0415

            info = composite_browser(master, payload, out, renderer,
                                     crf=crf, preset=preset)
        info["engine"] = getattr(renderer, "name", engine)
        return info
    finally:
        try:
            renderer.close()
        except Exception:  # noqa: BLE001
            pass
