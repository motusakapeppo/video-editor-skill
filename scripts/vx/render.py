"""vx.render — filtergraph construction and the render pipeline.

Invariants this module is built on (measured, not guessed):

  * Cuts are frame-accurate only when re-encoded. ``-c copy`` + ``-ss`` seeks to
    a keyframe, keeps pre-roll and emits negative PTS (measured: 105 frames for
    a 2 s request). So every cut goes through ``trim``/``atrim`` +
    ``setpts=PTS-STARTPTS``/``asetpts=PTS-STARTPTS`` + ``concat``.
  * Every segment is normalised (``setsar=1``, ``format=yuv420p``) *before*
    concat, because concat requires identical size / SAR / pix_fmt.
  * Stage order is fixed: trim/atrim -> concat -> stabilize -> crop/scale ->
    zoom -> eq/color -> subtitles -> format/setsar.
  * vidstab is two passes. The ``.trf`` is referenced by *bare filename* and the
    ffmpeg process runs with ``cwd = workdir``: absolute Windows paths break
    filtergraph option parsing.
  * loudnorm is two passes: measure the stage-03 file, then apply ``measured_*``.
  * proxy and full share one graph. Every numeric parameter is either a fraction
    or derived from the output size, so frame counts match within 1.

No ``zoompan`` (slow, steps visibly), no ``-c copy`` for cuts, no ``shell=True``.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import contract
from . import ffmpeg as ff
from . import fonts as fonts_mod
from .contract import (
    LOUDNESS_TARGETS,
    THRESHOLDS,
    CropPlan,
    EditPlan,
    RenderFailure,
    even,
    expected_frames,
    output_size,
    target_crop_size,
)
from .winpath import ass_filter_path

__all__ = [
    "build_filtergraph",
    "write_script",
    "render",
    "loudnorm_two_pass",
    "swap_proxy_to_full",
    "build_trim_graph",
    "loudness_target",
    "verify_treatments",
]

TRF_NAME = "transforms.trf"
DEFAULT_FPS = 30.0
FADE_MS = 12  # afade at joints: no duration change (acrossfade would cost 10 ms)
FIT_BLUR_SIGMA = 20.0


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:  # NaN
        return default
    return out


def _snap(t: float, fps: float) -> float:
    """Snap a time to the frame grid so trim yields an exact frame count."""
    if fps <= 0:
        return float(t)
    return round(float(t) * fps) / fps


def _cut_bounds(cut: Dict[str, Any]) -> Tuple[float, float]:
    start = cut.get("in", cut.get("start", 0.0))
    end = cut.get("out", cut.get("end", 0.0))
    return _num(start), _num(end)


def _segments(plan: EditPlan, media: Dict[str, Any]) -> List[Tuple[float, float]]:
    """Frame-snapped (start, end) pairs; falls back to the whole source."""
    fps = _num(media.get("fps"), DEFAULT_FPS) or DEFAULT_FPS
    cuts = list(plan.cuts or [])
    if not cuts:
        duration = _num(media.get("duration"))
        if duration <= 0:
            raise RenderFailure("plan has no cuts and media has no duration")
        cuts = [{"in": 0.0, "out": duration}]
    out: List[Tuple[float, float]] = []
    for cut in cuts:
        start, end = _cut_bounds(cut)
        start, end = _snap(start, fps), _snap(end, fps)
        if end > start:
            out.append((start, end))
    if not out:
        raise RenderFailure("plan has no usable cuts (every out <= in)")
    return out


def _treatment(plan: EditPlan, name: str) -> Optional[Dict[str, Any]]:
    for item in plan.treatments or []:
        if item.get("name") == name:
            return item
    return None


def _enabled(plan: EditPlan, name: str) -> bool:
    item = _treatment(plan, name)
    return bool(item and item.get("enabled"))


def _params(plan: EditPlan, name: str) -> Dict[str, Any]:
    item = _treatment(plan, name) or {}
    params = item.get("params") or {}
    return params if isinstance(params, dict) else {}


def _aspect(plan: EditPlan) -> str:
    return str((plan.output or {}).get("aspect") or "9:16")


def _precision(plan: EditPlan, precision: str) -> str:
    return precision or str((plan.render or {}).get("precision") or "full")


def loudness_target(plan: EditPlan) -> Tuple[float, float, float]:
    """(I, TP, LRA) from the plan, first hit wins.

    Merge order, so the *executed* declaration beats the hardcoded fallback:

      a. ``output.loudness`` as a profile name -> ``LOUDNESS_TARGETS[name]``
      b. ``output.loudness`` as a dict with lowercase ``i``/``tp``/``lra``
      c. the ``loudnorm`` treatment params with ffmpeg-style ``I``/``TP``/``LRA``
      d. ``output.loudness_target`` as a profile name
      e. the social profile (-14, -1, 11) only as a last resort

    Public on purpose: ``qc.py`` mirrors this so the render and the check agree
    on what was declared.
    """
    out_cfg = plan.output or {}
    render_cfg = plan.render or {}
    raw = out_cfg.get("loudness") or render_cfg.get("loudness")
    if isinstance(raw, str) and raw in LOUDNESS_TARGETS:
        i, tp = LOUDNESS_TARGETS[raw]
        return float(i), float(tp), 11.0
    if isinstance(raw, dict):
        return (
            _num(raw.get("i", raw.get("target_i")), -14.0),
            _num(raw.get("tp", raw.get("target_tp")), -1.0),
            _num(raw.get("lra", raw.get("target_lra")), 11.0),
        )
    params = _params(plan, "loudnorm")
    if any(key in params for key in ("I", "TP", "LRA")):
        return (
            _num(params.get("I"), -14.0),
            _num(params.get("TP"), -1.0),
            _num(params.get("LRA"), 11.0),
        )
    name = out_cfg.get("loudness_target") or render_cfg.get("loudness_target")
    if isinstance(name, str) and name in LOUDNESS_TARGETS:
        i, tp = LOUDNESS_TARGETS[name]
        return float(i), float(tp), 11.0
    i, tp = LOUDNESS_TARGETS["social"]
    return float(i), float(tp), 11.0


def _loudness_target(plan: EditPlan) -> Tuple[float, float, float]:
    """Thin alias kept for callers that predate the public name."""
    return loudness_target(plan)


@contextmanager
def _in_dir(path: Path):
    """Run ffmpeg with cwd=path so a bare ``transforms.trf`` resolves.

    ``run_ff`` takes no cwd argument (frozen API), and absolute Windows paths
    inside a filtergraph break option parsing, so the process cwd is the only
    correct lever. All input/output paths we pass are absolute.
    """
    previous = os.getcwd()
    os.chdir(str(path))
    try:
        yield
    finally:
        os.chdir(previous)


# --------------------------------------------------------------------------- #
# filtergraph pieces
# --------------------------------------------------------------------------- #


def _crop_scale(
    plan: EditPlan,
    media: Dict[str, Any],
    crop_plan: Optional[Dict[str, Any]],
    precision: str,
    *,
    segments: Optional[Sequence[Tuple[float, float]]] = None,
) -> str:
    """crop -> scale. Crop geometry is in *source* pixels, so it is identical
    for proxy and full; only the scale target changes."""
    src_w = int(_num(media.get("width")))
    src_h = int(_num(media.get("height")))
    aspect = _aspect(plan)
    out_w, out_h = output_size(aspect, precision)

    if src_w <= 0 or src_h <= 0:
        # Unknown source geometry: let ffmpeg derive it from the stream.
        return f"scale={out_w}:{out_h}:flags=lanczos"

    if crop_plan:
        cp = crop_plan if isinstance(crop_plan, dict) else crop_plan.to_dict()
        crop_w = (
            int(_num(cp.get("crop_w"), 0)) or target_crop_size(src_w, src_h, aspect)[0]
        )
        crop_h = (
            int(_num(cp.get("crop_h"), 0)) or target_crop_size(src_w, src_h, aspect)[1]
        )
        crop_w = min(even(crop_w), even(src_w))
        crop_h = min(even(crop_h), even(src_h))
        x_expr = y_expr = None
        if cp.get("boxes") and cp.get("mode") == "track":
            from . import autoframe  # noqa: PLC0415

            normalized = CropPlan(**cp)
            if normalized.src_w <= 0:
                normalized.src_w = src_w
            if normalized.src_h <= 0:
                normalized.src_h = src_h
            segs = segments if segments is not None else _segments(plan, media)
            fps = _num(media.get("fps"), DEFAULT_FPS) or DEFAULT_FPS
            remapped = autoframe.remap_to_timeline(normalized, segs, fps=fps)
            expr = autoframe.crop_expression(remapped, offset_s=0.0)
            x_expr = expr["x"]
            y_expr = expr["y"]
        elif cp.get("boxes") and cp.get("mode") == "lock":
            first = cp["boxes"][0]
            x_expr = str(int(max(0, min(src_w - crop_w, _num(first.get("x"))))))
            y_expr = str(int(max(0, min(src_h - crop_h, _num(first.get("y"))))))
        x = x_expr if x_expr is not None else str(max(0, (src_w - crop_w) // 2))
        y = y_expr if y_expr is not None else str(max(0, (src_h - crop_h) // 2))
    else:
        crop_w, crop_h = target_crop_size(src_w, src_h, aspect)
        x = str(max(0, (src_w - crop_w) // 2))
        y = str(max(0, (src_h - crop_h) // 2))

    return f"crop={crop_w}:{crop_h}:{x}:{y},scale={out_w}:{out_h}:flags=lanczos"


def _zoom_chain(plan: EditPlan, precision: str) -> Optional[str]:
    """Static push-in via scale+crop. Never zoompan.

    The factor is a fraction of the output size, so proxy and full zoom by the
    same relative amount.
    """
    if not _enabled(plan, "zoom"):
        return None
    params = _params(plan, "zoom")
    factor = _num(params.get("factor"), 0.0)
    if factor <= 0:
        factor = 1.0 + _num(params.get("amount"), 0.06)
    if factor <= 1.0:
        return None
    out_w, out_h = output_size(_aspect(plan), precision)
    zoom_w = even(out_w * factor)
    zoom_h = even(out_h * factor)
    return (
        f"scale={zoom_w}:{zoom_h}:flags=lanczos,"
        f"crop={out_w}:{out_h}:{(zoom_w - out_w) // 2}:{(zoom_h - out_h) // 2}"
    )


def _eq_chain(plan: EditPlan) -> Optional[str]:
    """eq/color from color_fix / exposure_fix params. Unitless -> parity safe."""
    params: Dict[str, Any] = {}
    for name in ("color_fix", "exposure_fix"):
        if _enabled(plan, name):
            params.update(_params(plan, name))
    if not params:
        return None
    keys = (
        "brightness",
        "contrast",
        "saturation",
        "gamma",
        "gamma_r",
        "gamma_g",
        "gamma_b",
    )
    eq_parts = [f"{k}={_num(params[k]):.4f}" for k in keys if k in params]
    chain: List[str] = []
    if eq_parts:
        chain.append("eq=" + ":".join(eq_parts))
    raw = str(params.get("filter") or "").strip()
    if raw:
        chain.append(raw)
    return ",".join(chain) if chain else None


def _db_to_linear(db: float) -> float:
    """dB -> linear amplitude, the unit acompressor's threshold/makeup expect."""
    return 10.0 ** (db / 20.0)


