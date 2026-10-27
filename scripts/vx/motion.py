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

import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import ffmpeg as ff
from . import motion_cache, motion_lint
from .contract import VxError

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
    # F3 - Motion Studio (Chromium, virtual time)
    "MotionError",
    "available",
    "load_template",
    "validate_params",
    "capture",
    "to_video",
    "templates_root",
    "MOTION_FRAME_TIMEOUT_S",
    "MOTION_CAPTURE_TIMEOUT_S",
    "FREEZE_CSS",
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
        return {
            "name": self.name,
            "path": self.path,
            "box": list(self.box),
            "kind": self.kind,
            "z": self.z,
            "pulse": self.pulse,
            "max_box": list(self.max_box()),
        }


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
    style: Dict[str, Any] = field(default_factory=dict)

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
    style: Optional[Dict[str, Any]] = None,
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
            (
                probe.textlength(" ".join(w.text for w in line), font=font)
                for cue in built
                for line in cue.lines
            ),
            default=0.0,
        )
        if widest <= avail:
            break
        size -= 2

    st = dict(style or {})
    if st.get("font_path"):
        font_path = st["font_path"]
    if st.get("font_px"):
        size = int(st["font_px"])
    if st.get("bottom_limit"):
        bottom_limit = int(st["bottom_limit"])
    payload = MotionPayload(
        width=int(width),
        height=int(height),
        fps=float(fps),
        aspect=str(aspect),
        font_path=str(font_path),
        font_px=size,
        margin_lr=int(margin_lr),
        # L'ombra sfocata dipinge oltre il glifo: il limite del testo la riserva
        # dentro di se', altrimenti il box massimo sfonda la fascia grafica.
        bottom_limit=int(bottom_limit) - pad,
        cues=built,
        style=st,
    )
    return payload


def expected_frames(video: str | Path) -> Tuple[int, float, float]:
    """Frame e durata dichiarati dal file, con i fallback che il mondo reale impone.

    WebM/Matroska non espone `duration` ne' `nb_frames` a livello di stream: stanno
    nel container, e `nb_frames` spesso non c'e' affatto. Senza questi fallback la
    sonda legge 0.00s e il render muore su ogni .webm (trovato dal corpus reale).
    """
    proc = ff.run_fp(
        [
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_frames,r_frame_rate,duration:format=duration",
            "-of",
            "json",
            str(video),
        ]
    )
    fps, dur, frames = 25.0, 0.0, 0
    try:
        payload = json.loads(proc.stdout)
        stream = payload["streams"][0]
        num, den = (stream.get("r_frame_rate") or "25/1").split("/")
        fps = float(num) / float(den or 1)
        dur = float(stream.get("duration") or 0.0)
        frames = int(stream.get("nb_frames") or 0)
        if dur <= 0:
            dur = float((payload.get("format") or {}).get("duration") or 0.0)
    except Exception:  # noqa: BLE001
        pass
    if frames <= 0 and dur > 0:
        frames = int(round(dur * fps))
    if dur <= 0 or frames <= 0:
        counted = ff.run_fp(
            [
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=nb_read_frames,duration:format=duration",
                "-of",
                "json",
                str(video),
            ]
        )
        try:
            payload = json.loads(counted.stdout)
            stream = payload["streams"][0]
            read = int(stream.get("nb_read_frames") or 0)
            if read > 0:
                frames = read
            if dur <= 0:
                dur = float(
                    stream.get("duration")
                    or (payload.get("format") or {}).get("duration")
                    or 0.0
                )
            if dur <= 0 and frames > 0:
                dur = frames / fps
        except Exception:  # noqa: BLE001
            pass
    return frames, dur, fps


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


