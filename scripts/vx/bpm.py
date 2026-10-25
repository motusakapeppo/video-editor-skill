"""vx.bpm — tempo estimation that listens to the kick, not to the vocal.

`librosa.beat.beat_track` on a live mix locks onto the voice as often as onto the beat:
three clips of the same performance came back as 117.5 / 136.0 / 172.3 BPM. The fix is
not a better model, it is a better signal and a self-check:

  1. band-limit to the low end (kick + bass) before onset detection, because the vocal
     lives at 200 Hz - 4 kHz where it competes with the snare;
  2. score every candidate tempo by how well a grid at that tempo actually sits on the
     onsets (comb + phase search), instead of trusting a single autocorrelation peak;
  3. report the score, so a wrong answer is visible instead of confident.

Half/double-time is inherent to tempo perception: the function returns the candidates
around the best fit so the caller can pick the metrical level explicitly.
"""

from __future__ import annotations

import math
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from . import ffmpeg as ff

HOP = 256
LOW_BAND = (28.0, 180.0)


@dataclass
class Estimate:
    bpm: float = 0.0
    phase: float = 0.0
    score: float = 0.0
    baseline: float = 0.0
    contrast: float = 0.0
    candidates: List[Dict[str, float]] = field(default_factory=list)
    method: str = "comb-lowband"
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def confident(self) -> bool:
        return self.contrast >= 1.35 and self.score > 0.0


def _decode(path: str, seconds: float = 120.0) -> tuple[np.ndarray, int]:
    import librosa

    target = Path(tempfile.mkdtemp(prefix="vxbpm-")) / "a.wav"
    proc = ff.run_ff(
        [
            "-i",
            path,
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "22050",
            "-t",
            str(seconds),
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        loglevel="error",
    )
    if not proc.ok:
        raise RuntimeError("audio decode failed")
    y, sr = librosa.load(str(target), sr=22050, mono=True)
    return y, sr


def _lowband(y: np.ndarray, sr: int) -> np.ndarray:
    from scipy.signal import butter, sosfiltfilt

    sos = butter(
        4,
        [LOW_BAND[0], min(LOW_BAND[1], sr / 2 - 100)],
        btype="band",
        fs=sr,
        output="sos",
    )
    return sosfiltfilt(sos, y).astype(np.float32)


def _grid_score(env: np.ndarray, period: float) -> tuple[float, float]:
    """Best mean onset strength over all phases of a grid with this period (in frames)."""
    n = len(env)
    if period < 2 or n < period * 4:
        return 0.0, 0.0
    phases = int(math.floor(period))
    best_score, best_phase = -1.0, 0.0
    for phase in range(phases):
        idx = np.arange(phase, n, period).astype(int)
        idx = idx[idx < n]
        if idx.size < 4:
            continue
        score = float(env[idx].mean())
        if score > best_score:
            best_score, best_phase = score, phase
    return best_score, best_phase


def estimate(
    path: str, *, bpm_min: float = 60.0, bpm_max: float = 200.0, seconds: float = 120.0
) -> Estimate:
    import librosa

    try:
        y, sr = _decode(path, seconds)
    except Exception as exc:  # noqa: BLE001
        return Estimate(reason=f"{type(exc).__name__}: {exc}"[:180])
    if y.size == 0:
        return Estimate(reason="empty audio")

    fps = sr / HOP
    env_low = librosa.onset.onset_strength(y=_lowband(y, sr), sr=sr, hop_length=HOP)
    env_all = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    if env_low.size == 0:
        return Estimate(reason="onset envelope empty")

    baseline = float(env_low.mean()) + 1e-9
    scored: List[Dict[str, float]] = []
    for bpm in np.arange(bpm_min, bpm_max + 0.01, 0.25):
        score, phase = _grid_score(env_low, 60.0 / bpm * fps)
        if score > 0:
            scored.append(
                {
                    "bpm": round(float(bpm), 2),
                    "score": round(score / baseline, 3),
                    "phase_frames": float(phase),
                }
            )
    if not scored:
        return Estimate(
            reason="no candidate explained the onsets", baseline=round(baseline, 4)
        )

    scored.sort(key=lambda item: item["score"], reverse=True)
    best = scored[0]
    second = next(
        (c for c in scored[1:] if abs(c["bpm"] - best["bpm"]) > 2.0),
        scored[1] if len(scored) > 1 else best,
    )
    contrast = best["score"] / max(second["score"], 1e-9)
    phase_s = float(best["phase_frames"]) / fps

    candidates = [
        c
        for c in scored
        if abs(c["bpm"] / best["bpm"] - 1.0) < 0.03
        or abs(c["bpm"] / best["bpm"] - 2.0) < 0.04
        or abs(c["bpm"] / best["bpm"] - 0.5) < 0.025
    ][:6]

    return Estimate(
        bpm=float(best["bpm"]),
        phase=round(phase_s, 4),
        score=float(best["score"]),
        baseline=round(baseline, 5),
        contrast=round(contrast, 3),
        candidates=candidates,
        reason=""
        if contrast >= 1.35
        else "weak contrast between the best tempo and the runner-up",
    )


def session_bpm(estimates: Dict[str, Estimate]) -> Dict[str, Any]:
    """Do the clips agree once octave equivalence (x0.5, x1, x2) is allowed?"""
    bpms = [(label, est.bpm) for label, est in estimates.items() if est.bpm > 0]
    if not bpms:
        return {"bpm": 0.0, "agree": False, "detail": "no estimates"}
    reference_label, reference = max(bpms, key=lambda item: estimates[item[0]].contrast)
    ratios, detail = [], []
    for label, bpm in bpms:
        raw = bpm / reference
        closest = min((0.5, 1.0, 2.0), key=lambda a: abs(raw - a))
        ratios.append(round(raw / closest, 4))
        detail.append(
            {
                "label": label,
                "bpm": bpm,
                "octave": closest,
                "normalised_ratio": ratios[-1],
            }
        )
    spread = max(ratios) / min(ratios) if ratios else 1.0
    return {
        "bpm": round(reference, 2),
        "reference": reference_label,
        "agree": spread <= 1.02,
        "spread": round(spread, 4),
        "detail": detail,
        "reason": "clips agree after octave normalisation"
        if spread <= 1.02
        else "clips disagree even after octave normalisation",
    }
