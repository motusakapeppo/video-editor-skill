"""vx.effects.composite — ten single-input layering looks, as filter templates.

This family draws *over* the picture instead of reshaping it: bands, frames,
guides, a vignette and a rounded-corner matte. Every entry takes exactly one
input and emits one linear ffmpeg 8.1 chain, so the compiler can drop it into an
existing filtergraph without a second branch or a split/overlay pair.

Two things are deliberately absent, and their absence is the contract:

``split_screen`` / ``pip``
    both need a second input plus a ``split`` + ``overlay`` graph; a linear
    template cannot express them, and faking one would produce a still frame
    with a rectangle painted on it.
a two-input ``frame_in_frame``
    the "frame in frame" here is the *single-input* reading: the same picture
    inset on a black surround by ``scale`` + ``pad``. It never samples a second
    clip.

Text is not drawn here either: captions are a composited layer upstream, so
``caption_band`` only reserves the band the layer will sit in. No ``drawtext``,
no font path, no glyph in this module.

A template is real ffmpeg syntax. ``{name}`` refers to a declared
:class:`vx.effect_spec.ParamSpec` and the compiler substitutes the instance's
static value or the declared default, so the placeholder set is exactly the
declared parameter names.
"""

from __future__ import annotations

from typing import Dict

from ..effect_spec import EffectSpec, ParamSpec

#: Bar, band and guide colour. Baked, not a parameter: ``ParamSpec`` carries
#: numeric ranges only, and a colour string in a numeric slot would break the
#: formatter every other family relies on.
_INK = "black"
_GUIDE = "white"


def _rounded_template() -> str:
    """The rounded-corner matte, built once so the distance field is written once.

    ``geq`` evaluates a per-pixel expression. The matte is a rounded rectangle:
    a pixel survives when the squared distance from the rounded core (the frame
    shrunk by the corner radius on each axis) is inside ``r*r``. All three
    planes carry the same predicate, so a corner is ``(0, 128, 128)`` — true
    black — rather than a luma-only hole that keeps its chroma and reads green.
    """
    radius = "min(W,H)*{radius}"
    gap_x = f"max(abs(X-W/2)-(W/2-{radius}),0)"
    gap_y = f"max(abs(Y-H/2)-(H/2-{radius}),0)"
    inside = f"lt({gap_x}*{gap_x}+{gap_y}*{gap_y},{radius}*{radius})"
    return (
        f"geq=lum='if({inside},lum(X,Y),0)'"
        f":cb='if({inside},cb(X,Y),128)'"
        f":cr='if({inside},cr(X,Y),128)'"
    )


