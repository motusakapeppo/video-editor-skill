"""vx.structure -- two independent readings of "structure".

*The edit's shape against a declared profile* (F26-T04). :mod:`vx.profiles` says
what a genre is; :func:`check_structure` says whether a document is built that
way. It reads the timeline and nothing else: no render, no ffmpeg, no filesystem,
no import of the registry at runtime. The refusals are the document's own
numbers, never a guess. Beat alignment is deliberately *not* checked: a document
carries no beat grid, no BPM and no marker on the timeline, so a beat check would
have to invent the beats.

*The song's shape from its audio* (vx.structure/1). :func:`analyze` listens to a
track and names its sections -- intro, verse, chorus, bridge, outro -- because a
brief that says "the important parts are the ones with the chorus" needs a
machine that knows what a chorus is. It is a measurement, not a model:

  1. decode to mono 8 kHz PCM with ffmpeg (the only decoder this module uses),
     then frame it into chroma + RMS + spectral flatness;
  2. find boundaries where the material on the two sides actually differs
     (chroma/energy change points), corroborated by real silence gaps;
  3. compare every pair of sections: the chorus is the family of sections that
     repeats most similar to itself *and* carries the highest energy;
  4. say how sure it is. No repeat, no chorus: ``chorus_span`` is ``None`` and
     every confidence is ``low``, with the reason in ``notes``.

No network, no ML model, no learned weights. The two readings share the file
because they share the word "structure"; they never call each other.
"""

from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import ffmpeg as ff

if TYPE_CHECKING:  # pragma: no cover - typing only, never an import at runtime
    from .edl3 import Timeline3
    from .profiles import Profile

__all__ = [
    "DENSITY_WINDOW",
    "SCHEMA",
    "analyze",
    "check_structure",
]

#: The window every ``cut_density_*`` number is quoted over, in seconds: the
#: profile bands are "cuts per ten seconds", the unit D031 already uses.
DENSITY_WINDOW = 10.0


def _layer0_spans(timeline: "Timeline3") -> List[Tuple[float, float]]:
    """The ``(start, end)`` of every layer-0 clip, in timeline order.

    Same anchoring as :func:`vx.edl3._clip_start_times`: ``dst_in`` when the
    document declares it, otherwise the concatenation cursor. Overlay layers are
    windows over this base track and never extend it.
    """
    cursor = 0.0
    spans: List[Tuple[float, float]] = []
    for clip in timeline.clips:
        if clip.layer != 0:
            continue
        start = cursor if clip.dst_in is None else float(clip.dst_in)
        end = start + max(0.0, clip.timeline_duration)
        spans.append((start, end))
        cursor = max(cursor, end)
    spans.sort(key=lambda span: span[0])
    return spans


def _cut_count(spans: List[Tuple[float, float]]) -> int:
    """Boundaries where one layer-0 clip ends and the next begins on the timeline.

    The first layer-0 clip's start is not a cut (it is the opening frame), and a
    boundary counts only when the two windows meet: a gap is a gap, not a cut.
    """
    return sum(
        1
        for (_, previous_end), (start, _) in zip(spans, spans[1:])
        if abs(start - previous_end) <= 1e-6
    )


def _hook_instant(
    timeline: "Timeline3", spans: List[Tuple[float, float]]
) -> Optional[float]:
    """The first instant the picture or an opening overlay visibly changes.

    Candidates are: the first hard cut on layer 0 (the start of the second
    layer-0 clip), every text layer's ``time_in``, and the start of every
    generator overlay on a layer >= 1. The earliest candidate wins, so a text
    card visible from frame 0 is a hook at ``0.0``; an opening generator on
    layer 0 is the picture itself, not a change.
    """
    instants: List[float] = []
    if len(spans) > 1 and abs(spans[1][0] - spans[0][1]) <= 1e-6:
        instants.append(spans[1][0])
    instants.extend(float(layer.time_in) for layer in timeline.text_layers)
    for clip in timeline.clips:
        if clip.layer >= 1 and clip.generator is not None:
            instants.append(0.0 if clip.dst_in is None else float(clip.dst_in))
    return min(instants) if instants else None


