"""vx.effects — the declarative effect families.

Each family is a module that exports ``ENTRIES: dict[str, EffectSpec]``:

    geometry   framing warps that move the picture, not its colour
    color      grade / tone / keying adjustments
    glitch     deliberate signal damage
    film       stock, grain and gate looks
    composite  layering and alpha

:func:`families` imports the modules that exist and returns
``{family: {kind: spec}}``. A family module that has not been written yet is
skipped **silently** — only a ``ModuleNotFoundError`` whose ``exc.name`` is that
exact module counts as absent. A module that exists but fails to import (a
missing dependency, a syntax error, a bad name inside it) propagates, because a
broken family is a defect, never a quiet omission.
"""

from __future__ import annotations

import importlib
from typing import Dict

from ..effect_spec import EffectSpec

#: The families the loader looks for, in registry order.
FAMILY_MODULES = ("geometry", "color", "glitch", "film", "composite", "restore")


def families() -> Dict[str, Dict[str, EffectSpec]]:
    """``{family: {kind: EffectSpec}}`` for every family module that exists."""
    found: Dict[str, Dict[str, EffectSpec]] = {}
    for name in FAMILY_MODULES:
        module_name = f"{__name__}.{name}"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                continue
            raise
        found[name] = dict(module.ENTRIES)
    return found


def family_entries() -> Dict[str, EffectSpec]:
    """Every declared entry, flattened; a duplicate kind is a defect."""
    merged: Dict[str, EffectSpec] = {}
    owner: Dict[str, str] = {}
    for family, entries in families().items():
        for kind, spec in entries.items():
            if kind in merged:
                raise ValueError(
                    f"effect {kind!r} is declared by both {owner[kind]!r} and "
                    f"{family!r}; a kind has exactly one owner"
                )
            merged[kind] = spec
            owner[kind] = family
    return merged


# The composable layer lives next to the families: a spec is built from
# primitives, validated, costed and expanded from a historical name, while the
# family modules above stay the static registry. Re-exported here so callers can
# write ``from vx.effects import validate`` whether they compose or look up.
from .compose import (  # noqa: E402  (deliberately after the family loaders)
    CURVES,
    DEFAULT_DURATION,
    HISTORICAL_NAMES,
    MAX_COST,
    MAX_DURATION,
    MAX_PRIMITIVES,
    PRIMITIVES,
    TRANSITIONS,
    Issue,
    Param,
    Primitive,
    SpecError,
    canonical_easing,
    cost,
    describe,
    describe_primitive,
    expand,
    normalize,
    sample_curve,
    sample_param,
    transition,
    validate,
)

__all__ = [
    "FAMILY_MODULES",
    "families",
    "family_entries",
    "CURVES",
    "DEFAULT_DURATION",
    "HISTORICAL_NAMES",
    "MAX_COST",
    "MAX_DURATION",
    "MAX_PRIMITIVES",
    "PRIMITIVES",
    "TRANSITIONS",
    "Issue",
    "Param",
    "Primitive",
    "SpecError",
    "canonical_easing",
    "cost",
    "describe",
    "describe_primitive",
    "expand",
    "normalize",
    "sample_curve",
    "sample_param",
    "transition",
    "validate",
]
