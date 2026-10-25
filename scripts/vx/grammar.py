"""vx.grammar — editing grammar as code: beat grid, cut placement, camera movement.

The cutter in `render.py` places cuts at fixed durations. That is a rough cut. A music
edit is cut to the music: the boundaries land on accents, and the shot length is derived
from the tempo, not chosen in advance.

This module answers three questions the renderer cannot:
    what is the tempo?          -> beat_grid()
    where should the cuts go?   -> snap_cuts()
    how does each shot move?    -> push_in()

Citations live in references/10-grammatica-del-montaggio.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import contract


@dataclass
class Grid:
    bpm: float = 0.0
    beats: List[float] = field(default_factory=list)
    onsets: List[float] = field(default_factory=list)
    duration: float = 0.0
    method: str = "none"
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bpm": round(self.bpm, 2),
            "beat_count": len(self.beats),
            "onset_count": len(self.onsets),
            "duration": round(self.duration, 3),
            "method": self.method,
            "confidence": round(self.confidence, 3),
            "beats": [round(b, 3) for b in self.beats[:64]],
            "onsets": [round(o, 3) for o in self.onsets[:64]],
        }


def beat_grid(path: str, *, max_seconds: float = 180.0) -> Grid:
    """Tempo, beat and onset times from the audio track (librosa, CPU, no GPU needed)."""
    try:
        import librosa
        import numpy as np
    except Exception as exc:  # noqa: BLE001 - optional stack
        return Grid(method=f"unavailable: {exc}")

    import subprocess
    import tempfile
    from pathlib import Path

    from . import ffmpeg as ff

    target = Path(tempfile.mkdtemp(prefix="vxgrid-")) / "audio.wav"
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
            str(max_seconds),
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        loglevel="error",
    )
    if not proc.ok:
        return Grid(method="decode failed")

    y, sr = librosa.load(str(target), sr=22050, mono=True)
    if y.size == 0:
        return Grid(method="empty audio")

    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beats = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    onset_frames = librosa.onset.onset_detect(
        y=y, sr=sr, units="frames", backtrack=True
    )
    onsets = librosa.frames_to_time(onset_frames, sr=sr).tolist()

    bpm = float(np.atleast_1d(tempo)[0]) if tempo is not None else 0.0
    duration = float(len(y)) / sr
    confidence = 0.0
    if bpm > 0 and len(beats) > 3:
        intervals = np.diff(beats)
        spread = float(np.std(intervals)) if intervals.size else 1.0
        confidence = float(max(0.0, min(1.0, 1.0 - spread / 0.25)))
    return Grid(
        bpm=bpm,
        beats=[float(b) for b in beats],
        onsets=[float(o) for o in onsets],
        duration=duration,
        method="librosa-beat+onset",
        confidence=confidence,
    )


PERCEPTUAL_TOLERANCE_FRAMES = 1


def frame_tolerance(fps: float) -> float:
    """±1 frame. At 25 fps that is 40 ms, the edge of perceptible asynchrony.

    A 300 ms tolerance (7 frames at 25 fps) does not put a cut "on the beat", it puts
    it near the beat — which reads as sloppy rather than rhythmic.
    """
    return PERCEPTUAL_TOLERANCE_FRAMES / max(float(fps), 1.0)


def dissolve_limit(a_len: float, b_len: float, requested: float) -> Dict[str, Any]:
    """A dissolve longer than a quarter of the shorter shot eats the shot."""
    ceiling = 0.25 * min(float(a_len), float(b_len))
    return {
        "requested": round(float(requested), 3),
        "max": round(ceiling, 3),
        "ok": float(requested) <= ceiling,
        "why": "dissolve <= 25% of the shorter adjacent shot",
    }


def snap_cuts(
    shots: List[Dict[str, Any]],
    grids: Dict[str, Grid],
    *,
    min_len: float,
    max_len: float,
    tolerance: Optional[float] = None,
    fps: float = 25.0,
) -> List[Dict[str, Any]]:
    """Retime each shot so its out-point lands on an accent *of its own source clip*.

    The out-point must be a beat/onset of the music actually playing under that shot,
    which means the grid has to come from that clip, in that clip's time. Passing one
    grid for the whole timeline would align cuts to accents that are not sounding.
    """
    tol = frame_tolerance(fps) if tolerance is None else float(tolerance)
    out: List[Dict[str, Any]] = []
    cursor = 0.0
    for shot in shots:
        label = str(shot.get("label", ""))
        grid = grids.get(label)
        start = float(shot.get("start", 0.0))
        wanted = float(shot.get("duration", (min_len + max_len) / 2))
        ideal = start + max(min_len, min(max_len, wanted))
        accents = sorted(
            {
                round(b, 3)
                for b in ((grid.beats if grid else []) + (grid.onsets if grid else []))
            }
        )
        candidates = [
            a
            for a in accents
            if min_len <= a - start <= max_len and abs(a - ideal) <= tol
        ]
        if candidates:
            boundary = min(candidates, key=lambda a: abs(a - ideal))
            reason = "beat"
        else:
            boundary = ideal
            reason = "no-accent-in-window"
        out.append(
            {
                "label": label,
                "start": round(start, 3),
                "duration": round(boundary - start, 3),
                "cut_at": round(cursor + (boundary - start), 3),
                "cut_on": reason,
            }
        )
        cursor += boundary - start
    return out


def push_in(
    duration: float,
    *,
    amount: float = 0.06,
    out_w: int = 1080,
    out_h: int = 1440,
    fps: int = 25,
) -> str:
    """Slow push-in for one shot, per guide 5.3 (1.00 -> 1.06 over the shot).

    zoompan rather than animated scale: crop w/h are evaluated once, and scale cannot
    animate size on a timeline. Kept to 6% so it reads as breath, not as a zoom effect.
    """
    frames = max(2, int(round(duration * fps)))
    return (
        f"zoompan=z='min({1 + amount:.3f},1+{amount:.4f}*on/{frames})':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={out_w}x{out_h}:fps={fps}"
    )


TRANSITIONS = {
    "cut": {"allowed": True, "why": "default grammar; carries rhythm, costs no frames"},
    "dissolve": {
        "allowed": True,
        "why": "time passing or location change; 0.4-0.6s on a 3-4s shot",
    },
    "fadeblack": {"allowed": True, "why": "only to open/close a block, 0.6-1.0s"},
    "wipe": {
        "allowed": False,
        "why": "decorative; guide 5.4 forbids visible transitions",
    },
    "slide": {"allowed": False, "why": "decorative"},
    "glitch": {"allowed": False, "why": "decorative, dates the edit"},
    "zoom_effect": {"allowed": False, "why": "decorative (a slow push-in is not this)"},
}


def transition_policy(name: str) -> Dict[str, Any]:
    return TRANSITIONS.get(name, {"allowed": False, "why": "not in the allowed set"})


def tempo_agreement(
    grids: Dict[str, Grid], *, tolerance: float = 0.04
) -> Dict[str, Any]:
    """Do the sources agree on a tempo? Beat trackers are confident even when wrong.

    On a live recording the tracker often latches onto the vocal or onto half/double
    time. Three clips of the same performance must share a tempo (or a 2:1 relative),
    otherwise the grid is untrustworthy and cutting to it is cutting to noise.
    """
    bpms = [round(g.bpm, 2) for g in grids.values() if g.bpm > 0]
    if len(bpms) < 2:
        return {
            "trustworthy": True,
            "reason": "single source",
            "bpms": bpms,
            "ratios": [],
        }
    reference = sorted(bpms)[len(bpms) // 2]
    ratios = [round(b / reference, 3) for b in bpms]
    allowed = (0.5, 1.0, 2.0)
    ok = all(any(abs(r - a) <= tolerance for a in allowed) for r in ratios)
    return {
        "trustworthy": ok,
        "reason": "tempi agree"
        if ok
        else "tempi disagree: tracker locked onto different metrical levels",
        "bpms": bpms,
        "reference": reference,
        "ratios": ratios,
    }


def cut_mode(grids: Dict[str, Grid], *, manual_bpm: float = 0.0) -> Dict[str, Any]:
    """How to place cuts on this material: beats, onsets, or ask a human."""
    if manual_bpm > 0:
        return {
            "mode": "beats",
            "source": "manual",
            "bpm": manual_bpm,
            "reason": "tempo supplied by the operator: always preferred over estimation",
        }
    agreement = tempo_agreement(grids)
    if agreement["trustworthy"]:
        return {
            "mode": "beats",
            "source": "detected",
            "bpm": agreement.get("reference", 0.0),
            "reason": agreement["reason"],
        }
    return {
        "mode": "onsets",
        "source": "fallback",
        "bpm": 0.0,
        "reason": agreement["reason"]
        + "; cutting to attacks only, or ask for the track BPM",
    }


def validate_edit(shots: List[Dict[str, Any]], *, min_len: float = 1.0) -> List[str]:
    problems: List[str] = []
    if len(shots) < 2:
        problems.append("an edit needs at least two shots")
    for index, shot in enumerate(shots):
        if float(shot.get("duration", 0)) < min_len:
            problems.append(f"shot {index} shorter than {min_len}s")
        if (
            index
            and abs(
                float(shot.get("start", 0)) - float(shots[index - 1].get("start", 0))
            )
            < 0.01
        ):
            problems.append(f"shot {index} reuses the previous source in-point")
    return problems


def bar_positions(grid: Grid, beats_per_bar: int = 4) -> List[float]:
    """Bar lines: every Nth beat, anchored on the first detected beat."""
    if not grid.beats:
        return []
    return [round(b, 3) for b in grid.beats[::beats_per_bar]]


def plan_multisource(
    segments: List[Dict[str, Any]],
    grids: Dict[str, Grid],
    *,
    min_len: float,
    max_len: float,
    beats_per_bar: int = 4,
    manual_bpm: float = 0.0,
) -> Dict[str, Any]:
    """Cut plan across sources with different tempos, joining on bar lines.

    With one audio take per shot (lip-sync), the shot boundary *is* the audio boundary,
    so the only rhythmic freedom is the shot length. This treats the requested duration
    as a preference and lets the music choose inside [min_len, max_len]: bar line first,
    then beat, then onset, recording the cost in seconds. Different tempi are acceptable
    as long as each join lands on its own source's bar — the tempo change then reads as
    musical rather than accidental.
    """
    shots: List[Dict[str, Any]] = []
    cursor = 0.0
    for segment in segments:
        label = str(segment.get("label", ""))
        start = float(segment.get("start", 0.0))
        wanted = float(segment.get("duration", (min_len + max_len) / 2))
        ideal = start + max(min_len, min(max_len, wanted))
        grid = grids.get(label)
        if manual_bpm > 0:
            anchor = min(grid.onsets) if (grid and grid.onsets) else start
            step = 60.0 / manual_bpm
            n = int(max(0.0, (start + max_len + 1.0 - anchor)) / step) + 1
            grid = Grid(
                bpm=manual_bpm,
                beats=[round(anchor + i * step, 4) for i in range(n)],
                onsets=[],
                duration=grid.duration if grid else 0.0,
                method="manual",
                confidence=1.0,
            )

        chosen: Optional[float] = None
        how = "unsnapped"
        if grid is not None:
            for name, pool in (
                ("bar", bar_positions(grid, beats_per_bar)),
                ("beat", list(grid.beats)),
                ("onset", list(grid.onsets)),
            ):
                inside = [a for a in pool if min_len <= a - start <= max_len]
                if inside:
                    chosen = min(inside, key=lambda a: abs(a - ideal))
                    how = name
                    break

        boundary = chosen if chosen is not None else ideal
        shots.append(
            {
                "label": label,
                "start": round(start, 3),
                "duration": round(boundary - start, 3),
                "cut_at": round(cursor + (boundary - start), 3),
                "cut_on": how,
                "ideal": round(wanted, 3),
                "cost": round(abs(boundary - ideal), 3),
                "tempo": round(grid.bpm, 1) if grid else 0.0,
            }
        )
        cursor += boundary - start

    tempi = sorted({s["tempo"] for s in shots if s["tempo"] > 0})
    spread = round((max(tempi) / min(tempi) - 1) * 100, 1) if len(tempi) > 1 else 0.0
    return {
        "shots": shots,
        "total": round(cursor, 3),
        "tempi": tempi,
        "tempo_spread_pct": spread,
        "all_on_music": all(s["cut_on"] != "unsnapped" for s in shots),
        "warning": (
            "tempi diversi: ogni giunzione e' su una battuta del proprio brano, "
            f"il cambio di tempo e' dichiarato ({spread}% di scarto)"
            if spread > 5
            else ""
        ),
    }
