"""vx.edl3 — the edit document (schema ``vx.edl3/1``), full F2-T02 contract.

The vertical slice proved the sampling model end to end. This module is the
whole contract: every entity validates, every entity round-trips through
``to_dict``/``from_dict``, and every refusal has a stable code.

What is modelled
----------------
``Effect`` is a *registry-backed* entry. :data:`EFFECT_REGISTRY` declares, for
every kind, the parameter names it accepts, whether it animates at all, which
of its parameters animate, the backend that implements the animation, and a
note recording the measured constraint behind that choice. An effect kind that
cannot declare its own mechanism does not enter the registry (D012).

``AdjustmentLayer(track, effects)`` applies its effects to everything *below*
it on the same segment: on a flat timeline every clip, in registry order before
the clip's own effects.

``TextLayer`` and ``CaptionTrack`` validate and serialise but are **not**
rendered here: the compiler refuses them with ``UNSUPPORTED_EFFECT`` until
F2-T07 wires the Chromium/ASS backend. Refusing loudly is the contract.

``NestedSequence(id, timeline)`` is referenced by ``Clip3.sequence`` (instead
of ``Clip3.source``). The inner timeline must itself be valid and the nesting
depth may not exceed :data:`MAX_NESTING_DEPTH`.

``Transition`` kinds are ``fade_in``, ``fade_out`` and ``crossfade``. The
compiler implements the first two and refuses ``crossfade`` with
``UNSUPPORTED_TRANSITION``.

``AudioTrack3`` / ``Ducking3`` are carried so that an EDL 2.2 document can be
migrated without dropping its music bed or its ducking declaration. The slice
renderer does not mix audio; it refuses it loudly.

Keyframes
---------
``Keyframe(t, value, interp)`` is one scalar control point; ``interp`` governs
the segment that follows it:

* ``linear`` — straight line.
* ``hold``   — step function.
* ``bezier`` — cubic ease-in-out, the smoothstep Hermite ``3x^2 - 2x^3`` with
  zero tangents at both ends.

A two-dimensional property (``text_layers[].keyframes.position``) is expressed
as two scalar tracks, ``position_x`` and ``position_y``: ``Keyframe.value`` is
scalar and pretending otherwise would lose precision.

Validation
----------
``validate()`` never raises and returns ``[{"code", "where", "detail"}]`` with
stable string codes. The compiler decides which codes are fatal.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .edl3_time import AudioPolicy
from .effect_spec import (
    BACKEND_FILTER,
    EffectSpec,
    ParamSpec,
)
from .effects import family_entries

EDL3_VERSION = "vx.edl3/1.2"
#: Accepted schema versions: an additive minor keeps every previous document valid.
EDL3_VERSIONS = ("vx.edl3/1", "vx.edl3/1.1", "vx.edl3/1.2")
#: Declared types a template parameter may expose.
EXPOSED_TYPES = ("string", "number", "int", "color", "bool")
#: What a provenance record may point at.
PROVENANCE_KINDS = ("finding", "opportunity")

#: Deeper nesting is rejected with ``NESTING_TOO_DEEP``.
MAX_NESTING_DEPTH = 2

#: Anti-arlecchino cap: at most this many visible effect instances may start
#: inside any :data:`EFFECT_DENSITY_WINDOW`-second window of one timeline.
#: Breaching it is reported as ``EFFECT_DENSITY``.
EFFECT_DENSITY_LIMIT = 3
#: The sliding window the cap is measured over, in seconds; both edges count.
EFFECT_DENSITY_WINDOW = 10.0

INTERPOLATIONS = frozenset({"linear", "hold", "bezier"})
TRANSITION_KINDS = frozenset({"fade_in", "fade_out", "crossfade"})
#: Generators: the third source kind. ``generator`` names a Motion Studio
#: template plus the parameters that instantiate it; the frames are captured
#: once (Chromium, or the Pillow raster fallback) and consumed as an input.
GENERATOR_ENGINES = frozenset({"chromium", "pillow", "auto"})
GENERATOR_KEYS = frozenset({"template", "params", "engine", "alpha"})

#: Audio time policies a speed ramp may declare. The vocabulary is the planning
#: module's own (:class:`vx.edl3_time.AudioPolicy`), not a copy of it.
AUDIO_TIME_POLICIES = frozenset(str(item.value) for item in AudioPolicy)

#: Animation mechanisms an effect kind may declare (D012). ``filter`` is the
#: declarative backend: one static ffmpeg filter template, no keyframes.
BACKENDS = frozenset(
    {"expr", "sendcmd", "zoompan", "chromium", "frames", BACKEND_FILTER}
)

_TEXT_ALIGNS = frozenset({"left", "center", "right"})
_TEXT_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_TEXT_SIZE = 512.0
MAX_TEXT_STROKE = 64.0
TEXT_KEYFRAME_PARAMS = frozenset({"opacity", "position_x", "position_y"})


#: The effect registry. ``backend`` is the mechanism used *when a parameter is
#: animated*; a static value is a plain filter argument. ``notes`` records the
#: measured constraint that fixed the choice.
#:
#: The five hand-written kinds below are the contract's own: their params,
#: backends and notes are frozen. Every other kind is declared by a family
#: module under :mod:`vx.effects` and merged in by :func:`_merge_families`.
CORE_EFFECT_REGISTRY: Dict[str, EffectSpec] = {
    "transform": EffectSpec(
        kind="transform",
        params=("scale", "opacity"),
        animatable=True,
        animated_params=("opacity",),
        backend="expr",
        notes=(
            "opacity is baked into a geq alpha expression (<=3 keyframes) or "
            "flattened into sendcmd (D012 #1: sendcmd precedes its target); "
            "scale is NOT animatable (D012 #2: scale has no time variable, it "
            "would need zoompan/scale2ref)"
        ),
        fallback="expr",
    ),
    "crop": EffectSpec(
        kind="crop",
        params=("x", "y", "width", "height"),
        animatable=True,
        animated_params=("x", "y", "width", "height"),
        backend="expr",
        notes=(
            "normalised rect; this ffmpeg marks crop w/h/x/y as time-variable, "
            "so the rect animates per frame as expressions of T; a downstream "
            "fixed-scale fit absorbs the changing crop size"
        ),
        fallback="expr",
    ),
    "color": EffectSpec(
        kind="color",
        params=("brightness", "contrast", "saturation"),
        animatable=True,
        animated_params=("brightness", "contrast", "saturation"),
        backend="sendcmd",
        notes=(
            "eq rejects a time variable in its options; animated values are "
            "flattened to one command per frame, emitted before the eq "
            "instance it targets (D012 #1)"
        ),
        fallback="sendcmd",
    ),
    "blur": EffectSpec(
        kind="blur",
        params=("sigma",),
        animatable=False,
        animated_params=(),
        backend="expr",
        notes="static gblur=sigma; not animatable in this contract",
        fallback="expr",
    ),
    "chroma_key": EffectSpec(
        kind="chroma_key",
        params=("similarity", "blend"),
        animatable=False,
        animated_params=(),
        backend="frames",
        notes=(
            "DECLARED PLACEHOLDER: validate + serialise only; the compiler "
            "refuses it with UNSUPPORTED_EFFECT until the frame backend lands"
        ),
    ),
}


def _merge_families(
    core: Mapping[str, EffectSpec], declared: Mapping[str, EffectSpec]
) -> Dict[str, EffectSpec]:
    """The core registry plus every family entry, refusing a stolen kind name."""
    merged: Dict[str, EffectSpec] = dict(core)
    for kind, spec in declared.items():
        if kind in core:
            raise ValueError(
                f"family entry {kind!r} collides with the core registry; a core "
                "kind keeps its own params, backend and notes"
            )
        merged[kind] = spec
    return merged


#: The whole registry: the five contract kinds plus every family module that
#: exists under :mod:`vx.effects`.
EFFECT_REGISTRY: Dict[str, EffectSpec] = _merge_families(
    CORE_EFFECT_REGISTRY, family_entries()
)

EFFECT_KINDS = frozenset(EFFECT_REGISTRY)
TRANSFORM_PARAMS = frozenset(EFFECT_REGISTRY["transform"].params)

#: Keyframes above which a mechanism-flexible entry switches from a baked
#: expression to ``sendcmd``. It mirrors
#: :data:`vx.edl3_render.SENDCMD_THRESHOLD`; the renderer still owns its copy and
#: a test asserts the two numbers agree, so the rule cannot drift silently.
SENDCMD_THRESHOLD = 3

#: Kinds whose ``expr`` declaration can be swapped for ``sendcmd``. It mirrors
#: ``edl3_render.SENDCMD_TRACKS`` (``{("transform", "opacity")}``): ``crop``
#: animates through a time-variable ``crop`` expression and never flips.
FLEXIBLE_EXPR_KINDS = frozenset({"transform"})

#: The compositor a text layer is drawn with. ``TextLayer``/``CaptionTrack`` are
#: their own entities, not registry effects, so their mechanism has no
#: ``EffectSpec`` to declare it and is named here instead.
TEXT_BACKEND = "overlay"
#: The spellings :func:`select_backend` accepts for that entity family.
TEXT_MECHANISM_KINDS = frozenset({"text", "text_layer", "caption"})

#: Effect kinds the compiler implements: the four hand-written kinds plus every
#: entry whose declared backend is the static filter template.
RENDERABLE_EFFECTS = frozenset(
    {"transform", "crop", "color", "blur"}
    | {
        kind
        for kind, spec in EFFECT_REGISTRY.items()
        if spec.backend == BACKEND_FILTER
    }
)
#: Transition kinds the compiler implements.
RENDERABLE_TRANSITIONS = frozenset({"fade_in", "fade_out"})

__all__ = [
    "EDL3_VERSION",
    "MAX_NESTING_DEPTH",
    "INTERPOLATIONS",
    "TRANSITION_KINDS",
    "BACKENDS",
    "EFFECT_REGISTRY",
    "EFFECT_KINDS",
    "TRANSFORM_PARAMS",
    "RENDERABLE_EFFECTS",
    "RENDERABLE_TRANSITIONS",
    "TEXT_KEYFRAME_PARAMS",
    "SENDCMD_THRESHOLD",
    "FLEXIBLE_EXPR_KINDS",
    "TEXT_BACKEND",
    "TEXT_MECHANISM_KINDS",
    "EFFECT_DENSITY_LIMIT",
    "EFFECT_DENSITY_WINDOW",
    "EDLError",
    "ParamSpec",
    "EffectSpec",
    "BACKEND_FILTER",
    "Keyframe",
    "KeyframeTrack",
    "Effect",
    "Transition",
    "Clip3",
    "AdjustmentLayer",
    "TextStyle",
    "TextLayer",
    "CaptionCue",
    "CaptionTrack",
    "AudioClip3",
    "AudioTrack3",
    "Ducking3",
    "NestedSequence",
    "Timeline3",
    "param_domain_problem",
    "select_backend",
    "check_effect_density",
    "sample",
]


class EDLError(Exception):
    """A document could not be sampled or trusted, with a stable code."""

    def __init__(self, code: str, detail: str, where: str = "") -> None:
        self.code = code
        self.detail = detail
        self.where = where
        text = f"{code}: {detail}"
        if where:
            text = f"{text} ({where})"
        super().__init__(text)


# --------------------------------------------------------------- keyframes


@dataclass(frozen=True)
class Keyframe:
    """A single control point. ``interp`` governs the segment that follows it."""

    t: float
    value: float
    interp: str = "linear"

    def __post_init__(self) -> None:
        object.__setattr__(self, "t", float(self.t))
        object.__setattr__(self, "value", float(self.value))

    def to_dict(self) -> Dict[str, Any]:
        return {"t": self.t, "value": self.value, "interp": self.interp}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Keyframe":
        if not isinstance(data, Mapping):
            raise EDLError("KEYFRAME_SHAPE", f"keyframe is not an object: {data!r}")
        return cls(
            t=_float(data.get("t", 0.0)),
            value=_float(data.get("value", 0.0)),
            interp=str(data.get("interp", "linear")),
        )


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ease_in_out(x: float) -> float:
    """Cubic ease-in-out: smoothstep with zero tangents at both ends."""
    return x * x * (3.0 - 2.0 * x)


@dataclass
class KeyframeTrack:
    """An ordered list of keyframes for one scalar property.

    ``sample`` is the only interpolation surface and it refuses a broken time
    axis. Construction stays permissive so that ``validate()`` can report the
    problem instead of an exception escaping from ``from_dict``.
    """

    keys: List[Keyframe] = field(default_factory=list)

    def __post_init__(self) -> None:
        coerced: List[Keyframe] = []
        for raw in self.keys:
            if isinstance(raw, Keyframe):
                coerced.append(raw)
            elif isinstance(raw, Mapping):
                coerced.append(Keyframe.from_dict(raw))
            else:
                raise EDLError(
                    "KEYFRAME_SHAPE", f"keyframe is not a Keyframe or mapping: {raw!r}"
                )
        self.keys = coerced

    # -- ordering ---------------------------------------------------------
    def order_problem(self) -> Optional[str]:
        """Return a human detail when the time axis is not strictly increasing."""
        previous: Optional[float] = None
        for position, key in enumerate(self.keys):
            if not math.isfinite(key.t):
                return f"keyframe {position}: t is not finite ({key.t!r})"
            if not math.isfinite(key.value):
                return f"keyframe {position}: value is not finite ({key.value!r})"
            if previous is not None and key.t <= previous:
                return (
                    f"keyframe {position}: t={key.t:.6f} does not advance past "
                    f"the previous t={previous:.6f}"
                )
            previous = key.t
        return None

    def ordered(self) -> List[Keyframe]:
        problem = self.order_problem()
        if problem is not None:
            raise EDLError("KEYFRAME_ORDER", problem)
        return self.keys

    # -- sampling ---------------------------------------------------------
    def sample(self, t: float) -> float:
        keys = self.ordered()
        if not keys:
            raise EDLError("KEYFRAME_EMPTY", "cannot sample an empty track")
        if t <= keys[0].t:
            return keys[0].value
        if t >= keys[-1].t:
            return keys[-1].value
        for left, right in zip(keys, keys[1:]):
            if left.t <= t <= right.t:
                if left.interp == "hold":
                    return left.value
                span = right.t - left.t
                if span <= 0.0:
                    return right.value
                x = (t - left.t) / span
                if left.interp == "linear":
                    return left.value + (right.value - left.value) * x
                if left.interp == "bezier":
                    return left.value + (right.value - left.value) * _ease_in_out(x)
                raise EDLError(
                    "KEYFRAME_INTERP",
                    f"unknown interpolation {left.interp!r}",
                )
        return keys[-1].value

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> List[Dict[str, Any]]:
        return [key.to_dict() for key in self.keys]

    @classmethod
    def from_list(cls, raw: Any) -> "KeyframeTrack":
        if isinstance(raw, KeyframeTrack):
            return cls(list(raw.keys))
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise EDLError("KEYFRAME_SHAPE", f"track is not a list: {raw!r}")
        return cls(list(raw))


def sample(track: KeyframeTrack, t: float) -> float:
    """Module-level convenience wrapper around :meth:`KeyframeTrack.sample`."""
    return track.sample(t)


def _coerce_tracks(raw: Any) -> Dict[str, KeyframeTrack]:
    tracks: Dict[str, KeyframeTrack] = {}
    if isinstance(raw, Mapping):
        for name, value in raw.items():
            tracks[str(name)] = KeyframeTrack.from_list(value)
    return tracks


# ----------------------------------------------------------------- effects


def param_domain_problem(kind: str, name: str, value: float) -> Optional[str]:
    """Return a human detail when ``value`` is outside the parameter domain."""
    if not math.isfinite(value):
        return f"{kind}.{name} is not finite ({value!r})"

    def between(low: float, high: float) -> Optional[str]:
        if low <= value <= high:
            return None
        return f"{kind}.{name}={value} outside [{low}, {high}]"

    if kind == "transform":
        if name == "opacity":
            return between(0.0, 1.0)
        if name == "scale":
            if 0.0 < value <= 16.0:
                return None
            return f"transform.scale={value} outside (0, 16]"
    elif kind == "crop":
        if name in ("x", "y", "width", "height"):
            return between(0.0, 1.0)
    elif kind == "color":
        if name == "brightness":
            return between(-1.0, 1.0)
        if name == "contrast":
            return between(0.0, 4.0)
        if name == "saturation":
            return between(0.0, 3.0)
    elif kind == "blur":
        if name == "sigma":
            if 0.0 < value <= 100.0:
                return None
            return f"blur.sigma={value} outside (0, 100]"
    elif kind == "chroma_key":
        if name in ("similarity", "blend"):
            return between(0.0, 1.0)

    spec = EFFECT_REGISTRY.get(kind)
    if spec is not None and spec.params_spec:
        declared = spec.param(name)
        if declared is None:
            return f"{kind}.{name} is not a declared parameter"
        if not declared.contains(value):
            return (
                f"{kind}.{name}={value} outside "
                f"[{declared.lo:g}, {declared.hi:g}]"
            )
    return None


def select_backend(kind: str, keyframe_count: int = 0, duration: float = 0.0) -> str:
    """Return the ffmpeg mechanism this registry entry is rendered with.

    The rule is total over the registry, so no animatable kind can reach the
    compiler without a declared mechanism:

    * text layers and captions are composited, never drawn by a filter, so they
      return :data:`TEXT_BACKEND`;
    * a static entry returns the backend its :class:`EffectSpec` declares;
    * an animatable entry returns its declared backend, except that a
      mechanism-flexible ``expr`` entry with more than
      :data:`SENDCMD_THRESHOLD` keyframes returns ``sendcmd``.

    ``duration`` is part of the surface because a mechanism may legitimately
    depend on clip length, but no entry in the current registry does: duration
    changes the number of ``sendcmd`` commands emitted (``frames + 1``), never
    the choice between mechanisms. It is therefore accepted and unused, which is
    what keeps this function equal to ``vx.edl3_render.select_backend`` for every
    registry entry. An unknown kind is refused with ``EFFECT_UNKNOWN``.
    """
    name = str(kind)
    if name in TEXT_MECHANISM_KINDS:
        return TEXT_BACKEND
    spec = EFFECT_REGISTRY.get(name)
    if spec is None:
        raise EDLError(
            "EFFECT_UNKNOWN",
            f"no selection rule for effect kind {name!r}; "
            f"declared kinds are {sorted(EFFECT_REGISTRY)}",
            "kind",
        )
    if not spec.animatable:
        return spec.backend
    try:
        count = int(keyframe_count)
    except (TypeError, ValueError) as exc:
        raise EDLError(
            "KEYFRAME_SHAPE",
            f"keyframe_count must be an integer, got {keyframe_count!r}",
            "keyframes",
        ) from exc
    if (
        spec.backend == "expr"
        and name in FLEXIBLE_EXPR_KINDS
        and count > SENDCMD_THRESHOLD
    ):
        return "sendcmd"
    return spec.backend


@dataclass
class Effect:
    """One effect instance. ``keyframes`` maps a param name to its track."""

    kind: str
    params: Dict[str, float] = field(default_factory=dict)
    keyframes: Dict[str, KeyframeTrack] = field(default_factory=dict)
    backend: Optional[str] = None
    properties: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.kind = str(self.kind)
        coerced_params: Dict[str, float] = {}
        for name, value in (self.params or {}).items():
            coerced_params[str(name)] = _float(value)
        self.params = coerced_params
        self.keyframes = _coerce_tracks(self.keyframes or {})
        self.backend = str(self.backend) if self.backend is not None else None
        if isinstance(self.properties, str):
            self.properties = (self.properties,)
        else:
            self.properties = tuple(str(name) for name in (self.properties or ()))

    @property
    def spec(self) -> Optional[EffectSpec]:
        return EFFECT_REGISTRY.get(self.kind)

    @property
    def declared_backend(self) -> str:
        if self.backend:
            return self.backend
        spec = self.spec
        return "" if spec is None else spec.backend

    def sample(self, name: str, t: float) -> Optional[float]:
        """Animated value when a track exists, otherwise the static param."""
        track = self.keyframes.get(name)
        if track is not None:
            return track.sample(t)
        value = self.params.get(name)
        return None if value is None else float(value)

    def keyframe_count(self) -> int:
        return sum(len(track.keys) for track in self.keyframes.values())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "params": dict(self.params),
            "keyframes": {
                name: track.to_dict() for name, track in self.keyframes.items()
            },
            "backend": self.backend,
            "properties": list(self.properties),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Effect":
        if not isinstance(data, Mapping):
            raise EDLError("EFFECT_SHAPE", f"effect is not an object: {data!r}")
        return cls(
            kind=str(data.get("kind", "")),
            params=dict(data.get("params") or {}),
            keyframes=_coerce_tracks(data.get("keyframes") or {}),
            backend=data.get("backend"),
            properties=tuple(data.get("properties") or ()),
        )


def _coerce_effects(raw: Any) -> List[Effect]:
    return [
        item if isinstance(item, Effect) else Effect.from_dict(item)
        for item in (raw or [])
    ]


def _validate_effect(
    effect: Effect,
    where: str,
    duration: float,
    add,
) -> None:
    """Validate one effect in place, including every keyframe track."""
    spec = EFFECT_REGISTRY.get(effect.kind)
    if spec is None:
        add("EFFECT_UNKNOWN", f"{where}.kind", f"unknown effect kind {effect.kind!r}")
        return

    for name, value in effect.params.items():
        if name not in spec.params:
            add(
                "EFFECT_PARAM",
                f"{where}.params.{name}",
                f"unknown {effect.kind} param {name!r} (declared: {list(spec.params)})",
            )
            continue
        problem = param_domain_problem(effect.kind, name, value)
        if problem is not None:
            add("EFFECT_RANGE", f"{where}.params.{name}", problem)

    if effect.kind == "crop":
        for axis in ("x", "y"):
            start = effect.params.get(axis)
            size = effect.params.get("width" if axis == "x" else "height")
            if start is not None and size is not None and start + size > 1.0 + 1e-9:
                add(
                    "EFFECT_RANGE",
                    f"{where}.params.{axis}",
                    f"crop {axis}={start} + size={size} exceeds the source (1.0)",
                )

    for name, track in effect.keyframes.items():
        track_where = f"{where}.keyframes.{name}"
        if name not in spec.params:
            add(
                "KEYFRAME_PARAM",
                track_where,
                f"unknown keyframable {effect.kind} param {name!r}",
            )
            continue
        if not track.keys:
            add("KEYFRAME_EMPTY", track_where, "a keyframe track needs one key")
        if not spec.animatable or name not in spec.animated_params:
            add(
                "EFFECT_NOT_ANIMATABLE",
                track_where,
                f"{effect.kind}.{name} is not animatable "
                f"(animated: {list(spec.animated_params) or 'none'})",
            )
        order = track.order_problem()
        if order is not None:
            add("KEYFRAME_ORDER", track_where, order)
            continue
        for position, key in enumerate(track.keys):
            key_where = f"{track_where}[{position}]"
            if key.interp not in INTERPOLATIONS:
                add(
                    "KEYFRAME_INTERP",
                    key_where,
                    f"unknown interpolation {key.interp!r}",
                )
            if key.t < 0.0 or key.t > duration + 1e-9:
                add(
                    "KEYFRAME_RANGE",
                    key_where,
                    f"t={key.t} outside the clip [0, {duration}]",
                )
            problem = param_domain_problem(effect.kind, name, key.value)
            if problem is not None:
                add("KEYFRAME_RANGE", key_where, problem)


def _validate_clip_time(clip: Clip3, where: str, add) -> None:
    """Shape and domain of ``speed``/``reverse``/``freeze``/``compose``.

    Caps and modes are the planning modules' business: this only refuses a
    document whose numbers cannot describe a treatment at all.
    """
    if isinstance(clip.speed, list):
        if len(clip.speed) < 2:
            add(
                "SPEED_RANGE",
                f"{where}.speed",
                f"a ramp needs at least 2 keyframes, got {len(clip.speed)}",
            )
        for index, point in enumerate(clip.speed):
            for key in ("t_timeline", "t_source"):
                value = point[key]
                if not math.isfinite(value) or value < 0.0:
                    add(
                        "SPEED_RANGE",
                        f"{where}.speed[{index}].{key}",
                        f"{key} must be finite and >= 0, got {value}",
                    )
        for index in range(1, len(clip.speed)):
            left = clip.speed[index - 1]
            right = clip.speed[index]
            if (
                right["t_timeline"] <= left["t_timeline"]
                or right["t_source"] <= left["t_source"]
            ):
                add(
                    "SPEED_RANGE",
                    f"{where}.speed[{index}]",
                    f"{right} does not advance past {left} on both axes",
                )
    elif clip.speed is not None:
        ratio = float(clip.speed)
        if not math.isfinite(ratio) or ratio <= 0.0:
            add(
                "SPEED_RANGE",
                f"{where}.speed",
                f"a speed ratio must be finite and > 0, got {ratio}",
            )

    if clip.freeze is not None:
        missing = [key for key in ("t", "hold") if key not in clip.freeze]
        if missing:
            add(
                "FREEZE_SHAPE",
                f"{where}.freeze",
                f"freeze needs t and hold, missing {missing}",
            )
        else:
            if not math.isfinite(clip.freeze["t"]) or clip.freeze["t"] < 0.0:
                add(
                    "FREEZE_SHAPE",
                    f"{where}.freeze.t",
                    f"t must be finite and >= 0, got {clip.freeze['t']}",
                )
            if not math.isfinite(clip.freeze["hold"]) or clip.freeze["hold"] <= 0.0:
                add(
                    "FREEZE_SHAPE",
                    f"{where}.freeze.hold",
                    f"hold must be finite and > 0, got {clip.freeze['hold']}",
                )

    if clip.compose is not None:
        op = clip.compose.get("op")
        if not isinstance(op, str) or not op.strip():
            add(
                "COMPOSE_SHAPE",
                f"{where}.compose.op",
                "compose needs a non-empty op (pip/mask/blend/chroma)",
            )
        if clip.layer < 1:
            add(
                "COMPOSE_SHAPE",
                f"{where}.compose",
                "compose composites onto the layer below, so it applies to a "
                f"clip on layer >= 1; this clip sits on layer {clip.layer}",
            )

    if clip.audio_policy is not None and clip.audio_policy not in AUDIO_TIME_POLICIES:
        add(
            "AUDIO_POLICY",
            f"{where}.audio_policy",
            f"audio policy {clip.audio_policy!r} not in {sorted(AUDIO_TIME_POLICIES)}",
        )


# ------------------------------------------------------------- transitions


@dataclass
class Transition:
    """The transition that precedes a clip."""

    kind: str = "fade_in"
    duration: float = 0.0

    def __post_init__(self) -> None:
        self.kind = str(self.kind)
        self.duration = _float(self.duration)

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "duration": self.duration}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Transition":
        if not isinstance(data, Mapping):
            raise EDLError("TRANSITION_SHAPE", f"transition is not an object: {data!r}")
        return cls(
            kind=str(data.get("kind", "fade_in")),
            duration=_float(data.get("duration", 0.0)),
        )


# ------------------------------------------------------------------- clips


def _speed_point(raw: Any, where: str) -> Dict[str, float]:
    """One ``(t_timeline, t_source)`` keyframe in its serialised form."""
    if isinstance(raw, Mapping):
        if "t_timeline" in raw and "t_source" in raw:
            pair = (raw["t_timeline"], raw["t_source"])
        elif "timeline" in raw and "source" in raw:
            pair = (raw["timeline"], raw["source"])
        else:
            raise EDLError(
                "SPEED_RANGE",
                f"{where}: a keyframe needs t_timeline/t_source, got {raw!r}",
            )
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        if len(raw) != 2:
            raise EDLError(
                "SPEED_RANGE",
                f"{where}: a keyframe pair holds 2 values, got {len(raw)}",
            )
        pair = (raw[0], raw[1])
    else:
        raise EDLError(
            "SPEED_RANGE", f"{where}: a keyframe is a pair or a mapping, got {raw!r}"
        )
    try:
        return {"t_timeline": float(pair[0]), "t_source": float(pair[1])}
    except (TypeError, ValueError) as exc:
        raise EDLError(
            "SPEED_RANGE", f"{where}: a keyframe time is not a number: {raw!r}"
        ) from exc


def _speed_value(raw: Any) -> Any:
    """``None``, a ratio, or a list of keyframes: the three declared shapes."""
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        raw = raw.get("points")
        if raw is None:
            raise EDLError("SPEED_RANGE", "a speed mapping must carry a 'points' list")
    if isinstance(raw, bool):
        raise EDLError("SPEED_RANGE", "speed is a ratio or keyframes, not a boolean")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return [
            _speed_point(item, f"speed point {index}") for index, item in enumerate(raw)
        ]
    raise EDLError("SPEED_RANGE", f"speed is a ratio or keyframes, got {raw!r}")


def _freeze_value(raw: Any) -> Optional[Dict[str, float]]:
    """``{t, hold}`` in seconds, or ``None``. Unknown keys are dropped."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise EDLError("FREEZE_SHAPE", f"freeze is an object {{t, hold}}, got {raw!r}")
    spec: Dict[str, float] = {}
    for key in ("t", "hold"):
        if key not in raw:
            continue
        try:
            spec[key] = float(raw[key])
        except (TypeError, ValueError) as exc:
            raise EDLError(
                "FREEZE_SHAPE", f"freeze.{key} is not a number: {raw[key]!r}"
            ) from exc
    return spec


