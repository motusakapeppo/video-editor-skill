"""vx.qc — binary quality gates over a rendered file.

Every gate is a binary observable with a code. ``QCResult.pass_`` is True iff
``failures == []``; each check dict is ``{"code","pass","value","limit","detail"}``.

Gate codes
----------
duration_drift_ms, frame_count_delta, lufs_delta, true_peak, av_start_ms,
black_frames_edges, font_fallback_count, cue_cps_max, cue_lines_max,
crop_in_bounds, source_sha_unchanged, no_nan_timestamps, has_audio, has_video,
faststart, aspect_mismatch.

``aspect_mismatch`` is an extension of the frozen code list: a render at the
wrong aspect is otherwise invisible to the gates, and the acceptance test
requires it to be observable.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import ffmpeg as ff
from .contract import (
    LOUDNESS_TARGETS,
    QCResult,
    THRESHOLDS,
    EditPlan,
    expected_frames,
    output_size,
)
from .probe import probe
from .winpath import ass_filter_path, same_fingerprint, source_fingerprint

__all__ = ["check_render", "frame_count", "check_source_unchanged", "lufs_of"]

LIMITS = {
    "duration_drift_ms": 40.0,
    "frame_count_delta": 1,
    "lufs_delta": 0.3,
    "true_peak_margin": 0.1,
    "av_start_ms": 20.0,
    "black_frames_edges": 2,
    "font_fallback_count": 0,
    "cue_cps_max": float(THRESHOLDS["sub_max_cps"]),
    "cue_lines_max": int(THRESHOLDS["sub_max_lines"]),
}

_ASS_TIME = re.compile(r"^(\d+):(\d{2}):(\d{2})[.:](\d{1,3})$")
_FONTSELECT = re.compile(r"fontselect:\s*\(([^,]+),[^)]*\)\s*->\s*([^,]+),")
_GLYPH_MISS = re.compile(r"Glyph 0x[0-9A-Fa-f]+ not found", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if out != out else out


def _temp_script(graph: str) -> str:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8", newline="\n"
    )
    with handle:
        handle.write(" ".join(str(graph).split()) + "\n")
    return handle.name


def _run_graph(
    graph: str,
    inputs: Sequence[Any],
    extra: Sequence[str],
    *,
    loglevel: str = "info",
) -> ff.Proc:
    script = _temp_script(graph)
    try:
        args: List[str] = []
        for item in inputs:
            args += ["-i", str(item)]
        args += ["-filter_complex_script", script, *extra]
        return ff.run_ff(args, loglevel=loglevel)
    finally:
        try:
            os.unlink(script)
        except OSError:
            pass


def _check(
    code: str, ok: bool, value: Any, limit: Any, detail: str = ""
) -> Dict[str, Any]:
    return {
        "code": code,
        "pass": bool(ok),
        "value": value,
        "limit": limit,
        "detail": detail,
    }


def _streams(path: Path) -> List[Dict[str, Any]]:
    proc = ff.run_fp(
        [
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,start_time,duration,width,height,avg_frame_rate",
            "-of",
            "json",
            str(path),
        ]
    )
    if not proc.ok:
        return []
    try:
        return json.loads(proc.stdout or "{}").get("streams", [])
    except json.JSONDecodeError:
        return []


def _loudness_target(plan: EditPlan) -> tuple[float, float]:
    raw = (plan.output or {}).get("loudness") or (plan.render or {}).get("loudness")
    if isinstance(raw, str) and raw in LOUDNESS_TARGETS:
        return LOUDNESS_TARGETS[raw]
    if isinstance(raw, dict):
        return (
            _num(raw.get("i", raw.get("target_i")), -14.0),
            _num(raw.get("tp", raw.get("target_tp")), -1.0),
        )
    return LOUDNESS_TARGETS["social"]


def _ass_seconds(stamp: str) -> float:
    match = _ASS_TIME.match(stamp.strip())
    if not match:
        return 0.0
    hours, minutes, seconds, frac = match.groups()
    scale = 10 ** len(frac)
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(frac) / scale


def _ass_cues(path: Path) -> List[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    cues: List[Dict[str, Any]] = []
    for line in text.splitlines():
        if not line.lower().startswith("dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) < 10:
            continue
        start = _ass_seconds(fields[1])
        end = _ass_seconds(fields[2])
        body = re.sub(r"\{[^}]*\}", "", fields[9])
        lines = body.count("\\N") + 1
        chars = len(body.replace("\\N", "").replace("\\n", "").strip())
        duration = max(0.0, end - start)
        cues.append(
            {
                "start": start,
                "end": end,
                "chars": chars,
                "lines": lines,
                "cps": (chars / duration) if duration > 0 else 0.0,
            }
        )
    return cues


def _font_fallbacks(path: Path, subs_path: Path) -> tuple[int, str]:
    """Burn one frame and count font substitutions in ffmpeg's log."""
    ff.require("subtitles")
    graph = f"[0:v]subtitles={ass_filter_path(subs_path)}[vo]"
    proc = _run_graph(
        graph,
        [path],
        ["-map", "[vo]", "-frames:v", "1", "-an", "-f", "null", "-"],
        loglevel="info",
    )
    if not proc.ok:
        return 1, f"subtitle burn failed: {proc.tail(4)}"
    fallbacks = 0
    notes: List[str] = []
    for line in proc.stderr.splitlines():
        match = _FONTSELECT.search(line)
        if match:
            requested = re.sub(r"[^a-z0-9]", "", match.group(1).lower())
            resolved = re.sub(r"[^a-z0-9]", "", match.group(2).lower())
            same_family = bool(requested) and (
                resolved.startswith(requested) or requested.startswith(resolved)
            )
            if not same_family:
                fallbacks += 1
                notes.append(f"{match.group(1)} -> {match.group(2)}")
        elif _GLYPH_MISS.search(line):
            fallbacks += 1
            notes.append(line.strip()[:120])
    return fallbacks, "; ".join(notes[:4])


