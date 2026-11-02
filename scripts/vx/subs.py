"""vx.subs — speech to dynamic subtitles.

faster-whisper word timestamps -> cue segmentation -> ASS (karaoke / clean)
+ SRT export + font verification + burn-in filter string.

Design notes (verified on this machine, ffmpeg 8.1 + libass, CPU int8):

* ``faster_whisper.Word`` carries ``start, end, word, probability``; ``word``
  usually has a leading space, so it is stripped before it enters
  ``Transcript.words``.
* Cue text never exceeds ``max_chars`` (18 vertical / 42 horizontal by
  default).  ``max_lines`` is a hard cap on the wrapped line count; with the
  default 18-char budget a cue is always a single rendered line.
* ``\\k`` values are centiseconds and are made to sum *exactly* to the cue
  duration: the residual is pushed onto the last syllable (or pulled back
  from the tail when the residual is negative).
* libass fails silently on a missing font, so :func:`verify_fonts` is the
  mandatory gate before burning.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import ffmpeg as ff
from . import fonts as fonts_mod
from .contract import (
    EXIT_MISSING_DEP,
    EXIT_OK,
    EXIT_RENDER_FAIL,
    PLATFORM_INSETS,
    THRESHOLDS,
    Cue,
    Transcript,
    safe_rect,
)
from .winpath import ass_filter_path

__all__ = [
    "transcribe",
    "build_cues",
    "write_ass",
    "write_srt",
    "verify_fonts",
    "burn_filter",
    "transcript_stats",
    "transcript_quality",
    "measure_bbox",
    "declared_box",
    "wrap_text",
    "write_hook_ass",
    "main",
]

DEFAULT_MODEL = "small"
DEFAULT_FONT = "Antonio"

# Tipografia cinetica. Antonio (OFL) e' condensed: 0.387 em/carattere misurato sul
# set di cue reale, 0.394 in maiuscolo. 0.42 e' il limite prudente usato per il box
# dichiarato, cosi' il cancello 4 non protegge un ingombro ottimistico.
#
# BorderStyle 1: il testo ha un contorno vero, non una placca. I valori sotto sono
# quelli misurati a 1080x1920 (references/04-specifiche-social.md:85): outline 7,
# shadow 4 sul verticale; 4 e 2 sull'orizzontale a 1080 di altezza. In `write_ass`
# vengono riscalati su `out_h`, cosi' la proporzione resta la stessa a ogni
# risoluzione invece di essere un numero magico legato a un solo output.
BORDER_STYLE_VERTICAL = 1
BOX_PADDING_VERTICAL = 0
BACK_COLOUR = "&H80000000"

# Animazione: dissolvenza morbida ai due capi e pop-in dal 95% al 100%. Il pop
# parte piu' piccolo e finisce esattamente al 100%, quindi non supera mai il box
# dichiarato: il cancello 4 resta valido durante tutto l'animazione.
FADE_MS = 90
POP_MS = 180
POP_FROM = 95
ANIMATE = True

# Rosso del beat: &HBBGGRR, quindi &H0000FF& = rosso pieno.
BEAT_COLOUR = "&H0000FF&"
BEAT_FLASH_MS = 70
BEAT_MAX_PER_CUE = 4

# Valori di riferimento alla risoluzione documentata; `_scaled` li porta a out_h.
OUTLINE_VERTICAL = 7
SHADOW_VERTICAL = 4
OUTLINE_HORIZONTAL = 4
SHADOW_HORIZONTAL = 2

# Budget di caratteri per riga: con 2 righe e 960 px utili, 30 caratteri
# permettono un corpo piu' grande di una riga sola. Misurato: la cue piu' lunga e'
# 44 caratteri.
WRAP_CHARS_VERTICAL = 30
MAX_LINES_VERTICAL = 2


GAP_BREAK = 0.45  # seconds of silence that closes a cue
MIN_CUE = 0.4  # never emit a shorter cue; extend the end instead
SENTENCE_END = ".!?…"
_TRAILING = "\"'”’»)]}"

# Arial Black uppercase advance is ~0.484 em/char (measured: "QUESTO È UN
# TEST" @119px renders 935 px wide including outline).  0.50 is a safe upper
# bound that keeps the longest cue clear of the frame edge.
_CHAR_EM = 0.34

FILLERS = frozenset(
    {
        "ehm",
        "eh",
        "uh",
        "um",
        "mmm",
        "mm",
        "ah",
        "ahm",
        "hmm",
        "cioe",
        "cioè",
        "tipo",
        "praticamente",
        "insomma",
        "diciamo",
        "ecco",
        "beh",
        "boh",
        "mah",
        "ok",
        "okay",
    }
)

_FONTSELECT = re.compile(
    r"fontselect:\s*\(([^,]+),\s*([^,]+),\s*([^)]+)\)\s*->\s*([^,]+)"
)
_DIALOGUE = re.compile(r"^Dialogue:\s*\d+,(\d+):(\d\d):(\d\d)\.(\d\d),")
_PLAYRES = re.compile(r"^PlayRes([XY]):\s*(\d+)", re.MULTILINE)


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _norm_family(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _norm_word(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower())


def _ass_time(t: float) -> str:
    cs = max(0, int(round(float(t) * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _srt_time(t: float) -> str:
    ms = max(0, int(round(float(t) * 1000)))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_escape(text: str) -> str:
    """Neutralise the three characters libass treats as markup."""
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def _wrap_count(text: str, width: int) -> int:
    """Number of rendered lines when greedily wrapped at `width` chars."""
    if not text:
        return 1
    lines, cur = 1, 0
    for word in text.split():
        wl = len(word)
        if cur == 0:
            cur = wl
        elif cur + 1 + wl <= width:
            cur += 1 + wl
        else:
            lines += 1
            cur = wl
    return lines


def _ends_sentence(word: str) -> bool:
    stripped = word.rstrip(_TRAILING)
    return bool(stripped) and stripped[-1] in SENTENCE_END


def _limits(
    aspect: str,
    max_chars: Optional[int],
    max_lines: Optional[int],
    max_cps: Optional[float],
) -> tuple[int, int, float]:
    vertical = aspect not in ("16:9", "4:3")
    chars = (
        max_chars
        if max_chars is not None
        else (
            THRESHOLDS["sub_max_chars_916"]
            if vertical
            else THRESHOLDS["sub_max_chars_169"]
        )
    )
    lines = max_lines if max_lines is not None else THRESHOLDS["sub_max_lines"]
    cps = max_cps if max_cps is not None else THRESHOLDS["sub_max_cps"]
    return max(1, int(chars)), max(1, int(lines)), float(cps)


def _scaled(value: int, out_h: int, ref_h: int) -> int:
    """Reference px at `ref_h` -> px at `out_h`, never below 1."""
    return max(1, int(round(float(value) * float(out_h) / float(ref_h))))


# --------------------------------------------------------------------------- #
# 1. transcribe
# --------------------------------------------------------------------------- #


def _device_choice(device: Optional[str], compute_type: Optional[str]) -> dict:
    """Explicit arguments win; otherwise ask the accel probe (verified by execution)."""
    if device and compute_type:
        return {"device": device, "compute_type": compute_type}
    try:
        from . import accel

        target = accel.whisper_target(preferred=device or "")
        if compute_type:
            target = dict(target, compute_type=compute_type)
        return target
    except Exception:  # noqa: BLE001 - never block transcription on the probe
        return {"device": "cpu", "compute_type": "int8"}


def transcribe(
    path,
    *,
    model: str = DEFAULT_MODEL,
    language: Optional[str] = None,
    vad: bool = True,
    max_duration: Optional[float] = None,
    device: Optional[str] = None,
    compute_type: Optional[str] = None,
) -> Transcript:
    """Run faster-whisper on `path` and normalise the words.

    Never raises: a missing package, a missing file or a decoder failure all
    come back as a ``Transcript`` with ``skipped_reason`` set.
    """
    tr = Transcript(model=model, device="cpu")
    src = Path(path)
    if not src.exists():
        tr.skipped_reason = f"input not found: {src}"
        return tr

    try:  # guard: the toolkit must stay importable without faster-whisper
        from faster_whisper import WhisperModel  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on the venv
        tr.skipped_reason = f"faster-whisper unavailable: {exc}"
        return tr

    try:
        target = _device_choice(device, compute_type)
        tr.device = target["device"]
        engine = WhisperModel(
            model, device=target["device"], compute_type=target["compute_type"]
        )
        segments, info = engine.transcribe(
            str(src),
            language=language,
            beam_size=5,
            word_timestamps=True,
            vad_filter=bool(vad),
            condition_on_previous_text=False,
        )

        words: List[Dict[str, Any]] = []
        segs: List[Dict[str, Any]] = []
        texts: List[str] = []
        prev_start = 0.0
        for seg in segments:  # generator: must be consumed here
            seg_start = float(seg.start or 0.0)
            seg_end = float(seg.end or 0.0)
            seg_text = (seg.text or "").strip()
            if max_duration is not None and seg_start >= max_duration:
                continue
            if max_duration is not None:
                seg_end = min(seg_end, float(max_duration))
            segs.append(
                {
                    "start": round(seg_start, 3),
                    "end": round(seg_end, 3),
                    "text": seg_text,
                    "avg_logprob": round(
                        float(getattr(seg, "avg_logprob", 0.0) or 0.0), 4
                    ),
                    "no_speech_prob": round(
                        float(getattr(seg, "no_speech_prob", 0.0) or 0.0), 4
                    ),
                }
            )
            if seg_text:
                texts.append(seg_text)
            for w in getattr(seg, "words", None) or []:
                token = (w.word or "").strip()
                if not token:
                    continue
                start = float(w.start or 0.0)
                end = float(w.end or start)
                if max_duration is not None:
                    if start >= max_duration:
                        continue
                    end = min(end, float(max_duration))
                # keep the timeline monotonic even if the model wobbles
                start = max(start, prev_start)
                end = max(end, start)
                prev_start = start
                words.append(
                    {
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "word": token,
                        "probability": round(float(w.probability or 0.0), 4),
                    }
                )

        duration = float(getattr(info, "duration", 0.0) or 0.0)
        if max_duration is not None:
            duration = min(duration, float(max_duration))
        if not duration and words:
            duration = words[-1]["end"]

        tr.language = str(getattr(info, "language", "") or language or "")
        tr.words = words
        tr.segments = segs
        tr.text = " ".join(texts)
        tr.duration = round(duration, 3)
        tr.wpm = round(len(words) / (duration / 60.0), 1) if duration > 0 else 0.0
        if not words:
            tr.skipped_reason = "no speech detected"
    except Exception as exc:  # pragma: no cover - model/decoder failures
        tr.skipped_reason = f"transcription failed: {exc}"
    return tr


# --------------------------------------------------------------------------- #
# 2. cue segmentation
# --------------------------------------------------------------------------- #


def _make_cue(words: Sequence[Dict[str, Any]], style: str, max_chars: int) -> Cue:
    text = " ".join(w["word"] for w in words)
    return Cue(
        start=float(words[0]["start"]),
        end=float(words[-1]["end"]),
        text=text,
        words=[dict(w) for w in words],
        style=style,
        line_count=_wrap_count(text, max_chars),
    )


def _finalize(
    cues: List[Cue],
    max_chars: int,
    max_cps: float,
    max_lines: Optional[int] = None,
) -> List[Cue]:
    """Enforce min duration / cps without letting cues overlap.

    ``max_chars`` is the width of *one* line, not of the whole cue: two lines of
    18 characters hold 36. A cue that would need more time than the gap before
    the next cue is merged into it whenever the combined text still fits the
    declared lines; only then is it clamped to the next cue's start, and a second
    pass lends it the silence *before* it, which is where a fast phrase usually
    has room. Nothing is invented and nothing overlaps.
    """
    lines = max(1, int(max_lines or 1))
    out = list(cues)

    def fits(text: str) -> bool:
        return _wrap_count(text, max_chars) <= lines

    def needed(cue: Cue) -> float:
        need = MIN_CUE
        if max_cps > 0 and cue.text:
            need = max(need, len(cue.text) / max_cps)
        return need

    i = 0
    while i < len(out):
        cue = out[i]
        target = cue.start + needed(cue)
        limit = out[i + 1].start if i + 1 < len(out) else None

        if limit is None:
            cue.end = max(cue.end, target)
        elif target > limit:
            nxt = out[i + 1]
            merged = f"{cue.text} {nxt.text}"
            if fits(merged):
                cue.text = merged
                cue.words = cue.words + nxt.words
                cue.end = nxt.end
                cue.line_count = _wrap_count(merged, max_chars)
                out.pop(i + 1)
                continue  # re-evaluate the merged cue
            cue.end = limit
        else:
            cue.end = min(max(cue.end, target), limit)
        i += 1

    # A cue still short of its need borrows the silence before it: the line
    # appears a moment before the first word is spoken, which is how a fast
    # phrase becomes readable without overlap and without inventing words.
    for index, cue in enumerate(out):
        need = needed(cue)
        if cue.end - cue.start >= need - 1e-9:
            continue
        floor = out[index - 1].end if index else 0.0
        cue.start = max(floor, cue.end - need)

    for cue in out:
        cue.start = round(cue.start, 3)
        cue.end = round(cue.end, 3)
    return out


def build_cues(
    tr: Transcript,
    *,
    style: str = "karaoke",
    aspect: str = "9:16",
    max_chars: Optional[int] = None,
    max_lines: Optional[int] = None,
    max_cps: Optional[float] = None,
) -> List[Cue]:
    """Segment word timestamps into subtitle cues."""
    chars, lines, cps = _limits(aspect, max_chars, max_lines, max_cps)
    words = [w for w in (tr.words or []) if (w.get("word") or "").strip()]
    if not words:
        return []

    raw: List[Cue] = []
    cur: List[Dict[str, Any]] = []
    for w in words:
        if cur:
            gap = float(w["start"]) - float(cur[-1]["end"])
            cand = cur + [w]
            text = " ".join(x["word"] for x in cand)
            dur = max(float(cand[-1]["end"]) - float(cand[0]["start"]), 1e-6)
            too_long = len(text) > chars
            too_many_lines = _wrap_count(text, chars) > lines
            # a cps break only makes sense once the cue has a real phrase in
            # it; splitting a single word cannot lower its own cps
            too_fast = len(cand) >= 2 and (len(text) / dur) > cps
            if gap > GAP_BREAK or too_long or too_many_lines or too_fast:
                raw.append(_make_cue(cur, style, chars))
                cur = []
        cur.append(w)
        if _ends_sentence(w["word"]):
            raw.append(_make_cue(cur, style, chars))
            cur = []
    if cur:
        raw.append(_make_cue(cur, style, chars))

    return _finalize(raw, chars, cps, lines)


# --------------------------------------------------------------------------- #
# 3. ASS writer
# --------------------------------------------------------------------------- #


def _karaoke_body(cue: Cue) -> str:
    """`{\\kNN}WORD` per word, centiseconds summing exactly to the cue."""
    words = cue.words or [{"start": cue.start, "end": cue.end, "word": cue.text}]
    starts = [float(w.get("start", cue.start)) for w in words]
    total_cs = max(1, int(round((cue.end - cue.start) * 100)))

    # A cue can begin before its first word (the line is shown, then the word
    # lights up); that lead-in is its own silent `\k` so the highlight still
    # lands on the spoken word instead of stretching it.
    lead_cs = max(0, min(total_cs - 1, int(round((starts[0] - cue.start) * 100))))
    ks: List[int] = []
    for i in range(len(words)):
        end_i = starts[i + 1] if i + 1 < len(words) else cue.end
        ks.append(max(0, int(round((end_i - starts[i]) * 100))))

    diff = total_cs - lead_cs - sum(ks)
    if diff > 0:
        ks[-1] += diff
    elif diff < 0:
        need = -diff
        for j in range(len(ks) - 1, -1, -1):
            take = min(ks[j], need)
            ks[j] -= take
            need -= take
            if not need:
                break

    body = " ".join(
        "{\\k%d}%s" % (k, _ass_escape(str(w.get("word", "")).upper()))
        for w, k in zip(words, ks)
    )
    return ("{\\k%d}" % lead_cs + body) if lead_cs else body


def _clean_body(cue: Cue) -> str:
    return _ass_escape(cue.text)


def wrap_text(
    text: str, width: int, *, max_lines: int = MAX_LINES_VERTICAL
) -> List[str]:
    """Greedy word wrap, then rebalanced so a two-line cue has no orphan word.

    `width` is characters, not pixels: the caller has already chosen the font so
    that `width` characters fit the safe box. Greedy alone produced
    "Lo racchiudo per intero con il / palmo"; the rebalance turns that into
    "Lo racchiudo per intero / con il palmo", which is what a caption looks like.
    """
    words = str(text).split()
    if not words:
        return []
    width = max(1, int(width))
    lines: List[str] = []
    cur = ""
    for word in words:
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= width or len(lines) >= max_lines - 1:
            cur = f"{cur} {word}"
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)

    if len(lines) == 2 and len(words) > 2:
        best: Optional[List[str]] = None
        best_cost: Optional[tuple[int, int]] = None
        for k in range(1, len(words)):
            first = " ".join(words[:k])
            second = " ".join(words[k:])
            if len(first) > width or len(second) > width:
                continue
            cost = (max(len(first), len(second)), abs(len(first) - len(second)))
            if best_cost is None or cost < best_cost:
                best_cost, best = cost, [first, second]
        if best is not None:
            return best
    return lines


def write_ass(
    cues: List[Cue],
    dst,
    *,
    aspect: str = "9:16",
    platform: str = "tiktok",
    style: str = "karaoke",
    font: str = DEFAULT_FONT,
    out_w: int = 1080,
    out_h: int = 1920,
    bottom_limit_px: Optional[int] = None,
    wrap_chars: Optional[int] = None,
    outline: Optional[int] = None,
    shadow: Optional[int] = None,
    margin_lr: Optional[int] = None,
    border_style: int = BORDER_STYLE_VERTICAL,
    back_colour: str = BACK_COLOUR,
    animate: bool = ANIMATE,
    beats: Optional[Sequence[float]] = None,
    margin_v: Optional[int] = None,
    font_family: Optional[str] = None,
) -> str:
    """Write an ASS file and return its absolute path (plain UTF-8, no BOM).

    `bottom_limit_px` is the highest Y (exclusive) the text block may reach. It is
    what `vx.layout` computes from the graphic overlays, and it overrides the
    platform-inset heuristic entirely: a measured limit beats a generic one.

    `beats` are absolute times (seconds). Every beat falling inside a cue produces a
    short colour flash on the text, so the caption pulses with the metric instead of
    being a static plate. Off by default: it is a look, not a rule.

    `margin_v` replaces the computed bottom margin; the bridge uses it to anchor the
    captions to the canvas instead of to the platform safe zone.

    `font_family` is the ASS `Fontname`: when given it replaces `font` verbatim, so
    the renderer can burn the family `typography.apply` actually copied into the
    job's fonts dir. When `None`, `font` is used exactly as before.
    """
    target = Path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    margin_override = margin_v

    vertical = aspect not in ("16:9", "4:3")
    if vertical:
        base = int(round(0.070 * out_h))
        default_outline = (
            BOX_PADDING_VERTICAL
            if border_style == 3
            else _scaled(OUTLINE_VERTICAL, out_h, 1920)
        )
        outline = default_outline if outline is None else int(outline)
        shadow = (
            _scaled(SHADOW_VERTICAL, out_h, 1920) if shadow is None else int(shadow)
        )
        chars = WRAP_CHARS_VERTICAL if wrap_chars is None else int(wrap_chars)
        insets = PLATFORM_INSETS.get(platform, (0, 0, 0, 0))
        scale = out_h / 1920.0
        margin_v = int(
            round(
                max(
                    out_h - safe_rect(aspect)[3] + 12,
                    insets[1] * scale + 12,
                )
            )
        )
    else:
        base = int(round(0.045 * out_h))
        outline = (
            _scaled(OUTLINE_HORIZONTAL, out_h, 1080)
            if outline is None
            else int(outline)
        )
        shadow = (
            _scaled(SHADOW_HORIZONTAL, out_h, 1080) if shadow is None else int(shadow)
        )
        chars = 42 if wrap_chars is None else int(wrap_chars)
        margin_v = 40

    if style == "clean" and border_style == 1 and int(outline) > 0:
        outline = max(0, int(outline) - 1)
        shadow = max(0, int(shadow) - 1)

    if bottom_limit_px is not None:
        # Con la placca (BorderStyle 3) MarginV àncora il glifo, ma lo sfondo
        # dipinto si estende di `outline` px oltre: senza compensare, l'inchiostro
        # visivo sfonda il limite pur con il testo dentro (misurato: 1212 vs 1207).
        plate = int(outline) if int(border_style) == 3 else 0
        margin_v = max(0, int(out_h) - int(bottom_limit_px) + plate)

    if margin_override is not None:
        margin_v = max(0, int(margin_override))

    margin_lr = 60 if margin_lr is None else int(margin_lr)
    wrapped: List[List[str]] = [wrap_text(c.text, chars) for c in cues]
    longest = max(
        (max((len(x) for x in lines), default=1) for lines in wrapped), default=1
    )
    avail = max(64, out_w - 2 * margin_lr)
    size = max(24, min(base, int(avail / (max(1, longest) * _CHAR_EM))))

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {out_w}\n"
        f"PlayResY: {out_h}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Vx,{font_family or font},{size},&H00FFFFFF,&H0000D7FF,&H00000000,"
        f"{back_colour},-1,0,0,0,100,100,0,0,{border_style},{outline},{shadow},2,"
        f"{margin_lr},{margin_lr},{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )

    pop = (
        (
            f"{{\\fad({FADE_MS},{FADE_MS})"
            f"\\fscx{POP_FROM}\\fscy{POP_FROM}"
            f"\\t(0,{POP_MS},\\fscx100\\fscy100)}}"
        )
        if animate
        else ""
    )

    def beat_flashes(cue: Cue) -> str:
        """Colour flashes on the beats that land inside this cue.

        Times are relative to the event start, which is what `\\t` expects. The cap
        keeps the override block short and stops a fast passage from strobing.
        """
        if not beats:
            return ""
        hits = [
            b - cue.start
            for b in beats
            if 0.0 <= (b - cue.start) < (cue.end - cue.start)
        ]
        out = []
        for offset in hits[:BEAT_MAX_PER_CUE]:
            a = int(offset * 1000)
            b = a + BEAT_FLASH_MS
            out.append(f"\\t({a},{b},\\1c{BEAT_COLOUR})")
            out.append(f"\\t({b},{b + BEAT_FLASH_MS},\\1c&HFFFFFF&)")
        return "".join(out)

    lines: List[str] = []
    for cue, chunks in zip(cues, wrapped):
        if cue.end <= cue.start:
            continue
        prefix = pop + (f"{{{beat_flashes(cue)}}}" if beats else "")
        if style == "karaoke":
            body = prefix + _karaoke_body(cue)
        elif len(chunks) > 1:
            body = prefix + "\\N".join(_ass_escape(chunk) for chunk in chunks)
        else:
            body = prefix + _clean_body(cue)
        lines.append(
            f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},Vx,,0,0,0,,{body}"
        )

    text = header + "\n".join(lines) + ("\n" if lines else "")
    target.write_text(text, encoding="utf-8", newline="\n")
    return str(target.resolve())


# --------------------------------------------------------------------------- #
# 3b. hook card — one event, no karaoke
# --------------------------------------------------------------------------- #


def write_hook_ass(
    path,
    text,
    *,
    out_w,
    out_h,
    start_s=0.0,
    duration_s=3.0,
    style="hook",
    font=None,
) -> Path:
    """One ASS event for the hook card: uppercase, no karaoke, one Dialogue line."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    body = " ".join(str(text).split()).upper()
    family = font or DEFAULT_FONT
    margin_lr = 60
    margin_v = 40
    base = int(round(0.045 * out_h))
    avail = max(64, int(out_w) - 2 * margin_lr)
    size = max(24, min(base, int(avail / (max(1, len(body)) * _CHAR_EM))))
    outline = _scaled(OUTLINE_HORIZONTAL, out_h, 1080)
    shadow = _scaled(SHADOW_HORIZONTAL, out_h, 1080)

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {out_w}\n"
        f"PlayResY: {out_h}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: {style},{family},{size},&H00FFFFFF,&H0000D7FF,&H00000000,"
        f"{BACK_COLOUR},-1,0,0,0,100,100,0,0,{BORDER_STYLE_VERTICAL},"
        f"{outline},{shadow},2,{margin_lr},{margin_lr},{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )
    start = float(start_s)
    end = start + float(duration_s)
    event = (
        f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,"
        f"{_ass_escape(body)}\n"
    )
    target.write_text(header + event, encoding="utf-8", newline="\n")
    return target.resolve()


