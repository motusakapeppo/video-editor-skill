"""vx.plan — findings, the triage gate, and the edit plan.

The law of this module: **nessun finding -> nessun trattamento**. Every enabled
treatment cites a ``finding_id`` that exists in ``report["findings"]``; the plan
publishes its own findings there before building anything.

stdlib + ``vx.contract`` + ``vx.report`` only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import report as report_mod
from .contract import (
    ASPECTS,
    EXIT_NEEDS_INPUT,
    EXIT_OK,
    EXIT_USAGE,
    LOUDNESS_TARGETS,
    THRESHOLDS,
    EditPlan,
    clamp,
    output_size,
    target_crop_size,
    validate_plan,
)

__all__ = [
    "FINDING_IDS",
    "detect_findings",
    "needs_triage",
    "build_plan",
    "validate",
    "deliverables",
    "apply_answers",
    "main",
]

FINDING_IDS: tuple[str, ...] = (
    "video.jitter.shaky",
    "video.exposure.under",
    "video.exposure.high_clip",
    "video.color.cast",
    "video.freeze.static",
    "video.pacing.drag",
    "video.scene.cuts",
    "audio.noise.high",
    "audio.dead_air.excess",
    "audio.clipping",
    "audio.loudness.off",
    "audio.lra.wide",
    "audio.silent",
    "faces.none",
    "faces.single",
    "faces.multi_span",
    "faces.track_broken",
    "speech.present",
    "speech.fast",
    "speech.filler",
    "format.unknown",
    "hook.missing",
    "subs.unknown",
)

TRIAGE_ONLY = frozenset({"format.unknown", "hook.missing", "subs.unknown"})
CREATIVE_PRIORITY = ("autoframe", "hook_overlay", "zoom", "fit")
FACE_PRIORITY = ("faces.multi_span", "faces.track_broken", "faces.single", "faces.none")

_ANSWER_KEYS = {"target_format": "format", "hook": "hook", "subtitles": "subtitles"}
_PASSTHROUGH = (
    "platform",
    "tone",
    "loudness",
    "zoom",
    "fit",
    "autoframe",
    "hook_text",
    "timelapse",
)
_NORMALISE = {
    "nessuno": "none",
    "testo": "text",
    "verticale": "9:16",
    "orizzontale": "16:9",
    "quadrato": "1:1",
}


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _num(value: Any) -> Optional[float]:
    """Float or None; booleans and junk are not numbers."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _finding(
    fid: str,
    severity: str,
    metric: str,
    value: Any,
    threshold: Any,
    evidence: str,
    treatment: Optional[str],
) -> Dict[str, Any]:
    return {
        "id": fid,
        "severity": severity,
        "metric": metric,
        "value": value,
        "threshold": threshold,
        "evidence": evidence,
        "suggested_treatment": treatment,
    }


def _aspect_of(width: int, height: int) -> Optional[str]:
    if not width or not height:
        return None
    ratio = width / height
    for name, (aw, ah) in ASPECTS.items():
        if abs(ratio - aw / ah) < 0.01:
            return name
    return "other"


def _default_platform(aspect: str) -> str:
    if aspect == "9:16":
        return "tiktok"
    if aspect in ("4:5", "1:1"):
        return "reels"
    if aspect == "16:9":
        return "youtube"
    return "tiktok"


def _is_vertical(aspect: str) -> bool:
    w, h = output_size(aspect)
    return h > w


# --------------------------------------------------------------------------- #
# findings
# --------------------------------------------------------------------------- #