def _word_scale(word: Word, t: float, pop_ms: float = POP_MS) -> float:
    """Pop che atterra a 1.0 e non lo supera mai."""
    span = max(1.0, float(pop_ms)) / 1000.0
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

    style = payload.style or {}
    rows = _layout(payload, active, font)
    # animazione dichiarata per questa cue (slide_up/left/right, typeon, pop, none)
    try:
        cue_idx = payload.cues.index(active)
    except ValueError:
        cue_idx = 0
    spec_list = (style.get("animation", {}) or {}).get("per_cue", []) or []
    spec = next((s for s in spec_list if int(s.get("cue", -1)) == cue_idx), {})
    effect = str(spec.get("in", "none"))
    anim_ms = max(1.0, float(spec.get("ms", 0))) / 1000.0
    age = max(0.0, t - active.start)
    prog = min(1.0, age / anim_ms) if anim_ms > 0 else 1.0
    ease = 1.0 - (1.0 - prog) ** 3
    if effect == "slide_up":
        dy, dx = (1.0 - ease) * 70.0, 0.0
    elif effect == "slide_left":
        dy, dx = 0.0, (1.0 - ease) * 90.0
    elif effect == "slide_right":
        dy, dx = 0.0, -(1.0 - ease) * 90.0
    else:
        dy = dx = 0.0
    if effect == "typeon":
        visible_chars = int(round(ease * 10**6))
    else:
        visible_chars = 10**6
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

    if style.get("ink"):
        c = style["ink"]
        ink = (
            (int(c[0]), int(c[1]), int(c[2]), 255)
            if isinstance(c, (list, tuple))
            else ink
        )
    else:
        ink = (16, 16, 16, 255) if light_bg else (255, 255, 255, 255)
    if not isinstance(ink, tuple) or len(ink) != 4:
        ink = (16, 16, 16, 255) if light_bg else (255, 255, 255, 255)
    shadow_rgb = (255, 255, 255) if sum(ink[:3]) < 200 else (0, 0, 0)

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
        scale = _word_scale(word, t, float(style.get("pop_ms", POP_MS)))
        if str(style.get("case", "")).lower() == "upper":
            word_text = word.text.upper()
        else:
            word_text = word.text
        if word.beat and 0.0 <= (t - word.t_start) < BEAT_FLASH_MS / 1000.0:
            # palette a rotazione: una tinta per cue, se il brief la fornisce
            pal = style.get("palette") or []
            if pal:
                pick = pal[cue_idx % len(pal)]
                colour = (int(pick[0]), int(pick[1]), int(pick[2]))
            else:
                accent = style.get("accent")
                if isinstance(accent, (list, tuple)) and len(accent) >= 3:
                    colour = (int(accent[0]), int(accent[1]), int(accent[2]))
                else:
                    colour = (255, 60, 60) if not light_bg else (170, 0, 0)
        f = font
        if abs(scale - 1.0) > 1e-3:
            f = font.font_variant(size=max(8, int(round(payload.font_px * scale))))
        if len(word_text) > visible_chars:
            word_text = word_text[:visible_chars]
        if not word_text:
            continue
        d.text(
            (cx + dx, y + dy), word_text, font=f, fill=(*colour[:3], alpha), anchor="ma"
        )

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
    grade_node=None,
    lossless: bool = False,
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
            if grade_node is not None:
                from . import grade as grade_mod  # noqa: PLC0415

                base = grade_mod.apply_pillow(base, grade_node)
            t = n / fps
            layer = render_layer(payload, base, t, font=font)
            # `alpha_composite` ricompone TUTTO il frame: il round-trip di
            # premoltiplicazione sposta ogni pixel di ~1/255, e nel primo percentile
            # (ombra profonda) quel singolo codice fa sfondare il Gate 5.
            # Il paste con maschera tocca solo dove l'alpha e' attivo e lascia il
            # fondo intatto per costruzione.
            merged = base
            if layer.getbbox() is not None:
                canvas = base.convert("RGBA")
                canvas.paste(layer, (0, 0), layer)
                merged = canvas.convert("RGB")
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
    grade: Optional[Dict[str, Any]] = None,
    lossless: bool = False,
) -> Dict[str, Any]:
    """Unico ingresso: sceglie il motore, i Gate restano identici.

    `pillow`  -> editoria, documentario, pulito: veloce e geometrico
    `chromium` -> social, hype, virale: font variabili, CSS, backdrop-filter
    """
    renderer = renderer_for(payload, engine)
    try:
        from . import grade as grade_mod  # noqa: PLC0415

        node = grade_mod.parse(grade) if grade else None
        if getattr(renderer, "name", "") == "pillow":
            info = composite(
                master,
                payload,
                out,
                crf=crf,
                preset=preset,
                grade_node=node,
                lossless=lossless,
            )
        else:
            from .motion_browser import composite_browser  # noqa: PLC0415

            info = composite_browser(
                master, payload, out, renderer, crf=crf, preset=preset
            )
        info["engine"] = getattr(renderer, "name", engine)
        return info
    finally:
        try:
            renderer.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# F3 - Motion Studio: HTML template capture on virtual time (Chromium)
