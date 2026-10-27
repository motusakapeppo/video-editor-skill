"""vx.edl3_render — compile an ``vx.edl3/1`` document to ffmpeg and render it.

Backend rule
------------
``ffmpeg`` is the default engine for every cut, B-roll, colour, audio and
standard titling. ``chromium`` is admitted only for what genuinely needs an
engine that reflows, and that is decided by a *declared property*, never by the
effect's name: see :data:`CHROMIUM_QUALIFYING`. A node that declares
``chromium`` without one of those properties is redirected to its registry
fallback; without a fallback it is refused. Availability is a declared setting
(:data:`DEFAULT_SETTINGS`, ``chromium_available=False`` until F3 builds the
engine), so a qualified node reports ``backend_unavailable`` instead of
pretending.

Implemented end to end
----------------------
* ``transform`` — static ``scale``; ``opacity`` animated with real alpha
  (``geq`` for up to three keyframes, ``sendcmd`` beyond that, D012 #1: the
  commands precede the filter they target);
* ``crop`` — normalised rect, time-variable ``w``/``h``/``x``/``y``;
* ``color`` — ``eq``; animated values flattened to one ``sendcmd`` command per
  frame because ``eq`` has no time variable in its options;
* ``blur`` — static ``gblur``;
* ``fade_in`` / ``fade_out`` transitions;
* adjustment layers, applied before each clip's own effects;
* multi-layer video: layer 0 concatenates, every higher layer is one
  ``overlay`` gated by ``enable='between(t,start,end)'``;
* the audio bus: dialogue concatenated, music placed with ``adelay``, ducked by
  ``sidechaincompress`` keyed on the dialogue, summed with ``amix``.

Refused loudly, never skipped
-----------------------------
``chroma_key`` and any kind outside :data:`edl3.RENDERABLE_EFFECTS` →
``UNSUPPORTED_EFFECT``. A chromium node that qualifies while the engine is
absent → ``BACKEND_UNAVAILABLE``; a chromium node with no fallback →
``BACKEND_REFUSED``; an unknown property or backend → ``BACKEND_PROPERTY`` /
``BACKEND_UNKNOWN``. Two overlays on the same layer → ``LAYER_OVERLAP``. Text
layers and caption tracks → ``UNSUPPORTED_EFFECT`` until F2-T07. Nested
sequences → ``UNSUPPORTED_SEQUENCE``. ``crossfade`` → ``UNSUPPORTED_TRANSITION``.
Audio is ``UNSUPPORTED_AUDIO`` under the default ``audio="refuse"`` policy;
``audio="mix"`` builds the bus and ``audio="drop"`` states the picture-only
policy.

Loudness: EDL3 declares no loudness target and carries no findings, so the bus
is not normalised. That is the same rule the EDL renderer applies at stage 04
(no finding -> remux, do not invent a normalisation); the mixed bus is trimmed
to the picture duration and encoded with the house AAC settings.

``compile_timeline`` is pure: it never runs ffmpeg. ``render`` writes the
script, runs ffmpeg with ``-filter_complex_script`` and returns the report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import ffmpeg
from .edl3 import (
    BACKENDS,
    EDL3_VERSION,
    EFFECT_REGISTRY,
    Keyframe,
    KeyframeTrack,
    MAX_NESTING_DEPTH,
    RENDERABLE_EFFECTS,
    RENDERABLE_TRANSITIONS,
    Transition,
    Timeline3,
)
from .edl3_compose import (
    BUILDERS as COMPOSE_BUILDERS,
    blend_fragment,
    relabel,
    validate_op,
)
from .edl3_extremes import freeze_plan, reverse_plan
from .edl3_nested import resolve as resolve_nesting
from .edl3_time import AudioPolicy, RemapCurve, audio_fragment, ramp_fragment

BACKEND_EXPR = "expr"
BACKEND_SENDCMD = "sendcmd"
BACKEND_CHROMIUM = "chromium"
#: More keyframes on a mechanism-flexible track than this switches from a baked
#: expression to sendcmd. One constant: select_backend and choose_backend read it.
SENDCMD_THRESHOLD = 3
#: (kind, param) tracks that can compile either way; only these flip.
SENDCMD_TRACKS = frozenset({("transform", "opacity")})
#: Properties that justify a real layout engine. Declared, never inferred from a
#: kind name: a node cannot smuggle ``chromium`` past this set.
CHROMIUM_QUALIFYING = frozenset(
    {"layout", "backdrop_filter", "blend_animated", "reflow"}
)
#: Declared compiler settings. F3 flips ``chromium_available`` through settings
#: without editing a single routing branch.
DEFAULT_SETTINGS: Dict[str, Any] = {"chromium_available": False}
#: Mechanisms this compiler actually emits per kind. A node that declares
#: anything else is refused: a report may never name a mechanism the graph does
#: not use.
IMPLEMENTED_BACKENDS: Dict[str, frozenset] = {
    "transform": frozenset({BACKEND_EXPR, BACKEND_SENDCMD}),
    "crop": frozenset({BACKEND_EXPR}),
    "color": frozenset({BACKEND_SENDCMD}),
    "blur": frozenset({BACKEND_EXPR}),
}
RULE_DEFAULT = "default"
RULE_KEYFRAMES = "keyframe_threshold"
RULE_QUALIFIED = "chromium_qualified"
RULE_REDIRECTED = "chromium_redirected"
RULE_UNAVAILABLE = "chromium_unavailable"
RULE_REFUSED = "no_fallback_refused"
RULES = frozenset(
    {
        RULE_DEFAULT,
        RULE_KEYFRAMES,
        RULE_QUALIFIED,
        RULE_REDIRECTED,
        RULE_UNAVAILABLE,
        RULE_REFUSED,
    }
)
AUDIO_POLICIES = frozenset({"refuse", "drop", "mix"})
#: Audio time policy used for a ramp the document does not declare one for.
DEFAULT_RAMP_AUDIO_POLICY = AudioPolicy.SEGMENTS
#: Compositing ops that build alpha inside the clip, before the layer stage.
KEYING_OPS = frozenset({"pip", "mask", "chroma"})
#: Canonical per-clip transform order. Emitted in this order, reported as is.
CLIP_ORDER = (
    "source",
    "time_fx",
    "speed",
    "geometry",
    "keying",
    "compose",
)
AUDIO_BITRATE = "192k"
AUDIO_RATE = 48000
#: Dialogue joint fade, mirroring the house 12 ms cut fade.
DIALOGUE_FADE_SEC = 0.012

#: Structural validation codes the compiler refuses outright.
HARD_CODES = frozenset(
    {
        "SCHEMA_VERSION",
        "FPS_INVALID",
        "SIZE_INVALID",
        "TIMELINE_EMPTY",
        "DUPLICATE_CLIP_ID",
        "CLIP_RANGE",
        "CLIP_SHAPE",
        "LAYER_RANGE",
        "EFFECT_UNKNOWN",
        "EFFECT_PARAM",
        "EFFECT_RANGE",
        "EFFECT_NOT_ANIMATABLE",
        "KEYFRAME_ORDER",
        "KEYFRAME_RANGE",
        "KEYFRAME_INTERP",
        "KEYFRAME_PARAM",
        "KEYFRAME_EMPTY",
        "TRANSITION_UNKNOWN",
        "TRANSITION_RANGE",
        "TRANSITION_TOO_LONG",
        "NESTING_TOO_DEEP",
        "NESTING_UNKNOWN",
        "SEQUENCE_SHAPE",
        "ADJUSTMENT_TRACK",
        "TEXT_STYLE",
        "TEXT_RANGE",
        "CAPTION_RANGE",
        "CAPTION_OVERLAP",
        "AUDIO_RANGE",
        "DUCKING_RANGE",
        "SPEED_RANGE",
        "FREEZE_SHAPE",
        "COMPOSE_SHAPE",
        "AUDIO_POLICY",
        "GENERATOR_SHAPE",
        "GENERATOR_TEMPLATE",
        "GENERATOR_PARAMS",
        "GENERATOR_SPAN",
        "GENERATOR_TIME_FX",
        "GENERATOR_ALPHA_LAYER",
        "GENERATOR_SOURCE_CONFLICT",
    }
)

#: Codes this module raises itself. Every refusal names one of them.
REFUSAL_CODES = frozenset(
    {
        "AUDIO_POLICY",
        "SETTINGS_UNKNOWN",
        "SETTINGS_TYPE",
        "BACKEND_UNKNOWN",
        "BACKEND_PROPERTY",
        "BACKEND_UNIMPLEMENTED",
        "BACKEND_UNAVAILABLE",
        "BACKEND_REFUSED",
        "LAYER_OVERLAP",
        "LAYER_RANGE",
        "UNSUPPORTED_EFFECT",
        "UNSUPPORTED_SEQUENCE",
        "UNSUPPORTED_TRANSITION",
        "UNSUPPORTED_AUDIO",
        "MISSING_SOURCE",
        "RENDER_FAILED",
        "SPEED_RANGE",
        "FREEZE_SHAPE",
        "COMPOSE_SHAPE",
        "COMPOSE_FRAGMENT",
        "CHROMIUM_NEEDS_GENERATOR",
        "GENERATOR_EFFECT",
        "GENERATOR_CAPTURE",
    }
)

#: Codes the planning modules raise themselves. They are propagated untouched,
#: so a caller sees the module's own vocabulary instead of a generic wrapper.
MODULE_CODES = frozenset(
    {
        "REVERSE_TOO_LONG",
        "FREEZE_TOO_LONG",
        "EXTREMES_DURATION_INVALID",
        "EXTREMES_TIME_INVALID",
        "EXTREMES_HOLD_INVALID",
        "EXTREMES_FPS_INVALID",
        "ATEMPO_RATIO_INVALID",
        "ATEMPO_BOUNDS",
        "ATEMPO_CASCADE_UNREACHABLE",
        "AUDIO_SAMPLE_RATE_INVALID",
        "AUDIO_POLICY_UNKNOWN",
        "REMAP_SHAPE",
        "REMAP_EMPTY",
        "REMAP_NONFINITE",
        "REMAP_NEGATIVE",
        "REMAP_ORDER",
        "REMAP_DUPLICATE",
        "REMAP_TOO_SHORT",
        "RAMP_FPS_INVALID",
        "NESTING_CYCLE",
        "NESTING_TOO_DEEP",
        "NESTING_UNKNOWN",
        "NESTING_SHAPE",
        "NESTING_DEPTH_INVALID",
        "COMPOSE_OP",
        "COMPOSE_PARAMS",
        "PIP_GEOMETRY",
        "PIP_BORDER",
        "MASK_SHAPE",
        "MASK_GEOMETRY",
        "MASK_FEATHER",
        "BLEND_MODE",
        "CHROMA_COLOR",
        "CHROMA_RANGE",
    }
)

#: Effect kinds the compiler can emit today, from the registry's own view.
SUPPORTED_EFFECTS = tuple(sorted(RENDERABLE_EFFECTS))

__all__ = [
    "BACKEND_EXPR",
    "BACKEND_SENDCMD",
    "BACKEND_CHROMIUM",
    "SENDCMD_THRESHOLD",
    "SENDCMD_TRACKS",
    "CHROMIUM_QUALIFYING",
    "DEFAULT_SETTINGS",
    "RULES",
    "AUDIO_POLICIES",
    "HARD_CODES",
    "REFUSAL_CODES",
    "MODULE_CODES",
    "KEYING_OPS",
    "CLIP_ORDER",
    "SUPPORTED_EFFECTS",
    "CompileError",
    "resolve_settings",
    "select_backend",
    "choose_backend",
    "track_expression",
    "sendcmd_commands",
    "compile_timeline",
    "render",
]


class CompileError(Exception):
    """The document cannot be compiled; ``code`` is stable and actionable."""

    def __init__(self, code: str, detail: str, where: str = "") -> None:
        self.code = code
        self.detail = detail
        self.where = where
        text = f"{code}: {detail}"
        if where:
            text = f"{text} ({where})"
        super().__init__(text)


# ------------------------------------------------------------- expression


def _num(value: float) -> str:
    """Stable decimal literal for ffmpeg's expression parser."""
    return f"{float(value):.6f}"