def _has_interview_evidence(timeline: "Timeline3") -> bool:
    """Whether the document itself declares spoken/interview material.

    ``Clip3.audio_policy`` is a *time* policy (``segments``/``silence``/``pitch``,
    see :class:`vx.edl3_time.AudioPolicy`), not a role, so it cannot carry
    "dialogue". The two carriers the document really has are a nested sequence
    named as an interview (``interview``/``intervista`` in its id) and an audio
    bus whose role is ``dialogue`` and which carries at least one clip. Nothing
    is inferred from the picture.
    """
    for clip in timeline.clips:
        name = (clip.sequence or "").lower()
        if "interview" in name or "intervista" in name:
            return True
    for track in timeline.audio:
        if track.role.strip().lower() == "dialogue" and track.clips:
            return True
    return any(
        "interview" in str(key).lower() or "intervista" in str(key).lower()
        for key in timeline.sequences
    )


def check_structure(timeline: "Timeline3", profile: "Profile") -> Tuple[str, ...]:
    """One human detail per way the edit breaks ``profile``'s declared grammar.

    Pure: the document is read, never rendered; nothing is guessed. Each detail
    starts with its stable code, so ``Timeline3.validate()`` can report it.

    *Duration.* The timeline is the extent of layer 0, from the first base clip's
    start to the last base clip's end (overlays do not lengthen the edit). It must
    sit inside ``[min_seconds, max_seconds]``.

    *Cut count and rate.* A cut is a boundary where one layer-0 clip ends and the
    next begins at the same instant (within ``1e-6`` s); the first clip's start is
    the opening frame, not a cut. The rate is ``cuts * 10 / duration`` -- cuts per
    :data:`DENSITY_WINDOW` seconds over the whole timeline -- and must sit inside
    ``[cut_density_min, cut_density_max]``. A transition across the boundary still
    counts as a boundary; a gap does not count as a cut.

    *Hook.* Only when ``hook_seconds > 0``. The hook instant is the earliest of:
    the first hard cut on layer 0; any text layer's ``time_in``; the start of any
    generator overlay on a layer >= 1. A text card up from frame 0 hooks at 0.0.
    The instant must be ``<= hook_seconds``; a one-shot document with no text and
    no overlay has no visible change at all and is reported as such.

    *Interview.* Only when ``interview_driven`` is true. Only what the document
    carries counts: a nested sequence whose id names an interview, or a
    ``dialogue`` audio bus holding at least one clip. (``Clip3.audio_policy`` is a
    time policy, not a role.) Without either, the detail says the grammar is
    unbacked; no interview is inferred from the picture.

    *Beat.* Not checked, deliberately. ``beat_driven`` profiles declare that cuts
    should land on a musical grid, but the document carries no beat grid, no BPM
    and no marker on the timeline: a check would have to invent the beats. This
    function stays silent about it rather than guess.
    """
    spans = _layer0_spans(timeline)
    span = max((end for _, end in spans), default=0.0)
    cuts = _cut_count(spans)
    problems: List[str] = []

    if span < profile.min_seconds - 1e-9 or span > profile.max_seconds + 1e-9:
        problems.append(
            f"STRUCTURE_DURATION: {span:.3f}s is outside the "
            f"{profile.min_seconds:g}-{profile.max_seconds:g}s window for "
            f"{profile.id}"
        )

    if span > 0.0:
        rate = cuts * DENSITY_WINDOW / span
        if rate < profile.cut_density_min - 1e-9 or rate > profile.cut_density_max + 1e-9:
            problems.append(
                f"STRUCTURE_DENSITY: {rate:.1f} cuts per {DENSITY_WINDOW:g}s "
                f"({cuts} cuts between layer-0 clips over {span:.3f}s) is outside "
                f"the {profile.cut_density_min:g}-{profile.cut_density_max:g} band "
                f"for {profile.id}"
            )

    if profile.hook_seconds > 0.0:
        instant = _hook_instant(timeline, spans)
        if instant is None:
            problems.append(
                f"STRUCTURE_HOOK: no visible change in {span:.3f}s: one shot runs "
                f"to the end, so the {profile.hook_seconds:g}s hook for "
                f"{profile.id} never lands"
            )
        elif instant > profile.hook_seconds + 1e-9:
            problems.append(
                f"STRUCTURE_HOOK: the first visible change is at {instant:.3f}s, "
                f"after the {profile.hook_seconds:g}s hook for {profile.id}"
            )

    if profile.interview_driven and not _has_interview_evidence(timeline):
        problems.append(
            f"STRUCTURE_INTERVIEW: the {profile.id} grammar is interview-driven, "
            "but no nested sequence is named as an interview and no audio track "
            "declares dialogue material"
        )

    return tuple(problems)