def _compose_value(raw: Any) -> Optional[Dict[str, Any]]:
    """The raw compositing declaration; its semantics belong to the builders."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise EDLError("COMPOSE_SHAPE", f"compose is an object, got {raw!r}")
    return dict(raw)


def _generator_value(raw: Any) -> Any:
    """The raw generator declaration, kept as given so ``validate`` can name it.

    Coercing here would hide the shape problem behind a later, vaguer error, so a
    non-object is carried through untouched and ``GENERATOR_SHAPE`` is raised by
    ``validate()`` with the offending value in the detail.
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        return raw
    return dict(raw)


def _records(value: Any) -> Tuple[Dict[str, Any], ...]:
    """A tuple of plain dicts out of a list of mappings; anything else is dropped."""
    if value is None:
        return ()
    items = value if isinstance(value, (list, tuple)) else (value,)
    return tuple(dict(item) for item in items if isinstance(item, Mapping))


@dataclass
class Clip3:
    """One segment on the timeline.

    Exactly one of ``source`` (a file), ``sequence`` (a
    :class:`NestedSequence` id) or ``generator`` (a captured Motion Studio
    template) is meaningful. ``dst_in``/``dst_out`` are the
    placement on the timeline; when omitted the clip is concatenated after the
    previous one, which is what the slice renderer has always done.

    ``speed`` is a ratio or a list of ``(t_timeline, t_source)`` keyframes;
    ``reverse``/``freeze`` are the time extremes and ``compose`` the layer
    compositing declaration (``op`` plus its parameters).
    """

    id: str
    source: str = ""
    src_in: float = 0.0
    src_out: float = 0.0
    effects: List[Effect] = field(default_factory=list)
    transition_in: Optional[Transition] = None
    transition_out: Optional[Transition] = None
    sequence: Optional[str] = None
    dst_in: Optional[float] = None
    dst_out: Optional[float] = None
    #: Video layer: 0 is the base track that concatenates; >= 1 overlays.
    layer: int = 0
    #: Speed ramp: a positive ratio or keyframes; ``None`` plays at 1x.
    speed: Optional[Any] = None
    #: Reverse picture and audio across the whole clip.
    reverse: bool = False
    #: ``{"t": seconds, "hold": seconds}`` single-frame hold.
    freeze: Optional[Dict[str, float]] = None
    #: ``{"op": "pip"|"mask"|"blend"|"chroma", ...params}`` on a layer >= 1 clip.
    compose: Optional[Dict[str, Any]] = None
    #: Declared audio time policy for the ramp; ``None`` means the compiler's
    #: default (``segments``), reported as a default rather than as a choice.
    audio_policy: Optional[str] = None
    #: ``{"template", "params", "engine", "alpha"}``: a captured Motion Studio
    #: template as the clip's picture, instead of ``source`` or ``sequence``.
    generator: Optional[Dict[str, Any]] = None
    #: Named preset resolved from a registry: camera move, profile, lens, palette, light.
    preset_ref: Optional[str] = None
    #: Up to three camera movements composed on this shot, in order.
    stack: Tuple[Dict[str, Any], ...] = ()
    #: The clip is generated content, not captured material.
    synthetic: bool = False
    #: Evidence records behind the clip's creative choices.
    provenance: Tuple[Dict[str, Any], ...] = ()
    #: Named Motion Studio template, resolved against its declared parameters.
    template_ref: Optional[str] = None
    #: The values this document sets; the rest come from the template's defaults.
    exposed_params: Dict[str, Any] = field(default_factory=dict)
    #: Licence, author, source and URL for third-party audio or pictures.
    attribution: Tuple[Dict[str, Any], ...] = ()
    #: Unknown ``x-`` fields, preserved verbatim across a round trip.
    ext: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = str(self.id)
        self.source = str(self.source)
        self.src_in = _float(self.src_in)
        self.src_out = _float(self.src_out)
        self.effects = _coerce_effects(self.effects)
        if isinstance(self.transition_in, Mapping):
            self.transition_in = Transition.from_dict(self.transition_in)
        if isinstance(self.transition_out, Mapping):
            self.transition_out = Transition.from_dict(self.transition_out)
        self.sequence = None if self.sequence is None else str(self.sequence)
        self.dst_in = None if self.dst_in is None else _float(self.dst_in)
        self.dst_out = None if self.dst_out is None else _float(self.dst_out)
        try:
            self.layer = int(self.layer)
        except (TypeError, ValueError):
            self.layer = -1
        self.speed = _speed_value(self.speed)
        self.reverse = bool(self.reverse)
        self.freeze = _freeze_value(self.freeze)
        self.compose = _compose_value(self.compose)
        self.audio_policy = (
            None if self.audio_policy is None else str(self.audio_policy)
        )
        self.generator = _generator_value(self.generator)
        self.preset_ref = None if self.preset_ref is None else str(self.preset_ref)
        self.template_ref = (
            None if self.template_ref is None else str(self.template_ref)
        )
        self.stack = _records(self.stack)
        self.provenance = _records(self.provenance)
        self.attribution = _records(self.attribution)
        self.synthetic = bool(self.synthetic)
        self.exposed_params = dict(self.exposed_params or {})
        self.ext = dict(self.ext or {})

    @property
    def duration(self) -> float:
        """Source duration: ``src_out - src_in``."""
        return self.src_out - self.src_in

    @property
    def timeline_duration(self) -> float:
        """Duration on the timeline when declared, otherwise the source one."""
        if self.dst_in is not None and self.dst_out is not None:
            return self.dst_out - self.dst_in
        return self.duration

    def transform(self) -> Optional[Effect]:
        for effect in self.effects:
            if effect.kind == "transform":
                return effect
        return None

    def effect(self, kind: str) -> Optional[Effect]:
        for item in self.effects:
            if item.kind == kind:
                return item
        return None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.id,
            "source": self.source,
            "sequence": self.sequence,
            "src_in": self.src_in,
            "src_out": self.src_out,
            "dst_in": self.dst_in,
            "dst_out": self.dst_out,
            "layer": self.layer,
            "speed": self.speed,
            "reverse": self.reverse,
            "freeze": dict(self.freeze) if self.freeze is not None else None,
            "compose": dict(self.compose) if self.compose is not None else None,
            "audio_policy": self.audio_policy,
            "generator": (
                dict(self.generator)
                if isinstance(self.generator, Mapping)
                else self.generator
            ),
            "effects": [effect.to_dict() for effect in self.effects],
            "transition_in": (
                self.transition_in.to_dict() if self.transition_in is not None else None
            ),
            "transition_out": (
                self.transition_out.to_dict()
                if self.transition_out is not None
                else None
            ),
        }
        if self.preset_ref:
            data["preset_ref"] = self.preset_ref
        if self.template_ref:
            data["template_ref"] = self.template_ref
        if self.stack:
            data["stack"] = [dict(item) for item in self.stack]
        if self.synthetic:
            data["synthetic"] = True
        if self.provenance:
            data["provenance"] = [dict(item) for item in self.provenance]
        if self.exposed_params:
            data["exposed_params"] = dict(self.exposed_params)
        if self.attribution:
            data["attribution"] = [dict(item) for item in self.attribution]
        data.update(self.ext)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Clip3":
        if not isinstance(data, Mapping):
            raise EDLError("CLIP_SHAPE", f"clip is not an object: {data!r}")
        raw_transition = data.get("transition_in")
        raw_transition_out = data.get("transition_out")
        ext = dict(data.get("ext") or {})
        for key, value in data.items():
            if isinstance(key, str) and key.startswith("x-"):
                ext[key] = value
        return cls(
            id=str(data.get("id", "")),
            source=str(data.get("source") or ""),
            src_in=_float(data.get("src_in", 0.0)),
            src_out=_float(data.get("src_out", 0.0)),
            effects=_coerce_effects(data.get("effects")),
            transition_in=(
                Transition.from_dict(raw_transition)
                if isinstance(raw_transition, Mapping)
                else None
            ),
            transition_out=(
                Transition.from_dict(raw_transition_out)
                if isinstance(raw_transition_out, Mapping)
                else None
            ),
            sequence=(
                None if data.get("sequence") is None else str(data.get("sequence"))
            ),
            dst_in=None if data.get("dst_in") is None else _float(data.get("dst_in")),
            dst_out=None
            if data.get("dst_out") is None
            else _float(data.get("dst_out")),
            layer=data.get("layer", 0),
            speed=data.get("speed"),
            reverse=data.get("reverse", False),
            freeze=data.get("freeze"),
            compose=data.get("compose"),
            audio_policy=data.get("audio_policy"),
            generator=data.get("generator"),
            preset_ref=data.get("preset_ref"),
            stack=data.get("stack"),
            synthetic=data.get("synthetic", False),
            provenance=data.get("provenance"),
            template_ref=data.get("template_ref"),
            exposed_params=data.get("exposed_params"),
            attribution=data.get("attribution"),
            ext=ext,
        )