# --------------------------------------------------------------------------- #
# 4. SRT writer
# --------------------------------------------------------------------------- #


def write_srt(cues: List[Cue], dst) -> str:
    target = Path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    blocks: List[str] = []
    for i, cue in enumerate(cues, 1):
        body = cue.text.replace("\\N", "\n").strip()
        blocks.append(f"{i}\n{_srt_time(cue.start)} --> {_srt_time(cue.end)}\n{body}\n")
    target.write_text("\n".join(blocks), encoding="utf-8", newline="\n")
    return str(target.resolve())


# --------------------------------------------------------------------------- #
# 5. font verification + burn filter
# --------------------------------------------------------------------------- #


def burn_filter(ass_path) -> str:
    """`ass='D\\:/work/a.ass':fontsdir='.../assets/fonts'`.

    The bundled font directory is part of the filter on purpose: libass prefers
    a font it can load from `fontsdir` over a system substitute, so the render
    is the same on a clean machine as on this one.
    """
    return "ass=" + ass_filter_path(ass_path) + ":fontsdir=" + fonts_mod.fontsdir_arg()


def _playres(ass_text: str) -> tuple[int, int]:
    found = {k: int(v) for k, v in _PLAYRES.findall(ass_text)}
    return found.get("X", 1080), found.get("Y", 1920)


