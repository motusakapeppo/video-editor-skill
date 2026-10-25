"""vx.autoframe — smoothed virtual-camera crop plan + ffmpeg crop expression.

The plan is a list of ``{t, x, y}`` keyframes in *source* pixels. ``crop_w`` /
``crop_h`` are fixed for the whole clip: ffmpeg evaluates ``crop`` width/height
once, so animating them is impossible. Only ``x`` / ``y`` move.

Smoothing chain (autoclip, MIT): One Euro filter on the target centre, then a
12 px dead zone, then a 260 px/s velocity cap. Scene cuts split the plan so no
interpolation ever crosses a cut.

The emitted expression is piecewise-linear, built backwards, with commas
escaped as ``\\,`` (the filtergraph parser unescapes them before the expression
parser sees them — quoting instead would leave a literal backslash).

``cv2`` is never imported here: ``import vx.autoframe`` works without OpenCV.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .contract import (
    ASPECTS,
    PROXY_WIDTH,
    CropPlan,
    FaceTracks,
    VxError,
    clamp,
    even,
    output_size,
    safe_rect,
    target_crop_size,
)

# --------------------------------------------------------------------------- #
# constants introduced by this module (not part of the frozen contract)
# --------------------------------------------------------------------------- #

ONE_EURO_MIN_CUTOFF = 0.6
ONE_EURO_BETA = 0.02
ONE_EURO_D_CUTOFF = 1.0

DEAD_ZONE_PX = 12.0  # hold the crop until the target moves this far
MAX_VELOCITY_PX_S = 260.0  # hard pan speed limit

LOCK_FACE_WIDTH_RATIO = 0.55  # face wider than this * crop_w -> lock, no pan
FIT_SPAN_RATIO = 0.95  # faces spread wider than this * src_w -> blur-pad
DEFAULT_EYE_LINE = 0.38  # eyes land at this fraction of crop height
SAFE_LEFT_BIAS = 0.02  # extra left bias inside the safe box (right rail)

MAX_KEYFRAMES = 48  # per segment, per axis
DECIMATE_TOLERANCE_PX = 1.0  # drop a keyframe if it deviates less than this
FIT_BLUR_SIGMA = 20

# Reference canvases the frozen SAFE_ZONES were authored on. The zones are
# pixel rects, so they must be normalised before they can be applied to a crop
# of arbitrary size.
_SAFE_REF = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "16:9": (1920, 1080),
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _even_coord(value: float) -> int:
    """Nearest even integer <= value, never negative.

    ``contract.even`` floors at 2 because it is meant for *sizes*; a crop
    offset of 0 is legal, so coordinates get their own quantiser.
    """
    i = int(math.floor(float(value)))
    if i % 2:
        i -= 1
    return max(0, i)


def _safe_fractions(aspect: str) -> Tuple[float, float, float, float]:
    x1, x2, y1, y2 = safe_rect(aspect)
    rw, rh = _SAFE_REF.get(aspect, (1080, 1920))
    return x1 / rw, x2 / rw, y1 / rh, y2 / rh


def _note_values(plan: CropPlan, key: str) -> List[float]:
    out: List[float] = []
    for note in plan.notes or []:
        if note.startswith(key + "="):
            try:
                out.append(float(note.split("=", 1)[1]))
            except ValueError:
                continue
    return out


def _estimate_fps(tracks: Sequence[Dict[str, Any]]) -> float:
    """Median sample rate of the tracks. 0.0 when timestamps are unusable."""
    stamps = sorted(
        {round(float(s["t"]), 4) for track in tracks for s in track.get("samples", [])}
    )
    if len(stamps) < 2:
        return 0.0
    deltas = sorted(b - a for a, b in zip(stamps, stamps[1:]) if b - a > 1e-6)
    if not deltas:
        return 0.0
    median = deltas[len(deltas) // 2]
    return 1.0 / median if median > 0 else 0.0


class _OneEuro:
    """One Euro filter (Casiez et al.) — low jitter, low lag."""

    def __init__(
        self,
        min_cutoff: float = ONE_EURO_MIN_CUTOFF,
        beta: float = ONE_EURO_BETA,
        d_cutoff: float = ONE_EURO_D_CUTOFF,
    ) -> None:
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x: Optional[float] = None
        self._dx = 0.0
        self._t: Optional[float] = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t: float) -> float:
        if self._x is None or self._t is None:
            self._x, self._t = x, t
            return x
        dt = t - self._t
        if dt <= 0:
            dt = 1e-3
        dx = (x - self._x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self._dx = a_d * dx + (1.0 - a_d) * self._dx
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        a = self._alpha(cutoff, dt)
        self._x = a * x + (1.0 - a) * self._x
        self._t = t
        return self._x


def _split_series(
    series: Sequence[Tuple[float, float, float]], cuts: Sequence[float]
) -> List[List[Tuple[float, float, float]]]:
    bounds = [0.0] + sorted(cuts) + [float("inf")]
    out: List[List[Tuple[float, float, float]]] = []
    for a, b in zip(bounds, bounds[1:]):
        seg = [p for p in series if a <= p[0] < b]
        if seg:
            out.append(seg)
    return out


def _smooth(
    series: Sequence[Tuple[float, float, float]], cuts: Sequence[float]
) -> List[Tuple[float, float, float]]:
    """One Euro -> dead zone -> velocity cap, filter state reset at every cut."""
    out: List[Tuple[float, float, float]] = []
    for seg in _split_series(series, cuts):
        fx = _OneEuro()
        fy = _OneEuro()
        last_x: Optional[float] = None
        last_y: Optional[float] = None
        prev_t: Optional[float] = None
        for t, cx, cy in seg:
            sx = fx(cx, t)
            sy = fy(cy, t)
            if last_x is None or last_y is None:
                last_x, last_y = sx, sy
            else:
                if abs(sx - last_x) < DEAD_ZONE_PX:
                    sx = last_x
                if abs(sy - last_y) < DEAD_ZONE_PX:
                    sy = last_y
                if prev_t is not None and t > prev_t:
                    max_step = MAX_VELOCITY_PX_S * (t - prev_t)
                    if abs(sx - last_x) > max_step:
                        sx = last_x + math.copysign(max_step, sx - last_x)
                    if abs(sy - last_y) > max_step:
                        sy = last_y + math.copysign(max_step, sy - last_y)
                last_x, last_y = sx, sy
            prev_t = t
            out.append((t, sx, sy))
    return out


def _max_deviation(
    points: Sequence[Tuple[float, float, float]], i: int, j: int
) -> float:
    t0, x0, y0 = points[i]
    t1, x1, y1 = points[j]
    dt = t1 - t0
    worst = 0.0
    for k in range(i + 1, j):
        t, x, y = points[k]
        if dt <= 1e-9:
            ex, ey = abs(x - x0), abs(y - y0)
        else:
            f = (t - t0) / dt
            ex = abs(x - (x0 + (x1 - x0) * f))
            ey = abs(y - (y0 + (y1 - y0) * f))
        worst = max(worst, ex, ey)
    return worst


def _decimate(
    points: Sequence[Tuple[float, float, float]],
    tolerance: float = DECIMATE_TOLERANCE_PX,
) -> List[Tuple[float, float, float]]:
    """Drop keyframes whose straight-line deviation is below `tolerance` px."""
    n = len(points)
    if n <= 2:
        return list(points)
    keep = [points[0]]
    i = 0
    while i < n - 1:
        best = i + 1
        j = i + 2
        while j < n and _max_deviation(points, i, j) <= tolerance:
            best = j
            j += 1
        keep.append(points[best])
        i = best
    return keep


def _cap(
    points: Sequence[Tuple[float, float, float]], limit: int = MAX_KEYFRAMES
) -> List[Tuple[float, float, float]]:
    n = len(points)
    if n <= limit:
        return list(points)
    step = (n - 1) / float(limit - 1)
    idxs = sorted({int(round(i * step)) for i in range(limit)})
    return [points[i] for i in idxs]


def _num(value: float) -> str:
    return str(int(round(float(value))))


def _piecewise(points: Sequence[Tuple[float, float]], tv: str) -> str:
    """Nested if() built backwards: if(lt(tv,t1),v0+s0*(tv-t0), ... v_last)."""
    if not points:
        return "0"
    if len(points) == 1:
        return _num(points[0][1])
    expr = _num(points[-1][1])
    for i in range(len(points) - 2, -1, -1):
        t0, v0 = points[i]
        t1, v1 = points[i + 1]
        dt = t1 - t0
        slope = 0.0 if dt <= 1e-9 else (v1 - v0) / dt
        if abs(slope) < 1e-9:
            expr = f"if(lt({tv},{t1:.4f}),{_num(v0)},{expr})"
        else:
            expr = (
                f"if(lt({tv},{t1:.4f}),{_num(v0)}{slope:+.4f}*({tv}-{t0:.4f}),{expr})"
            )
    return expr


def _axis_expr(
    segments: Sequence[Tuple[float, float, List[Tuple[float, float]]]],
    offset_s: float,
) -> str:
    """segments: (seg_start, seg_end, [(t, value)...]) ascending, non-empty."""
    tv = "t" if abs(offset_s) < 1e-9 else f"(t-{offset_s:.3f})"
    if not segments:
        return "0"
    parts: List[Tuple[float, str]] = []
    for seg_start, seg_end, pts in segments:
        local = list(pts)
        if local and local[0][0] > seg_start:
            local.insert(0, (seg_start, local[0][1]))
        parts.append((seg_end, _piecewise(local, tv)))
    expr = parts[-1][1]
    for seg_end, sub in reversed(parts[:-1]):
        expr = f"if(lt({tv},{seg_end:.4f}),{sub},{expr})"
    return expr.replace(",", "\\,")


def _split_boxes(
    boxes: Sequence[Dict[str, float]], cuts: Sequence[float]
) -> List[Tuple[float, float, List[Tuple[float, float, float]]]]:
    last_t = max(float(b["t"]) for b in boxes)
    bounds = [0.0] + sorted(cuts) + [last_t + 1e-6]
    out: List[Tuple[float, float, List[Tuple[float, float, float]]]] = []
    for a, b in zip(bounds, bounds[1:]):
        pts = [
            (float(box["t"]), _even_coord(box["x"]), _even_coord(box["y"]))
            for box in boxes
            if a <= float(box["t"]) < b
        ]
        if pts:
            out.append((a, b, pts))
    return out


def _group_track(tracks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Synthetic track: per-timestamp mean box of every visible face."""
    by_t: Dict[float, List[Dict[str, Any]]] = {}
    for track in tracks:
        for s in track.get("samples", []):
            by_t.setdefault(round(float(s["t"]), 4), []).append(s)
    samples: List[Dict[str, float]] = []
    for t in sorted(by_t):
        group = by_t[t]
        n = float(len(group))
        samples.append(
            {
                "t": t,
                "x": sum(float(s["x"]) for s in group) / n,
                "y": sum(float(s["y"]) for s in group) / n,
                "w": sum(float(s["w"]) for s in group) / n,
                "h": sum(float(s["h"]) for s in group) / n,
                "eye_y": sum(float(s["eye_y"]) for s in group) / n,
                "mar": sum(float(s["mar"]) for s in group) / n,
            }
        )
    return {
        "id": "group",
        "samples": samples,
        "start": samples[0]["t"],
        "end": samples[-1]["t"],
    }


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def build_crop_plan(
    tracks: FaceTracks,
    src_w: int,
    src_h: int,
    *,
    aspect: str = "9:16",
    eye_line: float = DEFAULT_EYE_LINE,
    scene_cuts: Optional[List[float]] = None,
    precision: str = "full",
) -> CropPlan:
    """Turn face tracks into a smoothed virtual-camera crop plan."""
    src_w = int(src_w)
    src_h = int(src_h)
    if src_w <= 0 or src_h <= 0:
        raise VxError("src_w and src_h must be positive")
    if aspect not in ASPECTS:
        raise VxError(f"unknown aspect {aspect!r}; known: {sorted(ASPECTS)}")
    if precision not in ("full", "proxy"):
        raise VxError(f"precision must be 'full' or 'proxy', got {precision!r}")
    eye_line = clamp(float(eye_line), 0.0, 1.0)

    # --- geometry, always computed in source pixels ------------------------ #
    cw_f, ch_f = target_crop_size(src_w, src_h, aspect)
    ow, oh = output_size(aspect, "full")
    fx1, fx2, fy1, fy2 = _safe_fractions(aspect)
    safe_x1, safe_x2 = fx1 * cw_f, fx2 * cw_f
    safe_y1, safe_y2 = fy1 * ch_f, fy2 * ch_f
    safe_cx = (safe_x1 + safe_x2) / 2.0
    bias = SAFE_LEFT_BIAS * cw_f

    fps = _estimate_fps(tracks.tracks or [])
    cuts = sorted({round(float(c), 4) for c in (scene_cuts or []) if float(c) > 0.0})

    notes: List[str] = [
        f"fps={fps:.6g}",
        f"eye_line={eye_line:.4f}",
        f"precision={precision}",
    ]
    for cut in cuts:
        notes.append(f"seg={cut:.4f}")

    def map_xy(cx: float, cy: float) -> Tuple[float, float]:
        """Place the subject at the safe-box centre (biased left), eye on line."""
        want_x = cx - safe_cx + bias
        lo_x = max(0.0, cx - safe_x2)
        hi_x = min(src_w - cw_f, cx - safe_x1)
        if lo_x <= hi_x:
            x = clamp(want_x, lo_x, hi_x)
        else:
            x = clamp(want_x, 0.0, src_w - cw_f)

        want_y = cy - eye_line * ch_f
        lo_y = max(0.0, cy - safe_y2)
        hi_y = min(src_h - ch_f, cy - safe_y1)
        if lo_y <= hi_y:
            y = clamp(want_y, lo_y, hi_y)
        else:
            y = clamp(want_y, 0.0, src_h - ch_f)
        return x, y

    trs = [t for t in (tracks.tracks or []) if t.get("samples")]
    framing = tracks.framing or "center"
    span = float(tracks.span_x_ratio or 0.0)

    mode = "center"
    subject: Optional[Dict[str, Any]] = None
    if not trs:
        mode = "center"
        notes.append("subject=none")
    elif framing == "fit" or span > FIT_SPAN_RATIO:
        mode = "fit"
        notes.append(f"subject=fit span_x_ratio={span:.3f}")
    else:
        if len(trs) == 1:
            subject = trs[0]
        else:
            duration = max(float(t["end"]) for t in trs)
            from .faces import select_speaker_track  # noqa: PLC0415

            subject = select_speaker_track(trs, duration=duration)
            if subject is None:
                subject = _group_track(trs)
                notes.append("speaker=ambiguous, using group centroid")
        widths = [float(s["w"]) for s in subject["samples"]]
        mean_w = sum(widths) / len(widths) if widths else 0.0
        if mean_w > LOCK_FACE_WIDTH_RATIO * cw_f:
            mode = "lock"
            notes.append(
                f"lock: face width {mean_w:.1f} > "
                f"{LOCK_FACE_WIDTH_RATIO:.2f}*crop_w ({LOCK_FACE_WIDTH_RATIO * cw_f:.1f})"
            )
        else:
            mode = "track"
        notes.append(f"subject={subject.get('id', 'group')}")

    # --- keyframes in source pixels ---------------------------------------- #
    boxes_src: List[Tuple[float, float, float]] = []
    if mode in ("center", "fit"):
        boxes_src = [(0.0, (src_w - cw_f) / 2.0, (src_h - ch_f) / 2.0)]
    elif mode == "lock":
        assert subject is not None
        samples = subject["samples"]
        cx = sum(float(s["x"]) + float(s["w"]) / 2.0 for s in samples) / len(samples)
        cy = sum(float(s["eye_y"]) for s in samples) / len(samples)
        x, y = map_xy(cx, cy)
        boxes_src = [(0.0, x, y)]
    else:
        assert subject is not None
        series = sorted(
            (
                float(s["t"]),
                float(s["x"]) + float(s["w"]) / 2.0,
                float(s["eye_y"]),
            )
            for s in subject["samples"]
        )
        smoothed = _smooth(series, cuts)
        mapped = [(t, *map_xy(cx, cy)) for (t, cx, cy) in smoothed]
        mapped = _cap(_decimate(mapped))
        boxes_src = mapped

    # --- resolution-relative output ---------------------------------------- #
    if precision == "proxy":
        scale = PROXY_WIDTH / float(src_w)
        plan_w = even(src_w * scale)
        plan_h = even(src_h * scale)
        cw = even(cw_f * scale)
        ch = even(ch_f * scale)
    else:
        scale = 1.0
        plan_w, plan_h = src_w, src_h
        cw, ch = cw_f, ch_f

    boxes: List[Dict[str, float]] = []
    for t, x, y in boxes_src:
        px = _even_coord(clamp(x * scale, 0.0, plan_w - cw))
        py = _even_coord(clamp(y * scale, 0.0, plan_h - ch))
        boxes.append({"t": round(float(t), 4), "x": px, "y": py})

    headroom_pct = 0.0
    if mode in ("track", "lock") and subject is not None:
        eye = sum(float(s["eye_y"]) for s in subject["samples"]) / len(
            subject["samples"]
        )
        y_mean = sum(b["y"] for b in boxes) / len(boxes)
        headroom_pct = clamp((eye * scale - y_mean) / ch * 100.0, 0.0, 100.0)

    return CropPlan(
        mode=mode,
        aspect=aspect,
        src_w=plan_w,
        src_h=plan_h,
        crop_w=cw,
        crop_h=ch,
        out_w=output_size(aspect, precision)[0],
        out_h=output_size(aspect, precision)[1],
        boxes=boxes,
        headroom_pct=round(headroom_pct, 2),
        safe=True,
        notes=notes,
    )


