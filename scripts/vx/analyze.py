"""vx.analyze — silent, non-destructive video pre-analysis (Fase 4).

Answers, without ever writing to the source: is it shaky, is it badly exposed,
does it have a colour cast, where are the cuts, where are the freezes, and how
is the rhythm.

Every ffmpeg call goes through :mod:`vx.ffmpeg` (argument lists, never a shell).
Pixel work uses OpenCV when available and degrades to ``verdict="unknown"``
dicts when it is not — this module never raises because of a missing cv2.

Public API (frozen):
    analyze_video, detect_scene_cuts, detect_freezes, measure_jitter,
    measure_exposure, measure_color, measure_motion, measure_pacing,
    build_retention
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import ffmpeg as ff
from .contract import SAMPLE_FPS_VIDEO, THRESHOLDS, VideoAnalysis, clamp, fmt
from .probe import probe

# --------------------------------------------------------------------------- #
# optional OpenCV / numpy
# --------------------------------------------------------------------------- #

try:  # pragma: no cover - exercised by the environment, not by tests
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    _CV_OK = True
    _CV_WHY = ""
except Exception as exc:  # pragma: no cover
    cv2 = None  # type: ignore
    np = None  # type: ignore
    _CV_OK = False
    _CV_WHY = f"opencv/numpy unavailable: {exc}"

# Width every pixel metric is computed at. Jitter is reported back in source
# pixels, so the ratio is all that matters.
_WORK_WIDTH = 320

# A frame is "static" for motion purposes below this mean absolute difference.
_STATIC_DIFF = 1.0

# "bright" is the mirror of THRESHOLDS["luma_under"] on the 0..255 scale.
_BRIGHT_LUMA = 255.0 - float(THRESHOLDS["luma_under"])

# The early-hook window is three quarters of the hook window (4.0 -> 3.0).
_EARLY_HOOK = float(THRESHOLDS["hook_window"]) * 0.75

# --------------------------------------------------------------------------- #
# ffmpeg log parsing
# --------------------------------------------------------------------------- #

_RE_PTS_TIME = re.compile(r"pts_time:\s*(-?\d+(?:\.\d+)?)")
_RE_SCENE_SCORE = re.compile(r"scene_score[=:]\s*(-?\d+(?:\.\d+)?)")
_RE_SCD = re.compile(
    r"lavfi\.scd\.score:\s*(-?\d+(?:\.\d+)?)\s*,\s*lavfi\.scd\.time:\s*(-?\d+(?:\.\d+)?)"
)
_RE_FREEZE_START = re.compile(r"freeze_start:\s*(-?\d+(?:\.\d+)?)")
_RE_FREEZE_END = re.compile(r"freeze_end:\s*(-?\d+(?:\.\d+)?)")
_RE_FREEZE_DUR = re.compile(r"freeze_duration:\s*(-?\d+(?:\.\d+)?)")


def _log(proc: ff.Proc) -> str:
    """ffmpeg writes av_log to stderr; some builds also echo to stdout."""
    return (proc.stderr or "") + "\n" + (proc.stdout or "")


def _dedupe(times: Sequence[float], min_gap: float = 0.25) -> List[float]:
    """Sorted, non-negative, at least `min_gap` apart (first of a cluster wins)."""
    out: List[float] = []
    for t in sorted(float(x) for x in times):
        if t < 0.0:
            continue
        if not out or t - out[-1] >= min_gap:
            out.append(fmt(t))
    return out


def _duration(path: str | Path) -> float:
    try:
        return float(probe(path).duration)
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- #
# scene cuts
# --------------------------------------------------------------------------- #


def _parse_scene_log(text: str) -> List[float]:
    """`select=gt(scene,TH),metadata=print` emits a pts_time line followed by a
    scene_score line. Timestamps arrive with 6 decimals *or* as bare integers."""
    cuts: List[float] = []
    pending: Optional[float] = None
    for line in text.splitlines():
        m_pts = _RE_PTS_TIME.search(line)
        if m_pts:
            pending = float(m_pts.group(1))
            continue
        m_score = _RE_SCENE_SCORE.search(line)
        if m_score and pending is not None:
            cuts.append(pending)
            pending = None
    return cuts


def _scdet_times(path: str | Path, threshold: float) -> List[float]:
    """High-confidence cross-check channel. scdet scores live on a 0..100 scale
    that is not identical to `scene`, so the filter threshold is the scene
    threshold scaled to 100 — i.e. deliberately stricter than the primary pass."""
    if not ff.capabilities().get("scdet"):
        return []
    vf = f"scdet=threshold={threshold * 100.0:g}"
    proc = ff.run_ff(
        ["-i", str(path), "-an", "-sn", "-vf", vf, "-f", "null", "-"],
        loglevel="info",
    )
    times: List[float] = []
    for line in _log(proc).splitlines():
        m = _RE_SCD.search(line)
        if m:
            times.append(float(m.group(2)))
    return times


def detect_scene_cuts(
    path: str | Path, *, threshold: Optional[float] = None
) -> List[float]:
    """Hard-cut timestamps in seconds, sorted and deduped (>= 0.25 s apart).

    Primary detector: ``select='gt(scene,TH)',metadata=print``.
    Cross-check: ``scdet`` (when the build has it) contributes only cuts the
    primary pass missed by more than 0.5 s, so it can add recall but never
    reshuffle a confident primary result.
    """
    th = float(THRESHOLDS["scene_cut"] if threshold is None else threshold)
    src = str(path)
    vf = f"select='gt(scene,{th:.4f})',metadata=print"
    proc = ff.run_ff(
        ["-i", src, "-an", "-sn", "-vf", vf, "-f", "null", "-"], loglevel="info"
    )
    primary = _parse_scene_log(_log(proc))

    extra: List[float] = []
    for t in _scdet_times(src, th):
        if not primary or all(abs(t - p) > 0.5 for p in primary):
            extra.append(t)
    return _dedupe(primary + extra)


# --------------------------------------------------------------------------- #
# freezes
# --------------------------------------------------------------------------- #


def detect_freezes(
    path: str | Path,
    *,
    noise_db: float = -50.0,
    min_duration: Optional[float] = None,
) -> List[Dict[str, float]]:
    """Frozen spans via ``freezedetect``. A freeze still open at EOF is closed
    with the container duration."""
    ff.require("freezedetect")
    d = float(THRESHOLDS["freeze_min"] if min_duration is None else min_duration)
    src = str(path)
    vf = f"freezedetect=n={noise_db:g}dB:d={d:g}"
    proc = ff.run_ff(
        ["-i", src, "-an", "-sn", "-vf", vf, "-f", "null", "-"], loglevel="info"
    )

    spans: List[Dict[str, float]] = []
    start: Optional[float] = None
    last_dur: Optional[float] = None
    for line in _log(proc).splitlines():
        m_start = _RE_FREEZE_START.search(line)
        if m_start:
            start = float(m_start.group(1))
            last_dur = None
            continue
        m_dur = _RE_FREEZE_DUR.search(line)
        if m_dur:
            last_dur = float(m_dur.group(1))
            continue
        m_end = _RE_FREEZE_END.search(line)
        if m_end and start is not None:
            end = float(m_end.group(1))
            spans.append(_freeze(start, end))
            start = None
            last_dur = None

    if start is not None:
        end = start + last_dur if last_dur is not None else _duration(src)
        if end > start:
            spans.append(_freeze(start, end))
    return spans


def _freeze(start: float, end: float) -> Dict[str, float]:
    return {
        "start": fmt(start),
        "end": fmt(end),
        "duration": fmt(max(0.0, end - start)),
    }


# --------------------------------------------------------------------------- #
# pixel metrics (one decode pass feeds jitter / exposure / colour / motion)
# --------------------------------------------------------------------------- #


def _degraded(reason: str) -> Dict[str, Dict[str, Any]]:
    note = reason or _CV_WHY or "pixel analysis unavailable"
    return {
        "jitter": {
            "score": 0.0,
            "verdict": "unknown",
            "mean_residual_px": 0.0,
            "headroom_pct": 12.0,
            "samples": 0,
            "note": note,
        },
        "exposure": {
            "mean_luma": 0.0,
            "clip_high_pct": 0.0,
            "clip_low_pct": 0.0,
            "verdict": "unknown",
            "note": note,
        },
        "color": {
            "cast_rg": 0.0,
            "cast_by": 0.0,
            "verdict": "unknown",
            "saturation": 0.0,
            "note": note,
        },
        "motion": {
            "per_second": [],
            "mean": 0.0,
            "static_run_max": 0.0,
            "cuts_per_10s": 0.0,
            "note": note,
        },
    }


def _moving_average(values: Any, window: int) -> Any:
    """Centred moving average with edge padding; same length as the input."""
    n = int(values.shape[0])
    if n == 0 or window < 2 or n < window:
        return values.copy()
    pad = window // 2
    padded = np.pad(values, (pad, pad), mode="edge")
    kernel = np.ones(window, dtype=np.float64) / float(window)
    smoothed = np.convolve(padded, kernel, mode="valid")
    return smoothed[:n]


_DIS: Any = None


def _dense_flow(prev: Any, cur: Any) -> Any:
    """Dense optical flow between two 8-bit gray frames.

    DIS is preferred: on smooth or heavily aliased content Farneback's median
    collapses to zero, while DIS tracks the global translation reliably.
    Farneback is the fallback for builds without the DIS contrib module.
    """
    global _DIS
    if hasattr(cv2, "DISOpticalFlow_create"):
        if _DIS is None:
            _DIS = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        return _DIS.calc(prev, cur, None)
    return cv2.calcOpticalFlowFarneback(prev, cur, None, 0.5, 3, 15, 3, 5, 1.2, 0)


def _jitter_verdict(score: float) -> str:
    bands = THRESHOLDS["jitter_bands"]
    if score < float(bands[0]):
        return "stable"
    if score < float(bands[1]):
        return "slight"
    return "shaky"


def _exposure_verdict(mean_luma: float, clip_high_pct: float) -> str:
    if clip_high_pct >= float(THRESHOLDS["clip_high_block_pct"]):
        return "clipped"
    if mean_luma < float(THRESHOLDS["luma_under"]):
        return "dark"
    if mean_luma > _BRIGHT_LUMA or clip_high_pct >= float(THRESHOLDS["clip_high_pct"]):
        return "bright"
    return "ok"


def _color_verdict(cast_rg: float, cast_by: float) -> str:
    th_rg = float(THRESHOLDS["cast_rg"])
    th_by = float(THRESHOLDS["cast_by"])
    dev_rg = cast_rg / th_rg if th_rg else 0.0
    dev_by = cast_by / th_by if th_by else 0.0
    if max(abs(dev_rg), abs(dev_by)) <= 1.0:
        return "neutral"
    if abs(dev_rg) >= abs(dev_by):
        return "warm" if dev_rg > 0 else "green"
    return "cool" if dev_by > 0 else "magenta"


def _static_run_max(per_second: Sequence[float]) -> float:
    best = 0
    run = 0
    for value in per_second:
        if value < _STATIC_DIFF:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return float(best)


def _pixel_metrics(path: str | Path, sample_fps: float) -> Dict[str, Dict[str, Any]]:
    """Single decode pass at `sample_fps`, downscaled to 320 px wide.

    Produces the jitter, exposure, colour and motion blocks together so that
    `analyze_video` never decodes the same file four times.
    """
    if not _CV_OK:
        return _degraded(_CV_WHY)

    rate = float(sample_fps) if sample_fps and sample_fps > 0 else SAMPLE_FPS_VIDEO
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        cap.release()
        return _degraded(f"OpenCV cannot open {Path(path).name}")

    try:
        src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        src_w = float(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0.0)
        src_frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        step = max(1, int(round(src_fps / rate))) if src_fps > 0 else 1

        prev_gray: Any = None
        dxs: List[float] = []
        dys: List[float] = []
        diffs: List[Tuple[float, float]] = []
        headrooms: List[float] = []

        luma_sum = 0.0
        clip_hi = 0
        clip_lo = 0
        px_total = 0
        r_sum = g_sum = b_sum = 0.0
        sat_sum = 0.0
        frames = 0
        last_t = 0.0

        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step:
                idx += 1
                continue
            t = (idx / src_fps) if src_fps > 0 else float(frames)
            idx += 1

            height, width = frame.shape[:2]
            if width <= 0 or height <= 0:
                continue
            small_h = max(2, int(round(height * (_WORK_WIDTH / float(width)))))
            small = cv2.resize(
                frame, (_WORK_WIDTH, small_h), interpolation=cv2.INTER_AREA
            )
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            b = small[:, :, 0].astype(np.float32)
            g = small[:, :, 1].astype(np.float32)
            r = small[:, :, 2].astype(np.float32)
            luma = 0.114 * b + 0.587 * g + 0.299 * r

            luma_sum += float(luma.mean())
            clip_hi += int(np.count_nonzero(luma >= 250.0))
            clip_lo += int(np.count_nonzero(luma <= 5.0))
            px_total += int(luma.size)

            r_sum += float(r.mean())
            g_sum += float(g.mean())
            b_sum += float(b.mean())

            mx = small.max(axis=2).astype(np.float32)
            mn = small.min(axis=2).astype(np.float32)
            sat = np.where(mx > 0.0, (mx - mn) / np.maximum(mx, 1.0), 0.0)
            sat_sum += float(sat.mean()) * 100.0

            if prev_gray is not None:
                diff = float(
                    np.mean(
                        np.abs(gray.astype(np.float32) - prev_gray.astype(np.float32))
                    )
                )
                diffs.append((t, diff))

                flow = _dense_flow(prev_gray, gray)
                dxs.append(float(np.median(flow[:, :, 0])))
                dys.append(float(np.median(flow[:, :, 1])))

                mag = np.sqrt(flow[:, :, 0] ** 2 + flow[:, :, 1] ** 2)
                if float(mag.max()) > 0.5:
                    thr = max(1.0, 2.0 * float(np.median(mag)))
                    cols = np.flatnonzero((mag > thr).any(axis=0))
                    if cols.size:
                        box_w = float(cols[-1] - cols[0] + 1)
                        # A box covering the whole frame means the motion is
                        # global (pan/shake), not a subject: no estimate.
                        if box_w < 0.95 * _WORK_WIDTH:
                            headrooms.append((1.0 - box_w / _WORK_WIDTH) * 100.0)

            prev_gray = gray
            frames += 1
            last_t = t

        if frames == 0:
            return _degraded(f"no decodable frames in {Path(path).name}")

        # ---- jitter ------------------------------------------------------- #
        if len(dxs) >= 3:
            path_x = np.cumsum(np.asarray(dxs, dtype=np.float64))
            path_y = np.cumsum(np.asarray(dys, dtype=np.float64))
            window = max(3, int(round(0.5 * rate)))
            res_x = path_x - _moving_average(path_x, window)
            res_y = path_y - _moving_average(path_y, window)
            residual = np.sqrt(res_x**2 + res_y**2)
            mean_res_small = float(residual.mean())
            scale = (src_w / float(_WORK_WIDTH)) if src_w > 0 else 1.0
            mean_res_px = mean_res_small * scale
            frame_w = src_w if src_w > 0 else float(_WORK_WIDTH)
            score = (mean_res_px / frame_w) * 100.0
            jitter = {
                "score": fmt(score),
                "verdict": _jitter_verdict(score),
                "mean_residual_px": fmt(mean_res_px),
                "headroom_pct": fmt(float(np.median(headrooms)) if headrooms else 12.0),
                "samples": len(dxs),
            }
        else:
            jitter = {
                "score": 0.0,
                "verdict": "stable",
                "mean_residual_px": 0.0,
                "headroom_pct": 12.0,
                "samples": len(dxs),
            }

        # ---- exposure ----------------------------------------------------- #
        mean_luma = luma_sum / frames
        clip_high_pct = (clip_hi / px_total * 100.0) if px_total else 0.0
        clip_low_pct = (clip_lo / px_total * 100.0) if px_total else 0.0
        exposure = {
            "mean_luma": fmt(mean_luma),
            "clip_high_pct": fmt(clip_high_pct),
            "clip_low_pct": fmt(clip_low_pct),
            "verdict": _exposure_verdict(mean_luma, clip_high_pct),
        }

        # ---- colour ------------------------------------------------------- #
        mean_r = r_sum / frames
        mean_g = g_sum / frames
        mean_b = b_sum / frames
        mean_y = 0.299 * mean_r + 0.587 * mean_g + 0.114 * mean_b
        cast_rg = mean_r - mean_g
        cast_by = mean_b - mean_y
        color = {
            "cast_rg": fmt(cast_rg),
            "cast_by": fmt(cast_by),
            "verdict": _color_verdict(cast_rg, cast_by),
            "saturation": fmt(sat_sum / frames),
        }

        # ---- motion ------------------------------------------------------- #
        duration = (
            (src_frames / src_fps) if (src_fps > 0 and src_frames > 0) else last_t
        )
        n_sec = max(1, int(math.ceil(duration))) if duration > 0 else 1
        buckets: List[List[float]] = [[] for _ in range(n_sec)]
        for t, diff in diffs:
            buckets[min(n_sec - 1, max(0, int(t)))].append(diff)
        per_second = [fmt(float(np.mean(b))) if b else 0.0 for b in buckets]
        motion = {
            "per_second": per_second,
            "mean": fmt(float(np.mean(per_second))) if per_second else 0.0,
            "static_run_max": fmt(_static_run_max(per_second)),
            "cuts_per_10s": 0.0,
        }

        return {
            "jitter": jitter,
            "exposure": exposure,
            "color": color,
            "motion": motion,
        }
    finally:
        cap.release()


# --------------------------------------------------------------------------- #
# public single-metric wrappers
# --------------------------------------------------------------------------- #


def measure_jitter(
    path: str | Path, *, sample_fps: float = SAMPLE_FPS_VIDEO
) -> Dict[str, Any]:
    """Camera-shake score: high-passed cumulative optical-flow path, as a
    percentage of frame width. 0 = locked off, ~1+ = unusable handheld."""
    return dict(_pixel_metrics(path, sample_fps)["jitter"])


def measure_exposure(
    path: str | Path, *, sample_fps: float = SAMPLE_FPS_VIDEO
) -> Dict[str, Any]:
    """Luma mean plus highlight/shadow clipping percentages."""
    return dict(_pixel_metrics(path, sample_fps)["exposure"])


def measure_color(
    path: str | Path, *, sample_fps: float = SAMPLE_FPS_VIDEO
) -> Dict[str, Any]:
    """Gray-world colour cast (R-G and B-Y) plus mean saturation."""
    return dict(_pixel_metrics(path, sample_fps)["color"])


def measure_motion(
    path: str | Path, *, sample_fps: float = SAMPLE_FPS_VIDEO
) -> Dict[str, Any]:
    """Frame-difference energy per second, longest static run, cut density."""
    motion = dict(_pixel_metrics(path, sample_fps)["motion"])
    if "note" not in motion:
        duration = _duration(path)
        if duration > 0:
            cuts = detect_scene_cuts(path)
            motion["cuts_per_10s"] = fmt(len(cuts) / duration * 10.0)
    return motion


# --------------------------------------------------------------------------- #
# pacing / retention
# --------------------------------------------------------------------------- #


def _shot_bounds(cuts: Sequence[float], duration: float) -> List[Tuple[float, float]]:
    inner = sorted(float(c) for c in cuts if 0.0 < float(c) < duration)
    bounds = [0.0] + inner + [duration]
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if (b - a) > 1e-6]


def _seg_motion(per_second: Sequence[float], start: float, end: float) -> float:
    if not per_second:
        return 0.0
    lo = max(0, int(math.floor(start)))
    hi = min(len(per_second), int(math.ceil(end)))
    if hi <= lo:
        return 0.0
    window = per_second[lo:hi]
    return float(sum(window) / len(window))


def measure_pacing(path: str | Path, cuts: List[float]) -> Dict[str, Any]:
    """Shot-length statistics and a rhythm verdict.

    Verdict bands are derived from ``THRESHOLDS["hook_window"]``: a mean shot at
    or below half the hook window is snappy, at or above the hook window is slow.
    """
    duration = _duration(path)
    shots = _shot_bounds(cuts, duration) if duration > 0 else []
    if not shots:
        return {
            "mean_shot_len": 0.0,
            "shot_count": 0,
            "fastest_shot": 0.0,
            "slowest_shot": 0.0,
            "verdict": "normal",
        }
    lengths = [b - a for a, b in shots]
    mean_len = sum(lengths) / len(lengths)
    hook = float(THRESHOLDS["hook_window"])
    if mean_len <= hook * 0.5:
        verdict = "snappy"
    elif mean_len >= hook:
        verdict = "slow"
    else:
        verdict = "normal"
    return {
        "mean_shot_len": fmt(mean_len),
        "shot_count": len(lengths),
        "fastest_shot": fmt(min(lengths)),
        "slowest_shot": fmt(max(lengths)),
        "verdict": verdict,
    }


def build_retention(
    cuts: List[float],
    motion: Dict[str, Any],
    face_span_ratio: float = 0.0,
) -> Dict[str, Any]:
    """Where viewers are likely to drop off, and which opening beats to hook on.

    `risk_drop` = shots longer than the hook window whose motion sits below the
    clip mean, plus the 0..3 s window when the whole clip is near-static.
    `hook_candidates` = segments inside the hook window ranked by motion and
    scene novelty (a cut starting the segment), with a small bonus when a face
    spans a meaningful part of the frame.
    """
    per_second: Sequence[float] = list(motion.get("per_second") or [])
    if not per_second:
        return {"risk_drop": [], "hook_candidates": []}

    duration = float(len(per_second))
    mean_motion = float(motion.get("mean") or 0.0)
    hook = float(THRESHOLDS["hook_window"])
    shots = _shot_bounds(cuts, duration)

    risk: List[Dict[str, Any]] = []
    for start, end in shots:
        if (end - start) > hook and _seg_motion(per_second, start, end) < mean_motion:
            risk.append({"at": fmt(start), "reason": "long_shot_low_motion"})
    if mean_motion < _STATIC_DIFF:
        risk.append({"at": 0.0, "reason": "weak_hook_low_motion"})

    window_end = min(hook, duration)
    opening = _shot_bounds([c for c in cuts if c < window_end], window_end)
    if not opening:
        opening = [(0.0, window_end)]

    seg_motions = [_seg_motion(per_second, a, b) for a, b in opening]
    peak = max(seg_motions) if seg_motions else 0.0
    norm = peak if peak > 0.0 else 1.0
    face_bonus = 0.15 * clamp(float(face_span_ratio), 0.0, 1.0)

    candidates: List[Dict[str, Any]] = []
    for (start, _end), seg_motion in zip(opening, seg_motions):
        novelty = 1.0 if start > 0.0 else 0.0
        score = 0.7 * (seg_motion / norm) + 0.3 * novelty + face_bonus
        candidates.append(
            {
                "at": fmt(start),
                "score": fmt(clamp(score, 0.0, 1.0)),
                "reason": "high_motion_after_cut" if novelty else "high_motion_opening",
            }
        )
    candidates.sort(key=lambda c: (-float(c["score"]), float(c["at"])))

    return {"risk_drop": risk, "hook_candidates": candidates[:3]}


# --------------------------------------------------------------------------- #
# composition
# --------------------------------------------------------------------------- #


def analyze_video(
    path: str | Path,
    *,
    sample_fps: float = SAMPLE_FPS_VIDEO,
    proxy_path: Optional[str] = None,
) -> VideoAnalysis:
    """Full silent analysis. Read-only: `path` is never written to.

    Timestamp-accurate detection (cuts, freezes) always runs on the original;
    pixel work (jitter, exposure, colour, motion) runs on `proxy_path` when one
    is supplied, because a 640 px proxy is 4x cheaper to decode and the metrics
    are scale-normalised.
    """
    src = str(path)
    info = probe(src)
    pixel = str(proxy_path) if proxy_path else src

    cuts = detect_scene_cuts(src)
    freezes = detect_freezes(src)

    metrics = _pixel_metrics(pixel, sample_fps)
    motion = dict(metrics["motion"])
    if info.duration > 0:
        motion["cuts_per_10s"] = fmt(len(cuts) / info.duration * 10.0)

    pacing = measure_pacing(src, cuts)
    retention = build_retention(cuts, motion)

    return VideoAnalysis(
        scene_cuts=cuts,
        freezes=freezes,
        jitter=dict(metrics["jitter"]),
        exposure=dict(metrics["exposure"]),
        color=dict(metrics["color"]),
        motion=motion,
        pacing=pacing,
        retention=retention,
    )


# --------------------------------------------------------------------------- #
# smoke entry
# --------------------------------------------------------------------------- #


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vx.analyze",
        description="Silent, non-destructive video pre-analysis.",
    )
    parser.add_argument("--input", required=True, help="source video")
    parser.add_argument("--proxy", default=None, help="proxy used for pixel work")
    parser.add_argument(
        "--sample-fps", type=float, default=SAMPLE_FPS_VIDEO, help="pixel sample rate"
    )
    parser.add_argument("--json", action="store_true", help="print the full JSON")
    ns = parser.parse_args(list(argv) if argv is not None else None)

    result = analyze_video(ns.input, sample_fps=ns.sample_fps, proxy_path=ns.proxy)
    data = result.to_dict()

    if ns.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    print(f"input        {ns.input}")
    print(f"cuts         {len(data['scene_cuts'])} {data['scene_cuts']}")
    print(f"freezes      {len(data['freezes'])}")
    print(
        "jitter       {score} ({verdict})".format(**data["jitter"])
        if "score" in data["jitter"]
        else "jitter       n/a"
    )
    print(
        "exposure     {mean_luma} luma ({verdict})".format(**data["exposure"])
        if "mean_luma" in data["exposure"]
        else "exposure     n/a"
    )
    print(
        "color        rg={cast_rg} by={cast_by} ({verdict})".format(**data["color"])
        if "cast_rg" in data["color"]
        else "color        n/a"
    )
    print(
        "motion       mean={mean} static_run={static_run_max}".format(**data["motion"])
        if "mean" in data["motion"]
        else "motion       n/a"
    )
    print(
        "pacing       {mean_shot_len}s/shot ({verdict})".format(**data["pacing"])
        if "mean_shot_len" in data["pacing"]
        else "pacing       n/a"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
