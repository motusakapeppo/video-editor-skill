"""vx.effects.compose -- the composable effect specification.

This module does not render anything. It *describes* an effect as an ordered
list of primitives, *validates* that description, *estimates* its cost and
*normalises* the historical one-word effects (``zoom``, ``lut warm``) into the
same shape, so old callers and new ones share one language.

The spec
--------
::

    {"id": "spinta_calda",
     "primitives": [
        {"kind": "transform",
         "params": {"scale": 1.15, "x": [0, 40], "y": 0},
         "curve": {"easing": "out", "from": 0.0, "to": 1.0}},
        {"kind": "grade", "params": {"lut": "warm", "contrast": 1.1}}
     ],
     "duration": 2.4,
     "cost": 1.0}

A parameter declared as a *span* (``x``, ``scale``, ``mix``...) accepts either
one number (static) or ``[a, b]``: the primitive then interpolates from ``a`` to
``b`` through its curve. The curve is the *envelope*: ``sample_curve`` returns
the normalised progress and a span is evaluated as ``a + (b - a) * progress``.

A transition is a formula, not a separate entity: it is a spec whose primitives
carry the curve that shapes the cut (see :data:`TRANSITIONS`).

What validation returns
-----------------------
:func:`validate` never raises. It returns ``[Issue(code, where, message)]`` and
every message starts with the field path, in Italian, so a planner can point at
``primitives[1].params.contrast`` without guessing (example:
``primitives[1].params.contrast deve stare in [0.5, 2.0]``).

What cost returns
-----------------
:func:`cost` is additive: every primitive contributes a non-negative weight plus
its own complexity surcharges, the sum is scaled by duration and clamped to
``[0, 10]``. Adding a primitive never lowers the estimate (until the clamp), so
a budget can be honoured by dropping primitives from the tail.
"""

from __future__ import annotations

import copy
import difflib
import math
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: Default length of an effect that does not declare one, in seconds.
DEFAULT_DURATION = 2.4
#: Longest effect this spec accepts, in seconds.
MAX_DURATION = 120.0
#: Longest single primitive, in seconds.
MAX_PRIMITIVE_DURATION = 60.0
#: Most primitives one effect may chain.
MAX_PRIMITIVES = 16
#: Cost ceiling: the plan compares ``cost(spec)`` against a budget in this unit.
MAX_COST = 10.0

#: The declared interpolations. ``bezier`` needs ``points``.
CURVES = ("linear", "in", "out", "in_out", "bezier")
#: Spellings accepted for the same curve; the canonical name is what is stored.
_EASING_ALIASES = {
    "linear": "linear",
    "in": "in",
    "ease_in": "in",
    "easein": "in",
    "ease-in": "in",
    "out": "out",
    "ease_out": "out",
    "easeout": "out",
    "ease-out": "out",
    "in_out": "in_out",
    "inout": "in_out",
    "ease_in_out": "in_out",
    "easeinout": "in_out",
    "ease-in-out": "in_out",
    "bezier": "bezier",
}

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_NUMBER = (int, float)


def _is_number(value: Any) -> bool:
    """True for a real scalar (``bool`` is not a number here)."""
    return isinstance(value, _NUMBER) and not isinstance(value, bool)


def _is_pair(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(_is_number(item) for item in value)
    )


def _scalar(value: Any, default: float = 0.0) -> float:
    """A span's end value, or the scalar itself; ``default`` when malformed."""
    if _is_pair(value):
        return float(value[-1])
    if _is_number(value):
        return float(value)
    return float(default)


def _fmt(value: float) -> str:
    """The ``[0.5, 2.0]`` form used inside Italian messages."""
    return repr(float(value))


@dataclass(frozen=True)
class Issue:
    """One validation finding: a stable code, the field path, the sentence."""

    code: str
    where: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.message


class SpecError(ValueError):
    """Raised by :func:`expand` when a name or an argument cannot be resolved."""


@dataclass(frozen=True)
class Param:
    """One typed parameter: its kind, its default and (where relevant) domain."""

    kind: str
    default: Any
    lo: Optional[float] = None
    hi: Optional[float] = None
    choices: Tuple[str, ...] = ()
    min_len: int = 0
    max_len: int = 0

    def __post_init__(self) -> None:
        if self.lo is not None:
            object.__setattr__(self, "lo", float(self.lo))
        if self.hi is not None:
            object.__setattr__(self, "hi", float(self.hi))
        object.__setattr__(self, "choices", tuple(self.choices or ()))

    def as_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"kind": self.kind, "default": self.default}
        if self.lo is not None:
            data["min"] = self.lo
        if self.hi is not None:
            data["max"] = self.hi
        if self.choices:
            data["choices"] = list(self.choices)
        if self.min_len:
            data["min_len"] = self.min_len
        if self.max_len:
            data["max_len"] = self.max_len
        return data


@dataclass(frozen=True)
class Primitive:
    """One primitive kind: its typed parameters and its base cost weight."""

    kind: str
    params: Mapping[str, Param] = field(default_factory=dict)
    weight: float = 0.5
    summary: str = ""


def _params(**declared: Param) -> Mapping[str, Param]:
    return MappingProxyType(dict(declared))


