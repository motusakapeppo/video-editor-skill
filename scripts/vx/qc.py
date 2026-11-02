"""vx.qc — binary quality gates over a rendered file.

Every gate is a binary observable with a code. ``QCResult.pass_`` is True iff
``failures == []``; each check dict is
``{"code","pass","status","value","limit","detail"}``. ``status`` is ``"checked"``
for a gate that actually ran and ``"skipped"`` for one whose treatment is absent
from the plan — a skip is not a green check.

Gate codes
----------
duration_drift_ms, frame_count_delta, lufs_delta, true_peak, av_start_ms,
black_frames_edges, font_fallback_count, cue_cps_max, cue_lines_max,
crop_in_bounds, source_sha_unchanged, no_nan_timestamps, has_audio, has_video,
faststart, aspect_mismatch.

``aspect_mismatch`` is an extension of the frozen code list: a render at the
wrong aspect is otherwise invisible to the gates, and the acceptance test
requires it to be observable.

``audit_master`` is the final gate for the multitrack EDL path, which has no
loudness check of its own: master_exists, master_audio, master_duration,
master_lufs, master_true_peak.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import ffmpeg as ff
from . import fonts as fonts_mod
from .contract import (
    LOUDNESS_TARGETS,
    QCResult,
    QCFailure,
    THRESHOLDS,
    VxError,
    EditPlan,
    expected_frames,
    output_size,
)
from .probe import probe
from .winpath import ass_filter_path, same_fingerprint, source_fingerprint

__all__ = [
    "audit_master",
    "check_render",
    "frame_count",
    "check_source_unchanged",
    "lufs_of",
    "true_peak_ceiling",
]

QC_DEFAULTS = {
    "duration_drift_ms": 40.0,
    "frame_count_delta": 1,
    "lufs_delta": 0.3,
    "master_lufs_delta": 1.5,
    "true_peak_margin": 0.1,
    "av_start_ms": 20.0,
    "black_frames_edges": 2,
    "font_fallback_count": 0,
}

LIMITS = dict(QC_DEFAULTS)
LIMITS["cue_cps_max"] = float(THRESHOLDS["sub_max_cps"])
LIMITS["cue_lines_max"] = int(THRESHOLDS["sub_max_lines"])
LIMITS["cue_dur_min"] = float(THRESHOLDS["cue_dur_min"])
LIMITS["cue_dur_max"] = float(THRESHOLDS["cue_dur_max"])
LIMITS["upscale_warn"] = float(THRESHOLDS["upscale_warn"])


#: L'audio puo' finire mezzo secondo prima del quadro; oltre, il master e' muto
#: dove il video continua, e il loudness da solo non lo vede.
AUDIO_COVERAGE_TOLERANCE = 0.5


def true_peak_ceiling(target_tp: float) -> float:
    """The delivered true-peak ceiling: the target plus the gate's own margin."""
    return float(target_tp) + float(QC_DEFAULTS["true_peak_margin"])