def _audio_chain(plan: EditPlan) -> str:
    """Audio filters for the joined stream, in plan order.

    Only what the plan enabled is emitted: denoise, then compress, then the
    mandatory resample. ``denoise`` prefers the plan's ``fallback`` filter when
    no numeric ``noise_floor`` was declared (plan.py ships ``highpass=f=80``);
    otherwise it uses ``afftdn``. ``compress`` reads plan.py's key names
    (``threshold_db``/``attack_ms``/``release_ms``/``ratio``) and falls back to
    sane defaults when a key is absent. The plan declares threshold and makeup
    in dB; acompressor wants linear amplitude, so both are converted.
    """
    parts: List[str] = []
    if _enabled(plan, "denoise"):
        params = _params(plan, "denoise")
        floor = params.get("noise_floor")
        try:
            numeric_floor: Optional[float] = float(floor)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            numeric_floor = None
        if numeric_floor is None and params.get("fallback"):
            parts.append(str(params["fallback"]))
        else:
            parts.append(f"afftdn=nf={_num(floor, -25.0):.1f}")
    if _enabled(plan, "compress"):
        params = _params(plan, "compress")
        threshold_db = _num(params.get("threshold_db", params.get("threshold")), -18.0)
        ratio = _num(params.get("ratio"), 3.0)
        attack = _num(params.get("attack_ms", params.get("attack")), 10.0)
        release = _num(params.get("release_ms", params.get("release")), 200.0)
        makeup_db = _num(params.get("makeup_db", params.get("makeup")), 2.0)
        parts.append(
            f"acompressor=threshold={_db_to_linear(threshold_db):.6f}"
            f":ratio={ratio:.2f}:attack={attack:.1f}:release={release:.1f}"
            f":makeup={_db_to_linear(makeup_db):.6f}"
        )
    parts.append("aresample=48000")
    return ",".join(parts)


def _fade_chain(index: int, count: int, duration: float, fade: float) -> str:
    """Interior-joint ``afade`` in/out for one audio segment, or ``""``.

    One truth for both graph builders. No fade at the very start or the very
    end: only joints between two segments get one. ``afade`` never changes the
    duration (``acrossfade`` would), so ``expected_frames`` stays exact. A
    segment too short to hold two fades is left alone.
    """
    if fade <= 0 or duration <= 2 * fade:
        return ""
    span = min(fade, duration / 2.0)
    parts: List[str] = []
    if index > 0:
        parts.append(f"afade=t=in:st=0:d={span:.3f}")
    if index < count - 1:
        parts.append(f"afade=t=out:st={duration - span:.6f}:d={span:.3f}")
    return ",".join(parts)


def _detect_graph(plan: EditPlan) -> str:
    """vidstabdetect pass, driven by the plan's stabilize params.

    ``shakiness`` (4/6/8 from plan.py) and ``accuracy`` (9) come from the
    treatment; the historical 5/9 are only the fallback when a key is absent.
    """
    params = _params(plan, "stabilize")
    shakiness = int(_num(params.get("shakiness"), 5))
    accuracy = int(_num(params.get("accuracy"), 9))
    return (
        f"[0:v]vidstabdetect=shakiness={shakiness}:accuracy={accuracy}"
        f":result={TRF_NAME}[vo]"
    )


# --------------------------------------------------------------------------- #
# public: graph builders
# --------------------------------------------------------------------------- #


def _visual_fragment(
    visual: Dict[str, Any],
    index: int,
    start: float,
    end: float,
    media: Dict[str, Any],
    fps: float,
) -> str:
    """Grade + animation chains for one segment, or ``""`` when both are empty.

    The grade node is ``visual["grade_nodes"][i % len]``, the animation choice
    ``visual["animations"][i % len]``. Empty fragments are skipped entirely so
    the joined graph never carries a dangling comma. Imported lazily: a caller
    that never passes ``visual`` pays no import and sees no behaviour change.
    """
    from . import animplan, gradeplan  # noqa: PLC0415

    fragments: List[str] = []
    nodes = visual.get("grade_nodes") or []
    if nodes:
        chain = gradeplan.chain_for_shot(nodes[index % len(nodes)])
        if chain:
            fragments.append(chain)
    choices = visual.get("animations") or []
    if choices:
        shot = {
            "duration": end - start,
            "width": media.get("width"),
            "height": media.get("height"),
            "fps": fps,
        }
        chain = animplan.chain_for_shot(choices[index % len(choices)], shot)
        if chain:
            fragments.append(chain)
    return ",".join(fragments)