ENTRIES: Dict[str, EffectSpec] = {
    "letterbox": EffectSpec(
        kind="letterbox",
        family="composite",
        backend="filter",
        params_spec=(
            ParamSpec(name="ratio", lo=1.2, hi=3.6, default=2.39, step=0.01),
        ),
        filter_template=(
            "drawbox=x=0:y=0:w=iw:h='max(0,(ih-iw/{ratio})/2)'"
            f":color={_INK}:t=fill,"
            "drawbox=x=0:y='ih-max(0,(ih-iw/{ratio})/2)':w=iw"
            ":h='max(0,(ih-iw/{ratio})/2)'"
            f":color={_INK}:t=fill"
        ),
        trigger=(
            "Frame a vertical clip as cinema scope, black bars top and bottom, for a "
            "trailer or a title beat."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "bar height is (ih-iw/ratio)/2, so the visible strip is exactly the "
            "declared aspect ratio; max(0,...) makes a frame already wider than "
            "the ratio a no-op instead of a negative box"
        ),
    ),
    "bars": EffectSpec(
        kind="bars",
        family="composite",
        backend="filter",
        params_spec=(
            ParamSpec(name="size", lo=0.02, hi=0.25, default=0.08, step=0.01),
        ),
        filter_template=(
            f"drawbox=x=0:y=0:w='iw*{{size}}':h=ih:color={_INK}:t=fill,"
            f"drawbox=x='iw-iw*{{size}}':y=0:w='iw*{{size}}':h=ih:color={_INK}:t=fill"
        ),
        trigger=(
            "Pillar-box a wide shot into a vertical frame, or reserve a side gutter for a "
            "sticker column."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "two symmetric vertical bars of size*width each; size is the share of "
            "the width one bar takes, so the visible window is (1-2*size) wide"
        ),
    ),
    "frame_in_frame": EffectSpec(
        kind="frame_in_frame",
        family="composite",
        backend="filter",
        params_spec=(
            ParamSpec(name="inset", lo=0.4, hi=0.95, default=0.8, step=0.05),
        ),
        filter_template=(
            "scale=iw*{inset}:ih*{inset},"
            "pad=w=iw/{inset}:h=ih/{inset}"
            ":x='(iw/{inset}-iw)/2':y='(ih/{inset}-ih)/2':color=" + _INK
        ),
        trigger=(
            "Shrink the picture into a centred card on black, for a quote, a stat or a "
            "recap where the frame itself is the border."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "single input, exactly as the contract requires: scale to inset of the "
            "canvas then pad back to the original size, so the same picture sits "
            "centred on a black surround; the pad offsets use the *scaled* width, "
            "which is what pad's iw means inside its own chain"
        ),
    ),
    "border": EffectSpec(
        kind="border",
        family="composite",
        backend="filter",
        params_spec=(
            ParamSpec(name="thickness", lo=1.0, hi=40.0, default=8.0, step=1.0),
            ParamSpec(name="inset", lo=0.0, hi=0.2, default=0.02, step=0.01),
        ),
        filter_template=(
            "drawbox=x='iw*{inset}':y='ih*{inset}'"
            ":w='iw-2*iw*{inset}':h='ih-2*ih*{inset}'"
            f":color={_GUIDE}:t={{thickness}}"
        ),
        trigger=(
            "Put a crisp outline around the frame, for a picture-card look or to hold a "
            "flat background together."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "one stroked drawbox (t=thickness) inset by a share of each axis; the "
            "colour is baked white because a colour is not a numeric ParamSpec"
        ),
    ),
    "safe_area": EffectSpec(
        kind="safe_area",
        family="composite",
        backend="filter",
        params_spec=(
            ParamSpec(name="margin", lo=0.01, hi=0.15, default=0.05, step=0.01),
            ParamSpec(name="thickness", lo=1.0, hi=12.0, default=2.0, step=1.0),
        ),
        filter_template=(
            f"drawbox=x='iw*{{margin}}':y='ih*{{margin}}'"
            ":w='iw-2*iw*{margin}':h='ih-2*ih*{margin}'"
            f":color={_GUIDE}@0.7:t={{thickness}},"
            "drawbox=x='2*iw*{margin}':y='2*ih*{margin}'"
            ":w='iw-4*iw*{margin}':h='ih-4*ih*{margin}'"
            f":color={_GUIDE}@0.7:t={{thickness}}"
        ),
        trigger=(
            "Overlay action-safe and title-safe guides while checking a vertical cut before "
            "delivery."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "two nested rectangles: the outer at margin, the inner at twice the "
            "margin; this is a review guide for the operator's eye, so it is never "
            "baked into a deliverable automatically"
        ),
    ),
    "grid": EffectSpec(
        kind="grid",
        family="composite",
        backend="filter",
        params_spec=(
            ParamSpec(name="thickness", lo=1.0, hi=12.0, default=2.0, step=1.0),
            ParamSpec(name="alpha", lo=0.1, hi=1.0, default=0.5, step=0.05),
        ),
        filter_template=(
            "drawbox=x='iw/3':y=0:w={thickness}:h=ih"
            f":color={_GUIDE}@{{alpha}}:t=fill,"
            "drawbox=x='2*iw/3':y=0:w={thickness}:h=ih"
            f":color={_GUIDE}@{{alpha}}:t=fill,"
            "drawbox=x=0:y='ih/3':w=iw:h={thickness}"
            f":color={_GUIDE}@{{alpha}}:t=fill,"
            "drawbox=x=0:y='2*ih/3':w=iw:h={thickness}"
            f":color={_GUIDE}@{{alpha}}:t=fill"
        ),
        trigger=(
            "Lay a rule-of-thirds grid over the frame while deciding where a subject or a "
            "caption should sit."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "four filled drawboxes on the thirds, not a measured lattice; the lines "
            "are thickness pixels wide at any canvas, so a 1080-wide deliverable "
            "gets a thinner-looking grid than a 320-wide proxy"
        ),
    ),
    "spotlight": EffectSpec(
        kind="spotlight",
        family="composite",
        backend="filter",
        params_spec=(
            ParamSpec(name="angle", lo=0.63, hi=1.57, default=1.0, step=0.01),
        ),
        filter_template="vignette=angle={angle}",
        trigger=(
            "Darken the frame edges so the centre reads as a spotlight, for a reveal, a "
            "confession or a dream beat."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "vignette's lens angle in radians, clipped by the filter to "
            "[PI/5, PI/2]; the range here is that closed interval, so no declared "
            "value is ever silently clamped"
        ),
    ),
    "rounded": EffectSpec(
        kind="rounded",
        family="composite",
        backend="filter",
        params_spec=(
            ParamSpec(name="radius", lo=0.02, hi=0.30, default=0.08, step=0.01),
        ),
        filter_template=_rounded_template(),
        trigger=(
            "Round the frame corners on black, for a card, a sticker or a UI-style insert."
        ),
        cap_seconds=4.0,
        fallback="skip",
        notes=(
            "a real rounded matte, not a scale+pad imitation: a single geq distance "
            "field cuts each corner at radius*min(W,H) and writes a true black "
            "(0,128,128) outside it on all three planes, because a luma-only cut "
            "keeps the corner chroma and reads green; per-pixel, so it is capped at "
            "4 s and skipped past the cap"
        ),
    ),
    "progress_bar": EffectSpec(
        kind="progress_bar",
        family="composite",
        backend="filter",
        params_spec=(
            ParamSpec(name="seconds", lo=0.5, hi=30.0, default=4.0, step=0.5),
            ParamSpec(name="height", lo=0.004, hi=0.08, default=0.012, step=0.002),
        ),
        filter_template=(
            "drawbox=x=0:y='ih-ih*{height}':w=iw:h='ih*{height}'"
            f":color={_GUIDE}@0.3:t=fill,"
            "drawbox=x=0:y='ih-ih*{height}':w='iw*min(t/{seconds},1)'"
            f":h='ih*{{height}}':color={_GUIDE}:t=fill"
        ),
        trigger=(
            "Show elapsed time as a filling bar along the bottom edge, for a countdown, a "
            "drill or a step-by-step."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "drawbox has no 'duration' variable (ffmpeg 8.1 rejects it: undefined "
            "constant), so the fill is min(t/seconds,1) against a declared fill "
            "time, not against the clip length; a 30%-white track is drawn first "
            "so the frame differs from the source even at t=0 where the fill is 0"
        ),
    ),
    "caption_band": EffectSpec(
        kind="caption_band",
        family="composite",
        backend="filter",
        params_spec=(
            ParamSpec(name="height", lo=0.05, hi=0.45, default=0.18, step=0.01),
            ParamSpec(name="opacity", lo=0.2, hi=1.0, default=0.55, step=0.05),
        ),
        filter_template=(
            "drawbox=x=0:y='ih-ih*{height}':w=iw:h='ih*{height}'"
            ":color=" + _INK + "@{opacity}:t=fill"
        ),
        trigger=(
            "Reserve a darkened lower band so a caption layer stays readable over a busy "
            "shot."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "a scrim only: no glyph, no font, no drawtext — the caption itself is a "
            "composited layer upstream, and this band just guarantees contrast "
            "underneath it"
        ),
    ),
}

__all__ = ["ENTRIES"]