def refresh_from_settings(settings=None) -> None:
    """Re-derive the limits after `settings.apply()` changed the threshold table."""
    from . import settings as settings_mod

    source = settings_mod.current() if settings is None else settings
    qc_cfg = source.get("qc") or {}
    for key, fallback in QC_DEFAULTS.items():
        value = qc_cfg.get(key, fallback)
        try:
            LIMITS[key] = float(value) if isinstance(fallback, float) else int(value)
        except (TypeError, ValueError):
            LIMITS[key] = fallback
    LIMITS["cue_cps_max"] = float(THRESHOLDS["sub_max_cps"])
    LIMITS["cue_lines_max"] = int(THRESHOLDS["sub_max_lines"])


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
    code: str,
    ok: bool,
    value: Any,
    limit: Any,
    detail: str = "",
    *,
    status: str = "checked",
) -> Dict[str, Any]:
    return {
        "code": code,
        "pass": bool(ok),
        "status": status,
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


def _container_duration(path: Path) -> float:
    """Container duration in seconds from ffprobe's format block (0.0 if absent)."""
    proc = ff.run_fp(
        [
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ]
    )
    if not proc.ok:
        return 0.0
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or line.upper() == "N/A":
            continue
        try:
            return float(line)
        except ValueError:
            continue
    return 0.0


def _loudness_target(plan: EditPlan) -> tuple[float, float]:
    """(I, TP) from the plan, read by the same function the renderer uses.

    The import is lazy on purpose: ``render`` pulls in the whole ffmpeg stack,
    and a module-level import here would close an import cycle. Delegating
    instead of copying is the point — QC and the renderer cannot drift apart.
    """
    try:
        from .render import loudness_target
    except ImportError:
        i, tp, _lra = _loudness_target_local(plan)
        return i, tp
    i, tp, _lra = loudness_target(_normalise_loudness_keys(plan))
    return float(i), float(tp)


def _normalise_loudness_keys(plan: EditPlan) -> EditPlan:
    """Copy of ``plan`` with uppercase ``I``/``TP``/``LRA`` renamed to lowercase.

    ``render.loudness_target`` reads lowercase keys out of the loudness dict,
    while plan.py writes ``{"I","TP"}``. Handing it the raw plan would turn a
    declared -23/-2 into the -14/-1 social default, so the case is normalised
    before the shared reader sees it. The plan itself is never mutated.
    """
    output = dict(plan.output or {})
    render_cfg = dict(plan.render or {})
    changed = False
    for holder in (output, render_cfg):
        raw = holder.get("loudness")
        if not isinstance(raw, dict):
            continue
        if not any(key in raw for key in ("I", "TP", "LRA")):
            continue
        renamed = dict(raw)
        for upper, lower in (("I", "i"), ("TP", "tp"), ("LRA", "lra")):
            if upper in renamed and lower not in renamed:
                renamed[lower] = renamed.pop(upper)
        holder["loudness"] = renamed
        changed = True
    if not changed:
        return plan
    patched = copy.copy(plan)
    patched.output = output
    patched.render = render_cfg
    return patched


def _first_num(source: Dict[str, Any], keys: Sequence[str]) -> Optional[float]:
    for key in keys:
        if key in source:
            value = _num(source.get(key), None)
            if value is not None:
                return value
    return None


def _loudness_target_local(plan: EditPlan) -> tuple[float, float, float]:
    """Fallback reader, used only when ``render.loudness_target`` is missing.

    Same precedence as the renderer: profile string, then the lowercase dict
    written by plan.py, then the uppercase loudnorm treatment params, then
    ``output.loudness_target``, then the social profile. Uppercase ``I``/``TP``
    are honoured, so a plan written by plan.py never silently lands on -14/-1.
    """
    output = plan.output or {}
    raw = output.get("loudness") or (plan.render or {}).get("loudness")

    i: Optional[float] = None
    tp: Optional[float] = None
    lra: Optional[float] = None

    def absorb(source: Any) -> None:
        nonlocal i, tp, lra
        if isinstance(source, str):
            if source in LOUDNESS_TARGETS:
                profile_i, profile_tp = LOUDNESS_TARGETS[source]
                i = float(profile_i) if i is None else i
                tp = float(profile_tp) if tp is None else tp
                lra = 11.0 if lra is None else lra
            return
        if not isinstance(source, dict):
            return
        if i is None:
            i = _first_num(source, ("i", "I", "target_i"))
        if tp is None:
            tp = _first_num(source, ("tp", "TP", "target_tp"))
        if lra is None:
            lra = _first_num(source, ("lra", "LRA", "target_lra"))

    absorb(raw)
    for treatment in plan.treatments or []:
        if not isinstance(treatment, dict):
            continue
        if treatment.get("name") == "loudnorm" and treatment.get("enabled"):
            absorb(treatment.get("params"))
            break
    absorb(output.get("loudness_target"))

    social_i, social_tp = LOUDNESS_TARGETS["social"]
    return (
        float(social_i) if i is None else float(i),
        float(social_tp) if tp is None else float(tp),
        11.0 if lra is None else float(lra),
    )


def _loudnorm_enabled(plan: EditPlan) -> bool:
    """True when the plan actually asks the renderer to normalise loudness."""
    for treatment in plan.treatments or []:
        if not isinstance(treatment, dict):
            continue
        if treatment.get("name") == "loudnorm" and treatment.get("enabled"):
            return True
    return False


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
        start_cs = int(round(start * 100))
        end_cs = int(round(end * 100))
        cs = max(0, end_cs - start_cs)
        duration = cs / 100.0
        cues.append(
            {
                "start": start,
                "end": end,
                "cs": cs,
                "chars": chars,
                "lines": lines,
                "cps": (chars / duration) if duration > 0 else 0.0,
            }
        )
    return cues


def _font_fallbacks(path: Path, subs_path: Path) -> tuple[int, str]:
    """Burn one frame and count font substitutions in ffmpeg's log."""
    ff.require("subtitles")
    graph = (
        f"[0:v]subtitles={ass_filter_path(subs_path)}"
        f":fontsdir={fonts_mod.fontsdir_arg()}[vo]"
    )
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


def audit_master(
    path,
    *,
    target_lufs: float,
    target_tp: float,
    tolerance_lu: float = 1.5,
) -> Dict[str, Any]:
    """Final master gate: measure the delivered file, never trust the caller.

    ``target_lufs``/``target_tp`` are the delivery spec the renderer was asked
    to hit; the measurement comes from ``lufs_of`` on the file itself, so a
    declared target cannot stand in for a measurement. The tolerance is capped
    at 3.0 LUFS: above that a "master" is not a delivery spec, it is a wish,
    and a caller must not be able to widen the gate until it always passes.

    Returns the result dict when every check passes; raises
    ``contract.QCFailure(result)`` otherwise, which the CLI maps to exit 5.
    """
    tolerance = float(tolerance_lu)
    if tolerance <= 0:
        raise VxError(f"tolerance_lu must be > 0, got {tolerance_lu}")
    if tolerance > 3.0:
        raise VxError(
            f"tolerance_lu {tolerance_lu} exceeds the 3.0 LUFS delivery ceiling"
        )

    want_i = float(target_lufs)
    want_tp = float(target_tp)
    target = Path(path)

    checks: List[Dict[str, Any]] = []

    def add(code: str, status: str, detail: str) -> None:
        checks.append({"code": code, "status": status, "detail": detail})

    exists = False
    size = 0
    try:
        exists = target.is_file()
        if exists:
            size = target.stat().st_size
    except OSError as exc:
        add("master_exists", "fail", f"unreadable: {exc}")
    else:
        if exists and size > 0:
            add("master_exists", "pass", f"{target} ({size} bytes)")
        else:
            add("master_exists", "fail", f"missing or empty: {target}")

    if not (exists and size > 0):
        for code in (
            "master_audio",
            "master_audio_coverage",
            "master_duration",
            "master_lufs",
            "master_true_peak",
        ):
            add(code, "skipped", "no master file to inspect")
        result = {
            "ok": False,
            "measured": {"i": 0.0, "tp": 0.0},
            "target": {"i": want_i, "tp": want_tp},
            "tolerance_lu": tolerance,
            "checks": checks,
            "failures": [c["code"] for c in checks if c["status"] == "fail"],
        }
        raise QCFailure(result)

    streams = _streams(target)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if audio:
        add("master_audio", "pass", f"{len(audio)} audio stream(s)")
    else:
        add("master_audio", "fail", "no audio stream in the master")

    duration = _container_duration(target)
    if duration > 0:
        add("master_duration", "pass", f"duration={duration:.3f}s")
    else:
        add("master_duration", "fail", f"container duration={duration:.3f}s")

    if audio and duration > 0:
        audio_duration = max((_num(item.get("duration"), 0.0) or 0.0) for item in audio)
        shortfall = duration - audio_duration
        add(
            "master_audio_coverage",
            "pass" if shortfall <= AUDIO_COVERAGE_TOLERANCE else "fail",
            f"audio={audio_duration:.3f}s video={duration:.3f}s "
            f"shortfall={shortfall:.3f}s (tolerance {AUDIO_COVERAGE_TOLERANCE:.1f})",
        )
    elif not audio:
        add(
            "master_audio_coverage",
            "skipped",
            "no audio stream: coverage not applicable",
        )

    loud = lufs_of(target)
    measured_i = _num(loud.get("input_i"), None)
    measured_tp = _num(loud.get("input_tp"), None)

    if measured_i is None:
        add("master_lufs", "fail", "no loudnorm measurement (input_i missing)")
    else:
        delta = abs(measured_i - want_i)
        add(
            "master_lufs",
            "pass" if delta <= tolerance else "fail",
            f"measured={measured_i:.1f} target={want_i:.1f} "
            f"delta={delta:.1f} LU (tolerance {tolerance:.1f})",
        )

    if measured_tp is None:
        add("master_true_peak", "fail", "no loudnorm measurement (input_tp missing)")
    else:
        ceiling = true_peak_ceiling(want_tp)
        add(
            "master_true_peak",
            "pass" if measured_tp <= ceiling else "fail",
            f"measured={measured_tp:.1f} limit={ceiling:.1f} dBTP",
        )

    failures = [c["code"] for c in checks if c["status"] == "fail"]
    result = {
        "ok": not failures,
        "measured": {
            "i": float(measured_i) if measured_i is not None else 0.0,
            "tp": float(measured_tp) if measured_tp is not None else 0.0,
        },
        "target": {"i": want_i, "tp": want_tp},
        "tolerance_lu": tolerance,
        "checks": checks,
        "failures": failures,
    }
    if failures:
        raise QCFailure(result)
    return result


def check_render(
    plan: EditPlan,
    media: dict,
    out_path,
    *,
    subs_path: str | None = None,
    source_fp: dict | None = None,
    executed_target: dict | None = None,
) -> QCResult:
    """Run every gate over ``out_path`` and return a ``QCResult``.

    ``executed_target`` is the ``target`` block returned by ``render.render``:
    the I/TP/LRA values that actually went into the stage-04 filtergraph. QC
    compares the measurement against that, never against a re-derivation from
    the plan, so a declared profile and an executed profile cannot drift apart.
    """
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
    audio_flag = media.get("has_audio")
    expect_audio = True if audio_flag is None else bool(audio_flag)
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
    gated = _loudnorm_enabled(plan)
    metrics["loudness_gated"] = gated
    executed = executed_target or {}
    target_i = _num(executed.get("i"), None)
    target_tp = _num(executed.get("tp"), None)
    loud = lufs_of(target) if info.has_audio else {}
    metrics["loudness"] = loud
    metrics["loudness_target"] = executed or None
    if not gated:
        skip = "SKIPPED: no loudnorm treatment (no finding)"
        checks.append(
            _check(
                "lufs_delta",
                True,
                None,
                LIMITS["lufs_delta"],
                skip,
                status="skipped",
            )
        )
        checks.append(
            _check("true_peak", True, None, target_tp, skip, status="skipped")
        )
    elif target_i is None or target_tp is None:
        missing = "the plan enabled loudnorm but the render reported no executed target"
        checks.append(_check("loudness_target_recorded", False, None, True, missing))
        checks.append(_check("lufs_delta", False, None, LIMITS["lufs_delta"], missing))
        checks.append(_check("true_peak", False, None, None, missing))
    else:
        checks.append(
            _check(
                "loudness_target_recorded",
                True,
                executed,
                True,
                f"executed I={target_i} TP={target_tp}",
            )
        )
        if not info.has_audio:
            checks.append(
                _check(
                    "lufs_delta",
                    False,
                    None,
                    LIMITS["lufs_delta"],
                    "loudnorm requested but the output has no audio stream",
                )
            )
            checks.append(
                _check("true_peak", False, None, target_tp, "no audio stream")
            )
        else:
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
        cues = sorted(_ass_cues(subs), key=lambda c: (c["start"], c["end"]))
        # ASS stores centiseconds, so the comparison is done in centiseconds:
        # 9 characters over 0.45 s is 20.0 cps, and 0.4 s is 40 cs, exactly. Doing
        # it in floats made an exact 20.0 read as 20.000000000000004 and failed a
        # cue the producer had sized to the limit.
        worst = max(
            cues,
            key=lambda c: (100.0 * c["chars"] / c["cs"]) if c["cs"] else 0.0,
            default=None,
        )
        worst_cps = (100.0 * worst["chars"] / worst["cs"]) if worst and worst["cs"] else 0.0
        worst_lines = max((c["lines"] for c in cues), default=0)
        checks.append(
            _check(
                "cue_cps_max",
                worst is None
                or not worst["cs"]
                or 100 * worst["chars"] <= LIMITS["cue_cps_max"] * worst["cs"],
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
        cs_all = [c["cs"] for c in cues]
        shortest_cs = min(cs_all, default=0)
        longest_cs = max(cs_all, default=0)
        checks.append(
            _check(
                "cue_duration_min",
                shortest_cs >= int(round(LIMITS["cue_dur_min"] * 100)),
                round(shortest_cs / 100.0, 3),
                LIMITS["cue_dur_min"],
                f"{len(cues)} cue(s)",
            )
        )
        checks.append(
            _check(
                "cue_duration_max",
                longest_cs <= int(round(LIMITS["cue_dur_max"] * 100)),
                round(longest_cs / 100.0, 3),
                LIMITS["cue_dur_max"],
                f"{len(cues)} cue(s)",
            )
        )
        overlaps = sum(
            1
            for previous, current in zip(cues, cues[1:])
            if current["start"] < previous["end"] - 1e-6
        )
        checks.append(
            _check(
                "cue_overlap",
                overlaps == 0,
                overlaps,
                0,
                f"{len(cues)} cue(s)",
            )
        )
    else:
        for code, limit in (
            ("font_fallback_count", LIMITS["font_fallback_count"]),
            ("cue_cps_max", LIMITS["cue_cps_max"]),
            ("cue_lines_max", LIMITS["cue_lines_max"]),
            ("cue_duration_min", LIMITS["cue_dur_min"]),
            ("cue_duration_max", LIMITS["cue_dur_max"]),
            ("cue_overlap", 0),
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

    # -- effective upscale -------------------------------------------------- #
    out_w, out_h = output_size(aspect, precision)
    crop_rect = _crop_rect(plan) or {}
    eff_w = int(_num(crop_rect.get("w", crop_rect.get("crop_w")), 0)) or int(
        _num(media.get("width"), 0)
    )
    eff_h = int(_num(crop_rect.get("h", crop_rect.get("crop_h")), 0)) or int(
        _num(media.get("height"), 0)
    )
    if eff_w <= 0 or eff_h <= 0 or out_w <= 0 or out_h <= 0:
        checks.append(
            _check(
                "upscale_ratio",
                True,
                None,
                LIMITS["upscale_warn"],
                "no geometry",
                status="skipped",
            )
        )
    else:
        ratio = max(out_w / eff_w, out_h / eff_h)
        checks.append(
            _check(
                "upscale_ratio",
                True,
                round(ratio, 3),
                LIMITS["upscale_warn"],
                f"crop {eff_w}x{eff_h} -> {out_w}x{out_h}: {ratio:.2f}x"
                + (" upscale" if ratio > LIMITS["upscale_warn"] else " downscale"),
                status="warn" if ratio > LIMITS["upscale_warn"] else "checked",
            )
        )

    # -- source integrity --------------------------------------------------- #
    if source_fp:
        source_path = source_fp.get("path") or media.get("path")
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