# --------------------------------------------------------------------------- #
#
# The Pillow engine above renders an existing payload. This second entry point
# starts from an authored template instead: a directory with `index.html` and
# `template.json`. The harness owns time - it asks the page for one instant at a
# time through `window.__vxSeek(t)` and screenshots only after that promise has
# resolved - so a wall-clock CSS animation can never make two runs differ.
#
# The browser is the system Chrome/Edge via Playwright's channel, never a
# downloaded build and never an absolute path.

MOTION_FRAME_TIMEOUT_S = 10.0
MOTION_CAPTURE_TIMEOUT_S = 300.0

FREEZE_CSS = (
    "*{animation-play-state:paused!important;"
    "transition:none!important;"
    "animation-delay:0s!important}"
)

_BROWSER_ARGS = [
    "--force-color-profile=srgb",
    "--disable-lcd-text",
    "--font-render-hinting=none",
    "--disable-features=PaintHolding",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--hide-scrollbars",
]

_ENGINE_ORDER = ("chrome", "msedge")

_ENGINE_LOCATIONS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "chrome": (
        ("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
        ("PROGRAMW6432", "Google/Chrome/Application/chrome.exe"),
        ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
        ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
        ("/Applications/Google Chrome.app/Contents/MacOS", "Google Chrome"),
        ("/opt/google/chrome", "chrome"),
    ),
    "msedge": (
        ("PROGRAMFILES(X86)", "Microsoft/Edge/Application/msedge.exe"),
        ("PROGRAMFILES", "Microsoft/Edge/Application/msedge.exe"),
        ("PROGRAMW6432", "Microsoft/Edge/Application/msedge.exe"),
        ("LOCALAPPDATA", "Microsoft/Edge/Application/msedge.exe"),
    ),
}

_ENGINE_COMMANDS: Dict[str, Tuple[str, ...]] = {
    "chrome": ("chrome", "chrome.exe", "google-chrome", "chrome-linux"),
    "msedge": ("msedge", "msedge.exe", "microsoft-edge"),
}

_ALLOWED_SCHEMES = ("file:", "data:", "about:", "blob:")

_PARAM_TYPES = frozenset({"string", "number", "int", "color", "bool"})


class MotionError(VxError):
    """A declared Motion Studio failure: every branch carries a stable code."""

    def __init__(self, code: str, message: str, **payload: Any) -> None:
        self.code = code
        self.payload: Dict[str, Any] = {"code": code, **payload}
        detail = f"{code}: {message}"
        self.payload["message"] = message
        super().__init__(detail)

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else self.payload["code"]


def templates_root() -> Path:
    """`assets/templates/` of the skill, resolved from this file, never a literal path."""
    return Path(__file__).resolve().parents[2] / "assets" / "templates"


def _engine_installed(engine: str) -> bool:
    for env_name, relative in _ENGINE_LOCATIONS.get(engine, ()):
        if not env_name.startswith("/"):
            base = os.environ.get(env_name)
            if not base:
                continue
            candidate = Path(base) / relative
        else:
            candidate = Path(env_name) / relative
        if candidate.is_file():
            return True
    for command in _ENGINE_COMMANDS.get(engine, ()):
        if shutil.which(command):
            return True
    return False


def available() -> Dict[str, Any]:
    """Probe the import and the system browsers. Never launches anything."""
    report: Dict[str, Any] = {
        "available": False,
        "engine": None,
        "reason": None,
        "candidates": [],
    }
    try:
        import playwright.sync_api  # noqa: F401,PLC0415
    except ImportError as exc:
        report["reason"] = f"PLAYWRIGHT_MISSING: {exc}"
        return report
    installed = [name for name in _ENGINE_ORDER if _engine_installed(name)]
    if not installed:
        report["reason"] = (
            "ENGINE_NOT_FOUND: neither Chrome nor Edge is installed; "
            "no downloaded browser is permitted"
        )
        return report
    report.update(
        {
            "available": True,
            "engine": installed[0],
            "reason": "ok",
            "candidates": installed,
        }
    )
    return report


def _resolve_template_dir(template_dir: str | Path) -> Path:
    given = Path(template_dir)
    if given.is_dir():
        return given.resolve()
    named = templates_root() / str(template_dir)
    if named.is_dir():
        return named.resolve()
    raise MotionError(
        "TEMPLATE_MISSING",
        f"no template directory at {given} nor under {templates_root()}",
        template=str(template_dir),
    )