#: The eight primitives. ``weight`` is the base cost, before surcharges.
PRIMITIVES: Mapping[str, Primitive] = MappingProxyType(
    {
        "transform": Primitive(
            kind="transform",
            weight=0.50,
            summary="muove, scala, ruota o cambia l'opacita' del quadro",
            params=_params(
                scale=Param("span", 1.0, 0.5, 3.0),
                x=Param("span", 0.0, -4096.0, 4096.0),
                y=Param("span", 0.0, -4096.0, 4096.0),
                rotate=Param("number", 0.0, -180.0, 180.0),
                opacity=Param("span", 1.0, 0.0, 1.0),
                anchor=Param(
                    "enum",
                    "center",
                    choices=(
                        "center",
                        "top_left",
                        "top_right",
                        "bottom_left",
                        "bottom_right",
                    ),
                ),
            ),
        ),
        "grade": Primitive(
            kind="grade",
            weight=0.35,
            summary="corregge colore, tono e contrasto",
            params=_params(
                lut=Param(
                    "enum",
                    "none",
                    choices=(
                        "none",
                        "warm",
                        "cool",
                        "teal_orange",
                        "sepia",
                        "noir",
                        "bleach",
                        "vibrant",
                    ),
                ),
                contrast=Param("span", 1.0, 0.5, 2.0),
                saturation=Param("span", 1.0, 0.0, 2.0),
                brightness=Param("span", 0.0, -1.0, 1.0),
                temperature=Param("span", 0.0, -1.0, 1.0),
                gamma=Param("span", 1.0, 0.5, 2.0),
            ),
        ),
        "blur": Primitive(
            kind="blur",
            weight=0.90,
            summary="sfoca il quadro o una sua direzione",
            params=_params(
                sigma=Param("span", 4.0, 0.0, 100.0),
                style=Param(
                    "enum",
                    "gaussian",
                    choices=("gaussian", "box", "motion", "radial"),
                ),
                angle=Param("number", 0.0, -180.0, 180.0),
            ),
        ),
        "mask": Primitive(
            kind="mask",
            weight=0.60,
            summary="ritaglia la visibilita' con una forma e una sfumatura",
            params=_params(
                shape=Param(
                    "enum",
                    "ellipse",
                    choices=("ellipse", "rect", "linear", "luma", "chroma"),
                ),
                feather=Param("number", 24.0, 0.0, 200.0),
                cx=Param("number", 0.0, -1.0, 1.0),
                cy=Param("number", 0.0, -1.0, 1.0),
                width=Param("number", 1.0, 0.0, 1.0),
                height=Param("number", 1.0, 0.0, 1.0),
                angle=Param("number", 0.0, -180.0, 180.0),
                invert=Param("bool", False),
            ),
        ),
        "blend": Primitive(
            kind="blend",
            weight=0.70,
            summary="fonde due sorgenti con un modo e una quota",
            params=_params(
                mode=Param(
                    "enum",
                    "normal",
                    choices=(
                        "normal",
                        "multiply",
                        "screen",
                        "overlay",
                        "add",
                        "difference",
                        "soft_light",
                    ),
                ),
                mix=Param("span", 1.0, 0.0, 1.0),
                opacity=Param("number", 1.0, 0.0, 1.0),
                source=Param("string", "self", min_len=1, max_len=32),
            ),
        ),
        "time": Primitive(
            kind="time",
            weight=0.55,
            summary="cambia la velocita', ferma o ripete il tempo",
            params=_params(
                mode=Param(
                    "enum",
                    "speed",
                    choices=("speed", "freeze", "reverse", "stutter", "echo"),
                ),
                rate=Param("number", 1.0, 0.1, 8.0),
                at=Param("number", 0.0, 0.0, 3600.0),
                frames=Param("int", 3, 1, 120),
                hold=Param("number", 0.5, 0.0, 60.0),
            ),
        ),
        "text": Primitive(
            kind="text",
            weight=0.35,
            summary="disegna una linea di testo con stile dichiarato",
            params=_params(
                content=Param("string", "Titolo", min_len=1, max_len=200),
                size=Param("number", 64.0, 8.0, 512.0),
                color=Param("color", "#FFFFFF"),
                align=Param("enum", "center", choices=("left", "center", "right")),
                x=Param("number", 0.0, -1.0, 1.0),
                y=Param("number", 0.0, -1.0, 1.0),
                tracking=Param("number", 0.0, -10.0, 40.0),
                weight=Param("int", 600, 100, 900),
                font=Param("string", "", max_len=2048),
            ),
        ),
        "particles": Primitive(
            kind="particles",
            weight=1.40,
            summary="emette particelle deterministiche con un seme",
            params=_params(
                style=Param(
                    "enum",
                    "dust",
                    choices=("dust", "sparks", "snow", "confetti", "embers"),
                ),
                count=Param("int", 200, 1, 5000),
                size=Param("number", 6.0, 1.0, 64.0),
                speed=Param("number", 1.0, 0.0, 20.0),
                seed=Param("int", 0, 0, 2147483647),
                opacity=Param("number", 1.0, 0.0, 1.0),
            ),
        ),
    }
)

#: Fields a spec may carry. Anything else is a typo, not an extension point.
_TOP_KEYS = frozenset(
    {"id", "primitives", "duration", "cost", "description", "tags"}
)
#: Fields one primitive may carry.
_PRIM_KEYS = frozenset({"kind", "params", "curve", "at", "duration", "enabled"})
#: Fields one curve may carry.
_CURVE_KEYS = frozenset({"easing", "from", "to", "points", "keys"})
#: Fields one key may carry.
_KEY_KEYS = frozenset({"t", "value", "v", "easing"})

_KIND_COST = {kind: decl.weight for kind, decl in PRIMITIVES.items()}


# --------------------------------------------------------------------------- #
# curves
# --------------------------------------------------------------------------- #


def canonical_easing(name: Any) -> Optional[str]:
    """The canonical curve name, or ``None`` when it is not declared."""
    if not isinstance(name, str):
        return None
    return _EASING_ALIASES.get(name.strip().lower())


def _ease(name: str, x: float) -> float:
    x = min(1.0, max(0.0, x))
    if name == "linear":
        return x
    if name == "in":
        return x * x
    if name == "out":
        return 1.0 - (1.0 - x) * (1.0 - x)
    if name == "in_out":
        return x * x * (3.0 - 2.0 * x)
    return x


