"""vx.motion_fallback — the Pillow raster that stands in for Motion Studio.

Motion Studio renders an authored HTML template inside the system Chrome. That
engine is the only one that can reflow text, run a ``backdrop-filter`` and animate
a blend; when it is not installed there is no honest way to fake those. What a
caption, a title and a chart need instead is *legible pixels with the right
timing*, and that a CPU rasteriser can do. This module is that rasteriser, and it
says so on every frame.

Two entry points:

``resolve_engine(declared, probe, properties)``
    A pure decision. Given what the document declares, what
    :func:`vx.motion.available` probed, and which properties the node asks for,
    it names the engine to use, whether that is a fallback, what is degraded,
    and why. It never probes, never launches anything and never reads a file.

``capture(...)``
    The same contract as :func:`vx.motion.capture` — one RGBA PNG per virtual
    frame, ``frame_%05d.png``, ``max(1, round(duration * fps))`` frames — but the
    pixels come from a family-aware Pillow card instead of a browser. It runs the
    *same* static lint and the *same* parameter validation as the browser path
    first: there is no second, weaker gate. It never imports Playwright.

Determinism
-----------
The raster depends only on the resolved parameters, the frame size, the frame
index and ``t``. There is no clock, no random source and no metadata in the PNG,
so rendering the same template twice gives byte-identical files and identical
sha256 per frame. The fade/slide envelope is a closed-form function of ``t``.

What the fallback is *not*
-------------------------
It is not a layout engine. It draws a card: the primary string of the family,
the accent colour the template declares, a fade and a slide, and a small marker
that reads ``PILLOW FALLBACK`` so a frame can never be mistaken for a browser
render. ``degraded`` lists exactly what is lost: ``html``, ``layout``,
``effects``.
"""

from __future__ import annotations

import hashlib
import io
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from . import motion, motion_cache, motion_lint

__all__ = [
    "ENGINE_ONLY_PROPERTIES",
    "PILLOW_PROPERTIES",
    "FALLBACK_DEGRADED",
    "resolve_engine",
    "capture",
]

#: Properties a Pillow card cannot express at all. A document that asks for one
#: of these and has no browser is refused, never silently downgraded.
ENGINE_ONLY_PROPERTIES = frozenset({"backdrop_filter", "blend_animated", "reflow"})

#: The only declared property the card can approximate: the text is laid out by
#: this module instead of by CSS, so "layout" degrades rather than disappears.
PILLOW_PROPERTIES = frozenset({"layout"})

#: What a fallback frame loses, stated once and reported on every capture.
FALLBACK_DEGRADED = ["html", "layout", "effects"]

#: Primary string candidates, in the documented order. A family's own name wins
#: over its generic "text", and a chart's caption wins over its bare number.
_PRIMARY_ORDER = ("title", "subtitle", "label", "text", "caption", "value")
_SECONDARY_ORDER = (
    "subtitle",
    "note",
    "kicker",
    "name",
    "caption",
    "label",
    "text",
    "title",
)
_ACCENT_KEYS = ("accent", "colour", "color")
_DEFAULT_ACCENT = (255, 212, 0)
_CARD_INK = (12, 14, 18)
_CARD_INK_ALPHA = 214
_PAPER = (242, 239, 230)
#: Envelope: the first and last fifth of the clip fade; the first quarter slides.
_FADE_SHARE = 0.15
_SLIDE_SHARE = 0.22
_SLIDE_TRAVEL = 0.05
_FONT_RELATIVE = ("fonts", "Antonio-Bold.ttf")


# ------------------------------------------------------------------ decision