def _segment_expression(left: Keyframe, right: Keyframe, time_symbol: str) -> str:
    if left.interp == "hold":
        return _num(left.value)
    span = right.t - left.t
    x = f"(({time_symbol}-{_num(left.t)})/{_num(span)})"
    if left.interp == "bezier":
        shape = f"(3*{x}*{x}-2*{x}*{x}*{x})"
    else:
        shape = x
    return f"({_num(left.value)}+({_num(right.value - left.value)})*{shape})"


def track_expression(
    track: KeyframeTrack,
    low: float = 0.0,
    high: float = 1.0,
    time_symbol: str = "T",
) -> str:
    """Bake a keyframe track into one clamped ffmpeg expression of time.

    ``geq`` spells the timestamp ``T``; ``crop`` spells it ``t``. That is a real
    difference between the two filters' expression scopes, not a preference.
    """
    keys = track.ordered()
    if not keys:
        raise CompileError("KEYFRAME_EMPTY", "keyframe track has no keyframes")
    if len(keys) == 1:
        return f"max({_num(low)},min({_num(high)},{_num(keys[0].value)}))"
    expression = _num(keys[-1].value)
    for index in range(len(keys) - 2, -1, -1):
        segment = _segment_expression(keys[index], keys[index + 1], time_symbol)
        expression = (
            f"if(lt({time_symbol},{_num(keys[index + 1].t)}),{segment},{expression})"
        )
    first = keys[0]
    expression = (
        f"if(lt({time_symbol},{_num(first.t)}),{_num(first.value)},{expression})"
    )
    return f"max({_num(low)},min({_num(high)},{expression}))"


def _escape_commas(expression: str) -> str:
    return expression.replace(",", "\\,")


def sendcmd_commands(
    track: KeyframeTrack,
    fps: int,
    duration: float,
    target: str,
    param: str = "aa",
) -> str:
    """One command per frame so the rendered curve follows ``track.sample``."""
    keys = track.ordered()
    if not keys:
        raise CompileError("KEYFRAME_EMPTY", "keyframe track has no keyframes")
    frames = max(1, int(round(duration * fps)))
    commands: List[str] = []
    for index in range(frames + 1):
        t = index / float(fps)
        if t > duration + 1e-9:
            break
        value = track.sample(min(t, duration))
        commands.append(f"{_num(t)} {target} {param} {_num(value)}")
    return ";".join(commands)


# ---------------------------------------------------------------- routing