def _bezier(u: float, points: Sequence[Sequence[float]]) -> float:
    """Cubic bezier with endpoints (0,0)/(1,1); ``points`` are the two handles."""
    u = min(1.0, max(0.0, u))
    if len(points) == 4:
        x1, y1 = float(points[1][0]), float(points[1][1])
        x2, y2 = float(points[2][0]), float(points[2][1])
    else:
        x1, y1 = float(points[0][0]), float(points[0][1])
        x2, y2 = float(points[1][0]), float(points[1][1])

    def curve_x(t: float) -> float:
        return 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t**3

    def curve_y(t: float) -> float:
        return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t**3

    lo, hi = 0.0, 1.0
    for _ in range(48):
        mid = (lo + hi) / 2.0
        if curve_x(mid) < u:
            lo = mid
        else:
            hi = mid
    return curve_y((lo + hi) / 2.0)


def _default_curve() -> Dict[str, Any]:
    return {"easing": "linear", "from": 0.0, "to": 1.0}


def _point(pair: Any) -> bool:
    return (
        isinstance(pair, (list, tuple))
        and len(pair) == 2
        and all(_is_number(item) for item in pair)
        and 0.0 <= float(pair[0]) <= 1.0
        and 0.0 <= float(pair[1]) <= 1.0
    )


def sample_curve(curve: Optional[Mapping[str, Any]], u: float) -> float:
    """Evaluate ``curve`` at normalised time ``u`` in ``[0, 1]``.

    A curve with ``keys`` is a piecewise track (each key governs the segment
    that follows it); otherwise ``easing`` maps ``u`` through ``from``/``to``.
    This is a formula, not a render: no pixel is touched.
    """
    if not isinstance(curve, Mapping):
        return min(1.0, max(0.0, float(u)))
    u = min(1.0, max(0.0, float(u)))
    keys = curve.get("keys")
    if isinstance(keys, (list, tuple)) and keys:
        stops = sorted(
            (
                (
                    float(key.get("t", 0.0)),
                    float(key.get("value", key.get("v", 0.0))),
                    canonical_easing(key.get("easing", "linear")) or "linear",
                )
                for key in keys
            ),
            key=lambda item: item[0],
        )
        if u <= stops[0][0]:
            return stops[0][1]
        if u >= stops[-1][0]:
            return stops[-1][1]
        for (t0, v0, _), (t1, v1, easing) in zip(stops, stops[1:]):
            if t0 <= u <= t1:
                span = t1 - t0
                local = 0.0 if span <= 0 else (u - t0) / span
                return v0 + (v1 - v0) * _ease(easing, local)
        return stops[-1][1]

    easing = canonical_easing(curve.get("easing", "linear")) or "linear"
    points = curve.get("points") or ()
    if easing == "bezier" and len(points) >= 2:
        eased = _bezier(u, points)
    else:
        eased = _ease(easing, u)
    start = float(curve.get("from", 0.0))
    end = float(curve.get("to", 1.0))
    return start + (end - start) * eased


# --------------------------------------------------------------------------- #
# normalisation
# --------------------------------------------------------------------------- #


def _merged_params(primitive: Mapping[str, Any]) -> Dict[str, Any]:
    """The primitive's parameters with every declared default filled in."""
    raw = primitive.get("params")
    raw = dict(raw) if isinstance(raw, Mapping) else {}
    kind = primitive.get("kind")
    decl = PRIMITIVES.get(kind) if isinstance(kind, str) else None
    if decl is None:
        return raw
    merged = {name: param.default for name, param in decl.params.items()}
    merged.update(raw)
    return merged


