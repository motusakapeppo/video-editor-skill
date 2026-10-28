"""vx.presets -- the declared look presets (F27-T02, D034).

A preset is a starting point, not a law (D034 #1). It declares a **base** -- the
concrete values for one effect kind -- plus the parameters a caller is allowed to
change, each with its type, its range *inside* the effect's own declared range,
and the base value as its default. :func:`merge` turns one use of a preset into its
effective values (base plus override), :func:`effect_for` names the registry effect
those values instantiate, and :func:`difference` keeps only the values a use moved
away from the base -- the record a document stores (D034 #2). This module is data,
lookup, validation and that merge; it never renders and never varies on its own.

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
id, the same shape :func:`vx.profiles.resolve` uses for ``PROFILE_UNKNOWN``. The
merge refuses a use that breaks the schema instead of repairing it: a key the
preset does not expose is ``PARAM_NOT_EXPOSED``, a value outside the exposed range
is ``PARAM_OUT_OF_RANGE`` (never rounded into it), and a value of the wrong kind --
``True`` where a number is expected, or a number where a boolean is -- is
``PARAM_TYPE``.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from .edl3 import BACKEND_FILTER, EFFECT_REGISTRY, EDLError

__all__ = [
    "CATEGORIES",
    "PARAM_KINDS",
    "PresetParam",
    "Preset",
    "PRESETS",
    "names",
    "resolve",
    "merge",
    "effect_for",
    "difference",
    "variants",
    "describe",
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

    ``values`` is the closed set a ``color`` or ``string`` parameter may take: a
    variation has to pick one of them, so the declared set is the domain, the
    same way ``minimum``/``maximum`` is the domain of a number. It is empty for
    kinds with a numeric or boolean domain, and no declared preset uses it yet.
    """

    name: str
    kind: str
    minimum: Optional[float]
    maximum: Optional[float]
    default: ParamValue
    unit: str = ""
    values: Tuple[str, ...] = ()


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


def _type_problem(preset: Preset, param: PresetParam, value: Any) -> Optional[str]:
    """One human sentence when ``value`` is not a ``param.kind`` value, else ``None``.

    ``bool`` is never a number, even though Python says it is: a number knob is
    not satisfied by ``True`` and a boolean knob is not satisfied by ``1``. A
    ``color`` is judged by its type only -- no declared preset exposes one, and a
    ``#rrggbb`` shape is the palette's business, not the merge's.
    """
    got = type(value).__name__
    if param.kind == "bool":
        if isinstance(value, bool):
            return None
        return f"preset {preset.id!r} declares {param.name!r} as a boolean, got {got}"
    if param.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return (
                f"preset {preset.id!r} declares {param.name!r} as a number, got {got}"
            )
        if not math.isfinite(float(value)):
            return (
                f"preset {preset.id!r} declares {param.name!r} as a finite number, "
                f"got {value!r}"
            )
        return None
    if param.kind in ("color", "string"):
        if isinstance(value, str):
            return None
        return (
            f"preset {preset.id!r} declares {param.name!r} as a {param.kind}, "
            f"got {got}"
        )
    return (
        f"preset {preset.id!r} declares {param.name!r} with the unknown kind "
        f"{param.kind!r}"
    )


def _range_problem(preset: Preset, param: PresetParam, value: Any) -> Optional[str]:
    """One human sentence when ``value`` is outside the exposed range, else ``None``.

    The exposed range rules, not the effect's wider ``ParamSpec``: what a preset
    allows a caller to change is exactly what it exposes (D034 #1). Bounds are
    inclusive and nothing is rounded towards them.
    """
    if param.minimum is None or param.maximum is None:
        return None
    number = float(value)
    if param.minimum <= number <= param.maximum:
        return None
    unit = f" {param.unit}" if param.unit else ""
    return (
        f"preset {preset.id!r} exposes {param.name!r} in "
        f"[{param.minimum:g}, {param.maximum:g}]{unit}, got {value!r}"
    )


