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
    "main",
]

DEFAULT_MODEL = "small"
DEFAULT_FONT = "Arial Black"

GAP_BREAK = 0.45  # seconds of silence that closes a cue
MIN_CUE = 0.4  # never emit a shorter cue; extend the end instead
SENTENCE_END = ".!?…"
_TRAILING = "\"'”’»)]}"

# Arial Black uppercase advance is ~0.484 em/char (measured: "QUESTO È UN
# TEST" @119px renders 935 px wide including outline).  0.50 is a safe upper
# bound that keeps the longest cue clear of the frame edge.
_CHAR_EM = 0.50

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


def _finalize(cues: List[Cue], max_chars: int, max_cps: float) -> List[Cue]:
    """Enforce min duration / cps without letting cues overlap.

    A cue that would need more time than the gap before the next cue gets
    merged into it when the combined text still fits `max_chars`; otherwise
    it is clamped to the next cue's start.
    """
    out = list(cues)
    i = 0
    while i < len(out):
        cue = out[i]
        need = MIN_CUE
        if max_cps > 0 and cue.text:
            need = max(need, len(cue.text) / max_cps)
        target = cue.start + need
        limit = out[i + 1].start if i + 1 < len(out) else None

        if limit is None:
            cue.end = max(cue.end, target)
        elif target > limit:
            nxt = out[i + 1]
            if len(cue.text) + 1 + len(nxt.text) <= max_chars:
                cue.text = f"{cue.text} {nxt.text}"
                cue.words = cue.words + nxt.words
                cue.end = nxt.end
                cue.line_count = _wrap_count(cue.text, max_chars)
                out.pop(i + 1)
                continue  # re-evaluate the merged cue
            cue.end = max(cue.start + 0.01, limit)
        else:
            cue.end = min(max(cue.end, target), limit)
        i += 1

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

    return _finalize(raw, chars, cps)


# --------------------------------------------------------------------------- #
# 3. ASS writer
# --------------------------------------------------------------------------- #


def _karaoke_body(cue: Cue) -> str:
    """`{\\kNN}WORD` per word, centiseconds summing exactly to the cue."""
    words = cue.words or [{"start": cue.start, "end": cue.end, "word": cue.text}]
    starts = [float(w.get("start", cue.start)) for w in words]
    total_cs = max(1, int(round((cue.end - cue.start) * 100)))

    ks: List[int] = []
    for i in range(len(words)):
        end_i = starts[i + 1] if i + 1 < len(words) else cue.end
        ks.append(max(0, int(round((end_i - starts[i]) * 100))))

    diff = total_cs - sum(ks)
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

    parts = [
        "{\\k%d}%s" % (k, _ass_escape(str(w.get("word", "")).upper()))
        for w, k in zip(words, ks)
    ]
    return " ".join(parts)


def _clean_body(cue: Cue) -> str:
    return _ass_escape(cue.text)


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
) -> str:
    """Write an ASS file and return its absolute path (plain UTF-8, no BOM)."""
    target = Path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)

    vertical = aspect not in ("16:9", "4:3")
    if vertical:
        base = int(round(0.062 * out_h))
        outline, shadow = 7, 4
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
        outline, shadow = 4, 2
        margin_v = 40

    if style == "clean":
        outline = max(2, outline - 3)
        shadow = max(1, shadow - 2)

    # keep the longest cue inside the frame even if the caller raised max_chars
    longest = max((len(c.text) for c in cues), default=1) or 1
    avail = max(64, out_w - 80)
    size = max(24, min(base, int(avail / (longest * _CHAR_EM))))

    margin_lr = 60
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
        f"Style: Vx,{font},{size},&H00FFFFFF,&H0000D7FF,&H00000000,"
        f"&H80000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,"
        f"{margin_lr},{margin_lr},{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )

    pop = (
        "{\\fscx100\\fscy100\\t(0,80,\\fscx108\\fscy108)\\t(80,160,\\fscx100\\fscy100)}"
    )
    lines: List[str] = []
    for cue in cues:
        if cue.end <= cue.start:
            continue
        if style == "karaoke":
            body = pop + _karaoke_body(cue)
        else:
            body = _clean_body(cue)
        lines.append(
            f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},Vx,,0,0,0,,{body}"
        )

    text = header + "\n".join(lines) + ("\n" if lines else "")
    target.write_text(text, encoding="utf-8", newline="\n")
    return str(target.resolve())


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
    """`ass='D\\:/work/a.ass'` — the only supported way to pass a path."""
    return "ass=" + ass_filter_path(ass_path)


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


def verify_fonts(ass_path, *, log_path=None) -> Dict[str, Any]:
    """Burn one frame and read libass' `fontselect:` decisions.

    libass substitutes a missing family silently, so this is the only way to
    know the render will use the font we asked for.
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

    args = [
        "-ss",
        f"{start + 0.05:.3f}",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={w}x{h}:d=1",
        "-vf",
        burn_filter(src),
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
    result["fallback_count"] = fallback
    result["fonts_used"] = sorted(set(chosen))
    result["requested"] = sorted(set(requested))
    result["rc"] = proc.rc
    result["ok"] = bool(proc.rc == 0 and fallback == 0 and not hard_error)
    return result


# --------------------------------------------------------------------------- #
# 6. stats
# --------------------------------------------------------------------------- #


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
