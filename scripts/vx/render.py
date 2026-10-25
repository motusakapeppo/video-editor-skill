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
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import ffmpeg as ff
from .contract import (
    LOUDNESS_TARGETS,
    EditPlan,
    RenderFailure,
    even,
    expected_frames,
    output_size,
    target_crop_size,
)
from .winpath import ass_filter_path, escape_expr

__all__ = [
    "build_filtergraph",
    "write_script",
    "render",
    "loudnorm_two_pass",
    "swap_proxy_to_full",
    "build_trim_graph",
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


def _loudness_target(plan: EditPlan) -> Tuple[float, float, float]:
    """(I, TP, LRA) from the plan, defaulting to the social profile."""
    raw = (plan.output or {}).get("loudness") or (plan.render or {}).get("loudness")
    if isinstance(raw, str) and raw in LOUDNESS_TARGETS:
        i, tp = LOUDNESS_TARGETS[raw]
        return float(i), float(tp), 11.0
    if isinstance(raw, dict):
        return (
            _num(raw.get("i", raw.get("target_i")), -14.0),
            _num(raw.get("tp", raw.get("target_tp")), -1.0),
            _num(raw.get("lra", raw.get("target_lra")), 11.0),
        )
    i, tp = LOUDNESS_TARGETS["social"]
    return float(i), float(tp), 11.0


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


def _track_expr(boxes: Sequence[Dict[str, Any]], axis: str) -> Optional[str]:
    """Step-hold expression over output time for a tracked crop axis."""
    points = sorted(
        (_num(b.get("t")), _num(b.get(axis))) for b in boxes if b.get(axis) is not None
    )
    if not points:
        return None
    expr = str(int(round(points[-1][1])))
    for t, value in reversed(points[:-1]):
        expr = f"if(lt(t,{t:.3f}),{int(round(value))},{expr})"
    return escape_expr(expr)


def _crop_scale(
    plan: EditPlan,
    media: Dict[str, Any],
    crop_plan: Optional[Dict[str, Any]],
    precision: str,
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
        crop_w = (
            int(_num(crop_plan.get("crop_w"), 0))
            or target_crop_size(src_w, src_h, aspect)[0]
        )
        crop_h = (
            int(_num(crop_plan.get("crop_h"), 0))
            or target_crop_size(src_w, src_h, aspect)[1]
        )
        crop_w = min(even(crop_w), even(src_w))
        crop_h = min(even(crop_h), even(src_h))
        x_expr = y_expr = None
        if crop_plan.get("boxes") and crop_plan.get("mode") == "track":
            x_expr = _track_expr(crop_plan["boxes"], "x")
            y_expr = _track_expr(crop_plan["boxes"], "y")
        elif crop_plan.get("boxes") and crop_plan.get("mode") == "lock":
            first = crop_plan["boxes"][0]
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
    parts = [f"{k}={_num(params[k]):.4f}" for k in keys if k in params]
    if not parts:
        return None
    return "eq=" + ":".join(parts)


def _audio_chain(plan: EditPlan) -> str:
    parts: List[str] = []
    if _enabled(plan, "denoise"):
        nf = _num(_params(plan, "denoise").get("noise_floor"), -25.0)
        parts.append(f"afftdn=nf={nf:.1f}")
    parts.append("aresample=48000")
    return ",".join(parts)


# --------------------------------------------------------------------------- #
# public: graph builders
# --------------------------------------------------------------------------- #


def build_filtergraph(
    plan: EditPlan,
    media: dict,
    *,
    subs_path: str | None = None,
    precision: str = "full",
    crop_plan: dict | None = None,
) -> str:
    """Return the complete ``-filter_complex`` graph for one render pass.

    Outputs are labelled ``[vout]`` and, when the source has audio, ``[aout]``.
    The graph is a single line: it is written verbatim to a ``.txt`` and passed
    through ``-filter_complex_script`` (the Windows 8191-char limit is real).
    """
    precision = _precision(plan, precision)
    fps = _num(media.get("fps"), DEFAULT_FPS) or DEFAULT_FPS
    has_audio = bool(media.get("has_audio", True))
    segments = _segments(plan, media)

    parts: List[str] = []
    for index, (start, end) in enumerate(segments):
        parts.append(
            f"[0:v]trim=start={start:.6f}:end={end:.6f},"
            f"setpts=PTS-STARTPTS,setsar=1,format=yuv420p[v{index}]"
        )
        if has_audio:
            parts.append(
                f"[0:a]atrim=start={start:.6f}:end={end:.6f},"
                f"asetpts=PTS-STARTPTS[a{index}]"
            )

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
        pre.append(f"vidstabtransform=input={TRF_NAME}:smoothing=10:zoom=0:optzoom=1")

    post: List[str] = []
    zoom = _zoom_chain(plan, precision)
    if zoom:
        post.append(zoom)
    eq = _eq_chain(plan)
    if eq:
        post.append(eq)
    if subs_path:
        post.append(f"subtitles={ass_filter_path(subs_path)}")

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
        chain = pre + [_crop_scale(plan, media, crop_plan, precision)] + post
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
            if fade > 0:
                span = end - start
                audio += f",afade=t=in:st=0:d={fade:.3f}"
                if span > fade:
                    audio += f",afade=t=out:st={span - fade:.6f}:d={fade:.3f}"
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
    usable = all(
        measured[key] not in (None, "", "nan")
        for key in ("measured_I", "measured_TP", "measured_LRA", "measured_thresh")
    )
    base = f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}"
    if usable:
        base += (
            f":measured_I={measured['measured_I']}"
            f":measured_TP={measured['measured_TP']}"
            f":measured_LRA={measured['measured_LRA']}"
            f":measured_thresh={measured['measured_thresh']}"
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


def _encode_args(plan: EditPlan, precision: str) -> List[str]:
    render_cfg = plan.render or {}
    if precision == "proxy":
        crf = int(_num(render_cfg.get("proxy_crf"), 23))
        preset = str(render_cfg.get("proxy_preset") or "veryfast")
    else:
        crf = int(_num(render_cfg.get("crf"), 18))
        preset = str(render_cfg.get("preset") or "medium")
    return [
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-preset",
        preset,
        "-pix_fmt",
        "yuv420p",
    ]


def render(
    plan: EditPlan,
    media: dict,
    *,
    precision: str = "full",
    workdir: str | Path,
    subs_path: str | None = None,
    crop_plan: dict | None = None,
) -> dict:
    """Render ``plan`` and return a result dict.

    Stages (fixed order):
      01 trim/atrim + concat          (only when stabilization needs a source)
      02 vidstabdetect -> transforms.trf
      03 full graph -> stage/03_video.mp4
      04 loudnorm second pass -> final (faststart)

    Raises ``RenderFailure`` on any non-zero ffmpeg exit.
    """
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

    # -- stage 02: stabilization detect pass -------------------------------- #
    if _enabled(plan, "stabilize"):
        ff.require("vidstabdetect")
        ff.require("vidstabtransform")
        trim_graph = build_trim_graph(plan.cuts, fps=fps, with_audio=False)
        trim_script = write_script(trim_graph, stage_dir / "01_trim.txt")
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

        detect_graph = (
            f"[0:v]vidstabdetect=shakiness=5:accuracy=9:result={TRF_NAME}[vo]"
        )
        detect_script = write_script(detect_graph, stage_dir / "02_detect.txt")
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
    graph = build_filtergraph(plan, media, subs_path=subs_path, precision=precision)
    if crop_plan:
        # Rebuild with the crop plan folded in (build_filtergraph is crop-free by
        # contract; the crop plan is a render-time argument).
        graph = _graph_with_crop(plan, media, crop_plan, subs_path, precision)
    script = write_script(graph, stage_dir / "03_graph.txt")
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

    # -- stage 04: loudnorm second pass ------------------------------------- #
    final = work / f"final_{precision}.mp4"
    target_i, target_tp, target_lra = _loudness_target(plan)
    stats: Dict[str, float] = {}
    if has_audio:
        stats = _measure_loudness(stage03)
        args = [
            "-i",
            str(stage03),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            *loudnorm_two_pass(
                stats, target_i=target_i, target_tp=target_tp, target_lra=target_lra
            ),
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
        args = [
            "-i",
            str(stage03),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-movflags",
            "+faststart",
            str(final),
        ]
        proc = ff.run_ff(args, loglevel="error")
        if not proc.ok:
            raise RenderFailure(f"stage 04 (remux) failed: {proc.tail(12)}")
        stages.append("04_remux")

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
) -> str:
    """Same graph as ``build_filtergraph`` but with an explicit crop plan."""
    return build_filtergraph(
        plan,
        media,
        subs_path=subs_path,
        precision=precision,
        crop_plan=crop_plan,
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
