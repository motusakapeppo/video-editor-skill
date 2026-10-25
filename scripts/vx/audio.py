"""vx.audio — audio forensics for the video editor.

Fase 4 (analysis) plus the technical-cleanup input for Fase 6.

Everything here is measurement, not opinion: every number comes from a named
ffmpeg filter or from numpy over a decoded PCM stream. The single heuristic
(music detection) says so in its ``method`` field and never claims certainty.

Recipes verified on ffmpeg 8.1 / Windows:

* **Noise floor** — ``astats`` per-frame ``Overall.RMS_level``, then p10/p90
  percentiles. ``astats``' own ``Noise_floor`` field is unreliable (it reports
  ``-inf`` on clean digital silence) and is deliberately not used.
* **Dead air** — ``silencedetect`` MUST run at ``loglevel=info``;
  ``-loglevel error`` suppresses its output entirely. It compares per-sample
  *peak*, not RMS, so a span is "no sample above the threshold", not "quiet".
* **Loudness** — ``loudnorm`` with ``print_format=json``; the measurement is
  the last ``{...}`` block in stderr.
* **Peaks** — ``volumedetect`` for the native-stream peak, plus numpy over a
  decoded f32le stream for clipping percentage and DC offset.
* **Music** — spectral-flux periodicity + low-band energy continuity.

``silenceremove`` is never used: it shortens the audio and destroys A/V sync
(measured 3.0 s -> 2.239 s on this machine).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from . import ffmpeg as ff
from .contract import (
    EXIT_OK,
    EXIT_USAGE,
    THRESHOLDS,
    AudioAnalysis,
    MissingDependency,
    clamp,
)
from .probe import probe
from .winpath import ass_filter_path

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

PCM_SR = 16000  # decode rate for numpy work: cheap, plenty for these features
CLIP_LEVEL = 0.9999  # |x| at or above this counts as a clipped sample
MUSIC_THRESHOLD = 0.45  # music_ratio at or above this -> music_detected
QUIET_LUFS = -24.0  # integrated loudness below this -> verdict "quiet"
NOISE_MARGIN_DB = 8.0  # silencedetect threshold = noise floor + this
NOISE_CLAMP = (-45.0, -25.0)  # ... clamped into this window
MAX_CLIP_EVENTS = 32  # cap on the clipping-event list in AudioAnalysis.peaks

_ASTATS_FRAME = re.compile(r"frame:(\d+)\s+pts:(-?\d+)\s+pts_time:(\S+)")
_ASTATS_RMS = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(\S+)")
_SIL_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SIL_END = re.compile(
    r"silence_end:\s*(-?\d+(?:\.\d+)?)"
    r"(?:\s*\|\s*silence_duration:\s*(-?\d+(?:\.\d+)?))?"
)
_VOL_MAX = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?|-inf)\s*dB")
_VOL_MEAN = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?|-inf)\s*dB")
_EBUR_PEAK = re.compile(r"Peak:\s*(-?\d+(?:\.\d+)?|-inf)\s*dBFS")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _num(value: Any, default: float = 0.0) -> float:
    """float() that maps None / garbage / inf / nan to a finite default."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _percentile(values: Sequence[float], q: float) -> float:
    """Percentile over finite values only. Empty input -> 0.0."""
    arr = np.asarray([v for v in values if math.isfinite(v)], dtype=float)
    if arr.size == 0:
        return 0.0
    return float(np.percentile(arr, q))