# -------------------------------------------------------- adjustment layers


@dataclass
class AdjustmentLayer:
    """Effects applied to every clip below ``track`` on the same segment."""

    track: int = 1
    effects: List[Effect] = field(default_factory=list)

    def __post_init__(self) -> None:
        try:
            self.track = int(self.track)
        except (TypeError, ValueError):
            self.track = -1
        self.effects = _coerce_effects(self.effects)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "track": self.track,
            "effects": [effect.to_dict() for effect in self.effects],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AdjustmentLayer":
        if not isinstance(data, Mapping):
            raise EDLError(
                "ADJUSTMENT_SHAPE", f"adjustment layer is not an object: {data!r}"
            )
        return cls(
            track=data.get("track", 1), effects=_coerce_effects(data.get("effects"))
        )


# -------------------------------------------------------------------- text


@dataclass
class TextStyle:
    """Declared text style. Colour is ``#rrggbb``; position is normalised."""

    family: str = "Antonio"
    size: float = 48.0
    color: str = "#ffffff"
    stroke: float = 0.0
    position: Tuple[float, float] = (0.5, 0.85)
    align: str = "center"

    def __post_init__(self) -> None:
        self.family = str(self.family)
        self.size = _float(self.size)
        self.color = str(self.color)
        self.stroke = _float(self.stroke)
        position = self.position
        if isinstance(position, Mapping):
            position = (position.get("x", 0.5), position.get("y", 0.85))
        if isinstance(position, Sequence) and not isinstance(position, (str, bytes)):
            values = list(position)
        else:
            values = []
        if len(values) != 2:
            values = [0.5, 0.85]
        self.position = (_float(values[0]), _float(values[1]))
        self.align = str(self.align)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "family": self.family,
            "size": self.size,
            "color": self.color,
            "stroke": self.stroke,
            "position": [self.position[0], self.position[1]],
            "align": self.align,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TextStyle":
        if not isinstance(data, Mapping):
            return cls()
        return cls(
            family=data.get("family", "Antonio"),
            size=data.get("size", 48.0),
            color=data.get("color", "#ffffff"),
            stroke=data.get("stroke", 0.0),
            position=data.get("position", (0.5, 0.85)),
            align=data.get("align", "center"),
        )