def detect_findings(report: dict) -> List[Dict[str, Any]]:
    """Every gate in the matrix, evaluated against the real numbers only."""
    out: List[Dict[str, Any]] = []
    if not isinstance(report, dict):
        return out

    source = report.get("source") or {}
    analysis = report.get("analysis") or {}
    video = analysis.get("video") or {}
    audio = analysis.get("audio") or {}
    faces = analysis.get("faces") or {}
    speech = analysis.get("speech") or {}

    duration = _num(source.get("duration")) or 0.0

    # -- video ------------------------------------------------------------- #
    jitter = video.get("jitter") or {}
    score = _num(jitter.get("score"))
    if score is not None and score >= THRESHOLDS["jitter_shaky"]:
        bands = THRESHOLDS["jitter_bands"]
        severity = "critical" if score >= bands[2] else "warn"
        head = _num(jitter.get("headroom_pct"))
        head_txt = f"{head:.0f}%" if head is not None else "n/d"
        out.append(
            _finding(
                "video.jitter.shaky",
                severity,
                "jitter.score",
                round(score, 3),
                THRESHOLDS["jitter_shaky"],
                f"Tremolio {score:.2f} (soglia {THRESHOLDS['jitter_shaky']:.2f}), "
                f"headroom {head_txt}",
                "stabilize",
            )
        )

    exposure = video.get("exposure") or {}
    luma = _num(exposure.get("mean_luma"))
    if luma is not None and luma < THRESHOLDS["luma_under"]:
        out.append(
            _finding(
                "video.exposure.under",
                "warn",
                "exposure.mean_luma",
                round(luma, 2),
                THRESHOLDS["luma_under"],
                f"Luminanza media {luma:.1f} (soglia {THRESHOLDS['luma_under']:.1f}): "
                "immagine sottoesposta",
                "exposure_fix",
            )
        )
    clip_high = _num(exposure.get("clip_high_pct"))
    if clip_high is not None and clip_high >= THRESHOLDS["clip_high_pct"]:
        severity = (
            "critical" if clip_high >= THRESHOLDS["clip_high_block_pct"] else "warn"
        )
        out.append(
            _finding(
                "video.exposure.high_clip",
                severity,
                "exposure.clip_high_pct",
                round(clip_high, 2),
                THRESHOLDS["clip_high_pct"],
                f"Alte luci bruciate {clip_high:.1f}% "
                f"(soglia {THRESHOLDS['clip_high_pct']:.1f}%)",
                "exposure_fix",
            )
        )

    color = video.get("color") or {}
    cast_rg = _num(color.get("cast_rg"))
    cast_by = _num(color.get("cast_by"))
    rg_bad = cast_rg is not None and abs(cast_rg) > THRESHOLDS["cast_rg"]
    by_bad = cast_by is not None and abs(cast_by) > THRESHOLDS["cast_by"]
    if rg_bad or by_bad:
        worst = max(abs(cast_rg or 0.0), abs(cast_by or 0.0))
        out.append(
            _finding(
                "video.color.cast",
                "warn",
                "color.cast",
                round(worst, 2),
                THRESHOLDS["cast_rg"],
                f"Dominante colore R/G {cast_rg or 0.0:+.1f} B/Y {cast_by or 0.0:+.1f} "
                f"(soglia {THRESHOLDS['cast_rg']:.0f})",
                "color_fix",
            )
        )

    worst_freeze: Optional[float] = None
    for freeze in video.get("freezes") or []:
        if not isinstance(freeze, dict):
            continue
        span = _num(freeze.get("duration"))
        if span is None:
            start = _num(freeze.get("start"))
            end = _num(freeze.get("end"))
            span = (end - start) if (start is not None and end is not None) else None
        if span is not None and span >= THRESHOLDS["freeze_min"]:
            if worst_freeze is None or span > worst_freeze:
                worst_freeze = span
    if worst_freeze is not None:
        out.append(
            _finding(
                "video.freeze.static",
                "warn",
                "freeze.duration",
                round(worst_freeze, 2),
                THRESHOLDS["freeze_min"],
                f"Immagine ferma per {worst_freeze:.1f}s "
                f"(soglia {THRESHOLDS['freeze_min']:.1f}s)",
                "cut_silence",
            )
        )

    pacing = video.get("pacing") or {}
    motion = video.get("motion") or {}
    shot_len = _num(pacing.get("mean_shot_len"))
    motion_mean = _num(motion.get("mean"))
    if (
        shot_len is not None
        and shot_len > THRESHOLDS["hook_window"]
        and motion_mean is not None
        and motion_mean < 1.0
    ):
        out.append(
            _finding(
                "video.pacing.drag",
                "info",
                "pacing.mean_shot_len",
                round(shot_len, 2),
                THRESHOLDS["hook_window"],
                f"Inquadratura media {shot_len:.1f}s "
                f"(soglia {THRESHOLDS['hook_window']:.1f}s) con movimento "
                f"{motion_mean:.2f}: il ritmo trascina",
                "pacing_fix",
            )
        )

    scene_cuts = video.get("scene_cuts") or []
    if isinstance(scene_cuts, list) and scene_cuts:
        out.append(
            _finding(
                "video.scene.cuts",
                "info",
                "video.scene_cuts",
                len(scene_cuts),
                THRESHOLDS["scene_cut"],
                f"{len(scene_cuts)} cambi scena rilevati "
                f"(soglia {THRESHOLDS['scene_cut']:.2f}/s)",
                "hook_overlay",
            )
        )

    # -- audio ------------------------------------------------------------- #
    snr = _num(audio.get("snr_db"))
    if snr is not None and snr < THRESHOLDS["noise_snr_low"]:
        out.append(
            _finding(
                "audio.noise.high",
                "warn",
                "audio.snr_db",
                round(snr, 2),
                THRESHOLDS["noise_snr_low"],
                f"Rapporto segnale/rumore {snr:.1f} dB "
                f"(soglia {THRESHOLDS['noise_snr_low']:.1f} dB)",
                "denoise",
            )
        )

    dead_total = 0.0
    for span in audio.get("dead_air") or []:
        if not isinstance(span, dict):
            continue
        length = _num(span.get("duration"))
        if length is None:
            start = _num(span.get("start"))
            end = _num(span.get("end"))
            length = (end - start) if (start is not None and end is not None) else 0.0
        dead_total += max(0.0, length)
    if duration > 0 and dead_total / duration >= THRESHOLDS["dead_air_ratio"]:
        ratio = dead_total / duration
        out.append(
            _finding(
                "audio.dead_air.excess",
                "warn",
                "audio.dead_air_ratio",
                round(ratio, 3),
                THRESHOLDS["dead_air_ratio"],
                f"Silenzi morti {dead_total:.1f}s su {duration:.1f}s "
                f"({100 * ratio:.0f}%, soglia "
                f"{100 * THRESHOLDS['dead_air_ratio']:.0f}%)",
                "cut_silence",
            )
        )

    clipping = _num(audio.get("clipping_pct"))
    if clipping is not None and clipping > THRESHOLDS["clip_pct"]:
        out.append(
            _finding(
                "audio.clipping",
                "warn",
                "audio.clipping_pct",
                round(clipping, 3),
                THRESHOLDS["clip_pct"],
                f"Clipping sul {clipping:.2f}% dei campioni "
                f"(soglia {THRESHOLDS['clip_pct']:.2f}%)",
                "clipping_fix",
            )
        )

    loudness = audio.get("loudness") or {}
    input_i = _num(loudness.get("input_i"))
    if input_i is not None and abs(input_i - (-14.0)) >= 1.0:
        out.append(
            _finding(
                "audio.loudness.off",
                "warn",
                "audio.loudness.input_i",
                round(input_i, 2),
                -14.0,
                f"Loudness integrata {input_i:.1f} LUFS (target -14.0, tolleranza 1.0)",
                "loudnorm",
            )
        )
    lra = _num(loudness.get("input_lra"))
    if lra is not None and lra > THRESHOLDS["lra_wide"]:
        out.append(
            _finding(
                "audio.lra.wide",
                "warn",
                "audio.loudness.input_lra",
                round(lra, 2),
                THRESHOLDS["lra_wide"],
                f"Range dinamico {lra:.1f} LU (soglia {THRESHOLDS['lra_wide']:.1f} LU)",
                "compress",
            )
        )

    if source.get("has_audio") is False:
        out.append(
            _finding(
                "audio.silent",
                "warn",
                "source.has_audio",
                False,
                True,
                "La sorgente non ha traccia audio",
                "audio_restore",
            )
        )

    # -- faces ------------------------------------------------------------- #
    detected_ratio = _num(faces.get("detected_ratio"))
    if detected_ratio is not None:
        if detected_ratio == 0:
            out.append(
                _finding(
                    "faces.none",
                    "info",
                    "faces.detected_ratio",
                    0.0,
                    0.0,
                    "Nessun volto rilevato nel girato",
                    "autoframe",
                )
            )
        else:
            concurrent = _num(faces.get("max_concurrent"))
            if concurrent is not None and int(concurrent) == 1:
                out.append(
                    _finding(
                        "faces.single",
                        "info",
                        "faces.max_concurrent",
                        1,
                        1,
                        "Un solo volto in scena: posso seguirlo",
                        "autoframe",
                    )
                )
            span_x = _num(faces.get("span_x_ratio"))
            if span_x is not None and span_x > 0.95:
                out.append(
                    _finding(
                        "faces.multi_span",
                        "warn",
                        "faces.span_x_ratio",
                        round(span_x, 3),
                        0.95,
                        f"I volti coprono il {100 * span_x:.0f}% della larghezza "
                        "(soglia 95%)",
                        "autoframe",
                    )
                )
            persistence = [
                _num(track.get("persistence"))
                for track in (faces.get("tracks") or [])
                if isinstance(track, dict)
            ]
            persistence = [p for p in persistence if p is not None]
            if persistence and max(persistence) < 0.5:
                out.append(
                    _finding(
                        "faces.track_broken",
                        "warn",
                        "faces.persistence",
                        round(max(persistence), 2),
                        0.5,
                        f"Traccia volto instabile: persistenza "
                        f"{max(persistence):.2f} (soglia 0.50)",
                        "autoframe",
                    )
                )

    # -- speech ------------------------------------------------------------ #
    words = _num(speech.get("word_count"))
    if words is not None and words > 0:
        out.append(
            _finding(
                "speech.present",
                "info",
                "speech.word_count",
                int(words),
                0,
                f"{int(words)} parole trascritte",
                "subtitles",
            )
        )
        wpm = _num(speech.get("wpm"))
        if wpm is not None and wpm > THRESHOLDS["speech_wpm_fast"]:
            out.append(
                _finding(
                    "speech.fast",
                    "warn",
                    "speech.wpm",
                    round(wpm, 1),
                    THRESHOLDS["speech_wpm_fast"],
                    f"Parlato a {wpm:.0f} parole/min "
                    f"(soglia {THRESHOLDS['speech_wpm_fast']}): i sottotitoli "
                    "vanno spezzati",
                    "subtitles",
                )
            )
        fillers = _num(speech.get("fillers"))
        if fillers is not None and fillers / words > 0.08:
            ratio = fillers / words
            out.append(
                _finding(
                    "speech.filler",
                    "info",
                    "speech.fillers_ratio",
                    round(ratio, 3),
                    0.08,
                    f"{int(fillers)} intercalari su {int(words)} parole "
                    f"({100 * ratio:.0f}%, soglia 8%)",
                    "filler_cut",
                )
            )
    if not speech:
        out.append(
            _finding(
                "subs.unknown",
                "info",
                "analysis.speech",
                None,
                None,
                "Nessuna analisi del parlato: non so se servono sottotitoli",
                None,
            )
        )

    # -- format / hook ----------------------------------------------------- #
    if (
        _num(source.get("width")) is None
        or _num(source.get("height")) is None
        or _num(source.get("duration")) is None
    ):
        out.append(
            _finding(
                "format.unknown",
                "info",
                "source.format",
                None,
                None,
                "Formato sorgente incompleto: larghezza, altezza o durata mancanti",
                None,
            )
        )

    retention = video.get("retention")
    if isinstance(retention, dict):
        candidates = retention.get("hook_candidates") or []
        if not candidates and duration > THRESHOLDS["hook_window"]:
            out.append(
                _finding(
                    "hook.missing",
                    "info",
                    "retention.hook_candidates",
                    0,
                    1,
                    f"Nessun aggancio nei primi {THRESHOLDS['hook_window']:.0f}s",
                    "hook_overlay",
                )
            )

    return out