def _merge_intervals(
    spans: Iterable[Tuple[float, float]], tol: float = 1e-6
) -> List[Tuple[float, float]]:
    """Sort and merge overlapping / touching (start, end) pairs."""
    ordered = sorted((float(s), float(e)) for s, e in spans if e > s)
    merged: List[Tuple[float, float]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1] + tol:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _audio_info(path: str | Path) -> Dict[str, Any]:
    """has_audio / duration / sample_rate / channels for any container.

    ``probe()`` insists on a video stream, so audio-only inputs (wav, m4a)
    fall back to a direct ffprobe stream listing.
    """
    p = Path(path)
    if not p.exists():
        raise MissingDependency(f"input not found: {p}")
    try:
        pr = probe(p)
        return {
            "has_audio": pr.has_audio,
            "duration": pr.duration,
            "sample_rate": pr.sample_rate,
            "channels": pr.channels,
        }
    except MissingDependency:
        pass  # no video stream: audio-only file, ask ffprobe directly

    proc = ff.run_fp(["-show_streams", "-show_format", "-of", "json", str(p)])
    if not proc.ok:
        raise MissingDependency(f"ffprobe failed on {p.name}: {proc.tail(5)}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}
    return {
        "has_audio": audio is not None,
        "duration": _num(fmt.get("duration")),
        "sample_rate": int(_num((audio or {}).get("sample_rate"))),
        "channels": int(_num((audio or {}).get("channels"))),
    }


def _decode_pcm(path: str | Path, sr: int = PCM_SR) -> np.ndarray:
    """Decode the first audio stream to mono float32 at ``sr``.

    Written to a temp file rather than stdout: ``ffmpeg.run`` captures stdout
    as UTF-8 text, which would corrupt raw PCM bytes.
    """
    with tempfile.TemporaryDirectory(prefix="vx_pcm_") as tmp:
        out = Path(tmp) / "audio.pcm"
        proc = ff.run_ff(
            [
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-f",
                "f32le",
                "-ac",
                "1",
                "-ar",
                str(sr),
                str(out),
            ],
            loglevel="error",
        )
        if not out.exists() or out.stat().st_size == 0:
            return np.zeros(0, dtype=np.float32)
        return np.fromfile(out, dtype="<f4")


def _last_json(text: str) -> Dict[str, Any]:
    """Last parseable ``{...}`` block in ``text`` (loudnorm's measurement)."""
    tail = text[-6000:]
    for block in reversed(re.findall(r"\{[\s\S]*?\}", tail)):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "input_i" in data:
            return data
    return {}


# --------------------------------------------------------------------------- #
# 1. noise floor / SNR
# --------------------------------------------------------------------------- #


def _parse_astats(text: str) -> List[Tuple[float, float]]:
    """[(pts_time, Overall.RMS_level)] per astats frame, non-finite dropped."""
    frames: List[Tuple[float, float]] = []
    current_t = 0.0
    for line in text.splitlines():
        head = _ASTATS_FRAME.search(line)
        if head:
            current_t = _num(head.group(3))
            continue
        rms = _ASTATS_RMS.search(line)
        if rms:
            value = _num(rms.group(1), default=float("nan"))
            if math.isfinite(value):
                frames.append((current_t, value))
    return frames


def noise_floor_and_speech(path: str | Path) -> Dict[str, Any]:
    """Noise floor, speech level and SNR from per-frame astats RMS.

    p10 of the frame RMS distribution is the noise floor, p90 is the speech
    level, SNR is their difference. Validated on a synthetic clip:
    p10 = -59.7 dBFS, p90 = -21.07 dBFS, SNR = 38.6 dB.
    """
    info = _audio_info(path)
    if not info["has_audio"]:
        return {
            "noise_floor_dbfs": 0.0,
            "speech_level_dbfs": 0.0,
            "snr_db": 0.0,
            "method": "none",
            "samples": 0,
        }

    with tempfile.TemporaryDirectory(prefix="vx_astats_") as tmp:
        out = Path(tmp) / "astats.txt"
        graph = (
            "astats=metadata=1:reset=1,"
            f"ametadata=mode=print:file={ass_filter_path(out)}"
        )
        proc = ff.run_ff(
            ["-i", str(path), "-map", "0:a:0", "-af", graph, "-f", "null", "-"]
        )
        text = ""
        if out.exists():
            text = out.read_text(encoding="utf-8", errors="replace")
        if not text:
            text = proc.stderr

    frames = _parse_astats(text)
    values = [db for _, db in frames]
    if not values:
        return {
            "noise_floor_dbfs": 0.0,
            "speech_level_dbfs": 0.0,
            "snr_db": 0.0,
            "method": "astats_p10_p90",
            "samples": 0,
        }

    noise = _percentile(values, 10)
    speech = _percentile(values, 90)
    return {
        "noise_floor_dbfs": round(noise, 3),
        "speech_level_dbfs": round(speech, 3),
        "snr_db": round(speech - noise, 3),
        "method": "astats_p10_p90",
        "samples": len(values),
    }


# --------------------------------------------------------------------------- #
# 2. dead air
# --------------------------------------------------------------------------- #


def _parse_silencedetect(text: str, duration: float) -> List[Tuple[float, float]]:
    """Parse silencedetect stderr into (start, end) spans.

    Tolerates the ``channel: 0 | `` prefix of the ``mono=1`` variant and
    integer timestamps without decimals (``silence_start: 0``).
    """
    spans: List[Tuple[float, float]] = []
    pending: Optional[float] = None
    for line in text.splitlines():
        start = _SIL_START.search(line)
        if start:
            pending = _num(start.group(1))
            continue
        end = _SIL_END.search(line)
        if end:
            stop = _num(end.group(1))
            if pending is not None:
                spans.append((pending, stop))
                pending = None
            elif end.group(2) is not None:
                spans.append((stop - _num(end.group(2)), stop))
    if pending is not None:  # silence runs to end of file
        spans.append((pending, duration if duration > 0 else pending))
    return spans


def dead_air(
    path: str | Path,
    *,
    noise_floor_dbfs: Optional[float] = None,
    min_silence: Optional[float] = None,
    pad: Optional[float] = None,
) -> List[Dict[str, float]]:
    """Silent spans via ``silencedetect``.

    The threshold is ``clamp(noise_floor + 8, -45, -25)`` dB. ``pad`` is
    accepted for API symmetry with :func:`build_cut_ranges`; it does not
    change the detected spans (padding is applied when building cut ranges).

    Note: silencedetect compares per-sample *peak*, so a span means "no sample
    exceeded the threshold", not "the RMS was low".
    """
    info = _audio_info(path)
    if not info["has_audio"]:
        return []

    if noise_floor_dbfs is None:
        noise_floor_dbfs = noise_floor_and_speech(path)["noise_floor_dbfs"]
    if min_silence is None:
        min_silence = float(THRESHOLDS["silence_min"])
    if pad is None:
        pad = float(THRESHOLDS["silence_pad"])

    threshold = clamp(
        _num(noise_floor_dbfs) + NOISE_MARGIN_DB, NOISE_CLAMP[0], NOISE_CLAMP[1]
    )
    graph = f"silencedetect=noise={threshold:.1f}dB:d={float(min_silence):.3f}"
    proc = ff.run_ff(
        ["-i", str(path), "-map", "0:a:0", "-af", graph, "-f", "null", "-"],
        loglevel="info",  # -loglevel error would hide silencedetect entirely
    )

    spans = _merge_intervals(_parse_silencedetect(proc.stderr, info["duration"]))
    out: List[Dict[str, float]] = []
    for start, end in spans:
        start = max(0.0, start)
        end = min(end, info["duration"]) if info["duration"] > 0 else end
        length = end - start
        if length < float(min_silence):
            continue
        out.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(length, 3),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# 3. loudness
# --------------------------------------------------------------------------- #


def loudness(path: str | Path) -> Dict[str, float]:
    """Two-pass loudnorm measurement (pass 1 only: measure, do not normalise)."""
    info = _audio_info(path)
    empty = {
        "input_i": 0.0,
        "input_tp": 0.0,
        "input_lra": 0.0,
        "input_thresh": 0.0,
        "target_offset": 0.0,
    }
    if not info["has_audio"]:
        return empty

    proc = ff.run_ff(
        [
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            "loudnorm=I=-14:TP=-1.0:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    data = _last_json(proc.stderr)
    if not data:
        return empty
    return {
        key: round(_num(data.get(key)), 3)
        for key in (
            "input_i",
            "input_tp",
            "input_lra",
            "input_thresh",
            "target_offset",
        )
    }


# --------------------------------------------------------------------------- #
# 4. peaks / clipping / DC
# --------------------------------------------------------------------------- #


def _parse_volumedetect(text: str) -> Tuple[float, float]:
    """(max_volume, mean_volume) in dBFS; last occurrence wins."""
    max_v = 0.0
    mean_v = 0.0
    for match in _VOL_MAX.finditer(text):
        max_v = _num(match.group(1))
    for match in _VOL_MEAN.finditer(text):
        mean_v = _num(match.group(1))
    return max_v, mean_v


def _true_peak(path: str | Path) -> float:
    """True peak (dBTP) from ebur128, independent of loudnorm."""
    proc = ff.run_ff(
        [
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ]
    )
    values = _EBUR_PEAK.findall(proc.stderr)
    return _num(values[-1]) if values else 0.0


def _clip_events(
    absx: np.ndarray, limit: int = MAX_CLIP_EVENTS
) -> List[Dict[str, float]]:
    """Contiguous runs of full-scale samples as {start, end, peak_dbfs}."""
    mask = absx >= CLIP_LEVEL
    if not mask.any():
        return []
    edges = np.flatnonzero(np.diff(np.concatenate(([0], mask.astype(np.int8), [0]))))
    events: List[Dict[str, float]] = []
    for start, end in zip(edges[0::2][:limit], edges[1::2][:limit]):
        peak = float(absx[start:end].max())
        events.append(
            {
                "start": round(float(start) / PCM_SR, 3),
                "end": round(float(end) / PCM_SR, 3),
                "peak_dbfs": round(20.0 * math.log10(max(peak, 1e-9)), 3),
            }
        )
    return events


def _peak_scan(path: str | Path) -> Tuple[Dict[str, float], List[Dict[str, float]]]:
    """Aggregate peak stats plus the clipping-event list."""
    info = _audio_info(path)
    if not info["has_audio"]:
        return (
            {"peak_dbfs": 0.0, "clipping_pct": 0.0, "dc_offset": 0.0, "true_peak": 0.0},
            [],
        )

    proc = ff.run_ff(
        ["-i", str(path), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"]
    )
    peak_dbfs, _mean_dbfs = _parse_volumedetect(proc.stderr)

    pcm = _decode_pcm(path)
    if pcm.size:
        absx = np.abs(pcm)
        clipping_pct = float((absx >= CLIP_LEVEL).mean() * 100.0)
        dc_offset = float(pcm.mean())
        events = _clip_events(absx)
    else:
        clipping_pct = 0.0
        dc_offset = 0.0
        events = []

    return (
        {
            "peak_dbfs": round(peak_dbfs, 3),
            "clipping_pct": round(clipping_pct, 4),
            "dc_offset": round(dc_offset, 6),
            "true_peak": round(_true_peak(path), 3),
        },
        events,
    )


def peaks(path: str | Path) -> Dict[str, float]:
    """Peak level, clipping percentage, DC offset and true peak."""
    return _peak_scan(path)[0]


# --------------------------------------------------------------------------- #
# 5. music detection (heuristic)
# --------------------------------------------------------------------------- #


def _music_ratio_numpy(pcm: np.ndarray) -> float:
    """librosa-free fallback: same two features, plain numpy."""
    sr, hop, n_fft = PCM_SR, 512, 2048
    if pcm.size < n_fft * 2:
        return 0.0
    frames = np.lib.stride_tricks.sliding_window_view(pcm, n_fft)[::hop]
    if frames.shape[0] < 5:
        return 0.0
    window = np.hanning(n_fft).astype(np.float32)
    spec = np.abs(np.fft.rfft(frames * window, axis=1)) ** 2
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)

    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1))
    db = 20.0 * np.log10(rms + 1e-10)
    active = db > max(float(db.max()) - 25.0, -60.0)
    if int(active.sum()) < 5:
        return 0.0

    low = spec[:, (freqs >= 20.0) & (freqs <= 200.0)].sum(axis=1)
    total = spec.sum(axis=1) + 1e-12
    low_ratio = float(np.mean(low[active] / total[active]))

    flux = np.maximum(0.0, np.diff(np.log1p(spec), axis=0)).sum(axis=1)
    flux = flux[active[:-1]]
    periodicity = 0.0
    if flux.size > 8 and float(flux.std()) > 1e-9:
        centred = flux - flux.mean()
        ac = np.correlate(centred, centred, "full")[centred.size - 1 :]
        if ac[0] > 0:
            ac = ac / ac[0]
            fps = sr / hop
            lo, hi = int(0.33 * fps), min(int(1.0 * fps), ac.size - 1)
            if hi > lo:
                periodicity = float(np.clip(ac[lo : hi + 1].max(), 0.0, 1.0))

    return float(
        np.clip(0.55 * periodicity + 0.45 * min(low_ratio / 0.35, 1.0), 0.0, 1.0)
    )


def _music_ratio(pcm: np.ndarray) -> float:
    """0..1 heuristic: beat periodicity + sustained low-band energy."""
    try:
        import librosa
    except Exception:  # pragma: no cover - librosa is present in the venv
        return _music_ratio_numpy(pcm)

    sr, hop, n_fft = PCM_SR, 512, 2048
    y = pcm.astype(np.float32)
    if y.size < n_fft * 2:
        return 0.0

    rms = librosa.feature.rms(y=y, frame_length=n_fft, hop_length=hop)[0]
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    spec = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)

    n = min(rms.size, onset.size, spec.shape[1])
    if n < 5:
        return 0.0
    rms, onset, spec = rms[:n], onset[:n], spec[:, :n]

    db = librosa.amplitude_to_db(rms + 1e-10, ref=1.0)
    active = db > max(float(db.max()) - 25.0, -60.0)
    if int(active.sum()) < 5:
        return 0.0

    periodicity = 0.0
    centred = onset[active] - float(onset[active].mean())
    if float(centred.std()) > 1e-9:
        ac = np.correlate(centred, centred, "full")[centred.size - 1 :]
        if ac[0] > 0:
            ac = ac / ac[0]
            fps = sr / hop
            lo, hi = int(0.33 * fps), min(int(1.0 * fps), ac.size - 1)
            if hi > lo:
                periodicity = float(np.clip(ac[lo : hi + 1].max(), 0.0, 1.0))

    low = spec[(freqs >= 20.0) & (freqs <= 200.0)].sum(axis=0)
    total = spec.sum(axis=0) + 1e-12
    low_ratio = float(np.mean(low[active] / total[active]))

    return float(
        np.clip(0.55 * periodicity + 0.45 * min(low_ratio / 0.35, 1.0), 0.0, 1.0)
    )