@dataclass
class TextLayer:
    """A text overlay. Validated and serialised; not rendered until F2-T07."""

    text: str = ""
    style: TextStyle = field(default_factory=TextStyle)
    time_in: float = 0.0
    time_out: float = 0.0
    keyframes: Dict[str, KeyframeTrack] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.text = str(self.text)
        if not isinstance(self.style, TextStyle):
            self.style = TextStyle.from_dict(self.style)
        self.time_in = _float(self.time_in)
        self.time_out = _float(self.time_out)
        self.keyframes = _coerce_tracks(self.keyframes or {})

    @property
    def duration(self) -> float:
        return self.time_out - self.time_in

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "style": self.style.to_dict(),
            "in": self.time_in,
            "out": self.time_out,
            "keyframes": {
                name: track.to_dict() for name, track in self.keyframes.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TextLayer":
        if not isinstance(data, Mapping):
            raise EDLError("TEXT_SHAPE", f"text layer is not an object: {data!r}")
        return cls(
            text=data.get("text", ""),
            style=TextStyle.from_dict(data.get("style")),
            time_in=_float(data.get("in", 0.0)),
            time_out=_float(data.get("out", 0.0)),
            keyframes=_coerce_tracks(data.get("keyframes") or {}),
        )


# ---------------------------------------------------------------- captions


@dataclass
class CaptionCue:
    """One caption: spoken text with an in/out window."""

    text: str = ""
    time_in: float = 0.0
    time_out: float = 0.0

    def __post_init__(self) -> None:
        self.text = str(self.text)
        self.time_in = _float(self.time_in)
        self.time_out = _float(self.time_out)

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "in": self.time_in, "out": self.time_out}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CaptionCue":
        if not isinstance(data, Mapping):
            raise EDLError("CAPTION_SHAPE", f"caption cue is not an object: {data!r}")
        return cls(
            text=data.get("text", ""),
            time_in=_float(data.get("in", 0.0)),
            time_out=_float(data.get("out", 0.0)),
        )