def build_filtergraph(
    plan: EditPlan,
    media: dict,
    *,
    subs_path: str | None = None,
    precision: str = "full",
    crop_plan: dict | None = None,
    visual: dict | None = None,
    hook_path: str | None = None,
) -> str:
    """Return the complete ``-filter_complex`` graph for one render pass.

    Outputs are labelled ``[vout]`` and, when the source has audio, ``[aout]``.
    The graph is a single line: it is written verbatim to a ``.txt`` and passed
    through ``-filter_complex_script`` (the Windows 8191-char limit is real).

    ``visual`` carries the adaptive visual decisions (``vx.visual/1.0``): when
    present, each video segment gets the grade chain and the animation chain
    spliced in after ``format=yuv420p`` and before its ``[vN]`` label. Empty
    chains are skipped, so a visual of only no-op decisions yields a graph
    byte-identical to ``visual=None``. ``visual["width"]``/``["height"]``
    override the media geometry when present.
    """
    precision = _precision(plan, precision)
    fps = _num(media.get("fps"), DEFAULT_FPS) or DEFAULT_FPS
    has_audio = bool(media.get("has_audio", True))
    segments = _segments(plan, media)

    media_eff: Dict[str, Any] = media
    if visual:
        media_eff = dict(media)
        if visual.get("width") is not None:
            media_eff["width"] = visual["width"]
        if visual.get("height") is not None:
            media_eff["height"] = visual["height"]

    parts: List[str] = []
    for index, (start, end) in enumerate(segments):
        head = (
            f"[0:v]trim=start={start:.6f}:end={end:.6f},"
            f"setpts=PTS-STARTPTS,setsar=1,format=yuv420p"
        )
        if visual:
            extra = _visual_fragment(visual, index, start, end, media_eff, fps)
            if extra:
                head += "," + extra
        parts.append(head + f"[v{index}]")
        if has_audio:
            audio = f"[0:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS"
            fade = _fade_chain(index, len(segments), end - start, FADE_MS / 1000.0)
            if fade:
                audio += "," + fade
            parts.append(audio + f"[a{index}]")

    if len(segments) == 1:
        video_label, audio_label = "[v0]", "[a0]"
    else:
        inputs = "".join(
            f"[v{i}][a{i}]" if has_audio else f"[v{i}]" for i in range(len(segments))
        )
        if has_audio:
            parts.append(f"{inputs}concat=n={len(segments)}:v=1:a=1[vc][ac]")
            video_label, audio_label = "[vc]", "[ac]"
        else:
            parts.append(f"{inputs}concat=n={len(segments)}:v=1:a=0[vc]")
            video_label, audio_label = "[vc]", ""

    chain: List[str] = []
    pre: List[str] = []
    if _enabled(plan, "stabilize"):
        # Two-pass: the detect pass is run by render() into workdir/transforms.trf.
        stab = _params(plan, "stabilize")
        smoothing = int(_num(stab.get("smoothing"), 10))
        stab_zoom = int(_num(stab.get("zoom"), 0))
        pre.append(
            f"vidstabtransform=input={TRF_NAME}:smoothing={smoothing}"
            f":zoom={stab_zoom}:optzoom=1"
        )

    post: List[str] = []
    zoom = _zoom_chain(plan, precision)
    if zoom:
        post.append(zoom)
    eq = _eq_chain(plan)
    if eq:
        post.append(eq)
    if hook_path:
        post.append(
            f"subtitles={ass_filter_path(hook_path)}:fontsdir={fonts_mod.fontsdir_arg()}"
        )
    if subs_path:
        post.append(
            f"subtitles={ass_filter_path(subs_path)}:fontsdir={fonts_mod.fontsdir_arg()}"
        )

    if crop_plan is not None and str(crop_plan.get("mode")) == "fit":
        out_w, out_h = output_size(_aspect(plan), precision)
        head = video_label
        if pre:
            parts.append(f"{head}{','.join(pre + ['format=yuv420p'])}[vpre]")
            head = "[vpre]"
        parts.append(
            f"{head}split=2[fitbg][fitfg];"
            f"[fitbg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={out_w}:{out_h},gblur=sigma={FIT_BLUR_SIGMA}[fitbgb];"
            f"[fitfg]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos[fitfgs];"
            f"[fitbgb][fitfgs]overlay=(W-w)/2:(H-h)/2[vfit]"
        )
        parts.append(f"[vfit]{','.join(post + ['setsar=1', 'format=yuv420p'])}[vout]")
    else:
        chain = (
            pre
            + [_crop_scale(plan, media_eff, crop_plan, precision, segments=segments)]
            + post
        )
        chain += ["format=yuv420p", "setsar=1"]
        parts.append(f"{video_label}{','.join(chain)}[vout]")

    if has_audio:
        parts.append(f"{audio_label}{_audio_chain(plan)}[aout]")

    return ";".join(parts)


def build_trim_graph(
    cuts: list[dict],
    *,
    fps: float,
    fade_ms: int | None = None,
    with_audio: bool = True,
) -> str:
    """Standalone trim/atrim + concat graph for a single input.

    ``fade_ms`` adds an ``afade`` in/out at every joint. Unlike ``acrossfade``
    (which shortens the total by its duration) ``afade`` does not change the
    duration, so ``expected_frames`` stays exact.
    """
    fps = _num(fps, DEFAULT_FPS) or DEFAULT_FPS
    segments: List[Tuple[float, float]] = []
    for cut in cuts or []:
        start, end = _cut_bounds(cut)
        start, end = _snap(start, fps), _snap(end, fps)
        if end > start:
            segments.append((start, end))
    if not segments:
        raise RenderFailure("build_trim_graph: no usable cuts")

    fade = max(0, int(fade_ms)) / 1000.0 if fade_ms else 0.0
    parts: List[str] = []
    for index, (start, end) in enumerate(segments):
        parts.append(
            f"[0:v]trim=start={start:.6f}:end={end:.6f},"
            f"setpts=PTS-STARTPTS,setsar=1,format=yuv420p[v{index}]"
        )
        if with_audio:
            audio = f"[0:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS"
            fade_text = _fade_chain(index, len(segments), end - start, fade)
            if fade_text:
                audio += "," + fade_text
            parts.append(f"{audio}[a{index}]")

    if len(segments) == 1:
        video_label, audio_label = "[v0]", "[a0]"
    else:
        inputs = "".join(
            f"[v{i}][a{i}]" if with_audio else f"[v{i}]" for i in range(len(segments))
        )
        if with_audio:
            parts.append(f"{inputs}concat=n={len(segments)}:v=1:a=1[vc][ac]")
            video_label, audio_label = "[vc]", "[ac]"
        else:
            parts.append(f"{inputs}concat=n={len(segments)}:v=1:a=0[vc]")
            video_label, audio_label = "[vc]", ""

    parts.append(f"{video_label}format=yuv420p,setsar=1[vout]")
    if with_audio:
        parts.append(f"{audio_label}aresample=48000[aout]")
    return ";".join(parts)


def write_script(graph: str, dst) -> str:
    """Write a filtergraph to ``dst`` and return the path as a string.

    ``-filter_complex_script`` sidesteps the 8191-character Windows command
    line limit. The file is written as one line with a trailing newline.
    """
    path = Path(dst)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = " ".join(str(graph).split())
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text + "\n")
    return str(path)


# --------------------------------------------------------------------------- #
# public: loudness
# --------------------------------------------------------------------------- #


def _silent_loudness(stats: dict) -> bool:
    if not stats:
        return False
    try:
        number = float(stats.get("input_i"))
    except (TypeError, ValueError):
        return True
    if not math.isfinite(number):
        return True
    return number <= THRESHOLDS["silence_dbfs"]


def loudnorm_two_pass(
    stats: dict,
    *,
    target_i: float = -14.0,
    target_tp: float = -1.0,
    target_lra: float = 11.0,
) -> list[str]:
    """Build the ``-af`` argument pair for the second loudnorm pass.

    Returns ``["-af", "loudnorm=..."]`` ready to splice into ``run_ff`` args.
    Falls back to dynamic mode when the measurement is unusable.
    """
    measured = {
        "measured_I": stats.get("input_i"),
        "measured_TP": stats.get("input_tp"),
        "measured_LRA": stats.get("input_lra"),
        "measured_thresh": stats.get("input_thresh"),
        "offset": stats.get("target_offset", 0.0),
    }
    numbers: Dict[str, Optional[float]] = {}
    for key in ("measured_I", "measured_TP", "measured_LRA", "measured_thresh"):
        try:
            number = float(measured[key])
        except (TypeError, ValueError):
            number = None
        numbers[key] = number if number is not None and math.isfinite(number) else None
    usable = all(value is not None for value in numbers.values()) and (
        numbers["measured_I"] > THRESHOLDS["silence_dbfs"]
    )
    base = f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}"
    if usable:
        base += (
            f":measured_I={numbers['measured_I']:.2f}"
            f":measured_TP={numbers['measured_TP']:.2f}"
            f":measured_LRA={numbers['measured_LRA']:.2f}"
            f":measured_thresh={numbers['measured_thresh']:.2f}"
            f":offset={measured['offset']}"
            ":linear=true"
        )
    return ["-af", base + ":print_format=summary"]