def music_detect(path: str | Path) -> Dict[str, Any]:
    """Heuristic music presence. Never claims certainty."""
    info = _audio_info(path)
    if not info["has_audio"]:
        return {"music_detected": False, "music_ratio": 0.0, "method": "none"}

    pcm = _decode_pcm(path)
    if pcm.size < PCM_SR:
        return {"music_detected": False, "music_ratio": 0.0, "method": "heuristic"}

    ratio = _music_ratio(pcm)
    return {
        "music_detected": bool(ratio >= MUSIC_THRESHOLD),
        "music_ratio": round(ratio, 4),
        "method": "heuristic",
    }


# --------------------------------------------------------------------------- #
# 6. cut ranges
# --------------------------------------------------------------------------- #


def build_cut_ranges(
    dead_air: List[Dict[str, float]],
    duration: float,
    *,
    pad: float,
    min_clip: float = 0.25,
    min_gap: float = 0.15,
    max_cut_ratio: float = 0.35,
) -> List[Dict[str, float]]:
    """Turn dead-air spans into keep-ranges covering ``0..duration``.

    Each span is shrunk by ``pad`` on both sides so no cut ever lands inside
    speech. Gaps shorter than ``min_gap`` are not worth a cut and are merged
    away; keep-ranges shorter than ``min_clip`` are absorbed into a neighbour;
    total removed never exceeds ``max_cut_ratio * duration`` (the longest gaps
    win when the budget is tight).
    """
    total_duration = float(duration)
    if total_duration <= 0.0:
        return []

    pad = max(0.0, float(pad))
    cuts: List[Tuple[float, float]] = []
    for span in dead_air or []:
        start = _num(span.get("start"))
        end = _num(span.get("end"))
        inner_start = max(0.0, start + pad)
        inner_end = min(total_duration, end - pad)
        if inner_end - inner_start > 0.0:
            cuts.append((inner_start, inner_end))

    cuts = _merge_intervals(cuts)
    cuts = [c for c in cuts if (c[1] - c[0]) >= float(min_gap)]

    budget = max(0.0, float(max_cut_ratio)) * total_duration
    if sum(e - s for s, e in cuts) > budget:
        kept: List[Tuple[float, float]] = []
        spent = 0.0
        for span in sorted(cuts, key=lambda c: c[1] - c[0], reverse=True):
            length = span[1] - span[0]
            if spent + length <= budget + 1e-9:
                kept.append(span)
                spent += length
        cuts = sorted(kept)

    keeps: List[List[float]] = []
    cursor = 0.0
    for start, end in cuts:
        if start > cursor:
            keeps.append([cursor, start])
        cursor = max(cursor, end)
    if cursor < total_duration:
        keeps.append([cursor, total_duration])

    # absorb keep-ranges shorter than min_clip into a neighbour
    changed = True
    while changed and len(keeps) > 1:
        changed = False
        for index, (start, end) in enumerate(keeps):
            if end - start < float(min_clip):
                if index > 0:
                    keeps[index - 1][1] = end
                    keeps.pop(index)
                else:
                    keeps[index + 1][0] = start
                    keeps.pop(index)
                changed = True
                break

    merged: List[List[float]] = []
    for start, end in keeps:
        if merged and start - merged[-1][1] < float(min_gap):
            merged[-1][1] = end
        else:
            merged.append([start, end])

    return [
        {"in": round(start, 3), "out": round(end, 3)}
        for start, end in merged
        if end - start > 1e-6
    ]


