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
* every declarative ``filter`` entry (the ``vx.effects`` families) — one static
  ffmpeg filter template, formatted with the instance's static params;
* ``fade_in`` / ``fade_out`` transitions;
* adjustment layers, applied before each clip's own effects;
* multi-layer video: layer 0 concatenates, every higher layer is one
  ``overlay`` gated by ``enable='between(t,start,end)'``;
* text layers — one static ``drawtext`` per layer, drawn **after** the concat,
  the transitions and the overlays, so the layer sits on the finished picture
  and never inside a single clip; the font is the work's own file (bundled
  ``assets/fonts`` or a pinned path), never a system fallback;
* the audio bus: dialogue concatenated, music placed with ``adelay``, ducked by
  ``sidechaincompress`` keyed on the dialogue, summed with ``amix``. A dissolve
  shortens the picture, so the music bed is cut and crossfaded on the same joins
  and stays on the shortened timeline instead of being refused.

Refused loudly, never skipped
-----------------------------
``chroma_key`` and any kind outside :data:`edl3.RENDERABLE_EFFECTS` →
``UNSUPPORTED_EFFECT``. A keyframed instance of a declarative ``filter`` kind →
``EFFECT_NOT_ANIMATABLE``: the template is static and no emitter would honour the
keys. A chromium node that qualifies while the engine is
absent → ``BACKEND_UNAVAILABLE``; a chromium node with no fallback →
``BACKEND_REFUSED``; an unknown property or backend → ``BACKEND_PROPERTY`` /
``BACKEND_UNKNOWN``. Two overlays on the same layer → ``LAYER_OVERLAP``. A text
layer with a field the emitter does not render (``keyframes.*``, multi-line
text) → ``PARAM_NOT_RENDERED`` naming the field; a family with no file of the
work → ``FONT_MISSING``. Caption tracks stay ``UNSUPPORTED_EFFECT``: their
contract declares no style, so painting them would mean inventing one. Nested
sequences → ``UNSUPPORTED_SEQUENCE``. A dissolve on an absolutely placed clip
(``dst_in``/``dst_out``) → ``TRANSITION_DST``: the clip is placed, not
concatenated, so the overlap exists only in sequential mode. Audio is
``UNSUPPORTED_AUDIO`` under the default ``audio="refuse"`` policy;
``audio="mix"`` builds the bus and ``audio="drop"`` states the picture-only
policy.

Loudness: EDL3 declares no loudness target of its own, so the bus is not
normalised -- and that is exactly why the master is *measured*. A mixed bus sums
its masters (``amix`` with ``normalize=0``) and can land at -8 LUFS while every
individual source was fine. :func:`loudness_gate` therefore judges the encoded
file against the delivery spec (-14 +/- 1 LUFS, true peak <= -1 dBTP) and the
report carries ``loudness`` and ``deliverable``: a master outside the spec is
reported as not deliverable, with the measured values, never as done. The mixed
bus is trimmed to the picture duration and encoded with the house AAC settings.

``compile_timeline`` is pure: it never runs ffmpeg. ``render`` writes the
script, runs ffmpeg with ``-filter_complex_script`` and returns the report.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import ffmpeg
class _EmptyTrack:
    """A track with no keys: the rect is static on that axis."""

    keys: tuple = ()