def _first_dialogue_start(ass_text: str) -> float:
    for line in ass_text.splitlines():
        m = _DIALOGUE.match(line.strip())
        if m:
            h, mi, s, cs = (int(g) for g in m.groups())
            return h * 3600 + mi * 60 + s + cs / 100.0
    return 0.0


def _declared_style(ass_text: str) -> tuple[str, int]:
    for line in ass_text.splitlines():
        if not line.startswith("Style:"):
            continue
        fields = line.split(",")
        if len(fields) < 3:
            continue
        name = fields[0].split(":", 1)[1].strip()
        try:
            return name, int(fields[2])
        except ValueError:
            return name, 0
    return "", 0


def _resolved_style_size(log: str, style_name: str) -> int:
    """Font size libass reports for `style_name` in its verbose `Style:` dump."""
    if not style_name:
        return 0
    pattern = re.compile(r"Style:\s*" + re.escape(style_name) + r",([^\n]*)")
    for match in pattern.finditer(log):
        fields = match.group(1).split(",")
        if len(fields) < 2:
            continue
        try:
            return int(fields[1])
        except ValueError:
            continue
    return 0


def verify_fonts(ass_path, *, log_path=None) -> Dict[str, Any]:
    """Burn one frame and read libass' `fontselect:` decisions.

    libass substitutes a missing family silently, so this is the only way to
    know the render will use the font we asked for.  The same log carries the
    `Style:` line libass parsed: if its font size differs from the one the ASS
    declares, the style was not understood and the frame is not trustworthy.
    """
    src = Path(ass_path)
    result: Dict[str, Any] = {
        "fallback_count": 0,
        "fonts_used": [],
        "ok": False,
    }
    if not src.exists():
        result["error"] = f"missing ass: {src}"
        return result

    ass_text = src.read_text(encoding="utf-8", errors="replace")
    w, h = _playres(ass_text)
    start = _first_dialogue_start(ass_text)
    style_name, declared_size = _declared_style(ass_text)

    args = [
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={w}x{h}:r=25:d={start + 1.5:.2f}",
        "-vf",
        burn_filter(src),
        "-ss",
        f"{start + 0.05:.3f}",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    proc = ff.run_ff(args, loglevel="verbose")
    log = proc.stderr or ""
    if log_path:
        Path(log_path).write_text(log, encoding="utf-8", newline="\n")

    requested: List[str] = []
    chosen: List[str] = []
    fallback = 0
    for line in log.splitlines():
        m = _FONTSELECT.search(line)
        if not m:
            continue
        req = m.group(1).strip()
        got = m.group(4).strip()
        requested.append(req)
        chosen.append(got)
        if _norm_family(req) not in _norm_family(got):
            fallback += 1

    hard_error = any(
        marker in log
        for marker in ("Could not open", "Error initializing", "No such file")
    )
    resolved_size = _resolved_style_size(log, style_name)
    size_ok = bool(declared_size > 0 and resolved_size == declared_size)
    result["fallback_count"] = fallback
    result["fonts_used"] = sorted(set(chosen))
    result["requested"] = sorted(set(requested))
    result["declared_size"] = declared_size
    result["resolved_size"] = resolved_size
    result["size_ok"] = size_ok
    result["rc"] = proc.rc
    result["ok"] = bool(
        proc.rc == 0
        and fallback == 0
        and not hard_error
        and len(chosen) > 0
        and size_ok
    )
    if not chosen:
        result["error"] = (
            "no fontselect line in the log: the frame carried no text, so the "
            "font was never exercised"
        )
    elif not size_ok:
        result["error"] = (
            f"font size mismatch: ass declares {declared_size}, libass resolved "
            f"{resolved_size}"
        )
    return result


# --------------------------------------------------------------------------- #
# 5b. measured ink box — the only proof that the typography fits
# --------------------------------------------------------------------------- #

_INK = re.compile(
    r"^Dialogue:\s*\d+,(\d+):(\d\d):(\d\d)\.(\d\d),(\d+):(\d\d):(\d\d)\.(\d\d),"
)


def measure_bbox(
    ass_path,
    *,
    workdir=None,
    threshold: int = 24,
    outline_px: int = OUTLINE_VERTICAL,
    shadow_px: int = SHADOW_VERTICAL,
) -> Dict[str, Any]:
    """Render every cue on black and measure the real ink box of the text.

    This is the ground truth the layout gate wants: the declared box in
    `vx.layout` is arithmetic, this is the pixels libass actually produced. A
    missing font, a wrong PlayRes or an unsupported tag all show up here.
    """
    src = Path(ass_path)
    out: Dict[str, Any] = {"ok": False, "cues": [], "bbox": None, "max_bottom": 0}
    if not src.exists():
        out["error"] = f"missing ass: {src}"
        return out
    try:
        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"opencv/numpy unavailable: {exc}"
        return out

    text = src.read_text(encoding="utf-8", errors="replace")
    w, h = _playres(text)
    work = Path(workdir) if workdir else src.parent / "_bbox"
    work.mkdir(parents=True, exist_ok=True)

    spans: List[tuple[float, float]] = []
    for line in text.splitlines():
        m = _INK.match(line.strip())
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 100.0
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 100.0
        if end > start:
            spans.append((start, end))
    if not spans:
        out["error"] = "no Dialogue lines"
        return out

    x1 = y1 = 10**9
    x2 = y2 = -1
    for i, (start, end) in enumerate(spans):
        at = start + 0.4 * (end - start)
        png = work / f"cue_{i:02d}.png"
        proc = ff.run_ff(
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s={w}x{h}:r=25:d={at + 1.5:.2f}",
                "-vf",
                burn_filter(src),
                "-ss",
                f"{at:.3f}",
                "-frames:v",
                "1",
                "-y",
                str(png),
            ],
            loglevel="error",
        )
        if not proc.ok or not png.exists():
            out["cues"].append(
                {"index": i, "start": round(start, 3), "error": proc.tail(3)}
            )
            continue
        img = cv2.imread(str(png))
        if img is None:
            continue
        gray = img.max(axis=2)
        ys, xs = np.where(gray > int(threshold))
        if not len(xs):
            out["cues"].append({"index": i, "start": round(start, 3), "empty": True})
            continue
        pad = int(outline_px) + int(shadow_px)
        box = {
            "x": int(xs.min()) - pad,
            "y": int(ys.min()) - pad,
            "w": int(xs.max() - xs.min() + 1) + 2 * pad,
            "h": int(ys.max() - ys.min() + 1) + 2 * pad,
        }
        out["cues"].append(
            {"index": i, "start": round(start, 3), "end": round(end, 3), "bbox": box}
        )
        x1, y1 = min(x1, box["x"]), min(y1, box["y"])
        x2, y2 = max(x2, box["x"] + box["w"]), max(y2, box["y"] + box["h"])

    if x2 > 0:
        out["bbox"] = {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}
        out["max_bottom"] = int(y2)
        out["max_right"] = int(x2)
        out["min_left"] = int(x1)
        out["ok"] = True
    return out


