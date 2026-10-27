"""vx.edl3 — minimal, forward-compatible edit document (schema ``vx.edl3/1``).

This is the vertical slice of the next-generation document model: one effect
(``transform``) with keyframes, one transition (``fade_in``), and nothing else.
It exists to prove the model end to end before the full phase integrates it.

Model
-----
``Keyframe(t, value, interp)`` is one control point. ``interp`` describes the
curve from *this* keyframe to the next one:

* ``linear`` — straight line.
* ``hold``   — the value stays here until the next keyframe (step function).
* ``bezier`` — cubic ease-in-out, implemented as the smoothstep Hermite
  ``3x^2 - 2x^3`` with zero tangents at both ends. It is documented here so the
  renderer and the tests agree on what "bezier" means.

A property is animated by a :class:`KeyframeTrack`. Tracks are the sampling
surface: ``track.sample(t)`` returns the interpolated value and enforces a
strictly increasing time axis (``KEYFRAME_ORDER``) rather than sorting a broken
track behind the caller's back. Before the first keyframe and after the last one
the track holds the boundary value.

``Effect(kind, params, keyframes)`` — for this slice ``kind`` is only
``"transform"`` with the params ``scale`` and ``opacity``; either can be
keyframed by putting a :class:`KeyframeTrack` under ``keyframes["scale"]`` or
``keyframes["opacity"]``. Params are the fallback when there is no track.

``Transition(kind, duration)`` — ``crossfade`` or ``fade_in`` are the declared
kinds; only ``fade_in`` is implemented by the slice renderer, which refuses the
other with ``UNSUPPORTED_TRANSITION`` instead of skipping it.

``Clip3`` / ``Timeline3`` hold the graph. ``validate()`` returns a list of
``{"code", "where", "detail"}`` dicts with stable string codes, never raising:
the compiler decides which codes are fatal and which it can translate into a
more specific refusal.

``to_dict`` / ``from_dict`` round trip, including every keyframe.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

EDL3_VERSION = "vx.edl3/1"

INTERPOLATIONS = frozenset({"linear", "hold", "bezier"})
EFFECT_KINDS = frozenset({"transform"})
TRANSITION_KINDS = frozenset({"crossfade", "fade_in"})
TRANSFORM_PARAMS = frozenset({"scale", "opacity"})

#: Effect kinds the slice renderer implements. Anything else is refused.
RENDERABLE_EFFECTS = frozenset({"transform"})
#: Transition kinds the slice renderer implements. Anything else is refused.
RENDERABLE_TRANSITIONS = frozenset({"fade_in"})

__all__ = [
    "EDL3_VERSION",
    "INTERPOLATIONS",
    "EFFECT_KINDS",
    "TRANSITION_KINDS",
    "TRANSFORM_PARAMS",
    "RENDERABLE_EFFECTS",
    "RENDERABLE_TRANSITIONS",
    "EDLError",
    "Keyframe",
    "KeyframeTrack",
    "Effect",
    "Transition",
    "Clip3",
    "Timeline3",
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
            t=float(data.get("t", 0.0)),
            value=float(data.get("value", 0.0)),
            interp=str(data.get("interp", "linear")),
        )


def _ease_in_out(x: float) -> float:
    """Cubic ease-in-out: smoothstep with zero tangents at both ends."""
    return x * x * (3.0 - 2.0 * x)


@dataclass
class KeyframeTrack:
    """An ordered list of keyframes for one property.

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


# ----------------------------------------------------------------- effects


@dataclass
class Effect:
    """One effect instance. ``keyframes`` maps a param name to its track."""

    kind: str
    params: Dict[str, float] = field(default_factory=dict)
    keyframes: Dict[str, KeyframeTrack] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.kind = str(self.kind)
        coerced_params: Dict[str, float] = {}
        for name, value in (self.params or {}).items():
            coerced_params[str(name)] = float(value)
        self.params = coerced_params
        coerced_tracks: Dict[str, KeyframeTrack] = {}
        for name, raw in (self.keyframes or {}).items():
            coerced_tracks[str(name)] = KeyframeTrack.from_list(raw)
        self.keyframes = coerced_tracks

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
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Effect":
        if not isinstance(data, Mapping):
            raise EDLError("EFFECT_SHAPE", f"effect is not an object: {data!r}")
        raw_kf = data.get("keyframes") or {}
        tracks: Dict[str, KeyframeTrack] = {}
        if isinstance(raw_kf, Mapping):
            for name, raw in raw_kf.items():
                tracks[str(name)] = KeyframeTrack.from_list(raw)
        return cls(
            kind=str(data.get("kind", "")),
            params=dict(data.get("params") or {}),
            keyframes=tracks,
        )


# ------------------------------------------------------------- transitions


@dataclass
class Transition:
    """The transition that precedes a clip."""

    kind: str = "fade_in"
    duration: float = 0.0

    def __post_init__(self) -> None:
        self.kind = str(self.kind)
        self.duration = float(self.duration)

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "duration": self.duration}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Transition":
        if not isinstance(data, Mapping):
            raise EDLError("TRANSITION_SHAPE", f"transition is not an object: {data!r}")
        return cls(
            kind=str(data.get("kind", "fade_in")),
            duration=float(data.get("duration", 0.0)),
        )


# ------------------------------------------------------------------- clips


