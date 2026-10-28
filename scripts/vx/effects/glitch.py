"""vx.effects.glitch — the twelve deliberate signal-damage looks.

Every entry is a single ffmpeg 8.1 filter template, so ``animatable=False`` and
the compiler refuses a keyframed instance with ``EFFECT_NOT_ANIMATABLE`` rather
than silently dropping the keys.

A ``{name}`` placeholder refers to a declared :class:`vx.effect_spec.ParamSpec`;
the compiler substitutes the instance's static value, or the declared default
when the node omits it. What is *not* a placeholder is the free variable
``T`` inside a ``geq`` expression, or the frame history ``tmix``/``tblend``/
``lagfun`` read: those make a look move over time without declaring a
keyframe, which is exactly why the family stays ``animatable=False``. Commas
inside a single-quoted expression (``lum(X,Y)``, ``mod(...,W)``) stay inside the
quote and are not filter separators; every template below was rendered by the
real ffmpeg 8.1 binary before it was declared, and
``tests/test_effects_glitch.py`` renders them again.

The family is damage, not colour or framing: nothing here invents a picture the
source did not have, except the noise the ``noise`` filter adds.
"""

from __future__ import annotations

from typing import Dict

from ..effect_spec import EffectSpec, ParamSpec

ENTRIES: Dict[str, EffectSpec] = {
    "rgb_split": EffectSpec(
        kind="rgb_split",
        family="glitch",
        backend="filter",
        params_spec=(
            ParamSpec(name="offset_r", lo=-16.0, hi=16.0, default=6.0, step=1.0),
            ParamSpec(name="offset_b", lo=-16.0, hi=16.0, default=-6.0, step=1.0),
        ),
        filter_template="rgbashift=rh={offset_r}:bh={offset_b}:edge=wrap",
        trigger=(
            "Split the red and blue planes apart on a hit or a dropped signal, so the "
            "frame reads as mis-converged."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "rgbashift moves the red plane right by offset_r and the blue plane right "
            "by offset_b (a negative value moves it left), leaving green in place; the "
            "defaults 6/-6 are the classic opposite-direction split, and edge=wrap "
            "re-enters from the other side instead of smearing the border pixels"
        ),
    ),
    "chromatic_aberration": EffectSpec(
        kind="chromatic_aberration",
        family="glitch",
        backend="filter",
        params_spec=(
            ParamSpec(name="chroma_r", lo=-16.0, hi=16.0, default=4.0, step=1.0),
            ParamSpec(name="chroma_b", lo=-16.0, hi=16.0, default=-4.0, step=1.0),
        ),
        filter_template=(
            "chromashift=crh={chroma_r}:cbh={chroma_b}:edge=wrap"
        ),
        trigger=(
            "Fringe the colour around edges like a bad lens or a cheap capture, without "
            "touching the luma detail."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "chromashift offsets the two chroma planes in opposite directions and "
            "leaves the luma plane sharp, so the result is coloured fringing rather "
            "than the plane split rgb_split makes; chroma is already half resolution, "
            "so one step of these offsets is two luma pixels of fringe"
        ),
    ),
    "scanlines": EffectSpec(
        kind="scanlines",
        family="glitch",
        backend="filter",
        params_spec=(
            ParamSpec(name="period", lo=2.0, hi=16.0, default=4.0, step=1.0),
            ParamSpec(name="depth", lo=0.1, hi=1.0, default=0.6, step=0.05),
            ParamSpec(name="speed", lo=0.0, hi=2.0, default=0.25, step=0.05),
        ),
        filter_template=(
            "geq=lum='lum(X,Y)*(1-{depth}*max(0,sin(2*PI*(Y/{period}-{speed}*T))))'"
        ),
        trigger=(
            "Lay a CRT raster over the frame, for a monitor, a security camera or a "
            "retro screen insert."
        ),
        cap_seconds=8.0,
        fallback="skip",
        notes=(
            "geq darkens every row by the positive half of a sine whose period is "
            "{period} pixels, and T drifts the phase so the raster crawls slowly; it "
            "is a per-pixel read, so it is capped at 8s and skipped past the cap "
            "instead of being paid for on a long clip"
        ),
    ),
    "vhs": EffectSpec(
        kind="vhs",
        family="glitch",
        backend="filter",
        params_spec=(
            ParamSpec(name="grain", lo=0.0, hi=60.0, default=18.0, step=1.0),
            ParamSpec(name="shift", lo=0.0, hi=16.0, default=6.0, step=1.0),
        ),
        filter_template="noise=alls={grain}:allf=t+u,chromashift=cbh={shift}:cbv={shift}",
        trigger=(
            "Make a clean clip look like a worn tape: luma/chroma noise plus the chroma "
            "lag of a mis-tracking head."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "temporal uniform noise (allf=t+u) supplies the tape grain and chromashift "
            "drags both chroma planes by {shift} pixels diagonally, which is the colour "
            "bleed a VHS deck leaves; no head-switching tear is claimed, that needs a "
            "row-wise time offset this single chain does not declare"
        ),
    ),
    "glitch_blocks": EffectSpec(
        kind="glitch_blocks",
        family="glitch",
        backend="filter",
        params_spec=(
            ParamSpec(name="block", lo=4.0, hi=128.0, default=24.0, step=4.0),
            ParamSpec(name="seed", lo=0.0, hi=999.0, default=7.0, step=1.0),
        ),
        filter_template=(
            "shufflepixels=mode=block:width={block}:height={block}:seed={seed}"
        ),
        trigger=(
            "Scramble the frame in square blocks, for a corrupted-file or a "
            "broken-decoder beat."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "shufflepixels permutes {block}x{block} tiles; the same seed keeps the "
            "permutation reproducible while the filter still advances its generator "
            "every frame, so consecutive frames scramble differently instead of "
            "freezing on one pattern"
        ),
    ),
    "datamosh": EffectSpec(
        kind="datamosh",
        family="glitch",
        backend="filter",
        params_spec=(
            ParamSpec(name="block", lo=8.0, hi=96.0, default=32.0, step=8.0),
            ParamSpec(name="frames", lo=2.0, hi=8.0, default=3.0, step=1.0),
        ),
        filter_template=(
            "shufflepixels=mode=block:width={block}:height={block}:seed=5"
            ",tmix=frames={frames}"
        ),
        trigger=(
            "Suggest a broken codec where blocks carry stale motion, for a "
            "corrupted-footage montage."
        ),
        cap_seconds=6.0,
        fallback="static",
        notes=(
            "this is a filter-level APPROXIMATION, not datamoshing: shufflepixels "
            "displaces {block}-pixel tiles and tmix averages the last {frames} frames "
            "over the moving blocks so they drag their own past with them. It does not "
            "touch the bitstream - it cannot drop I-frames, cannot duplicate P-frames "
            "and cannot re-key a packet; a true datamosh needs container-level packet "
            "surgery outside the filter graph. Capped at 6s because tmix holds "
            "{frames} frames of history in memory"
        ),
    ),
    "echo": EffectSpec(
        kind="echo",
        family="glitch",
        backend="filter",
        params_spec=(
            ParamSpec(name="opacity", lo=0.05, hi=0.95, default=0.45, step=0.05),
        ),
        filter_template="tblend=all_mode=addition:all_opacity={opacity}",
        trigger=(
            "Print a bright copy of the previous frame over the current one, for motion "
            "trails on a fast move."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "tblend adds the previous frame to this one at all_opacity, so bright "
            "subject matter leaves a one-frame ladder of echoes; the trail is one frame "
            "long by design - chaining more tblend passes would stack more copies but "
            "the template declares exactly one"
        ),
    ),
    "ghosting": EffectSpec(
        kind="ghosting",
        family="glitch",
        backend="filter",
        params_spec=(
            ParamSpec(name="decay", lo=0.5, hi=0.99, default=0.95, step=0.01),
        ),
        filter_template="lagfun=decay={decay}",
        trigger=(
            "Leave a fading dark afterimage of whatever moves, for a haunted or a "
            "long-exposure beat."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "lagfun only updates pixels that get darker and keeps the rest at decay of "
            "their previous value, so the ghost is a dark, decaying afterimage rather "
            "than the bright ladder echo leaves; decay closer to 1 means a longer trail"
        ),
    ),
    "motion_blur": EffectSpec(
        kind="motion_blur",
        family="glitch",
        backend="filter",
        params_spec=(
            ParamSpec(name="frames", lo=2.0, hi=12.0, default=5.0, step=1.0),
        ),
        filter_template="tmix=frames={frames}",
        trigger=(
            "Smear fast motion over several frames like a long shutter, for a strobe or "
            "a whip."
        ),
        cap_seconds=8.0,
        fallback="static",
        notes=(
            "tmix averages the last {frames} frames with equal weights, which is a box "
            "shutter: the trail length is {frames} frames at the timeline rate, and the "
            "first {frames} - 1 frames of a clip show less trail because the history is "
            "not full yet; capped at 8s so the history buffer stays small"
        ),
    ),
    "smear": EffectSpec(
        kind="smear",
        family="glitch",
        backend="filter",
        params_spec=(
            ParamSpec(name="sigma", lo=4.0, hi=64.0, default=20.0, step=1.0),
        ),
        filter_template="gblur=sigma={sigma}:sigmaV=0",
        trigger=(
            "Drag the frame sideways in one direction only, for speed lines or a "
            "scanning-head smear."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "gblur with sigmaV=0 keeps the vertical sigma at zero and applies sigma "
            "horizontally only, so the picture smears left-right while its rows stay "
            "crisp; a symmetric blur would need sigmaV left at -1, which is the "
            "opposite of the look"
        ),
    ),
    "slit_scan": EffectSpec(
        kind="slit_scan",
        family="glitch",
        backend="filter",
        params_spec=(
            ParamSpec(name="shear", lo=0.0, hi=8.0, default=3.0, step=0.5),
            ParamSpec(name="speed", lo=0.0, hi=2.0, default=0.5, step=0.05),
        ),
        filter_template="geq=lum='lum(mod(X+{shear}*Y+{speed}*W*T,W),Y)'",
        trigger=(
            "Skew the picture row by row and drift the skew, for a rolling-shutter or a "
            "photocopier tear."
        ),
        cap_seconds=8.0,
        fallback="skip",
        notes=(
            "geq reads each row from a horizontal position sheared by {shear}*Y and "
            "drifts that shear by {speed}*W*T, so rows slide across the frame over "
            "time and the wrap at W keeps the read in bounds. This is a row-dependent "
            "warp, NOT a true slit scan: a real slit scan samples every row at a "
            "different moment in time, which a single filter chain cannot do. Capped "
            "at 8s because geq is a per-pixel read"
        ),
    ),
    "interference": EffectSpec(
        kind="interference",
        family="glitch",
        backend="filter",
        params_spec=(
            ParamSpec(name="grain", lo=0.0, hi=40.0, default=12.0, step=1.0),
            ParamSpec(name="depth", lo=0.1, hi=0.9, default=0.6, step=0.05),
        ),
        filter_template=(
            "noise=alls={grain}:allf=t"
            ",geq=lum='lum(X,Y)*(1+{depth}*sin(2*PI*(Y/56-3*T)))'"
        ),
        trigger=(
            "Roll a wide bright band across the picture and sprinkle fresh noise on it, "
            "for a broken-transmission beat."
        ),
        cap_seconds=8.0,
        fallback="skip",
        notes=(
            "temporal uniform noise at {grain} supplies the sparkle and geq multiplies "
            "luma by a rolling 56-pixel sine band at {depth} that travels three cycles "
            "per second, so the band is far wider and much faster than scanlines; the "
            "band period and rate are fixed in the template because the node has no "
            "parameter for them. Capped at 8s because geq is a per-pixel read"
        ),
    ),
}

__all__ = ["ENTRIES"]