# ---------------------------------------------------------------------------
# The song's shape, read from its audio (vx.structure/1)
# ---------------------------------------------------------------------------

#: Contract identifier of :func:`analyze`'s payload.
SCHEMA = "vx.structure/1"

#: Analysis rate. Chroma needs pitch, not fidelity; 8 kHz covers 65-2000 Hz.
_SR = 8000
#: FFT frame (256 ms) and hop (64 ms): enough resolution to place a boundary.
_FRAME = 2048
_HOP = 512
#: Frames per analysis chunk, so a 10-minute track never materialises its
#: spectra all at once.
_CHUNK = 2048
#: Grid the change-point scan is evaluated on.
_STEP = 0.5
#: Half-window compared on each side of a candidate boundary.
_HALF = 4.0
#: Frame is "active" when above this many dB below the loudest frame, and
#: above this absolute floor.
_DEPTH_DB = 40.0
_FLOOR_DB = -55.0
#: A gap is a section-worthy silence only when this long and this deep.
_MIN_SILENCE = 0.5
_SILENCE_FLOOR_DB = -45.0
#: Two sections are the "same material" only when the chroma agrees this well,
#: the durations are comparable, and the combined similarity clears the bar.
_REPEAT_COS = 0.78
_REPEAT_SIM = 0.82
_RATIO_LIMIT = 1.75
#: Per-frame spectral flatness above this is broadband material (noise,
#: cymbals-only): it cannot carry a chorus, however similar it looks.
_TONAL_FLATNESS = 0.45
#: Tempo search band and the autocorrelation peak below which there is no
#: measurable pulse at all (a sustained tone has none).
_TEMPO_MIN, _TEMPO_MAX = 60.0, 200.0
_TEMPO_FLOOR = 0.25


@dataclass
class _Section:
    """One contiguous span, with the measurements its labels are based on."""

    index: int
    t_in: float
    t_out: float
    chroma: np.ndarray
    energy: float
    edb: float
    flatness: float
    silent: float
    edge: float = 0.0
    label: str = "verse"
    repeat_of: Optional[int] = None
    confidence: str = "low"

    @property
    def duration(self) -> float:
        return max(0.0, self.t_out - self.t_in)