def crop_expression(plan: CropPlan, *, offset_s: float = 0.0) -> Dict[str, str]:
    """ffmpeg ``crop`` x/y expressions. Constant when ``t`` cannot be trusted."""
    boxes = list(plan.boxes or [])
    if not boxes:
        return {
            "x": str(_even_coord((plan.src_w - plan.crop_w) / 2.0)),
            "y": str(_even_coord((plan.src_h - plan.crop_h) / 2.0)),
        }

    fps = _note_values(plan, "fps")
    fps_value = fps[0] if fps else 0.0
    cuts = _note_values(plan, "seg")
    segments = _split_boxes(boxes, cuts)

    # NaN trap: no fps, no time axis, or a static plan -> single numeric value.
    static = (
        plan.mode in ("fit", "lock", "center")
        or len(boxes) < 2
        or fps_value <= 0.0
        or not segments
    )

    out: Dict[str, str] = {}
    for axis, index in (("x", 1), ("y", 2)):
        values = {_even_coord(b[axis]) for b in boxes}
        if static or len(values) == 1:
            out[axis] = str(_even_coord(boxes[0][axis]))
            continue
        axis_segments = [
            (a, b, [(p[0], p[index]) for p in pts]) for a, b, pts in segments
        ]
        out[axis] = _axis_expr(axis_segments, offset_s)
    return out