# --------------------------------------------------------------------------- #
# 5c. declared geometry — what the ASS promises, before anything is rendered
# --------------------------------------------------------------------------- #

_OVERRIDE = re.compile(r"\{[^}]*\}")


def declared_box(ass_path) -> Dict[str, Any]:
    """Read back the geometry the ASS declares.

    Consumers need this to run a layout gate without duplicating the style-line
    parsing: font size, outline/shadow, margins, and the widest rendered line
    measured in characters. `line_px` converts the character count to pixels with
    the same conservative advance the writer used, so the declared box is an upper
    bound and never an optimistic one.
    """
    src = Path(ass_path)
    text = src.read_text(encoding="utf-8", errors="replace")
    w, h = _playres(text)
    style = next((ln for ln in text.splitlines() if ln.startswith("Style:")), "")
    fields = style.split(",")
    font_px = int(fields[2]) if len(fields) > 2 else 0
    outline_px = int(fields[16]) if len(fields) > 16 else 0
    shadow_px = int(fields[17]) if len(fields) > 17 else 0
    margin_lr = int(fields[19]) if len(fields) > 19 else 0
    margin_v = int(fields[21]) if len(fields) > 21 else 0

    lines = 1
    widest = 0
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        body = line.split(",", 9)[-1]
        chunks = [
            _OVERRIDE.sub("", chunk).strip()
            for chunk in body.replace("\\N", "\n").split("\n")
        ]
        chunks = [c for c in chunks if c]
        lines = max(lines, len(chunks) or 1)
        widest = max(widest, max((len(c) for c in chunks), default=0))

    return {
        "playres": [w, h],
        "font_px": font_px,
        "outline_px": outline_px,
        "shadow_px": shadow_px,
        "margin_v": margin_v,
        "margin_lr": margin_lr,
        "lines": lines,
        "widest_chars": widest,
        "line_px": int(round(widest * font_px * _CHAR_EM)),
    }


