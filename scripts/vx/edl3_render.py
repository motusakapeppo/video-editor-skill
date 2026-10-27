"""vx.edl3_render — compile an ``vx.edl3/1`` document to ffmpeg and render it.

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
* adjustment layers, applied before each clip's own effects.

Refused loudly, never skipped
-----------------------------
``chroma_key`` and any kind outside :data:`edl3.RENDERABLE_EFFECTS` →
``UNSUPPORTED_EFFECT``. Text layers and caption tracks → ``UNSUPPORTED_EFFECT``
until F2-T07 wires the Chromium/ASS backend. Nested sequences →
``UNSUPPORTED_SEQUENCE``. ``crossfade`` → ``UNSUPPORTED_TRANSITION``. Audio
tracks → ``UNSUPPORTED_AUDIO`` unless the caller asks for ``audio="drop"``,
which the report states as ``audio_policy`` (the picture only).

``compile_timeline`` is pure: it never runs ffmpeg. ``render`` writes the
script, runs ffmpeg with ``-filter_complex_script`` and returns the report.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import ffmpeg
from .edl3 import (
    EDL3_VERSION,
    EFFECT_REGISTRY,
    Keyframe,
    KeyframeTrack,
    RENDERABLE_EFFECTS,
    RENDERABLE_TRANSITIONS,
    Transition,
    Timeline3,
)

BACKEND_EXPR = "expr"
BACKEND_SENDCMD = "sendcmd"
#: More opacity keyframes than this switches from a baked expression to sendcmd.
SENDCMD_THRESHOLD = 3
AUDIO_POLICIES = frozenset({"refuse", "drop"})

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
    }
)

#: Effect kinds the compiler can emit today, from the registry's own view.
SUPPORTED_EFFECTS = tuple(sorted(RENDERABLE_EFFECTS))

__all__ = [
    "BACKEND_EXPR",
    "BACKEND_SENDCMD",
    "SENDCMD_THRESHOLD",
    "AUDIO_POLICIES",
    "HARD_CODES",
    "SUPPORTED_EFFECTS",
    "CompileError",
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


# ---------------------------------------------------------------- planning


def choose_backend(timeline: Timeline3) -> str:
    """The opacity backend: the largest opacity track decides for the document."""
    largest = 0
    for clip in timeline.clips:
        transform = clip.effect("transform")
        if transform is None:
            continue
        for name, track in transform.keyframes.items():
            if name == "opacity":
                largest = max(largest, len(track.keys))
    return BACKEND_SENDCMD if largest > SENDCMD_THRESHOLD else BACKEND_EXPR


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
    timeline: Timeline3, clip: Any, where: str
) -> Dict[str, List[Any]]:
    """Adjustment layers first (track ascending), then the clip's own effects.

    Colour and blur stack, because they are point-wise and order is meaningful.
    Geometry (``crop``) and the compositing transform may not stack: two crop
    rects or two scales have no declared meaning here, so the compiler refuses
    instead of inventing one.
    """
    merged: List[Any] = []
    for layer in sorted(timeline.adjustments, key=lambda item: item.track):
        merged.extend(layer.effects)
    merged.extend(clip.effects)

    by_kind: Dict[str, List[Any]] = {}
    for effect in merged:
        by_kind.setdefault(effect.kind, []).append(effect)
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
    return by_kind


def _clip_plan(
    index: int,
    clip: Any,
    effects: Dict[str, List[Any]],
    backend: str,
    width: int,
    height: int,
    fps: int,
) -> Dict[str, Any]:
    duration = clip.timeline_duration
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

    foreground: List[str] = ["setpts=PTS-STARTPTS"]
    crop = effects.get("crop")
    if crop:
        foreground.append(_crop_fragment(crop[0]))
    foreground.append(
        f"scale={scaled_width}:{scaled_height}:"
        "force_original_aspect_ratio=decrease:flags=bicubic"
    )
    for ordinal, color in enumerate(effects.get("color") or []):
        foreground.append(_color_fragment(index, ordinal, color, fps, duration))
    for blur in effects.get("blur") or []:
        foreground.append(_blur_fragment(blur))

    opacity_filter: Optional[str] = None
    opacity_mode = "none"
    if transform:
        foreground.append("format=rgba")
        opacity_filter, opacity_mode = _opacity_fragment(
            index, opacity_track, static_opacity, backend, fps, duration
        )
        if opacity_filter is not None:
            foreground.append(opacity_filter)

    fade_filter = _transition_fragment(clip.transition_in, duration)
    fade_out_filter = _transition_fragment(clip.transition_out, duration)
    fades = [fade for fade in (fade_filter, fade_out_filter) if fade is not None]

    chains: List[str] = []
    if transform is None:
        direct = foreground + [
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            "setsar=1",
            "format=yuv420p",
        ]
        direct.extend(fades)
        chains.append(f"[{index}:v]" + ",".join(direct) + f"[c{index}]")
    else:
        chains.append(f"[{index}:v]" + ",".join(foreground) + f"[fg{index}]")
        chains.append(
            f"color=c=black:s={width}x{height}:r={fps}:d={_num(duration)}[bg{index}]"
        )
        composited = [
            f"[bg{index}][fg{index}]overlay="
            "x=(W-w)/2:y=(H-h)/2:format=auto:eof_action=pass:shortest=1",
            "format=yuv420p",
        ]
        composited.extend(fades)
        chains.append(",".join(composited) + f"[c{index}]")

    mechanisms = {kind: EFFECT_REGISTRY[kind].backend for kind in effects}
    return {
        "id": clip.id,
        "index": index,
        "duration": duration,
        "backend": backend if opacity_mode == "keyframed" else BACKEND_EXPR,
        "backends": mechanisms,
        "opacity_mode": opacity_mode,
        "opacity_filter": opacity_filter,
        "fade_filter": fade_filter,
        "fade_out_filter": fade_out_filter,
        "scale": scale,
        "keyframes": (len(opacity_track.keys) if opacity_track is not None else 0),
        "chains": chains,
    }


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
    if timeline.sequences or any(clip.sequence is not None for clip in timeline.clips):
        raise CompileError(
            "UNSUPPORTED_SEQUENCE",
            "nested sequences are validated and serialised only; this compiler "
            "does not flatten them",
            "sequences",
        )
    if timeline.audio and audio != "drop":
        raise CompileError(
            "UNSUPPORTED_AUDIO",
            f"{len(timeline.audio)} audio track(s) carried but this compiler does "
            "not mix audio; pass audio='drop' to render the picture only and "
            "record the policy",
            "audio",
        )


def compile_timeline(timeline: Timeline3, *, audio: str = "refuse") -> Dict[str, Any]:
    """Validate, refuse unsupported features, and return a compile plan."""
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

    for problem in timeline.validate():
        if problem["code"] in HARD_CODES:
            raise CompileError(problem["code"], problem["detail"], problem["where"])

    _refuse_unsupported(timeline, audio)

    backend = choose_backend(timeline)

    plans: List[Dict[str, Any]] = []
    for clip_index, clip in enumerate(timeline.clips):
        clip_where = f"clips[{clip_index}]"
        if clip.sequence is None and not Path(clip.source).exists():
            raise CompileError(
                "MISSING_SOURCE",
                f"source file not found: {clip.source}",
                f"{clip_where}.source",
            )
        effects = _effective_effects(timeline, clip, clip_where)
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
        plans.append(
            _clip_plan(
                clip_index,
                clip,
                effects,
                backend,
                timeline.width,
                timeline.height,
                timeline.fps,
            )
        )

    chains: List[str] = []
    for plan in plans:
        chains.extend(plan["chains"])

    if len(plans) == 1:
        final_label = "c0"
    else:
        inputs = "".join(f"[c{index}]" for index in range(len(plans)))
        chains.append(f"{inputs}concat=n={len(plans)}:v=1:a=0[out]")
        final_label = "out"

    input_args: List[str] = []
    for clip in timeline.clips:
        input_args.extend(
            [
                "-ss",
                _num(clip.src_in),
                "-t",
                _num(clip.timeline_duration),
                "-i",
                str(clip.source),
            ]
        )

    return {
        "version": timeline.version,
        "backend": backend,
        "audio_policy": audio,
        "filter_script": ";\n".join(chains) + "\n",
        "input_args": input_args,
        "map": f"[{final_label}]",
        "clips": [{k: v for k, v in plan.items() if k != "chains"} for plan in plans],
        "warnings": [],
    }


def render(
    timeline: Timeline3,
    output: Any,
    *,
    workdir: Optional[Any] = None,
    timeout: Optional[int] = None,
    audio: str = "refuse",
) -> Dict[str, Any]:
    """Render ``timeline`` to ``output`` and return the compile/run report."""
    plan = compile_timeline(timeline, audio=audio)
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    script_dir = Path(workdir) if workdir is not None else destination.parent
    script_dir.mkdir(parents=True, exist_ok=True)
    script_path = script_dir / "filter_complex.txt"
    script_path.write_text(plan["filter_script"], encoding="utf-8", newline="\n")

    args = list(plan["input_args"])
    args.extend(
        [
            "-filter_complex_script",
            str(script_path),
            "-map",
            plan["map"],
            "-an",
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
        "warnings": plan["warnings"],
        "command": proc.args,
        "stderr_tail": proc.tail(5),
    }
    return report
