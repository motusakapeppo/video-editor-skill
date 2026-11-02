"""vx.lenses — le lenti come schede: schema, tetto, ripiego dichiarato.

Una lente non e' un look: e' un difetto ottico controllato. Quattro voci, ognuna
con i suoi parametri e il suo dominio, appoggiate agli effetti che il compilatore
sa gia' rendere:

    vignette              angle             (film.vignette)
    chromatic_aberration  chroma_r, chroma_b (glitch.chromatic_aberration)
    bokeh                 radius, power     (film.bokeh)
    fisheye               strength          (geometry.fisheye)

Due regole che il codice impone:

* il **bokeh senza mappa di profondita'** non e' un errore non gestito: e' il
  ripiego dichiarato -- il blur mascherato al centro, con ``fallback=True`` e il
  motivo nel referto;
* due lenti in pila sono legittime (una vignetta piu' un'aberrazione), **due
  distorsioni no**: la seconda e' rifiutata con ``LENS_CONFLICT`` e i due nomi.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .contract import VxError
from .edl3 import Effect


class LensError(VxError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Limit:
    name: str
    lo: float
    hi: float
    default: float

    def contains(self, value: float) -> bool:
        return self.lo <= float(value) <= self.hi


@dataclass(frozen=True)
class Lens:
    """One optical defect: what it does, how far it may go, what it needs."""

    name: str
    effect: str
    params: Tuple[Limit, ...]
    trigger: str
    cap_seconds: float = 20.0
    #: A lens that warps geometry; two of them on a clip is a hall of mirrors.
    distortion: bool = False
    #: A lens that needs a depth map to be what it claims to be.
    needs_depth: bool = False
    fallback_effect: str = ""
    fallback_why: str = ""

    def limit(self, name: str) -> Optional[Limit]:
        for item in self.params:
            if item.name == name:
                return item
        return None


LENSES: Dict[str, Lens] = {
    "vignette": Lens(
        name="vignette",
        effect="vignette",
        params=(Limit("angle", 0.1, 1.5, 0.6),),
        trigger="Chiudi i bordi per tenere l'occhio al centro: un ritratto, un dettaglio.",
    ),
    "chromatic_aberration": Lens(
        name="chromatic_aberration",
        effect="chromatic_aberration",
        params=(Limit("chroma_r", 0.0, 8.0, 2.0), Limit("chroma_b", 0.0, 8.0, 2.0)),
        trigger="Sfasa i canali sui bordi: un look da lente economica o da segnale analogico.",
    ),
    "bokeh": Lens(
        name="bokeh",
        effect="bokeh",
        params=(Limit("radius", 1.0, 40.0, 8.0), Limit("power", 1.0, 4.0, 2.0)),
        trigger="Sfoca lo sfondo tenendo il soggetto: separazione senza tagliare.",
        needs_depth=True,
        fallback_effect="soft_focus",
        fallback_why=(
            "senza una mappa di profondita' non si sa cosa e' sfondo: si applica un "
            "soft focus uniforme e lo si dichiara, invece di sfocare il volto"
        ),
    ),
    "fisheye": Lens(
        name="fisheye",
        effect="fisheye",
        params=(Limit("strength", 0.0, 1.0, 0.35),),
        trigger="Distorsione grandangolare spinta: spazio che si apre, soggetto al centro.",
        distortion=True,
    ),
}

FAMILY = tuple(sorted(LENSES))
MAX_STACK = 3


def require(name: str) -> Lens:
    lens = LENSES.get(str(name))
    if lens is None:
        raise LensError(
            "LENS_UNKNOWN",
            f"unknown lens {name!r}; declared: {sorted(LENSES)}",
        )
    return lens


def validate() -> List[str]:
    found: List[str] = []
    for name, lens in LENSES.items():
        if name != lens.name:
            found.append(f"{name}: key and name disagree")
        if not lens.trigger:
            found.append(f"{name}: no trigger")
        if not lens.params:
            found.append(f"{name}: no parameters")
        for limit in lens.params:
            if not limit.contains(limit.default):
                found.append(
                    f"{name}.{limit.name}: default {limit.default} outside "
                    f"[{limit.lo}, {limit.hi}]"
                )
        if lens.needs_depth and not lens.fallback_effect:
            found.append(f"{name}: needs depth but declares no fallback")
    return found


def stack(names: List[str]) -> List[Lens]:
    """Three lenses may ride a clip; two distortions never do."""
    resolved = [require(name) for name in names]
    if len(resolved) > MAX_STACK:
        raise LensError(
            "STACK_TOO_DEEP",
            f"{len(resolved)} lenses on one clip ({', '.join(l.name for l in resolved)}); "
            f"the declared maximum is {MAX_STACK}",
        )
    warps = [lens.name for lens in resolved if lens.distortion]
    if len(warps) > 1:
        raise LensError(
            "LENS_CONFLICT",
            f"two distortion lenses on one clip: {warps[0]!r} and {warps[1]!r}; "
            "a frame cannot be warped two ways",
        )
    return resolved


def build(
    name: str,
    *,
    depth_map: Optional[str] = None,
    **params: float,
) -> Dict[str, object]:
    """The declared chain for one lens.

    Returns ``{"effects": [Effect], "fallback": bool, "why": str}``. A bokeh
    without a depth map is *not* an exception: it comes back as the declared
    fallback, with the reason, so the report can say what reached the frame.
    """
    lens = require(name)
    unknown = [key for key in params if lens.limit(key) is None]
    if unknown:
        raise LensError(
            "LENS_PARAM",
            f"{lens.name} does not declare {unknown[0]!r}; it declares "
            f"{sorted(limit.name for limit in lens.params)}",
        )
    values: Dict[str, float] = {}
    for limit in lens.params:
        value = float(params.get(limit.name, limit.default))
        if not limit.contains(value):
            raise LensError(
                "LENS_RANGE",
                f"{lens.name}.{limit.name} = {value} outside [{limit.lo}, {limit.hi}]",
            )
        values[limit.name] = value

    if lens.needs_depth and not depth_map:
        fallback = lens.fallback_effect
        return {
            "effects": [
                Effect(
                    kind=fallback,
                    params={"sigma": min(12.0, 2.0 + values.get("radius", 8.0) / 4.0)},
                )
            ],
            "fallback": True,
            "why": lens.fallback_why,
            "requested": lens.effect,
        }
    return {
        "effects": [Effect(kind=lens.effect, params=values)],
        "fallback": False,
        "why": "",
        "requested": lens.effect,
    }


def describe(name: str) -> Dict[str, object]:
    lens = require(name)
    return {
        "name": lens.name,
        "effect": lens.effect,
        "trigger": lens.trigger,
        "params": [[l.name, l.lo, l.hi, l.default] for l in lens.params],
        "cap_seconds": lens.cap_seconds,
        "distortion": lens.distortion,
        "needs_depth": lens.needs_depth,
        "fallback_effect": lens.fallback_effect,
    }


__all__ = [
    "FAMILY",
    "LENSES",
    "Lens",
    "LensError",
    "Limit",
    "MAX_STACK",
    "build",
    "describe",
    "require",
    "stack",
    "validate",
]
