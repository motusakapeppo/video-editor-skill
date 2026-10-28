"""vx.effect_spec — the declarative effect schema shared by the registry.

``EffectSpec`` describes one effect kind: the family it belongs to, the
mechanism that renders it, the parameters it accepts (:class:`ParamSpec`), and
— for the declarative ``filter`` backend — the ffmpeg filter template that
implements it.

The fields the EDL 3 contract already published are unchanged:
``params`` is still the tuple of accepted parameter names every caller reads
(``list(spec.params)``, ``name not in spec.params``), and ``animatable`` /
``animated_params`` / ``backend`` / ``notes`` / ``fallback`` keep their meaning.
For a declarative entry ``params`` is derived from ``params_spec`` when it is not
given, so the name list has one owner and cannot drift from the ranges.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

#: The backend string of a declarative ffmpeg filter template. It is declared
#: here because both the registry (``vx.edl3``) and the compiler
#: (``vx.edl3_render``) have to agree on it; each module exports it under the
#: same name and a test pins the two to one value.
BACKEND_FILTER = "filter"


@dataclass(frozen=True)
class ParamSpec:
    """One declared numeric parameter: its domain and the value used if absent."""

    name: str
    lo: float
    hi: float
    default: float
    #: Preferred granularity for a UI slider; 0.0 means "continuous".
    step: float = 0.0

    def contains(self, value: float) -> bool:
        """True when ``value`` lies inside the declared closed interval."""
        return self.lo <= value <= self.hi


@dataclass(frozen=True)
class EffectSpec:
    """One registry entry: what a kind accepts, and how it is rendered."""

    # --- the fields the EDL 3 contract published before F4-T01 --------------
    kind: str
    #: Accepted parameter names. Derived from ``params_spec`` when omitted.
    params: Tuple[str, ...] = ()
    animatable: bool = False
    animated_params: Tuple[str, ...] = ()
    backend: str = ""
    notes: str = ""
    #: The ffmpeg mechanism a ``chromium`` declaration falls back to when the
    #: node declares no property that genuinely needs a layout engine. Empty
    #: means "this kind has no ffmpeg fallback", so the node is refused.
    fallback: str = ""
    # --- declarative metadata (F4-T01) --------------------------------------
    #: Effect family: ``geometry``, ``color``, ``glitch``, ``film``,
    #: ``composite``, or ``core`` for the five hand-written kinds.
    family: str = ""
    #: Declared numeric parameters, in template order.
    params_spec: Tuple[ParamSpec, ...] = ()
    #: ffmpeg filter template; ``{name}`` refers to a declared parameter.
    filter_template: str = ""
    #: One sentence: when a planner should reach for this effect.
    trigger: str = ""
    #: Longest clip this effect is allowed to run on; 0.0 means "no cap".
    cap_seconds: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", str(self.kind))
        object.__setattr__(
            self, "params", tuple(str(name) for name in (self.params or ()))
        )
        object.__setattr__(
            self,
            "animated_params",
            tuple(str(name) for name in (self.animated_params or ())),
        )
        object.__setattr__(self, "params_spec", tuple(self.params_spec or ()))
        # One owner for the name list: a declarative entry never spells its
        # parameter names twice, and a legacy entry keeps the tuple it passed.
        if not self.params and self.params_spec:
            object.__setattr__(
                self, "params", tuple(param.name for param in self.params_spec)
            )
        object.__setattr__(self, "cap_seconds", float(self.cap_seconds))

    def param(self, name: str) -> Optional[ParamSpec]:
        """The declared specification of ``name``, or ``None`` when undeclared."""
        for param in self.params_spec:
            if param.name == name:
                return param
        return None

    def default_params(self) -> Dict[str, float]:
        """Every declared parameter at its default value."""
        return {param.name: param.default for param in self.params_spec}


__all__ = [
    "BACKEND_FILTER",
    "ParamSpec",
    "EffectSpec",
]