# --------------------------------------------------------------------------- #
# composition
# --------------------------------------------------------------------------- #


def _verdict(
    noise: Dict[str, Any], peak: Dict[str, float], loud: Dict[str, float]
) -> str:
    if _num(peak.get("clipping_pct")) >= float(THRESHOLDS["clip_pct"]):
        return "clipped"
    if _num(noise.get("snr_db")) < float(THRESHOLDS["noise_snr_low"]):
        return "noisy"
    if _num(loud.get("input_i")) < QUIET_LUFS:
        return "quiet"
    return "clean"


def analyze_audio(path: str | Path) -> AudioAnalysis:
    """Full audio forensics pass. Never raises on a video without audio."""
    info = _audio_info(path)
    if not info["has_audio"]:
        return AudioAnalysis(
            noise_floor_dbfs=0.0,
            speech_level_dbfs=0.0,
            snr_db=0.0,
            dead_air=[],
            peaks=[],
            clipping_pct=0.0,
            dc_offset=0.0,
            loudness={},
            music_detected=False,
            music_ratio=0.0,
            verdict="silent",
        )

    noise = noise_floor_and_speech(path)
    spans = dead_air(path, noise_floor_dbfs=noise["noise_floor_dbfs"])
    loud = loudness(path)
    peak, events = _peak_scan(path)
    music = music_detect(path)

    return AudioAnalysis(
        noise_floor_dbfs=float(noise["noise_floor_dbfs"]),
        speech_level_dbfs=float(noise["speech_level_dbfs"]),
        snr_db=float(noise["snr_db"]),
        dead_air=spans,
        peaks=events,
        clipping_pct=float(peak["clipping_pct"]),
        dc_offset=float(peak["dc_offset"]),
        loudness=dict(loud),
        music_detected=bool(music["music_detected"]),
        music_ratio=float(music["music_ratio"]),
        verdict=_verdict(noise, peak, loud),
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vx.audio", description="audio forensics for the video editor"
    )
    parser.add_argument("--input", required=True, help="source media file")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--cuts", action="store_true", help="also print keep-ranges for dead air"
    )
    args = parser.parse_args(argv)

    try:
        analysis = analyze_audio(args.input)
    except MissingDependency as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    payload: Dict[str, Any] = analysis.to_dict()
    if args.cuts:
        info = _audio_info(args.input)
        payload["cut_ranges"] = build_cut_ranges(
            analysis.dead_air,
            info["duration"],
            pad=float(THRESHOLDS["silence_pad"]),
            max_cut_ratio=float(THRESHOLDS["dead_air_max_cut"]),
        )

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK

    print(f"verdict        : {analysis.verdict}")
    print(f"noise floor    : {analysis.noise_floor_dbfs:.2f} dBFS")
    print(f"speech level   : {analysis.speech_level_dbfs:.2f} dBFS")
    print(f"snr            : {analysis.snr_db:.2f} dB")
    print(f"integrated     : {analysis.loudness.get('input_i', 0.0):.2f} LUFS")
    print(f"true peak      : {analysis.loudness.get('input_tp', 0.0):.2f} dBTP")
    print(f"clipping       : {analysis.clipping_pct:.4f} %")
    print(f"dc offset      : {analysis.dc_offset:.6f}")
    print(f"music          : {analysis.music_ratio:.3f} ({analysis.music_detected})")
    print(f"dead air spans : {len(analysis.dead_air)}")
    for span in analysis.dead_air:
        print(f"  {span['start']:.3f}s -> {span['end']:.3f}s ({span['duration']:.3f}s)")
    if args.cuts:
        print("keep ranges    :")
        for item in payload["cut_ranges"]:
            print(f"  {item['in']:.3f}s -> {item['out']:.3f}s")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