@dataclass
class Clip3:
    """One source segment on the timeline."""

    id: str
    source: str
    src_in: float
    src_out: float
    effects: List[Effect] = field(default_factory=list)
    transition_in: Optional[Transition] = None

    def __post_init__(self) -> None:
        self.id = str(self.id)
        self.source = str(self.source)
        self.src_in = float(self.src_in)
        self.src_out = float(self.src_out)
        self.effects = [
            effect if isinstance(effect, Effect) else Effect.from_dict(effect)
            for effect in (self.effects or [])
        ]
        if isinstance(self.transition_in, Mapping):
            self.transition_in = Transition.from_dict(self.transition_in)

    @property
    def duration(self) -> float:
        return self.src_out - self.src_in

    def transform(self) -> Optional[Effect]:
        for effect in self.effects:
            if effect.kind == "transform":
                return effect
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "src_in": self.src_in,
            "src_out": self.src_out,
            "effects": [effect.to_dict() for effect in self.effects],
            "transition_in": (
                self.transition_in.to_dict() if self.transition_in is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Clip3":
        if not isinstance(data, Mapping):
            raise EDLError("CLIP_SHAPE", f"clip is not an object: {data!r}")
        raw_transition = data.get("transition_in")
        return cls(
            id=str(data.get("id", "")),
            source=str(data.get("source", "")),
            src_in=float(data.get("src_in", 0.0)),
            src_out=float(data.get("src_out", 0.0)),
            effects=[Effect.from_dict(raw) for raw in (data.get("effects") or [])],
            transition_in=(
                Transition.from_dict(raw_transition)
                if isinstance(raw_transition, Mapping)
                else None
            ),
        )


# ---------------------------------------------------------------- timeline


@dataclass
class Timeline3:
    """The versioned document."""

    fps: int
    size: Tuple[int, int]
    clips: List[Clip3] = field(default_factory=list)
    version: str = EDL3_VERSION

    def __post_init__(self) -> None:
        self.version = str(self.version)
        self.fps = int(self.fps)
        width, height = self.size
        self.size = (int(width), int(height))
        self.clips = [
            clip if isinstance(clip, Clip3) else Clip3.from_dict(clip)
            for clip in (self.clips or [])
        ]

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1]

    @property
    def duration(self) -> float:
        return sum(max(0.0, clip.duration) for clip in self.clips)

    # -- validation -------------------------------------------------------
    def validate(self) -> List[Dict[str, str]]:
        problems: List[Dict[str, str]] = []

        def add(code: str, where: str, detail: str) -> None:
            problems.append({"code": code, "where": where, "detail": detail})

        if self.version != EDL3_VERSION:
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
            if not clip.source:
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
                    f"src_out ({clip.src_out}) must be greater than src_in ({clip.src_in})",
                )

            duration = clip.duration
            for effect_index, effect in enumerate(clip.effects):
                effect_where = f"{clip_where}.effects[{effect_index}]"
                if effect.kind not in EFFECT_KINDS:
                    add(
                        "EFFECT_UNKNOWN",
                        f"{effect_where}.kind",
                        f"unknown effect kind {effect.kind!r}",
                    )
                for name in effect.params:
                    if effect.kind == "transform" and name not in TRANSFORM_PARAMS:
                        add(
                            "EFFECT_PARAM",
                            f"{effect_where}.params.{name}",
                            f"unknown transform param {name!r}",
                        )
                for name, track in effect.keyframes.items():
                    track_where = f"{effect_where}.keyframes.{name}"
                    if effect.kind == "transform" and name not in TRANSFORM_PARAMS:
                        add(
                            "KEYFRAME_PARAM",
                            track_where,
                            f"unknown keyframable transform param {name!r}",
                        )
                        continue
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
                        if key.t < 0.0 or key.t > duration:
                            add(
                                "KEYFRAME_RANGE",
                                key_where,
                                f"t={key.t} outside clip [0, {duration}]",
                            )
                        if name == "opacity" and not (0.0 <= key.value <= 1.0):
                            add(
                                "KEYFRAME_RANGE",
                                key_where,
                                f"opacity {key.value} outside [0, 1]",
                            )
                        if name == "scale" and not (key.value > 0.0):
                            add(
                                "KEYFRAME_RANGE",
                                key_where,
                                f"scale {key.value} must be positive",
                            )

            transition = clip.transition_in
            if transition is not None:
                transition_where = f"{clip_where}.transition_in"
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
                elif transition.duration > duration:
                    add(
                        "TRANSITION_TOO_LONG",
                        f"{transition_where}.duration",
                        f"duration {transition.duration} exceeds clip duration {duration}",
                    )

        return problems

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "fps": self.fps,
            "size": [self.width, self.height],
            "clips": [clip.to_dict() for clip in self.clips],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Timeline3":
        if not isinstance(data, Mapping):
            raise EDLError("TIMELINE_SHAPE", f"timeline is not an object: {data!r}")
        raw_size = data.get("size") or (0, 0)
        if isinstance(raw_size, Mapping):
            width = int(raw_size.get("width", 0))
            height = int(raw_size.get("height", 0))
        else:
            width, height = raw_size
        return cls(
            fps=int(data.get("fps", 0)),
            size=(int(width), int(height)),
            clips=[Clip3.from_dict(raw) for raw in (data.get("clips") or [])],
            version=str(data.get("version", EDL3_VERSION)),
        )