# --------------------------------------------------------------------------- #
# 6. stats
# --------------------------------------------------------------------------- #


def transcript_quality(words) -> Dict[str, Any]:
    """Burn gate from references/12-lezioni-dal-campo.md:70-80.

    Fewer than 12 words or a mean probability below 0.55 means the transcript is
    not trustworthy enough to be burned into the picture.  A missing probability
    counts as 0, never as an optimistic default.
    """
    items = [w for w in (words or []) if (w.get("word") or "").strip()]
    count = len(items)
    probs = [float(w.get("probability") or 0.0) for w in items]
    mean = sum(probs) / len(probs) if probs else 0.0
    ok = count >= 12 and mean >= 0.55
    if count < 12:
        reason = f"{count} parole: sotto le 12 minime"
    elif mean < 0.55:
        reason = f"probabilita' media {mean:.2f}: sotto 0.55"
    else:
        reason = f"{count} parole / probabilita' media {mean:.2f}"
    return {
        "ok": ok,
        "word_count": count,
        "mean_probability": round(mean, 4),
        "reason": reason,
    }


def transcript_stats(tr: Transcript) -> Dict[str, Any]:
    words = [w for w in (tr.words or []) if (w.get("word") or "").strip()]
    count = len(words)
    duration = float(tr.duration or 0.0)
    if not duration and words:
        duration = float(words[-1]["end"])
    wpm = float(tr.wpm or 0.0)
    if not wpm and duration > 0:
        wpm = count / (duration / 60.0)

    fillers = sum(1 for w in words if _norm_word(w["word"]) in FILLERS)

    fastest = 0.0
    for cue in build_cues(tr):
        span = cue.end - cue.start
        if span > 0:
            fastest = max(fastest, len(cue.text) / span)

    return {
        "word_count": count,
        "wpm": round(wpm, 1),
        "fillers": fillers,
        "fastest_cue_wps": round(fastest, 2),
    }