def resolve_engine(
    declared: str,
    probe: Mapping[str, Any],
    properties: Iterable[str],
) -> Dict[str, Any]:
    """Decide the engine for one generator node, without touching the machine.

    ``declared`` is ``chromium``, ``pillow`` or ``auto``; ``probe`` is the dict
    :func:`vx.motion.available` returned; ``properties`` is the set of declared
    properties of the node's effects.

    Returned keys, always the same four:

    ``engine``
        ``"chromium"``, ``"pillow"``, or ``""`` for *unavailable* (the caller
        raises its own ``BACKEND_UNAVAILABLE``).
    ``fallback``
        True only when the document asked for ``auto`` and got Pillow.
    ``degraded``
        What the chosen engine cannot do, as a list of names.
    ``reason``
        A stable, human-readable sentence; never a stack trace.

    Rules:

    * ``pillow`` — Pillow, not a fallback: the document asked for it.
    * ``chromium`` — Chromium when the probe found a browser. Without one the
      answer is *unavailable*: an engine-only property set is never downgraded
      silently.
    * ``auto`` — Chromium when a browser exists. Without one, Pillow only when
      every declared property is one the card can approximate
      (:data:`PILLOW_PROPERTIES`); otherwise unavailable.
    """
    if declared not in ("chromium", "pillow", "auto"):
        raise ValueError(
            f"declared engine must be chromium, pillow or auto, got {declared!r}"
        )
    requested = [str(name) for name in properties or ()]
    engine_only = sorted({name for name in requested if name in ENGINE_ONLY_PROPERTIES})
    available = bool(probe.get("available"))
    probed = str(probe.get("engine") or "")
    probe_reason = str(probe.get("reason") or "")

    if declared == "pillow":
        return {
            "engine": "pillow",
            "fallback": False,
            "degraded": [],
            "reason": "declared pillow: the document asks for the CPU raster",
        }

    if declared == "chromium":
        if available:
            return {
                "engine": "chromium",
                "fallback": False,
                "degraded": [],
                "reason": f"declared chromium and the probe found {probed or 'a browser'}",
            }
        return {
            "engine": "",
            "fallback": False,
            "degraded": [],
            "reason": (
                "declared chromium but no browser is usable"
                f" ({probe_reason or 'probe reported unavailable'}); a chromium-only "
                "node is refused, never downgraded"
            ),
        }

    # declared == "auto"
    if available:
        return {
            "engine": "chromium",
            "fallback": False,
            "degraded": [],
            "reason": f"auto and the probe found {probed or 'a browser'}",
        }
    if engine_only:
        return {
            "engine": "",
            "fallback": False,
            "degraded": [],
            "reason": (
                "auto without a browser, but the node declares engine-only "
                f"propert{'y' if len(engine_only) == 1 else 'ies'} {engine_only}; "
                f"the card can only approximate {sorted(PILLOW_PROPERTIES)}"
            ),
        }
    return {
        "engine": "pillow",
        "fallback": True,
        "degraded": list(FALLBACK_DEGRADED),
        "reason": (
            "auto without a browser: the declared properties are within "
            f"{sorted(PILLOW_PROPERTIES)}, so the card renders them and the report "
            f"declares {list(FALLBACK_DEGRADED)} degraded"
            + (
                f"; probe said: {probe_reason}"
                if probe_reason and probe_reason != "ok"
                else ""
            )
        ),
    }


# -------------------------------------------------------------------- raster


def _font_path() -> Path:
    """The repo font, located from the module tree — never a literal machine path."""
    path = motion.templates_root().parent.joinpath(*_FONT_RELATIVE)
    if not path.is_file():
        raise motion.MotionError(
            "FONT_MISSING",
            f"the fallback raster needs {path.name} under assets/fonts",
            path=str(path),
        )
    return path


def _accent(resolved: Mapping[str, Any]) -> Tuple[Tuple[int, int, int], str]:
    """The template's accent colour, or the house yellow when none is declared."""
    for key in _ACCENT_KEYS:
        raw = resolved.get(key)
        if not isinstance(raw, str):
            continue
        text = raw.strip().lstrip("#")
        if len(text) in (3, 6, 8) and all(c in "0123456789abcdefABCDEF" for c in text):
            if len(text) == 3:
                text = "".join(c * 2 for c in text)
            return (
                (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)),
                str(key),
            )
    return _DEFAULT_ACCENT, ""


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value) == int(value) else f"{value:g}"
    return ""