def _faststart(path: Path) -> tuple[bool, str]:
    try:
        with path.open("rb") as handle:
            head = handle.read(1 << 20)
    except OSError as exc:
        return False, f"unreadable: {exc}"
    moov = head.find(b"moov")
    mdat = head.find(b"mdat")
    if moov < 0:
        return False, "no moov atom in the first 1 MiB"
    if mdat < 0:
        return True, "moov before mdat (mdat beyond 1 MiB)"
    return moov < mdat, f"moov@{moov} mdat@{mdat}"


def _nan_timestamps(path: Path) -> tuple[bool, str]:
    proc = ff.run_fp(
        [
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "packet=pts_time,dts_time",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )
    if not proc.ok:
        return False, f"ffprobe failed: {proc.tail(3)}"
    bad = 0
    for line in (proc.stdout or "").splitlines():
        lowered = line.lower()
        if "nan" in lowered:
            bad += 1
    return bad == 0, f"{bad} NaN timestamp(s)"


def _black_edges(path: Path, duration: float) -> tuple[int, str]:
    graph = "[0:v]blackdetect=d=0.05:pix_th=0.10[vo]"
    proc = _run_graph(
        graph,
        [path],
        ["-map", "[vo]", "-an", "-f", "null", "-"],
        loglevel="info",
    )
    if not proc.ok:
        return 0, f"blackdetect failed: {proc.tail(3)}"
    edge = 0
    spans: List[str] = []
    for line in proc.stderr.splitlines():
        if "black_start" not in line:
            continue
        start = re.search(r"black_start:([0-9.]+)", line)
        end = re.search(r"black_end:([0-9.]+)", line)
        if not start or not end:
            continue
        s, e = float(start.group(1)), float(end.group(1))
        spans.append(f"{s:.2f}-{e:.2f}")
        if s <= 0.15 or (duration > 0 and e >= duration - 0.15):
            edge += 1
    return edge, ", ".join(spans[:6]) or "none"


def _crop_rect(plan: EditPlan) -> Optional[Dict[str, Any]]:
    for holder in (plan.render or {}, plan.output or {}):
        for key in ("crop", "crop_plan"):
            value = holder.get(key)
            if isinstance(value, dict):
                return value
    return None


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def frame_count(path) -> int:
    """Exact decoded video frame count (``-count_frames``)."""
    target = Path(path)
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
            str(target),
        ]
    )
    if proc.ok:
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                return int(line)
    try:
        result = probe(target)
    except Exception:  # noqa: BLE001 - probe raises MissingDependency
        return 0
    if result.nb_frames:
        return int(result.nb_frames)
    return int(round(result.duration * result.fps)) if result.fps else 0


def check_source_unchanged(fp: dict, path) -> bool:
    """True when the file on disk still matches the recorded fingerprint."""
    if not fp:
        return False
    try:
        return same_fingerprint(fp, source_fingerprint(path))
    except OSError:
        return False


