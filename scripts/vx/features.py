"""vx.features — the declared feature flags of the toolkit.

A flag exists only if it is declared here, with a default, an owner and a
description. The same table is mirrored into ``settings.DEFAULTS["features"]``,
so the ordinary cascade reaches it (``--override features.stage_cache=on``) and
``--no-feature NAME`` has a bool to turn off. The effective values are written
into every ``auto_report.json`` under ``features``: a report always says which
optional paths were active when it produced its numbers.

A flag that changes the output must default to ``False``. The default run has to
be the run the documentation describes; an experimental path is asked for by
name, never inherited by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from . import contract


@dataclass(frozen=True)
class Feature:
    """One declared flag: what it means, who owns it, and how it defaults."""

    default: bool
    owner: str
    description: str


FEATURES: Dict[str, Feature] = {
    "stage_cache": Feature(
        default=False,
        owner="f0-stagecache",
        description="riusa gli stage di render identici fra due giri (F0-T04/T05)",
    ),
}

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def names() -> List[str]:
    """Every declared flag name, sorted."""
    return sorted(FEATURES)


def defaults() -> Dict[str, bool]:
    """The registry defaults, as the table ``settings.DEFAULTS`` carries."""
    return {name: feature.default for name, feature in FEATURES.items()}


def describe() -> Dict[str, Dict[str, Any]]:
    """The registry as plain data, for docs and MCP introspection."""
    return {
        name: {
            "default": feature.default,
            "owner": feature.owner,
            "description": feature.description,
        }
        for name, feature in FEATURES.items()
    }


def _as_bool(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    raise contract.VxError(
        f"feature {name!r}: valore non booleano {value!r} "
        f"(attesi: on/off, true/false, 1/0)"
    )


def validate(name: str) -> str:
    """Return the flag name, or fail naming the ones that exist."""
    if name not in FEATURES:
        raise contract.VxError(
            f"feature sconosciuta: {name!r}; valide: {', '.join(names())}"
        )
    return name


def validate_table(tree: Optional[Mapping[str, Any]]) -> Dict[str, bool]:
    """Check a settings tree's ``features`` table: known names, boolean values."""
    table = _table(tree)
    resolved: Dict[str, bool] = {}
    for name, value in table.items():
        validate(str(name))
        resolved[str(name)] = _as_bool(str(name), value)
    return resolved


def _table(tree: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    if tree is None:
        from . import settings

        table = settings.get("features", {})
    else:
        table = tree.get("features", {})
    return table if isinstance(table, Mapping) else {}


def effective(tree: Optional[Mapping[str, Any]] = None) -> Dict[str, bool]:
    """The value each declared flag actually has for this run.

    Undeclared keys in the table are ignored here: ``validate_table`` is the
    gate that reports a typo, and a report builder must not fail because a
    settings file carries a leftover from another version.
    """
    table = _table(tree)
    values: Dict[str, bool] = {}
    for name, feature in FEATURES.items():
        values[name] = _as_bool(name, table[name]) if name in table else feature.default
    return values


def enabled(name: str, tree: Optional[Mapping[str, Any]] = None) -> bool:
    """True only when the flag is declared and on; an unknown name is an error."""
    validate(name)
    return bool(effective(tree)[name])


def guard(
    name: str,
    action: Callable[[], Any],
    tree: Optional[Mapping[str, Any]] = None,
) -> Any:
    """Run ``action`` when the flag is on, return ``None`` when it is off."""
    if not enabled(name, tree):
        return None
    return action()


def apply_disables(tree: Dict[str, Any], disabled: Sequence[str]) -> Dict[str, Any]:
    """Turn named flags off in a settings tree, before it is applied.

    ``--no-feature`` wins over every lower layer, including an
    ``--override features.<name>=on``: it is the operator saying "not this run",
    and the effective report will show the off value it produced.
    """
    if not disabled:
        return tree
    table = tree.get("features")
    if not isinstance(table, dict):
        table = {}
        tree["features"] = table
    for name in disabled:
        table[validate(str(name))] = False
    return tree