def _decode(path: str) -> Tuple[np.ndarray, str]:
    """Mono float32 PCM at :data:`_SR`, always decoded by ffmpeg.

    ffmpeg is the module's single decoder: it downmixes to mono, resamples to
    :data:`_SR` (``swr``) and writes raw ``f32le`` to a temp file (``run_ff``
    captures text, so stdout cannot carry samples). One code path, one set of
    numbers, whatever the container. A missing ffmpeg is a declared failure:
    :class:`vx.contract.MissingDependency` propagates from :func:`vx.ffmpeg.ffmpeg`.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"audio not found: {p}")
    with tempfile.TemporaryDirectory(
        prefix="vxstruct_", ignore_cleanup_errors=True
    ) as tmp:
        out = Path(tmp) / "audio.pcm"
        proc = ff.run_ff(
            [
                "-i",
                str(p),
                "-map",
                "0:a:0",
                "-vn",
                "-f",
                "f32le",
                "-ac",
                "1",
                "-ar",
                str(_SR),
                str(out),
            ],
            loglevel="error",
            step="probe",
        )
        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(f"audio decode failed for {p.name}: {proc.tail(3)}")
        pcm = np.fromfile(out, dtype="<f4")
    pcm = np.asarray(pcm, dtype=np.float32)
    if pcm.size == 0:
        raise RuntimeError(f"no audio samples decoded from {p.name}")
    return pcm, f"decoded by ffmpeg to mono {_SR} Hz PCM"


def _features(pcm: np.ndarray) -> Dict[str, np.ndarray]:
    """Frame-wise chroma, RMS, dBFS and spectral flatness, computed in chunks.

    Chroma folds every 65-2000 Hz bin into its pitch class and L2-normalises the
    frame, so a sustained tone is one hot vector and white noise is nearly flat.
    Flatness is the geometric/arithmetic mean of the frame's power spectrum: it
    is near 0 for tonal material and near 0.56 for Gaussian noise.
    """
    if len(pcm) < _FRAME:
        pcm = np.pad(pcm, (0, _FRAME - len(pcm)))
    n = 1 + (len(pcm) - _FRAME) // _HOP
    window = np.hanning(_FRAME).astype(np.float32)
    freqs = np.fft.rfftfreq(_FRAME, 1.0 / _SR)
    band = (freqs >= 65.0) & (freqs <= 2000.0)
    pitch = np.mod(
        np.round(69.0 + 12.0 * np.log2(freqs[band] / 440.0)).astype(int), 12
    )
    onehot = np.zeros((int(band.sum()), 12), dtype=np.float32)
    onehot[np.arange(onehot.shape[0]), pitch] = 1.0

    chroma = np.zeros((n, 12), dtype=np.float32)
    rms = np.zeros(n, dtype=np.float32)
    flat = np.zeros(n, dtype=np.float32)
    for start in range(0, n, _CHUNK):
        stop = min(n, start + _CHUNK)
        idx = (start + np.arange(stop - start))[:, None] * _HOP + np.arange(_FRAME)
        frames = pcm[idx]
        rms[start:stop] = np.sqrt(
            np.mean(np.square(frames, dtype=np.float64), axis=1) + 1e-12
        )
        power = np.abs(np.fft.rfft(frames * window, axis=1))[:, band] ** 2
        chroma[start:stop] = power @ onehot
        gm = np.exp(np.mean(np.log(power + 1e-12), axis=1))
        flat[start:stop] = gm / (np.mean(power, axis=1) + 1e-12)

    norm = np.linalg.norm(chroma, axis=1, keepdims=True) + 1e-12
    return {
        "chroma": chroma / norm,
        "rms": rms,
        "db": 20.0 * np.log10(rms + 1e-12),
        "flat": flat,
    }


def _frame_time(index: int) -> float:
    return (index * _HOP + _FRAME / 2.0) / _SR


def _frame_slice(t_in: float, t_out: float, n: int) -> slice:
    start = int(round((t_in * _SR - _FRAME / 2.0) / _HOP))
    stop = int(round((t_out * _SR - _FRAME / 2.0) / _HOP))
    start = max(0, min(n - 1, start))
    stop = max(start + 1, min(n, stop))
    return slice(start, stop)


def _side(
    feat: Dict[str, np.ndarray], t_in: float, t_out: float, floor: float
) -> Tuple[np.ndarray, float, float]:
    """Mean chroma (active frames), mean energy in dB, and silent fraction."""
    sl = _frame_slice(t_in, t_out, len(feat["chroma"]))
    db = feat["db"][sl]
    chroma = feat["chroma"][sl]
    if db.size == 0:
        return np.zeros(12, dtype=np.float32), -120.0, 1.0
    active = db > floor
    silent = 1.0 - float(active.mean())
    if not active.any():
        return np.zeros(12, dtype=np.float32), -120.0, silent
    vector = chroma[active].mean(axis=0)
    norm = float(np.linalg.norm(vector))
    if norm > 1e-9:
        vector = vector / norm
    energy_db = float(10.0 * np.log10(np.mean(10.0 ** (db[active] / 10.0)) + 1e-12))
    return vector.astype(np.float32), energy_db, silent


def _contrast(
    feat: Dict[str, np.ndarray], at: float, half: float, floor: float
) -> Tuple[float, float, float, float]:
    """How different the material before ``at`` is from the material after it.

    Returns ``(distance, chroma cosine, energy gap dB, silent-fraction gap)``.
    The distance weights harmony/timbre (0.7), loudness (0.3) and the presence
    of silence (0.5, capped at 1) so a gap between two held tones still shows.
    """
    left, left_db, left_silent = _side(feat, at - half, at, floor)
    right, right_db, right_silent = _side(feat, at, at + half, floor)
    nl, nr = float(np.linalg.norm(left)), float(np.linalg.norm(right))
    if nl < 1e-9 and nr < 1e-9:
        cosine = 1.0
    elif nl < 1e-9 or nr < 1e-9:
        cosine = 0.0
    else:
        cosine = float(np.dot(left, right))
    gap = abs(left_db - right_db)
    silence = abs(left_silent - right_silent)
    distance = (
        0.7 * (1.0 - cosine) + 0.3 * min(1.0, gap / 15.0) + 0.5 * silence
    )
    return min(1.0, distance), cosine, gap, silence


def _silence_runs(feat: Dict[str, np.ndarray], duration: float) -> List[Tuple[float, float]]:
    """Spans at least :data:`_MIN_SILENCE` long and :data:`_DEPTH_DB` below peak."""
    db = feat["db"]
    quiet = (db < float(db.max()) - _DEPTH_DB) & (db < _SILENCE_FLOOR_DB)
    runs: List[Tuple[float, float]] = []
    index = 0
    while index < len(quiet):
        if not quiet[index]:
            index += 1
            continue
        stop = index
        while stop < len(quiet) and quiet[stop]:
            stop += 1
        # Sample bounds, not frame centres: the run then starts at the first
        # silent sample instead of half a frame later.
        start_t = index * _HOP / _SR
        end_t = min(duration, stop * _HOP / _SR)
        if end_t - start_t >= _MIN_SILENCE and end_t - start_t <= 0.85 * duration:
            runs.append((max(0.0, start_t), end_t))
        index = stop
    return runs


def _tempo(pcm: np.ndarray) -> Tuple[float, float]:
    """BPM from the autocorrelation of the onset envelope, and its peak score.

    A sustained tone or steady noise has no pulse: the score stays under
    :data:`_TEMPO_FLOOR` and the answer is ``0.0`` instead of a made-up number.
    """
    win, hop = 160, 80  # 20 ms / 10 ms at 8 kHz
    n = 1 + max(0, (len(pcm) - win) // hop)
    if n < 60:
        return 0.0, 0.0
    rms = np.zeros(n, dtype=np.float32)
    for start in range(0, n, 8192):
        stop = min(n, start + 8192)
        idx = (start + np.arange(stop - start))[:, None] * hop + np.arange(win)
        rms[start:stop] = np.sqrt(
            np.mean(np.square(pcm[idx], dtype=np.float64), axis=1) + 1e-10
        )
    onset = np.maximum(0.0, np.diff(20.0 * np.log10(rms + 1e-10)))
    onset -= np.median(onset)
    onset[onset < 0] = 0.0
    if float(onset.std()) < 1e-6:
        return 0.0, 0.0
    series = onset - onset.mean()
    size = 1 << int(math.ceil(math.log2(max(2, 2 * len(series)))))
    ac = np.fft.irfft(np.abs(np.fft.rfft(series, size)) ** 2)[: len(series)]
    if ac[0] <= 0:
        return 0.0, 0.0
    ac = ac / ac[0]
    rate = 100.0
    # Lag in frames: the fastest tempo sets the shortest lag.
    lag_min = max(2, int(round(60.0 / _TEMPO_MAX * rate)))
    lag_max = min(len(ac) - 2, int(round(60.0 / _TEMPO_MIN * rate)))
    if lag_max <= lag_min + 1:
        return 0.0, 0.0
    # Overlap compensation: long lags average fewer samples.
    lags = np.arange(lag_min, lag_max + 1)
    window = ac[lags] * len(series) / np.maximum(1, len(series) - lags)
    peaks = [
        (float(window[i]), int(lags[i]))
        for i in range(1, len(window) - 1)
        if window[i] >= window[i - 1] and window[i] >= window[i + 1]
    ]
    if not peaks:
        return 0.0, 0.0
    peaks.sort(reverse=True)
    best_score, best_lag = peaks[0]
    # Metrical preference: a strong peak at double tempo is the same pulse,
    # heard one level finer. Take it only when it is nearly as strong.
    for score, lag in peaks:
        if abs(2 * lag - best_lag) <= 2 and score >= 0.9 * best_score and lag >= lag_min:
            best_score, best_lag = score, lag
            break
    if best_score < _TEMPO_FLOOR:
        return 0.0, float(best_score)
    y0, y1, y2 = (
        float(ac[best_lag - 1]),
        float(ac[best_lag]),
        float(ac[best_lag + 1]),
    )
    denom = y0 - 2.0 * y1 + y2
    shift = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-12 else 0.0
    bpm = 60.0 * rate / (best_lag + max(-1.0, min(1.0, shift)))
    return (bpm if _TEMPO_MIN <= bpm <= _TEMPO_MAX else 0.0), float(best_score)


def _boundaries(
    feat: Dict[str, np.ndarray],
    duration: float,
    floor: float,
    runs: Sequence[Tuple[float, float]],
    min_section: float,
) -> List[Tuple[float, float]]:
    """Section boundaries with their contrast, selected on a coarse grid.

    A novelty peak is only accepted when the two sides are corroborated by a
    real difference -- chroma cosine <= 0.90, energy gap >= 6 dB, or a silence
    gap nearby -- so random fluctuation in steady material cannot invent one.
    Silence runs are boundaries in their own right. Peaks are taken strongest
    first, keeping at least ``0.75 * min_section`` between them; anything still
    too short is merged afterwards.
    """
    if duration <= 2.0 * _HALF or len(feat["db"]) < 8:
        return []
    centers = np.arange(_STEP / 2.0, duration, _STEP)
    values = np.array(
        [_contrast(feat, float(c), _HALF, floor)[0] for c in centers], dtype=float
    )
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median))) + 1e-9
    # Three guards, because one is not enough: an absolute floor so steady
    # material cannot invent a boundary, a robust deviation so ramps do not
    # raise the bar above their own peak, and a share of the strongest change
    # so only prominent edges are taken.
    threshold = max(0.10, median + 2.5 * 1.4826 * mad, 0.40 * float(values.max()))
    span = max(1, int(round(1.0 / _STEP)))
    candidates: List[Tuple[float, float]] = []
    for i in range(len(values)):
        if values[i] < threshold:
            continue
        lo, hi = max(0, i - span), min(len(values), i + span + 1)
        if values[i] < float(values[lo:hi].max()) - 1e-12:
            continue
        if centers[i] < 1.0 or duration - centers[i] < 1.0:
            continue
        # Refine on the frame grid: the coarse centre is only accurate to _STEP.
        best = (0.0, float(centers[i]))
        for t in np.arange(
            max(0.5, centers[i] - _STEP), min(duration - 0.5, centers[i] + _STEP) + 1e-9, _HOP / _SR
        ):
            value = _contrast(feat, float(t), 1.5, floor)[0]
            if value > best[0]:
                best = (value, float(t))
        at = best[1]
        _, cosine, gap, _ = _contrast(feat, at, 1.5, floor)
        near_gap = any(a - 0.75 <= at <= b + 0.75 for a, b in runs)
        if cosine <= 0.90 or gap >= 6.0 or near_gap:
            candidates.append((float(best[0]), at))

    for start, end in runs:
        candidates.append((1.0, 0.5 * (start + end)))

    chosen: List[Tuple[float, float]] = []
    for strength, at in sorted(candidates, key=lambda item: -item[0]):
        if all(abs(at - other) >= max(2.0, 0.75 * min_section) for _, other in chosen):
            chosen.append((strength, at))
    return sorted(chosen, key=lambda item: item[1])


def _build_sections(
    feat: Dict[str, np.ndarray],
    boundaries: Sequence[Tuple[float, float]],
    duration: float,
    floor: float,
    min_section: float,
) -> List[_Section]:
    """Turn boundaries into sections, merging anything shorter than the minimum."""
    edges = [0.0] + [at for _, at in boundaries] + [duration]
    strengths = [value for value, _ in boundaries]
    while len(edges) > 2:
        lengths = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]
        shortest = min(range(len(lengths)), key=lambda i: lengths[i])
        if lengths[shortest] >= min_section - _HOP / _SR:
            break
        if shortest == 0:
            drop = 0
        elif shortest == len(edges) - 2:
            drop = len(strengths) - 1
        else:
            drop = shortest - 1 if strengths[shortest - 1] <= strengths[shortest] else shortest
        del edges[drop + 1]
        del strengths[drop]

    sections: List[_Section] = []
    for index in range(len(edges) - 1):
        t_in, t_out = float(edges[index]), float(edges[index + 1])
        chroma, energy_db, silent = _side(feat, t_in, t_out, floor)
        sl = _frame_slice(t_in, t_out, len(feat["chroma"]))
        active = feat["db"][sl] > floor
        energy = float(feat["rms"][sl].mean()) if feat["rms"][sl].size else 0.0
        flatness = (
            float(feat["flat"][sl][active].mean()) if active.any() else 1.0
        )
        sections.append(
            _Section(
                index=index,
                t_in=t_in,
                t_out=t_out,
                chroma=chroma,
                energy=energy,
                edb=energy_db,
                flatness=flatness,
                silent=silent,
                edge=float(strengths[index - 1]) if index >= 1 else 0.0,
            )
        )
    return sections


def _similarity(a: _Section, b: _Section) -> Tuple[float, float, bool]:
    """Combined similarity, chroma cosine, and whether a repeat may be claimed."""
    cosine = float(np.dot(a.chroma, b.chroma))
    gap = abs(a.edb - b.edb)
    shorter = max(1e-6, min(a.duration, b.duration))
    ratio = max(a.duration, b.duration) / shorter
    similarity = 0.75 * cosine + 0.25 * (1.0 - min(1.0, gap / 12.0))
    if ratio > _RATIO_LIMIT:
        similarity *= 0.65
    repeatable = cosine >= _REPEAT_COS and ratio <= _RATIO_LIMIT
    return similarity, cosine, repeatable


def _families(
    sections: Sequence[_Section],
) -> Tuple[List[List[int]], Dict[Tuple[int, int], float]]:
    """Connected components of the "same material" relation, plus cosines."""
    parent = list(range(len(sections)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    cosines: Dict[Tuple[int, int], float] = {}
    for i in range(len(sections)):
        for j in range(i + 1, len(sections)):
            similarity, cosine, repeatable = _similarity(sections[i], sections[j])
            cosines[(i, j)] = cosine
            if repeatable and similarity >= _REPEAT_SIM:
                union(i, j)
    groups: Dict[int, List[int]] = {}
    for index in range(len(sections)):
        groups.setdefault(find(index), []).append(index)
    return [sorted(members) for members in groups.values()], cosines


def analyze(path: str, *, min_section: float = 8.0) -> Dict[str, Any]:
    """Name the sections of the track at ``path`` and point at its chorus.

    ``min_section`` is the shortest span that may be called a section (seconds);
    shorter runs are merged into a neighbour. The returned dict follows
    :data:`SCHEMA`::

        {"schema": "vx.structure/1", "duration": float, "tempo_bpm": float,
         "sections": [{"label": ..., "t_in": ..., "t_out": ...,
                       "energy": ..., "repeat_of": int|null,
                       "confidence": "high|medium|low"}],
         "chorus_span": [t_in, t_out] | None,
         "notes": [...]}

    ``energy`` is the section's mean RMS in ``[0, 1]`` (silence inside the span
    counts as zero). ``repeat_of`` is the index of the earlier section this one
    repeats, or ``None`` when it is the first occurrence (or not a repeat at
    all). No repeat structure means no chorus: ``chorus_span`` is ``None``,
    every confidence is ``low`` and ``notes`` says why.
    """
    if (
        not isinstance(min_section, (int, float))
        or not math.isfinite(float(min_section))
        or min_section <= 0
    ):
        raise ValueError(f"min_section must be a positive number, got {min_section!r}")

    pcm, decode_note = _decode(path)
    duration = len(pcm) / float(_SR)
    feat = _features(pcm)
    peak = float(feat["db"].max())
    floor = max(_FLOOR_DB, peak - _DEPTH_DB)
    notes: List[str] = [decode_note]

    bpm, tempo_score = _tempo(pcm)
    if bpm > 0.0:
        notes.append(f"tempo {bpm:.1f} BPM (onset autocorrelation peak {tempo_score:.2f})")
    else:
        notes.append(
            "tempo not measurable: no onset pulse cleared the autocorrelation "
            f"floor (best {tempo_score:.2f} < {_TEMPO_FLOOR:g})"
        )

    runs = _silence_runs(feat, duration)
    boundaries = _boundaries(feat, duration, floor, runs, min_section)
    sections = _build_sections(feat, boundaries, duration, floor, min_section)
    notes.append(
        f"{len(sections)} section(s) from {len(boundaries)} boundary/ies; "
        f"silence gaps: {len(runs)}; shortest section {min(s.duration for s in sections):.2f}s"
    )

    groups, cosines = _families(sections)
    repeated = [members for members in groups if len(members) > 1]
    best_similarity = max(cosines.values(), default=0.0)
    max_energy = max((s.energy for s in sections), default=0.0) or 1e-12

    chorus_members: List[int] = []
    chorus_confidence = "low"
    rejected: List[str] = []
    scored: List[Tuple[float, List[int], float, float, float]] = []
    for members in repeated:
        pairs = [
            cosines[(a, b)]
            for pos, a in enumerate(members)
            for b in members[pos + 1 :]
        ]
        mean_cos = float(np.mean(pairs)) if pairs else 0.0
        mean_energy = float(np.mean([sections[i].energy for i in members]))
        flatness = float(np.mean([sections[i].flatness for i in members]))
        relative = mean_energy / max_energy
        if flatness > _TONAL_FLATNESS:
            rejected.append(
                f"sections {members} repeat (cosine {mean_cos:.2f}) but are "
                f"broadband (flatness {flatness:.2f} > {_TONAL_FLATNESS:g})"
            )
            continue
        scored.append(
            (mean_cos * (0.4 + 0.6 * relative), members, mean_cos, relative, flatness)
        )
    if scored:
        scored.sort(key=lambda item: (-item[0], sections[item[1][0]].t_in))
        _, chorus_members, mean_cos, relative, flatness = scored[0]
        if mean_cos >= 0.92 and relative >= 0.70:
            chorus_confidence = "high"
        elif mean_cos >= 0.86:
            chorus_confidence = "medium"
        notes.append(
            f"chorus = sections {chorus_members} (repeat cosine {mean_cos:.2f}, "
            f"energy {relative:.2f} of the loudest section, flatness {flatness:.2f})"
        )
    elif rejected:
        notes.append("no chorus claimed: " + "; ".join(rejected))
    else:
        notes.append(
            f"no chorus claimed: no section repeats (best similarity "
            f"{best_similarity:.2f} < {_REPEAT_SIM:g})"
        )
    if len(sections) == 1:
        notes.append(
            "one continuous section: no internal boundary and no repeat to compare"
        )
    if min_section > duration:
        notes.append(
            f"track ({duration:.2f}s) is shorter than min_section ({min_section:g}s)"
        )

    chorus_set = set(chorus_members)
    family_of: Dict[int, int] = {}
    for group_index, members in enumerate(groups):
        for member in members:
            family_of[member] = group_index
    first_of = {group_index: members[0] for group_index, members in enumerate(groups)}

    last = len(sections) - 1
    for section in sections:
        group_index = family_of[section.index]
        members = groups[group_index]
        if section.index in chorus_set:
            section.label = "chorus"
            section.confidence = chorus_confidence
            section.repeat_of = (
                None if section.index == first_of[group_index] else first_of[group_index]
            )
        elif len(sections) == 1:
            # A single continuous block is body, not a title card: call it a
            # verse and say it is unproven.
            section.label = "verse"
            section.confidence = "low"
        elif len(members) > 1:
            section.label = "verse"
            cosine = cosines.get(
                (min(section.index, first_of[group_index]), max(section.index, first_of[group_index])),
                1.0 if section.index == first_of[group_index] else 0.0,
            )
            section.confidence = "medium" if cosine >= 0.86 else "low"
            section.repeat_of = (
                None if section.index == first_of[group_index] else first_of[group_index]
            )
        elif section.index == 0:
            section.label = "intro"
            edge = sections[1].edge if len(sections) > 1 else 0.0
            section.confidence = "medium" if edge >= 0.25 else "low"
        elif section.index == last:
            section.label = "outro"
            section.confidence = "medium" if section.edge >= 0.25 else "low"
        else:
            neighbours = {
                sections[section.index - 1].index in chorus_set,
                sections[section.index + 1].index in chorus_set,
            }
            section.label = "bridge"
            section.confidence = "medium" if all(neighbours) else "low"

    if not chorus_members:
        for section in sections:
            section.confidence = "low"

    chorus_span: Optional[List[float]] = None
    if chorus_members:
        first = sections[min(chorus_members, key=lambda i: sections[i].t_in)]
        chorus_span = [round(first.t_in, 3), round(first.t_out, 3)]

    return {
        "schema": SCHEMA,
        "duration": round(duration, 3),
        "tempo_bpm": round(bpm, 1),
        "sections": [
            {
                "label": section.label,
                "t_in": round(section.t_in, 3),
                "t_out": round(section.t_out, 3),
                "energy": round(section.energy, 4),
                "repeat_of": section.repeat_of,
                "confidence": section.confidence,
            }
            for section in sections
        ],
        "chorus_span": chorus_span,
        "notes": notes,
    }