def _coerce_flag(name: str, raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    raise CompileError(
        "SETTINGS_TYPE",
        f"{name} must be a boolean, got {raw!r}",
        f"settings.{name}",
    )


def resolve_settings(
    settings: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge the caller's settings over the declared defaults."""
    resolved: Dict[str, Any] = dict(DEFAULT_SETTINGS)
    for name, raw in (settings or {}).items():
        if name not in DEFAULT_SETTINGS:
            raise CompileError(
                "SETTINGS_UNKNOWN",
                f"unknown compiler setting {name!r} "
                f"(declared: {sorted(DEFAULT_SETTINGS)})",
                f"settings.{name}",
            )
        resolved[name] = _coerce_flag(name, raw)
    return resolved


def _flexible_keyframe_count(effect: Any) -> int:
    largest = 0
    for kind, param in SENDCMD_TRACKS:
        if kind != effect.kind:
            continue
        track = effect.keyframes.get(param)
        if track is not None:
            largest = max(largest, len(track.keys))
    return largest


def _declared_properties(effect: Any) -> Tuple[str, ...]:
    raw = getattr(effect, "properties", ()) or ()
    if isinstance(raw, str):
        return (raw,)
    return tuple(str(name) for name in raw)


def select_backend(
    effect: Any, *, settings: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    """Route one effect to a mechanism and state the rule that decided it."""
    resolved = resolve_settings(settings)
    available = bool(resolved["chromium_available"])
    spec = EFFECT_REGISTRY.get(effect.kind)
    declared = str(getattr(effect, "declared_backend", "") or "")
    if not declared and spec is not None:
        declared = spec.backend
    fallback = "" if spec is None else spec.fallback
    properties = _declared_properties(effect)

    if declared not in BACKENDS:
        raise CompileError(
            "BACKEND_UNKNOWN",
            f"{effect.kind!r} declares backend {declared!r}; declared mechanisms "
            f"are {sorted(BACKENDS)}",
            f"effects.{effect.kind}.backend",
        )

    unknown = [name for name in properties if name not in CHROMIUM_QUALIFYING]
    if unknown:
        raise CompileError(
            "BACKEND_PROPERTY",
            f"{effect.kind!r} declares unknown propert"
            f"{'y' if len(unknown) == 1 else 'ies'} {unknown}; the declared set is "
            f"{sorted(CHROMIUM_QUALIFYING)}",
            f"effects.{effect.kind}.properties",
        )
    if properties and declared != BACKEND_CHROMIUM:
        raise CompileError(
            "BACKEND_PROPERTY",
            f"{effect.kind!r} declares properties {list(properties)} on backend "
            f"{declared!r}; properties describe a chromium requirement",
            f"effects.{effect.kind}.properties",
        )

    entry: Dict[str, Any] = {
        "kind": effect.kind,
        "declared": declared,
        "chosen": declared,
        "rule": RULE_DEFAULT,
        "fallback": fallback,
        "available": available,
    }

    if declared == BACKEND_CHROMIUM:
        if properties:
            entry["chosen"] = BACKEND_CHROMIUM if available else ""
            entry["rule"] = RULE_QUALIFIED if available else RULE_UNAVAILABLE
            return entry
        if fallback:
            chosen = fallback
            if chosen == BACKEND_EXPR and (
                _flexible_keyframe_count(effect) > SENDCMD_THRESHOLD
            ):
                chosen = BACKEND_SENDCMD
            entry["chosen"] = chosen
            entry["rule"] = RULE_REDIRECTED
            return entry
        entry["chosen"] = ""
        entry["rule"] = RULE_REFUSED
        return entry

    if declared == BACKEND_EXPR and (
        _flexible_keyframe_count(effect) > SENDCMD_THRESHOLD
    ):
        entry["chosen"] = BACKEND_SENDCMD
        entry["rule"] = RULE_KEYFRAMES
    implemented = IMPLEMENTED_BACKENDS.get(effect.kind)
    if implemented is not None and declared not in implemented:
        raise CompileError(
            "BACKEND_UNIMPLEMENTED",
            f"{effect.kind!r} declares {declared!r} but this compiler emits "
            f"{sorted(implemented)} for that kind; declaring another engine "
            "cannot change what the graph does",
            f"effects.{effect.kind}.backend",
        )
    return entry


def choose_backend(timeline: Timeline3) -> str:
    """The document's opacity backend: the largest opacity track decides."""
    largest = 0
    for clip in timeline.clips:
        transform = clip.effect("transform")
        if transform is None:
            continue
        largest = max(largest, _flexible_keyframe_count(transform))
    return BACKEND_SENDCMD if largest > SENDCMD_THRESHOLD else BACKEND_EXPR


# ---------------------------------------------------------------- planning


def _even(value: float) -> int:
    rounded = int(round(value / 2.0)) * 2
    return max(2, rounded)


def _opacity_fragment(
    index: int,
    track: Optional[KeyframeTrack],
    static_value: Optional[float],
    backend: str,
    fps: int,
    duration: float,
) -> Tuple[Optional[str], str]:
    """Return ``(filter_fragment, mode)`` for this clip's opacity."""
    if track is not None:
        if len(track.keys) >= 2:
            if backend == BACKEND_SENDCMD:
                target = f"colorchannelmixer@opa{index}"
                commands = sendcmd_commands(track, fps, duration, target)
                start = track.sample(0.0)
                fragment = f"sendcmd=c='{commands}',{target}=aa={_num(start)}"
                return fragment, "keyframed"
            expression = track_expression(track)
            fragment = f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='255*{expression}'"
            return fragment, "keyframed"
        value = track.sample(0.0)
    else:
        value = 1.0 if static_value is None else static_value
    return f"colorchannelmixer=aa={_num(value)}", "static"


def _crop_fragment(effect: Any) -> str:
    """Normalised rect as a time-variable ``crop`` expression."""

    def axis(name: str, symbol: str, default: float) -> str:
        track = effect.keyframes.get(name)
        if track is not None:
            expression = track_expression(track, time_symbol="t")
            return f"{symbol}*({_escape_commas(expression)})"
        value = effect.params.get(name, default)
        return f"{symbol}*{_num(value)}"

    width = axis("width", "iw", 1.0)
    height = axis("height", "ih", 1.0)
    x = axis("x", "iw", 0.0)
    y = axis("y", "ih", 0.0)
    return f"crop=w={width}:h={height}:x={x}:y={y}"


def _color_fragment(
    index: int, ordinal: int, effect: Any, fps: int, duration: float
) -> str:
    """``eq`` with one sendcmd command per frame for every animated channel."""
    target = f"eq@grade{index}_{ordinal}"
    defaults = {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0}
    initial: Dict[str, float] = dict(defaults)
    for name in defaults:
        value = effect.sample(name, 0.0)
        if value is not None:
            initial[name] = float(value)
    commands: List[str] = []
    for name in defaults:
        track = effect.keyframes.get(name)
        if track is None:
            continue
        commands.append(sendcmd_commands(track, fps, duration, target, name))
    settings = ":".join(f"{name}={_num(value)}" for name, value in initial.items())
    fragment = f"{target}={settings}"
    if commands:
        joined = ";".join(command for command in commands if command)
        fragment = f"sendcmd=c='{joined}',{fragment}"
    return fragment


def _blur_fragment(effect: Any) -> str:
    sigma = effect.sample("sigma", 0.0)
    return f"gblur=sigma={_num(1.0 if sigma is None else sigma)}"


def _transition_fragment(
    transition: Optional[Transition], duration: float
) -> Optional[str]:
    if transition is None or transition.kind not in RENDERABLE_TRANSITIONS:
        return None
    if transition.kind == "fade_in":
        return f"fade=t=in:st=0:d={_num(transition.duration)}"
    start = max(0.0, duration - transition.duration)
    return f"fade=t=out:st={_num(start)}:d={_num(transition.duration)}"


def _effective_effects(
    timeline: Timeline3,
    clip: Any,
    where: str,
    routed: Mapping[str, Dict[str, Any]],
) -> Tuple[Dict[str, List[Any]], Dict[str, str]]:
    """Adjustment layers first (track ascending), then the clip's own effects.

    Colour and blur stack, because they are point-wise and order is meaningful.
    Geometry (``crop``) and the compositing transform may not stack: two crop
    rects or two scales have no declared meaning here, so the compiler refuses
    instead of inventing one. The returned map is the mechanism actually chosen
    for each kind, which is the fallback when chromium was redirected.
    """
    merged: List[Tuple[str, Any]] = []
    ordered = sorted(enumerate(timeline.adjustments), key=lambda item: item[1].track)
    for index, layer in ordered:
        for position, effect in enumerate(layer.effects):
            merged.append((f"adjustments[{index}].effects[{position}]", effect))
    for position, effect in enumerate(clip.effects):
        scope = f"{where}.effects[{position}]"
        if routed.get(scope, {}).get("realized_by") == "generator":
            continue
        merged.append((scope, effect))

    by_kind: Dict[str, List[Any]] = {}
    chosen: Dict[str, str] = {}
    for scope, effect in merged:
        by_kind.setdefault(effect.kind, []).append(effect)
        chosen[effect.kind] = routed[scope]["chosen"]
    for kind, instances in by_kind.items():
        if kind not in RENDERABLE_EFFECTS:
            raise CompileError(
                "UNSUPPORTED_EFFECT",
                f"effect kind {kind!r} is not implemented by edl3 "
                f"(implemented: {list(SUPPORTED_EFFECTS)}); "
                f"registry backend: {EFFECT_REGISTRY[kind].backend!r}",
                f"{where}.effects.{kind}",
            )
        if len(instances) > 1 and kind not in {"color", "blur"}:
            raise CompileError(
                "UNSUPPORTED_EFFECT",
                f"two {kind!r} effects on one clip have no declared order; "
                "geometry and transform stack is not implemented",
                where,
            )
    return by_kind, chosen


def _render_blocks(
    index: int,
    source: str,
    blocks: List[Dict[str, Any]],
    final_label: str,
) -> List[str]:
    """Emit a clip pipeline as ``-filter_complex_script`` lines.

    Two block kinds, in the order the document declares them:

    * ``filters`` — appends to the current chain;
    * ``fanout``  — ``split``, one branch per segment, ``concat`` back (reverse
      and freeze are linear; a freeze or a multi-segment ramp fans out).

    The emitter is what enforces the canonical order: it never sorts, so the
    chain order is exactly the block order the caller built.
    """
    lines: List[str] = []
    label = source
    pending: List[str] = []
    serial = 0

    for block in blocks:
        kind = block["kind"]
        if kind == "filters":
            pending.extend(block["filters"])
            continue

        if kind == "fanout":
            branches = list(block["branches"])
            if len(branches) == 1:
                pending.extend(branches[0])
                pending.extend(block.get("post") or [])
                continue
            outs = [f"fo{index}_{serial}_{slot}" for slot in range(len(branches))]
            split = block.get("split")
            if not split:
                raise CompileError(
                    "COMPOSE_FRAGMENT",
                    f"a {len(branches)}-branch fan-out needs a split filter",
                    f"clips[{index}]",
                )
            lines.append(
                label + ",".join(pending + [split]) + "".join(f"[{o}]" for o in outs)
            )
            mids: List[str] = []
            for slot, (out, branch) in enumerate(zip(outs, branches)):
                mid = f"fb{index}_{serial}_{slot}"
                lines.append(f"[{out}]" + ",".join(branch) + f"[{mid}]")
                mids.append(mid)
            joined = f"fj{index}_{serial}"
            lines.append(
                "".join(f"[{m}]" for m in mids)
                + (block.get("concat") or "null")
                + f"[{joined}]"
            )
            label = f"[{joined}]"
            pending = list(block.get("post") or [])
            serial += 1
            continue

        raise CompileError(
            "COMPOSE_FRAGMENT", f"unknown block kind {kind!r}", f"clips[{index}]"
        )

    lines.append(label + ",".join(pending or ["null"]) + f"[{final_label}]")
    return lines


# -------------------------------------------------------------- clip stages


def _time_fx_stage(
    clip: Any, index: int, fps: int
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """Reverse, then freeze: the two time extremes, at the head of the chain."""
    blocks: List[Dict[str, Any]] = []
    applied: List[str] = []
    details: Dict[str, Any] = {}
    if clip.reverse:
        plan = reverse_plan(clip.duration, has_audio=False)
        blocks.append({"kind": "filters", "filters": list(plan["video_filters"])})
        applied.append("reverse")
        details["reverse"] = {
            "duration": plan["duration"],
            "memory_note": plan["memory_note"],
        }
    if clip.freeze is not None:
        plan = freeze_plan(clip.freeze["t"], clip.freeze["hold"], fps=float(fps))
        blocks.append(
            {
                "kind": "fanout",
                "split": plan["split"],
                "branches": [[chain] for chain in plan["video_filters"]],
                "concat": plan["concat"],
                "post": list(plan["post"]),
            }
        )
        applied.append("freeze")
        details["freeze"] = {
            "frame_index": plan["frame_index"],
            "freeze_at": plan["freeze_at"],
            "hold": plan["hold_duration"],
            "audio_note": plan["audio_note"],
        }
    return blocks, applied, details


def _speed_curve(clip: Any) -> Optional[RemapCurve]:
    """The declared ramp as a ``RemapCurve``; a ratio becomes two keyframes."""
    if clip.speed is None:
        return None
    if isinstance(clip.speed, list):
        return RemapCurve(clip.speed)
    span = clip.duration
    ratio = float(clip.speed)
    return RemapCurve([(0.0, 0.0), (span / ratio, span)])


def _speed_stage(
    clip: Any, index: int, fps: int
) -> Tuple[Optional[Dict[str, Any]], Optional[float], Optional[Dict[str, Any]]]:
    """The ramp block, the effective ratio and the plan the report quotes."""
    curve = _speed_curve(clip)
    if curve is None:
        return None, None, None
    plan = ramp_fragment(curve, fps=float(fps))
    block = {
        "kind": "fanout",
        "split": plan["split"],
        "branches": [[chain] for chain in plan["video_filters"]],
        "concat": plan["concat"],
        "post": list(plan["post"]),
    }
    return block, float(plan["ratio"]), plan


def _compose_declaration(
    raw: Mapping[str, Any], width: int, height: int
) -> Tuple[str, Dict[str, Any]]:
    """Split ``{op, ...params}`` and fill the frame size the compiler knows."""
    op = str(raw.get("op"))
    params = {key: value for key, value in raw.items() if key != "op"}
    if op == "pip":
        params.setdefault("width", width)
        params.setdefault("height", height)
    validate_op(op, params)
    return op, params


def _clip_plan(
    index: int,
    clip: Any,
    effects: Dict[str, List[Any]],
    chosen: Dict[str, str],
    backend: str,
    width: int,
    height: int,
    fps: int,
) -> Dict[str, Any]:
    duration = clip.timeline_duration
    generator_alpha = bool(
        isinstance(clip.generator, Mapping) and clip.generator.get("alpha") is True
    )
    if generator_alpha and clip.layer < 1:
        raise CompileError(
            "GENERATOR_ALPHA_LAYER",
            "an alpha generator is composited over the layer below, so it needs "
            f"layer >= 1; clip {clip.id!r} sits on layer {clip.layer}",
            f"clips[{index}]",
        )
    final_format = "rgba" if generator_alpha else "yuv420p"
    transform = effects.get("transform")
    scale = 1.0
    opacity_track: Optional[KeyframeTrack] = None
    static_opacity: Optional[float] = None
    if transform:
        raw_scale = transform[0].sample("scale", 0.0)
        scale = 1.0 if raw_scale is None else float(raw_scale)
        opacity_track = transform[0].keyframes.get("opacity")
        static_opacity = transform[0].sample("opacity", 0.0)

    scaled_width = _even(width * scale)
    scaled_height = _even(height * scale)

    blocks: List[Dict[str, Any]] = [
        {"kind": "filters", "filters": ["setpts=PTS-STARTPTS"]}
    ]
    time_blocks, time_fx, time_details = _time_fx_stage(clip, index, fps)
    blocks.extend(time_blocks)
    speed_block, speed_ratio, speed_plan = _speed_stage(clip, index, fps)
    if speed_block is not None:
        blocks.append(speed_block)

    geometry: List[str] = []
    crop = effects.get("crop")
    if crop:
        geometry.append(_crop_fragment(crop[0]))
    geometry.append(
        f"scale={scaled_width}:{scaled_height}:"
        "force_original_aspect_ratio=decrease:flags=bicubic"
    )
    for ordinal, color in enumerate(effects.get("color") or []):
        geometry.append(_color_fragment(index, ordinal, color, fps, duration))
    for blur in effects.get("blur") or []:
        geometry.append(_blur_fragment(blur))
    blocks.append({"kind": "filters", "filters": geometry})

    opacity_filter: Optional[str] = None
    opacity_mode = "none"
    if transform:
        opacity_filter, opacity_mode = _opacity_fragment(
            index, opacity_track, static_opacity, backend, fps, duration
        )
        stage = ["format=rgba"]
        if opacity_filter is not None:
            stage.append(opacity_filter)
        blocks.append({"kind": "filters", "filters": stage})

    fade_filter = _transition_fragment(clip.transition_in, duration)
    fade_out_filter = _transition_fragment(clip.transition_out, duration)
    fades = [fade for fade in (fade_filter, fade_out_filter) if fade is not None]

    compose_ops: List[str] = []
    compose_op: Optional[str] = None
    compose_fragment: Optional[Dict[str, Any]] = None
    if clip.compose is not None:
        compose_op, compose_params = _compose_declaration(clip.compose, width, height)
        compose_ops.append(compose_op)
        if compose_op == "blend":
            compose_fragment = blend_fragment(compose_params["mode"])
        else:
            compose_fragment = COMPOSE_BUILDERS[compose_op](**compose_params)

    canvas = [f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2", "setsar=1"]
    chains: List[str] = []
    if transform is None:
        clip_blocks = blocks + [
            {"kind": "filters", "filters": canvas + [f"format={final_format}"] + fades}
        ]
        chains = _render_blocks(index, f"[{index}:v]", clip_blocks, f"c{index}")
    else:
        chains = _render_blocks(index, f"[{index}:v]", blocks, f"fg{index}")
        chains.append(
            f"color=c=black:s={width}x{height}:r={fps}:d={_num(duration)}[bg{index}]"
        )
        centred = (
            f"[bg{index}][fg{index}]overlay="
            "x=(W-w)/2:y=(H-h)/2:format=auto:eof_action=pass:shortest=1"
        )
        composited = [centred, f"format={final_format}"]
        composited.extend(fades)
        chains.append(",".join(composited) + f"[c{index}]")

    audio_policy = (
        DEFAULT_RAMP_AUDIO_POLICY.value
        if clip.audio_policy is None
        else str(clip.audio_policy)
    )
    audio_payload: Optional[Dict[str, Any]] = None
    if speed_plan is not None:
        audio_payload = audio_fragment(
            audio_policy, float(speed_plan["ratio"])
        ).to_dict()

    mechanisms = {
        kind: chosen.get(kind, EFFECT_REGISTRY[kind].backend) for kind in effects
    }
    return {
        "id": clip.id,
        "index": index,
        "layer": clip.layer,
        "duration": duration,
        "backend": backend if opacity_mode == "keyframed" else BACKEND_EXPR,
        "backends": mechanisms,
        "opacity_mode": opacity_mode,
        "opacity_filter": opacity_filter,
        "fade_filter": fade_filter,
        "fade_out_filter": fade_out_filter,
        "scale": scale,
        "keyframes": (len(opacity_track.keys) if opacity_track is not None else 0),
        "time_fx": time_fx,
        "time_fx_detail": time_details,
        "speed": speed_ratio,
        "audio_policy": audio_policy,
        "audio_policy_declared": clip.audio_policy is not None,
        "audio": audio_payload,
        "compose": compose_ops,
        "compose_op": compose_op,
        "compose_fragment": compose_fragment,
        "generator": (
            None
            if not isinstance(clip.generator, Mapping)
            else {
                "template": clip.generator.get("template"),
                "engine": clip.generator.get("engine"),
                "alpha": generator_alpha,
            }
        ),
        "order": list(CLIP_ORDER),
        "chains": chains,
    }


# ------------------------------------------------------------------ audio


def _audio_roles(timeline: Timeline3) -> Tuple[str, str]:
    ducking = timeline.ducking
    target = ducking.target_role if ducking is not None else "music"
    sidechain = ducking.sidechain_role if ducking is not None else ""
    return sidechain, target or "music"


def _audio_time_filters(timeline: Timeline3, audio_clip: Any) -> List[str]:
    """Reverse and ramp filters for an audio clip that follows a video clip.

    The bus is a separate entity from the picture, so the link is the declared
    source: an audio clip whose ``source`` matches a retimed video clip gets the
    same treatment, planned by the very modules the picture uses.
    """
    for clip in timeline.clips:
        if clip.source != audio_clip.source:
            continue
        filters: List[str] = []
        if clip.reverse:
            filters.append("areverse")
        if clip.speed is not None:
            curve = _speed_curve(clip)
            if curve is not None:
                plan = ramp_fragment(curve, fps=float(timeline.fps))
                policy = (
                    DEFAULT_RAMP_AUDIO_POLICY.value
                    if clip.audio_policy is None
                    else str(clip.audio_policy)
                )
                filters.extend(list(audio_fragment(policy, float(plan["ratio"]))))
        if filters:
            return filters
    return []


def _audio_clip_filters(
    clip: Any, *, delay: bool, time_filters: Optional[List[str]] = None
) -> List[str]:
    duration = max(0.0, clip.src_out - clip.src_in)
    filters = [
        f"atrim=start=0.000000:end={_num(duration)}",
        "asetpts=PTS-STARTPTS",
        f"aformat=sample_rates={AUDIO_RATE}:channel_layouts=stereo",
    ]
    filters.extend(time_filters or [])
    if clip.gain_db != 0.0:
        filters.append(f"volume={float(clip.gain_db):.3f}dB")
    if delay:
        milliseconds = int(round(float(clip.dst_in) * 1000.0))
        filters.append(f"adelay={milliseconds}|{milliseconds}")
    elif duration > 0.0:
        fade = min(DIALOGUE_FADE_SEC, duration / 2.0)
        filters.append(f"afade=t=in:st=0.000000:d={_num(fade)}")
        filters.append(f"afade=t=out:st={_num(duration - fade)}:d={_num(fade)}")
    return filters


def _audio_bus(
    timeline: Timeline3, policy: str, first_index: int
) -> Tuple[List[str], List[str], Optional[str]]:
    """Return ``(chains, input_args, final_label)`` for the declared audio buses."""
    if policy == "drop" or not timeline.audio:
        return [], [], None

    _sidechain_role, target_role = _audio_roles(timeline)
    dialogue: List[Any] = []
    music: List[Any] = []
    for track in timeline.audio:
        bucket = music if track.role == target_role else dialogue
        bucket.extend(track.clips)
    if not dialogue and not music:
        return [], [], None
    dialogue.sort(key=lambda clip: (float(clip.dst_in), float(clip.src_in)))
    music.sort(key=lambda clip: (float(clip.dst_in), float(clip.src_in)))

    chains: List[str] = []
    input_args: List[str] = []
    cursor = first_index

    def declare(clip: Any) -> int:
        nonlocal cursor
        index = cursor
        cursor += 1
        duration = max(0.0, clip.src_out - clip.src_in)
        input_args.extend(
            [
                "-ss",
                _num(clip.src_in),
                "-t",
                _num(duration),
                "-i",
                clip.source,
            ]
        )
        return index

    dialogue_labels: List[str] = []
    for position, clip in enumerate(dialogue):
        label = f"dlg{position}"
        index = declare(clip)
        filters = ",".join(
            _audio_clip_filters(
                clip, delay=False, time_filters=_audio_time_filters(timeline, clip)
            )
        )
        chains.append(f"[{index}:a]{filters}[{label}]")
        dialogue_labels.append(label)
    if len(dialogue_labels) == 1:
        dialogue_label: Optional[str] = dialogue_labels[0]
    elif dialogue_labels:
        inputs = "".join(f"[{label}]" for label in dialogue_labels)
        chains.append(f"{inputs}concat=n={len(dialogue_labels)}:v=0:a=1[dialog]")
        dialogue_label = "dialog"
    else:
        dialogue_label = None

    music_labels: List[str] = []
    for position, clip in enumerate(music):
        label = f"mus{position}"
        index = declare(clip)
        filters = ",".join(
            _audio_clip_filters(
                clip, delay=True, time_filters=_audio_time_filters(timeline, clip)
            )
        )
        chains.append(f"[{index}:a]{filters}[{label}]")
        music_labels.append(label)
    if len(music_labels) == 1:
        music_label: Optional[str] = music_labels[0]
    elif music_labels:
        inputs = "".join(f"[{label}]" for label in music_labels)
        chains.append(
            f"{inputs}amix=inputs={len(music_labels)}:duration=longest:"
            "dropout_transition=0:normalize=0[music]"
        )
        music_label = "music"
    else:
        music_label = None

    ducking = timeline.ducking
    if ducking is not None and dialogue_label and music_label:
        linear = max(1.0, 10.0 ** (float(ducking.makeup_db) / 20.0))
        chains.append(f"[{dialogue_label}]asplit=2[dialog_mix][dialog_sc]")
        chains.append(
            f"[dialog_sc]apad=whole_dur={_num(float(timeline.duration))}[dialog_sc_pad]"
        )
        chains.append(
            f"[{music_label}][dialog_sc_pad]sidechaincompress="
            f"threshold={float(ducking.threshold):.3f}:"
            f"ratio={float(ducking.ratio):.3f}:"
            f"attack={float(ducking.attack_ms):.3f}:"
            f"release={float(ducking.release_ms):.3f}:"
            f"makeup={linear:.3f}[music_duck]"
        )
        mixed = (
            "[dialog_mix][music_duck]amix=inputs=2:duration=longest:"
            "dropout_transition=0:normalize=0"
        )
    elif dialogue_label and music_label:
        mixed = (
            f"[{dialogue_label}][{music_label}]amix=inputs=2:duration=longest:"
            "dropout_transition=0:normalize=0"
        )
    elif dialogue_label:
        mixed = f"[{dialogue_label}]anull"
    else:
        mixed = f"[{music_label}]anull"
    chains.append(f"{mixed}[aout_mix]")

    duration = float(timeline.duration)
    chains.append(
        f"[aout_mix]apad=whole_dur={_num(duration)},"
        f"atrim=start=0.000000:end={_num(duration)},"
        "asetpts=PTS-STARTPTS[aout]"
    )
    return chains, input_args, "aout"


# --------------------------------------------------------------- compiling


def _refuse_unsupported(timeline: Timeline3, audio: str) -> None:
    if timeline.text_layers:
        raise CompileError(
            "UNSUPPORTED_EFFECT",
            "text layers are validated and serialised only; the Chromium/ASS "
            "backend lands in F2-T07, so nothing is rendered silently",
            "text_layers[0]",
        )
    if timeline.captions:
        raise CompileError(
            "UNSUPPORTED_EFFECT",
            "caption tracks are validated and serialised only; the Chromium/ASS "
            "backend lands in F2-T07, so nothing is rendered silently",
            "captions[0]",
        )
    if timeline.audio and audio == "refuse":
        raise CompileError(
            "UNSUPPORTED_AUDIO",
            f"{len(timeline.audio)} audio track(s) carried but audio is refused "
            "by policy; pass audio='mix' to build the bus or audio='drop' to "
            "render the picture only and record the policy",
            "audio",
        )


def _route_effects(
    timeline: Timeline3, settings: Mapping[str, Any]
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Route every effect, and decide what a chromium qualifier is realized by.

    A qualifying node is a *marker*: the property it names (layout, reflow, a
    backdrop filter, an animated blend) is realized by the captured template of
    the clip it sits on, not by a filter in this graph. So the rule is accepted
    only when that clip carries a generator, and the marker must be empty --
    parameters or keyframes on it would be silently discarded, and this compiler
    never discards a declaration. With the engine available and no generator the
    document is refused with CHROMIUM_NEEDS_GENERATOR; with the engine absent it
    stays BACKEND_UNAVAILABLE.
    """
    scopes: List[Tuple[str, Any, Any]] = []
    for index, layer in enumerate(timeline.adjustments):
        for position, effect in enumerate(layer.effects):
            scopes.append((f"adjustments[{index}].effects[{position}]", effect, None))
    for index, clip in enumerate(timeline.clips):
        for position, effect in enumerate(clip.effects):
            scopes.append((f"clips[{index}].effects[{position}]", effect, clip))

    table: List[Dict[str, Any]] = []
    routed: Dict[str, Dict[str, Any]] = {}
    summary = {
        "available": bool(settings["chromium_available"]),
        "qualified": 0,
        "redirected": 0,
        "refused": 0,
        "realized_by_generator": 0,
    }
    for where, effect, clip in scopes:
        entry = select_backend(effect, settings=settings)
        generator = None if clip is None else clip.generator
        if entry["rule"] == RULE_QUALIFIED:
            if generator is None:
                raise CompileError(
                    "CHROMIUM_NEEDS_GENERATOR",
                    f"{effect.kind!r} declares chromium with properties "
                    f"{list(_declared_properties(effect))} and the engine is "
                    "available, but this clip declares no generator: the property "
                    "is realized by a captured template, and there is none",
                    where,
                )
            if effect.params or effect.keyframes:
                raise CompileError(
                    "GENERATOR_EFFECT",
                    f"{effect.kind!r} is a marker on a generator clip and must "
                    "carry no params and no keyframes: the picture comes from the "
                    f"captured template, so {effect.params or 'the keyframes'} "
                    "would be discarded",
                    where,
                )
            entry["realized_by"] = "generator"
            summary["qualified"] += 1
            summary["realized_by_generator"] += 1
        elif entry["rule"] == RULE_REDIRECTED:
            summary["redirected"] += 1
        elif entry["rule"] == RULE_REFUSED:
            summary["refused"] += 1
        routed[where] = entry
        row: Dict[str, Any] = {
            "effect": effect.kind,
            "chosen": entry["chosen"],
            "rule": entry["rule"],
            "where": where,
        }
        if "realized_by" in entry:
            row["realized_by"] = entry["realized_by"]
        table.append(row)
        if entry["rule"] == RULE_REFUSED:
            raise CompileError(
                "BACKEND_REFUSED",
                f"{effect.kind!r} declares chromium with no qualifying property "
                f"and no ffmpeg fallback; qualifying properties are "
                f"{sorted(CHROMIUM_QUALIFYING)}",
                where,
            )
        if entry["rule"] == RULE_UNAVAILABLE:
            raise CompileError(
                "BACKEND_UNAVAILABLE",
                f"{effect.kind!r} declares chromium with properties "
                f"{list(_declared_properties(effect))}: the engine qualifies but "
                f"is not available (settings.chromium_available="
                f"{summary['available']}); it is refused, never rendered with "
                "the wrong mechanism",
                where,
            )
    return table, routed, summary


def _split_layers(
    timeline: Timeline3,
) -> Tuple[List[Tuple[int, Any]], List[Tuple[int, Any]]]:
    base: List[Tuple[int, Any]] = []
    overlays: List[Tuple[int, Any]] = []
    for index, clip in enumerate(timeline.clips):
        (overlays if clip.layer >= 1 else base).append((index, clip))
    return base, overlays


def _refuse_layer_overlap(overlays: List[Tuple[int, Any]]) -> None:
    by_layer: Dict[int, List[Tuple[int, Any]]] = {}
    for index, clip in overlays:
        if clip.dst_in is None or clip.dst_out is None:
            raise CompileError(
                "LAYER_RANGE",
                f"layer {clip.layer} clip {clip.id!r} needs dst_in/dst_out: its "
                "window is the only place it is visible",
                f"clips[{index}]",
            )
        by_layer.setdefault(int(clip.layer), []).append((index, clip))
    for layer, items in sorted(by_layer.items()):
        ordered = sorted(items, key=lambda item: (float(item[1].dst_in), item[1].id))
        for previous, current in zip(ordered, ordered[1:]):
            previous_end = float(previous[1].dst_out)
            current_start = float(current[1].dst_in)
            if current_start < previous_end - 1e-6:
                raise CompileError(
                    "LAYER_OVERLAP",
                    f"layer {layer}: clip {current[1].id!r} starts at "
                    f"{current_start:.6f}s before clip {previous[1].id!r} ends at "
                    f"{previous_end:.6f}s",
                    f"clips[{current[0]}]",
                )


def _generator_table(
    timeline: Timeline3, generator_paths: Optional[Mapping[str, Any]]
) -> Dict[int, Dict[str, Any]]:
    """Prove every generator clip against its ``capture.json``. Pure: reads only.

    The proof is content-addressed and stricter than ``exists()``: the capture
    must name the same template, carry the same template digest, resolve to the
    same parameters, and match the timeline's fps, size and frame count. A
    capture taken from an older template, at another size or for another clock is
    not the picture the document declares, so it is refused instead of rendered.
    """
    from . import motion, motion_cache  # noqa: PLC0415 - compile stays light

    table: Dict[int, Dict[str, Any]] = {}
    for index, clip in enumerate(timeline.clips):
        if clip.generator is None:
            continue
        where = f"clips[{index}].generator"
        declaration = clip.generator
        if not isinstance(declaration, Mapping):
            raise CompileError(
                "GENERATOR_SHAPE",
                f"generator must be an object, got {type(declaration).__name__}",
                where,
            )
        entry = None if generator_paths is None else generator_paths.get(clip.id)
        if entry is None:
            raise CompileError(
                "GENERATOR_CAPTURE",
                f"generator clip {clip.id!r} has no entry in generator_paths: "
                "compile_timeline is pure and never launches an engine, so "
                "render() materialises the frames first",
                where,
            )
        directory = Path(entry)
        capture_path = directory / "capture.json"
        try:
            capture = json.loads(capture_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CompileError(
                "GENERATOR_CAPTURE",
                f"{capture_path.name} is missing or unreadable: {exc}",
                where,
            ) from exc
        if not isinstance(capture, Mapping):
            raise CompileError(
                "GENERATOR_CAPTURE", "capture.json must be an object", where
            )
        try:
            meta = motion.load_template(declaration.get("template"))
        except Exception as exc:  # noqa: BLE001 - declared, with a stable code
            raise CompileError(
                "GENERATOR_TEMPLATE",
                f"template {declaration.get('template')!r} cannot be loaded: {exc}",
                where,
            ) from exc
        try:
            resolved = motion.validate_params(meta, declaration.get("params") or {})
        except Exception as exc:  # noqa: BLE001 - declared, with a stable code
            raise CompileError(
                "GENERATOR_PARAMS",
                f"params do not resolve for {meta['name']!r}: {exc}",
                where,
            ) from exc
        digest = motion_cache.template_digest(meta["dir"])
        expected_frames = max(
            1, int(round(float(meta["duration"]) * max(0, timeline.fps)))
        )
        mismatches: List[str] = []
        if capture.get("template") != meta["name"]:
            mismatches.append(
                f"template {capture.get('template')!r} != {meta['name']!r}"
            )
        if capture.get("template_digest") != digest:
            mismatches.append("template digest differs from the template's content")
        if capture.get("params") != resolved:
            mismatches.append(f"params {capture.get('params')!r} != {resolved!r}")
        try:
            captured_fps = float(capture.get("fps"))
        except (TypeError, ValueError):
            captured_fps = -1.0
        if captured_fps != float(timeline.fps):
            mismatches.append(f"fps {capture.get('fps')!r} != {timeline.fps}")
        if list(capture.get("size") or []) != [timeline.width, timeline.height]:
            mismatches.append(
                f"size {capture.get('size')!r} != {[timeline.width, timeline.height]}"
            )
        if capture.get("frame_count") != expected_frames:
            mismatches.append(
                f"frame count {capture.get('frame_count')!r} != {expected_frames} "
                f"for {meta['duration']}s at {timeline.fps} fps"
            )
        if not (directory / "frame_00000.png").is_file():
            mismatches.append("frame_00000.png is absent from the capture directory")
        if mismatches:
            raise CompileError(
                "GENERATOR_CAPTURE",
                "the capture does not match the declaration: " + "; ".join(mismatches),
                where,
            )
        table[index] = {
            "directory": str(directory),
            "capture": capture,
            "frames": expected_frames,
            "template": meta["name"],
            "duration": float(meta["duration"]),
        }
    return table


def compile_timeline(
    timeline: Timeline3,
    *,
    audio: str = "refuse",
    settings: Optional[Mapping[str, Any]] = None,
    nested_paths: Optional[Mapping[str, str]] = None,
    generator_paths: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Validate, route every effect, and return a compile plan.

    Nested sequences are resolved by :mod:`vx.edl3_nested` and *rendered once*
    by :func:`render`; the resulting ``{virtual_source: path}`` map arrives as
    ``nested_paths``. Generator clips arrive the same way: ``generator_paths``
    maps a clip id to the directory :func:`_materialize_generators` captured, and
    each directory's ``capture.json`` is checked against the declaration. A
    document with either cannot be compiled without that evidence, because a
    compile never runs ffmpeg and never launches a browser.
    """
    if timeline.version != EDL3_VERSION:
        raise CompileError(
            "SCHEMA_VERSION",
            f"unsupported document version {timeline.version!r}",
            "version",
        )
    if audio not in AUDIO_POLICIES:
        raise CompileError(
            "AUDIO_POLICY",
            f"audio policy {audio!r} not in {sorted(AUDIO_POLICIES)}",
            "audio",
        )
    resolved = resolve_settings(settings)

    nesting = resolve_nesting(timeline, max_depth=MAX_NESTING_DEPTH)

    for problem in timeline.validate():
        if problem["code"] in HARD_CODES:
            raise CompileError(problem["code"], problem["detail"], problem["where"])

    sources: Dict[int, str] = {}
    for clip_index, clip in enumerate(timeline.clips):
        if clip.sequence is None:
            continue
        virtual = nesting["sources"].get(clip.id)
        path = None if virtual is None else (nested_paths or {}).get(virtual)
        if path is None:
            raise CompileError(
                "UNSUPPORTED_SEQUENCE",
                f"clip {clip.id!r} references sequence {clip.sequence!r}: an "
                "inner timeline is rendered once by render() and joins the source "
                "table as "
                f"{virtual or 'a virtual source'!r}; compile_timeline is pure, so "
                "it needs that rendered source in nested_paths",
                f"clips[{clip_index}].sequence",
            )
        sources[clip_index] = str(path)

    generators = _generator_table(timeline, generator_paths)

    table, routed, chromium = _route_effects(timeline, resolved)
    _refuse_unsupported(timeline, audio)

    base_clips, overlay_clips = _split_layers(timeline)
    if not base_clips:
        raise CompileError(
            "LAYER_RANGE",
            "a timeline needs at least one layer 0 clip to concatenate; "
            f"every clip sits on layer >= 1 ({len(overlay_clips)} clip(s))",
            "clips",
        )
    _refuse_layer_overlap(overlay_clips)

    backend = choose_backend(timeline)

    plans: Dict[int, Dict[str, Any]] = {}
    for clip_index, clip in enumerate(timeline.clips):
        clip_where = f"clips[{clip_index}]"
        source = sources.get(clip_index, clip.source)
        if clip.generator is None and not Path(source).exists():
            raise CompileError(
                "MISSING_SOURCE",
                f"source file not found: {source}",
                f"{clip_where}.source",
            )
        effects, chosen = _effective_effects(timeline, clip, clip_where, routed)
        for slot, transition in (
            ("transition_in", clip.transition_in),
            ("transition_out", clip.transition_out),
        ):
            if transition is None or transition.kind in RENDERABLE_TRANSITIONS:
                continue
            raise CompileError(
                "UNSUPPORTED_TRANSITION",
                f"transition kind {transition.kind!r} is not implemented by edl3 "
                f"(implemented: {sorted(RENDERABLE_TRANSITIONS)})",
                f"{clip_where}.{slot}.kind",
            )
        plans[clip_index] = _clip_plan(
            clip_index,
            clip,
            effects,
            chosen,
            chosen.get("transform") or BACKEND_EXPR,
            timeline.width,
            timeline.height,
            timeline.fps,
        )

    chains: List[str] = []
    for index, _clip in base_clips:
        chains.extend(plans[index]["chains"])
    if len(base_clips) == 1:
        base_label = f"c{base_clips[0][0]}"
    else:
        inputs = "".join(f"[c{index}]" for index, _clip in base_clips)
        chains.append(f"{inputs}concat=n={len(base_clips)}:v=1:a=0[out]")
        base_label = "out"

    overlay_clips = sorted(
        overlay_clips,
        key=lambda item: (
            int(item[1].layer),
            float(item[1].dst_in or 0.0),
            item[1].id,
        ),
    )
    for position, (index, clip) in enumerate(overlay_clips):
        start = float(clip.dst_in or 0.0)
        end = float(clip.dst_out or 0.0)
        window = (
            f"enable='between(t,{_num(start)},{_num(end)})':eof_action=pass:format=auto"
        )
        op = plans[index]["compose_op"]
        chains.extend(plans[index]["chains"])
        if op is None:
            chains.append(f"[c{index}]setpts=PTS+{_num(start)}/TB[ov{position}]")
            chains.append(
                f"[{base_label}][ov{position}]overlay={window}[base{position}]"
            )
        elif op == "blend":
            fragment = relabel(
                plans[index]["compose_fragment"],
                {
                    "base": f"bwin{position}",
                    "fg": f"c{index}",
                    "blend": f"blended{position}",
                },
            )
            chains.append(f"[{base_label}]split=2[bm{position}][bw{position}]")
            chains.append(
                f"[bw{position}]trim=start={_num(start)}:end={_num(end)},"
                f"setpts=PTS-STARTPTS[bwin{position}]"
            )
            chains.extend(
                _chain_into(fragment, f"blended{position}", ["format=yuv420p"])
            )
            chains.append(
                f"[blended{position}]setpts=PTS+{_num(start)}/TB[blendt{position}]"
            )
            chains.append(
                f"[bm{position}][blendt{position}]overlay=0:0:{window}[base{position}]"
            )
        else:
            if op not in KEYING_OPS:
                # Unreachable through a validated document; a guard, not a fallback.
                raise CompileError(
                    "COMPOSE_OP",
                    f"compose op {op!r} has no layer-stage wiring "
                    f"(keying ops: {sorted(KEYING_OPS)}, plus blend)",
                    f"clips[{index}].compose",
                )
            fragment = relabel(
                plans[index]["compose_fragment"],
                {
                    "fg": f"ov{position}",
                    "pip": f"pip{position}",
                    "masked": f"msk{position}",
                    "chroma": f"chr{position}",
                    "mskA": f"ma{position}",
                    "mskB": f"mb{position}",
                    "mskMask": f"mm{position}",
                },
            )
            chains.append(f"[c{index}]setpts=PTS+{_num(start)}/TB[ov{position}]")
            chains.extend(fragment["filters"])
            keyed = fragment["produces"].strip("[]")
            chains.append(
                f"[{base_label}][{keyed}]overlay=0:0:{window},"
                f"format=yuv420p[base{position}]"
            )
        base_label = f"base{position}"

    audio_chains, audio_input_args, audio_label = _audio_bus(
        timeline, audio, len(timeline.clips)
    )
    chains.extend(audio_chains)

    input_args: List[str] = []
    for clip_index, clip in enumerate(timeline.clips):
        if clip.generator is not None:
            # The capture directory is a numbered image sequence: same input
            # ordinal as the clip index, no -ss, and the timeline's own clock.
            entry = generators[clip_index]
            input_args.extend(
                [
                    "-framerate",
                    _num(float(timeline.fps)),
                    "-loop",
                    "1",
                    "-t",
                    _num(clip.timeline_duration),
                    "-i",
                    str(Path(entry["directory"]) / "frame_%05d.png"),
                ]
            )
            continue
        input_args.extend(
            [
                "-ss",
                _num(clip.src_in),
                "-t",
                _num(_input_span(clip)),
                "-i",
                sources.get(clip_index, clip.source),
            ]
        )
    input_args.extend(audio_input_args)

    warnings: List[str] = []
    for index, clip in enumerate(timeline.clips):
        entry = plans[index]
        if entry["speed"] is not None and not entry["audio_policy_declared"]:
            warnings.append(
                f"clips[{index}].speed: the document declares no audio policy, so "
                f"the compiler used {entry['audio_policy']!r} "
                f"(declared: segments, silence, pitch)"
            )
        if "freeze" in entry["time_fx"] and timeline.audio:
            warnings.append(
                f"clips[{index}].freeze: the hold is video-only; the audio bus is "
                "not held across the freeze"
            )
        if timeline.audio and (
            entry["speed"] is not None or "freeze" in entry["time_fx"]
        ):
            warnings.append(
                f"clips[{index}]: the retimed picture is not "
                f"{timeline.duration:.3f}s long, while the audio bus is padded and "
                "trimmed to that declared duration"
            )

    return {
        "version": timeline.version,
        "backend": backend,
        "audio_policy": audio,
        "filter_script": ";\n".join(chains) + "\n",
        "input_args": input_args,
        "map": f"[{base_label}]",
        "audio_map": f"[{audio_label}]" if audio_label else None,
        "overlays": len(overlay_clips),
        "clips": [
            {
                key: value
                for key, value in plans[index].items()
                if key not in {"chains", "compose_fragment"}
            }
            for index in range(len(timeline.clips))
        ],
        "backends": table,
        "chromium": chromium,
        "generators": [
            {
                "clip": timeline.clips[index].id,
                "index": index,
                "template": entry["capture"].get("template"),
                "engine": entry["capture"].get("engine"),
                "fallback": bool(entry["capture"].get("fallback")),
                "degraded": list(entry["capture"].get("degraded") or []),
                "frames": entry["capture"].get("frame_count"),
                "directory": entry["directory"],
            }
            for index, entry in sorted(generators.items())
        ],
        "nested_sequences": list(nesting["order"]),
        "warnings": warnings,
    }


def _input_span(clip: Any) -> float:
    """Seconds to read from the source.

    A ramp or a freeze changes how long the result is, so the read length is the
    declared source span, never the timeline placement.
    """
    if clip.speed is not None or clip.freeze is not None:
        return max(0.0, clip.duration)
    return clip.timeline_duration


def _chain_into(
    fragment: Mapping[str, Any], produce: str, extra: List[str]
) -> List[str]:
    """Append ``extra`` filters to the one chain that produces ``produce``."""
    filters = list(fragment.get("filters") or [])
    token = f"[{produce}]"
    if not filters or not filters[-1].endswith(token):
        raise CompileError(
            "COMPOSE_FRAGMENT",
            f"fragment {fragment.get('op')!r} does not produce {token}",
            str(fragment.get("produces")),
        )
    filters[-1] = filters[-1][: -len(token)] + "," + ",".join(extra) + token
    return filters


def _encode(
    plan: Mapping[str, Any], destination: Path, script_dir: Path, timeout: Optional[int]
) -> Tuple[ffmpeg.Proc, Path]:
    script_path = script_dir / "filter_complex.txt"
    script_path.write_text(plan["filter_script"], encoding="utf-8", newline="\n")

    args = list(plan["input_args"])
    args.extend(["-filter_complex_script", str(script_path), "-map", plan["map"]])
    if plan["audio_map"]:
        args.extend(
            [
                "-map",
                plan["audio_map"],
                "-c:a",
                "aac",
                "-b:a",
                AUDIO_BITRATE,
                "-ar",
                str(AUDIO_RATE),
            ]
        )
    else:
        args.append("-an")
    args.extend(
        [
            "-sn",
            "-dn",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(destination),
        ]
    )
    proc = ffmpeg.run_ff(args, loglevel="error", timeout=timeout, step="render")
    if not proc.ok:
        raise CompileError("RENDER_FAILED", proc.tail(), str(destination))
    if not destination.exists() or destination.stat().st_size == 0:
        raise CompileError(
            "RENDER_FAILED",
            "ffmpeg reported success but wrote no output",
            str(destination),
        )
    return proc, script_path


def _render_nested(
    timeline: Timeline3,
    script_dir: Path,
    timeout: Optional[int],
    audio: str,
    settings: Optional[Mapping[str, Any]],
) -> Dict[str, str]:
    """Render every reachable inner sequence exactly once, children first."""
    plan = resolve_nesting(timeline, max_depth=MAX_NESTING_DEPTH)
    if not plan["renders"]:
        return {}
    root = script_dir / "nested"
    root.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, str] = {}
    for entry in plan["renders"]:
        virtual = entry["virtual_source"]
        destination = root / f"{virtual}.mp4"
        if not destination.exists() or destination.stat().st_size == 0:
            inner_dir = root / virtual
            inner_dir.mkdir(parents=True, exist_ok=True)
            inner_plan = compile_timeline(
                entry["timeline"],
                audio=audio,
                settings=settings,
                nested_paths=dict(paths),
            )
            _encode(inner_plan, destination, inner_dir, timeout)
        paths[virtual] = str(destination)
    return paths


def _materialize_generators(
    timeline: Timeline3,
    script_dir: Path,
    probe: Mapping[str, Any],
) -> Dict[str, str]:
    """Capture each generator clip once and write the record compile then verifies.

    `compile_timeline` stays pure, so the pixels are produced here, before it runs,
    the same way `_render_nested` produces inner sequences. The engine is chosen by
    `motion_fallback.resolve_engine` from the declared engine and the real probe;
    when it answers `unavailable` the document is refused with BACKEND_UNAVAILABLE
    rather than rendered with a raster that cannot honour the effect.
    """
    from . import motion, motion_cache, motion_fallback  # noqa: PLC0415 - render only

    clips = [clip for clip in timeline.clips if clip.generator is not None]
    if not clips:
        return {}
    required: set = set()
    for clip in clips:
        for effect in clip.effects or ():
            required.update(getattr(effect, "properties", ()) or ())
    for layer in timeline.adjustments:
        for effect in layer.effects or ():
            required.update(getattr(effect, "properties", ()) or ())
    root = script_dir / "generators"
    root.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, str] = {}
    for index, clip in enumerate(timeline.clips):
        if clip.generator is None:
            continue
        declaration = clip.generator
        where = f"clips[{index}].generator"
        meta = motion.load_template(declaration.get("template"))
        resolved = motion.validate_params(meta, declaration.get("params") or {})
        decision = motion_fallback.resolve_engine(
            declaration.get("engine") or "auto", probe, required
        )
        if decision["engine"] == "unavailable":
            raise CompileError(
                "BACKEND_UNAVAILABLE",
                f"generator clip {clip.id!r} requires {sorted(required)} and "
                f"{decision['reason']}",
                where,
            )
        out_dir = root / str(clip.id)
        out_dir.mkdir(parents=True, exist_ok=True)
        if decision["engine"] == "pillow":
            capture = motion_fallback.capture
        else:
            capture = motion.capture
        result = capture(
            meta["dir"],
            resolved,
            out_dir,
            fps=timeline.fps,
            size=(timeline.width, timeline.height),
        )
        record = {
            "template": result["template"],
            "template_digest": motion_cache.template_digest(meta["dir"]),
            "params": resolved,
            "fps": result["fps"],
            "size": [int(result["size"][0]), int(result["size"][1])],
            "frame_count": result["frame_count"],
            "engine": result["engine"],
            "engine_version": result.get("engine_version", ""),
        }
        (out_dir / "capture.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        paths[str(clip.id)] = str(out_dir)
    return paths


def render(
    timeline: Timeline3,
    output: Any,
    *,
    workdir: Optional[Any] = None,
    timeout: Optional[int] = None,
    audio: str = "refuse",
    settings: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Render ``timeline`` to ``output`` and return the compile/run report."""
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    script_dir = Path(workdir) if workdir is not None else destination.parent
    script_dir.mkdir(parents=True, exist_ok=True)
    from . import motion  # noqa: PLC0415 - render only

    probe = motion.available()
    if settings is None:
        settings = {"chromium_available": bool(probe.get("available"))}
        settings_source = "probe"
    else:
        settings_source = "declared"
    nested = _render_nested(timeline, script_dir, timeout, audio, settings)
    generators = _materialize_generators(timeline, script_dir, probe)
    plan = compile_timeline(
        timeline,
        audio=audio,
        settings=settings,
        nested_paths=nested,
        generator_paths=generators,
    )
    proc, script_path = _encode(plan, destination, script_dir, timeout)

    report: Dict[str, Any] = {
        "version": plan["version"],
        "backend": plan["backend"],
        "audio_policy": plan["audio_policy"],
        "output": str(destination.resolve()),
        "output_size": destination.stat().st_size,
        "duration": timeline.duration,
        "fps": timeline.fps,
        "size": [timeline.width, timeline.height],
        "filter_script": plan["filter_script"],
        "filter_script_path": str(script_path.resolve()),
        "clips": plan["clips"],
        "map": plan["map"],
        "audio_map": plan["audio_map"],
        "overlays": plan["overlays"],
        "backends": plan["backends"],
        "chromium": plan["chromium"],
        "nested_sequences": plan["nested_sequences"],
        "nested_renders": len(nested),
        "generators": plan["generators"],
        "generator_renders": len(generators),
        "settings_source": settings_source,
        "warnings": plan["warnings"],
        "command": proc.args,
        "stderr_tail": proc.tail(5),
    }
    return report