def merge(name: str, overrides: Mapping[str, Any]) -> Dict[str, Any]:
    """The effective values of one use of the preset ``name``.

    ``preset.base`` with ``overrides`` applied and nothing else: a key the preset
    does not declare in ``exposed`` is refused, never silently dropped; a value
    outside the exposed range is refused, never rounded into it; a value of the
    wrong kind is refused, never coerced. An empty ``overrides`` returns the base
    unchanged -- same keys, same order, same values -- so a use that changes
    nothing is exactly the declared preset.

    Pure and deterministic: the registry and its base dicts are never mutated, and
    the returned dict is a fresh copy whose key order is the base's.
    """
    preset = resolve(name)
    exposed = {param.name: param for param in preset.exposed}
    effective = dict(preset.base)
    for key, value in overrides.items():
        spot = f"exposed_params.{key}"
        param = exposed.get(key)
        if param is None:
            raise EDLError(
                "PARAM_NOT_EXPOSED",
                f"preset {preset.id!r} exposes no parameter {key!r} "
                f"(exposed: {sorted(exposed)})",
                spot,
            )
        problem = _type_problem(preset, param, value)
        if problem is not None:
            raise EDLError("PARAM_TYPE", problem, spot)
        problem = _range_problem(preset, param, value)
        if problem is not None:
            raise EDLError("PARAM_OUT_OF_RANGE", problem, spot)
        effective[key] = value
    return effective


def effect_for(name: str, overrides: Mapping[str, Any]) -> Dict[str, Any]:
    """The registry effect one use of ``name`` instantiates: its kind and values.

    ``{"kind": preset.effect, "params": merge(name, overrides)}``: the shape the
    compiler needs, with the preset's own effect kind, never a mechanism of its
    own.
    """
    preset = resolve(name)
    return {"kind": preset.effect, "params": merge(preset.id, overrides)}


def difference(name: str, overrides: Mapping[str, Any]) -> Dict[str, Any]:
    """Only the values ``overrides`` moves away from ``preset.base`` (D034 #2).

    This is the record a document keeps: a use that overrides nothing has an empty
    difference, and an override equal to the base is not a change. The merge's
    validation applies unchanged, so an unexposed key or an out-of-range value is
    refused here instead of quietly missing from the record. Keys come back in the
    base's declaration order, so the record is stable whatever order the overrides
    arrive in.
    """
    preset = resolve(name)
    effective = merge(preset.id, overrides)
    return {
        key: effective[key]
        for key, value in preset.base.items()
        if effective[key] != value
    }


def _format_value(value: Any) -> str:
    """One value as it reads in a report: ``1.31``, ``7200``, ``True``, ``#d94b2a``."""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _can_vary(preset: Preset, param: PresetParam) -> bool:
    """Whether ``param`` admits at least one value different from the base.

    A number needs a real interval ``minimum < maximum`` (or a bound that is not
    the base), a boolean needs a boolean base to invert, and a colour/string
    needs at least one declared value besides the base. Anything else cannot be
    moved and must not pretend to be a knob.
    """
    if param.name not in preset.base:
        return False
    base_value = preset.base[param.name]
    if param.kind == "number":
        if param.minimum is None or param.maximum is None:
            return False
        if param.minimum > param.maximum:
            return False
        return not (
            param.minimum == param.maximum
            and float(base_value) == float(param.minimum)
        )
    if param.kind == "bool":
        return isinstance(base_value, bool)
    if param.kind in ("color", "string"):
        return any(value != base_value for value in param.values)
    return False


def _sample(
    param: PresetParam, base_value: ParamValue, rng: random.Random
) -> Optional[ParamValue]:
    """One value of ``param`` inside its domain and different from ``base_value``.

    ``None`` only when the parameter cannot be moved at all. Numbers are drawn
    uniformly from the exposed ``[minimum, maximum]`` and kept to four decimals
    for readable reports; nothing is ever pushed back into range, a draw that
    lands on the base is simply redrawn, and the last resort is the bound the
    base is not, so the caller always gets a value it can merge.
    """
    if param.kind == "number":
        low, high = param.minimum, param.maximum
        if low is None or high is None or low > high:
            return None
        if low == high:
            return None if float(base_value) == float(low) else low
        for _ in range(64):
            value = rng.uniform(low, high)
            rounded = round(value, 4)
            if low <= rounded <= high:
                value = rounded
            if value != base_value:
                return value
        return low if float(low) != float(base_value) else high
    if param.kind == "bool":
        if not isinstance(base_value, bool):
            return None
        return not base_value
    if param.kind in ("color", "string"):
        options = [value for value in param.values if value != base_value]
        if not options:
            return None
        return rng.choice(options)
    return None


