"""vx.effects.restore — il passo di ripristino per il materiale da telefonino.

Nato dalla frase dell'utente «si vede che sono girati col cellulare»: un clip di
telefono non si scarta, si ripristina. Due voci, una per difetto, perché i due
difetti vogliono due gesti diversi.

Nessuna delle due inventa dettaglio: ``hqdn3d`` toglie la grana a bassa frequenza,
``unsharp`` alza il micro-contrasto di ciò che c'è, ``eq`` alza le ombre. Il
parametro è dichiarato con il suo dominio, quindi il piano può dire *quanto*
ripristino ha applicato e il golden prova che arriva ai pixel.

Sono voci statiche: il ripristino non si anima per keyframe, e il compilatore
rifiuta un'istanza animata con ``EFFECT_NOT_ANIMATABLE`` invece di scartare le
chiavi in silenzio.
"""

from __future__ import annotations

from typing import Dict

from ..effect_spec import EffectSpec, ParamSpec

ENTRIES: Dict[str, EffectSpec] = {
    "restore_soft": EffectSpec(
        kind="restore_soft",
        family="restore",
        backend="filter",
        params_spec=(
            ParamSpec(name="sharpen", lo=0.0, hi=1.5, default=0.6, step=0.05),
            ParamSpec(name="denoise", lo=0.0, hi=1.0, default=0.35, step=0.05),
        ),
        filter_template="hqdn3d={denoise}:{denoise}:{denoise}:{denoise},unsharp=5:5:{sharpen}",
        trigger=(
            "A phone clip whose measured sharpness is low but reliable: take the grain "
            "down and give the edges back the micro-contrast the sensor lost."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "hqdn3d first, unsharp second: sharpening before denoising would amplify the "
            "grain the clip already has. All four hqdn3d strengths carry the same value "
            "because the template substitutes numbers, not expressions, and a light pass "
            "is the point: it must not smooth skin"
        ),
    ),
    "restore_lowlight": EffectSpec(
        kind="restore_lowlight",
        family="restore",
        backend="filter",
        params_spec=(
            ParamSpec(name="lift", lo=0.0, hi=0.25, default=0.08, step=0.01),
            ParamSpec(name="sharpen", lo=0.0, hi=1.5, default=0.4, step=0.05),
        ),
        filter_template="eq=brightness={lift},unsharp=5:5:{sharpen}",
        trigger=(
            "A phone clip shot in the dark: lift the shadows without touching the black "
            "point, then give back a little edge."
        ),
        cap_seconds=0.0,
        fallback="static",
        notes=(
            "eq brightness, not a curve: it shifts the whole range by a fixed amount, so "
            "the high end clips sooner and the caller can measure it. The amount is a "
            "declared parameter, never a guess baked into the filter"
        ),
    ),
}