def _primary_text(resolved: Mapping[str, Any]) -> Tuple[str, str]:
    for key in _PRIMARY_ORDER:
        text = _as_text(resolved.get(key))
        if text:
            return key, text
    for key in sorted(resolved):
        text = _as_text(resolved[key])
        if text:
            return key, text
    return "", ""


def _secondary_text(resolved: Mapping[str, Any], primary_key: str) -> str:
    for key in _SECONDARY_ORDER:
        if key == primary_key:
            continue
        text = _as_text(resolved.get(key))
        if text and text != _as_text(resolved.get(primary_key)):
            return text
    return ""


def _wrap(text: str, font: ImageFont.FreeTypeFont, limit: float, probe) -> List[str]:
    """Greedy wrap, measured with the real font; a single long word never splits."""
    words = text.split()
    if not words:
        return []
    lines: List[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if probe.textlength(candidate, font=font) <= limit:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _fit_primary(
    text: str,
    font_path: Path,
    height: int,
    width: int,
    limit: float,
) -> Tuple[ImageFont.FreeTypeFont, List[str], int]:
    """Largest Antonio size that keeps the primary string inside the card."""
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    size = max(14, int(round(0.092 * height)))
    while size > 14:
        font = ImageFont.truetype(str(font_path), size)
        lines = _wrap(text, font, limit, probe)
        widest = max((probe.textlength(line, font=font) for line in lines), default=0.0)
        if widest <= limit:
            return font, lines, size
        size -= 2
    font = ImageFont.truetype(str(font_path), 14)
    return font, _wrap(text, font, limit, probe), 14


def _envelope(t: float, duration: float) -> Tuple[float, float]:
    """Fade factor and remaining slide as a share of height: closed forms of ``t``."""
    if duration <= 0:
        return 1.0, 0.0
    progress = min(1.0, max(0.0, t / duration))
    fade_in = min(1.0, progress / _FADE_SHARE) if progress < _FADE_SHARE else 1.0
    fade_out = (
        min(1.0, (1.0 - progress) / _FADE_SHARE)
        if progress > 1.0 - _FADE_SHARE
        else 1.0
    )
    alpha = max(0.0, min(1.0, fade_in * fade_out))
    entered = min(1.0, progress / _SLIDE_SHARE)
    ease = 1.0 - (1.0 - entered) ** 3
    return alpha, (1.0 - ease) * _SLIDE_TRAVEL


def _raster(
    resolved: Mapping[str, Any],
    *,
    width: int,
    height: int,
    t: float,
    duration: float,
    font_path: Path,
) -> Image.Image:
    """One RGBA frame: dark card, accent bar, two text rows, honest marker."""
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    primary_key, primary = _primary_text(resolved)
    secondary = _secondary_text(resolved, primary_key)
    accent, _accent_key = _accent(resolved)

    margin = max(12, int(round(0.08 * width)))
    radius = max(6, int(round(0.026 * min(width, height))))
    inner_pad = max(10, int(round(0.045 * width)))
    limit = float(width - 2 * margin - 2 * inner_pad)

    primary_font, primary_lines, primary_px = _fit_primary(
        primary, font_path, height, width, limit
    )
    # A chart's number is the picture: when the family declares a numeric value
    # beside its caption, the card draws it as the figure above the label. Same
    # params, same figure; a different value is a different frame.
    raw_value = resolved.get("value")
    figure = ""
    if (
        primary_key != "value"
        and isinstance(raw_value, (int, float))
        and not isinstance(raw_value, bool)
    ):
        figure = _as_text(raw_value)
    figure_px = max(12, int(round(primary_px * 1.55)))
    figure_font = ImageFont.truetype(str(font_path), figure_px)
    secondary_px = max(11, int(round(primary_px * 0.42)))
    secondary_font = ImageFont.truetype(str(font_path), secondary_px)
    secondary_lines = (
        _wrap(secondary, secondary_font, limit, ImageDraw.Draw(Image.new("L", (8, 8))))
        if secondary
        else []
    )

    figure_line_h = figure_px * 1.12
    primary_line_h = primary_px * 1.2
    secondary_line_h = secondary_px * 1.35
    marker_px = max(10, int(round(primary_px * 0.26)))
    marker_font = ImageFont.truetype(str(font_path), marker_px)
    marker_text = "PILLOW FALLBACK"
    marker_h = marker_px * 1.6

    content_h = (
        (figure_line_h if figure else 0.0)
        + len(primary_lines) * primary_line_h
        + len(secondary_lines) * secondary_line_h
        + marker_h
    )
    card_h = int(round(min(height - 2 * margin, content_h + 2 * inner_pad)))
    card_w = width - 2 * margin
    card_x = margin
    card_y = int(round((height - card_h) / 2.0))
    bar_w = max(5, int(round(0.012 * width)))

    alpha_factor, slide_share = _envelope(t, duration)
    offset = int(round(slide_share * height))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (card_x, card_y + offset, card_x + card_w, card_y + card_h + offset),
        radius=radius,
        fill=(*_CARD_INK, _CARD_INK_ALPHA),
    )
    draw.rectangle(
        (
            card_x,
            card_y + offset + radius // 2,
            card_x + bar_w,
            card_y + card_h + offset - radius // 2,
        ),
        fill=(*accent, 255),
    )

    text_x = card_x + inner_pad
    text_y = card_y + inner_pad + offset
    if figure:
        draw.text(
            (text_x, text_y),
            figure,
            font=figure_font,
            fill=(*accent, 255),
            anchor="la",
        )
        text_y += figure_line_h
    for line in primary_lines:
        draw.text(
            (text_x, text_y),
            line,
            font=primary_font,
            fill=(*_PAPER, 255),
            anchor="la",
        )
        text_y += primary_line_h
    for line in secondary_lines:
        draw.text(
            (text_x, text_y),
            line,
            font=secondary_font,
            fill=(*accent, 255),
            anchor="la",
        )
        text_y += secondary_line_h

    marker_y = card_y + card_h + offset - inner_pad - marker_px
    draw.rectangle(
        (
            text_x,
            marker_y + marker_px * 0.45,
            text_x + marker_px * 0.5,
            marker_y + marker_px * 0.95,
        ),
        fill=(*accent, 255),
    )
    draw.text(
        (text_x + marker_px * 0.9, marker_y),
        marker_text,
        font=marker_font,
        fill=(*accent, 190),
        anchor="la",
    )

    if alpha_factor >= 1.0:
        return canvas
    alpha = canvas.getchannel("A").point(lambda value: int(value * alpha_factor + 0.5))
    canvas.putalpha(alpha)
    return canvas