def load_template(template_dir: str | Path) -> Dict[str, Any]:
    """Read `index.html` + `template.json` and refuse anything malformed."""
    root = _resolve_template_dir(template_dir)
    html = root / "index.html"
    meta_path = root / "template.json"
    for required in (html, meta_path):
        if not required.is_file():
            raise MotionError(
                "TEMPLATE_MISSING",
                f"{required.name} missing in {root.name}",
                template=root.name,
                file=required.name,
            )
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MotionError(
            "TEMPLATE_INVALID",
            f"template.json is not readable JSON: {exc}",
            template=root.name,
        ) from exc
    if not isinstance(meta, dict):
        raise MotionError(
            "TEMPLATE_INVALID", "template.json must be an object", template=root.name
        )

    name = meta.get("name")
    if not isinstance(name, str) or not name.strip():
        raise MotionError(
            "TEMPLATE_INVALID",
            "template.json needs a non-empty 'name'",
            template=root.name,
        )
    fps = meta.get("fps")
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or fps <= 0:
        raise MotionError(
            "TEMPLATE_INVALID",
            f"'fps' must be a positive number, got {fps!r}",
            template=name,
            field="fps",
        )
    duration = meta.get("duration")
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or duration <= 0
    ):
        raise MotionError(
            "TEMPLATE_INVALID",
            f"'duration' must be a positive number of seconds, got {duration!r}",
            template=name,
            field="duration",
        )
    size = meta.get("size")
    if (
        not isinstance(size, (list, tuple))
        or len(size) != 2
        or not all(
            isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in size
        )
    ):
        raise MotionError(
            "TEMPLATE_INVALID",
            f"'size' must be [width, height] positive integers, got {size!r}",
            template=name,
            field="size",
        )
    params = meta.get("params")
    if not isinstance(params, dict):
        raise MotionError(
            "TEMPLATE_INVALID",
            "'params' must be an object of declarations",
            template=name,
            field="params",
        )
    for param_name, declaration in params.items():
        if not isinstance(declaration, dict):
            raise MotionError(
                "TEMPLATE_INVALID",
                f"parameter {param_name!r} must declare an object",
                template=name,
                param=param_name,
            )
        kind = declaration.get("type")
        if kind not in _PARAM_TYPES:
            raise MotionError(
                "TEMPLATE_INVALID",
                f"parameter {param_name!r} declares unknown type {kind!r}; "
                f"known: {sorted(_PARAM_TYPES)}",
                template=name,
                param=param_name,
            )
        if "default" not in declaration:
            raise MotionError(
                "TEMPLATE_INVALID",
                f"parameter {param_name!r} has no 'default'",
                template=name,
                param=param_name,
            )
    return {
        "name": name,
        "dir": str(root),
        "html": str(html),
        "params": params,
        "fps": float(fps),
        "duration": float(duration),
        "size": (int(size[0]), int(size[1])),
    }


def _describe_domain(declaration: Dict[str, Any]) -> str:
    values = declaration.get("values")
    if isinstance(values, (list, tuple)) and values:
        return "one of " + ", ".join(repr(v) for v in values)
    low, high = declaration.get("min"), declaration.get("max")
    if low is not None and high is not None:
        return f"between {low} and {high}"
    if low is not None:
        return f">= {low}"
    if high is not None:
        return f"<= {high}"
    return "the declared domain"


