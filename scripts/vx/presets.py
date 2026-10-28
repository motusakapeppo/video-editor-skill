"""vx.presets -- the declared look presets (F27-T02, D034).

A preset is a starting point, not a law (D034 #1). It declares a **base** -- the
concrete values for one effect kind -- plus the parameters a caller is allowed to
change, each with its type, its range *inside* the effect's own declared range,
and the base value as its default. Nothing here merges an override: resolving a
document against its preset is F27-T03. This module is data, lookup and
validation only -- no variation helper, no merge, no render.

Every row names an effect kind that exists in :data:`vx.edl3.EFFECT_REGISTRY` with
the declarative ``filter`` backend: a preset is a filter with values, never a
mechanism of its own. The five categories are the five decisions a director makes
on a shot:

``camera_move``     how the frame travels (zoompan, shake, whip_pan)
``camera_profile``  the tone response (curves: clean, filmic, log-like)
``lens``            the focal-length signature (fisheye width, bokeh length)
``palette``         the grade (teal-orange, bleach bypass, warm balance)
``light``           the light in the gate (golden-hour leak, overcast haze, night)

Import-cheap and side-effect free on purpose: nothing is validated at import.
:func:`check_all` is the test/tooling entry point and :func:`check` the per-row
one, so a registry edit cannot make an import fail -- it makes a test fail.

Refusal
-------
:func:`resolve` raises ``EDLError("PRESET_UNKNOWN", ...)`` naming every declared
id, the same shape :func:`vx.profiles.resolve` uses for ``PROFILE_UNKNOWN``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

from .edl3 import BACKEND_FILTER, EFFECT_REGISTRY, EDLError

__all__ = [
    "CATEGORIES",
    "PARAM_KINDS",
    "PresetParam",
    "Preset",
    "PRESETS",
    "names",
    "resolve",
    "by_category",
    "check",
    "check_all",
]

#: The five decision points a shot preset is filed under, in declaration order.
CATEGORIES = ("camera_move", "camera_profile", "lens", "palette", "light")
#: The value kinds an exposed parameter may declare.
PARAM_KINDS = frozenset({"number", "bool", "color", "string"})

#: An exposed parameter's value: a number, a boolean, or a string (a colour is a
#: ``#rrggbb`` string). The registry below only exposes numbers, but the schema
#: is total so that a later non-numeric effect needs no new type.
ParamValue = Union[float, bool, str]


@dataclass(frozen=True)
class PresetParam:
    """One parameter a preset exposes, with its domain and its base value.

    ``minimum``/``maximum`` are ``None`` for a kind that has no numeric domain
    (``bool``, ``color``, ``string``). ``default`` is the preset's own base value:
    a use that does not override the parameter therefore returns the declared
    base, never a second, drifting default. ``unit`` is ``""`` when the value has
    no unit (a zoom factor, a curve point), and a display unit otherwise.
    """

    name: str
    kind: str
    minimum: Optional[float]
    maximum: Optional[float]
    default: ParamValue
    unit: str = ""


@dataclass(frozen=True)
class Preset:
    """One declared look: a base, the knobs it exposes, and why the look works.

    ``effect`` is the registry kind the base instantiates, so a preset is only
    ever as real as the effect registry. ``base`` carries concrete values for
    that kind's declared parameters. ``notes`` is one sentence: what the look is,
    where the numbers come from, and that they are a starting point, not a law.
    """

    id: str
    category: str
    label: str
    effect: str
    base: Dict[str, float]
    exposed: Tuple[PresetParam, ...]
    notes: str


#: The registry, in declaration order (``names()`` returns it unchanged). Every
#: exposed parameter's default is its base value, and every range sits inside the
#: effect's own ``ParamSpec`` range -- :func:`check_all` is what proves it.
# allow: SIZE_OK -- the fifteen rows below ARE the deliverable (D034), every line
# a declared value rather than logic; the module's own code stays small.
PRESETS: Dict[str, Preset] = {
    # ------------------------------------------------------------ camera move
    "slow_push": Preset(
        id="slow_push",
        category="camera_move",
        label="Slow push",
        effect="zoompan",
        base={"zoom": 1.12},
        exposed=(PresetParam("zoom", "number", 1.02, 1.35, 1.12),),
        notes=(
            "A slow, almost imperceptible push into the frame at 1.12x, just under "
            "zoompan's 1.25 default so the crop still reads as a lens and not a "
            "digital zoom; the numbers are a starting point, not a law."
        ),
    ),
    "handheld_drift": Preset(
        id="handheld_drift",
        category="camera_move",
        label="Handheld drift",
        effect="shake",
        base={"amplitude": 6.0, "frequency": 0.9},
        exposed=(
            PresetParam("amplitude", "number", 3.0, 14.0, 6.0, "px"),
            PresetParam("frequency", "number", 0.4, 2.5, 0.9, "Hz"),
        ),
        notes=(
            "A tired shoulder-rig drift -- 6 px of sway at under one cycle per "
            "second, half of shake's 8 px / 3 Hz default; the numbers are a "
            "starting point, not a law."
        ),
    ),
    "whip_pan_cut": Preset(
        id="whip_pan_cut",
        category="camera_move",
        label="Whip pan",
        effect="whip_pan",
        base={"speed": 12.0, "window": 0.6},
        exposed=(
            PresetParam("speed", "number", 6.0, 20.0, 12.0, "/s"),
            PresetParam("window", "number", 0.5, 0.8, 0.6),
        ),
        notes=(
            "One fast lateral whip that crosses 12 window-widths per second (about "
            "80 ms to the edge), twice whip_pan's 6.0 default speed on a tighter 0.6 "
            "crop; the numbers are a starting point, not a law."
        ),
    ),
    # --------------------------------------------------------- camera profile
    "profile_clean": Preset(
        id="profile_clean",
        category="camera_profile",
        label="Clean profile",
        effect="curves",
        base={"mid": 0.5},
        exposed=(PresetParam("mid", "number", 0.45, 0.55, 0.5),),
        notes=(
            "A clean profile with no tone change at all -- the curve point at input "
            "0.5 maps back to 0.5, unlike curves' 0.36 default which densifies the "
            "mids; the numbers are a starting point, not a law."
        ),
    ),
    "profile_filmic": Preset(
        id="profile_filmic",
        category="camera_profile",
        label="Filmic profile",
        effect="curves",
        base={"mid": 0.44},
        exposed=(PresetParam("mid", "number", 0.36, 0.49, 0.44),),
        notes=(
            "A filmic profile that pulls input-0.5 down to 0.44, between curves' 0.36 "
            "default and neutral 0.5, adding gentle midtone density while the fixed "
            "0/0 and 1/1 endpoints hold black and white; the numbers are a starting "
            "point, not a law."
        ),
    ),
    "profile_log": Preset(
        id="profile_log",
        category="camera_profile",
        label="Log-like profile",
        effect="curves",
        base={"mid": 0.6},
        exposed=(PresetParam("mid", "number", 0.52, 0.68, 0.6),),
        notes=(
            "A log-like flat profile that lifts input-0.5 to 0.60, the mirror of the "
            "filmic row around neutral, opening the shadows for a grade; the numbers "
            "are a starting point, not a law."
        ),
    ),
    # ------------------------------------------------------------------- lens
    "lens_wide": Preset(
        id="lens_wide",
        category="lens",
        label="Wide lens",
        effect="fisheye",
        base={"strength": 0.65},
        exposed=(PresetParam("strength", "number", 0.4, 0.9, 0.65),),
        notes=(
            "A wide-angle bulge at 0.65 of fisheye's declared 1.0 ceiling, twice its "
            "0.35 default, for the edge stretch of a very short focal length; the "
            "numbers are a starting point, not a law."
        ),
    ),
    "lens_normal": Preset(
        id="lens_normal",
        category="lens",
        label="Normal lens",
        effect="fisheye",
        base={"strength": 0.06},
        exposed=(PresetParam("strength", "number", 0.05, 0.2, 0.06),),
        notes=(
            "A near-rectilinear normal lens: 0.06 is the smallest bulge the fisheye "
            "entry declares above its 0.05 floor, so straight lines stay straight and "
            "only the corners breathe; the numbers are a starting point, not a law."
        ),
    ),
    "lens_long": Preset(
        id="lens_long",
        category="lens",
        label="Long lens",
        effect="bokeh",
        base={"radius": 20.0, "power": 1.8},
        exposed=(
            PresetParam("radius", "number", 12.0, 24.0, 20.0, "px"),
            PresetParam("power", "number", 1.0, 2.5, 1.8),
        ),
        notes=(
            "A long-lens read from a heavy background blur, bokeh radius 20 (default "
            "10) at power 1.8, though the filter blurs the whole frame so it needs an "
            "isolated subject; the numbers are a starting point, not a law."
        ),
    ),
    # ---------------------------------------------------------------- palette
    "palette_teal_orange": Preset(
        id="palette_teal_orange",
        category="palette",
        label="Teal and orange",
        effect="teal_orange",
        base={"shadow": 0.32, "highlight": 0.3},
        exposed=(
            PresetParam("shadow", "number", 0.1, 0.45, 0.32),
            PresetParam("highlight", "number", 0.1, 0.45, 0.3),
        ),
        notes=(
            "The blockbuster split -- teal shadows at 0.32 and orange highlights at "
            "0.30 against teal_orange's 0.25 / 0.25 default, which keeps skin warm "
            "while the shadows cool; the numbers are a starting point, not a law."
        ),
    ),
    "palette_bleach": Preset(
        id="palette_bleach",
        category="palette",
        label="Bleach bypass",
        effect="bleach_bypass",
        base={"saturation": 0.22, "crush": 0.15, "lift": 0.86},
        exposed=(
            PresetParam("saturation", "number", 0.1, 0.5, 0.22),
            PresetParam("crush", "number", 0.1, 0.19, 0.15),
            PresetParam("lift", "number", 0.8, 0.92, 0.86),
        ),
        notes=(
            "A bleach-bypass war-film palette: colour drained to 0.22 and the curve "
            "crushed at 0.15 then driven to 0.86, harder than the entry's 0.35 / 0.13 "
            "/ 0.78 default; the numbers are a starting point, not a law."
        ),
    ),
    "palette_warm_film": Preset(
        id="palette_warm_film",
        category="palette",
        label="Warm film",
        effect="white_balance",
        base={"temperature": 7200.0},
        exposed=(PresetParam("temperature", "number", 5800.0, 9000.0, 7200.0, "K"),),
        notes=(
            "A warm film palette: 7200 K sits 2200 K above white_balance's 5000 K "
            "default and 700 K above the 6500 K identity, pushing skin and highlights "
            "amber; the numbers are a starting point, not a law."
        ),
    ),
    # ----------------------------------------------------------------- light
    "light_golden_hour": Preset(
        id="light_golden_hour",
        category="light",
        label="Golden hour",
        effect="light_leak",
        base={"strength": 0.45, "radius": 1.05},
        exposed=(
            PresetParam("strength", "number", 0.2, 0.7, 0.45),
            PresetParam("radius", "number", 0.6, 1.4, 1.05),
        ),
        notes=(
            "Golden hour: a warm corner leak at 0.45 strength and 1.05 radius, above "
            "light_leak's 0.35 / 0.9 default, as if the sun sat just outside the "
            "top-right of frame; the numbers are a starting point, not a law."
        ),
    ),
    "light_overcast": Preset(
        id="light_overcast",
        category="light",
        label="Overcast",
        effect="fog",
        base={"density": 0.1, "saturation": 0.62},
        exposed=(
            PresetParam("density", "number", 0.03, 0.2, 0.1),
            PresetParam("saturation", "number", 0.3, 0.8, 0.62),
        ),
        notes=(
            "An overcast key: a thin haze that lifts the black point to 0.10 and pulls "
            "colour to 0.62, softer than fog's 0.14 / 0.75 default, for flat white-sky "
            "light with no visible sun; the numbers are a starting point, not a law."
        ),
    ),
    "light_neon_night": Preset(
        id="light_neon_night",
        category="light",
        label="Neon night",
        effect="day_for_night",
        base={"brightness": -0.3, "blue": 0.45},
        exposed=(
            PresetParam("brightness", "number", -0.45, -0.15, -0.3),
            PresetParam("blue", "number", 0.2, 0.55, 0.45),
        ),
        notes=(
            "A neon night base: exposure down to -0.30 and blue shadows at 0.45, past "
            "day_for_night's -0.25 / 0.25 default, so saturated practicals read as the "
            "only light; the numbers are a starting point, not a law."
        ),
    ),
}


def names() -> Tuple[str, ...]:
    """Every declared preset id, in declaration order."""
    return tuple(PRESETS)


def resolve(name: str) -> Preset:
    """The preset called ``name``; an unknown name is refused with its ids."""
    key = str(name).strip()
    preset = PRESETS.get(key)
    if preset is None:
        raise EDLError(
            "PRESET_UNKNOWN",
            f"no look preset {key!r}; declared presets are {sorted(PRESETS)}",
            "preset_ref",
        )
    return preset


def by_category() -> Dict[str, Tuple[Preset, ...]]:
    """Every preset grouped under its category, in declaration order.

    The five :data:`CATEGORIES` are always present, even when a category has no
    row: a caller can iterate the taxonomy without checking for a missing key.
    """
    grouped: Dict[str, List[Preset]] = {category: [] for category in CATEGORIES}
    for preset in PRESETS.values():
        grouped.setdefault(preset.category, []).append(preset)
    return {category: tuple(rows) for category, rows in grouped.items()}


def check(preset: Preset) -> Tuple[str, ...]:
    """One human problem per way ``preset`` breaks the registry contract.

    It answers "is this row usable as a preset at all", so a hand-edited row is
    refused before a document can point at it: the effect must exist and be the
    ``filter`` backend; every base key must be a declared parameter of that
    effect with a value inside its ``ParamSpec``; every exposed parameter must be
    declared, must default to its base value inside its own exposed range, and
    that range must stay inside the effect's range; and a preset with nothing
    exposed cannot be varied, which is the "stamp" D034 refuses.
    """
    problems: List[str] = []

    def add(field: str, detail: str) -> None:
        problems.append(f"{preset.id}.{field}: {detail}")

    if preset.category not in CATEGORIES:
        add("category", f"{preset.category!r} is not one of {list(CATEGORIES)}")
    spec = EFFECT_REGISTRY.get(preset.effect)
    if spec is None:
        add("effect", f"unknown effect kind {preset.effect!r}")
        return tuple(problems)
    if spec.backend != BACKEND_FILTER:
        add(
            "effect",
            f"{preset.effect!r} is rendered by {spec.backend!r}, not the filter backend",
        )

    for name, value in preset.base.items():
        declared = spec.param(name)
        if declared is None:
            add(
                f"base.{name}",
                f"not a declared {preset.effect} parameter "
                f"(declared: {list(spec.params)})",
            )
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            add(f"base.{name}", f"{value!r} is not a number")
        elif not declared.contains(float(value)):
            add(
                f"base.{name}",
                f"{value!r} outside [{declared.lo:g}, {declared.hi:g}]",
            )

    if not preset.exposed:
        add("exposed", "no parameter is exposed, so the preset cannot be varied")
    for param in preset.exposed:
        where = f"exposed.{param.name}"
        declared = spec.param(param.name)
        if declared is None:
            add(where, f"not a declared {preset.effect} parameter")
            continue
        if param.kind not in PARAM_KINDS:
            add(f"{where}.kind", f"{param.kind!r} is not one of {sorted(PARAM_KINDS)}")
        if param.name not in preset.base:
            add(where, "has no base value to default to")
        elif param.default != preset.base[param.name]:
            add(
                f"{where}.default",
                f"{param.default!r} is not the base value "
                f"{preset.base[param.name]!r}",
            )
        if param.minimum is None or param.maximum is None:
            add(where, "a numeric exposure needs a minimum and a maximum")
            continue
        if param.minimum > param.maximum:
            add(
                where,
                f"minimum {param.minimum:g} is above maximum {param.maximum:g}",
            )
        if param.minimum < declared.lo or param.maximum > declared.hi:
            add(
                where,
                f"[{param.minimum:g}, {param.maximum:g}] escapes the declared "
                f"[{declared.lo:g}, {declared.hi:g}]",
            )
        if isinstance(param.default, (int, float)) and not isinstance(
            param.default, bool
        ):
            if not (param.minimum <= float(param.default) <= param.maximum):
                add(
                    f"{where}.default",
                    f"{param.default!r} outside the exposed "
                    f"[{param.minimum:g}, {param.maximum:g}]",
                )
    return tuple(problems)


def check_all() -> Dict[str, Tuple[str, ...]]:
    """Every declared preset's problems, keyed by id; an empty tuple is clean.

    Import stays free of this call on purpose: validating the whole registry is
    a test's job, so a bad row fails a test instead of an import.
    """
    report: Dict[str, Tuple[str, ...]] = {}
    for name, preset in PRESETS.items():
        problems = list(check(preset))
        if preset.id != name:
            problems.insert(0, f"{name}: row declares id {preset.id!r}")
        report[name] = tuple(problems)
    return report