def _measure_loudness(path: Path) -> Dict[str, float]:
    """First loudnorm pass: measure, do not touch the file."""
    proc = ff.run_ff(
        ["-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        loglevel="info",
    )
    if not proc.ok:
        raise RenderFailure(f"loudnorm measure failed: {proc.tail(8)}")
    text = proc.stderr
    start, end = text.rfind("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise RenderFailure("loudnorm measure produced no JSON block")
    try:
        raw = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise RenderFailure(f"loudnorm JSON unreadable: {exc}") from exc
    out: Dict[str, float] = {}
    for key, value in raw.items():
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------- #
# public: plan surgery
# --------------------------------------------------------------------------- #


def swap_proxy_to_full(plan: EditPlan, media: dict) -> EditPlan:
    """Return a copy of ``plan`` pinned to full precision.

    Used to re-render a proxy-approved plan at delivery resolution: the graph is
    identical, only the scale target changes.
    """
    del media  # geometry is re-derived from the plan; kept for API symmetry
    clone = copy.deepcopy(plan)
    clone.render = dict(clone.render or {})
    clone.render["precision"] = "full"
    clone.render.pop("proxy_width", None)
    clone.output = dict(clone.output or {})
    clone.output["precision"] = "full"
    return clone


# --------------------------------------------------------------------------- #
# public: the pipeline
# --------------------------------------------------------------------------- #


def _frame_count(path: Path) -> int:
    proc = ff.run_fp(
        [
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ]
    )
    try:
        return int((proc.stdout or "").strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0


def _encoder_for(plan: EditPlan, precision: str) -> Dict[str, Any]:
    """Master goes to software, previews to the verified hardware encoder."""
    from . import accel

    render_cfg = plan.render or {}
    requested = str(render_cfg.get("encoder") or "")
    if precision == "proxy":
        quality = int(_num(render_cfg.get("proxy_crf"), 23))
        kind, preset = "scratch", "veryfast"
    else:
        quality = int(_num(render_cfg.get("crf"), 18))
        kind = "master"
        preset = str(render_cfg.get("preset") or "medium")
    choice = accel.pick(kind, quality=quality, preset=preset, preferred=requested)
    return {
        "name": choice["name"],
        "software": choice["software"],
        "args": list(choice["args"]),
        "reason": choice["reason"],
        "quality": quality,
    }


def _encode_args(plan: EditPlan, precision: str) -> List[str]:
    choice = _encoder_for(plan, precision)
    return [*choice["args"], "-pix_fmt", "yuv420p"]


TREATMENT_TOKENS: Dict[str, Tuple[str, ...]] = {
    "denoise": ("afftdn", "highpass"),
    "compress": ("acompressor",),
    "stabilize": ("vidstabtransform",),
    "loudnorm": ("loudnorm",),
    "color_fix": ("eq", "colorbalance", "curves"),
    "exposure_fix": ("eq",),
    "subtitles": ("subtitles=",),
    "hook_overlay": ("hook.ass",),
    # autoframe/zoom are resolved to the concrete geometry below. A bare
    # "crop" or "scale" appears in every graph, so it would be an unfalsifiable
    # gate: the audit's rule is that a bypassable control is a useless control.
    "autoframe": ("__autoframe_geometry__",),
    "fit": ("gblur=sigma=",),
    "zoom": ("__zoom_geometry__",),
}

# EXEMPT: cut_silence is expressed in the segment count (the cuts themselves),
# never as a filter node, so there is no token to look for.
TREATMENT_EXEMPT = ("cut_silence",)


def _expected_fragment(
    plan: EditPlan,
    name: str,
    media: Optional[Dict[str, Any]],
    precision: str,
    crop_plan: Optional[Dict[str, Any]],
) -> Optional[str]:
    """The concrete filter fragment an enabled treatment must leave in the graph.

    ``None`` means the plain token is already discriminating. An impossible
    token means the plan declared the treatment but its input is missing: that
    is a failure, never a pass.
    """
    if name == "autoframe":
        if not crop_plan or media is None:
            return "__autoframe_without_crop_plan__"
        return _crop_scale(plan, media, crop_plan, precision).split(",")[0]
    if name == "zoom":
        fragment = _zoom_chain(plan, precision)
        return fragment.split(",")[0] if fragment else "__zoom_without_filter__"
    if name in ("color_fix", "exposure_fix"):
        fragment = _eq_chain(plan)
        return fragment.split(",")[0] if fragment else "__eq_without_filter__"
    return None


def verify_treatments(
    plan: EditPlan,
    scripts: Iterable[str],
    *,
    media: Optional[Dict[str, Any]] = None,
    precision: str = "full",
    crop_plan: Optional[Dict[str, Any]] = None,
) -> None:
    """Runtime gate: every enabled treatment must appear in a written script.

    Anti-"declared but not executed": a plan can declare a treatment that the
    graph builder silently drops. This walks the filter_complex scripts that
    were actually written to disk and raises ``RenderFailure`` with the missing
    fragment when an enabled treatment left no trace. Geometry-bearing
    treatments (autoframe, zoom, grade) are checked against the exact fragment
    derived from the plan, not against a word that occurs in every graph.
    """
    text = "\n".join(str(item) for item in scripts)
    for treatment in plan.treatments or []:
        if not isinstance(treatment, dict) or not treatment.get("enabled"):
            continue
        name = str(treatment.get("name") or "")
        if name and name not in TREATMENT_TOKENS and name not in TREATMENT_EXEMPT:
            raise RenderFailure(
                {
                    "step": "verify_treatments",
                    "treatment": name,
                    "missing": "no ledger entry for this treatment",
                }
            )
    for name, tokens in TREATMENT_TOKENS.items():
        if not _enabled(plan, name):
            continue
        expected = _expected_fragment(plan, name, media, precision, crop_plan)
        candidates = (expected,) if expected is not None else tokens
        if not any(token and token in text for token in candidates):
            raise RenderFailure(
                {
                    "step": "verify_treatments",
                    "treatment": name,
                    "missing": candidates[0],
                }
            )


def edl_input_map(staged_sources: Mapping[str, str]) -> Dict[str, int]:
    return {source_id: index for index, source_id in enumerate(staged_sources)}


def _edl_clips(plan: Any, *, layers: Optional[set] = None) -> List[Any]:
    clips = []
    for track in getattr(plan, "video_tracks", ()):
        for clip in getattr(track, "clips", ()):
            if layers is None or clip.layer in layers:
                clips.append(clip)
    return sorted(
        clips, key=lambda clip: (float(clip.dst_in), int(clip.layer), clip.clip_id)
    )


def _edl_audio_clips(plan: Any) -> List[Any]:
    clips = []
    for track in getattr(plan, "audio_tracks", ()):
        clips.extend(getattr(track, "clips", ()))
    return sorted(clips, key=lambda clip: float(clip.dst_in))


def _edl_audio_buses(plan: Any) -> Tuple[List[Any], List[Any]]:
    """Split audio clips by track role: ``music`` to the music bus, all else to dialogue."""
    dialogue: List[Any] = []
    music: List[Any] = []
    for track in getattr(plan, "audio_tracks", ()):
        role = str(getattr(track, "role", "") or "")
        target = music if role == "music" else dialogue
        target.extend(getattr(track, "clips", ()))
    key = lambda clip: float(clip.dst_in)  # noqa: E731
    return sorted(dialogue, key=key), sorted(music, key=key)


def _edl_label(prefix: str, clip: Any, used: set) -> str:
    raw = str(getattr(clip, "clip_id", "") or "")
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw) or "clip"
    label = f"{prefix}{safe}"
    if label in used:
        index = 2
        while f"{label}_{index}" in used:
            index += 1
        label = f"{label}_{index}"
    used.add(label)
    return label


def _edl_audio_clip_chain(
    clip: Any,
    audio_index: int,
    label: str,
    *,
    fades: bool,
    denoise: Optional[str] = None,
) -> str:
    src_in = float(clip.src_in)
    src_out = float(clip.src_out)
    duration = src_out - src_in
    chain = (
        f"[{audio_index}:a]atrim=start={src_in:.6f}:end={src_out:.6f},"
        f"asetpts=PTS-STARTPTS"
    )
    if denoise:
        chain += f",{denoise}"
    gain = float(getattr(clip, "gain_db", 0.0) or 0.0)
    if gain != 0.0:
        chain += f",volume={gain:.3f}dB"
    if fades and duration > 0:
        fade = min(THRESHOLDS["dialogue_fade_ms"] / 1000.0, duration / 2.0)
        chain += f",afade=t=in:st=0:d={fade:.6f}"
        chain += f",afade=t=out:st={duration - fade:.6f}:d={fade:.6f}"
    delay_ms = int(round(float(clip.dst_in) * 1000.0))
    chain += f",adelay={delay_ms}|{delay_ms}[{label}]"
    return chain


def _edl_music_chain(
    clip: Any,
    audio_index: int,
    label: str,
    *,
    timeline: float,
) -> List[str]:
    """The music bed: one pass when it covers the timeline, a loop when it does not.

    A track shorter than the window it must fill is split with ``asplit`` into
    ``N`` copies of the trimmed bed, joined with ``acrossfade`` so every seam
    overlaps instead of butting, then ``atrim``-med to the window. When the bed
    already reaches the window the chain is the single ``atrim`` this engine
    shipped, character for character.
    """
    src_in = float(clip.src_in)
    src_out = float(clip.src_out)
    remaining = max(0.0, float(timeline) - float(clip.dst_in))
    loop = contract.plan_music_loop(
        src_out - src_in,
        remaining,
        float(THRESHOLDS["music_loop_crossfade_sec"]),
    )
    if not loop.looped:
        return [_edl_audio_clip_chain(clip, audio_index, label, fades=False)]

    tail = f"atrim=start=0:end={remaining:.6f},asetpts=PTS-STARTPTS"
    gain = float(getattr(clip, "gain_db", 0.0) or 0.0)
    if gain != 0.0:
        tail += f",volume={gain:.3f}dB"
    delay_ms = int(round(float(clip.dst_in) * 1000.0))
    tail += f",adelay={delay_ms}|{delay_ms}[{label}]"

    copies = loop.copies
    # The split happens after the trim, so every copy is already the bed length.
    stems = [f"{label}loop{index}" for index in range(1, copies + 1)]
    parts = [
        f"[{audio_index}:a]atrim=start={src_in:.6f}:end={src_out:.6f},"
        f"asetpts=PTS-STARTPTS,asplit={copies}" + "".join(f"[{stem}]" for stem in stems)
    ]
    if loop.crossfade_sec > 0:
        previous = stems[0]
        for index, stem in enumerate(stems[1:], start=1):
            merged = f"{label}xfade{index}"
            parts.append(
                f"[{previous}][{stem}]acrossfade=d={loop.crossfade_sec:.6f}[{merged}]"
            )
            previous = merged
        joined = previous
    else:
        inputs = "".join(f"[{stem}]" for stem in stems)
        joined = f"{label}concat"
        parts.append(f"{inputs}concat=n={copies}:v=0:a=1[{joined}]")
    parts.append(f"[{joined}]{tail}")
    return parts


def _edl_bus(
    labels: List[str],
    bus: str,
    duration: float,
) -> List[str]:
    if not labels:
        return []
    if len(labels) == 1:
        parts = [f"[{labels[0]}]anull[{bus}_bus]"]
    else:
        inputs = "".join(f"[{label}]" for label in labels)
        parts = [
            f"{inputs}amix=inputs={len(labels)}:"
            f"dropout_transition=0:normalize=0[{bus}_bus]"
        ]
    parts.append(
        f"[{bus}_bus]apad=whole_dur={duration:.6f},"
        f"atrim=start=0:end={duration:.6f},"
        f"aformat=sample_fmts=fltp:sample_rates=48000:"
        f"channel_layouts=stereo[{bus}]"
    )
    return parts


FILL_POLICIES = ("crop", "fit", "blur")


def _fill_policy(value: Any) -> str:
    """Validate the fill policy: an unknown value is a failure, never a silent fit."""
    policy = str(value or "fit").strip().lower()
    if policy not in FILL_POLICIES:
        raise RenderFailure(f"fill policy {policy!r} not in {'/'.join(FILL_POLICIES)}")
    return policy


def _label_safe(value: str) -> str:
    """A filter-pad label may only carry alphanumerics and underscores."""
    return re.sub(r"[^0-9A-Za-z_]", "_", str(value)) or "clip"


DENOISE_PREFIXES = ("afftdn=", "highpass=")


def _reframe_points(raw: Any) -> List[Tuple[float, float, float, float]]:
    """Usable ``(dst_t, x, y, scale)`` keyframes, ascending, one per instant.

    ``x``/``y`` are the normalised centre of the crop window, ``scale`` its size
    as a fraction of the output canvas. A malformed entry is dropped: the bridge
    declares every skip it derives, so the renderer never invents one.
    """
    points: List[Tuple[float, float, float, float]] = []
    for item in raw or ():
        if not isinstance(item, Mapping):
            continue
        try:
            t = float(item["dst_t"])
            x = float(item["x"])
            y = float(item["y"])
            scale = float(item.get("scale", 1.0))
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (t, x, y, scale)):
            continue
        if scale <= 0.0:
            continue
        points.append(
            (
                t,
                min(1.0, max(0.0, x)),
                min(1.0, max(0.0, y)),
                min(1.0, scale),
            )
        )
    by_t: Dict[float, Tuple[float, float, float, float]] = {}
    for point in sorted(points, key=lambda item: item[0]):
        by_t[round(point[0], 4)] = point
    return [by_t[key] for key in sorted(by_t)]


def _crop_expr(
    points: Sequence[Tuple[float, float, float, float]],
    axis: int,
    offset_s: float,
    symbol: str,
    size: int,
) -> str:
    """Piecewise-linear centre as a ``crop`` offset, clamped to the slack.

    Times are destination times, so the clip-local expression is ``t - dst_in``.
    Commas are escaped as ``\\,`` for the same reason ``vx.autoframe`` escapes
    them: the filtergraph parser unescapes before the expression parser runs.
    """
    tv = "t" if abs(offset_s) < 1e-9 else f"(t-{offset_s:.6f})"
    expr = f"{points[-1][axis]:.6f}*{symbol}"
    for index in range(len(points) - 2, -1, -1):
        t0, t1 = points[index][0], points[index + 1][0]
        v0, v1 = points[index][axis], points[index + 1][axis]
        span = t1 - t0
        slope = 0.0 if span <= 1e-9 else (v1 - v0) / span
        if abs(slope) < 1e-9:
            expr = f"if(lt({tv},{t1:.6f}),{v0:.6f}*{symbol},{expr})"
        else:
            expr = (
                f"if(lt({tv},{t1:.6f}),"
                f"{v0:.6f}*{symbol}{slope:+.6f}*({tv}-{t0:.6f}),{expr})"
            )
    expr = f"clip({expr}-{size / 2.0:.4f},0,{symbol}-{size})"
    return expr.replace(",", "\\,")


def _reframe_geometry(clip: Any, raw: Any, width: int, height: int) -> Optional[str]:
    """Cover-scale, keyframed crop, scale back. ``None`` when nothing animates.

    ``crop`` evaluates its width and height once, so a list whose scales differ
    cannot be expressed and fails loudly instead of silently holding one of
    them. Fewer than two usable instants, or a centre that never moves, is not
    an animation: the clip keeps its fill policy and the bridge has already
    declared why.
    """
    points = _reframe_points(raw)
    if len(points) < 2:
        return None
    scale = points[0][3]
    if any(abs(point[3] - scale) > 1e-3 for point in points):
        raise RenderFailure(
            f"reframe: clip {clip.clip_id!r} carries more than one crop scale"
        )
    if all(
        abs(point[1] - points[0][1]) < 1e-6 and abs(point[2] - points[0][2]) < 1e-6
        for point in points
    ):
        return None
    offset_s = float(clip.dst_in)
    if points[0][0] > offset_s + 1e-9:
        first = points[0]
        points.insert(0, (offset_s, first[1], first[2], first[3]))
    crop_w = max(2, even(float(width) * scale))
    crop_h = max(2, even(float(height) * scale))
    x_expr = _crop_expr(points, 1, offset_s, "iw", crop_w)
    y_expr = _crop_expr(points, 2, offset_s, "ih", crop_h)
    return (
        f"scale={int(width)}:{int(height)}:force_original_aspect_ratio=increase,"
        f"crop={crop_w}:{crop_h}:{x_expr}:{y_expr},"
        f"scale={int(width)}:{int(height)}:flags=bicubic,setsar=1,format=yuv420p"
    )


def _restore_denoise(item: Any) -> Optional[str]:
    """The resolved denoise filter of one clip, or ``None``. Never a guess."""
    if not item:
        return None
    value = item.get("denoise")
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if not text.startswith(DENOISE_PREFIXES):
            raise RenderFailure(f"restore: denoise filter {text!r} not allowed")
        return text
    if value:
        raise RenderFailure("restore: denoise declared without a filter")
    return None


def _restore_stabilize(item: Any) -> bool:
    return bool(item and item.get("stabilize"))


def _restore_windows(
    plan: Any, restore: Optional[Mapping[str, Any]]
) -> Dict[Tuple[str, float, float], Any]:
    """The restore entry of every keyed layer-0 clip, by its destination window.

    A dialogue clip is the 1:1 mirror of an a_roll fragment and carries a
    different ``clip_id`` (``a0_0_0`` against ``v0_0_0``), so the audio chain
    finds its denoise through the geometry the two share. Destination windows
    are unique per plan, so the key cannot collide.
    """
    if not restore:
        return {}
    windows: Dict[Tuple[str, float, float], Any] = {}
    for clip in _edl_clips(plan, layers={0}):
        entry = restore.get(clip.clip_id)
        if entry is not None:
            windows[
                (
                    clip.source_id,
                    round(float(clip.dst_in), 6),
                    round(float(clip.dst_out), 6),
                )
            ] = entry
    return windows


def _restore_for(
    restore: Optional[Mapping[str, Any]],
    windows: Mapping[Tuple[str, float, float], Any],
    clip: Any,
) -> Any:
    if not restore:
        return None
    entry = restore.get(clip.clip_id)
    if entry is not None:
        return entry
    return windows.get(
        (
            clip.source_id,
            round(float(clip.dst_in), 6),
            round(float(clip.dst_out), 6),
        )
    )


def _edl_normalise(
    clip: Any,
    index: int,
    width: int,
    height: int,
    *,
    fill: str = "fit",
    reframe: Any = None,
    restore: Any = None,
) -> str:
    """Trim, normalise geometry and hand back an *unlabelled* chain.

    The caller appends the output pad, so the last filter of this chain must
    stay unlabelled. Three fill policies, each a distinct graph shape:

    * ``fit`` (default) — letterbox/pillarbox: scale to fit, pad the rest;
    * ``crop`` — cover and centre-crop, no bands, source pixels thrown away;
    * ``blur`` — blurred cover background under the whole fitted foreground.

    ``restore`` splices the single-pass stabiliser in before any geometry, and
    ``reframe`` replaces the geometry with the keyframed crop of that clip. Both
    absent leaves the chain byte-identical to the one this engine shipped.
    """
    policy = _fill_policy(fill)
    head = (
        f"[{index}:v]trim=start={float(clip.src_in):.6f}:"
        f"end={float(clip.src_out):.6f},setpts=PTS-STARTPTS,"
    )
    if _restore_stabilize(restore):
        head += "deshake,"
    geometry = _reframe_geometry(clip, reframe, width, height)
    if geometry:
        return head + geometry
    if policy == "fit":
        return (
            head + f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
        )
    if policy == "crop":
        return (
            head + f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2,"
            f"setsar=1,format=yuv420p"
        )
    tag = _label_safe(f"{clip.clip_id}_{index}")
    background, foreground, cover, fitted = (
        f"fbg_{tag}",
        f"ffg_{tag}",
        f"fbc_{tag}",
        f"fbf_{tag}",
    )
    return (
        head + f"split=2[{background}][{foreground}];"
        f"[{background}]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma={FIT_BLUR_SIGMA}[{cover}];"
        f"[{foreground}]scale={width}:{height}:"
        f"force_original_aspect_ratio=decrease[{fitted}];"
        f"[{cover}][{fitted}]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p"
    )


def build_edl_filtergraph(
    plan: Any,
    source_to_index: Mapping[str, int],
    *,
    has_audio: Optional[bool] = None,
    fill: str = "fit",
    subtitles: Optional[str] = None,
    reframe: Optional[Mapping[str, Any]] = None,
    restore: Optional[Mapping[str, Any]] = None,
) -> str:
    """A-Roll is concatenated; audio is split into dialogue and music buses; B-roll is overlaid.

    Pad indices come from ``source_to_index``, never from a hardcoded ``[0:v]``.
    The audio graph is independent of the video concat: each clip is trimmed,
    shifted to its absolute ``dst_in`` with adelay, then summed per bus with
    amix, so overlap and J/L cuts are expressible without duplicating a stream.
    Every audio track whose role is not ``music`` feeds the dialogue bus, which
    keeps older plans working unchanged.

    ``reframe`` keys a layer-0 ``clip_id`` to the keyframes the bridge declared
    for it (``{dst_t, x, y, scale}``); ``restore`` keys a ``clip_id`` to the
    resolved effects (``{denoise: <filter>|None, stabilize: bool}``). Both
    ``None`` — the default — emits the graph this engine shipped, unchanged.
    """
    width, height = (int(value) for value in plan.resolution)
    policy = _fill_policy(fill)
    video_clips = _edl_clips(plan, layers={0})
    audio_clips = _edl_audio_clips(plan)
    audio = bool(audio_clips) if has_audio is None else bool(has_audio)
    if not video_clips:
        raise RenderFailure("EDL has no layer-0 video clips")
    if audio and not audio_clips:
        raise RenderFailure("has_audio=True but the EDL has no audio clips")

    needed = {clip.source_id for clip in video_clips}
    needed |= {clip.source_id for clip in audio_clips}
    missing = sorted(needed - set(source_to_index))
    if missing:
        raise RenderFailure("staged sources missing: " + ", ".join(missing))

    parts: List[str] = []
    for index, clip in enumerate(video_clips):
        index_in = source_to_index[clip.source_id]
        parts.append(
            _edl_normalise(
                clip,
                index_in,
                width,
                height,
                fill=policy,
                reframe=reframe.get(clip.clip_id) if reframe else None,
                restore=restore.get(clip.clip_id) if restore else None,
            )
            + f"[v{index}]"
        )

    if audio:
        dialogue_clips, music_clips = _edl_audio_buses(plan)
        windows = _restore_windows(plan, restore)
        used: set = set()
        dialogue_labels: List[str] = []
        music_labels: List[str] = []
        for clip in dialogue_clips:
            label = _edl_label("ad", clip, used)
            dialogue_labels.append(label)
            parts.append(
                _edl_audio_clip_chain(
                    clip,
                    source_to_index[clip.source_id],
                    label,
                    fades=True,
                    denoise=_restore_denoise(_restore_for(restore, windows, clip)),
                )
            )
        for clip in music_clips:
            label = _edl_label("mu", clip, used)
            music_labels.append(label)
            parts.extend(
                _edl_music_chain(
                    clip,
                    source_to_index[clip.source_id],
                    label,
                    timeline=float(plan.duration_sec),
                )
            )

        duration = float(plan.duration_sec)
        parts.extend(_edl_bus(dialogue_labels, "dialog", duration))
        parts.extend(_edl_bus(music_labels, "music", duration))

        ducking = getattr(plan, "ducking", None)
        if ducking is not None and dialogue_labels and music_labels:
            linear = max(1.0, 10.0 ** (float(ducking.makeup_db) / 20.0))
            parts.append("[dialog]asplit=2[dialog_mix][dialog_sc]")
            parts.append(
                f"[music][dialog_sc]sidechaincompress="
                f"threshold={float(ducking.threshold):.3f}:"
                f"ratio={float(ducking.ratio):.3f}:"
                f"attack={float(ducking.attack_ms):.3f}:"
                f"release={float(ducking.release_ms):.3f}:"
                f"makeup={linear:.3f}[music_duck]"
            )
            parts.append(
                "[dialog_mix][music_duck]amix=inputs=2:"
                "dropout_transition=0:normalize=0[aout]"
            )
        elif dialogue_labels and music_labels:
            parts.append(
                "[dialog][music]amix=inputs=2:dropout_transition=0:normalize=0[aout]"
            )
        elif dialogue_labels:
            parts.append("[dialog]anull[aout]")
        else:
            parts.append("[music]anull[aout]")

    if len(video_clips) == 1:
        base = "[v0]"
    else:
        inputs = "".join(f"[v{i}]" for i in range(len(video_clips)))
        parts.append(f"{inputs}concat=n={len(video_clips)}:v=1:a=0[base]")
        base = "[base]"

    overlay_clips = _edl_clips(plan, layers=set(range(1, 64)))
    for index, clip in enumerate(overlay_clips):
        index_in = source_to_index[clip.source_id]
        duration = float(clip.dst_out) - float(clip.dst_in)
        chain = _edl_normalise(clip, index_in, width, height, fill=policy)
        if clip.transition_in is not None and clip.transition_in.duration_sec > 0:
            fade_in = min(float(clip.transition_in.duration_sec), duration)
            chain += f",fade=t=in:st=0:d={fade_in:.6f}"
        if clip.transition_out is not None and clip.transition_out.duration_sec > 0:
            fade_out = min(float(clip.transition_out.duration_sec), duration)
            chain += (
                f",fade=t=out:st={max(0.0, duration - fade_out):.6f}:d={fade_out:.6f}"
            )
        chain += f",setpts=PTS-STARTPTS+{float(clip.dst_in):.6f}/TB[ov{index}]"
        parts.append(chain)
        parts.append(
            f"{base}[ov{index}]overlay=enable='between(t,"
            f"{float(clip.dst_in):.6f},{float(clip.dst_out):.6f})':"
            f"eof_action=pass:format=auto[base{index}]"
        )
        base = f"[base{index}]"

    if subtitles:
        parts.append(
            f"{base}subtitles={ass_filter_path(subtitles)}:"
            f"fontsdir={fonts_mod.fontsdir_arg()},null[vout]"
        )
    else:
        parts.append(f"{base}null[vout]")
    return ";".join(parts)


def build_edl_command(
    plan: Any,
    staged_sources: Mapping[str, str],
    output_path: str | Path,
    *,
    graph_path: str | Path,
    has_audio: Optional[bool] = None,
    crf: int = 17,
    preset: str = "fast",
    progress_file: Optional[str] = None,
) -> List[str]:
    audio = (
        bool(getattr(plan, "audio_tracks", ()))
        if has_audio is None
        else bool(has_audio)
    )
    args: List[str] = []
    for path in staged_sources.values():
        args += ["-i", str(path)]
    args += ["-filter_complex_script", str(graph_path), "-map", "[vout]"]
    if audio:
        args += ["-map", "[aout]"]
    args += [
        "-c:v",
        "libx264",
        "-crf",
        str(int(crf)),
        "-preset",
        str(preset),
        "-pix_fmt",
        "yuv420p",
    ]
    if audio:
        args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    args += ["-movflags", "+faststart"]
    if progress_file is not None:
        # ``-progress`` is a global option: it must precede the output path.
        args += ["-progress", str(progress_file)]
    args.append(str(output_path))
    return args


def render_edl(
    plan: Any,
    staged_sources: Mapping[str, str],
    output_path: str | Path,
    *,
    workdir: str | Path,
    has_audio: Optional[bool] = None,
    crf: int = 17,
    preset: str = "fast",
    loudness: Optional[Tuple[float, float]] = None,
    progress_file: Optional[str] = None,
    fill: str = "fit",
    subtitles: Optional[str] = None,
    reframe: Optional[Mapping[str, Any]] = None,
    restore: Optional[Mapping[str, Any]] = None,
) -> Any:
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    audio = (
        bool(getattr(plan, "audio_tracks", ()))
        if has_audio is None
        else bool(has_audio)
    )
    if loudness is not None and not audio:
        raise RenderFailure("loudness requested but the EDL has no audio")
    mapping = edl_input_map(staged_sources)
    graph = build_edl_filtergraph(
        plan,
        mapping,
        has_audio=has_audio,
        fill=_fill_policy(fill),
        subtitles=subtitles,
        reframe=reframe,
        restore=restore,
    )
    script = write_script(graph, work / "edl_graph.txt")
    out = Path(output_path)

    if loudness is None:
        args = build_edl_command(
            plan,
            staged_sources,
            out,
            graph_path=script,
            has_audio=has_audio,
            crf=crf,
            preset=preset,
            progress_file=progress_file,
        )
        proc = ff.run_ff(args)
        if not proc.ok:
            raise RenderFailure(f"EDL render failed: {proc.tail(15)}")
        if not out.is_file() or out.stat().st_size == 0:
            raise RenderFailure("EDL render produced no file")
        return out

    pre = work / "final_pre_loudness.mp4"
    args = build_edl_command(
        plan,
        staged_sources,
        pre,
        graph_path=script,
        has_audio=has_audio,
        crf=crf,
        preset=preset,
        progress_file=progress_file,
    )
    proc = ff.run_ff(args)
    if not proc.ok:
        raise RenderFailure(f"EDL pre-loudness render failed: {proc.tail(15)}")
    if not pre.is_file() or pre.stat().st_size == 0:
        raise RenderFailure("EDL pre-loudness render produced no file")

    target_i, target_tp = float(loudness[0]), float(loudness[1])
    stats = _measure_loudness(pre)
    af_args = loudnorm_two_pass(
        stats, target_i=target_i, target_tp=target_tp, target_lra=11.0
    )
    graph04 = f"[0:a]{af_args[1]}[aout]"
    script04 = write_script(graph04, work / "04_loudness.txt")
    # Audio-only pass: the video stream is already final, so -c:v copy is legal
    # here for the same reason as stage 04 of the EditPlan path: this pass does
    # not cut.
    args = [
        "-i",
        str(pre),
        "-filter_complex_script",
        script04,
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(out),
    ]
    proc = ff.run_ff(args, loglevel="error")
    if not proc.ok:
        raise RenderFailure(f"EDL loudness pass failed: {proc.tail(12)}")
    if not out.is_file() or out.stat().st_size == 0:
        raise RenderFailure("EDL loudness pass produced no file")

    payload = {
        "pre": str(pre),
        "final": str(out),
        "loudness": {
            "target": {"i": target_i, "tp": target_tp},
            "measured": stats,
        },
    }
    (work / "edl_render.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return out


def render(
    plan: EditPlan,
    media: dict,
    *,
    precision: str = "full",
    workdir: str | Path,
    subs_path: str | None = None,
    crop_plan: dict | None = None,
    visual: dict | None = None,
    hook_path: str | None = None,
) -> dict:
    """Render ``plan`` and return a result dict.

    Stages (fixed order):
      01 trim/atrim + concat          (only when stabilization needs a source)
      02 vidstabdetect -> transforms.trf
      03 full graph -> stage/03_video.mp4
      04 loudnorm second pass -> final (faststart), only when the plan enabled
         loudnorm; otherwise 04 remux stream-copies stage 03 to final

    ``visual`` is the adaptive visual decision dict (see
    :func:`build_filtergraph`); it is passed through to the graph untouched.

    Raises ``RenderFailure`` on any non-zero ffmpeg exit.
    """
    plan_dict = plan.to_dict() if hasattr(plan, "to_dict") else plan
    shape_errors = contract.validate_evidence_shape(plan_dict)
    if shape_errors:
        raise RenderFailure(
            "plan without evidence: " + "; ".join(str(e) for e in shape_errors)
        )

    precision = _precision(plan, precision)
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    stage_dir = work / "stage"
    stage_dir.mkdir(exist_ok=True)

    source = media.get("path") or (plan.output or {}).get("source")
    if not source:
        raise RenderFailure("media dict has no 'path' and plan.output has no 'source'")
    source_path = Path(str(source))
    if not source_path.exists():
        raise RenderFailure(f"source not found: {source_path}")

    fps = _num(media.get("fps"), DEFAULT_FPS) or DEFAULT_FPS
    has_audio = bool(media.get("has_audio", True))
    stages: List[str] = []
    scripts: List[str] = []

    def _record(path: str) -> None:
        scripts.append(Path(path).read_text(encoding="utf-8"))

    # -- stage 02: stabilization detect pass -------------------------------- #
    if _enabled(plan, "stabilize"):
        ff.require("vidstabdetect")
        ff.require("vidstabtransform")
        trim_graph = build_trim_graph(plan.cuts, fps=fps, with_audio=False)
        trim_script = write_script(trim_graph, stage_dir / "01_trim.txt")
        _record(trim_script)
        trim_out = stage_dir / "01_trim.mp4"
        proc = ff.run_ff(
            [
                "-i",
                str(source_path),
                "-filter_complex_script",
                trim_script,
                "-map",
                "[vout]",
                *_encode_args(plan, precision),
                str(trim_out),
            ]
        )
        if not proc.ok:
            raise RenderFailure(f"stage 01 (trim) failed: {proc.tail(12)}")
        stages.append("01_trim")

        detect_graph = _detect_graph(plan)
        detect_script = write_script(detect_graph, stage_dir / "02_detect.txt")
        _record(detect_script)
        with _in_dir(work):
            proc = ff.run_ff(
                [
                    "-i",
                    str(trim_out),
                    "-filter_complex_script",
                    detect_script,
                    "-map",
                    "[vo]",
                    "-f",
                    "null",
                    "-",
                ],
                loglevel="info",
            )
        if not proc.ok:
            raise RenderFailure(f"stage 02 (vidstabdetect) failed: {proc.tail(12)}")
        if not (work / TRF_NAME).exists():
            raise RenderFailure(f"vidstabdetect produced no {TRF_NAME} in {work}")
        stages.append("02_detect")

    # -- stage 03: the full graph ------------------------------------------- #
    graph = build_filtergraph(
        plan,
        media,
        subs_path=subs_path,
        precision=precision,
        visual=visual,
        hook_path=hook_path,
    )
    if crop_plan:
        # Rebuild with the crop plan folded in (build_filtergraph is crop-free by
        # contract; the crop plan is a render-time argument).
        graph = _graph_with_crop(
            plan, media, crop_plan, subs_path, precision, visual, hook_path
        )
    script = write_script(graph, stage_dir / "03_graph.txt")
    _record(script)
    stage03 = stage_dir / "03_video.mp4"

    args: List[str] = [
        "-i",
        str(source_path),
        "-filter_complex_script",
        script,
        "-map",
        "[vout]",
    ]
    if has_audio:
        args += ["-map", "[aout]"]
    args += _encode_args(plan, precision)
    if has_audio:
        args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    args.append(str(stage03))

    if _enabled(plan, "stabilize"):
        with _in_dir(work):
            proc = ff.run_ff(args)
    else:
        proc = ff.run_ff(args)
    if not proc.ok:
        raise RenderFailure(f"stage 03 (graph) failed: {proc.tail(15)}")
    stages.append("03_graph")

    # -- stage 04: loudnorm second pass, or a plain remux ------------------- #
    final = work / f"final_{precision}.mp4"
    target_i, target_tp, target_lra = loudness_target(plan)
    stats: Dict[str, float] = {}
    run_loudnorm = has_audio and _enabled(plan, "loudnorm")
    if run_loudnorm:
        stats = _measure_loudness(stage03)
        run_loudnorm = not _silent_loudness(stats)
    if run_loudnorm:
        # Two passes: measure stage 03, then apply the measured values. The
        # filter graph goes to a script file (the project's hard rule: never
        # -af), so the executed declaration is the one on disk.
        af_args = loudnorm_two_pass(
            stats, target_i=target_i, target_tp=target_tp, target_lra=target_lra
        )
        graph04 = f"[0:a]{af_args[1]}[aout]"
        script04 = write_script(graph04, work / "04_loudnorm.txt")
        _record(script04)
        args = [
            "-i",
            str(stage03),
            "-filter_complex_script",
            script04,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",  # audio-only pass: the video stream is already final
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            str(final),
        ]
        proc = ff.run_ff(args, loglevel="error")
        if not proc.ok:
            raise RenderFailure(f"stage 04 (loudnorm) failed: {proc.tail(12)}")
        stages.append("04_loudnorm")
    else:
        # No loudnorm finding: do not invent a normalization. Stream copy is
        # allowed here because this stage does not cut.
        args = ["-i", str(stage03), "-map", "0:v:0"]
        if has_audio:
            args += ["-map", "0:a:0"]
        args += ["-c", "copy", "-movflags", "+faststart", str(final)]
        proc = ff.run_ff(args, loglevel="error")
        if not proc.ok:
            raise RenderFailure(f"stage 04 (remux) failed: {proc.tail(12)}")
        stages.append("04_remux")

    if not final.exists():
        raise RenderFailure(f"stage 04 produced no output: {final}")
    verify_treatments(
        plan, scripts, media=media, precision=precision, crop_plan=crop_plan
    )

    frames = _frame_count(final)
    out_w, out_h = output_size(_aspect(plan), precision)
    return {
        "path": str(final),
        "precision": precision,
        "size": [out_w, out_h],
        "frames": frames,
        "expected_frames": expected_frames(
            plan.cuts or [{"in": 0.0, "out": _num(media.get("duration"), 0.0)}],
            fps,
        ),
        "fps": fps,
        "has_audio": has_audio,
        "loudness": _measure_loudness(final) if has_audio else stats,
        "target": {"i": target_i, "tp": target_tp, "lra": target_lra},
        "stages": stages,
        "graph": graph,
        "script": script,
        "stage03": str(stage03),
    }


def _graph_with_crop(
    plan: EditPlan,
    media: Dict[str, Any],
    crop_plan: Dict[str, Any],
    subs_path: Optional[str],
    precision: str,
    visual: Optional[Dict[str, Any]] = None,
    hook_path: Optional[str] = None,
) -> str:
    """Same graph as ``build_filtergraph`` but with an explicit crop plan."""
    return build_filtergraph(
        plan,
        media,
        subs_path=subs_path,
        precision=precision,
        crop_plan=crop_plan,
        visual=visual,
        hook_path=hook_path,
    )


# --------------------------------------------------------------------------- #
# smoke entry
# --------------------------------------------------------------------------- #


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Smoke entry: print the graph for a synthetic 2-cut plan."""
    argv = list(sys.argv[1:] if argv is None else argv)
    media = {
        "path": argv[0] if argv else "input.mp4",
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "duration": 8.0,
        "has_audio": True,
    }
    plan = EditPlan(
        output={"aspect": "9:16", "loudness": "social"},
        cuts=[{"in": 0.5, "out": 2.5}, {"in": 4.0, "out": 6.0}],
        render={"precision": "full"},
    )
    graph = build_filtergraph(plan, media)
    print(graph)
    print("---")
    print("expected_frames:", expected_frames(plan.cuts, media["fps"]))
    print("trim graph:", build_trim_graph(plan.cuts, fps=30.0, fade_ms=FADE_MS))
    print(
        "loudnorm:",
        loudnorm_two_pass(
            {
                "input_i": -18.2,
                "input_tp": -2.1,
                "input_lra": 6.4,
                "input_thresh": -28.5,
                "target_offset": 0.1,
            }
        ),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
