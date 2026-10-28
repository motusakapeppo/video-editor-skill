"""vx.effects.color — the twelve grade, tone and key adjustments.

Every entry here is static by construction: a grade is a per-pixel function of
the picture, nothing on it is animated per keyframe, so ``animatable=False`` and
the compiler refuses a keyframed instance with ``EFFECT_NOT_ANIMATABLE`` instead
of silently dropping the keys.

A template is a real ffmpeg 8.1 filter chain. ``{name}`` refers to a declared
:class:`vx.effect_spec.ParamSpec`; the compiler substitutes the instance's
static value, or the declared default when the node omits it. Nothing here
changes the frame size or the rate, and every chain is point-wise, so the
compiler's restore stage (re-stamp rate, scale, crop back to the clip canvas) is
a no-op for these entries and they carry no time cap.

Where a filter does not exist in this build the template composes real ones that
produce the same look, and the notes say which: there is no ``posterize`` and no
``threshold`` filter, so both are a real ``lutyuv`` expression; there is no
``sepia`` filter, so it is a per-channel ``lutrgb`` ramp.
"""

from __future__ import annotations

from typing import Dict

from ..effect_spec import EffectSpec, ParamSpec

ENTRIES: Dict[str, EffectSpec] = {
    "curves": EffectSpec(
        kind="curves",
        family="color",
        backend="filter",
        params_spec=(
            ParamSpec(name="mid", lo=0.15, hi=0.85, default=0.36, step=0.01),
        ),
        filter_template="curves=master='0/0 0.5/{mid} 1/1'",
        trigger=(
            "Give a flat or a dense grade its body by moving one tone-curve point, without "
            "touching the black or the white point."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "one master curve point at input 0.5: a value under 0.5 darkens the "
            "midtones, above it lifts them, and the fixed 0/0 and 1/1 endpoints are "
            "what keep the black and white points where they were"
        ),
    ),
    "hueshift": EffectSpec(
        kind="hueshift",
        family="color",
        backend="filter",
        params_spec=(
            ParamSpec(name="angle", lo=-180.0, hi=180.0, default=30.0, step=1.0),
        ),
        filter_template="hue=h={angle}",
        trigger=(
            "Rotate every hue by a fixed angle, to move a costume or a location into a "
            "different palette."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "hue rotates the whole chroma wheel in degrees and leaves luma alone; the "
            "declared range is ±180, the point where a full turn meets itself, so a "
            "rotation can never wrap past the same picture twice"
        ),
    ),
    "selective_color": EffectSpec(
        kind="selective_color",
        family="color",
        backend="filter",
        params_spec=(
            ParamSpec(name="yellow", lo=0.0, hi=1.0, default=0.5, step=0.05),
            ParamSpec(name="magenta", lo=0.0, hi=1.0, default=0.5, step=0.05),
        ),
        filter_template=(
            "selectivecolor=blues='0 0 {yellow} 0':greens='0 {magenta} 0 0'"
        ),
        trigger=(
            "Move one colour range without touching the rest, to push a sky or a "
            "foliage away from its neighbours."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "selectivecolor adjusts CMYK per colour range: the blues take a yellow "
            "boost and the greens a magenta one, and the six ranges the entry does "
            "not name keep their exact zeros, so the shift lands only on those two"
        ),
    ),
    "white_balance": EffectSpec(
        kind="white_balance",
        family="color",
        backend="filter",
        params_spec=(
            ParamSpec(
                name="temperature", lo=2000.0, hi=12000.0, default=5000.0, step=100.0
            ),
        ),
        filter_template="colortemperature=temperature={temperature}",
        trigger=(
            "Correct a colour-temperature cast, or impose one, to make a shot read warm "
            "or cool."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "colortemperature takes the target white point in Kelvin and 6500K is the "
            "identity, so the default sits at 5000K on purpose: a neutral default "
            "would format into a filter that changes nothing at all"
        ),
    ),
    "duotone": EffectSpec(
        kind="duotone",
        family="color",
        backend="filter",
        params_spec=(
            ParamSpec(name="tint", lo=0.0, hi=360.0, default=210.0, step=5.0),
            ParamSpec(name="strength", lo=0.1, hi=1.0, default=0.6, step=0.05),
        ),
        filter_template=(
            "hue=s=0,colorize=hue={tint}:saturation={strength}:lightness=0.3:mix=1"
        ),
        trigger=(
            "Wash the frame in one tint over black, for a graphic, poster-like beat."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "hue=s=0 empties the chroma first, then colorize paints the luma ramp with "
            "one hue at the declared saturation: the second tone of the duotone is the "
            "black the ramp already ends on, not a second gradient; lightness=0.3 "
            "keeps the ramp centred instead of crushing its bottom"
        ),
    ),
    "posterize": EffectSpec(
        kind="posterize",
        family="color",
        backend="filter",
        params_spec=(
            ParamSpec(name="step", lo=4.0, hi=128.0, default=32.0, step=4.0),
        ),
        filter_template="lutyuv=y='round(val/{step})*{step}'",
        trigger=(
            "Quantise the picture into flat bands, for a print, a comic or a "
            "cheap-camera look."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "this build has no posterize filter, so a lutyuv expression rounds luma to "
            "the nearest multiple of step; the chroma is left alone, which is what "
            "keeps the bands coloured instead of grey; step is a band width in 8-bit "
            "code values, 4 is near-smooth and 128 collapses the picture to two levels"
        ),
    ),
    "threshold": EffectSpec(
        kind="threshold",
        family="color",
        backend="filter",
        params_spec=(
            ParamSpec(name="cut", lo=0.0, hi=255.0, default=128.0, step=1.0),
        ),
        filter_template="lutyuv=y='if(gte(val,{cut}),255,0)':u=128:v=128",
        trigger=(
            "Reduce the frame to pure black and white, for a stencil, a title card or a "
            "mask."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "ffmpeg's own threshold filter takes four inputs (the picture plus a "
            "threshold, a min and a max stream) and cannot run in this single-chain "
            "backend, so lutyuv compares luma to the cut and emits 255 or 0; u=v=128 "
            "flattens the chroma to neutral, which is what makes the result "
            "two-valued instead of a tinted threshold"
        ),
    ),
    "negative": EffectSpec(
        kind="negative",
        family="color",
        backend="filter",
        params_spec=(
            ParamSpec(name="contrast", lo=0.5, hi=2.0, default=1.2, step=0.05),
        ),
        filter_template="negate,eq=contrast={contrast}",
        trigger=(
            "Invert the picture to a photographic negative, for a flash, a reveal or a "
            "dream insert."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "negate inverts all three planes; the eq stage re-applies contrast because "
            "a plain inversion of an already graded frame reads flat, so the two "
            "filters are one effect rather than two entries"
        ),
    ),
    "sepia": EffectSpec(
        kind="sepia",
        family="color",
        backend="filter",
        params_spec=(
            ParamSpec(name="warmth", lo=0.0, hi=1.0, default=0.6, step=0.05),
        ),
        filter_template=(
            "lutrgb=r='min(255,val*(1+{warmth}*0.2)+{warmth}*70)'"
            ":g='min(255,val*(1-{warmth}*0.1)+{warmth}*25)'"
            ":b='val*(1-{warmth}*0.35)'"
        ),
        trigger=(
            "Tint the frame brown-gold like an old print, for memory, archive or "
            "flashback material."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "this build has no sepia filter, so three per-channel luma ramps do it: red "
            "gains a slope and an offset, green loses a little, blue loses the most; "
            "warmth 0 is the identity and the red and green ramps are clamped at 255 "
            "so the highlights stay white instead of clipping pink"
        ),
    ),
    "teal_orange": EffectSpec(
        kind="teal_orange",
        family="color",
        backend="filter",
        params_spec=(
            ParamSpec(name="shadow", lo=0.0, hi=0.6, default=0.25, step=0.05),
            ParamSpec(name="highlight", lo=0.0, hi=0.6, default=0.25, step=0.05),
        ),
        filter_template=(
            "colorbalance=rs=-{shadow}:bs={shadow}"
            ":rh={highlight}:bh=-{highlight}:pl=1"
        ),
        trigger=(
            "Split the grade to teal shadows and orange highlights, the blockbuster "
            "skin-tone look."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "colorbalance pulls the shadows away from red and toward blue and pushes "
            "the highlights the other way; pl=1 preserves lightness, so the split tone "
            "changes the colour of the ends of the ramp without shifting the exposure"
        ),
    ),
    "bleach_bypass": EffectSpec(
        kind="bleach_bypass",
        family="color",
        backend="filter",
        params_spec=(
            ParamSpec(name="saturation", lo=0.0, hi=1.0, default=0.35, step=0.05),
            ParamSpec(name="crush", lo=0.05, hi=0.19, default=0.13, step=0.01),
            ParamSpec(name="lift", lo=0.71, hi=0.95, default=0.78, step=0.01),
        ),
        filter_template=(
            "hue=s={saturation},curves=master='0/0 0.2/{crush} 0.7/{lift} 1/1'"
        ),
        trigger=(
            "Drain the colour and harden the contrast, the bleach-bypass look of a war "
            "film."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "hue drops the saturation to a declared share of the original, then the "
            "curve crushes the shadows at input 0.2 and drives the highlights at 0.7 "
            "up, which is the silver-retention contrast; crush is declared under 0.2 "
            "and lift over 0.7, because outside those two bounds the curve stops being "
            "a contrast at all"
        ),
    ),
    "day_for_night": EffectSpec(
        kind="day_for_night",
        family="color",
        backend="filter",
        params_spec=(
            ParamSpec(name="brightness", lo=-0.5, hi=-0.05, default=-0.25, step=0.05),
            ParamSpec(name="blue", lo=0.0, hi=0.6, default=0.25, step=0.05),
        ),
        filter_template=(
            "eq=brightness={brightness}:saturation=0.4,colorbalance=bs={blue}"
        ),
        trigger=(
            "Turn a daylight shot into a night scene without a second plate, for a night "
            "beat on a daylight budget."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "eq drops the exposure and the saturation, then colorbalance adds blue to "
            "the shadows; the whole brightness range is negative on purpose, because a "
            "day-for-night that brightens the frame is not one"
        ),
    ),
}

__all__ = ["ENTRIES"]
