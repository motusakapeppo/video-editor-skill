"""vx.effects.geometry — the twelve framing warps, declared as filter templates.

Every entry here is static by construction: the picture is reshaped, nothing on
it is animated per keyframe, so ``animatable=False`` and the compiler refuses a
keyframed instance with ``EFFECT_NOT_ANIMATABLE`` instead of silently dropping
the keys.

A template is a real ffmpeg 8.1 filter chain. ``{name}`` refers to a declared
:class:`vx.effect_spec.ParamSpec`; the compiler substitutes the instance's
static value, or the declared default when the node omits it. Templates are
allowed to change the frame size (``zoompan`` always emits its own canvas): the
compiler re-stamps the declared timeline rate and crops back to the clip canvas
after the stage, so a geometry effect can never change a clip's duration or
deliverable size.
"""

from __future__ import annotations

from typing import Dict

from ..effect_spec import EffectSpec, ParamSpec

ENTRIES: Dict[str, EffectSpec] = {
    "zoompan": EffectSpec(
        kind="zoompan",
        family="geometry",
        backend="filter",
        params_spec=(
            ParamSpec(name="zoom", lo=1.0, hi=4.0, default=1.25, step=0.05),
        ),
        filter_template=(
            "zoompan=z={zoom}:d=1"
            ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        ),
        trigger=(
            "Push in on a frame without moving the camera, to add weight to a still or a "
            "reaction."
        ),
        cap_seconds=8.0,
        fallback="static",
        notes=(
            "zoompan always emits its own canvas (hd720 by default) and re-tags "
            "the frame rate; the compiler's restore stage scales, crops back to "
            "the clip canvas and re-stamps the declared rate, so neither size nor "
            "duration drifts"
        ),
    ),
    "rotate": EffectSpec(
        kind="rotate",
        family="geometry",
        backend="filter",
        params_spec=(
            ParamSpec(name="angle", lo=-180.0, hi=180.0, default=5.0, step=1.0),
        ),
        filter_template="rotate={angle}*PI/180:c=black",
        trigger=(
            "Rotate the whole frame by a fixed angle to level a tilted shot or to tilt it "
            "on purpose."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "one static rotation in degrees, expanded to radians inside the "
            "filter; the corners are filled black and the restore stage crops "
            "the result back to the clip canvas"
        ),
    ),
    "flip_h": EffectSpec(
        kind="flip_h",
        family="geometry",
        backend="filter",
        params_spec=(
            ParamSpec(name="amount", lo=0.1, hi=1.0, default=1.0, step=0.1),
        ),
        filter_template="crop=iw*{amount}:ih:0:0,hflip,pad=iw/{amount}:ih:0:0:black",
        trigger=(
            "Mirror the frame left to right, for a selfie view or to break a viewer's "
            "sense of direction."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "hflip only; amount=1 flips the whole frame, a smaller amount flips "
            "that share of the width from the left edge and pads the rest black, "
            "which pad restores to the full width inside the same chain"
        ),
    ),
    "flip_v": EffectSpec(
        kind="flip_v",
        family="geometry",
        backend="filter",
        params_spec=(
            ParamSpec(name="amount", lo=0.1, hi=1.0, default=1.0, step=0.1),
        ),
        filter_template="crop=iw:ih*{amount}:0:0,vflip,pad=iw:ih/{amount}:0:0:black",
        trigger=(
            "Mirror the frame top to bottom, for reflections or an upside-down gag."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "vflip only; amount=1 flips the whole frame, a smaller amount flips "
            "that share of the height from the top edge and pads the rest black"
        ),
    ),
    "mirror": EffectSpec(
        kind="mirror",
        family="geometry",
        backend="filter",
        params_spec=(
            ParamSpec(name="offset", lo=0.0, hi=0.5, default=0.0, step=0.05),
        ),
        filter_template=(
            "geq=lum='lum(abs(W*(1/2+{offset})-1-X),Y)'"
            ":cb='cb(abs(W*(1/2+{offset})-1-X),Y)'"
            ":cr='cr(abs(W*(1/2+{offset})-1-X),Y)'"
        ),
        trigger=(
            "Fold the frame about a vertical axis so one half mirrors the other, for a "
            "symmetric, dreamlike frame."
        ),
        cap_seconds=6.0,
        fallback="skip",
        notes=(
            "geq folds every plane about the axis at 1/2+offset of the width; it "
            "is a per-pixel read, so it is capped and skipped past the cap rather "
            "than paid for on a long clip"
        ),
    ),
    "shake": EffectSpec(
        kind="shake",
        family="geometry",
        backend="filter",
        params_spec=(
            ParamSpec(name="amplitude", lo=0.0, hi=40.0, default=8.0, step=1.0),
            ParamSpec(name="frequency", lo=0.1, hi=10.0, default=3.0, step=0.1),
        ),
        filter_template=(
            "crop=iw*0.9:ih*0.9"
            ":x='(iw-ow)/2+{amplitude}*sin(2*PI*t*{frequency})'"
            ":y='(ih-oh)/2+{amplitude}*cos(2*PI*t*{frequency})'"
        ),
        trigger="Shake the frame on impact, a hit, a slam or a loud beat.",
        cap_seconds=8.0,
        fallback="static",
        notes=(
            "a 10% safety crop whose x/y track sin/cos of t, so the camera shakes "
            "without ever exposing the edge; the restore stage scales the crop "
            "back to the clip canvas"
        ),
    ),
    "whip_pan": EffectSpec(
        kind="whip_pan",
        family="geometry",
        backend="filter",
        params_spec=(
            ParamSpec(name="speed", lo=0.5, hi=20.0, default=6.0, step=0.5),
            ParamSpec(name="window", lo=0.5, hi=1.0, default=0.75, step=0.05),
        ),
        filter_template=(
            "crop=w=iw*{window}:h=ih:x='(iw-ow)*min(t*{speed},1)':y=0"
        ),
        trigger=(
            "Whip the frame sideways in one fast move, for a transition between two "
            "beats."
        ),
        cap_seconds=6.0,
        fallback="static",
        notes=(
            "a narrower crop whose x slides across the source once, clamped at "
            "the right edge; the restore stage scales it back to the clip canvas "
            "so the window never reads as a zoom out"
        ),
    ),
    "dolly_zoom": EffectSpec(
        kind="dolly_zoom",
        family="geometry",
        backend="filter",
        params_spec=(
            ParamSpec(name="zoom", lo=1.0, hi=2.0, default=1.05, step=0.01),
            ParamSpec(name="rate", lo=0.001, hi=0.2, default=0.02, step=0.001),
        ),
        filter_template=(
            "zoompan=z='min({zoom}+{rate}*on,8)':d=1"
            ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        ),
        trigger=(
            "Grow the frame continuously while the subject holds, the vertigo move of a "
            "dolly zoom."
        ),
        cap_seconds=8.0,
        fallback="static",
        notes=(
            "zoompan with a zoom keyed on the output frame number, clamped at 8x; "
            "the compiler's restore stage re-stamps the rate and the canvas that "
            "zoompan rewrites"
        ),
    ),
    "tilt_shift": EffectSpec(
        kind="tilt_shift",
        family="geometry",
        backend="filter",
        params_spec=(
            ParamSpec(name="shift", lo=0.0, hi=0.5, default=0.08, step=0.01),
        ),
        filter_template=(
            "perspective=x0=0:y0='{shift}*H':x1=W:y1=0"
            ":x2=0:y2=H:x3=W:y3='H-{shift}*H'"
        ),
        trigger=(
            "Tilt the image plane so a city or a set reads as a miniature, the optical "
            "half of a tilt-shift look."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "the tilt half of a tilt-shift lens, a keystone warp from perspective; "
            "the selective-blur half needs two branches and a mask, which this "
            "single-chain backend does not declare"
        ),
    ),
    "fisheye": EffectSpec(
        kind="fisheye",
        family="geometry",
        backend="filter",
        params_spec=(
            ParamSpec(name="strength", lo=0.05, hi=1.0, default=0.35, step=0.05),
        ),
        filter_template="lenscorrection=k1=-{strength}:k2=0:i=bilinear:fc=black",
        trigger=(
            "Bulge the frame like an action camera or a peephole, for a wide, distorted "
            "look."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "lenscorrection with a negative quadratic term bulges instead of "
            "rectifying; unmapped corners are filled black and cropped back to "
            "the clip canvas by the restore stage"
        ),
    ),
    "pixelate": EffectSpec(
        kind="pixelate",
        family="geometry",
        backend="filter",
        params_spec=(
            ParamSpec(name="size", lo=2.0, hi=64.0, default=16.0, step=1.0),
        ),
        filter_template="pixelize=w={size}:h={size}:mode=avg",
        trigger=(
            "Pixelate a face, a plate or a logo, for anonymity or a lo-fi game look."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "pixelize blocks the frame at a square size in pixels; the size is a "
            "float in the schema and the formatter emits it as a bare integer, "
            "which is what the filter demands"
        ),
    ),
    "swirl": EffectSpec(
        kind="swirl",
        family="geometry",
        backend="filter",
        params_spec=(
            ParamSpec(name="yaw", lo=-90.0, hi=90.0, default=20.0, step=1.0),
            ParamSpec(name="pitch", lo=-45.0, hi=45.0, default=10.0, step=1.0),
            ParamSpec(name="roll", lo=-180.0, hi=180.0, default=15.0, step=1.0),
        ),
        filter_template=(
            "v360=input=flat:output=flat:yaw={yaw}:pitch={pitch}:roll={roll}"
            ":h_fov=53.13:v_fov=53.13"
        ),
        trigger=(
            "Swing the frame through a 3D projection so it twists around its centre, for "
            "a disorienting beat."
        ),
        cap_seconds=0.0,
        fallback="skip",
        notes=(
            "v360 re-projects the frame on a rectilinear view and swings it on "
            "yaw/pitch/roll; it warps the picture rather than spinning it in "
            "plane (that is rotate), so the look is a projection swing, not a "
            "per-pixel spiral"
        ),
    ),
}

__all__ = ["ENTRIES"]
