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

    # Log-normal prior on the tempo (Ellis 2007): without it the comb search is
    # monotonic in period and the answer sticks to the low edge of the range.
    ac = librosa.autocorrelate(env_low, max_size=int(fps * 2.0))
    lags = np.arange(ac.size, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        lag_bpm = np.where(lags > 0, 60.0 * fps / np.maximum(lags, 1e-9), 0.0)
    prior = np.exp(-0.5 * (np.log2(np.maximum(lag_bpm, 1e-9) / 110.0) / 0.9) ** 2)
    in_range = (lag_bpm >= bpm_min) & (lag_bpm <= bpm_max)
    salience = np.where(in_range, ac * prior, 0.0)
    if not np.any(salience > 0):
        return Estimate(
            reason="no salience inside the tempo range", baseline=round(baseline, 4)
        )
    coarse = float(lag_bpm[int(np.argmax(salience))])

    scored: List[Dict[str, float]] = []
    for bpm in np.arange(
        max(bpm_min, coarse * 0.94), min(bpm_max, coarse * 1.06) + 0.01, 0.1
    ):
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


METRICAL_RELATIONS = (0.5, 2.0 / 3.0, 0.75, 1.0, 4.0 / 3.0, 1.5, 2.0)

PHASE_TOLERANCE_PERIOD = 1.0 / 16.0


def session_bpm(estimates: Dict[str, Estimate]) -> Dict[str, Any]:
    """Do the clips belong to the same rhythmic family once metric level is allowed?

    Octave equivalence (x0.5, x2) is the classic ambiguity, but a change of figuration
    (triplets, double-time feel) moves the estimate by 2:3 or 3:4. Those ratios belong
    to the same ecosystem and must not read as disagreement; the per-clip grid still
    uses each clip's own tempo when cutting.
    """
    bpms = [(label, est.bpm) for label, est in estimates.items() if est.bpm > 0]
    if not bpms:
        return {"bpm": 0.0, "agree": False, "detail": "no estimates"}
    reference_label, reference = max(bpms, key=lambda item: estimates[item[0]].contrast)
    ratios, detail, fractions = [], [], []
    for label, bpm in bpms:
        raw = bpm / reference
        closest = min(METRICAL_RELATIONS, key=lambda a: abs(raw - a))
        effective = bpm / closest
        period = 60.0 / effective if effective > 0 else 0.0
        fraction = ((estimates[label].phase % period) / period) if period > 0 else 0.0
        fractions.append(fraction)
        ratios.append(round(raw / closest, 4))
        detail.append(
            {
                "label": label,
                "bpm": bpm,
                "metrical_relation": round(closest, 4),
                "normalised_ratio": ratios[-1],
                "effective_bpm": round(effective, 2),
                "phase_s": estimates[label].phase,
                "phase_fraction": round(fraction, 4),
                "contrast": estimates[label].contrast,
                "confident": estimates[label].confident,
            }
        )

    def circular(a: float, b: float) -> float:
        delta = abs(a - b) % 1.0
        return min(delta, 1.0 - delta)

    pairwise = []
    for i in range(len(fractions)):
        for j in range(i + 1, len(fractions)):
            deviation = circular(fractions[i], fractions[j])
            pairwise.append(
                {
                    "a": detail[i]["label"],
                    "b": detail[j]["label"],
                    "phase_deviation": round(deviation, 4),
                    "within_tolerance": deviation <= PHASE_TOLERANCE_PERIOD,
                }
            )

    spread = round(max(ratios) / min(ratios), 4) if ratios else 1.0
    phase_ok = all(item["within_tolerance"] for item in pairwise) if pairwise else True
    opaque = [row["label"] for row in detail if row["contrast"] < 1.35]
    agree = phase_ok and not opaque
    return {
        "bpm": round(reference, 2),
        "reference": reference_label,
        "agree": agree,
        "criterion": "phase coherence",
        "phase_tolerance_period": PHASE_TOLERANCE_PERIOD,
        "pairwise": pairwise,
        "spread": spread,
        "opaque_clips": opaque,
        "detail": detail,
        "phase_note": (
            "cross-clip phase is decisive when the clips share a time origin; "
            "on independent excerpts agreement can be chance and must be read as weak evidence"
        ),
        "reason": "grids superimpose within phase tolerance"
        if agree
        else "phase deviates beyond tolerance, or a clip is rhythmically opaque",
    }


def _score_envelope(
    env: Any, fps: float, bpm_min: float, bpm_max: float
) -> tuple[float, float, float, float, List[Dict[str, float]]]:
    """Tempo, phase, score, contrast for one onset envelope."""
    import librosa

    baseline = float(env.mean()) + 1e-9
    ac = librosa.autocorrelate(env, max_size=int(fps * 2.0))
    lags = np.arange(ac.size, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        lag_bpm = np.where(lags > 0, 60.0 * fps / np.maximum(lags, 1e-9), 0.0)
    prior = np.exp(-0.5 * (np.log2(np.maximum(lag_bpm, 1e-9) / 110.0) / 0.9) ** 2)
    in_range = (lag_bpm >= bpm_min) & (lag_bpm <= bpm_max)
    salience = np.where(in_range, ac * prior, 0.0)
    if not np.any(salience > 0):
        return 0.0, 0.0, 0.0, 0.0, []
    coarse = float(lag_bpm[int(np.argmax(salience))])

    scored: List[Dict[str, float]] = []
    for bpm in np.arange(
        max(bpm_min, coarse * 0.94), min(bpm_max, coarse * 1.06) + 0.01, 0.1
    ):
        score, phase = _grid_score(env, 60.0 / bpm * fps)
        if score > 0:
            scored.append(
                {
                    "bpm": round(float(bpm), 2),
                    "score": round(score / baseline, 3),
                    "phase_frames": float(phase),
                }
            )
    if not scored:
        return 0.0, 0.0, 0.0, 0.0, []
    scored.sort(key=lambda item: item["score"], reverse=True)
    best = scored[0]
    second = next(
        (c for c in scored[1:] if abs(c["bpm"] - best["bpm"]) > 2.0),
        scored[1] if len(scored) > 1 else best,
    )
    contrast = best["score"] / max(second["score"], 1e-9)
    return (
        float(best["bpm"]),
        float(best["phase_frames"]) / fps,
        float(best["score"]),
        float(contrast),
        scored[:8],
    )


def estimate_best(
    path: str,
    *,
    bpm_min: float = 60.0,
    bpm_max: float = 200.0,
    seconds: float = 120.0,
) -> Estimate:
    """Decode once, score the low band and the full band, keep the higher contrast.

    A clip whose kick is weak carries its rhythm in the hats or in the vocal cadence:
    the low band alone is then too poor and returns a confident-looking wrong answer.
    """
    import librosa

    try:
        y, sr = _decode(path, seconds)
    except Exception as exc:  # noqa: BLE001
        return Estimate(reason=f"{type(exc).__name__}: {exc}"[:180])
    if y.size == 0:
        return Estimate(reason="empty audio")

    fps = sr / HOP
    envelopes = {
        "lowband": librosa.onset.onset_strength(
            y=_lowband(y, sr), sr=sr, hop_length=HOP
        ),
        "fullband": librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP),
    }
    winner: Optional[Estimate] = None
    for name, env in envelopes.items():
        if env is None or env.size == 0:
            continue
        bpm, phase, score, contrast, candidates = _score_envelope(
            env, fps, bpm_min, bpm_max
        )
        if bpm <= 0:
            continue
        candidate = Estimate(
            bpm=bpm,
            phase=round(phase, 4),
            score=round(score, 3),
            contrast=round(contrast, 3),
            candidates=candidates,
            method=f"comb-{name}+prior",
            reason=""
            if contrast >= 1.35
            else "weak contrast between the best tempo and the runner-up",
        )
        if winner is None or candidate.contrast > winner.contrast:
            winner = candidate

    if winner is None:
        return Estimate(reason="no band produced a usable estimate")
    if winner.contrast < 1.35:
        winner.reason = (
            "rhythmically opaque: both bands stayed below 1.35 contrast, "
            "rhythmic cuts must be blocked on this clip"
        )
    return winner


def estimate_stable(
    path: str,
    *,
    canonical_seconds: float = 90.0,
    full_seconds: float = 900.0,
    bpm_min: float = 60.0,
    bpm_max: float = 200.0,
) -> Estimate:
    """Two windows, take the lower contrast: in doubt, do not act.

    The same clip scored 1.53 on 120 s and 1.31 on 90 s, so a confidence that moves with
    the length of the sample is not a measurement of the track but of the reading window.
    The clip is usable only if BOTH windows clear the floor; the one that fails is named
    so the log says which reading triggered the block.
    """
    full = estimate_best(path, bpm_min=bpm_min, bpm_max=bpm_max, seconds=full_seconds)
    canonical = estimate_best(
        path, bpm_min=bpm_min, bpm_max=bpm_max, seconds=canonical_seconds
    )
    floor = 1.35
    worst = min(full.contrast, canonical.contrast)
    triggered = "full" if full.contrast <= canonical.contrast else "canonical"
    winner = full if full.contrast <= canonical.contrast else canonical
    winner = Estimate(
        bpm=winner.bpm,
        phase=winner.phase,
        score=winner.score,
        baseline=winner.baseline,
        contrast=round(worst, 3),
        candidates=winner.candidates,
        method=winner.method,
        reason=(
            "rhythmically opaque: contrast "
            f"{worst:.2f} in the {triggered} window "
            f"(full {full.contrast:.2f} / canonical {canonical.contrast:.2f})"
            if worst < floor
            else ""
        ),
    )
    setattr(
        winner,
        "windows",
        {
            "full_seconds": full_seconds,
            "canonical_seconds": canonical_seconds,
            "contrast_full": round(full.contrast, 3),
            "contrast_canonical": round(canonical.contrast, 3),
            "min": round(worst, 3),
            "triggered_by": triggered,
            "bpm_full": full.bpm,
            "bpm_canonical": canonical.bpm,
        },
    )
    return winner