@dataclass
class CaptionTrack:
    """A cue list. Validated and serialised; the compiler refuses it loudly."""

    clips: List[CaptionCue] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.clips = [
            cue if isinstance(cue, CaptionCue) else CaptionCue.from_dict(cue)
            for cue in (self.clips or [])
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {"clips": [cue.to_dict() for cue in self.clips]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CaptionTrack":
        if not isinstance(data, Mapping):
            raise EDLError("CAPTION_SHAPE", f"caption track is not an object: {data!r}")
        return cls(clips=data.get("clips") or [])


# ------------------------------------------------------------------- audio


@dataclass
class AudioClip3:
    """A music/dialogue segment carried through migration (not mixed here)."""

    source: str = ""
    src_in: float = 0.0
    src_out: float = 0.0
    dst_in: float = 0.0
    dst_out: float = 0.0
    gain_db: float = 0.0

    def __post_init__(self) -> None:
        self.source = str(self.source)
        self.src_in = _float(self.src_in)
        self.src_out = _float(self.src_out)
        self.dst_in = _float(self.dst_in)
        self.dst_out = _float(self.dst_out)
        self.gain_db = _float(self.gain_db)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "src_in": self.src_in,
            "src_out": self.src_out,
            "dst_in": self.dst_in,
            "dst_out": self.dst_out,
            "gain_db": self.gain_db,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AudioClip3":
        if not isinstance(data, Mapping):
            raise EDLError("AUDIO_SHAPE", f"audio clip is not an object: {data!r}")
        return cls(
            source=data.get("source", ""),
            src_in=_float(data.get("src_in", 0.0)),
            src_out=_float(data.get("src_out", 0.0)),
            dst_in=_float(data.get("dst_in", 0.0)),
            dst_out=_float(data.get("dst_out", 0.0)),
            gain_db=_float(data.get("gain_db", 0.0)),
        )


@dataclass
class AudioTrack3:
    """An audio bus: ``music`` feeds the music bed, anything else dialogue."""

    role: str = ""
    clips: List[AudioClip3] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.role = str(self.role)
        self.clips = [
            clip if isinstance(clip, AudioClip3) else AudioClip3.from_dict(clip)
            for clip in (self.clips or [])
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "clips": [clip.to_dict() for clip in self.clips]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AudioTrack3":
        if not isinstance(data, Mapping):
            raise EDLError("AUDIO_SHAPE", f"audio track is not an object: {data!r}")
        return cls(role=data.get("role", ""), clips=data.get("clips") or [])


@dataclass
class Ducking3:
    """Declared sidechain ducking, mirroring EDL 2.2 ``DuckingSpec``."""

    sidechain_role: str = "dialogue"
    target_role: str = "music"
    threshold: float = 0.05
    ratio: float = 8.0
    attack_ms: float = 20.0
    release_ms: float = 250.0
    makeup_db: float = 0.0

    def __post_init__(self) -> None:
        self.sidechain_role = str(self.sidechain_role)
        self.target_role = str(self.target_role)
        for name in ("threshold", "ratio", "attack_ms", "release_ms", "makeup_db"):
            setattr(self, name, _float(getattr(self, name)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sidechain_role": self.sidechain_role,
            "target_role": self.target_role,
            "threshold": self.threshold,
            "ratio": self.ratio,
            "attack_ms": self.attack_ms,
            "release_ms": self.release_ms,
            "makeup_db": self.makeup_db,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Ducking3":
        if not isinstance(data, Mapping):
            raise EDLError("DUCKING_SHAPE", f"ducking is not an object: {data!r}")
        return cls(
            sidechain_role=data.get("sidechain_role", "dialogue"),
            target_role=data.get("target_role", "music"),
            threshold=data.get("threshold", 0.05),
            ratio=data.get("ratio", 8.0),
            attack_ms=data.get("attack_ms", 20.0),
            release_ms=data.get("release_ms", 250.0),
            makeup_db=data.get("makeup_db", 0.0),
        )


# ------------------------------------------------------------- sequences


@dataclass
class NestedSequence:
    """A named inner timeline, referenced by ``Clip3.sequence``."""

    id: str = ""
    timeline: "Timeline3" = field(default_factory=lambda: Timeline3(fps=1, size=(2, 2)))

    def __post_init__(self) -> None:
        self.id = str(self.id)
        if not isinstance(self.timeline, Timeline3):
            self.timeline = Timeline3.from_dict(self.timeline)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "timeline": self.timeline.to_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NestedSequence":
        if not isinstance(data, Mapping):
            raise EDLError("SEQUENCE_SHAPE", f"sequence is not an object: {data!r}")
        return cls(id=data.get("id", ""), timeline=data.get("timeline") or {})


# ---------------------------------------------------------------- timeline


def _clip_start_times(timeline: "Timeline3") -> List[float]:
    """Timeline start of every clip, in document order."""
    cursor = 0.0
    starts: List[float] = []
    for clip in timeline.clips:
        start = cursor if clip.dst_in is None else float(clip.dst_in)
        if clip.layer == 0:
            cursor = max(cursor, start + max(0.0, clip.timeline_duration))
        starts.append(start)
    return starts


def check_effect_density(
    timeline: "Timeline3",
    limit: int = EFFECT_DENSITY_LIMIT,
    window: float = EFFECT_DENSITY_WINDOW,
) -> List[str]:
    """Return one human detail per window holding more than ``limit`` effects.

    No more than ``limit`` visible effect instances may *start* inside any
    window of ``window`` seconds on one timeline. The window slides and both
    edges are inclusive: an instance at ``t`` and one at ``t + window`` share a
    window whenever the material spans it.

    An instance is one library entry of ``Clip3.effects`` on a clip that
    actually renders, meaning it carries a source picture or a generator. A clip
    that only references a nested sequence is measured on its own inner
    timeline; text layers and captions are not ``Effect`` entities, so they
    never count; and an adjustment layer's effects apply to a whole segment and
    carry no start of their own, so they are outside the rule.

    The primitives (``transform``, ``crop``, ``color``, ``blur``, ``chroma_key``)
    are the compositing stack every clip may carry, not looks: they are outside
    the rule. What the cap governs is the declarative library, where the
    patchwork risk lives, so a five-primitive stack stays legal while four
    library looks in ten seconds do not. ``tests/test_edl3_contract.py`` stacks
    five primitives on one clip and must keep validating.

    ``Effect`` declares no per-instance start time, so each instance is
    anchored on its clip's start on the timeline: ``dst_in`` when declared,
    otherwise the concatenation cursor. Pure: the document is only read.
    """
    if limit < 1 or window <= 0.0:
        return []
    anchored: List[Tuple[float, str]] = []
    for start, clip in zip(_clip_start_times(timeline), timeline.clips):
        if not clip.source and clip.generator is None:
            continue
        for effect in clip.effects:
            spec = EFFECT_REGISTRY.get(effect.kind)
            if spec is None or spec.backend != BACKEND_FILTER:
                continue
            anchored.append((start, clip.id))
    anchored.sort(key=lambda item: item[0])

    problems: List[str] = []
    visited: List[float] = []
    for index, (start, _clip_id) in enumerate(anchored):
        if start in visited:
            continue
        visited.append(start)
        windowed = [
            item for item in anchored[index:] if item[0] - start <= window + 1e-9
        ]
        if len(windowed) <= limit:
            continue
        clips: List[str] = []
        for _t, clip_id in windowed:
            if clip_id not in clips:
                clips.append(clip_id)
        target = (
            f"clip {clips[0]}" if len(clips) == 1 else f"clips {', '.join(clips)}"
        )
        problems.append(
            f"{len(windowed)} effect instances start between {start:.3f}s and "
            f"{start + window:.3f}s, at most {limit} per {window:g}s (on {target})"
        )
    return problems



@dataclass
class Timeline3:
    """The versioned document."""

    fps: int
    size: Tuple[int, int]
    clips: List[Clip3] = field(default_factory=list)
    version: str = EDL3_VERSION
    sequences: Dict[str, NestedSequence] = field(default_factory=dict)
    adjustments: List[AdjustmentLayer] = field(default_factory=list)
    text_layers: List[TextLayer] = field(default_factory=list)
    captions: List[CaptionTrack] = field(default_factory=list)
    audio: List[AudioTrack3] = field(default_factory=list)
    ducking: Optional[Ducking3] = None
    #: Reference to the project's brand.json.
    brand_ref: Optional[str] = None
    #: Named genre profile from :mod:`vx.profiles` (D033). Declared data: no
    #: renderer, compiler or effect branches on it, and a document without it
    #: behaves exactly as before.
    profile_ref: Optional[str] = None
    #: ``{"id", "source_project", "axis"}`` for an A/B variant of a project.
    variant: Optional[Dict[str, Any]] = None
    #: Unknown ``x-`` fields, preserved verbatim across a round trip.
    ext: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.version = str(self.version)
        try:
            self.fps = int(self.fps)
        except (TypeError, ValueError):
            self.fps = -1
        raw_size = self.size
        if isinstance(raw_size, Mapping):
            width, height = raw_size.get("width", 0), raw_size.get("height", 0)
        elif isinstance(raw_size, Sequence) and not isinstance(raw_size, (str, bytes)):
            values = list(raw_size)
            width, height = (values + [0, 0])[:2]
        else:
            width, height = 0, 0
        self.size = (int(_float(width)), int(_float(height)))
        self.clips = [
            clip if isinstance(clip, Clip3) else Clip3.from_dict(clip)
            for clip in (self.clips or [])
        ]
        coerced_sequences: Dict[str, NestedSequence] = {}
        for key, raw in (self.sequences or {}).items():
            sequence = (
                raw
                if isinstance(raw, NestedSequence)
                else NestedSequence.from_dict(raw)
            )
            coerced_sequences[str(key)] = sequence
        self.sequences = coerced_sequences
        self.adjustments = [
            item
            if isinstance(item, AdjustmentLayer)
            else AdjustmentLayer.from_dict(item)
            for item in (self.adjustments or [])
        ]
        self.text_layers = [
            item if isinstance(item, TextLayer) else TextLayer.from_dict(item)
            for item in (self.text_layers or [])
        ]
        self.captions = [
            item if isinstance(item, CaptionTrack) else CaptionTrack.from_dict(item)
            for item in (self.captions or [])
        ]
        self.audio = [
            item if isinstance(item, AudioTrack3) else AudioTrack3.from_dict(item)
            for item in (self.audio or [])
        ]
        if isinstance(self.ducking, Mapping):
            self.ducking = Ducking3.from_dict(self.ducking)
        self.brand_ref = None if self.brand_ref is None else str(self.brand_ref)
        self.profile_ref = (
            None if self.profile_ref is None else str(self.profile_ref)
        )
        self.variant = (
            dict(self.variant) if isinstance(self.variant, Mapping) else None
        )
        self.ext = dict(self.ext or {})

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1]

    @property
    def duration(self) -> float:
        declared = [
            clip.dst_out
            for clip in self.clips
            if clip.dst_out is not None and clip.dst_in is not None
        ]
        if declared and len(declared) == len(self.clips):
            return max(declared)
        return sum(max(0.0, clip.duration) for clip in self.clips)

    # -- validation -------------------------------------------------------
    def validate(self, *, depth: int = 0, prefix: str = "") -> List[Dict[str, str]]:
        """Return every problem, never raising. ``depth`` is used for nesting."""
        problems: List[Dict[str, str]] = []

        def add(code: str, where: str, detail: str) -> None:
            problems.append(
                {"code": code, "where": f"{prefix}{where}", "detail": detail}
            )

        self._validate_header(add)
        self._validate_clips(add, depth, problems)
        self._validate_effect_density(add)
        self._validate_extras(add)
        self._validate_adjustments(add)
        self._validate_text(add)
        self._validate_captions(add)
        self._validate_audio(add)
        return problems

    def _validate_header(self, add) -> None:
        if self.version not in EDL3_VERSIONS:
            add("SCHEMA_VERSION", "version", f"unsupported version {self.version!r}")
        if self.fps <= 0:
            add("FPS_INVALID", "fps", f"fps must be positive, got {self.fps}")
        if self.width <= 0 or self.height <= 0:
            add(
                "SIZE_INVALID",
                "size",
                f"size must be positive, got {self.width}x{self.height}",
            )
        if not self.clips:
            add("TIMELINE_EMPTY", "clips", "a timeline needs at least one clip")

        for key, sequence in self.sequences.items():
            if not sequence.id:
                add("SEQUENCE_SHAPE", f"sequences.{key}.id", "sequence id is empty")
            elif sequence.id != key:
                add(
                    "SEQUENCE_SHAPE",
                    f"sequences.{key}.id",
                    f"sequence id {sequence.id!r} does not match its key {key!r}",
                )

    def _validate_extras(self, add) -> None:
        for index, clip in enumerate(self.clips):
            where = f"clips[{index}]"
            if len(clip.stack) > 3:
                add(
                    "STACK_TOO_DEEP",
                    f"{where}.stack",
                    f"{len(clip.stack)} movements, at most 3",
                )
            for position, movement in enumerate(clip.stack):
                if not str(movement.get("kind") or "").strip():
                    add(
                        "STACK_SHAPE",
                        f"{where}.stack[{position}]",
                        "a movement needs a kind",
                    )
            for name in ("preset_ref", "template_ref"):
                value = getattr(clip, name)
                if value is not None and not value.strip():
                    add("PRESET_REF_SHAPE", f"{where}.{name}", "empty reference")
            for name, declaration in (clip.exposed_params or {}).items():
                spot = f"{where}.exposed_params.{name}"
                if not isinstance(declaration, Mapping):
                    add("EXPOSED_PARAM_SHAPE", spot, "declaration is not an object")
                    continue
                if declaration.get("type") not in EXPOSED_TYPES:
                    add(
                        "EXPOSED_PARAM_SHAPE",
                        spot,
                        f"unknown type {declaration.get('type')!r}",
                    )
                if "default" not in declaration:
                    add("EXPOSED_PARAM_SHAPE", spot, "no default value")
            for position, record in enumerate(clip.provenance):
                spot = f"{where}.provenance[{position}]"
                if record.get("kind") not in PROVENANCE_KINDS:
                    add(
                        "PROVENANCE_SHAPE",
                        spot,
                        f"unknown kind {record.get('kind')!r}",
                    )
                for key in ("id", "source"):
                    if not str(record.get(key) or "").strip():
                        add("PROVENANCE_SHAPE", spot, f"missing {key}")
            for position, record in enumerate(clip.attribution):
                spot = f"{where}.attribution[{position}]"
                for key in ("licence", "author", "source", "url"):
                    if not str(record.get(key) or "").strip():
                        add("ATTRIBUTION_SHAPE", spot, f"missing {key}")
            for key in clip.ext:
                if not str(key).startswith("x-"):
                    add("EXT_KEY", f"{where}.ext", f"{key!r} must start with x-")
        if self.brand_ref is not None and not self.brand_ref.strip():
            add("BRAND_REF_SHAPE", "brand_ref", "empty reference")
        self._validate_profile_ref(add)
        if self.variant is not None:
            for key in ("id", "source_project", "axis"):
                if not str(self.variant.get(key) or "").strip():
                    add("VARIANT_SHAPE", "variant", f"missing {key}")
        for key in self.ext:
            if not str(key).startswith("x-"):
                add("EXT_KEY", "ext", f"{key!r} must start with x-")

    def _validate_profile_ref(self, add) -> None:
        """A declared genre profile must resolve; nothing else reads it here.

        The registry is imported on demand so a document without a profile never
        loads it (D033: the engine stays genre-agnostic).
        """
        if self.profile_ref is None:
            return
        if not self.profile_ref.strip():
            add("PROFILE_UNKNOWN", "profile_ref", "empty profile reference")
            return

        if self.version != "vx.edl3/1.2":
            add(
                "SCHEMA_VERSION",
                "profile_ref",
                f"profile_ref needs vx.edl3/1.2, the document declares {self.version!r}",
            )
            return

        from . import profiles  # noqa: PLC0415 - the registry is read, never imported

        try:
            profiles.resolve(self.profile_ref)
        except EDLError as exc:
            add("PROFILE_UNKNOWN", "profile_ref", exc.detail)

    def _validate_clips(self, add, depth: int, problems: List[Dict[str, str]]) -> None:
        declared = [
            clip
            for clip in self.clips
            if clip.dst_in is not None or clip.dst_out is not None
        ]
        if declared and len(declared) != len(self.clips):
            add(
                "CLIP_RANGE",
                "clips",
                "clips must declare dst_in/dst_out uniformly, not partially",
            )
        if self.clips and len(declared) == len(self.clips):
            self._validate_placement(add)

        seen_ids: Dict[str, int] = {}
        for index, clip in enumerate(self.clips):
            clip_where = f"clips[{index}]"
            if clip.id in seen_ids:
                add(
                    "DUPLICATE_CLIP_ID",
                    f"{clip_where}.id",
                    f"clip id {clip.id!r} already used by clips[{seen_ids[clip.id]}]",
                )
            seen_ids.setdefault(clip.id, index)

            if clip.sequence is not None:
                if clip.source:
                    add(
                        "CLIP_SHAPE",
                        clip_where,
                        "a clip declares both source and sequence",
                    )
                if not clip.sequence:
                    add("CLIP_SHAPE", f"{clip_where}.sequence", "sequence id is empty")
                elif clip.sequence not in self.sequences:
                    add(
                        "NESTING_UNKNOWN",
                        f"{clip_where}.sequence",
                        f"no nested sequence {clip.sequence!r} on this timeline",
                    )
            elif clip.generator is None and not clip.source:
                add("CLIP_RANGE", f"{clip_where}.source", "source is empty")

            if not math.isfinite(clip.src_in) or clip.src_in < 0.0:
                add(
                    "CLIP_RANGE",
                    f"{clip_where}.src_in",
                    f"src_in must be >= 0, got {clip.src_in}",
                )
            if not math.isfinite(clip.src_out) or clip.src_out <= clip.src_in:
                add(
                    "CLIP_RANGE",
                    f"{clip_where}.src_out",
                    f"src_out ({clip.src_out}) must be greater than src_in "
                    f"({clip.src_in})",
                )
            if clip.dst_in is not None and clip.dst_out is not None:
                if clip.dst_in < 0.0 or not math.isfinite(clip.dst_in):
                    add(
                        "CLIP_RANGE",
                        f"{clip_where}.dst_in",
                        f"dst_in must be >= 0, got {clip.dst_in}",
                    )
                if clip.dst_out <= clip.dst_in or not math.isfinite(clip.dst_out):
                    add(
                        "CLIP_RANGE",
                        f"{clip_where}.dst_out",
                        f"dst_out ({clip.dst_out}) must be greater than dst_in "
                        f"({clip.dst_in})",
                    )
                elif self.fps > 0 and clip.src_out > clip.src_in:
                    if (
                        abs(clip.timeline_duration - clip.duration)
                        > 1.0 / self.fps + 1e-9
                    ):
                        add(
                            "CLIP_RANGE",
                            clip_where,
                            f"dst duration {clip.timeline_duration:.4f} differs from "
                            f"src duration {clip.duration:.4f} by more than one frame",
                        )

            if clip.layer < 0:
                add(
                    "LAYER_RANGE",
                    f"{clip_where}.layer",
                    f"layer must be >= 0, got {clip.layer}",
                )
            elif clip.layer >= 1 and (clip.dst_in is None or clip.dst_out is None):
                add(
                    "LAYER_RANGE",
                    clip_where,
                    "a layer >= 1 overlay needs dst_in/dst_out: its window is "
                    "the only place it is visible",
                )

            duration = clip.timeline_duration
            for effect_index, effect in enumerate(clip.effects):
                _validate_effect(
                    effect,
                    f"{clip_where}.effects[{effect_index}]",
                    duration,
                    add,
                )

            _validate_clip_time(clip, clip_where, add)
            self._validate_generator(clip, clip_where, add)

            for slot, transition in (
                ("transition_in", clip.transition_in),
                ("transition_out", clip.transition_out),
            ):
                if transition is None:
                    continue
                transition_where = f"{clip_where}.{slot}"
                if transition.kind not in TRANSITION_KINDS:
                    add(
                        "TRANSITION_UNKNOWN",
                        f"{transition_where}.kind",
                        f"unknown transition kind {transition.kind!r}",
                    )
                if not math.isfinite(transition.duration) or transition.duration <= 0.0:
                    add(
                        "TRANSITION_RANGE",
                        f"{transition_where}.duration",
                        f"duration must be positive, got {transition.duration}",
                    )
                elif transition.duration > duration + 1e-9:
                    add(
                        "TRANSITION_TOO_LONG",
                        f"{transition_where}.duration",
                        f"duration {transition.duration} exceeds clip duration "
                        f"{duration}",
                    )

        self._validate_sequences(add, depth, problems)

    def _validate_generator(self, clip: Clip3, where: str, add) -> None:
        """The generator declaration, its placement and its exclusivity.

        A generator clip *is* the template: it starts at the template's frame 0,
        keeps the template's timing, and its timeline span is the template's
        duration within half a frame. Speed, reverse and freeze would reinterpret
        frames that were rendered for another clock, so they are refused instead
        of being applied twice.
        """
        raw = clip.generator
        if raw is None:
            return
        field_where = f"{where}.generator"
        if not isinstance(raw, Mapping):
            add(
                "GENERATOR_SHAPE",
                field_where,
                f"generator must be an object {{template, params, engine, alpha}}, "
                f"got {type(raw).__name__}",
            )
            return
        missing = sorted(GENERATOR_KEYS - set(raw))
        unknown = sorted(set(raw) - GENERATOR_KEYS)
        if missing:
            add(
                "GENERATOR_SHAPE",
                field_where,
                f"generator is missing {missing}",
            )
        if unknown:
            add(
                "GENERATOR_SHAPE",
                field_where,
                f"generator declares unknown key(s) {unknown} "
                f"(declared: {sorted(GENERATOR_KEYS)})",
            )

        if clip.source or clip.sequence is not None:
            add(
                "GENERATOR_SOURCE_CONFLICT",
                where,
                "a generator clip renders a captured template: it cannot also "
                "declare a source file or a nested sequence",
            )
        if clip.speed is not None:
            add(
                "GENERATOR_TIME_FX",
                f"{where}.speed",
                "a generator clip keeps the template's clock; speed would retime "
                "frames rendered for another one",
            )
        if clip.reverse:
            add(
                "GENERATOR_TIME_FX",
                f"{where}.reverse",
                "a generator clip keeps the template's clock; reverse is refused",
            )
        if clip.freeze is not None:
            add(
                "GENERATOR_TIME_FX",
                f"{where}.freeze",
                "a generator clip keeps the template's clock; freeze is refused",
            )
        if abs(clip.src_in) > 1e-9:
            add(
                "GENERATOR_SPAN",
                f"{where}.src_in",
                f"a generator starts at its template's first frame, so src_in must "
                f"be 0, got {clip.src_in}",
            )

        template = raw.get("template")
        if not isinstance(template, str) or not template.strip():
            add(
                "GENERATOR_TEMPLATE",
                f"{field_where}.template",
                f"template must be a non-empty name or directory, got {template!r}",
            )
            return
        engine = raw.get("engine")
        if engine not in GENERATOR_ENGINES:
            add(
                "GENERATOR_SHAPE",
                f"{field_where}.engine",
                f"engine {engine!r} not in {sorted(GENERATOR_ENGINES)}",
            )
        alpha = raw.get("alpha")
        if not isinstance(alpha, bool):
            add(
                "GENERATOR_SHAPE",
                f"{field_where}.alpha",
                f"alpha must be a boolean, got {alpha!r}",
            )
        if alpha is True and clip.layer < 1:
            add(
                "GENERATOR_ALPHA_LAYER",
                f"{where}.layer",
                "an alpha generator is composited over the layer below, so it "
                f"needs layer >= 1; this clip sits on layer {clip.layer}",
            )
        params = raw.get("params")
        if params is not None and not isinstance(params, Mapping):
            add(
                "GENERATOR_PARAMS",
                f"{field_where}.params",
                f"params must be an object, got {type(params).__name__}",
            )

        from . import motion  # noqa: PLC0415 - validation stays cheap without it

        try:
            meta = motion.load_template(template)
        except Exception as exc:  # noqa: BLE001 - every failure is a named finding
            add(
                "GENERATOR_TEMPLATE",
                f"{field_where}.template",
                f"template {template!r} cannot be loaded: {exc}",
            )
            return
        try:
            motion.validate_params(
                meta, params if isinstance(params, Mapping) else None
            )
        except Exception as exc:  # noqa: BLE001 - every failure is a named finding
            add(
                "GENERATOR_PARAMS",
                f"{field_where}.params",
                f"params do not resolve for {meta['name']!r}: {exc}",
            )
        span = clip.timeline_duration
        expected = float(meta["duration"])
        tolerance = 0.5 / self.fps + 1e-9 if self.fps > 0 else 1e-9
        if abs(span - expected) > tolerance:
            add(
                "GENERATOR_SPAN",
                where,
                f"timeline span {span:.6f}s differs from the template duration "
                f"{expected:.6f}s by more than half a frame "
                f"({0.5 / self.fps if self.fps > 0 else 0.0:.6f}s)",
            )

    def _validate_effect_density(self, add) -> None:
        for detail in check_effect_density(self):
            add("EFFECT_DENSITY", "clips", detail)

    def _validate_placement(self, add) -> None:
        """Layer 0 must tile the timeline: no gap, no overlap.

        Higher layers are windows placed *over* that base, so they are not part
        of the tiling; the compiler refuses their internal overlaps instead.
        """
        cursor = 0.0
        for index, clip in enumerate(self.clips):
            if clip.layer != 0:
                continue
            assert clip.dst_in is not None and clip.dst_out is not None
            if abs(clip.dst_in - cursor) > 1e-6:
                add(
                    "CLIP_RANGE",
                    f"clips[{index}].dst_in",
                    f"dst_in {clip.dst_in:.6f} leaves a gap or overlap at "
                    f"{cursor:.6f}; the slice concatenates clips",
                )
            cursor = max(cursor, clip.dst_out)

    def _validate_sequences(
        self, add, depth: int, problems: List[Dict[str, str]]
    ) -> None:
        for key, sequence in self.sequences.items():
            where = f"sequences.{key}"
            if depth + 1 > MAX_NESTING_DEPTH:
                add(
                    "NESTING_TOO_DEEP",
                    where,
                    f"nesting depth {depth + 1} exceeds the declared maximum "
                    f"{MAX_NESTING_DEPTH}",
                )
                continue
            already_prefixed = sequence.timeline.validate(
                depth=depth + 1, prefix=f"sequences.{key}.timeline."
            )
            problems.extend(already_prefixed)

    def _validate_adjustments(self, add) -> None:
        duration = self.duration
        for index, layer in enumerate(self.adjustments):
            where = f"adjustments[{index}]"
            if layer.track < 1:
                add(
                    "ADJUSTMENT_TRACK",
                    f"{where}.track",
                    f"an adjustment layer sits above the content, track >= 1, "
                    f"got {layer.track}",
                )
            if not layer.effects:
                add("ADJUSTMENT_TRACK", f"{where}.effects", "no effects declared")
            for effect_index, effect in enumerate(layer.effects):
                _validate_effect(
                    effect, f"{where}.effects[{effect_index}]", duration, add
                )

    def _validate_text(self, add) -> None:
        for index, layer in enumerate(self.text_layers):
            where = f"text_layers[{index}]"
            self._validate_text_style(layer.style, f"{where}.style", add)
            if not layer.text.strip():
                add("TEXT_STYLE", f"{where}.text", "text is empty")
            if layer.time_in < 0.0 or not math.isfinite(layer.time_in):
                add(
                    "TEXT_RANGE",
                    f"{where}.in",
                    f"in must be >= 0, got {layer.time_in}",
                )
            if layer.time_out <= layer.time_in:
                add(
                    "TEXT_RANGE",
                    f"{where}.out",
                    f"out ({layer.time_out}) must be after in ({layer.time_in})",
                )
            for name, track in layer.keyframes.items():
                track_where = f"{where}.keyframes.{name}"
                if name not in TEXT_KEYFRAME_PARAMS:
                    add(
                        "KEYFRAME_PARAM",
                        track_where,
                        f"unknown text keyframe param {name!r} "
                        f"(declared: {sorted(TEXT_KEYFRAME_PARAMS)})",
                    )
                    continue
                order = track.order_problem()
                if order is not None:
                    add("KEYFRAME_ORDER", track_where, order)
                    continue
                for position, key in enumerate(track.keys):
                    key_where = f"{track_where}[{position}]"
                    if key.t < 0.0 or key.t > layer.duration + 1e-9:
                        add(
                            "KEYFRAME_RANGE",
                            key_where,
                            f"t={key.t} outside the layer [0, {layer.duration}]",
                        )
                    if not (0.0 <= key.value <= 1.0):
                        add(
                            "KEYFRAME_RANGE",
                            key_where,
                            f"{name}={key.value} outside [0, 1]",
                        )

    @staticmethod
    def _validate_text_style(style: TextStyle, where: str, add) -> None:
        if not style.family.strip():
            add("TEXT_STYLE", f"{where}.family", "font family is empty")
        if not (0.0 < style.size <= MAX_TEXT_SIZE):
            add(
                "TEXT_STYLE",
                f"{where}.size",
                f"size must be in (0, {MAX_TEXT_SIZE}], got {style.size}",
            )
        if not _TEXT_COLOR.match(style.color):
            add(
                "TEXT_STYLE",
                f"{where}.color",
                f"colour must be #rrggbb, got {style.color!r}",
            )
        if not (0.0 <= style.stroke <= MAX_TEXT_STROKE):
            add(
                "TEXT_STYLE",
                f"{where}.stroke",
                f"stroke must be in [0, {MAX_TEXT_STROKE}], got {style.stroke}",
            )
        for axis, value in zip(("x", "y"), style.position):
            if not (0.0 <= value <= 1.0):
                add(
                    "TEXT_STYLE",
                    f"{where}.position.{axis}",
                    f"position must be normalised [0, 1], got {value}",
                )
        if style.align not in _TEXT_ALIGNS:
            add(
                "TEXT_STYLE",
                f"{where}.align",
                f"align must be one of {sorted(_TEXT_ALIGNS)}, got {style.align!r}",
            )

    def _validate_captions(self, add) -> None:
        for index, track in enumerate(self.captions):
            where = f"captions[{index}]"
            for cue_index, cue in enumerate(track.clips):
                cue_where = f"{where}.clips[{cue_index}]"
                if not cue.text.strip():
                    add("CAPTION_RANGE", f"{cue_where}.text", "caption text is empty")
                if cue.time_in < 0.0 or not math.isfinite(cue.time_in):
                    add(
                        "CAPTION_RANGE",
                        f"{cue_where}.in",
                        f"in must be >= 0, got {cue.time_in}",
                    )
                if cue.time_out <= cue.time_in:
                    add(
                        "CAPTION_RANGE",
                        f"{cue_where}.out",
                        f"out ({cue.time_out}) must be after in ({cue.time_in})",
                    )
            ordered = sorted(track.clips, key=lambda cue: cue.time_in)
            for previous, current in zip(ordered, ordered[1:]):
                if current.time_in < previous.time_out - 1e-9:
                    add(
                        "CAPTION_OVERLAP",
                        where,
                        f"cue at {current.time_in:.3f}s starts before the previous "
                        f"one ends at {previous.time_out:.3f}s",
                    )

    def _validate_audio(self, add) -> None:
        for index, track in enumerate(self.audio):
            where = f"audio[{index}]"
            if not track.role.strip():
                add("AUDIO_RANGE", f"{where}.role", "audio track role is empty")
            if not track.clips:
                add("AUDIO_RANGE", f"{where}.clips", "audio track has no clips")
            for clip_index, clip in enumerate(track.clips):
                clip_where = f"{where}.clips[{clip_index}]"
                if not clip.source:
                    add("AUDIO_RANGE", f"{clip_where}.source", "source is empty")
                if clip.src_out <= clip.src_in:
                    add(
                        "AUDIO_RANGE",
                        f"{clip_where}.src_out",
                        f"src_out ({clip.src_out}) must be greater than src_in "
                        f"({clip.src_in})",
                    )
                if clip.dst_out <= clip.dst_in:
                    add(
                        "AUDIO_RANGE",
                        f"{clip_where}.dst_out",
                        f"dst_out ({clip.dst_out}) must be greater than dst_in "
                        f"({clip.dst_in})",
                    )
                if not (-60.0 <= clip.gain_db <= 24.0):
                    add(
                        "AUDIO_RANGE",
                        f"{clip_where}.gain_db",
                        f"gain must be in [-60, 24] dB, got {clip.gain_db}",
                    )

        spec = self.ducking
        if spec is None:
            return
        where = "ducking"
        if not spec.sidechain_role.strip() or not spec.target_role.strip():
            add("DUCKING_RANGE", where, "roles must be non-empty")
        elif spec.sidechain_role == spec.target_role:
            add("DUCKING_RANGE", where, "sidechain and target roles must differ")
        for name, low, high in (
            ("threshold", 0.000976563, 1.0),
            ("ratio", 1.0, 20.0),
            ("attack_ms", 0.01, 2000.0),
            ("release_ms", 0.01, 9000.0),
            ("makeup_db", 0.0, 36.0),
        ):
            value = getattr(spec, name)
            if not (low <= value <= high):
                add(
                    "DUCKING_RANGE",
                    f"{where}.{name}",
                    f"value {value} outside [{low}, {high}]",
                )

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "version": self.version,
            "fps": self.fps,
            "size": [self.width, self.height],
            "clips": [clip.to_dict() for clip in self.clips],
            "sequences": {
                key: sequence.to_dict() for key, sequence in self.sequences.items()
            },
            "adjustments": [layer.to_dict() for layer in self.adjustments],
            "text_layers": [layer.to_dict() for layer in self.text_layers],
            "captions": [track.to_dict() for track in self.captions],
            "audio": [track.to_dict() for track in self.audio],
            "ducking": self.ducking.to_dict() if self.ducking is not None else None,
        }
        if self.brand_ref:
            data["brand_ref"] = self.brand_ref
        if self.profile_ref:
            data["profile_ref"] = self.profile_ref
        if self.variant:
            data["variant"] = dict(self.variant)
        data.update(self.ext)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Timeline3":
        if not isinstance(data, Mapping):
            raise EDLError("TIMELINE_SHAPE", f"timeline is not an object: {data!r}")
        raw_size = data.get("size") or (0, 0)
        if isinstance(raw_size, Mapping):
            width = int(_float(raw_size.get("width", 0)))
            height = int(_float(raw_size.get("height", 0)))
        else:
            values = list(raw_size) if isinstance(raw_size, Sequence) else [0, 0]
            width, height = (values + [0, 0])[:2]
        ext = dict(data.get("ext") or {})
        for key, value in data.items():
            if isinstance(key, str) and key.startswith("x-"):
                ext[key] = value
        return cls(
            fps=data.get("fps", 0),
            size=(int(_float(width)), int(_float(height))),
            clips=[Clip3.from_dict(raw) for raw in (data.get("clips") or [])],
            version=str(data.get("version", EDL3_VERSION)),
            sequences=dict(data.get("sequences") or {}),
            adjustments=[
                AdjustmentLayer.from_dict(raw)
                for raw in (data.get("adjustments") or [])
            ],
            text_layers=[
                TextLayer.from_dict(raw) for raw in (data.get("text_layers") or [])
            ],
            captions=[
                CaptionTrack.from_dict(raw) for raw in (data.get("captions") or [])
            ],
            audio=[AudioTrack3.from_dict(raw) for raw in (data.get("audio") or [])],
            ducking=(
                None
                if data.get("ducking") is None
                else Ducking3.from_dict(data["ducking"])
            ),
            brand_ref=data.get("brand_ref"),
            profile_ref=data.get("profile_ref"),
            variant=data.get("variant"),
            ext=ext,
        )