# --------------------------------------------------------------------------- #
# 7. CLI smoke entry
# --------------------------------------------------------------------------- #


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="vx.subs", description="speech -> dynamic subtitles (ASS + SRT)"
    )
    ap.add_argument("--input", required=True, help="audio or video file")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--style", default="karaoke", choices=("karaoke", "clean"))
    ap.add_argument("--lang", default=None, help="ISO language, e.g. it")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--aspect", default="9:16")
    ap.add_argument("--platform", default="tiktok")
    ap.add_argument("--font", default=DEFAULT_FONT)
    ap.add_argument("--no-vad", action="store_true")
    ap.add_argument("--max-duration", type=float, default=None)
    ap.add_argument("--burn", default=None, help="optional video to burn into")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    tr = transcribe(
        args.input,
        model=args.model,
        language=args.lang,
        vad=not args.no_vad,
        max_duration=args.max_duration,
    )
    t_transcribe = time.perf_counter() - t0
    if tr.skipped_reason:
        print(json.dumps({"ok": False, "reason": tr.skipped_reason}, indent=2))
        return EXIT_MISSING_DEP

    t1 = time.perf_counter()
    cues = build_cues(tr, style=args.style, aspect=args.aspect)
    t_cues = time.perf_counter() - t1

    stem = Path(args.input).stem
    ass_path = write_ass(
        cues,
        out_dir / f"{stem}.ass",
        aspect=args.aspect,
        platform=args.platform,
        style=args.style,
        font=args.font,
    )
    srt_path = write_srt(cues, out_dir / f"{stem}.srt")

    t2 = time.perf_counter()
    fonts = verify_fonts(ass_path, log_path=out_dir / "fontselect.log")
    t_fonts = time.perf_counter() - t2

    summary: Dict[str, Any] = {
        "ok": True,
        "input": str(Path(args.input).resolve()),
        "ass": ass_path,
        "srt": srt_path,
        "style": args.style,
        "font": args.font,
        "cues": len(cues),
        "stats": transcript_stats(tr),
        "fonts": fonts,
        "timings_s": {
            "transcribe": round(t_transcribe, 2),
            "cues": round(t_cues, 3),
            "verify_fonts": round(t_fonts, 2),
        },
    }

    rc = EXIT_OK
    if args.burn:
        burned = out_dir / f"{stem}_subs.mp4"
        t3 = time.perf_counter()
        proc = ff.run_ff(
            [
                "-i",
                args.burn,
                "-vf",
                burn_filter(ass_path),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                str(burned),
            ]
        )
        summary["burn"] = {
            "path": str(burned),
            "rc": proc.rc,
            "seconds": round(time.perf_counter() - t3, 2),
        }
        if not proc.ok:
            summary["ok"] = False
            summary["burn"]["tail"] = proc.tail(12)
            rc = EXIT_RENDER_FAIL

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return rc


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