def _normal_curve(curve: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(curve, Mapping):
        return None
    data = _default_curve()
    easing = canonical_easing(curve.get("easing", "linear")) or "linear"
    data["easing"] = easing
    if "from" in curve:
        data["from"] = curve["from"]
    if "to" in curve:
        data["to"] = curve["to"]
    if "points" in curve:
        data["points"] = [list(point) for point in curve["points"]]
    if "keys" in curve:
        data["keys"] = [
            {
                "t": key.get("t", 0.0),
                "value": key.get("value", key.get("v", 0.0)),
                "easing": canonical_easing(key.get("easing", "linear")) or "linear",
            }
            for key in curve["keys"]
        ]
    return data


def normalize(spec: Mapping[str, Any]) -> Dict[str, Any]:
    """A plain, deterministic copy of ``spec`` with every default filled in.

    No range is checked here: :func:`validate` owns refusals. Normalising is what
    lets a planner read one shape whether the effect came from a name or a hand
    written spec.
    """
    if not isinstance(spec, Mapping):
        raise SpecError("la specifica deve essere un oggetto")
    out: Dict[str, Any] = {}
    out["id"] = spec.get("id") or "custom"
    out["duration"] = spec.get("duration", DEFAULT_DURATION)
    primitives: List[Dict[str, Any]] = []
    raw_primitives = spec.get("primitives") or []
    if isinstance(raw_primitives, (list, tuple)):
        for primitive in raw_primitives:
            if not isinstance(primitive, Mapping):
                primitives.append({})
                continue
            entry: Dict[str, Any] = {
                "kind": primitive.get("kind"),
                "params": _merged_params(primitive),
                "at": primitive.get("at", 0.0),
                "enabled": primitive.get("enabled", True),
            }
            curve = _normal_curve(primitive.get("curve"))
            if curve is not None:
                entry["curve"] = curve
            if "duration" in primitive:
                entry["duration"] = primitive["duration"]
            primitives.append(entry)
    out["primitives"] = primitives
    for key in ("cost", "description", "tags"):
        if key in spec:
            out[key] = copy.deepcopy(spec[key])
    return out


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #


def _param_problem(decl: Param, value: Any) -> Optional[str]:
    """The Italian sentence for one bad value, or ``None`` when it is inside."""
    kind = decl.kind
    if kind in ("number", "span"):
        values = []
        if kind == "span" and isinstance(value, (list, tuple)):
            if len(value) != 2:
                return "deve essere un numero o una coppia [inizio, fine]"
            values = list(value)
        else:
            values = [value]
        for item in values:
            if not _is_number(item):
                return f"deve essere un numero (trovato {type(item).__name__})"
        for item in values:
            if decl.lo is not None and decl.hi is not None:
                if not (decl.lo <= float(item) <= decl.hi):
                    return f"deve stare in [{_fmt(decl.lo)}, {_fmt(decl.hi)}]"
        return None
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"deve essere un intero (trovato {type(value).__name__})"
        if decl.lo is not None and decl.hi is not None:
            if not (decl.lo <= value <= decl.hi):
                return f"deve stare in [{int(decl.lo)}, {int(decl.hi)}]"
        return None
    if kind == "bool":
        if not isinstance(value, bool):
            return f"deve essere true o false (trovato {type(value).__name__})"
        return None
    if kind == "enum":
        if not isinstance(value, str):
            return f"deve essere una stringa (trovato {type(value).__name__})"
        if value not in decl.choices:
            return f"deve essere uno di {list(decl.choices)} (trovato {value!r})"
        return None
    if kind == "string":
        if not isinstance(value, str):
            return f"deve essere una stringa (trovato {type(value).__name__})"
        if decl.min_len and len(value) < decl.min_len:
            return f"deve contenere almeno {decl.min_len} caratteri"
        if decl.max_len and len(value) > decl.max_len:
            return f"deve contenere al massimo {decl.max_len} caratteri"
        return None
    if kind == "color":
        if not isinstance(value, str):
            return f"deve essere un colore (trovato {type(value).__name__})"
        if not _COLOR.match(value):
            return "deve essere un colore #RRGGBB"
        return None
    return None


def _validate_curve(curve: Any, where: str, add) -> None:
    if not isinstance(curve, Mapping):
        add("CURVE_SHAPE", where, f"{where} deve essere un oggetto")
        return

    for key in curve:
        if key not in _CURVE_KEYS:
            add(
                "CURVE_FIELD",
                f"{where}.{key}",
                f"{where}.{key}: campo sconosciuto (ammessi: {', '.join(sorted(_CURVE_KEYS))})",
            )

    easing = curve.get("easing", "linear")
    canonical = canonical_easing(easing)
    if canonical is None:
        add(
            "CURVE_EASING",
            f"{where}.easing",
            f"{where}.easing: curva sconosciuta {easing!r} "
            f"(ammesse: {', '.join(CURVES)})",
        )
    elif canonical == "bezier" and "points" not in curve:
        add(
            "CURVE_POINTS",
            f"{where}.points",
            f"{where}.points: la curva bezier richiede due punti di controllo [[x1, y1], [x2, y2]]",
        )

    for field_name in ("from", "to"):
        if field_name in curve:
            value = curve[field_name]
            if not _is_number(value) or not math.isfinite(float(value)):
                add(
                    "CURVE_RANGE",
                    f"{where}.{field_name}",
                    f"{where}.{field_name} deve essere un numero finito",
                )

    if "points" in curve:
        points = curve["points"]
        valid = isinstance(points, (list, tuple)) and len(points) in (2, 4)
        valid = valid and all(_point(point) for point in points)
        if not valid:
            add(
                "CURVE_POINTS",
                f"{where}.points",
                f"{where}.points deve essere una lista di 2 punti "
                "[[x1, y1], [x2, y2]] con coordinate in [0.0, 1.0]",
            )
        elif len(points) == 4:
            first, last = points[0], points[-1]
            if not (
                abs(float(first[0])) < 1e-9
                and abs(float(first[1])) < 1e-9
                and abs(float(last[0]) - 1.0) < 1e-9
                and abs(float(last[1]) - 1.0) < 1e-9
            ):
                add(
                    "CURVE_POINTS",
                    f"{where}.points",
                    f"{where}.points: i quattro punti devono iniziare in [0, 0] e finire in [1, 1]",
                )

    if "keys" in curve:
        keys = curve["keys"]
        if not isinstance(keys, (list, tuple)) or len(keys) < 2:
            add(
                "CURVE_KEYS",
                f"{where}.keys",
                f"{where}.keys deve essere una lista di almeno 2 chiavi",
            )
            return
        previous = None
        for index, key in enumerate(keys):
            key_where = f"{where}.keys[{index}]"
            if not isinstance(key, Mapping):
                add("CURVE_KEY_SHAPE", key_where, f"{key_where} deve essere un oggetto")
                previous = None
                continue
            for name in key:
                if name not in _KEY_KEYS:
                    add(
                        "CURVE_KEY_FIELD",
                        f"{key_where}.{name}",
                        f"{key_where}.{name}: campo sconosciuto (ammessi: t, value, easing)",
                    )
            t = key.get("t")
            if not _is_number(t) or not (0.0 <= float(t) <= 1.0):
                add(
                    "CURVE_KEY_TIME",
                    f"{key_where}.t",
                    f"{key_where}.t deve essere un numero in [0.0, 1.0]",
                )
            elif previous is not None and float(t) <= previous:
                add(
                    "CURVE_KEY_ORDER",
                    f"{key_where}.t",
                    f"{key_where}.t deve crescere rispetto a keys[{index - 1}].t",
                )
            else:
                previous = float(t)
            value = key.get("value", key.get("v"))
            if not _is_number(value):
                add(
                    "CURVE_KEY_VALUE",
                    f"{key_where}.value",
                    f"{key_where}.value deve essere un numero",
                )
            elif not (-8.0 <= float(value) <= 8.0):
                add(
                    "CURVE_KEY_VALUE",
                    f"{key_where}.value",
                    f"{key_where}.value deve stare in [-8.0, 8.0]",
                )
            if "easing" in key:
                key_easing = canonical_easing(key["easing"])
                if key_easing is None or key_easing == "bezier":
                    add(
                        "CURVE_KEY_EASING",
                        f"{key_where}.easing",
                        f"{key_where}.easing: una chiave ammette solo "
                        "linear, in, out, in_out",
                    )


def _validate_primitive(primitive: Any, where: str, duration: float, add) -> None:
    if not isinstance(primitive, Mapping):
        add("PRIM_SHAPE", where, f"{where} deve essere un oggetto")
        return

    for key in primitive:
        if key not in _PRIM_KEYS:
            add(
                "PRIM_FIELD",
                f"{where}.{key}",
                f"{where}.{key}: campo sconosciuto (ammessi: {', '.join(sorted(_PRIM_KEYS))})",
            )

    kind = primitive.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        add("PRIM_KIND", f"{where}.kind", f"{where}.kind mancante o vuoto")
        return
    decl = PRIMITIVES.get(kind)
    if decl is None:
        add(
            "PRIM_UNKNOWN",
            f"{where}.kind",
            f"{where}.kind: primitiva sconosciuta {kind!r} "
            f"(ammesse: {', '.join(sorted(PRIMITIVES))})",
        )
        return

    raw = primitive.get("params", {})
    if not isinstance(raw, Mapping):
        add("PRIM_PARAMS", f"{where}.params", f"{where}.params deve essere un oggetto")
        raw = {}
    for name in raw:
        if name not in decl.params:
            add(
                "PRIM_PARAM",
                f"{where}.params.{name}",
                f"{where}.params.{name}: parametro sconosciuto per {kind} "
                f"(ammessi: {', '.join(decl.params)})",
            )
    for name, param in decl.params.items():
        if name not in raw:
            continue
        value = raw[name]
        if (
            param.kind == "span"
            and isinstance(value, (list, tuple))
            and len(value) == 2
        ):
            # A span is checked element by element, so the message names the
            # exact end that is outside its domain.
            for index, item in enumerate(value):
                if not _is_number(item):
                    add(
                        "PRIM_RANGE",
                        f"{where}.params.{name}[{index}]",
                        f"{where}.params.{name}[{index}] deve essere un numero "
                        f"(trovato {type(item).__name__})",
                    )
                elif param.lo is not None and param.hi is not None:
                    if not (param.lo <= float(item) <= param.hi):
                        add(
                            "PRIM_RANGE",
                            f"{where}.params.{name}[{index}]",
                            f"{where}.params.{name}[{index}] deve stare in "
                            f"[{_fmt(param.lo)}, {_fmt(param.hi)}]",
                        )
            continue
        problem = _param_problem(param, value)
        if problem is not None:
            add("PRIM_RANGE", f"{where}.params.{name}", f"{where}.params.{name} {problem}")

    if "curve" in primitive:
        _validate_curve(primitive["curve"], f"{where}.curve", add)

    if "at" in primitive:
        at = primitive["at"]
        if not _is_number(at) or not (0.0 <= float(at) <= duration):
            add(
                "PRIM_AT",
                f"{where}.at",
                f"{where}.at deve essere un numero in [0.0, {_fmt(duration)}]",
            )

    if "duration" in primitive:
        length = primitive["duration"]
        if not _is_number(length) or not (0.0 < float(length) <= MAX_PRIMITIVE_DURATION):
            add(
                "PRIM_DURATION",
                f"{where}.duration",
                f"{where}.duration deve essere un numero in (0.0, {_fmt(MAX_PRIMITIVE_DURATION)}]",
            )
        elif _is_number(primitive.get("at", 0.0)):
            end = float(primitive.get("at", 0.0)) + float(length)
            if end > duration + 1e-9:
                add(
                    "PRIM_DURATION",
                    f"{where}.duration",
                    f"{where}.at + {where}.duration supera duration "
                    f"({_fmt(end)} > {_fmt(duration)})",
                )

    if "enabled" in primitive and not isinstance(primitive["enabled"], bool):
        add(
            "PRIM_ENABLED",
            f"{where}.enabled",
            f"{where}.enabled deve essere true o false",
        )


def validate(spec: Any) -> List[Issue]:
    """Every refusal in ``spec``, in Italian, each with the field path.

    Never raises: a malformed document is itself a finding.
    """
    issues: List[Issue] = []

    def add(code: str, where: str, message: str) -> None:
        issues.append(Issue(code, where, message))

    if not isinstance(spec, Mapping):
        add("SPEC_SHAPE", "", "la specifica deve essere un oggetto")
        return issues

    for key in spec:
        if key not in _TOP_KEYS:
            add(
                "SPEC_FIELD",
                str(key),
                f"{key}: campo sconosciuto (ammessi: {', '.join(sorted(_TOP_KEYS))})",
            )

    identifier = spec.get("id")
    if identifier is not None:
        if not isinstance(identifier, str) or not identifier.strip():
            add("SPEC_ID", "id", "id deve essere una stringa non vuota")
        elif len(identifier) > 64:
            add("SPEC_ID", "id", "id deve contenere al massimo 64 caratteri")

    duration = spec.get("duration", DEFAULT_DURATION)
    duration_ok = _is_number(duration) and math.isfinite(float(duration))
    if not duration_ok:
        add("SPEC_DURATION", "duration", "duration deve essere un numero finito")
        duration = DEFAULT_DURATION
    elif not (0.0 < float(duration) <= MAX_DURATION):
        add(
            "SPEC_DURATION",
            "duration",
            f"duration deve stare in (0.0, {_fmt(MAX_DURATION)}] "
            f"(trovato {_fmt(duration)})",
        )
        duration = DEFAULT_DURATION
    duration = float(duration)

    if "cost" in spec:
        declared = spec["cost"]
        if not _is_number(declared) or not (0.0 <= float(declared) <= MAX_COST):
            add(
                "SPEC_COST",
                "cost",
                f"cost deve stare in [0.0, {_fmt(MAX_COST)}]",
            )

    if "description" in spec and not isinstance(spec["description"], str):
        add("SPEC_DESCRIPTION", "description", "description deve essere una stringa")
    if "tags" in spec:
        tags = spec["tags"]
        if not isinstance(tags, (list, tuple)) or not all(
            isinstance(tag, str) for tag in tags
        ):
            add("SPEC_TAGS", "tags", "tags deve essere una lista di stringhe")

    primitives = spec.get("primitives")
    if not isinstance(primitives, (list, tuple)):
        add("SPEC_PRIMITIVES", "primitives", "primitives deve essere una lista")
        return issues
    if not primitives:
        add("SPEC_EMPTY", "primitives", "primitives deve contenere almeno una primitiva")
        return issues
    if len(primitives) > MAX_PRIMITIVES:
        add(
            "SPEC_PRIMITIVES",
            "primitives",
            f"primitives ammette al massimo {MAX_PRIMITIVES} primitive "
            f"(trovate {len(primitives)})",
        )

    for index, primitive in enumerate(primitives):
        _validate_primitive(primitive, f"primitives[{index}]", duration, add)

    return issues


# --------------------------------------------------------------------------- #
# cost
# --------------------------------------------------------------------------- #


def _primitive_cost(primitive: Mapping[str, Any]) -> float:
    kind = primitive.get("kind")
    total = _KIND_COST.get(kind, 1.0) if isinstance(kind, str) else 1.0
    params = _merged_params(primitive)

    if kind == "transform":
        total += 0.12 * sum(
            1 for name in ("scale", "x", "y") if _is_pair(params.get(name))
        )
        total += abs(_scalar(params.get("rotate", 0.0))) / 360.0 * 0.15
    elif kind == "grade":
        if params.get("lut", "none") != "none":
            total += 0.15
        total += abs(_scalar(params.get("contrast", 1.0), 1.0) - 1.0) * 0.10
        total += abs(_scalar(params.get("saturation", 1.0), 1.0) - 1.0) * 0.05
    elif kind == "blur":
        total += min(0.50, max(0.0, _scalar(params.get("sigma", 0.0))) / 100.0 * 0.50)
    elif kind == "mask":
        total += min(0.30, max(0.0, _scalar(params.get("feather", 0.0))) / 200.0 * 0.30)
        if params.get("shape") in ("luma", "chroma"):
            total += 0.20
    elif kind == "blend":
        if _is_pair(params.get("mix")):
            total += 0.15
    elif kind == "time":
        total += int(params.get("frames", 3)) / 120.0 * 0.20
        if params.get("mode") in ("stutter", "echo"):
            total += 0.15
    elif kind == "text":
        total += min(0.20, len(str(params.get("content", ""))) / 200.0 * 0.20)
    elif kind == "particles":
        total += min(0.80, int(params.get("count", 1)) / 5000.0 * 0.80)

    curve = primitive.get("curve")
    if isinstance(curve, Mapping):
        if canonical_easing(curve.get("easing", "linear")) == "bezier":
            total += 0.05
        keys = curve.get("keys") or []
        if isinstance(keys, (list, tuple)) and len(keys) > 2:
            total += 0.06 * (len(keys) - 2)
    return total


def cost(spec: Any) -> float:
    """The normalised estimate in ``[0, 10]``; monotone in the primitive count.

    Every primitive adds a non-negative amount, so appending one never lowers
    the estimate before the ceiling. An explicit ``cost`` on the spec raises the
    estimate (a declared minimum), never lowers it.
    """
    if not isinstance(spec, Mapping):
        return 0.0
    primitives = spec.get("primitives") or []
    if not isinstance(primitives, (list, tuple)):
        return 0.0
    total = sum(
        _primitive_cost(primitive)
        for primitive in primitives
        if isinstance(primitive, Mapping)
    )
    duration = spec.get("duration", DEFAULT_DURATION)
    if _is_number(duration):
        total *= 1.0 + min(1.0, 0.04 * max(0.0, float(duration) - 3.0))
    declared = spec.get("cost")
    if _is_number(declared):
        total = max(total, float(declared))
    return round(min(MAX_COST, max(0.0, total)), 3)


# --------------------------------------------------------------------------- #
# historical names
# --------------------------------------------------------------------------- #

_HISTORY: Mapping[str, Dict[str, Any]] = MappingProxyType(
    {
        "zoom": {
            "id": "zoom",
            "duration": 2.4,
            "primitives": [
                {
                    "kind": "transform",
                    "params": {"scale": [1.0, 1.15]},
                    "curve": {"easing": "out"},
                }
            ],
        },
        "zoom_in": {"id": "zoom_in", "duration": 2.4, "primitives": [{"kind": "transform", "params": {"scale": [1.0, 1.15]}, "curve": {"easing": "out"}}]},
        "push_in": {"id": "push_in", "duration": 1.6, "primitives": [{"kind": "transform", "params": {"scale": [1.0, 1.12]}, "curve": {"easing": "in_out"}}]},
        "zoom_out": {"id": "zoom_out", "duration": 2.4, "primitives": [{"kind": "transform", "params": {"scale": [1.15, 1.0]}, "curve": {"easing": "out"}}]},
        "pull_out": {"id": "pull_out", "duration": 2.0, "primitives": [{"kind": "transform", "params": {"scale": [1.12, 1.0]}, "curve": {"easing": "in_out"}}]},
        "kenburns": {
            "id": "kenburns",
            "duration": 5.0,
            "primitives": [
                {
                    "kind": "transform",
                    "params": {"scale": [1.0, 1.08], "x": [0.0, 24.0]},
                    "curve": {"easing": "linear"},
                }
            ],
        },
        "punch": {"id": "punch", "duration": 0.6, "primitives": [{"kind": "transform", "params": {"scale": [1.0, 1.06]}, "curve": {"easing": "in_out"}}]},
        "shake": {
            "id": "shake",
            "duration": 0.8,
            "primitives": [
                {
                    "kind": "transform",
                    "params": {"x": [0.0, 8.0]},
                    "curve": {
                        "keys": [
                            {"t": 0.0, "value": 0.0},
                            {"t": 0.2, "value": 1.0},
                            {"t": 0.4, "value": -1.0},
                            {"t": 0.6, "value": 0.7},
                            {"t": 0.8, "value": -0.4},
                            {"t": 1.0, "value": 0.0},
                        ]
                    },
                },
                {
                    "kind": "transform",
                    "params": {"y": [0.0, 5.0]},
                    "curve": {
                        "keys": [
                            {"t": 0.0, "value": 0.0},
                            {"t": 0.25, "value": -1.0},
                            {"t": 0.5, "value": 0.8},
                            {"t": 0.75, "value": -0.5},
                            {"t": 1.0, "value": 0.0},
                        ]
                    },
                },
            ],
        },
        "fade_in": {"id": "fade_in", "duration": 0.8, "primitives": [{"kind": "transform", "params": {"opacity": [0.0, 1.0]}, "curve": {"easing": "out"}}]},
        "fade_out": {"id": "fade_out", "duration": 0.8, "primitives": [{"kind": "transform", "params": {"opacity": [1.0, 0.0]}, "curve": {"easing": "in"}}]},
        "crossfade": {"id": "crossfade", "duration": 0.8, "primitives": [{"kind": "blend", "params": {"mix": [0.0, 1.0]}, "curve": {"easing": "in_out"}}]},
        "freeze": {"id": "freeze", "duration": 1.0, "primitives": [{"kind": "time", "params": {"mode": "freeze", "hold": 0.8}}]},
        "slowmo": {"id": "slowmo", "duration": 3.0, "primitives": [{"kind": "time", "params": {"mode": "speed", "rate": 0.5}}]},
        "speed_up": {"id": "speed_up", "duration": 1.0, "primitives": [{"kind": "time", "params": {"mode": "speed", "rate": 1.8}}]},
        "grain": {"id": "grain", "duration": 3.0, "primitives": [{"kind": "particles", "params": {"style": "dust", "count": 400, "size": 2.0, "opacity": 0.18}}]},
        "vignette": {"id": "vignette", "duration": 3.0, "primitives": [{"kind": "mask", "params": {"shape": "ellipse", "feather": 80.0, "invert": True}}]},
        "chroma_key": {"id": "chroma_key", "duration": 2.0, "primitives": [{"kind": "mask", "params": {"shape": "chroma", "feather": 12.0}}]},
        "text_pop": {
            "id": "text_pop",
            "duration": 1.0,
            "primitives": [
                {"kind": "transform", "params": {"scale": [0.94, 1.0]}, "curve": {"easing": "out"}},
                {"kind": "text", "params": {"content": "Titolo", "size": 64.0}, "curve": {"easing": "out"}},
            ],
        },
        "lut warm": {"id": "lut_warm", "duration": 2.4, "primitives": [{"kind": "grade", "params": {"lut": "warm"}}]},
        "warm": {"id": "warm", "duration": 2.4, "primitives": [{"kind": "grade", "params": {"lut": "warm"}}]},
        "lut cool": {"id": "lut_cool", "duration": 2.4, "primitives": [{"kind": "grade", "params": {"lut": "cool"}}]},
        "cool": {"id": "cool", "duration": 2.4, "primitives": [{"kind": "grade", "params": {"lut": "cool"}}]},
        "teal_orange": {"id": "teal_orange", "duration": 2.4, "primitives": [{"kind": "grade", "params": {"lut": "teal_orange", "contrast": 1.08}}]},
        "sepia": {"id": "sepia", "duration": 2.4, "primitives": [{"kind": "grade", "params": {"lut": "sepia"}}]},
        "noir": {"id": "noir", "duration": 2.4, "primitives": [{"kind": "grade", "params": {"lut": "noir", "contrast": 1.15}}]},
        "particles": {"id": "particles", "duration": 3.0, "primitives": [{"kind": "particles", "params": {"style": "dust", "count": 200}}]},
    }
)

#: Historical names :func:`expand` understands, sorted, for a planner's help.
HISTORICAL_NAMES = tuple(sorted(_HISTORY))


def _coerce_text(param: Param, raw: str) -> Any:
    text = raw.strip().strip("'\"")
    try:
        if param.kind == "int":
            return int(text)
        if param.kind in ("number", "span"):
            return float(text)
    except ValueError:
        raise SpecError(
            f"valore {raw!r} non numerico per un parametro {param.kind}"
        ) from None
    if param.kind == "bool":
        lowered = text.lower()
        if lowered in ("true", "1", "si", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise SpecError(f"valore {raw!r} non booleano (usa true o false)")
    return text


def _parse_historical(raw: str) -> Dict[str, Any]:
    text = " ".join(
        raw.strip().lower().replace(":", " ").replace(",", " ").split()
    )
    if not text:
        raise SpecError("nome effetto vuoto")
    if text in _HISTORY:
        return copy.deepcopy(_HISTORY[text])

    tokens = text.split()
    name = tokens[0]
    rest = tokens[1:]
    if len(tokens) >= 2 and " ".join(tokens[:2]) in _HISTORY:
        name = " ".join(tokens[:2])
        rest = tokens[2:]

    base = _HISTORY.get(name)
    if base is None:
        close = difflib.get_close_matches(name, list(_HISTORY), n=3)
        hint = f"; forse: {', '.join(close)}" if close else ""
        raise SpecError(f"effetto storico sconosciuto {name!r}{hint}")

    spec = copy.deepcopy(base)
    first = spec["primitives"][0]
    decl = PRIMITIVES[first["kind"]]
    for token in rest:
        if "=" not in token:
            raise SpecError(
                f"argomento non riconosciuto {token!r} per {name!r} "
                "(usa chiave=valore, es. 'zoom scale=1.3')"
            )
        key, _, value = token.partition("=")
        key = key.strip()
        param = decl.params.get(key)
        if param is None:
            raise SpecError(
                f"parametro {key!r} sconosciuto per {first['kind']} "
                f"(ammessi: {', '.join(decl.params)})"
            )
        first.setdefault("params", {})[key] = _coerce_text(param, value)
    return spec


def expand(name_or_spec: Any) -> Dict[str, Any]:
    """Normalise a historical name (or a spec) into the canonical shape.

    ``expand("zoom")``, ``expand("lut warm")`` and
    ``expand("zoom scale=1.3")`` return the same structure a hand-written spec
    has, with defaults filled in; ``expand(spec)`` is a deterministic copy.
    An unknown name raises :class:`SpecError` with near-miss suggestions.
    """
    if isinstance(name_or_spec, Mapping):
        return normalize(name_or_spec)
    if isinstance(name_or_spec, str):
        return normalize(_parse_historical(name_or_spec))
    raise SpecError(
        f"expand attende un nome o una specifica, non {type(name_or_spec).__name__}"
    )


# --------------------------------------------------------------------------- #
# transitions as formulas
# --------------------------------------------------------------------------- #

#: A transition is not an entity: it is a spec whose blend/transform primitives
#: carry the curve that shapes the cut. ``duration`` is the formula's default.
TRANSITIONS: Mapping[str, Dict[str, Any]] = MappingProxyType(
    {
        "crossfade": {
            "duration": 0.8,
            "primitives": [
                {
                    "kind": "blend",
                    "params": {"mode": "normal", "mix": [0.0, 1.0]},
                    "curve": {"easing": "in_out"},
                }
            ],
        },
        "dip_to_black": {
            "duration": 1.2,
            "primitives": [
                {
                    "kind": "transform",
                    "params": {"opacity": [1.0, 0.0]},
                    "curve": {"easing": "in"},
                },
                {
                    "kind": "transform",
                    "params": {"opacity": [0.0, 1.0]},
                    "curve": {"easing": "out"},
                    "at": 0.6,
                },
            ],
        },
        "dip_to_white": {
            "duration": 1.0,
            "primitives": [
                {
                    "kind": "grade",
                    "params": {"brightness": [0.0, 0.85], "contrast": [1.0, 0.7]},
                    "curve": {"easing": "in_out"},
                }
            ],
        },
        "whip": {
            "duration": 0.45,
            "primitives": [
                {
                    "kind": "transform",
                    "params": {"x": [0.0, 160.0], "scale": [1.0, 1.06]},
                    "curve": {"easing": "in_out"},
                },
                {
                    "kind": "blur",
                    "params": {"sigma": [0.0, 18.0], "style": "motion"},
                    "curve": {"easing": "in_out"},
                },
            ],
        },
        "glitch_cut": {
            "duration": 0.22,
            "primitives": [
                {
                    "kind": "transform",
                    "params": {"x": [0.0, 14.0], "y": [0.0, -9.0]},
                    "curve": {
                        "keys": [
                            {"t": 0.0, "value": 0.0},
                            {"t": 0.3, "value": 1.0},
                            {"t": 0.55, "value": -0.7},
                            {"t": 0.8, "value": 0.4},
                            {"t": 1.0, "value": 0.0},
                        ]
                    },
                },
                {
                    "kind": "grade",
                    "params": {"saturation": [1.0, 1.6]},
                    "curve": {"easing": "out"},
                },
            ],
        },
    }
)


def transition(style: str, duration: Optional[float] = None) -> Dict[str, Any]:
    """The spec of a transition formula, normalised and ready to validate."""
    if not isinstance(style, str):
        raise SpecError("lo stile di transizione deve essere una stringa")
    key = style.strip().lower().replace("-", "_").replace(" ", "_")
    formula = TRANSITIONS.get(key)
    if formula is None:
        close = difflib.get_close_matches(key, list(TRANSITIONS), n=3)
        hint = f"; forse: {', '.join(close)}" if close else ""
        raise SpecError(f"transizione sconosciuta {style!r}{hint}")
    spec = {
        "id": f"transition_{key}",
        "duration": formula["duration"] if duration is None else duration,
        "primitives": copy.deepcopy(formula["primitives"]),
    }
    return normalize(spec)


# --------------------------------------------------------------------------- #
# sampling (descriptive maths, no render)
# --------------------------------------------------------------------------- #


def sample_param(primitive: Mapping[str, Any], name: str, u: float = 0.0) -> float:
    """The value of one parameter at normalised time ``u``.

    A span interpolates ``a -> b`` through the primitive's curve; anything else
    is static. This is what a renderer would ask, but nothing is rendered here.
    """
    if not isinstance(primitive, Mapping):
        raise SpecError("la primitiva deve essere un oggetto")
    kind = primitive.get("kind")
    decl = PRIMITIVES.get(kind) if isinstance(kind, str) else None
    if decl is None:
        raise SpecError(f"primitiva sconosciuta {kind!r}")
    if name not in decl.params:
        raise SpecError(
            f"parametro {name!r} sconosciuto per {kind} "
            f"(ammessi: {', '.join(decl.params)})"
        )
    value = _merged_params(primitive)[name]
    if _is_pair(value):
        start, end = float(value[0]), float(value[1])
        progress = sample_curve(primitive.get("curve"), u)
        return start + (end - start) * progress
    return float(value)


def describe_primitive(kind: str) -> Dict[str, Any]:
    """One primitive's declared parameters, ranges and defaults."""
    decl = PRIMITIVES.get(kind)
    if decl is None:
        raise SpecError(f"primitiva sconosciuta {kind!r}")
    return {
        "kind": decl.kind,
        "weight": decl.weight,
        "summary": decl.summary,
        "params": {
            name: param.as_dict() for name, param in decl.params.items()
        },
    }


def describe() -> Dict[str, Any]:
    """The whole language: primitives, curves and transition formulas."""
    return {
        "default_duration": DEFAULT_DURATION,
        "max_duration": MAX_DURATION,
        "max_primitives": MAX_PRIMITIVES,
        "curves": list(CURVES),
        "primitives": {
            kind: describe_primitive(kind) for kind in PRIMITIVES
        },
        "transitions": sorted(TRANSITIONS),
        "historical_names": list(HISTORICAL_NAMES),
    }


__all__ = [
    "CURVES",
    "DEFAULT_DURATION",
    "HISTORICAL_NAMES",
    "Issue",
    "MAX_COST",
    "MAX_DURATION",
    "MAX_PRIMITIVES",
    "PRIMITIVES",
    "Param",
    "Primitive",
    "SpecError",
    "TRANSITIONS",
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