def write_filter_script(
    plan: CropPlan,
    dst,
    *,
    offset_s: float = 0.0,
    extra_filters: Optional[List[str]] = None,
) -> str:
    """Write a ``-filter_complex_script`` file. Output pad is ``[vout]``."""
    expr = crop_expression(plan, offset_s=offset_s)
    ow, oh = plan.out_w, plan.out_h
    extras = list(extra_filters or [])

    if plan.mode == "fit":
        graph = (
            "[0:v]split=2[bg][fg];"
            f"[bg]scale={ow}:{oh}:force_original_aspect_ratio=increase,"
            f"crop={ow}:{oh},gblur=sigma={FIT_BLUR_SIGMA}[bgb];"
            f"[fg]scale={ow}:{oh}:force_original_aspect_ratio=decrease[fgs];"
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
        if extras:
            graph += "," + ",".join(extras)
        graph += "[vout]"
    else:
        chain = [
            f"crop=w={plan.crop_w}:h={plan.crop_h}:x={expr['x']}:y={expr['y']}",
            f"scale={ow}:{oh}:flags=bicubic",
            "setsar=1",
        ]
        chain.extend(extras)
        graph = "[0:v]" + ",".join(chain) + "[vout]"

    Path(dst).write_text(graph + "\n", encoding="utf-8")
    return graph


def center_plan(
    src_w: int, src_h: int, *, aspect: str = "9:16", precision: str = "full"
) -> CropPlan:
    """Static centre crop — the fallback when no face is found."""
    return build_crop_plan(
        FaceTracks(), src_w, src_h, aspect=aspect, precision=precision
    )


# --------------------------------------------------------------------------- #
# smoke entry
# --------------------------------------------------------------------------- #


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: python -m vx.autoframe <video> [aspect] [--proxy]")
        return 2
    path = args[0]
    aspect = args[1] if len(args) > 1 and ":" in args[1] else "9:16"
    precision = "proxy" if "--proxy" in args else "full"

    from .faces import detect_faces_tracked  # noqa: PLC0415
    from .probe import probe  # noqa: PLC0415

    meta = probe(path)
    tracks = detect_faces_tracked(path)
    plan = build_crop_plan(
        tracks, meta.width, meta.height, aspect=aspect, precision=precision
    )
    payload = {
        "framing": tracks.framing,
        "detected_ratio": tracks.detected_ratio,
        "plan": plan.to_dict(),
        "expression": crop_expression(plan),
    }
    print(json.dumps(payload, indent=2)[:6000])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