def _publish(report: dict, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Refresh findings by id, keep foreign ids, return the merged list."""
    existing = report.get("findings")
    if not isinstance(existing, list):
        existing = []
    fresh = {f["id"] for f in findings}
    kept = [f for f in existing if isinstance(f, dict) and f.get("id") not in fresh]
    report["findings"] = kept + findings
    return report["findings"]


# --------------------------------------------------------------------------- #
# triage gate
# --------------------------------------------------------------------------- #


def _brief_complete(brief: dict) -> bool:
    fmt = brief.get("format")
    hook = brief.get("hook")
    subs = brief.get("subtitles")
    return (
        isinstance(fmt, str)
        and fmt in ASPECTS
        and isinstance(hook, str)
        and hook.strip() != ""
        and isinstance(subs, str)
        and subs.strip() != ""
    )


def needs_triage(brief: dict, report: dict) -> Optional[dict]:
    """None when the brief answers format/hook/subtitles, else the briefing."""
    brief = brief if isinstance(brief, dict) else {}
    if _brief_complete(brief):
        return None
    from . import triage

    return triage.build_briefing(report if isinstance(report, dict) else {}, brief)


# --------------------------------------------------------------------------- #
# answers
# --------------------------------------------------------------------------- #


def apply_answers(brief: dict, answers: dict) -> dict:
    """Copy the brief and fold the user's answers into it."""
    out = dict(brief or {})
    if not isinstance(answers, dict):
        return out
    payload = answers.get("answers")
    if not isinstance(payload, dict):
        payload = answers
    for key, value in payload.items():
        if isinstance(value, dict):
            value = value.get("value", value.get("answer"))
        if isinstance(value, str):
            stripped = value.strip()
            value = _NORMALISE.get(stripped.lower(), stripped)
        if key in _ANSWER_KEYS:
            out[_ANSWER_KEYS[key]] = value
        elif key in _PASSTHROUGH:
            out[key] = value
    return out


# --------------------------------------------------------------------------- #
# cuts
# --------------------------------------------------------------------------- #


def _build_cuts(
    spans: List[Dict[str, float]], duration: float
) -> tuple[List[Dict[str, float]], float]:
    """Turn removal spans into keep-ranges under the removal budget."""
    if duration <= 0:
        return [], 0.0

    pad = THRESHOLDS["silence_pad"]
    cleaned: List[tuple[float, float]] = []
    for span in spans:
        start = max(0.0, float(span.get("start", 0.0)) + pad)
        end = min(duration, float(span.get("end", 0.0)) - pad)
        if end - start >= 0.4:
            cleaned.append((start, end))
    cleaned.sort()

    merged: List[tuple[float, float]] = []
    for start, end in cleaned:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    budget = THRESHOLDS["dead_air_max_cut"] * duration
    total = sum(end - start for start, end in merged)
    while total > budget and merged:
        idx = max(range(len(merged)), key=lambda i: merged[i][1] - merged[i][0])
        total -= merged[idx][1] - merged[idx][0]
        merged.pop(idx)

    keep: List[Dict[str, float]] = []
    cursor = 0.0
    for start, end in merged:
        if start - cursor >= 0.05:
            keep.append({"in": round(cursor, 3), "out": round(start, 3)})
        cursor = max(cursor, end)
    if duration - cursor >= 0.05:
        keep.append({"in": round(cursor, 3), "out": round(duration, 3)})

    removed = duration - sum(item["out"] - item["in"] for item in keep)
    return keep, removed


def _is_full_range(cuts: List[Dict[str, float]], duration: float) -> bool:
    return (
        len(cuts) == 1 and cuts[0]["in"] <= 0.001 and cuts[0]["out"] >= duration - 0.001
    )


# --------------------------------------------------------------------------- #
# autoframe / subtitles params
# --------------------------------------------------------------------------- #


def _autoframe_params(
    face_id: str, faces: dict, src_w: int, src_h: int, aspect: str
) -> tuple[Dict[str, Any], bool, str]:
    if face_id == "faces.none":
        return {"mode": "center"}, True, "nessun volto: centro geometrico"

    if face_id == "faces.track_broken":
        return {"mode": "lock"}, True, "traccia instabile: blocco su un punto fisso"

    crop_h = target_crop_size(src_w, src_h, aspect)[1] if src_w and src_h else 0

    if face_id == "faces.single":
        params: Dict[str, Any] = {"mode": "track", "eye_line": 0.38}
        areas = [
            _num(track.get("mean_area"))
            for track in (faces.get("tracks") or [])
            if isinstance(track, dict)
        ]
        areas = [a for a in areas if a is not None]
        if areas and crop_h:
            face_h = (max(areas) ** 0.5) * src_h
            if face_h / crop_h > 0.55:
                return (
                    {"mode": "lock", "eye_line": 0.38},
                    True,
                    "volto grande nell'inquadratura: blocco invece di inseguire",
                )
        return params, True, "un volto: inseguo con eye-line al 38%"

    params = {"mode": "fit", "gblur_sigma": 20}
    if src_h and crop_h and crop_h / src_h < 0.4:
        return (
            params,
            False,
            "il crop verticale taglierebbe oltre il 60% dell'altezza: non lo faccio",
        )
    return params, True, "volti distanti: riempio con fit e sfondo sfocato"


def _sub_style(brief: dict, aspect: str, speech: dict) -> str:
    choice = brief.get("subtitles")
    if choice in ("karaoke", "clean"):
        return choice
    if brief.get("tone") == "clean":
        return "clean"
    wpm = _num(speech.get("wpm")) or 0.0
    return "karaoke" if (wpm > 150 and _is_vertical(aspect)) else "clean"


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #


def _settings() -> Dict[str, Any]:
    try:
        from . import settings

        return settings.current()
    except Exception:  # noqa: BLE001 - plan must work without the settings layer
        return {}


def build_plan(report: dict, brief: dict, answers: Optional[dict] = None) -> EditPlan:
    report = report if isinstance(report, dict) else {}
    brief = dict(brief or {})
    if answers:
        brief = apply_answers(brief, answers)

    findings = detect_findings(report)
    published = _publish(report, findings)
    known = {f["id"]: f for f in published if isinstance(f, dict) and f.get("id")}

    source = report.get("source") or {}
    analysis = report.get("analysis") or {}
    video = analysis.get("video") or {}
    audio = analysis.get("audio") or {}
    faces = analysis.get("faces") or {}
    speech = analysis.get("speech") or {}

    src_w = int(_num(source.get("width")) or 0)
    src_h = int(_num(source.get("height")) or 0)
    duration = _num(source.get("duration")) or 0.0

    aspect = brief.get("format")
    if aspect not in ASPECTS:
        aspect = _aspect_of(src_w, src_h) or "9:16"
        if aspect not in ASPECTS:
            aspect = "9:16"

    platform = brief.get("platform") or _default_platform(aspect)
    target_name = brief.get("loudness") or "social"
    if target_name not in LOUDNESS_TARGETS:
        target_name = "social"
    loud_i, loud_tp = LOUDNESS_TARGETS[target_name]

    music_ratio = _num(audio.get("music_ratio")) or 0.0
    music_confidence = _num(audio.get("music_confidence")) or 0.0
    word_count = _num(speech.get("word_count")) or 0.0
    music_only = music_ratio > 0.6 and word_count == 0
    detector_floor = _num((_settings().get("audio") or {}).get("detector_min_confidence")) or 0.6
    music_uncertain = music_confidence < detector_floor
    tone = brief.get("tone")

    treatments: List[Dict[str, Any]] = []

    def add(
        name: str,
        kind: str,
        enabled: bool,
        params: Dict[str, Any],
        finding_id: str,
        explain: str,
    ) -> None:
        treatments.append(
            {
                "name": name,
                "kind": kind,
                "enabled": bool(enabled),
                "params": params,
                "reason": {"finding_id": finding_id, "explain": explain},
            }
        )

    # -- audio ------------------------------------------------------------- #
    if "audio.clipping" in known:
        add(
            "clipping_fix",
            "technical",
            False,
            {},
            "audio.clipping",
            "non correggo il clipping automaticamente",
        )

    if "audio.noise.high" in known:
        denoise_params = {"noise_floor": -25.0, "fallback": "highpass=f=80"}
        if music_uncertain:
            add(
                "denoise",
                "technical",
                False,
                denoise_params,
                "audio.noise.high",
                "detector musicale incerto: non distruggo il segnale",
            )
        elif music_ratio > 0.6:
            add(
                "denoise",
                "technical",
                False,
                denoise_params,
                "audio.noise.high",
                "traccia musicale dominante: il denoise mangerebbe la musica",
            )
        else:
            add(
                "denoise",
                "technical",
                True,
                denoise_params,
                "audio.noise.high",
                "rumore di fondo sopra soglia: denoise leggero con passa-alto a 80 Hz",
            )

    if "audio.loudness.off" in known:
        severity = known["audio.loudness.off"].get("severity") or "warn"
        social = target_name != "broadcast"
        if social or severity == "warn":
            if target_name == "broadcast":
                params = {"I": -23.0, "TP": -2.0, "LRA": 11.0}
            else:
                params = {"I": -14.0, "TP": -1.0, "LRA": 11.0}
            add(
                "loudnorm",
                "technical",
                True,
                params,
                "audio.loudness.off",
                f"normalizzo a {params['I']:.0f} LUFS / {params['TP']:.0f} dBTP",
            )

    if "audio.lra.wide" in known:
        compress_params = {
            "ratio": 2.0,
            "threshold_db": -18.0,
            "attack_ms": 10,
            "release_ms": 250,
        }
        if music_only:
            add(
                "compress",
                "technical",
                False,
                compress_params,
                "audio.lra.wide",
                "solo musica: comprimere schiaccerebbe la dinamica del brano",
            )
        else:
            add(
                "compress",
                "technical",
                True,
                compress_params,
                "audio.lra.wide",
                "dinamica troppo ampia: compressione morbida 2:1",
            )

    if "audio.silent" in known:
        add(
            "audio_restore",
            "technical",
            False,
            {},
            "audio.silent",
            "sorgente senza audio: non invento una traccia",
        )

    # -- video ------------------------------------------------------------- #
    if "video.jitter.shaky" in known:
        score = _num(known["video.jitter.shaky"].get("value")) or 0.0
        shakiness = 4 if score < 0.60 else (6 if score < 1.00 else 8)
        stab_params = {
            "shakiness": shakiness,
            "accuracy": 9,
            "smoothing": 10,
            "zoom": 0,
        }
        headroom = _num((video.get("jitter") or {}).get("headroom_pct"))
        veto = (faces.get("framing") == "fit") or (
            headroom is not None and headroom < THRESHOLDS["stab_min_headroom_pct"]
        )
        if veto:
            add(
                "stabilize",
                "technical",
                False,
                stab_params,
                "video.jitter.shaky",
                "inquadratura già stretta: stabilizzare richiederebbe un crop "
                "che taglia il soggetto",
            )
        else:
            add(
                "stabilize",
                "technical",
                True,
                stab_params,
                "video.jitter.shaky",
                f"tremolio {score:.2f}: stabilizzo con shakiness {shakiness}",
            )

    under = "video.exposure.under" in known
    high_clip = "video.exposure.high_clip" in known
    if under or high_clip:
        exposure_params: Dict[str, Any] = {}
        if under:
            exposure_params.update({"brightness": 0.06, "gamma": 1.05})
        if high_clip:
            exposure_params.update(
                {
                    "curves": "0/0 0.75/0.72 1/0.96",
                    "filter": "curves=0/0 0.75/0.72 1/0.96",
                }
            )
        cite = "video.exposure.high_clip" if high_clip else "video.exposure.under"
        enabled = True
        explain = "correggo l'esposizione con una curva morbida"
        if high_clip:
            clip_value = _num(known["video.exposure.high_clip"].get("value")) or 0.0
            if clip_value >= THRESHOLDS["clip_high_block_pct"]:
                enabled = False
                explain = "alte luci bruciate oltre l'8%: serve rifare la ripresa"
        if enabled and under:
            noise_floor = _num(audio.get("noise_floor_dbfs"))
            if noise_floor is not None and noise_floor > -45.0:
                enabled = False
                explain = "alzare l'esposizione amplificherebbe il rumore di fondo"
        add("exposure_fix", "technical", enabled, exposure_params, cite, explain)

    if "video.color.cast" in known:
        color = video.get("color") or {}
        cast_rg = _num(color.get("cast_rg")) or 0.0
        cast_by = _num(color.get("cast_by")) or 0.0
        strength = clamp(max(abs(cast_rg), abs(cast_by)) / 100.0, 0.02, 0.15)
        gamma_r = (
            round(1.0 - strength * (1 if cast_rg > 0 else -1), 4) if cast_rg else 1.0
        )
        gamma_b = (
            round(1.0 - strength * (1 if cast_by > 0 else -1), 4) if cast_by else 1.0
        )
        color_params = {
            "gamma_r": gamma_r,
            "gamma_b": gamma_b,
            "strength": round(strength, 4),
        }
        if tone == "raw":
            add(
                "color_fix",
                "technical",
                False,
                color_params,
                "video.color.cast",
                "tone raw: lascio la resa della macchina",
            )
        else:
            add(
                "color_fix",
                "technical",
                True,
                color_params,
                "video.color.cast",
                f"dominante {max(abs(cast_rg), abs(cast_by)):.0f}: correggo i gamma R/B",
            )

    if "video.pacing.drag" in known:
        add(
            "pacing_fix",
            "technical",
            False,
            {},
            "video.pacing.drag",
            "solo indicazione: accorcia le inquadrature in montaggio",
        )

    # -- creative ---------------------------------------------------------- #
    src_aspect = _aspect_of(src_w, src_h)
    autoframe_req = (src_aspect != aspect) or bool(brief.get("autoframe"))
    face_id = next((fid for fid in FACE_PRIORITY if fid in known), None)
    if face_id and autoframe_req:
        params, enabled, explain = _autoframe_params(
            face_id, faces, src_w, src_h, aspect
        )
        add("autoframe", "creative", enabled, params, face_id, explain)

    hook_value = brief.get("hook")
    if hook_value not in (None, "", "none") and "video.scene.cuts" in known:
        at = 0.0
        candidates = (video.get("retention") or {}).get("hook_candidates") or []
        if candidates and isinstance(candidates[0], dict):
            at = _num(candidates[0].get("at")) or 0.0
        hook_params = {"at": round(at, 3), "duration": 3.0}
        if tone == "clean":
            add(
                "hook_overlay",
                "creative",
                False,
                hook_params,
                "video.scene.cuts",
                "tone clean: niente overlay",
            )
        else:
            add(
                "hook_overlay",
                "creative",
                True,
                hook_params,
                "video.scene.cuts",
                f"aggancio testuale a {at:.1f}s per 3s",
            )

    if brief.get("zoom") and (
        "video.scene.cuts" in known or "video.pacing.drag" in known
    ):
        cite = (
            "video.scene.cuts" if "video.scene.cuts" in known else "video.pacing.drag"
        )
        zoom_params = {"amount": 0.06}
        if tone == "clean":
            add(
                "zoom",
                "creative",
                False,
                zoom_params,
                cite,
                "tone clean: niente zoom",
            )
        else:
            add(
                "zoom",
                "creative",
                True,
                zoom_params,
                cite,
                "zoom lento del 6% per dare respiro",
            )

    if brief.get("fit") and face_id:
        add(
            "fit",
            "creative",
            True,
            {"gblur_sigma": 20},
            face_id,
            "riempio il formato con sfondo sfocato",
        )

    # -- subtitles --------------------------------------------------------- #
    if "speech.present" in known and brief.get("subtitles") != "none":
        style = _sub_style(brief, aspect, speech)
        max_chars = (
            THRESHOLDS["sub_max_chars_916"]
            if _is_vertical(aspect)
            else THRESHOLDS["sub_max_chars_169"]
        )
        sub_params: Dict[str, Any] = {
            "style": style,
            "max_chars": max_chars,
            "max_lines": THRESHOLDS["sub_max_lines"],
            "max_cps": THRESHOLDS["sub_max_cps"],
        }
        explain = f"sottotitoli {style} a {max_chars} caratteri per riga"
        if "speech.fast" in known:
            sub_params["max_chars"] = 16
            sub_params["min_cue"] = 0.9
            explain = "parlato veloce: cue corte da 16 caratteri, minimo 0.9s"
        add("subtitles", "technical", True, sub_params, "speech.present", explain)

    if "speech.filler" in known:
        add(
            "filler_cut",
            "technical",
            False,
            {},
            "speech.filler",
            "propongo solo: i filler si tagliano in revisione manuale",
        )

    # -- creative cap ------------------------------------------------------ #
    enabled_creative = [
        t for t in treatments if t["kind"] == "creative" and t["enabled"]
    ]
    cap = THRESHOLDS["max_creative_treatments"]
    if len(enabled_creative) > cap:
        keep: set[str] = set()
        for name in CREATIVE_PRIORITY:
            if len(keep) >= cap:
                break
            if any(t["name"] == name for t in enabled_creative):
                keep.add(name)
        for treatment in enabled_creative:
            if treatment["name"] not in keep:
                treatment["enabled"] = False
                treatment["reason"]["explain"] = (
                    "tagliato: massimo 3 trattamenti creativi"
                )

    # -- cuts -------------------------------------------------------------- #
    spans: List[Dict[str, float]] = []
    if "audio.dead_air.excess" in known:
        for span in audio.get("dead_air") or []:
            if not isinstance(span, dict):
                continue
            spans.append(
                {
                    "start": _num(span.get("start")) or 0.0,
                    "end": _num(span.get("end")) or 0.0,
                }
            )
    if "video.freeze.static" in known:
        for freeze in video.get("freezes") or []:
            if not isinstance(freeze, dict):
                continue
            start = _num(freeze.get("start"))
            end = _num(freeze.get("end"))
            length = _num(freeze.get("duration"))
            if start is None and end is not None and length is not None:
                start = end - length
            if end is None and start is not None and length is not None:
                end = start + length
            if start is None or end is None:
                continue
            if end - start >= THRESHOLDS["freeze_min"]:
                spans.append({"start": start + 0.3, "end": end})

    cuts, removed = _build_cuts(spans, duration)

    cut_finding = None
    if "audio.dead_air.excess" in known:
        cut_finding = "audio.dead_air.excess"
    elif "video.freeze.static" in known:
        cut_finding = "video.freeze.static"

    if cut_finding:
        noise_floor = _num(audio.get("noise_floor_dbfs"))
        if noise_floor is None:
            noise_floor = -50.0
        cut_params = {
            "threshold_db": clamp(noise_floor + 8.0, -45.0, -25.0),
            "min_silence": THRESHOLDS["silence_min"],
            "pad": THRESHOLDS["silence_pad"],
            "fade_ms": THRESHOLDS["cut_fade_ms"],
            "max_cut_ratio": THRESHOLDS["dead_air_max_cut"],
        }
        if music_uncertain:
            add(
                "cut_silence",
                "technical",
                False,
                cut_params,
                cut_finding,
                "detector musicale incerto: nessun taglio alla cieca",
            )
        elif music_ratio > 0.5:
            add(
                "cut_silence",
                "technical",
                False,
                cut_params,
                cut_finding,
                "musica in sottofondo: tagliare i silenzi spezzerebbe il brano",
            )
            cuts = []
        elif not cuts or _is_full_range(cuts, duration):
            add(
                "cut_silence",
                "technical",
                False,
                cut_params,
                cut_finding,
                "nessun silenzio da tagliare che valga il taglio",
            )
            cuts = []
        else:
            add(
                "cut_silence",
                "technical",
                True,
                cut_params,
                cut_finding,
                f"taglio {removed:.1f}s di silenzi e freeze",
            )

    # -- output ------------------------------------------------------------ #
    out_w, out_h = output_size(aspect)
    output = {
        "aspect": aspect,
        "platform": platform,
        "width": out_w,
        "height": out_h,
        "loudness_target": target_name,
        "loudness": {"I": loud_i, "TP": loud_tp},
    }
    render = {
        "codec": "libx264",
        "crf": 18,
        "preset": "medium",
        "pix_fmt": "yuv420p",
        "faststart": True,
    }

    plan = EditPlan(
        output=output,
        treatments=treatments,
        cuts=cuts,
        render=render,
        deliverables=[],
        brief=brief,
    )
    plan.deliverables = deliverables(plan)
    return plan


# --------------------------------------------------------------------------- #
# deliverables
# --------------------------------------------------------------------------- #


def deliverables(plan: EditPlan, probe: Optional[dict] = None) -> List[Dict[str, Any]]:
    """Master, one export per platform, subtitles, thumbnail, contact sheet."""
    if isinstance(plan, EditPlan):
        output = plan.output or {}
        brief = plan.brief or {}
    else:
        output = (plan or {}).get("output") or {}
        brief = (plan or {}).get("brief") or {}

    aspect = output.get("aspect") or "9:16"
    width, height = output_size(aspect)
    source_note = ""
    if isinstance(probe, dict) and probe.get("width") and probe.get("height"):
        source_note = f" da {probe['width']}x{probe['height']}"

    items: List[Dict[str, Any]] = [
        {
            "name": "master.mp4",
            "kind": "master",
            "aspect": aspect,
            "width": width,
            "height": height,
            "notes": f"H.264 CRF 18, yuv420p, faststart{source_note}",
        }
    ]

    platforms = brief.get("platforms") or [
        output.get("platform") or _default_platform(aspect)
    ]
    if isinstance(platforms, str):
        platforms = [platforms]
    for name in platforms:
        items.append(
            {
                "name": f"{name}.mp4",
                "kind": "platform",
                "aspect": aspect,
                "width": width,
                "height": height,
                "notes": f"export {name}",
            }
        )

    items.append(
        {
            "name": "subtitles.srt",
            "kind": "subtitles",
            "aspect": aspect,
            "width": width,
            "height": height,
            "notes": "max 2 righe, 25 cps",
        }
    )
    items.append(
        {
            "name": "thumbnail.jpg",
            "kind": "thumbnail",
            "aspect": aspect,
            "width": width,
            "height": height,
            "notes": "frame dal hook candidate",
        }
    )
    items.append(
        {
            "name": "contact_sheet.jpg",
            "kind": "contact_sheet",
            "aspect": aspect,
            "width": width,
            "height": height,
            "notes": "griglia 4x4, un frame ogni 2s",
        }
    )
    return items


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #


def validate(plan, report: dict) -> List[str]:
    """Contract errors plus the plan-specific invariants."""
    data = plan.to_dict() if isinstance(plan, EditPlan) else dict(plan or {})
    errors = list(validate_plan(data, report))

    source = (report or {}).get("source") or {}
    duration = _num(source.get("duration")) or 0.0

    cuts = data.get("cuts") or []
    kept = 0.0
    for cut in cuts:
        start = _num(cut.get("in"))
        end = _num(cut.get("out"))
        if start is None or end is None:
            errors.append("cut missing in/out")
            continue
        if end <= start:
            errors.append(f"cut {start}-{end}: in must be < out")
        if start < 0 or (duration and end > duration + 1e-6):
            errors.append(f"cut {start}-{end} outside 0..{duration}")
        kept += max(0.0, end - start)

    # cuts are keep-ranges: what is removed is the complement of their sum
    removed = max(0.0, duration - kept) if (duration and cuts) else 0.0
    if duration and removed > THRESHOLDS["dead_air_max_cut"] * duration + 1e-6:
        errors.append(
            f"removed {removed:.2f}s > "
            f"{THRESHOLDS['dead_air_max_cut'] * duration:.2f}s budget"
        )

    aspect = (data.get("output") or {}).get("aspect")
    if aspect not in ASPECTS:
        errors.append(f"output.aspect {aspect!r} not in ASPECTS")

    for treatment in data.get("treatments") or []:
        if not treatment.get("enabled"):
            continue
        finding_id = (treatment.get("reason") or {}).get("finding_id")
        if finding_id in TRIAGE_ONLY:
            errors.append(
                f"treatment {treatment.get('name')} cites triage-only finding "
                f"{finding_id}"
            )

    if (data.get("brief") or {}).get("subtitles") == "none":
        for treatment in data.get("treatments") or []:
            if treatment.get("enabled") and treatment.get("name") == "subtitles":
                errors.append("subtitles disabled by brief but treatment enabled")

    return errors


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #


def _load_json(path: str) -> dict:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="vx.plan", description="Build an edit plan from a vx report."
    )
    parser.add_argument("--report", required=True)
    parser.add_argument("--brief")
    parser.add_argument("--answers")
    parser.add_argument("--out")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        rep = report_mod.read(args.report)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    rep["_path"] = str(args.report)

    try:
        brief = _load_json(args.brief) if args.brief else {}
        answers = _load_json(args.answers) if args.answers else None
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if answers:
        brief = apply_answers(brief, answers)

    request = needs_triage(brief, rep)
    if request is not None and not answers:
        print(json.dumps(request, indent=2, ensure_ascii=False))
        return EXIT_NEEDS_INPUT

    plan = build_plan(rep, brief, answers)
    errors = validate(plan, rep)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE

    payload = plan.to_dict()
    if args.out:
        report_mod.write(payload, args.out)
    print(
        json.dumps(
            payload,
            indent=None if args.json else 2,
            ensure_ascii=False,
        )
    )
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