def lufs_of(path) -> dict:
    """Integrated loudness / true peak via the loudnorm measurement pass."""
    proc = ff.run_ff(
        ["-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        loglevel="info",
    )
    if not proc.ok:
        return {}
    text = proc.stderr
    start, end = text.rfind("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        raw = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    out: Dict[str, float] = {}
    for key, value in raw.items():
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def check_render(
    plan: EditPlan,
    media: dict,
    out_path,
    *,
    subs_path: str | None = None,
    source_fp: dict | None = None,
) -> QCResult:
    """Run every gate over ``out_path`` and return a ``QCResult``."""
    target = Path(out_path)
    checks: List[Dict[str, Any]] = []
    metrics: Dict[str, Any] = {}

    if not target.exists():
        checks.append(
            _check("has_video", False, False, True, f"missing output: {target}")
        )
        return QCResult(
            pass_=False,
            checks=checks,
            failures=[c["code"] for c in checks if not c["pass"]],
            metrics=metrics,
        )

    try:
        info = probe(target)
    except Exception as exc:  # noqa: BLE001 - probe raises MissingDependency
        checks.append(_check("has_video", False, False, True, str(exc)))
        return QCResult(
            pass_=False,
            checks=checks,
            failures=[c["code"] for c in checks if not c["pass"]],
            metrics=metrics,
        )

    fps = _num(media.get("fps"), info.fps or 30.0) or 30.0
    precision = str((plan.render or {}).get("precision") or "full")
    aspect = str((plan.output or {}).get("aspect") or "9:16")
    want_w, want_h = output_size(aspect, precision)

    # -- presence ----------------------------------------------------------- #
    checks.append(
        _check(
            "has_video",
            info.width > 0 and info.height > 0,
            True,
            True,
            f"{info.width}x{info.height} {info.video_codec}",
        )
    )
    expect_audio = bool(media.get("has_audio", True))
    checks.append(
        _check(
            "has_audio",
            info.has_audio or not expect_audio,
            info.has_audio,
            expect_audio,
            f"codec={info.audio_codec or 'none'}",
        )
    )

    # -- geometry ----------------------------------------------------------- #
    checks.append(
        _check(
            "aspect_mismatch",
            (info.width, info.height) == (want_w, want_h),
            [info.width, info.height],
            [want_w, want_h],
            f"planned {aspect} @ {precision}",
        )
    )

    # -- duration / frames -------------------------------------------------- #
    cuts = list(plan.cuts or [])
    if cuts:
        want_frames = expected_frames(cuts, fps)
        want_duration = want_frames / fps
    else:
        want_duration = _num(media.get("duration"), info.duration)
        want_frames = int(round(want_duration * fps))

    drift_ms = abs(info.duration - want_duration) * 1000.0
    checks.append(
        _check(
            "duration_drift_ms",
            drift_ms <= LIMITS["duration_drift_ms"],
            round(drift_ms, 2),
            LIMITS["duration_drift_ms"],
            f"actual={info.duration:.3f}s expected={want_duration:.3f}s",
        )
    )

    actual_frames = frame_count(target)
    delta = abs(actual_frames - want_frames)
    checks.append(
        _check(
            "frame_count_delta",
            delta <= LIMITS["frame_count_delta"],
            delta,
            LIMITS["frame_count_delta"],
            f"actual={actual_frames} expected={want_frames}",
        )
    )

    # -- loudness ----------------------------------------------------------- #
    target_i, target_tp = _loudness_target(plan)
    loud = lufs_of(target) if info.has_audio else {}
    metrics["loudness"] = loud
    if info.has_audio:
        measured_i = _num(loud.get("input_i"), -99.0)
        measured_tp = _num(loud.get("input_tp"), -99.0)
        lufs_delta = abs(measured_i - target_i)
        # True-peak limiting: landing quieter than target with TP at the ceiling is physics.
        tp_limited = (
            measured_i < target_i
            and measured_tp <= target_tp + LIMITS["true_peak_margin"]
            and lufs_delta <= 1.0
        )
        checks.append(
            _check(
                "lufs_delta",
                lufs_delta <= LIMITS["lufs_delta"] or tp_limited,
                round(lufs_delta, 3),
                LIMITS["lufs_delta"],
                f"measured={measured_i:.2f} target={target_i:.2f}"
                + (" true-peak limited" if tp_limited else ""),
            )
        )
        checks.append(
            _check(
                "true_peak",
                measured_tp <= target_tp + LIMITS["true_peak_margin"],
                round(measured_tp, 3),
                round(target_tp + LIMITS["true_peak_margin"], 3),
                f"target_tp={target_tp}",
            )
        )
    else:
        checks.append(
            _check("lufs_delta", True, None, LIMITS["lufs_delta"], "no audio stream")
        )
        checks.append(_check("true_peak", True, None, target_tp, "no audio stream"))

    # -- A/V start ---------------------------------------------------------- #
    streams = _streams(target)
    v_start = next(
        (_num(s.get("start_time")) for s in streams if s.get("codec_type") == "video"),
        0.0,
    )
    a_start = next(
        (_num(s.get("start_time")) for s in streams if s.get("codec_type") == "audio"),
        None,
    )
    if a_start is None:
        checks.append(
            _check("av_start_ms", True, None, LIMITS["av_start_ms"], "no audio stream")
        )
    else:
        skew = abs(v_start - a_start) * 1000.0
        checks.append(
            _check(
                "av_start_ms",
                skew <= LIMITS["av_start_ms"],
                round(skew, 2),
                LIMITS["av_start_ms"],
                f"video={v_start:.4f}s audio={a_start:.4f}s",
            )
        )
    metrics["start"] = {"video": v_start, "audio": a_start}

    # -- black frames at the edges ------------------------------------------ #
    edges, spans = _black_edges(target, info.duration)
    checks.append(
        _check(
            "black_frames_edges",
            edges <= LIMITS["black_frames_edges"],
            edges,
            LIMITS["black_frames_edges"],
            spans,
        )
    )

    # -- subtitles ---------------------------------------------------------- #
    subs = Path(subs_path) if subs_path else None
    if subs is not None and subs.exists():
        fallbacks, note = _font_fallbacks(target, subs)
        checks.append(
            _check(
                "font_fallback_count",
                fallbacks <= LIMITS["font_fallback_count"],
                fallbacks,
                LIMITS["font_fallback_count"],
                note,
            )
        )
        cues = _ass_cues(subs)
        worst_cps = max((c["cps"] for c in cues), default=0.0)
        worst_lines = max((c["lines"] for c in cues), default=0)
        checks.append(
            _check(
                "cue_cps_max",
                worst_cps <= LIMITS["cue_cps_max"],
                round(worst_cps, 2),
                LIMITS["cue_cps_max"],
                f"{len(cues)} cue(s)",
            )
        )
        checks.append(
            _check(
                "cue_lines_max",
                worst_lines <= LIMITS["cue_lines_max"],
                worst_lines,
                LIMITS["cue_lines_max"],
                f"{len(cues)} cue(s)",
            )
        )
    else:
        for code, limit in (
            ("font_fallback_count", LIMITS["font_fallback_count"]),
            ("cue_cps_max", LIMITS["cue_cps_max"]),
            ("cue_lines_max", LIMITS["cue_lines_max"]),
        ):
            checks.append(_check(code, True, 0, limit, "no subtitles"))

    # -- crop in bounds ----------------------------------------------------- #
    rect = _crop_rect(plan)
    if rect:
        src_w = int(_num(rect.get("src_w"), _num(media.get("width"))))
        src_h = int(_num(rect.get("src_h"), _num(media.get("height"))))
        x = int(_num(rect.get("x")))
        y = int(_num(rect.get("y")))
        w = int(_num(rect.get("w", rect.get("crop_w"))))
        h = int(_num(rect.get("h", rect.get("crop_h"))))
        inside = (
            src_w > 0
            and src_h > 0
            and w > 0
            and h > 0
            and x >= 0
            and y >= 0
            and x + w <= src_w
            and y + h <= src_h
        )
        checks.append(
            _check(
                "crop_in_bounds",
                inside,
                [x, y, w, h],
                [src_w, src_h],
                "rect must sit inside the source",
            )
        )
    else:
        checks.append(_check("crop_in_bounds", True, None, None, "no crop plan"))

    # -- source integrity --------------------------------------------------- #
    if source_fp:
        source_path = media.get("path") or source_fp.get("path")
        unchanged = bool(source_path) and check_source_unchanged(source_fp, source_path)
        checks.append(
            _check(
                "source_sha_unchanged",
                unchanged,
                unchanged,
                True,
                str(source_path or "unknown source"),
            )
        )
    else:
        checks.append(
            _check("source_sha_unchanged", True, None, True, "no fingerprint supplied")
        )

    # -- timestamps --------------------------------------------------------- #
    clean, note = _nan_timestamps(target)
    checks.append(_check("no_nan_timestamps", clean, clean, True, note))

    # -- container ---------------------------------------------------------- #
    fast, note = _faststart(target)
    checks.append(_check("faststart", fast, fast, True, note))

    failures = [c["code"] for c in checks if not c["pass"]]
    metrics.update(
        {
            "frames": actual_frames,
            "expected_frames": want_frames,
            "duration": info.duration,
            "expected_duration": round(want_duration, 6),
            "size": [info.width, info.height],
            "fps": info.fps,
        }
    )
    return QCResult(
        pass_=failures == [],
        checks=checks,
        failures=failures,
        metrics=metrics,
    )


# --------------------------------------------------------------------------- #
# smoke entry
# --------------------------------------------------------------------------- #


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Smoke entry: ``python -m vx.qc <file> [aspect]``."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python -m vx.qc <file> [aspect]")
        return 2
    path = Path(argv[0])
    aspect = argv[1] if len(argv) > 1 else "9:16"
    info = probe(path)
    plan = EditPlan(output={"aspect": aspect, "loudness": "social"})
    media = info.to_dict()
    result = check_render(plan, media, path)
    print(json.dumps(result.to_dict(), indent=2, default=str))
    print("frames:", frame_count(path))
    print("lufs:", lufs_of(path))
    return 0 if result.pass_ else 5


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
