"""vx.vad — speech activity from Silero VAD, not from an energy guess.

Why this exists: the p10/p90 RMS estimator measures "quiet frames", and a pause inside
a word, a breath, or a sustained vowel all look quiet under that rule. The result is a
bad noise floor, a bad SNR, and therefore a denoise decision that can chew the
fundamental of the voice.

Silero gives a per-window speech probability. From its mask we get two things:

  * **speech regions** — the only place where a cut is not allowed to land;
  * **a speech-aware noise floor** — RMS measured *outside* speech, which is what
    "background noise" actually means.

The model is a 2.3 MB ONNX (Silero v5.1.2, 512-sample windows at 16 kHz, carried state).
Both the build and the window were chosen by measurement, not by the docs: the model on
master returns max 0.096 probability on audible speech, the v5.1.2 build with 512-sample
windows returns a speech ratio of 0.841 against an energy-based target of 0.82.
Inference runs on CPU through onnxruntime: it is small enough that a GPU probe would
cost more than it saves.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import ffmpeg as ff

SAMPLE_RATE = 16000
WINDOW = 512
DEFAULT_THRESHOLD = 0.5
DEFAULT_MIN_SPEECH = 0.25
DEFAULT_MIN_SILENCE = 0.35

MODEL = Path(__file__).resolve().parents[2] / "assets" / "models" / "silero_vad.onnx"


@dataclass
class VadResult:
    engine: str = "none"
    available: bool = False
    reason: str = ""
    regions: List[Dict[str, float]] = field(default_factory=list)
    speech_ratio: float = 0.0
    speech_dbfs: float = -90.0
    noise_dbfs: float = -90.0
    snr_db: float = 0.0
    windows: int = 0
    threshold: float = DEFAULT_THRESHOLD
    duration: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def available() -> bool:
    if not MODEL.is_file():
        return False
    try:
        import onnxruntime  # type: ignore  # noqa: F401
        import numpy  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001 - optional stack
        return False
    return True


def _decode(path: str | Path) -> Any:
    """Mono float32 at 16 kHz, through a temp file (ffmpeg stdout is unreliable here)."""
    import numpy as np
    import tempfile

    target = Path(tempfile.mkdtemp(prefix="vxvad-")) / "pcm.f32"
    proc = ff.run_ff(
        [
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "f32le",
            "-y",
            str(target),
        ],
        loglevel="error",
        timeout=3600,
    )
    if not proc.ok or not target.exists():
        raise RuntimeError(proc.tail(3) or "decode failed")
    return np.fromfile(target, dtype=np.float32)


def _probabilities(pcm: Any) -> Any:
    import numpy as np
    import onnxruntime as ort

    session = ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])
    state = np.zeros((2, 1, 128), dtype=np.float32)
    sr = np.array(SAMPLE_RATE, dtype=np.int64)
    probs: List[float] = []
    for start in range(0, max(0, len(pcm) - WINDOW + 1), WINDOW):
        chunk = pcm[start : start + WINDOW].reshape(1, -1).astype(np.float32)
        out, state = session.run(None, {"input": chunk, "state": state, "sr": sr})
        probs.append(float(out.reshape(-1)[0]))
    return np.asarray(probs, dtype=np.float32)


def _regions_from_probs(
    probs: Any, *, threshold: float, min_speech: float, min_silence: float
) -> List[Dict[str, float]]:
    """Threshold with hysteresis: a region must last `min_speech` and a gap `min_silence`."""
    step = WINDOW / SAMPLE_RATE
    raw: List[Dict[str, float]] = []
    active = False
    start = 0.0
    for index, prob in enumerate(probs):
        t = index * step
        if not active and prob >= threshold:
            active, start = True, t
        elif active and prob < threshold:
            if t - start >= min_speech:
                raw.append({"start": round(start, 3), "end": round(t, 3)})
            active = False
    if active:
        end = len(probs) * step
        if end - start >= min_speech:
            raw.append({"start": round(start, 3), "end": round(end, 3)})

    merged: List[Dict[str, float]] = []
    for region in raw:
        if merged and region["start"] - merged[-1]["end"] < min_silence:
            merged[-1]["end"] = region["end"]
        else:
            merged.append(dict(region))
    return merged


def _db(value: float) -> float:
    return round(20.0 * math.log10(max(value, 1e-12)), 2)


def _levels(pcm: Any, regions: List[Dict[str, float]]) -> tuple[float, float]:
    """RMS of speech vs RMS of everything outside speech, both in dBFS."""
    import numpy as np

    if pcm.size == 0:
        return -90.0, -90.0
    mask = np.zeros(pcm.shape[0], dtype=bool)
    for region in regions:
        a = int(region["start"] * SAMPLE_RATE)
        b = min(int(region["end"] * SAMPLE_RATE), pcm.shape[0])
        if b > a:
            mask[a:b] = True
    speech = pcm[mask]
    rest = pcm[~mask]
    speech_rms = float(np.sqrt(np.mean(np.square(speech)))) if speech.size else 0.0
    noise_rms = float(np.sqrt(np.mean(np.square(rest)))) if rest.size else 0.0
    return _db(speech_rms), _db(noise_rms)


def detect(
    path: str | Path,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_speech: float = DEFAULT_MIN_SPEECH,
    min_silence: float = DEFAULT_MIN_SILENCE,
) -> VadResult:
    """Speech regions and a speech-aware SNR. Never raises: degrades with a reason."""
    if not available():
        return VadResult(reason="silero model or onnxruntime unavailable")
    try:
        pcm = _decode(path)
        probs = _probabilities(pcm)
    except Exception as exc:  # noqa: BLE001 - VAD is an enhancement, not a requirement
        return VadResult(reason=f"{type(exc).__name__}: {exc}"[:200])

    if probs.size == 0:
        return VadResult(reason="audio too short for a single VAD window")

    regions = _regions_from_probs(
        probs, threshold=threshold, min_speech=min_speech, min_silence=min_silence
    )
    speech_dbfs, noise_dbfs = _levels(pcm, regions)
    duration = len(pcm) / SAMPLE_RATE
    speech_seconds = sum(region["end"] - region["start"] for region in regions)
    return VadResult(
        engine="silero-v5",
        available=True,
        regions=regions,
        speech_ratio=round(speech_seconds / duration, 4) if duration else 0.0,
        speech_dbfs=speech_dbfs,
        noise_dbfs=noise_dbfs,
        snr_db=round(speech_dbfs - noise_dbfs, 2),
        windows=int(probs.size),
        threshold=float(threshold),
        duration=round(duration, 3),
    )


def gaps_from(
    regions: List[Dict[str, float]], duration: float, *, min_gap: float = 0.35
) -> List[Dict[str, float]]:
    """Non-speech spans between speech regions: the only safe place to cut."""
    out: List[Dict[str, float]] = []
    cursor = 0.0
    for region in regions:
        if region["start"] - cursor >= min_gap:
            out.append(
                {
                    "start": round(cursor, 3),
                    "end": round(region["start"], 3),
                    "duration": round(region["start"] - cursor, 3),
                }
            )
        cursor = max(cursor, region["end"])
    if duration - cursor >= min_gap:
        out.append(
            {
                "start": round(cursor, 3),
                "end": round(duration, 3),
                "duration": round(duration - cursor, 3),
            }
        )
    return out


def describe() -> Dict[str, Any]:
    return {
        "engine": "silero-v5",
        "model": str(MODEL),
        "exists": MODEL.is_file(),
        "bytes": MODEL.stat().st_size if MODEL.is_file() else 0,
        "available": available(),
    }
