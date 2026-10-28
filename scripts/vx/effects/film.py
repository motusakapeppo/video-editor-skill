"""vx.effects.film â€” the twelve stock, grain and gate looks.

Every entry is one static linear ffmpeg filter chain: ``backend="filter"``,
``animatable=False``. The compiler's filter stage substitutes the instance's
static parameters, runs the chain, then re-stamps the rate and crops back to the
clip canvas, so no film look can change a clip's duration or deliverable size.

A single chain cannot read the frame twice. Every effect whose *physical*
mechanism is two-pass (a highlight bloom, a disc-shaped bokeh, a lens flare with
its ghost train) is therefore approximated here with the closest honest one-pass
chain, and ``notes`` states exactly what is missing. The additive looks
(``light_leak``, ``lens_flare``) are built with ``geq``, which can compute a new
value from the current pixel and its coordinates but cannot sample a neighbour.
"""

from __future__ import annotations

from typing import Dict

from ..effect_spec import EffectSpec, ParamSpec

ENTRIES: Dict[str, EffectSpec] = {
    # --- grain and gate ------------------------------------------------------
    "grain": EffectSpec(
        kind="grain",
        family="film",
        backend="filter",
        params_spec=(
            ParamSpec(name="strength", lo=0.0, hi=100.0, default=14.0, step=1.0),
        ),
        filter_template="noise=alls={strength}:allf=t+u",
        trigger=(
            "Give a clean digital frame the texture of a pushed film stock, or hide banding "
            "in a gradient."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "noise with temporal and uniform flags: every frame gets a fresh grain "
            "field instead of a frozen pattern, so strength reads as film grain and "
            "not as a static texture; it is a real one-pass filter, nothing is "
            "approximated"
        ),
    ),
    "vignette": EffectSpec(
        kind="vignette",
        family="film",
        backend="filter",
        params_spec=(
            ParamSpec(name="angle", lo=0.1, hi=1.5, default=0.6, step=0.05),
        ),
        filter_template="vignette=angle={angle}",
        trigger=(
            "Darken the corners of a frame to pull the eye to the centre, the gate look of "
            "an older lens."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "vignette in its forward mode; angle is the aperture angle in radians "
            "and a larger angle pushes the falloff further into the frame; it is a "
            "real one-pass filter, nothing is approximated"
        ),
    ),
    # --- highlight diffusion -------------------------------------------------
    "halation": EffectSpec(
        kind="halation",
        family="film",
        backend="filter",
        params_spec=(
            ParamSpec(name="knee", lo=0.7, hi=1.0, default=0.88, step=0.01),
            ParamSpec(name="cool", lo=0.4, hi=0.7, default=0.55, step=0.01),
        ),
        filter_template=(
            "curves=r='0/0 0.7/{knee} 1/1':b='0/0 0.7/{cool} 1/1'"
        ),
        trigger=(
            "Warm the highlights of a frame the way a film base bleeds light back into the "
            "red layer, for a period night look."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "approximation: real halation is a spatial spread of bright areas into a "
            "red-orange halo, which needs a split/blur/blend graph; this chain only "
            "re-maps the tone curve in place (red lifted above 0.7, blue pulled "
            "down), so highlights read warm but nothing bleeds outside the bright "
            "pixel itself"
        ),
    ),
    "glow": EffectSpec(
        kind="glow",
        family="film",
        backend="filter",
        params_spec=(
            ParamSpec(name="sigma", lo=0.5, hi=12.0, default=4.0, step=0.5),
            ParamSpec(name="lift", lo=0.5, hi=1.0, default=0.62, step=0.01),
        ),
        filter_template="gblur=sigma={sigma},curves=all='0/0 0.5/{lift} 1/1'",
        trigger=(
            "Soften a frame into a luminous haze, for a memory, a dream or a stage light "
            "flare."
        ),
        cap_seconds=8.0,
        fallback="static",
        notes=(
            "approximation: a true glow is the untouched frame plus its blurred "
            "highlight pass, which needs two branches and a blend; this chain blurs "
            "the whole frame once and lifts the midtones so the haze reads as light, "
            "losing the sharp original underneath"
        ),
    ),
    "soft_focus": EffectSpec(
        kind="soft_focus",
        family="film",
        backend="filter",
        params_spec=(
            ParamSpec(name="sigma", lo=0.2, hi=8.0, default=2.5, step=0.1),
        ),
        filter_template="gblur=sigma={sigma}:steps=1",
        trigger=(
            "Take the edge off a frame for a romantic or interview look without losing the "
            "subject entirely."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "one gaussian step: a plain diffusion, which is what a soft-focus filter "
            "does optically; it carries no highlight bloom, so it is exact for a "
            "diffusion and not a stand-in for glow"
        ),
    ),
    "bokeh": EffectSpec(
        kind="bokeh",
        family="film",
        backend="filter",
        params_spec=(
            ParamSpec(name="radius", lo=1.0, hi=24.0, default=10.0, step=1.0),
            ParamSpec(name="power", lo=1.0, hi=3.0, default=1.0, step=1.0),
        ),
        filter_template="boxblur=luma_radius={radius}:luma_power={power}",
        trigger=(
            "Throw a background out of focus with a broad, soft falloff for a portrait or "
            "a product shot."
        ),
        cap_seconds=8.0,
        fallback="static",
        notes=(
            "approximation: an optical bokeh is a disc-shaped kernel that keeps a "
            "point light as a disc; this is a box blur, so highlights smear into a "
            "square patch and the kernel is uniform rather than aperture shaped"
        ),
    ),
    # --- light in the gate ---------------------------------------------------
    "light_leak": EffectSpec(
        kind="light_leak",
        family="film",
        backend="filter",
        params_spec=(
            ParamSpec(name="strength", lo=0.0, hi=1.0, default=0.35, step=0.05),
            ParamSpec(name="radius", lo=0.3, hi=1.5, default=0.9, step=0.05),
        ),
        filter_template=(
            "geq=r='clip(r(X,Y)+{strength}*255*max(0,1-hypot(X-W,Y)"
            "/(max(W,H)*{radius})),0,255)'"
            ":g='clip(g(X,Y)+{strength}*0.45*255*max(0,1-hypot(X-W,Y)"
            "/(max(W,H)*{radius})),0,255)'"
            ":b='clip(b(X,Y)+{strength}*0.12*255*max(0,1-hypot(X-W,Y)"
            "/(max(W,H)*{radius})),0,255)'"
        ),
        trigger=(
            "Wash a warm leak of light in from a corner, for a film-camera or home-movie "
            "feel."
        ),
        cap_seconds=6.0,
        fallback="skip",
        notes=(
            "a real additive corner gradient computed per pixel by geq: the frame is "
            "kept and a warm radial falloff is added to red, some green and a little "
            "blue, which is exactly how a static leak behaves; it does not flicker "
            "over time (a leak's shimmer would need a temporal term) and it is "
            "capped and skipped past the cap because geq is a per-pixel read"
        ),
    ),
    "lens_flare": EffectSpec(
        kind="lens_flare",
        family="film",
        backend="filter",
        params_spec=(
            ParamSpec(name="strength", lo=0.0, hi=1.0, default=0.35, step=0.05),
            ParamSpec(name="size", lo=0.05, hi=0.4, default=0.12, step=0.01),
        ),
        filter_template=(
            "geq=r='clip(r(X,Y)+{strength}*255*(max(0,1-hypot(X-0.85*W,Y-0.12*H)"
            "/({size}*max(W,H)))+0.35*max(0,1-hypot(X-0.45*W,Y-0.58*H)"
            "/(0.28*max(W,H)))),0,255)'"
            ":g='clip(g(X,Y)+{strength}*245*(max(0,1-hypot(X-0.85*W,Y-0.12*H)"
            "/({size}*max(W,H)))+0.35*max(0,1-hypot(X-0.45*W,Y-0.58*H)"
            "/(0.28*max(W,H)))),0,255)'"
            ":b='clip(b(X,Y)+{strength}*210*(max(0,1-hypot(X-0.85*W,Y-0.12*H)"
            "/({size}*max(W,H)))+0.35*max(0,1-hypot(X-0.45*W,Y-0.58*H)"
            "/(0.28*max(W,H)))),0,255)'"
        ),
        trigger=(
            "Add a hot flare and its ghost where a light source hits the lens, for a "
            "concert or a sunlit exterior."
        ),
        cap_seconds=6.0,
        fallback="skip",
        notes=(
            "approximation: two additive geq discs, a hot core in the upper right and "
            "a faint ghost near the centre, which is the additive half of a flare; "
            "the anamorphic streak, the aperture blades and the occlusion of the "
            "source are missing because a single chain cannot sample or mask; "
            "capped and skipped because geq is a per-pixel read"
        ),
    ),
    # --- atmosphere and stock ------------------------------------------------
    "fog": EffectSpec(
        kind="fog",
        family="film",
        backend="filter",
        params_spec=(
            ParamSpec(name="density", lo=0.0, hi=0.35, default=0.14, step=0.01),
            ParamSpec(name="saturation", lo=0.0, hi=1.0, default=0.75, step=0.05),
        ),
        filter_template=(
            "curves=all='0/{density} 0.5/0.62 1/1',eq=saturation={saturation}"
        ),
        trigger=(
            "Put a low-contrast haze over a scene to sell distance, an overcast morning or "
            "a smoky room."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "a haze grade and nothing more: the curve lifts the black point to "
            "density, flattens the mids and eq pulls the colour down, which is what "
            "distance in air does to a picture; there is no depth-dependent falloff "
            "(that would need a depth map) and no volumetric light"
        ),
    ),
    "dreamcore": EffectSpec(
        kind="dreamcore",
        family="film",
        backend="filter",
        params_spec=(
            ParamSpec(name="softness", lo=0.0, hi=6.0, default=2.0, step=0.2),
            ParamSpec(name="lift", lo=0.0, hi=0.3, default=0.12, step=0.01),
            ParamSpec(name="saturation", lo=0.4, hi=1.4, default=0.85, step=0.05),
        ),
        filter_template=(
            "gblur=sigma={softness},curves=all='0/{lift} 0.5/0.66 0.9/0.97 1/1'"
            ",eq=saturation={saturation}"
        ),
        trigger=(
            "Push a frame into a pastel, airy half-dream, for a memory insert or a "
            "whimsical beat."
        ),
        cap_seconds=8.0,
        fallback="static",
        notes=(
            "the chain is literally the look: a small gaussian diffusion, then a "
            "curve that lifts the shadows and rolls the highlights, then a slight "
            "desaturation; the pastel airiness is a tone map here, so there is no "
            "bloom and no added light (both would need a second branch)"
        ),
    ),
    # --- alternate stocks ----------------------------------------------------
    "x_ray": EffectSpec(
        kind="x_ray",
        family="film",
        backend="filter",
        params_spec=(
            ParamSpec(name="contrast", lo=1.0, hi=2.5, default=1.5, step=0.05),
            ParamSpec(name="brightness", lo=-0.1, hi=0.3, default=0.06, step=0.01),
            ParamSpec(name="blue", lo=0.0, hi=0.6, default=0.25, step=0.05),
        ),
        filter_template=(
            "hue=s=0,negate,eq=contrast={contrast}:brightness={brightness}"
            ",colorbalance=bs={blue}:bm={blue}"
        ),
        trigger=(
            "Turn a frame into a blue negative readout, for a medical, sci-fi or "
            "surveillance insert."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "desaturate, negate, then contrast and a blue lift: the density "
            "inversion of an X-ray plate with the blue cast of its film; it is a "
            "tone and channel treatment only, so there is no edge enhancement "
            "toward bone density and no real radiological falloff"
        ),
    ),
    "infrared": EffectSpec(
        kind="infrared",
        family="film",
        backend="filter",
        params_spec=(
            ParamSpec(name="red", lo=0.3, hi=1.0, default=0.72, step=0.01),
            ParamSpec(name="green", lo=0.3, hi=1.0, default=0.85, step=0.01),
            ParamSpec(name="blue", lo=0.05, hi=0.5, default=0.22, step=0.01),
        ),
        filter_template=(
            "curves=r='0/0 0.5/{red} 1/1':g='0/0 0.5/{green} 1/1'"
            ":b='0/0 0.5/{blue} 1/1'"
        ),
        trigger=(
            "Re-map the frame into the false colour of infrared film, where foliage glows "
            "and skies go dark."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "a per-channel tone curve: greens are lifted hardest, red less, blue "
            "pressed down, which is the classic false-colour IR mapping; it is not a "
            "sensor response (true IR needs the wavelengths the camera threw away), "
            "so it is an emulation of the stock's look, exact as a curve map"
        ),
    ),
}

__all__ = ["ENTRIES"]