# ------------------------------------------------------------------- capture


def capture(
    template_dir: str | Path,
    params: Optional[Dict[str, Any]],
    out_dir: str | Path,
    *,
    fps: Optional[float] = None,
    size: Optional[Sequence[int]] = None,
    frame_timeout_s: Optional[float] = None,
    capture_timeout_s: Optional[float] = None,
    skip_lint: bool = False,
    workdir: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Render the card sequence with Pillow, honouring :func:`vx.motion.capture`.

    Same gate order as the browser path: the static lint refuses a broken
    template before any pixel is drawn, then the declaration is loaded and the
    parameters are resolved with the one validator both engines share. The only
    differences are the pixels and the declared degradation.
    """
    if not skip_lint:
        motion_lint.lint_or_raise(template_dir, runtime_check=False)
    template = motion.load_template(template_dir)
    resolved = motion.validate_params(template, params)
    cache = motion_cache.Cache(workdir)
    rate = float(fps if fps is not None else template["fps"])
    if rate <= 0:
        raise motion.MotionError(
            "PARAM_RANGE", f"fps must be > 0, got {rate}", param="fps", got=rate
        )
    if size is None:
        width, height = template["size"]
    else:
        if len(tuple(size)) != 2:
            raise motion.MotionError(
                "PARAM_RANGE",
                f"size must be [width, height], got {size!r}",
                param="size",
            )
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            raise motion.MotionError(
                "PARAM_RANGE",
                f"size must be positive, got {width}x{height}",
                param="size",
            )
    frame_limit = float(
        motion.MOTION_FRAME_TIMEOUT_S if frame_timeout_s is None else frame_timeout_s
    )
    capture_limit = float(
        motion.MOTION_CAPTURE_TIMEOUT_S
        if capture_timeout_s is None
        else capture_timeout_s
    )
    if frame_limit <= 0 or capture_limit <= 0:
        raise motion.MotionError(
            "PARAM_RANGE",
            "frame_timeout_s and capture_timeout_s must be > 0",
            frame_timeout_s=frame_limit,
            capture_timeout_s=capture_limit,
        )

    duration = float(template["duration"])
    count = max(1, int(round(duration * rate)))
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for stale in destination.glob("frame_*.png"):
        stale.unlink()

    font_path = _font_path()
    engine = "pillow"
    degraded: List[str] = []
    cache_degraded: List[str] = []
    paths: List[str] = []
    digests: List[str] = []

    started = time.perf_counter()
    deadline = started + capture_limit
    try:
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
                    engine=f"{engine}-fallback",
                    t=moment,
                )
                png = cache.read(frame_key)
            if png is None:
                if time.perf_counter() > deadline:
                    raise motion.MotionError(
                        "CAPTURE_TIMEOUT",
                        f"frame {index} not reached within {capture_limit}s "
                        "(whole-capture ceiling)",
                        frame=index,
                        timeout_s=capture_limit,
                        frames_done=len(paths),
                    )
                frame = _raster(
                    resolved,
                    width=width,
                    height=height,
                    t=moment,
                    duration=duration,
                    font_path=font_path,
                )
                buffer = _png_bytes(frame)
                if frame_key is not None:
                    try:
                        cache.put(frame_key, buffer)
                    except motion_cache.CacheRefused as exc:
                        cache_degraded.append(f"frame {index}: {exc}")
                    except Exception as exc:  # noqa: BLE001 - declared degradation
                        cache_degraded.append(
                            f"frame {index}: cache write failed: {exc}"
                        )
                png = buffer
            path = destination / f"frame_{index:05d}.png"
            path.write_bytes(png)
            paths.append(str(path))
            digests.append(hashlib.sha256(png).hexdigest())
    finally:
        if cache.enabled:
            try:
                cache.register()
            except Exception as exc:  # noqa: BLE001 - declared degradation
                cache_degraded.append(f"vault registration failed: {exc}")

    elapsed = time.perf_counter() - started
    cache_report = cache.report()
    cache_report["degraded"] = list(cache_degraded)
    return {
        "template": template["name"],
        "engine": engine,
        "fallback": True,
        "degraded": list(FALLBACK_DEGRADED),
        "frames": paths,
        "frame_count": len(paths),
        "fps": rate,
        "size": [width, height],
        "duration": duration,
        "params": resolved,
        "blocked_requests": [],
        "elapsed_s": round(elapsed, 3),
        "seconds_per_frame": round(elapsed / max(1, len(paths)), 4),
        "frame_sha256": digests,
        "frame_timeout_s": frame_limit,
        "capture_timeout_s": capture_limit,
        "cache": cache_report,
    }


def _png_bytes(frame: Image.Image) -> bytes:
    """Encode RGBA PNG with no metadata: same pixels, same bytes, every run."""
    buffer = io.BytesIO()
    frame.save(buffer, format="PNG", optimize=False, compress_level=6)
    return buffer.getvalue()
