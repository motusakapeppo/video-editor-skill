"""vx.edl3_render — compile an ``vx.edl3/1`` document to ffmpeg and render it.

The slice implements exactly two things, end to end:

* a ``transform`` effect whose ``opacity`` is keyframed, applied as **real
  alpha over a black canvas** (the foreground is scaled, converted to RGBA,
  its alpha channel is modulated, then overlaid on an opaque black source);
* a ``fade_in`` transition, emitted as ``fade=t=in`` on the composited clip.

Everything else is refused with a declared error. Nothing is silently skipped.

Opacity backends
----------------
The compiler picks one backend per document from the size of the largest
keyframe track:

* ``expr``    — up to three keyframes. The opacity curve is baked into a single
  ``geq`` alpha expression evaluated by ffmpeg per frame. No files, no timeline
  commands.
* ``sendcmd`` — more than three keyframes. The curve is flattened into one
  ``colorchannelmixer aa`` command per frame in a ``sendcmd`` block. ``sendcmd``
  is placed *before* the target filter so a command takes effect on the frame it
  names, not one frame later (a real one-frame lag, found while building this
  slice).

A static opacity (no keyframes) is always ``colorchannelmixer=aa=<value>``,
which is also real alpha.

``compile_timeline`` is pure and returns the filter script plus the argv; it
never runs ffmpeg. ``render`` writes the script, runs ffmpeg with
``-filter_complex_script`` and returns a report that states which backend was
chosen.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import ffmpeg
from .edl3 import (
    EDL3_VERSION,
    Keyframe,
    KeyframeTrack,
    RENDERABLE_EFFECTS,
    RENDERABLE_TRANSITIONS,
    Timeline3,
)

BACKEND_EXPR = "expr"
BACKEND_SENDCMD = "sendcmd"
#: More keyframes than this switches from a baked expression to sendcmd.
SENDCMD_THRESHOLD = 3

#: Structural validation codes the compiler refuses outright. Unsupported but
#: structurally valid things are translated to ``UNSUPPORTED_*`` on purpose.
HARD_CODES = frozenset(
    {
        "SCHEMA_VERSION",
        "FPS_INVALID",
        "SIZE_INVALID",
        "TIMELINE_EMPTY",
        "DUPLICATE_CLIP_ID",
        "CLIP_RANGE",
        "EFFECT_PARAM",
        "KEYFRAME_ORDER",
        "KEYFRAME_RANGE",
        "KEYFRAME_INTERP",
        "KEYFRAME_PARAM",
        "TRANSITION_RANGE",
        "TRANSITION_TOO_LONG",
    }
)

__all__ = [
    "BACKEND_EXPR",
    "BACKEND_SENDCMD",
    "SENDCMD_THRESHOLD",
    "CompileError",
    "choose_backend",
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


def _segment_expression(left: Keyframe, right: Keyframe) -> str:
    if left.interp == "hold":
        return _num(left.value)
    span = right.t - left.t
    x = f"((T-{_num(left.t)})/{_num(span)})"
    if left.interp == "bezier":
        shape = f"(3*{x}*{x}-2*{x}*{x}*{x})"
    else:
        shape = x
    return f"({_num(left.value)}+({_num(right.value - left.value)})*{shape})"


def track_expression(track: KeyframeTrack) -> str:
    """Bake a keyframe track into one clamped ffmpeg expression of ``T``."""
    keys = track.ordered()
    if not keys:
        raise CompileError("KEYFRAME_EMPTY", "opacity track has no keyframes")
    if len(keys) == 1:
        return f"max(0,min(1,{_num(keys[0].value)}))"
    expression = _num(keys[-1].value)
    for index in range(len(keys) - 2, -1, -1):
        segment = _segment_expression(keys[index], keys[index + 1])
        expression = f"if(lt(T,{_num(keys[index + 1].t)}),{segment},{expression})"
    first = keys[0]
    expression = f"if(lt(T,{_num(first.t)}),{_num(first.value)},{expression})"
    return f"max(0,min(1,{expression}))"


def sendcmd_commands(
    track: KeyframeTrack, fps: int, duration: float, target: str
) -> str:
    """One command per frame so the rendered curve follows ``track.sample``."""
    keys = track.ordered()
    if not keys:
        raise CompileError("KEYFRAME_EMPTY", "opacity track has no keyframes")
    frames = max(1, int(round(duration * fps)))
    commands: List[str] = []
    for index in range(frames + 1):
        t = index / float(fps)
        if t > duration + 1e-9:
            break
        value = track.sample(min(t, duration))
        commands.append(f"{_num(t)} {target} aa {_num(value)}")
    return ";".join(commands)


# ---------------------------------------------------------------- planning


def choose_backend(timeline: Timeline3) -> str:
    """The backend rule: the largest keyframe track decides for the document."""
    largest = 0
    for clip in timeline.clips:
        for effect in clip.effects:
            for track in effect.keyframes.values():
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


def _clip_plan(
    index: int,
    clip: Any,
    backend: str,
    width: int,
    height: int,
    fps: int,
) -> Dict[str, Any]:
    duration = clip.duration
    transform = clip.transform()
    scale = 1.0
    opacity_track: Optional[KeyframeTrack] = None
    static_opacity: Optional[float] = None
    if transform is not None:
        raw_scale = transform.sample("scale", 0.0)
        scale = 1.0 if raw_scale is None else float(raw_scale)
        opacity_track = transform.keyframes.get("opacity")
        static_opacity = transform.sample("opacity", 0.0)

    scaled_width = _even(width * scale)
    scaled_height = _even(height * scale)

    foreground: List[str] = [
        "setpts=PTS-STARTPTS",
        f"scale={scaled_width}:{scaled_height}:"
        "force_original_aspect_ratio=decrease:flags=bicubic",
        "format=rgba",
    ]
    opacity_filter: Optional[str] = None
    opacity_mode = "none"
    if transform is not None:
        opacity_filter, opacity_mode = _opacity_fragment(
            index, opacity_track, static_opacity, backend, fps, duration
        )
        if opacity_filter is not None:
            foreground.append(opacity_filter)

    fade_filter: Optional[str] = None
    transition = clip.transition_in
    if transition is not None and transition.kind == "fade_in":
        fade_filter = f"fade=t=in:st=0:d={_num(transition.duration)}"

    chains: List[str] = []
    chains.append(f"[{index}:v]" + ",".join(foreground) + f"[fg{index}]")
    chains.append(
        f"color=c=black:s={width}x{height}:r={fps}:d={_num(duration)}[bg{index}]"
    )
    composited = [
        f"[bg{index}][fg{index}]overlay="
        "x=(W-w)/2:y=(H-h)/2:format=auto:eof_action=pass:shortest=1",
        "format=yuv420p",
    ]
    if fade_filter is not None:
        composited.append(fade_filter)
    chains.append(",".join(composited) + f"[c{index}]")

    return {
        "id": clip.id,
        "index": index,
        "duration": duration,
        "backend": backend if opacity_mode == "keyframed" else BACKEND_EXPR,
        "opacity_mode": opacity_mode,
        "opacity_filter": opacity_filter,
        "fade_filter": fade_filter,
        "scale": scale,
        "keyframes": (len(opacity_track.keys) if opacity_track is not None else 0),
        "chains": chains,
    }


def compile_timeline(timeline: Timeline3) -> Dict[str, Any]:
    """Validate, refuse unsupported features, and return a compile plan."""
    if timeline.version != EDL3_VERSION:
        raise CompileError(
            "SCHEMA_VERSION",
            f"unsupported document version {timeline.version!r}",
            "version",
        )

    for problem in timeline.validate():
        if problem["code"] in HARD_CODES:
            raise CompileError(problem["code"], problem["detail"], problem["where"])

    backend = choose_backend(timeline)

    for clip_index, clip in enumerate(timeline.clips):
        clip_where = f"clips[{clip_index}]"
        if not Path(clip.source).exists():
            raise CompileError(
                "MISSING_SOURCE",
                f"source file not found: {clip.source}",
                f"{clip_where}.source",
            )
        transforms = [effect for effect in clip.effects if effect.kind == "transform"]
        for effect_index, effect in enumerate(clip.effects):
            if effect.kind not in RENDERABLE_EFFECTS:
                raise CompileError(
                    "UNSUPPORTED_EFFECT",
                    f"effect kind {effect.kind!r} is not implemented by edl3 "
                    f"(implemented: {sorted(RENDERABLE_EFFECTS)})",
                    f"{clip_where}.effects[{effect_index}].kind",
                )
        if len(transforms) > 1:
            raise CompileError(
                "UNSUPPORTED_EFFECT",
                "more than one transform effect per clip is not implemented",
                f"{clip_where}.effects",
            )
        if transforms:
            scale_track = transforms[0].keyframes.get("scale")
            if scale_track is not None:
                distinct = {round(key.value, 9) for key in scale_track.keys}
                if len(distinct) > 1:
                    raise CompileError(
                        "UNSUPPORTED_KEYFRAME",
                        "animated scale is not implemented; scale is static in "
                        "this slice, keep every scale keyframe at one value",
                        f"{clip_where}.effects.keyframes.scale",
                    )
        transition = clip.transition_in
        if transition is not None and transition.kind not in RENDERABLE_TRANSITIONS:
            raise CompileError(
                "UNSUPPORTED_TRANSITION",
                f"transition kind {transition.kind!r} is not implemented by edl3 "
                f"(implemented: {sorted(RENDERABLE_TRANSITIONS)})",
                f"{clip_where}.transition_in.kind",
            )

    plans = [
        _clip_plan(index, clip, backend, timeline.width, timeline.height, timeline.fps)
        for index, clip in enumerate(timeline.clips)
    ]

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
                _num(clip.duration),
                "-i",
                str(clip.source),
            ]
        )

    return {
        "version": timeline.version,
        "backend": backend,
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
) -> Dict[str, Any]:
    """Render ``timeline`` to ``output`` and return the compile/run report."""
    plan = compile_timeline(timeline)
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