def _coerce_param(name: str, declaration: Dict[str, Any], value: Any) -> Any:
    kind = declaration["type"]
    if kind in ("number", "int"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MotionError(
                "PARAM_TYPE",
                f"parameter {name!r} expects a number, got {type(value).__name__}",
                param=name,
                expected=kind,
                got=type(value).__name__,
            )
        if kind == "int":
            if float(value) != int(value):
                raise MotionError(
                    "PARAM_TYPE",
                    f"parameter {name!r} expects an integer, got {value!r}",
                    param=name,
                    expected=kind,
                    got=value,
                )
            return int(value)
        return float(value)
    if kind == "bool":
        if not isinstance(value, bool):
            raise MotionError(
                "PARAM_TYPE",
                f"parameter {name!r} expects a boolean, got {type(value).__name__}",
                param=name,
                expected=kind,
                got=type(value).__name__,
            )
        return value
    if not isinstance(value, str):
        raise MotionError(
            "PARAM_TYPE",
            f"parameter {name!r} expects a string, got {type(value).__name__}",
            param=name,
            expected=kind,
            got=type(value).__name__,
        )
    if kind == "color" and not (declaration.get("values") or []):
        if not (value.startswith("#") and len(value) in (4, 7, 9)):
            raise MotionError(
                "PARAM_RANGE",
                f"parameter {name!r} expects a hex colour (#rgb, #rrggbb or #rrggbbaa), "
                f"got {value!r}",
                param=name,
                domain="#rgb, #rrggbb, #rrggbbaa",
                got=value,
            )
    return value


def validate_params(
    template: Dict[str, Any] | str | Path, params: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Resolve `params` against the template declarations, or refuse with a code."""
    declared_source = (
        template if isinstance(template, dict) else load_template(template)
    )
    if "params" not in declared_source:
        raise MotionError(
            "TEMPLATE_INVALID",
            "template mapping has no 'params' key",
            template=str(template),
        )
    declared: Dict[str, Dict[str, Any]] = declared_source["params"]
    given = {} if params is None else dict(params)
    for key in given:
        if key not in declared:
            raise MotionError(
                "PARAM_UNKNOWN",
                f"unknown parameter {key!r}; the template declares {sorted(declared)}",
                param=key,
                known=sorted(declared),
            )
    resolved: Dict[str, Any] = {}
    for name, declaration in declared.items():
        value = _coerce_param(
            name, declaration, given[name] if name in given else declaration["default"]
        )
        values = declaration.get("values")
        if isinstance(values, (list, tuple)) and values:
            if value not in values:
                raise MotionError(
                    "PARAM_RANGE",
                    f"parameter {name!r} = {value!r} outside {_describe_domain(declaration)}",
                    param=name,
                    domain=list(values),
                    got=value,
                )
        else:
            kind = declaration["type"]
            if kind in ("number", "int"):
                low, high = declaration.get("min"), declaration.get("max")
                if (low is not None and value < low) or (
                    high is not None and value > high
                ):
                    raise MotionError(
                        "PARAM_RANGE",
                        f"parameter {name!r} = {value!r} outside {_describe_domain(declaration)}",
                        param=name,
                        min=low,
                        max=high,
                        got=value,
                    )
            elif kind in ("string", "color"):
                limit = declaration.get("max_length", declaration.get("max"))
                if isinstance(limit, int) and len(value) > limit:
                    raise MotionError(
                        "PARAM_RANGE",
                        f"parameter {name!r} is {len(value)} characters, "
                        f"the ceiling is {limit}",
                        param=name,
                        max_length=limit,
                        got=len(value),
                    )
        resolved[name] = value
    return resolved


_SEEK_JS = """
async (spec) => {
  let fn = window;
  for (const part of spec.path.split('.')) {
    if (fn === null || fn === undefined) return {status: 'SEEK_MISSING'};
    fn = fn[part];
  }
  if (typeof fn !== 'function') return {status: 'SEEK_MISSING'};
  let timer = 0;
  try {
    const painted = Promise.resolve()
      .then(() => fn(spec.t))
      .then(() => ({status: 'OK'}))
      .catch((err) => ({status: 'SEEK_ERROR',
                        message: String((err && err.message) || err)}));
    const guard = new Promise((resolve) => {
      timer = setTimeout(() => resolve({status: 'TIMEOUT'}), spec.timeoutMs);
    });
    return await Promise.race([painted, guard]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}
"""

_FREEZE_INIT = """
(() => {
  const css = %s;
  const install = () => {
    if (document.querySelector('style[data-vx-freeze]')) return;
    const style = document.createElement('style');
    style.setAttribute('data-vx-freeze', '1');
    style.textContent = css;
    (document.head || document.documentElement).appendChild(style);
  };
  install();
  document.addEventListener('DOMContentLoaded', install, {once: true});
})();
""" % json.dumps(FREEZE_CSS)


def _seek_path(seek_js: str) -> str:
    path = (seek_js or "").strip()
    if path.startswith("window."):
        path = path[len("window.") :]
    if not path:
        raise MotionError("SEEK_MISSING", "seek_js is empty", seek=seek_js)
    return path


def _launch(candidates: Sequence[str]) -> Tuple[Any, Any, str]:
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    failures: List[str] = []
    for engine in candidates:
        pw = sync_playwright().start()
        try:
            browser = pw.chromium.launch(
                channel=engine, headless=True, args=list(_BROWSER_ARGS)
            )
        except Exception as exc:  # noqa: BLE001 - reported with a code, never swallowed
            failures.append(f"{engine}: {exc}")
            pw.stop()
            continue
        return pw, browser, engine
    raise MotionError(
        "ENGINE_LAUNCH_FAILED",
        "no system browser would start: " + " | ".join(failures),
        engines=list(candidates),
        failures=failures,
    )


def _close_quietly(page: Any, context: Any, browser: Any, pw: Any) -> None:
    for handle in (page, context, browser):
        if handle is None:
            continue
        try:
            handle.close()
        except Exception:  # noqa: BLE001 - cleanup must not mask the real error
            pass
    if pw is not None:
        try:
            pw.stop()
        except Exception:  # noqa: BLE001 - cleanup must not mask the real error
            pass


def _as_rgba_png(png: bytes) -> bytes:
    """One frame shape on disk, whatever Chromium decided to encode.

    A template painted edge to edge has no transparent pixel, and Chromium then
    writes a three-channel PNG. The frame contract cannot depend on whether a
    picture happens to be opaque: the cache, `format=rgba` and the golden
    manifests all assume RGBA. An RGBA input is returned untouched, so frames
    that already carried alpha keep exactly the bytes they had.
    """
    with Image.open(io.BytesIO(png)) as image:
        if image.mode == "RGBA":
            return png
        buffer = io.BytesIO()
        image.convert("RGBA").save(buffer, format="PNG")
    return buffer.getvalue()


def capture(
    template_dir: str | Path,
    params: Optional[Dict[str, Any]],
    out_dir: str | Path,
    *,
    fps: Optional[float] = None,
    size: Optional[Sequence[int]] = None,
    seek_js: str = "window.__vxSeek",
    frame_timeout_s: Optional[float] = None,
    capture_timeout_s: Optional[float] = None,
    skip_lint: bool = False,
    workdir: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Render one frame per 1/fps of virtual time and write RGBA PNGs.

    The static linter runs first and refuses a template with any finding, so a
    dead template never pays for a browser launch. `skip_lint` exists only for
    tests that deliberately exercise the runtime network block.

    `workdir` turns on the frame cache of :mod:`vx.motion_cache`, rooted at
    `<workdir>/.motion-cache`. Without it the cache is off, nothing extra is
    written, and the result declares `cache: {"status": "disabled", ...}` --
    there is no global cache to fall back to.
    """
    if not skip_lint:
        motion_lint.lint_or_raise(template_dir, runtime_check=False)
    template = load_template(template_dir)
    resolved = validate_params(template, params)
    cache = motion_cache.Cache(workdir)
    rate = float(fps if fps is not None else template["fps"])
    if rate <= 0:
        raise MotionError(
            "PARAM_RANGE", f"fps must be > 0, got {rate}", param="fps", got=rate
        )
    if size is None:
        width, height = template["size"]
    else:
        if len(tuple(size)) != 2:
            raise MotionError(
                "PARAM_RANGE",
                f"size must be [width, height], got {size!r}",
                param="size",
            )
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            raise MotionError(
                "PARAM_RANGE",
                f"size must be positive, got {width}x{height}",
                param="size",
            )
    frame_limit = float(
        MOTION_FRAME_TIMEOUT_S if frame_timeout_s is None else frame_timeout_s
    )
    capture_limit = float(
        MOTION_CAPTURE_TIMEOUT_S if capture_timeout_s is None else capture_timeout_s
    )
    if frame_limit <= 0 or capture_limit <= 0:
        raise MotionError(
            "PARAM_RANGE",
            "frame_timeout_s and capture_timeout_s must be > 0",
            frame_timeout_s=frame_limit,
            capture_timeout_s=capture_limit,
        )

    probe = available()
    if not probe["available"]:
        raise MotionError("MOTION_UNAVAILABLE", str(probe["reason"]))

    duration = template["duration"]
    count = max(1, int(round(duration * rate)))
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for stale in destination.glob("frame_*.png"):
        stale.unlink()

    seek_path = _seek_path(seek_js)
    blocked: List[str] = []
    degraded: List[str] = []
    paths: List[str] = []
    digests: List[str] = []

    pw = browser = context = page = None
    started = time.perf_counter()
    deadline = started + capture_limit
    engine = ""
    engine_version = ""
    try:
        pw, browser, engine = _launch(probe["candidates"])
        # `engine` names the launch channel; this names the Chromium build behind
        # it, so the golden gate catches a silent browser upgrade. Best effort:
        # an unreadable property leaves the key empty rather than failing capture.
        try:
            engine_version = str(getattr(browser, "version", "") or "")
        except Exception:  # noqa: BLE001 - diagnostic only, never fatal
            engine_version = ""
        context = browser.new_context(
            viewport={"width": width, "height": height}, device_scale_factor=1
        )
        page = context.new_page()
        page.set_default_timeout(int(frame_limit * 1000))

        def _route(route: Any) -> None:
            url = route.request.url
            if url.startswith(_ALLOWED_SCHEMES):
                route.continue_()
                return
            blocked.append(url)
            route.abort()

        page.route("**/*", _route)
        page.add_init_script(script=_FREEZE_INIT)
        try:
            page.goto(
                Path(template["html"]).as_uri(),
                wait_until="load",
                timeout=int(min(frame_limit, capture_limit) * 1000),
            )
        except Exception as exc:  # noqa: BLE001 - declared, not swallowed
            raise MotionError(
                "TEMPLATE_LOAD_FAILED",
                f"{template['name']}: the page did not load: {exc}",
                template=template["name"],
            ) from exc
        page.add_style_tag(content=FREEZE_CSS)
        page.evaluate("(payload) => { window.__vxParams = payload; }", resolved)

        for index in range(count):
            moment = index / rate
            png: Optional[bytes] = None
            frame_key: Optional[str] = None
            if cache.enabled:
                frame_key = cache.key(
                    template["dir"],
                    resolved,
                    fps=rate,
                    size=(width, height),
                    engine=engine,
                    t=moment,
                )
                png = cache.read(frame_key)
            if png is None:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    raise MotionError(
                        "CAPTURE_TIMEOUT",
                        f"frame {index} not reached within {capture_limit}s "
                        f"(whole-capture ceiling)",
                        frame=index,
                        timeout_s=capture_limit,
                        frames_done=len(paths),
                    )
                budget_ms = int(min(frame_limit, remaining) * 1000)
                try:
                    outcome = page.evaluate(
                        _SEEK_JS,
                        {"path": seek_path, "t": moment, "timeoutMs": budget_ms},
                    )
                except Exception as exc:  # noqa: BLE001 - declared, not swallowed
                    raise MotionError(
                        "SEEK_FAILED",
                        f"frame {index} at t={moment:.4f}s: the browser refused the seek: {exc}",
                        frame=index,
                        time=moment,
                    ) from exc
                status = (outcome or {}).get("status")
                if status == "SEEK_MISSING":
                    raise MotionError(
                        "SEEK_MISSING",
                        f"template {template['name']!r} does not expose {seek_js}()",
                        template=template["name"],
                        frame=index,
                    )
                if status == "SEEK_ERROR":
                    raise MotionError(
                        "SEEK_FAILED",
                        f"frame {index} at t={moment:.4f}s: {outcome.get('message')}",
                        frame=index,
                        time=moment,
                    )
                if status == "TIMEOUT":
                    code = (
                        "CAPTURE_TIMEOUT"
                        if remaining < frame_limit
                        else "FRAME_TIMEOUT"
                    )
                    raise MotionError(
                        code,
                        f"frame {index} at t={moment:.4f}s did not paint within "
                        f"{budget_ms / 1000.0:.2f}s",
                        frame=index,
                        time=moment,
                        timeout_s=budget_ms / 1000.0,
                    )
                png = page.screenshot(omit_background=True, type="png")
                if frame_key is not None:
                    try:
                        cache.put(frame_key, png)
                    except motion_cache.CacheRefused as exc:
                        degraded.append(f"frame {index}: {exc}")
                    except (OSError, VxError) as exc:
                        degraded.append(f"frame {index}: cache write failed: {exc}")
            png = _as_rgba_png(png)
            path = destination / f"frame_{index:05d}.png"
            path.write_bytes(png)
            paths.append(str(path))
            digests.append(hashlib.sha256(png).hexdigest())
    finally:
        _close_quietly(page, context, browser, pw)
        if cache.enabled:
            try:
                cache.register()
            except (OSError, VxError) as exc:
                degraded.append(f"vault registration failed: {exc}")

    elapsed = time.perf_counter() - started
    cache_report = cache.report()
    cache_report["degraded"] = list(degraded)
    return {
        "template": template["name"],
        "engine": engine,
        "engine_version": engine_version,
        "frames": paths,
        "frame_count": len(paths),
        "fps": rate,
        "size": [width, height],
        "duration": duration,
        "params": resolved,
        "blocked_requests": blocked,
        "elapsed_s": round(elapsed, 3),
        "seconds_per_frame": round(elapsed / max(1, len(paths)), 4),
        "frame_sha256": digests,
        "frame_timeout_s": frame_limit,
        "capture_timeout_s": capture_limit,
        "cache": cache_report,
    }


def _probe_video(path: Path, *, decoder: Optional[str] = None) -> Dict[str, Any]:
    argv: List[str] = []
    if decoder:
        argv += ["-c:v", decoder]
    argv += [
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,pix_fmt:stream_tags=alpha_mode",
        "-of",
        "json",
        str(path),
    ]
    proc = ff.run_fp(argv)
    if not proc.ok:
        raise MotionError(
            "PROBE_FAILED",
            f"ffprobe could not read {path.name}: {proc.tail(4)}",
            path=str(path),
        )
    try:
        streams = json.loads(proc.stdout or "{}").get("streams") or []
    except json.JSONDecodeError as exc:
        raise MotionError(
            "PROBE_FAILED",
            f"ffprobe emitted non-JSON for {path.name}: {exc}",
            path=str(path),
        ) from exc
    if not streams:
        raise MotionError(
            "PROBE_FAILED", f"no video stream in {path.name}", path=str(path)
        )
    stream = streams[0]
    tags = stream.get("tags") or {}
    return {
        "codec": stream.get("codec_name"),
        "pix_fmt": stream.get("pix_fmt"),
        "alpha_mode": tags.get("alpha_mode"),
    }


def to_video(
    frames_dir: str | Path,
    out: str | Path,
    *,
    fps: float,
    alpha: bool = False,
    crf: Optional[int] = None,
) -> Dict[str, Any]:
    """Encode the PNG sequence. Alpha keeps VP9/WebM, otherwise H.264/MP4."""
    source = Path(frames_dir)
    destination = Path(out)
    frames = sorted(source.glob("frame_*.png"))
    if not frames:
        raise MotionError(
            "FRAMES_MISSING", f"no frame_*.png in {source}", frames_dir=str(source)
        )
    if float(fps) <= 0:
        raise MotionError("PARAM_RANGE", f"fps must be > 0, got {fps}", param="fps")
    destination.parent.mkdir(parents=True, exist_ok=True)

    argv: List[str] = [
        ff.ffmpeg(),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        str(fps),
        "-i",
        str(source / "frame_%05d.png"),
    ]
    if alpha:
        argv += [
            "-c:v",
            "libvpx-vp9",
            "-pix_fmt",
            "yuva420p",
            "-auto-alt-ref",
            "0",
            "-b:v",
            "0",
            "-crf",
            str(24 if crf is None else crf),
        ]
    else:
        argv += [
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            str(18 if crf is None else crf),
            "-preset",
            "medium",
            "-movflags",
            "+faststart",
        ]
    argv.append(str(destination))

    started = time.perf_counter()
    proc = ff.run(argv, step="render")
    if not proc.ok:
        raise MotionError(
            "ENCODE_FAILED",
            f"ffmpeg refused {destination.name}: {proc.tail(6)}",
            path=str(destination),
            encoder="libvpx-vp9" if alpha else "libx264",
        )
    info = _probe_video(destination)
    if alpha:
        # WebM carries the VP9 alpha as a side layer, and the native parser
        # reports the base stream (yuv420p) plus `alpha_mode=1`. Asking ffprobe
        # for the libvpx decoder, which honours the layer, resolves yuva420p.
        layered = _probe_video(destination, decoder="libvpx-vp9")
        info["container_pix_fmt"] = info.get("pix_fmt")
        info["codec"] = layered.get("codec") or info.get("codec")
        info["pix_fmt"] = layered.get("pix_fmt")
    info.update(
        {
            "path": str(destination),
            "frames": len(frames),
            "fps": float(fps),
            "alpha_requested": bool(alpha),
            "alpha_detected": bool(
                info.get("pix_fmt") == "yuva420p" or info.get("alpha_mode") == "1"
            ),
            "requested_pix_fmt": "yuva420p" if alpha else "yuv420p",
            "elapsed_s": round(time.perf_counter() - started, 3),
        }
    )
    return info