def _one_variant(
    preset: Preset, variable: List[PresetParam], rng: random.Random
) -> Dict[str, Any]:
    """One override dict: one or more movable keys, each moved off the base.

    The keys are chosen by the seeded generator and emitted in the preset's own
    exposed order, so the dict is stable and never carries an unchanged value.
    At least one key always survives, because a variant with no difference is
    not a variant.
    """
    names = [
        param.name
        for param in rng.sample(variable, rng.randint(1, len(variable)))
    ]
    variant: Dict[str, Any] = {}
    for param in preset.exposed:
        if param.name not in names:
            continue
        value = _sample(param, preset.base[param.name], rng)
        if value is None or value == preset.base[param.name]:
            continue
        variant[param.name] = value
    if not variant:
        param = variable[0]
        variant[param.name] = _sample(param, preset.base[param.name], rng)
    return variant


def variants(
    name: Union[str, Preset], seed: int, count: int = 3
) -> Tuple[Dict[str, Any], ...]:
    """``count`` deterministic override dicts for one preset (D034 #3).

    ``name`` is the preset id; a built :class:`Preset` is accepted too, so a row
    that is not registered (a test's ``dataclasses.replace`` copy) can still be
    varied. Each dict carries only the keys that moved off the base, so it is
    exactly what :func:`difference` records and what :func:`merge` accepts. A
    number stays inside its exposed range, a boolean is the base's opposite, and
    a colour or string is one of its declared values -- always different from the
    base. The variants of one call are distinct from each other while the
    parameter space allows it, without ever repeating an override dict.

    The generator is a private ``random.Random(seed)``: no module-level
    ``random`` state, no clock, no ``hash()`` of a name or an object, so the same
    seed prints the same variants whatever ``PYTHONHASHSEED`` is. Neither
    :data:`PRESETS` nor any base dict is read mutably or written.
    """
    preset = name if isinstance(name, Preset) else resolve(name)
    if count < 1:
        raise EDLError(
            "VARIANT_COUNT",
            f"preset {preset.id!r} needs at least one variant, got count={count!r}",
            "count",
        )
    if not preset.exposed:
        raise EDLError(
            "PRESET_NOT_VARIABLE",
            f"preset {preset.id!r} exposes no parameter, so it cannot be varied",
            "exposed",
        )
    variable = [param for param in preset.exposed if _can_vary(preset, param)]
    if not variable:
        raise EDLError(
            "PRESET_NOT_VARIABLE",
            f"preset {preset.id!r} exposes no parameter that can take a value "
            f"other than its base, so it cannot be varied",
            "exposed",
        )

    rng = random.Random(seed)
    produced: List[Dict[str, Any]] = []
    seen: List[Tuple[Tuple[str, Any], ...]] = []
    attempts = 0
    cap = max(64, count * 32)
    while len(produced) < count and attempts < cap:
        attempts += 1
        variant = _one_variant(preset, variable, rng)
        token = tuple(sorted(variant.items()))
        if token in seen:
            continue
        seen.append(token)
        produced.append(variant)
    # A space smaller than ``count`` (one boolean, one narrow set) still owes
    # the caller ``count`` usable variants; they repeat instead of growing.
    while len(produced) < count:
        produced.append(_one_variant(preset, variable, rng))
    return tuple(produced)


def describe(name: str, overrides: Mapping[str, Any]) -> str:
    """One line naming the preset and every key ``overrides`` moved off its base.

    ``describe("slow_push", {"zoom": 1.31})`` reads ``slow_push: zoom 1.12 ->
    1.31``. The merge's validation applies unchanged, so an unexposed key or an
    out-of-range value is refused here rather than missing from the summary; an
    override equal to the base is not a change and is not shown.
    """
    preset = resolve(name)
    changed = difference(preset.id, overrides)
    if not changed:
        return f"{preset.id}: base unchanged"
    moves = ", ".join(
        f"{key} {_format_value(preset.base[key])} -> {_format_value(value)}"
        for key, value in changed.items()
    )
    return f"{preset.id}: {moves}"


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