from .edl3 import (
    BACKENDS,
    DEFAULT_FILL_POLICY,
    EDL3_VERSION,
    EDL3_VERSIONS,
    EFFECT_REGISTRY,
    EDLError,
    Effect,
    EffectSpec,
    Keyframe,
    KeyframeTrack,
    MAX_NESTING_DEPTH,
    PRESET_APPLIED_VERSION,
    RENDERABLE_EFFECTS,
    RENDERABLE_TRANSITIONS,
    TRANSITION_RECIPES,
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
from .effects import compose as effect_compose
from .effects import ffmpeg as effect_ffmpeg
from .fonts import font_file as bundled_font

BACKEND_EXPR = "expr"
BACKEND_SENDCMD = "sendcmd"
BACKEND_CHROMIUM = "chromium"
#: The declarative backend: one static ffmpeg filter template per kind, with the
#: instance's static params substituted. Keyframes have no emitter here.
BACKEND_FILTER = "filter"

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

#: NTSC rates are exact rationals, never their decimal rounding: a declared
#: 29.97 is 30000/1001, and writing "29.97" makes ffmpeg stamp 2997/100 on the
#: file, which is not the rate the plan declared (D6).
NTSC_RATES: Tuple[Tuple[float, int, int], ...] = (
    (23.976, 24000, 1001),
    (29.97, 30000, 1001),
    (47.952, 48000, 1001),
    (59.94, 60000, 1001),
    (119.88, 120000, 1001),
)
#: How close a declared decimal must be to a named NTSC rate to be that rate.
RATE_TOLERANCE = 1e-3
#: Mechanisms this compiler actually emits per kind. A node that declares
#: anything else is refused: a report may never name a mechanism the graph does
#: not use.
IMPLEMENTED_BACKENDS: Dict[str, frozenset] = {
    "transform": frozenset({BACKEND_EXPR, BACKEND_SENDCMD}),
    "crop": frozenset({BACKEND_EXPR}),
    "color": frozenset({BACKEND_SENDCMD}),
    "blur": frozenset({BACKEND_EXPR}),
}
#: Every family entry declares the filter backend and nothing else, so its
#: implemented set is exactly that one mechanism.
for _kind, _spec in EFFECT_REGISTRY.items():
    if _spec.backend == BACKEND_FILTER:
        IMPLEMENTED_BACKENDS.setdefault(_kind, frozenset({BACKEND_FILTER}))
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
#: The delivery loudness spec the master is measured against: integrated
#: loudness and true peak. A mixed bus can sum two masters (`amix` with
#: ``normalize=0``) and land at -8.2 LUFS; the render then reports a master that
#: is not deliverable instead of calling it done (D20).
LOUDNESS_TARGET_LUFS = -14.0
LOUDNESS_TOLERANCE_LU = 1.0
TRUE_PEAK_CEILING_DBTP = -1.0
#: Dialogue joint fade, mirroring the house 12 ms cut fade.
DIALOGUE_FADE_SEC = 0.012
#: ffmpeg's ``-benchmark`` line on this build: Windows omits utime/stime/rtime,
#: so the peak RSS is the whole measurement it offers.
BENCH = re.compile(r"bench: maxrss=(\d+)KiB")

#: The SDR delivery: the 8-bit picture this engine has always produced, and the
#: one a document without a profile (or with an SDR profile) gets. Byte identity
#: with the pre-F26-T05 renderer rests on this row staying exactly this.
SDR_DELIVERY: Dict[str, Any] = {
    "profile": None,
    "transfer": "sdr",
    "bit_depth": 8,
    "declared_bit_depth": 8,
    "encoder": "libx264",
    "pix_fmt": "yuv420p",
    "tags": {},
    "x264_params": "",
}
#: A BT.2100 delivery is 10 bit by definition, so the transfer is what switches
#: the pixel format; the encoder stays libx264 because this build's x264 lists
#: yuv420p10le among its pixel formats.
HDR_BIT_DEPTH = 10
HDR_PIX_FMT = "yuv420p10le"
HDR_ENCODER = "libx264"
#: BT.2100 stream tags per declared transfer, plus the x264 VUI spelling of the
#: same three values. This build's libx264 carries colour into the bitstream VUI
#: through ``-x264-params`` only: the ``-color_primaries``/``-color_trc`` stream
#: options alone leave ``color_primaries``/``color_transfer`` unreadable by
#: ffprobe on the muxed file, so both are written and the test measures the
#: result on the file rather than trusting the command line.
HDR_DELIVERY: Dict[str, Dict[str, str]] = {
    "hlg": {
        "color_primaries": "bt2020",
        "color_trc": "arib-std-b67",
        "colorspace": "bt2020nc",
        "x264_params": "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
    },
    "pq": {
        "color_primaries": "bt2020",
        "color_trc": "smpte2084",
        "colorspace": "bt2020nc",
        "x264_params": "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
    },
}

#: Structural validation codes the compiler refuses outright.
HARD_CODES = frozenset(
    {
        "SCHEMA_VERSION",
        "PROFILE_UNKNOWN",
        "STRUCTURE_DURATION",
        "STRUCTURE_DENSITY",
        "STRUCTURE_HOOK",
        "STRUCTURE_INTERVIEW",
        "FPS_INVALID",
        "SIZE_INVALID",
        "FILL_POLICY",
        "TIMELINE_EMPTY",
        "DUPLICATE_CLIP_ID",
        "CLIP_RANGE",
        "CLIP_SHAPE",
        "LAYER_RANGE",
        "EFFECT_UNKNOWN",
        "EFFECT_PARAM",
        "EFFECT_RANGE",
        "EFFECT_NOT_ANIMATABLE",
        "EFFECT_DENSITY",
        "KEYFRAME_ORDER",
        "KEYFRAME_RANGE",
        "KEYFRAME_INTERP",
        "KEYFRAME_PARAM",
        "KEYFRAME_EMPTY",
        "TRANSITION_UNKNOWN",
        "TRANSITION_RANGE",
        "TRANSITION_TOO_LONG",
        "TRANSITION_SLOT",
        "TRANSITION_DST",
        "TRANSITION_ORPHAN",
        "TRANSITION_AUDIO_UNSUPPORTED",
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
        "TEMPLATE_UNKNOWN",
        "PRESET_UNKNOWN",
        "PRESET_EFFECT_CONFLICT",
        "PARAM_NOT_EXPOSED",
        "PARAM_TYPE",
        "PARAM_OUT_OF_RANGE",
    }
)

#: Codes this module raises itself. Every refusal names one of them.
REFUSAL_CODES = frozenset(
    {
        "AUDIO_POLICY",
        "SETTINGS_UNKNOWN",
        "SETTINGS_TYPE",
        "FILL_POLICY",
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
        "CHUNK_UNSUPPORTED",
        "SPEC_INVALID",
        "BUDGET_EXCEEDED",
        "PRIMITIVE_NOT_RENDERED",
        "CURVE_NOT_REPRESENTABLE",
        "BLUR_STYLE_NOT_RENDERED",
        "PARAM_NOT_RENDERED",
        "PARAM_NOT_ANIMATABLE",
        "MASK_SHAPE_NOT_RENDERED",
        "TIME_MODE_NOT_RENDERED",
        "TIME_FREEZE_EMPTY",
        "BLEND_MODE_NOT_RENDERED",
        "BLEND_SOURCE_MISSING",
        "BLEND_INPUT_UNAVAILABLE",
        "FONT_MISSING",
        "FONT_METRICS_MISSING",
        "FONT_UNREADABLE",
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
    "BACKEND_FILTER",
    "SENDCMD_THRESHOLD",
    "SENDCMD_TRACKS",
    "CHROMIUM_QUALIFYING",
    "DEFAULT_SETTINGS",
    "NTSC_RATES",
    "LOUDNESS_TARGET_LUFS",
    "LOUDNESS_TOLERANCE_LU",
    "TRUE_PEAK_CEILING_DBTP",
    "RULES",
    "AUDIO_POLICIES",
    "HARD_CODES",
    "REFUSAL_CODES",
    "MODULE_CODES",
    "KEYING_OPS",
    "CLIP_ORDER",
    "SUPPORTED_EFFECTS",
    "SDR_DELIVERY",
    "HDR_DELIVERY",
    "delivery_colour",
    "loudness_gate",
    "fill_policy",
    "fps_ratio",
    "fps_literal",
    "timebase_literal",
    "CompileError",
    "resolve_settings",
    "select_backend",
    "choose_backend",
    "track_expression",
    "sendcmd_commands",
    "compile_timeline",
    "render",
    "render_effect_spec",
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


def _crop_fragment(
    effect: Any, *, width: int, height: int, fps: float
) -> str:
    """The rect as a time-variable expression, on the filter that can animate it.

    ``crop`` evaluates ``w`` and ``h`` once, at initialisation, and only ``x``/``y``
    per frame: a keyframed width therefore froze at its first value, and every move
    that changes the scale (a push-out, a zoom, a crane) rendered the untouched
    frame. ``zoompan`` evaluates ``z`` per frame, so an animated size becomes a
    ``z`` expression instead: ``z = 1 / width`` because a full-frame crop is zoom 1.

    A rect whose size is static stays a plain ``crop``: no need to pay for a
    scaler, and its reference frames do not move.
    """

    def fraction(name: str, default: float, time_symbol: str = "t") -> str:
        track = effect.keyframes.get(name)
        if track is not None and len(track.keys) > 1:
            return _escape_commas(track_expression(track, time_symbol=time_symbol))
        if track is not None:
            return _num(float(track.keys[0].value))
        return _num(float(effect.params.get(name, default)))

    sized = any(
        len((effect.keyframes.get(name) or _EmptyTrack()).keys) > 1
        for name in ("width", "height")
    )
    if sized:
        # zoompan's clock is the output frame index, so the same track expression
        # is written against ``on / fps``.
        clock = f"(on/{fps_literal(fps)})"
        zoom = fraction("width", 1.0, clock)
        x = fraction("x", 0.0, clock)
        y = fraction("y", 0.0, clock)
        return (
            f"zoompan=z='1/({zoom})':x='iw*({x})':y='ih*({y})':d=1:"
            f"s={int(width)}x{int(height)}:fps={fps_literal(fps)}"
        )
    return (
        f"crop=w=iw*({fraction('width', 1.0)}):h=ih*({fraction('height', 1.0)}):"
        f"x=iw*({fraction('x', 0.0)}):y=ih*({fraction('y', 0.0)})"
    )


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


def filter_number(value: float) -> str:
    """A stable decimal literal: never an exponent, never a trailing zero."""
    text = f"{float(value):.10f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def fps_ratio(fps: Any) -> Tuple[int, int]:
    """The declared rate as an exact ``(numerator, denominator)``.

    Decimals that name an NTSC rate map back to their rational (29.97 →
    30000/1001), integers stay integers and anything else keeps the closest
    rational the expression parser can carry.
    """
    value = float(fps)
    for named, numerator, denominator in NTSC_RATES:
        if abs(value - named) <= RATE_TOLERANCE:
            return numerator, denominator
    if abs(value - round(value)) <= 1e-9:
        return int(round(value)), 1
    from fractions import Fraction  # noqa: PLC0415 - only for odd rates

    ratio = Fraction(value).limit_denominator(1001)
    return max(1, ratio.numerator), ratio.denominator


def fps_literal(fps: Any) -> str:
    """The rate as ffmpeg writes it: ``30``, ``30000/1001``, ``25/2``."""
    numerator, denominator = fps_ratio(fps)
    return str(numerator) if denominator == 1 else f"{numerator}/{denominator}"


def timebase_literal(fps: Any) -> str:
    """The inverse of :func:`fps_literal`: ``1/30``, ``1001/30000``."""
    numerator, denominator = fps_ratio(fps)
    return f"{denominator}/{numerator}"


#: Template placeholders the formatter fills from the *plan*, not from the node.
#: They are reserved so a template can ask for the canvas it is staged at without
#: every effect declaring two parameters that no document may set.
CANVAS_PLACEHOLDERS = ("canvas_w", "canvas_h")


def filter_fragment(
    effect: Any,
    spec: EffectSpec,
    canvas: Optional[Tuple[int, int]] = None,
) -> str:
    """Format one declarative entry's template with this instance's static params.

    A parameter the node omits takes its declared default, so the graph never
    guesses a value the registry did not publish.

    ``{canvas_w}`` and ``{canvas_h}`` are *reserved* placeholders, not parameters
    of the effect: they are filled from the canvas the clip is staged at, never
    from the node. An effect that rewrites the frame size has to ask the plan
    where it is going -- ``zoompan`` without ``s=`` silently emits its own 1280x720
    and the stage then stretches those 720 lines back to a 1920-line canvas
    (D-10). A template that asks for the canvas and gets none is refused rather
    than formatted with a size nobody declared.
    """
    if not spec.filter_template:
        raise CompileError(
            "BACKEND_UNIMPLEMENTED",
            f"{spec.kind!r} declares the filter backend but has no template",
            f"effects.{spec.kind}",
        )
    values = spec.default_params()
    for name, value in (effect.params or {}).items():
        values[name] = float(value)
    formatted = {name: filter_number(value) for name, value in values.items()}
    if any(f"{{{name}}}" in spec.filter_template for name in CANVAS_PLACEHOLDERS):
        if canvas is None:
            raise CompileError(
                "BACKEND_UNIMPLEMENTED",
                f"{spec.kind!r} asks the template for the plan canvas and none "
                "was given",
                f"effects.{spec.kind}",
            )
        width, height = canvas
        formatted["canvas_w"] = str(int(width))
        formatted["canvas_h"] = str(int(height))
    try:
        return spec.filter_template.format(**formatted)
    except (IndexError, KeyError, ValueError) as exc:
        raise CompileError(
            "BACKEND_UNIMPLEMENTED",
            f"{spec.kind!r} has a filter template that cannot be formatted: {exc}",
            f"effects.{spec.kind}",
        ) from exc


def fill_policy(timeline: Timeline3) -> str:
    """The document's effective fill policy.

    ``Timeline3.fill`` is the declaration. A document that does not declare one
    is not automatically letterboxed: a ``crop`` effect is itself a declaration
    of intent -- a crop is a reframe, and a reframe that left black bands at the
    sides would not have reframed anything. So a document carrying a crop
    compiles with ``crop`` unless it explicitly says ``fill="fit"``. Either way
    it is *one* policy for every clip, so two clips can never disagree (D7).
    """
    declared = getattr(timeline, "fill", None)
    if declared is not None:
        declared = str(declared)
        if declared:
            return declared

    def crops(effects: Any) -> bool:
        return any(
            getattr(effect, "kind", "") == "crop" for effect in effects or ()
        )

    for clip in getattr(timeline, "clips", ()) or ():
        if crops(getattr(clip, "effects", ())):
            return "crop"
    for layer in getattr(timeline, "adjustments", ()) or ():
        if crops(getattr(layer, "effects", ())):
            return "crop"
    return DEFAULT_FILL_POLICY


def _fit_stage(policy: str, width: int, height: int) -> List[str]:
    """The document's one fill policy, applied to the clip's geometry.

    ``fit`` scales the frame down until it fits and pads the remainder (the
    historical letterbox). ``crop`` scales it up until it covers the canvas and
    centre-crops the overflow, so there is never a band. Both are the
    *document's* policy: a document cannot stage one clip with bars and the
    next one with a crop (D7).

    Cropping here is the policy's cover crop, on the geometry it is given --
    never the clip's own ``crop`` effect, which runs before any resizing.
    """
    if policy == "crop":
        return [
            f"scale={width}:{height}:"
            "force_original_aspect_ratio=increase:flags=bicubic",
            f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2",
        ]
    return [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=bicubic",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
    ]


def _filter_stage(
    effects: Mapping[str, List[Any]],
    canvas: Tuple[int, int],
    fps: float,
) -> List[str]:
    """The declarative filters for one clip, plus the stage's own guarantees.

    A template may legitimately re-declare the frame rate or the canvas
    (``zoompan`` does both), so the stage re-stamps the timeline rate and fits
    the result back to the clip canvas. A geometry effect therefore cannot
    change a clip's duration or the deliverable's size, whatever its template
    does.

    The restore is a *cover* on purpose: a template that crops its own window
    (``shake``, ``whip_pan``, a push-in) shrinks the frame as part of the
    effect, so covering keeps the effect full-frame. It cannot undo the
    document's fill policy, because the clip reaches this stage already staged
    at the canvas by ``_fit_stage``: a canvas-sized frame is a cover's fixed
    point.
    """
    fragments: List[str] = []
    for kind, instances in effects.items():
        spec = EFFECT_REGISTRY.get(kind)
        if spec is None or spec.backend != BACKEND_FILTER:
            continue
        for instance in instances:
            fragments.append(filter_fragment(instance, spec, canvas))
    if not fragments:
        return []
    width, height = canvas
    # The declared rate is a real number, not an integer: 23.976 and 29.97 are
    # deliverable rates (profiles.FPS_CHOICES) and `int()` would silently stamp
    # 23 and 29 on them. fps_literal keeps integer rates byte-identical.
    rate = fps_literal(fps)
    fragments.extend(
        [
            f"settb={timebase_literal(fps)}",
            "setpts=N",
            f"fps={rate}",
            f"scale={width}:{height}:"
            "force_original_aspect_ratio=increase:flags=bicubic",
            f"crop={width}:{height}",
        ]
    )
    return fragments


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
    preset: Optional[Effect] = None,
) -> Tuple[Dict[str, List[Any]], Dict[str, str]]:
    """Adjustment layers first (track ascending), then the clip's own effects.

    Colour and blur stack, because they are point-wise and order is meaningful.
    Geometry (``crop``) and the compositing transform may not stack: two crop
    rects or two scales have no declared meaning here, so the compiler refuses
    instead of inventing one. The returned map is the mechanism actually chosen
    for each kind, which is the fallback when chromium was redirected.

    ``preset`` is the single effect a ``preset_ref`` synthesized. It is the first
    of the clip's own effects and the clip's declared ones follow it, so a look
    is a starting point the rest of the chain grades over, never a replacement.
    """
    merged: List[Tuple[str, Any]] = []
    ordered = sorted(enumerate(timeline.adjustments), key=lambda item: item[1].track)
    for index, layer in ordered:
        for position, effect in enumerate(layer.effects):
            merged.append((f"adjustments[{index}].effects[{position}]", effect))
    if preset is not None:
        merged.append((f"{where}.preset_ref", preset))
    for position, effect in enumerate(clip.effects):
        scope = f"{where}.effects[{position}]"
        if routed.get(scope, {}).get("realized_by") == "generator":
            continue
        merged.append((scope, effect))

    by_kind: Dict[str, List[Any]] = {}
    chosen: Dict[str, str] = {}
    for scope, effect in merged:
        spec = EFFECT_REGISTRY.get(effect.kind)
        if spec is not None and spec.backend == BACKEND_FILTER and effect.keyframes:
            raise CompileError(
                "EFFECT_NOT_ANIMATABLE",
                f"{effect.kind!r} is rendered by one static filter template and "
                f"does not animate; keyframes on {sorted(effect.keyframes)} have "
                "no emitter",
                scope,
            )
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

    Three block kinds, in the order the document declares them:

    * ``filters`` — appends to the current chain;
    * ``fanout``  — ``split``, one branch per segment, ``concat`` back (reverse
      and freeze are linear; a freeze or a multi-segment ramp fans out);
    * ``graph``   — a stage that needs more than one stream (a mask against a
      generated canvas, a freeze against a ``split``): its own labelled lines,
      starting from ``input`` and ending at ``output``.

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

        if kind == "graph":
            # Flush the pending chain into the label the stage reads, then take
            # the label it produced as the head of the next chain.
            input_label = str(block["input"])
            output_label = str(block["output"])
            lines.append(label + ",".join(pending or ["null"]) + input_label)
            lines.extend(str(line) for line in block["lines"])
            label = output_label
            pending = []
            serial += 1
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
    fps: float,
    spec_plan: Optional[Mapping[str, Any]] = None,
    normalize_clock: bool = False,
    fill: str = "fit",
) -> Dict[str, Any]:
    duration = clip.timeline_duration
    generator = clip.generator_declaration
    generator_alpha = bool(
        isinstance(generator, Mapping) and generator.get("alpha") is True
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
        # The rect is in source geometry: it is cut before anything is resized,
        # so it is a real crop of the frame and not a slice of a scaled one
        # (D8). The fit that follows is the document's own policy.
        geometry.append(
            _crop_fragment(
                crop[0], width=scaled_width, height=scaled_height, fps=fps
            )
        )
    geometry.extend(_fit_stage(fill, scaled_width, scaled_height))
    # The composed spec runs as its own set of stages. They come before the
    # historical per-kind effects so the spec's transform is a real first stage,
    # while a document that never declares a spec keeps the exact graph it had.
    # A stage that needs more than one stream (mask, freeze) enters the block
    # list as a labelled sub-graph instead of one comma-joined chain.
    spec_blocks: List[Dict[str, Any]] = (
        [] if spec_plan is None else _effect_spec_blocks(spec_plan, index)
    )
    tail: List[str] = []
    for ordinal, color in enumerate(effects.get("color") or []):
        tail.append(_color_fragment(index, ordinal, color, fps, duration))
    for blur in effects.get("blur") or []:
        tail.append(_blur_fragment(blur))
    settled = _filter_stage(effects, (scaled_width, scaled_height), fps)
    tail.extend(settled)
    if spec_blocks:
        blocks.append({"kind": "filters", "filters": geometry})
        blocks.extend(spec_blocks)
        blocks.append({"kind": "filters", "filters": tail})
    else:
        blocks.append({"kind": "filters", "filters": geometry + tail})

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

    # The clip is staged at the canvas by ``_fit_stage`` (one policy, the
    # document's); ``setsar`` stays the last word so a filter template's own
    # scaling cannot leave a pixel-aspect behind, and the clock is normalised
    # when a join needs it.
    canvas: List[str] = ["setsar=1"]
    if normalize_clock:
        # A dissolve needs both links on the same clock. ``fps`` fixes the frame
        # rate and ``settb`` the timebase: a 29.97 source and a 30 fps document
        # (or two sources cut from different files) then meet xfade without the
        # "timebase do not match" refusal. Documents without a dissolve keep
        # their byte-identical graph.
        canvas += [f"fps={fps_literal(fps)}", f"settb={timebase_literal(fps)}"]
    chains: List[str] = []
    if transform is None:
        clip_blocks = blocks + [
            {"kind": "filters", "filters": canvas + [f"format={final_format}"] + fades}
        ]
        chains = _render_blocks(index, f"[{index}:v]", clip_blocks, f"c{index}")
    else:
        chains = _render_blocks(index, f"[{index}:v]", blocks, f"fg{index}")
        chains.append(
            f"color=c=black:s={width}x{height}:r={fps_literal(fps)}:"
            f"d={_num(duration)}[bg{index}]"
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
        "effect_spec": (
            None
            if spec_plan is None
            else {
                "id": spec_plan.get("id"),
                "cost": spec_plan.get("cost"),
                "budget": spec_plan.get("budget"),
                "primitives": list(spec_plan.get("primitives") or []),
                "filters": str(spec_plan.get("chain") or ""),
                "multi_input": spec_plan.get("chain") is None,
                "stages": len(spec_plan.get("stages") or []),
            }
        ),
        "generator": (
            None
            if not isinstance(generator, Mapping)
            else {
                "template": generator.get("template"),
                "engine": generator.get("engine"),
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
        # A sliced segment keeps a fade only on an edge that is also an edge of
        # the original segment; ``False`` marks a chunk boundary, where the
        # neighbour continues the same sound.
        if getattr(clip, "fade_in", None) is not False:
            filters.append(f"afade=t=in:st=0.000000:d={_num(fade)}")
        if getattr(clip, "fade_out", None) is not False:
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

    def declare_span(clip: Any, start: float, seconds: float) -> int:
        """One input for a piece of a source, from ``start`` for ``seconds``."""
        nonlocal cursor
        index = cursor
        cursor += 1
        input_args.extend(
            [
                "-ss",
                _num(start),
                "-t",
                _num(seconds),
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
    base_clips = [clip for clip in timeline.clips if int(clip.layer) == 0]
    joins = _positions_with_joins(
        [(index, clip) for index, clip in enumerate(base_clips)],
        timeline.crossfade_joins(),
    )
    # A dissolve shortens the picture, so the audio bus is shortened on the same
    # joins. ``join_cuts`` is that boundary set in the *declared* (uncut) audio
    # clock: (uncut start of the join, seconds removed there).
    join_cuts: List[Tuple[float, float]] = []
    uncut_cursor = 0.0
    for position, clip in enumerate(base_clips):
        if position in joins:
            join_cuts.append((uncut_cursor, float(joins[position])))
        uncut_cursor += max(0.0, float(clip.timeline_duration))

    def shortened_at(seconds_in: float) -> float:
        """Uncut position -> position on the shortened timeline.

        A piece that begins *on* a join begins where that join's crossfade
        begins, so the join at the piece's own start counts too.
        """
        removed = sum(
            length for start, length in join_cuts if start <= seconds_in + 1e-9
        )
        return seconds_in - removed

    if joins and dialogue_labels and len(dialogue_labels) != len(base_clips):
        raise CompileError(
            "TRANSITION_AUDIO_UNSUPPORTED",
            f"a crossfade needs one dialogue segment per base clip "
            f"({len(dialogue_labels)} dialogue, {len(base_clips)} base): the sound "
            "would shorten on different boundaries than the picture",
            "audio",
        )
    if joins and dialogue_labels and len(dialogue_labels) == len(base_clips):
        tolerance = (1.0 / float(timeline.fps or 1)) + 1e-6
        for position, (video_clip, audio_clip) in enumerate(zip(base_clips, dialogue)):
            if (
                abs(
                    float(video_clip.timeline_duration)
                    - (float(audio_clip.dst_out) - float(audio_clip.dst_in))
                )
                > tolerance
            ):
                raise CompileError(
                    "TRANSITION_AUDIO_UNSUPPORTED",
                    f"base clip {position} lasts "
                    f"{float(video_clip.timeline_duration):.4f} s but its dialogue "
                    f"lasts {(float(audio_clip.dst_out) - float(audio_clip.dst_in)):.4f} s: the runs "
                    "would shorten on different boundaries",
                    "audio",
                )
    if len(dialogue_labels) == 1:
        dialogue_label: Optional[str] = dialogue_labels[0]
    elif dialogue_labels and joins:
        runs = _partition_runs(dialogue_labels, joins)
        labels: List[Tuple[str, int]] = []
        for run_index, run in enumerate(runs):
            if len(run) == 1:
                labels.append((dialogue_labels[run[0]], run[0]))
                continue
            inputs = "".join(f"[{dialogue_labels[position]}]" for position in run)
            label = f"arun{run_index}"
            chains.append(f"{inputs}concat=n={len(run)}:v=0:a=1[{label}]")
            labels.append((label, run[0]))
        dialogue_label = labels[0][0]
        for run_index in range(1, len(labels)):
            next_label, first_position = labels[run_index]
            seconds = joins[first_position]
            out = f"axf{run_index}"
            chains.append(
                f"[{dialogue_label}][{next_label}]acrossfade=d={_num(seconds)}:"
                f"c1=tri:c2=tri[{out}]"
            )
            dialogue_label = out
    elif dialogue_labels:
        inputs = "".join(f"[{label}]" for label in dialogue_labels)
        chains.append(f"{inputs}concat=n={len(dialogue_labels)}:v=0:a=1[dialog]")
        dialogue_label = "dialog"
    else:
        dialogue_label = None

    music_labels: List[str] = []
    for position, clip in enumerate(music):
        time_filters = _audio_time_filters(timeline, clip)
        cuts = [
            (start, length)
            for start, length in join_cuts
            if clip.dst_in < start < clip.dst_out - 1e-9
        ]
        if cuts and time_filters:
            raise CompileError(
                "TRANSITION_AUDIO_UNSUPPORTED",
                "the music bed is retimed (reverse/ramp) and a crossfade cuts it "
                "at a join: the two transforms have no declared order. Declare the "
                "bed without a speed/reverse, or drop the dissolve",
                "audio",
            )
        bounds = [float(clip.dst_in)] + [start for start, _ in cuts] + [
            float(clip.dst_out)
        ]
        pieces: List[Tuple[float, float, float]] = []
        for index in range(len(bounds) - 1):
            lower, upper = bounds[index], bounds[index + 1]
            if upper - lower <= 1e-9:
                continue
            source_start = float(clip.src_in) + (lower - float(clip.dst_in))
            pieces.append((source_start, upper - lower, shortened_at(lower)))
        if not pieces:
            continue
        for piece_index in range(1, len(pieces)):
            dissolve = cuts[piece_index - 1][1]
            if min(pieces[piece_index - 1][1], pieces[piece_index][1]) < dissolve - 1e-9:
                raise CompileError(
                    "TRANSITION_AUDIO_UNSUPPORTED",
                    "the music bed starts or ends inside a dissolve "
                    f"({dissolve:.3f}s at {cuts[piece_index - 1][0]:.3f}s): the "
                    "piece is shorter than the crossfade, so the bed would be cut "
                    "twice. Move the bed's edge off the join",
                    "audio",
                )
        labels: List[str] = []
        for piece_index, (source_start, seconds, start_on_timeline) in enumerate(
            pieces
        ):
            label = f"mus{position}_{piece_index}"
            input_index = declare_span(clip, source_start, seconds)
            filters = [f"atrim=start=0.000000:end={_num(seconds)}", "asetpts=PTS-STARTPTS"]
            filters.append(
                f"aformat=sample_rates={AUDIO_RATE}:channel_layouts=stereo"
            )
            filters.extend(time_filters)
            if clip.gain_db != 0.0:
                filters.append(f"volume={float(clip.gain_db):.3f}dB")
            chains.append(f"[{input_index}:a]{','.join(filters)}[{label}]")
            labels.append(label)
        current = labels[0]
        for piece_index in range(1, len(labels)):
            seconds = cuts[piece_index - 1][1]
            out = f"musx{position}_{piece_index}"
            chains.append(
                f"[{current}][{labels[piece_index]}]acrossfade=d={_num(seconds)}:"
                f"c1=tri:c2=tri[{out}]"
            )
            current = out
        label = f"mus{position}"
        start_ms = int(round(max(0.0, pieces[0][2]) * 1000.0))
        # ``adelay`` is always written, including 0: the music bed's placement is
        # part of the bus's shape, and a zero delay is still a declaration.
        chains.append(f"[{current}]adelay={start_ms}|{start_ms}[{label}]")
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
    # `aformat` prima di `apad`: dopo un `amix` lo stream non ha formato ne'
    # durata nota, e `apad=whole_dur` non aggiunge silenzio. Misurato sul caso
    # minimo: 4,019 s su 6 richiesti senza il formato, 6,000 s con.
    chains.append(
        f"[aout_mix]aformat=sample_fmts=fltp:sample_rates=48000:"
        f"channel_layouts=stereo,"
        f"apad=whole_dur={_num(duration)},"
        f"atrim=start=0.000000:end={_num(duration)},"
        "asetpts=PTS-STARTPTS[aout]"
    )
    return chains, input_args, "aout"


# --------------------------------------------------------------- compiling


# --------------------------------------------------------------- text layers


def _text_font_path(
    layer: Any,
    where: str,
    font_files: Optional[Mapping[str, Any]],
) -> Path:
    """The work's font file for ``style.family``; never a system fallback.

    Resolution order: an explicit ``font_files`` pin (family -> absolute path)
    wins, then the font shipped in ``assets/fonts``. Anything else is
    ``FONT_MISSING``: drawtext with no ``fontfile`` silently picks a system
    face, which is exactly the substitution the skill promises never to make.
    """
    family = str(layer.style.family).strip()
    pinned = None
    for name, value in (font_files or {}).items():
        if str(name).strip().casefold() == family.casefold():
            pinned = str(value)
            break
    if pinned:
        path = Path(pinned).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise CompileError(
                "FONT_MISSING",
                f"text layer font {family!r} is pinned to {pinned!r}: an "
                "absolute path to an existing file is required, with no "
                "system-font fallback",
                f"{where}.style.family",
            )
        return path
    path = bundled_font(family)
    if path is None or not path.is_file():
        raise CompileError(
            "FONT_MISSING",
            f"text layer font {family!r} is not a file of the work: ship it in "
            "assets/fonts or pin it with font_files={family: absolute path}; "
            "the compiler never falls back to a system font",
            f"{where}.style.family",
        )
    return path


def _text_drawtext(
    layer: Any,
    index: int,
    *,
    width: int,
    height: int,
    font_path: Path,
) -> Tuple[str, Dict[str, Any]]:
    """One ``drawtext`` for a text layer, plus the record reported to callers.

    The style's normalised position is the anchor: ``x`` follows the declared
    alignment, ``y`` is the vertical centre. ``size`` and ``stroke`` are pixels.
    """
    where = f"text_layers[{index}]"
    style = layer.style
    content = str(layer.text)
    if "\n" in content or "\r" in content:
        raise CompileError(
            "PARAM_NOT_RENDERED",
            "text_layers[].text with multiple lines is not executed: drawtext "
            "paints one line and the compiler does not truncate the text in "
            "silence",
            f"{where}.text",
        )
    if layer.keyframes:
        name = sorted(layer.keyframes)[0]
        raise CompileError(
            "PARAM_NOT_RENDERED",
            f"{where}.keyframes.{name}: the text emitter is one static drawtext "
            "(declared tracks: opacity, position_x, position_y); animating a "
            "text layer is not implemented, so the field is refused instead of "
            "being ignored",
            f"{where}.keyframes.{name}",
        )
    align = str(style.align)
    if align not in {"left", "center", "right"}:
        # Unreachable through a validated document; a guard, not a fallback.
        raise CompileError(
            "PARAM_NOT_RENDERED",
            f"text layer align {align!r} has no drawtext anchor",
            f"{where}.style.align",
        )

    size = max(1, int(round(float(style.size))))
    color = effect_ffmpeg._color_literal(style.color)
    anchor_x = float(style.position[0]) * width
    anchor_y = float(style.position[1]) * height
    if align == "left":
        x_expression = _num(anchor_x)
    elif align == "right":
        x_expression = f"{_num(anchor_x)}-text_w"
    else:
        x_expression = f"{_num(anchor_x)}-text_w/2"
    y_expression = f"{_num(anchor_y)}-text_h/2"

    filters = [
        f"fontfile={effect_ffmpeg._font_argument(font_path)}",
        f"fontcolor={color}",
        f"fontsize={size}",
        "expansion=none",
        f"text={effect_ffmpeg._text_argument(content)}",
        f"x={x_expression}",
        f"y={y_expression}",
        f"enable='between(t,{_num(layer.time_in)},{_num(layer.time_out)})'",
    ]
    stroke = int(round(float(style.stroke)))
    if stroke > 0:
        filters.insert(3, f"borderw={stroke}")
        filters.insert(4, "bordercolor=black")
    record = {
        "text": content,
        "in": float(layer.time_in),
        "out": float(layer.time_out),
        "family": str(style.family),
        "font": str(font_path),
        "size": size,
        "color": str(style.color),
        "stroke": stroke,
        "align": align,
        "position": [float(style.position[0]), float(style.position[1])],
    }
    return "drawtext=" + ":".join(filters), record


def _text_stage(
    timeline: Timeline3,
    base_label: str,
    font_files: Optional[Mapping[str, Any]],
) -> Tuple[List[str], List[Dict[str, Any]], str]:
    """Draw every text layer over the finished picture.

    Called after the concat, the crossfades and the overlay loop: the text
    belongs to the composed timeline, not to a single clip, so a per-clip effect
    or a transition cannot move it or cut it.
    """
    chains: List[str] = []
    records: List[Dict[str, Any]] = []
    current = base_label
    for index, layer in enumerate(timeline.text_layers):
        font_path = _text_font_path(layer, f"text_layers[{index}]", font_files)
        fragment, record = _text_drawtext(
            layer,
            index,
            width=timeline.width,
            height=timeline.height,
            font_path=font_path,
        )
        produced = f"tx{index}"
        chains.append(f"[{current}]{fragment}[{produced}]")
        record["where"] = f"text_layers[{index}]"
        records.append(record)
        current = produced
    return chains, records, current


def _refuse_unsupported(timeline: Timeline3, audio: str) -> None:
    if timeline.captions:
        raise CompileError(
            "UNSUPPORTED_EFFECT",
            "caption tracks are validated and serialised only: a CaptionTrack "
            "declares no style, so painting it would mean inventing a font, a "
            "size and a position; declare the cues as text_layers instead",
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


def _preset_effects(timeline: Timeline3) -> Dict[int, Effect]:
    """One effect per clip that names a preset, materialized from the registry.

    ``presets.effect_for`` is the whole synthesis: the preset's own effect kind
    with its base values plus the clip's overrides, so the graph never invents a
    value the registry did not declare. Materializing here, before routing, keeps
    ``compile_timeline`` pure and puts the preset effect through the same backend
    rule as a declared one; the clip's own effects are emitted after it.

    A clip that already declares an effect of that kind is refused with
    ``PRESET_EFFECT_CONFLICT`` instead of rendering the kind twice: two instances
    have no declared order, and silently duplicating the look would hide the
    document's mistake. An ``EDLError`` from the registry is propagated as the
    same code, so a document that slipped past ``validate`` still never reaches
    the encoder.
    """
    if timeline.version != PRESET_APPLIED_VERSION:
        return {}
    if all(clip.preset_ref is None for clip in timeline.clips):
        return {}

    from . import presets  # noqa: PLC0415 - the registry is read, never imported

    applied: Dict[int, Effect] = {}
    for index, clip in enumerate(timeline.clips):
        if clip.preset_ref is None or not clip.preset_ref.strip():
            continue
        where = f"clips[{index}]"
        try:
            declared = presets.effect_for(clip.preset_ref, clip.preset_overrides())
        except EDLError as exc:
            spot = f"{where}.{exc.where}" if exc.where else where
            raise CompileError(exc.code, exc.detail, spot) from exc
        kind = str(declared["kind"])
        for position, effect in enumerate(clip.effects):
            if effect.kind != kind:
                continue
            raise CompileError(
                "PRESET_EFFECT_CONFLICT",
                f"preset {clip.preset_ref!r} instantiates the {kind!r} effect and "
                f"{where}.effects[{position}] declares its own {kind!r}: the "
                "preset applies first and the clip's effects after it, so the "
                "clip would carry that kind twice",
                f"{where}.preset_ref",
            )
        applied[index] = Effect(kind=kind, params=dict(declared["params"]))
    return applied


def _route_effects(
    timeline: Timeline3,
    settings: Mapping[str, Any],
    preset_effects: Optional[Mapping[int, Effect]] = None,
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

    ``preset_effects`` carries the effect each preset clip synthesized, keyed by
    clip index. Each is routed under ``clips[i].preset_ref`` so the report names
    the mechanism it really uses, exactly like a declared effect.
    """
    extras = preset_effects or {}
    scopes: List[Tuple[str, Any, Any]] = []
    for index, layer in enumerate(timeline.adjustments):
        for position, effect in enumerate(layer.effects):
            scopes.append((f"adjustments[{index}].effects[{position}]", effect, None))
    for index, clip in enumerate(timeline.clips):
        if index in extras:
            scopes.append((f"clips[{index}].preset_ref", extras[index], clip))
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
        generator = None if clip is None else clip.generator_declaration
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


def _positions_with_joins(
    base_clips: List[Tuple[int, Any]], joins: List[Tuple[int, float]]
) -> Dict[int, float]:
    """Base position -> dissolve seconds, refusing a join that has no neighbour."""
    by_position = {int(position): float(seconds) for position, seconds in joins}
    for position in by_position:
        if position <= 0 or position >= len(base_clips):
            raise CompileError(
                "TRANSITION_ORPHAN",
                f"the dissolve at base position {position} has no clip to dissolve "
                f"from or into ({len(base_clips)} base clip(s))",
                "clips",
            )
    return by_position


def _partition_runs(
    items: List[Any], joins: Dict[int, float]
) -> List[List[int]]:
    """Group positions into runs of hard cuts; each dissolve starts a new run."""
    runs: List[List[int]] = [[0]] if items else []
    for position in range(1, len(items)):
        if position in joins:
            runs.append([position])
        else:
            runs[-1].append(position)
    return runs


def _crossfade_base(
    base_clips: List[Tuple[int, Any]],
    plans: Dict[int, Dict[str, Any]],
    joins: Dict[int, float],
    chains: List[str],
    styles: Optional[Dict[int, str]] = None,
) -> str:
    """Chain the base layer, dissolving the runs where the document says so.

    ``xfade`` takes two inputs and its ``offset`` is a time inside the *first*
    one, so the chain is built forward: every run is concatenated on its own, then
    the runs are dissolved pairwise at ``accumulated - dissolve``. The accumulated
    time is the shortened one, which is exactly what the document's duration says.
    """
    runs = _partition_runs(base_clips, joins)
    labels: List[Tuple[str, float, int]] = []
    for run_index, run in enumerate(runs):
        first_position = run[0]
        if len(run) == 1:
            index, clip = base_clips[first_position]
            labels.append((f"c{index}", float(clip.timeline_duration), first_position))
            continue
        inputs = "".join(f"[c{base_clips[position][0]}]" for position in run)
        label = f"run{run_index}"
        chains.append(f"{inputs}concat=n={len(run)}:v=1:a=0[{label}]")
        media = sum(float(base_clips[position][1].timeline_duration) for position in run)
        labels.append((label, media, first_position))

    label, accumulated, _ = labels[0]
    for run_index in range(1, len(labels)):
        next_label, media, first_position = labels[run_index]
        seconds = joins[first_position]
        offset = accumulated - seconds
        if offset < -1e-6:
            raise CompileError(
                "TRANSITION_TOO_LONG",
                f"the dissolve at base position {first_position} ({seconds} s) does "
                f"not fit the {accumulated:.4f} s before it",
                "clips",
            )
        out = f"xf{run_index}"
        style = (styles or {}).get(first_position, "fade")
        recipe = TRANSITION_RECIPES.get(style)
        if recipe is None:
            raise CompileError(
                "TRANSITION_UNKNOWN",
                f"unknown dissolve recipe {style!r}; the catalogue has "
                f"{len(TRANSITION_RECIPES)}: {sorted(TRANSITION_RECIPES)}",
                "clips",
            )
        chains.append(
            f"[{label}][{next_label}]xfade=transition={recipe['ffmpeg']}:"
            f"duration={_num(seconds)}:offset={_num(max(0.0, offset))}[{out}]"
        )
        label = out
        accumulated = accumulated + media - seconds
    return label


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
        declaration = clip.generator_declaration
        if declaration is None:
            continue
        where = (
            f"clips[{index}].generator"
            if clip.generator is not None
            else f"clips[{index}].template_ref"
        )
        if clip.generator is not None and not isinstance(clip.generator, Mapping):
            raise CompileError(
                "GENERATOR_SHAPE",
                f"generator must be an object, got {type(clip.generator).__name__}",
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


def _compile_effect_spec(
    effect: Any,
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
    where: str,
    namespace: str = "",
) -> Dict[str, Any]:
    """Resolve a compose spec (or a historical name) into a validated plan.

    ``expand`` is the one owner of history: ``"zoom"`` and a hand-written
    ``transform`` spec reach the bridge through the same normalisation, so a
    legacy name cannot drift from its canonical formula.

    ``namespace`` prefixes the plan's labels and filter instance names, so a
    spec spliced into a timeline keeps its streams distinct from every other
    clip's.
    """
    try:
        normalized = effect_compose.expand(effect)
        return effect_ffmpeg.plan_spec(
            normalized,
            width=width,
            height=height,
            fps=fps,
            duration=duration,
            namespace=namespace,
        )
    except effect_compose.SpecError as exc:
        raise CompileError("SPEC_INVALID", str(exc), where) from exc
    except effect_ffmpeg.RenderSpecError as exc:
        if exc.code == "BLEND_SOURCE_MISSING":
            # The bridge is right: no such source was supplied. In a document
            # that is structural, because one clip carries one source.
            raise CompileError(
                "BLEND_INPUT_UNAVAILABLE",
                exc.message
                + " In un documento una clip ha una sola sorgente: usa "
                "render_effect_spec(extra_inputs=...) per quel blend, oppure "
                "params.source='self'.",
                exc.where or where,
            ) from exc
        raise CompileError(exc.code, exc.message, exc.where or where) from exc


def _effect_spec_blocks(
    spec_plan: Mapping[str, Any], index: int
) -> List[Dict[str, Any]]:
    """Turn a bridge plan into render blocks for one clip.

    A plan whose stages are all single-stream becomes one ``filters`` block, i.e.
    the historical comma-joined chain. A plan with a multi-stream stage (``mask``
    or ``time`` freeze, which synthesize their extra streams) becomes a
    ``graph`` block carrying the stage's own labelled lines. Only a stage that
    needs an input the clip does not have — ``blend`` against a named source —
    is still refused, because a timeline clip has exactly one source.
    """
    if not spec_plan.get("timeline_safe", True):
        sources = list(spec_plan.get("extra_inputs") or [])
        raise CompileError(
            "BLEND_INPUT_UNAVAILABLE",
            "la specifica fonde una seconda sorgente "
            f"({', '.join(sources)}), ma una clip del documento ha una sola "
            "sorgente: usa render_effect_spec(extra_inputs=...) per quel blend, "
            "oppure params.source='self'.",
            f"clips[{index}].effect_spec",
        )
    chain = spec_plan.get("chain")
    if chain is not None:
        return [] if not chain else [{"kind": "filters", "filters": [str(chain)]}]
    blocks: List[Dict[str, Any]] = []
    for stage in spec_plan.get("stages") or []:
        if stage.get("kind") == "graph":
            blocks.append(
                {
                    "kind": "graph",
                    "input": str(stage["input"]),
                    "output": str(stage["output"]),
                    "lines": [str(line) for line in stage.get("lines") or []],
                }
            )
        else:
            filters = [str(item) for item in stage.get("filters") or []]
            if filters:
                blocks.append({"kind": "filters", "filters": filters})
    return blocks


def compile_timeline(
    timeline: Timeline3,
    *,
    audio: str = "refuse",
    settings: Optional[Mapping[str, Any]] = None,
    nested_paths: Optional[Mapping[str, str]] = None,
    generator_paths: Optional[Mapping[str, str]] = None,
    effect_specs: Optional[Mapping[Any, Any]] = None,
    font_files: Optional[Mapping[str, Any]] = None,
    effect_budget: Optional[int] = None,
) -> Dict[str, Any]:
    """Validate, route every effect, and return a compile plan.

    Nested sequences are resolved by :mod:`vx.edl3_nested` and *rendered once*
    by :func:`render`; the resulting ``{virtual_source: path}`` map arrives as
    ``nested_paths``. Generator clips arrive the same way: ``generator_paths``
    maps a clip id to the directory :func:`_materialize_generators` captured, and
    each directory's ``capture.json`` is checked against the declaration. A
    document with either cannot be compiled without that evidence, because a
    compile never runs ffmpeg and never launches a browser.

    ``effect_specs`` maps a clip id to a composed spec (or a historical name):
    each entry goes through :func:`vx.effects.compose.expand` and the
    :mod:`vx.effects.ffmpeg` bridge, and its chain joins that clip's geometry.
    A spec that the bridge refuses (unsupported primitive, budget, curve) fails
    the compile with the bridge's own code instead of being skipped.

    ``font_files`` pins a text layer's family to an absolute font file (the
    work's own). Without a pin the font is the one shipped in ``assets/fonts``;
    a family with no file is ``FONT_MISSING``, never a system fallback. Text is
    drawn after the concat and the overlays, so it belongs to the finished
    timeline rather than to a clip.
    """
    if timeline.version not in EDL3_VERSIONS:
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

    for problem in timeline.validate(effect_budget=effect_budget):
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

    presets_applied = _preset_effects(timeline)
    table, routed, chromium = _route_effects(timeline, resolved, presets_applied)
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

    # One dissolve is enough to make every base link share the document's clock
    # (see ``_clip_plan``); without one the graph stays byte-identical.
    normalize_clock = bool(timeline.crossfade_joins())

    backend = choose_backend(timeline)
    fill = fill_policy(timeline)

    plans: Dict[int, Dict[str, Any]] = {}
    for clip_index, clip in enumerate(timeline.clips):
        clip_where = f"clips[{clip_index}]"
        source = sources.get(clip_index, clip.source)
        if clip.generator_declaration is None and not Path(source).exists():
            raise CompileError(
                "MISSING_SOURCE",
                f"source file not found: {source}",
                f"{clip_where}.source",
            )
        effects, chosen = _effective_effects(
            timeline,
            clip,
            clip_where,
            routed,
            presets_applied.get(clip_index),
        )
        raw_spec = (effect_specs or {}).get(clip.id)
        spec_plan = (
            None
            if raw_spec is None
            else _compile_effect_spec(
                raw_spec,
                width=timeline.width,
                height=timeline.height,
                fps=float(timeline.fps),
                duration=clip.timeline_duration,
                where=f"{clip_where}.effect_spec",
                namespace=f"fx{clip_index}_",
            )
        )
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
            spec_plan,
            normalize_clock=normalize_clock,
            fill=fill,
        )
        if clip_index in presets_applied:
            plans[clip_index]["preset"] = clip.preset_provenance()

    chains: List[str] = []
    for index, _clip in base_clips:
        chains.extend(plans[index]["chains"])
    joins = _positions_with_joins(base_clips, timeline.crossfade_joins())
    if len(base_clips) == 1:
        base_label = f"c{base_clips[0][0]}"
    elif joins:
        base_label = _crossfade_base(
            base_clips, plans, joins, chains, timeline.crossfade_styles()
        )
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

    text_chains, text_records, base_label = _text_stage(
        timeline, base_label, font_files
    )
    chains.extend(text_chains)

    audio_chains, audio_input_args, audio_label = _audio_bus(
        timeline, audio, len(timeline.clips)
    )
    chains.extend(audio_chains)

    input_args: List[str] = []
    for clip_index, clip in enumerate(timeline.clips):
        if clip_index in generators:
            # The capture directory is a numbered image sequence: same input
            # ordinal as the clip index, no -ss, and the timeline's own clock.
            entry = generators[clip_index]
            input_args.extend(
                [
                    "-framerate",
                    fps_literal(timeline.fps),
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

    clips_report = [
        {
            key: value
            for key, value in plans[index].items()
            if key not in {"chains", "compose_fragment"}
        }
        for index in range(len(timeline.clips))
    ]
    for index, entry in generators.items():
        record = entry["capture"].get("font")
        generator_entry = clips_report[index].get("generator")
        if record and isinstance(generator_entry, dict):
            generator_entry["font"] = record

    return {
        "version": timeline.version,
        "backend": backend,
        "audio_policy": audio,
        "fps": float(timeline.fps),
        "fps_text": fps_literal(timeline.fps),
        "fill": fill,
        "duration": float(timeline.duration),
        "color": delivery_colour(timeline.profile_ref),
        "filter_script": ";\n".join(chains) + "\n",
        "input_args": input_args,
        "map": f"[{base_label}]",
        "audio_map": f"[{audio_label}]" if audio_label else None,
        "overlays": len(overlay_clips),
        "text_layers": text_records,
        "clips": clips_report,
        "backends": table,
        "chromium": chromium,
        "generators": [
            {
                "clip": timeline.clips[index].id,
                "index": index,
                "template": entry["capture"].get("template"),
                "engine": entry["capture"].get("engine"),
                "font": entry["capture"].get("font"),
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


def _input_runs_at_declared_rate(plan: Mapping[str, Any]) -> bool:
    """True when every video input already carries the document's clock.

    Compile is pure and never opens a source, so the only place this can be
    answered is here, with ffprobe. A generator's numbered image sequence is
    declared at the timeline's rate by its own ``-framerate`` argument, so it is
    skipped.
    """
    from . import probe as probe_module

    rate = float(plan["fps"])
    args = list(plan["input_args"])
    for index, token in enumerate(args):
        if token != "-i" or index + 1 >= len(args):
            continue
        path = str(args[index + 1])
        if "%" in path:
            continue
        try:
            input_rate = float(probe_module.probe(path).fps)
        except Exception:  # noqa: BLE001 - an unreadable clock is not the declared one
            return False
        if input_rate > 0 and abs(input_rate - rate) > 1e-3:
            return False
    return True


def delivery_colour(profile_ref: Optional[str]) -> Dict[str, Any]:
    """What the document's declared profile means for the delivered stream.

    Pure data, no ffmpeg: the encoder, the pixel format and the stream tags the
    encoder must write. No profile, or an SDR profile, is the byte-identical
    8-bit picture this engine has always produced -- an SDR profile's declared
    10 bit is its graded negative (the ProRes deliverable), never a silent
    up-conversion of the mp4. A BT.2100 transfer is the only declaration that
    changes the pixels to 10 bit, because an HDR stream in 8 bit would be a lie
    about what the file carries.

    A non-SDR transfer reaches here only from a validated document, and
    :func:`vx.profiles.check` refuses such a row below 10 bit, so the 10-bit
    pixel format is never invented for a declaration that did not ask for it.
    """
    if profile_ref is None:
        return dict(SDR_DELIVERY, tags={})
    from . import profiles  # noqa: PLC0415 - the registry is read, never imported

    row = profiles.resolve(profile_ref)
    if row.transfer == "sdr":
        return dict(
            SDR_DELIVERY,
            tags={},
            profile=row.id,
            declared_bit_depth=row.bit_depth,
        )
    hdr = HDR_DELIVERY[row.transfer]
    return {
        "profile": row.id,
        "transfer": row.transfer,
        "bit_depth": HDR_BIT_DEPTH,
        "declared_bit_depth": row.bit_depth,
        "encoder": HDR_ENCODER,
        "pix_fmt": HDR_PIX_FMT,
        "tags": {
            "color_primaries": hdr["color_primaries"],
            "color_trc": hdr["color_trc"],
            "colorspace": hdr["colorspace"],
        },
        "x264_params": hdr["x264_params"],
    }


def _bench_maxrss(stderr: str) -> Optional[int]:
    """The encoder's own peak RSS in KiB, from ffmpeg's ``-benchmark`` line."""
    found = BENCH.findall(stderr or "")
    return int(found[-1]) if found else None


def _encode(
    plan: Mapping[str, Any],
    destination: Path,
    script_dir: Path,
    timeout: Optional[int],
    *,
    benchmark: bool = False,
) -> Tuple[ffmpeg.Proc, Path]:
    script_path = script_dir / "filter_complex.txt"
    script_path.write_text(plan["filter_script"], encoding="utf-8", newline="\n")

    colour = plan["color"]
    args = ["-benchmark"] if benchmark else []
    args.extend(plan["input_args"])
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
    # The deliverable's clock is the document's declared rate, never the source's.
    # Without this option the encoder keeps the input frame rate, so a 24 fps
    # document rendered from a 30 fps source ships at 30 fps. The rate is written
    # as the exact rational: "29.97" would be stamped 2997/100, not 30000/1001.
    args.extend(["-r", fps_literal(plan["fps"])])
    if not _input_runs_at_declared_rate(plan):
        # Resampling to another clock lets the cfr encoder emit up to two frames
        # past the source's last one, so the tail is bounded by the duration the
        # document declares. A document already at its own clock is untouched:
        # its picture length is its own business (a ramp or a freeze ends where
        # the plan says, and only the picture is retimed).
        args.extend(["-t", filter_number(float(plan["duration"]))])
    args.extend(
        [
            "-sn",
            "-dn",
            "-c:v",
            colour["encoder"],
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            colour["pix_fmt"],
        ]
    )
    for flag, value in colour["tags"].items():
        args.extend([f"-{flag}", value])
    if colour["x264_params"]:
        args.extend(["-x264-params", colour["x264_params"]])
    args.append(str(destination))
    # -benchmark prints its own ``bench: maxrss=...KiB`` line, and this ffmpeg
    # build emits it at info level only, so asking for it also opens the log.
    proc = ffmpeg.run_ff(
        args,
        loglevel="info" if benchmark else "error",
        timeout=timeout,
        step="render",
    )
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
    font_files: Optional[Mapping[str, Any]] = None,
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
                font_files=font_files,
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

    clips = [clip for clip in timeline.clips if clip.generator_declaration is not None]
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
        declaration = clip.generator_declaration
        if declaration is None:
            continue
        where = (
            f"clips[{index}].generator"
            if clip.generator is not None
            else f"clips[{index}].template_ref"
        )
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
            "font": result.get("font"),
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


#: One filtergraph grows with the timeline: a 60 s master at 1080x1920 reached
#: 5.848 MB of working set and over eleven minutes without writing a frame
#: (D-12, measured on the profile test of 28/10). The chunk path keeps every
#: graph small: 1.085-1.517 MB per profile in F26-T06. Above this many seconds a
#: render takes the chunk path on its own, so a long document cannot fall into
#: the unbounded one by forgetfulness.
AUTO_CHUNK_ABOVE_SEC = 120.0
AUTO_CHUNK_SECONDS = 20.0


def auto_chunks(duration: float) -> Optional[int]:
    """The chunk count a document needs, or ``None`` when one graph is enough."""
    seconds = float(duration or 0.0)
    if seconds <= AUTO_CHUNK_ABOVE_SEC:
        return None
    return max(2, int(math.ceil(seconds / AUTO_CHUNK_SECONDS)))


def loudness_gate(
    measured_lufs: Any,
    measured_true_peak: Any,
    *,
    target_lufs: float = LOUDNESS_TARGET_LUFS,
    tolerance_lu: float = LOUDNESS_TOLERANCE_LU,
    true_peak_ceiling: float = TRUE_PEAK_CEILING_DBTP,
) -> Dict[str, Any]:
    """Judge the master's loudness against the delivery spec.

    Measured values only: a caller cannot pass a declared target and have the
    gate agree. ``ok`` is ``True`` only when the integrated loudness is inside
    ``target ± tolerance`` *and* the true peak is at or below the ceiling. A
    master that fails is not deliverable, and every value that failed travels
    with the verdict so the report can say why (D20).
    """
    goal = float(target_lufs)
    tolerance = float(tolerance_lu)
    ceiling = float(true_peak_ceiling)
    measured_i = None if measured_lufs is None else float(measured_lufs)
    measured_tp = None if measured_true_peak is None else float(measured_true_peak)

    failures: List[str] = []
    parts: List[str] = []
    if measured_i is None:
        failures.append("loudness_missing")
        parts.append("integrated loudness was not measurable")
    else:
        delta = abs(measured_i - goal)
        if delta > tolerance + 1e-9:
            failures.append("loudness")
        parts.append(
            f"measured {measured_i:.2f} LUFS (target {goal:.1f} +/- "
            f"{tolerance:.1f}, delta {delta:.2f} LU)"
        )
    if measured_tp is None:
        failures.append("true_peak_missing")
        parts.append("true peak was not measurable")
    else:
        if measured_tp > ceiling + 1e-9:
            failures.append("true_peak")
        parts.append(
            f"true peak {measured_tp:.2f} dBTP (ceiling {ceiling:.1f})"
        )
    ok = not failures
    return {
        "ok": ok,
        "deliverable": ok,
        "measured": {"lufs": measured_i, "true_peak": measured_tp},
        "target": {
            "lufs": goal,
            "tolerance_lu": tolerance,
            "true_peak": ceiling,
        },
        "failures": failures,
        "detail": "; ".join(parts)
        + (
            ""
            if ok
            else ": the master is outside the delivery spec and is not deliverable"
        ),
    }


def _audit_master(destination: Path, has_audio: bool) -> Dict[str, Any]:
    """Measure the encoded master and run the gate on the measurement."""
    if not has_audio:
        return {
            "ok": True,
            "deliverable": True,
            "skipped": "the document declares no audio bus",
            "measured": {"lufs": None, "true_peak": None},
            "target": {
                "lufs": LOUDNESS_TARGET_LUFS,
                "tolerance_lu": LOUDNESS_TOLERANCE_LU,
                "true_peak": TRUE_PEAK_CEILING_DBTP,
            },
            "failures": [],
            "detail": "no audio bus: loudness not applicable",
        }
    from . import qc  # noqa: PLC0415 - the measurement only

    measurement = qc.lufs_of(destination)
    return loudness_gate(
        measurement.get("input_i"), measurement.get("input_tp")
    )


def render(
    timeline: Timeline3,
    output: Any,
    *,
    workdir: Optional[Any] = None,
    timeout: Optional[int] = None,
    audio: str = "refuse",
    settings: Optional[Mapping[str, Any]] = None,
    chunks: Optional[int] = None,
    benchmark: bool = False,
    jobs: int = 1,
    effect_specs: Optional[Mapping[Any, Any]] = None,
    font_files: Optional[Mapping[str, Any]] = None,
    effect_budget: Optional[int] = None,
    live: bool = False,
) -> Dict[str, Any]:
    """Render ``timeline`` to ``output`` and return the compile/run report.

    ``chunks`` selects the resumable path: at least two chunks are rendered and
    recorded by :mod:`vx.edl3_chunks`, which skips a chunk whose recorded inputs
    still match. With ``None`` a document longer than :data:`AUTO_CHUNK_ABOVE_SEC`
    takes that path on its own (``chunks_auto`` in the report), because one graph
    for a long timeline grows without bound; a short document keeps the single
    ffmpeg process this engine has always run, and its graph is untouched.
    ``benchmark`` asks ffmpeg for its own ``-benchmark`` line, reported under
    ``benchmark``.

    ``live`` forces the chunk path and publishes each finished piece into
    ``<workdir>/live/`` with a ``vx.live/1`` manifest, so a player can watch the
    picture that has really been encoded. The chunk count is chosen for a piece
    every :data:`vx.edl3_chunks.LIVE_CHUNK_SECONDS` seconds (2..40 pieces, never
    more pieces than layer-0 clips) unless the caller passed ``chunks``.

    ``effect_specs`` is forwarded to :func:`compile_timeline`; the chunk path
    refuses it instead of dropping the per-clip specs. ``font_files`` is
    forwarded the same way.

    A document with ``text_layers`` keeps the single graph: a chunk's
    sub-document carries no text layers, so the chunk path would paint the
    picture without them. An explicit ``chunks``/``live`` request is refused
    with ``CHUNK_UNSUPPORTED`` instead of silently dropping the text; the
    automatic path simply does not chunk, which only costs time.
    """
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
    auto_chunked = False
    has_text = bool(getattr(timeline, "text_layers", None))
    if live:
        from . import edl3_chunks  # noqa: PLC0415 - the resumable path only

        base_clips = sum(1 for clip in timeline.clips if int(clip.layer) == 0)
        if chunks is None or int(chunks) < 2:
            chunks = edl3_chunks.live_chunks(
                float(getattr(timeline, "duration", 0.0) or 0.0),
                clips=base_clips,
            )
    elif chunks is None and not has_text:
        decided = auto_chunks(float(getattr(timeline, "duration", 0.0) or 0.0))
        if decided is not None:
            chunks = decided
            auto_chunked = True
    if chunks is not None and int(chunks) > 1:
        if has_text:
            raise CompileError(
                "CHUNK_UNSUPPORTED",
                "text_layers non è collegato al percorso a blocchi: il "
                "sottodocumento di un blocco non porta i livelli di testo, "
                "quindi il render rifiuta invece di perdere il testo. Usa un "
                "grafo unico (chunks=None).",
                "text_layers",
            )
        if effect_specs:
            raise CompileError(
                "CHUNK_UNSUPPORTED",
                "effect_specs non è collegato al percorso a blocchi: il render "
                "rifiuta invece di scartare le specifiche per clip.",
                "effect_specs",
            )
        from . import edl3_chunks  # noqa: PLC0415 - the resumable path only

        report = edl3_chunks.render_chunked(
            timeline,
            destination,
            workdir=script_dir,
            timeout=timeout,
            audio=audio,
            settings=settings,
            chunks=int(chunks),
            benchmark=benchmark,
            jobs=int(jobs),
            live=bool(live),
            effect_budget=effect_budget,
        )
        report["settings_source"] = settings_source
        report["chunks_auto"] = auto_chunked
        report["jobs"] = max(1, int(jobs))
        audit = _audit_master(
            destination, bool(timeline.audio) and audio != "drop"
        )
        report["loudness"] = audit
        report["deliverable"] = bool(audit["deliverable"])
        if not audit["ok"]:
            report.setdefault("warnings", []).append(
                "loudness gate failed: " + audit["detail"]
            )
        return report
    nested = _render_nested(timeline, script_dir, timeout, audio, settings, font_files)
    generators = _materialize_generators(timeline, script_dir, probe)
    plan = compile_timeline(
        timeline,
        audio=audio,
        settings=settings,
        nested_paths=nested,
        generator_paths=generators,
        effect_specs=effect_specs,
        font_files=font_files,
        effect_budget=effect_budget,
    )
    proc, script_path = _encode(
        plan, destination, script_dir, timeout, benchmark=benchmark
    )

    report: Dict[str, Any] = {
        "version": plan["version"],
        "backend": plan["backend"],
        "audio_policy": plan["audio_policy"],
        "output": str(destination.resolve()),
        "output_size": destination.stat().st_size,
        "duration": timeline.duration,
        "fps": timeline.fps,
        "fps_text": fps_literal(timeline.fps),
        "fill": fill_policy(timeline),
        "size": [timeline.width, timeline.height],
        "color": plan["color"],
        "filter_script": plan["filter_script"],
        "filter_script_path": str(script_path.resolve()),
        "clips": plan["clips"],
        "map": plan["map"],
        "audio_map": plan["audio_map"],
        "overlays": plan["overlays"],
        "text_layers": plan["text_layers"],
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
        "benchmark": {"encode_kib": _bench_maxrss(proc.stderr)},
    }
    audit = _audit_master(destination, plan["audio_map"] is not None)
    report["loudness"] = audit
    report["deliverable"] = bool(audit["deliverable"])
    if not audit["ok"]:
        report["warnings"] = list(report["warnings"]) + [
            "loudness gate failed: " + audit["detail"]
        ]
    return report


def render_effect_spec(
    effect: Any,
    source: Any,
    output: Any,
    *,
    width: int,
    height: int,
    fps: float = 30.0,
    duration: Optional[float] = None,
    workdir: Optional[Any] = None,
    timeout: Optional[int] = None,
    budget: float = effect_ffmpeg.DEFAULT_BUDGET,
    extra_inputs: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Render one clip through the composed-effect bridge.

    ``effect`` is a spec mapping or a historical name (``"zoom"``): both are
    normalised by :func:`vx.effects.compose.expand`, so the legacy name and the
    hand-written formula produce the same graph. ``extra_inputs`` maps a blend
    source name to a path; the bridge assigns it the next ffmpeg input label.

    The report carries ``cost``, ``estimate`` and ``plan_choice`` so
    ``/api/state`` can show the estimate and the full/fast buttons without
    re-deriving them. A refused spec raises, and no output file is written.
    """
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    script_dir = Path(workdir) if workdir is not None else destination.parent
    script_dir.mkdir(parents=True, exist_ok=True)

    extras = dict(extra_inputs or {})
    labels = {name: f"[{index + 1}:v]" for index, name in enumerate(extras)}
    normalized = effect_compose.expand(effect)
    try:
        plan = effect_ffmpeg.plan_spec(
            normalized,
            width=width,
            height=height,
            fps=fps,
            duration=duration,
            budget=budget,
            extra_sources=labels,
        )
    except effect_ffmpeg.RenderSpecError as exc:
        raise CompileError(exc.code, exc.message, exc.where) from exc

    script_path = script_dir / "filter_complex.txt"
    script_path.write_text(plan["filter_complex"], encoding="utf-8", newline="\n")
    args: List[Any] = ["-i", str(source)]
    for path in extras.values():
        args.extend(["-i", str(path)])
    args.extend(["-filter_complex_script", str(script_path), "-map", "[vout]"])
    args.extend(["-t", filter_number(float(plan["duration"]))])
    args.extend(["-r", filter_number(float(plan["fps"]))])
    args.extend(
        [
            "-an",
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
    proc = ffmpeg.run_ff(args, timeout=timeout, loglevel="error", step="render")
    if not proc.ok:
        raise CompileError("RENDER_FAILED", proc.tail(), str(destination))
    if not destination.exists() or destination.stat().st_size == 0:
        raise CompileError(
            "RENDER_FAILED",
            "ffmpeg ha dichiarato successo ma non ha scritto il file",
            str(destination),
        )

    from . import probe as probe_module  # noqa: PLC0415 - render only

    try:
        probed = probe_module.probe(str(destination))
        measured = {
            "duration": float(probed.duration),
            "fps": float(probed.fps),
            "size": [int(probed.width), int(probed.height)],
        }
    except Exception:  # noqa: BLE001 - an unreadable probe is not a lie
        measured = {
            "duration": float(plan["duration"]),
            "fps": float(plan["fps"]),
            "size": [int(width), int(height)],
        }

    choice = ""
    try:
        from . import estimate as estimate_module  # noqa: PLC0415 - optional

        if workdir is not None:
            choice = estimate_module.read_plan_choice(workdir)
        choices = list(estimate_module.PLAN_CHOICE_VALUES)
    except Exception:  # noqa: BLE001 - the estimate module is a convenience
        choices = ["full", "fast"]

    return {
        "version": "vx.effect-spec/1",
        "output": str(destination.resolve()),
        "output_size": destination.stat().st_size,
        "duration": measured["duration"],
        "fps": measured["fps"],
        "size": measured["size"],
        "cost": plan["cost"],
        "budget": plan["budget"],
        "primitives": list(plan["primitives"]),
        "estimate": dict(plan["estimate"]),
        "plan_choice": {"choice": choice, "values": choices},
        "filter_script": plan["filter_complex"],
        "filter_script_path": str(script_path.resolve()),
        "command": proc.args,
        "stderr_tail": proc.tail(5),
    }
