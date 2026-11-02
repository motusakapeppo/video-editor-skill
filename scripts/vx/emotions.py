"""vx.emotions — la ruota delle otto emozioni come tabella versionata.

Un'emozione, per un montatore, non e' un aggettivo: e' una configurazione. Qui
ogni voce dichiara **quattro assi** e nessuno e' facoltativo:

    rhythm   quante inquadrature al minuto e quanto dura ognuna
    grading  quale palette della libreria :mod:`vx.lut`, con che forza
    music    tempo in BPM, modo, energia
    sfx      densita' al minuto, eventi, gamma di guadagno

La tabella ha una versione (``WHEEL_VERSION``). La versione usata viene *citata*:
nel Creative Brief sotto ``emotion`` e nell'EDL sotto ``x-emotion``, che e' il
canale dei campi estesi — nessuna modifica allo schema, e il campo sopravvive al
round trip. Una voce a cui manca un asse e' rifiutata con ``EMOTION_AXIS`` e il
nome dell'asse: non si completa a indovinare.

``sheet()`` rende lo stesso fotogramma con le otto gradazioni e ne fa un foglio
4x2: serve a vedere la differenza fra due emozioni, non a descriverla.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import lut
from .contract import VxError
from . import ffmpeg as ff

WHEEL_VERSION = "vx.emotions/1"
EDL_KEY = "x-emotion"
AXIS_NAMES = ("rhythm", "grading", "music", "sfx")
SHEET_COLUMNS = 4
SHEET_ROWS = 2


class EmotionError(VxError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Emotion:
    name: str
    rhythm: Dict[str, Any]
    grading: Dict[str, Any]
    music: Dict[str, Any]
    sfx: Dict[str, Any]

    def axes(self) -> Dict[str, Dict[str, Any]]:
        return {
            "rhythm": self.rhythm,
            "grading": self.grading,
            "music": self.music,
            "sfx": self.sfx,
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "wheel": WHEEL_VERSION,
            "axes": self.axes(),
        }


def _range(value: Any) -> Optional[Tuple[float, float]]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            low, high = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
        return (low, high) if low <= high else None
    return None


def axis_problems(axis: str, value: Any) -> List[str]:
    """What is wrong with one axis, in the axis's own terms."""
    if axis not in AXIS_NAMES:
        return [f"unknown axis {axis!r}; declared: {list(AXIS_NAMES)}"]
    if not isinstance(value, Mapping):
        return [f"{axis}: expected an object, got {type(value).__name__}"]
    found: List[str] = []
    if axis == "rhythm":
        cuts = _range(value.get("cuts_per_min"))
        shot = _range(value.get("shot_s"))
        if cuts is None or cuts[0] <= 0.0:
            found.append("rhythm.cuts_per_min: expected a positive [low, high]")
        if shot is None or shot[0] <= 0.0:
            found.append("rhythm.shot_s: expected a positive [low, high]")
        if cuts and shot and cuts[0] > 0.0:
            # 60 / cuts is the longest shot that range can hold; a range whose
            # own two ends disagree with the shot range is a contradiction, not
            # a style.
            longest = 60.0 / cuts[0]
            if shot[1] > longest * 1.25:
                found.append(
                    f"rhythm: shot_s up to {shot[1]:.2f} exceeds {longest:.2f} s "
                    f"at {cuts[0]:.2f} cuts/min"
                )
    elif axis == "grading":
        palette = value.get("palette")
        if not isinstance(palette, str) or palette not in lut.PALETTES:
            found.append(f"grading.palette: {palette!r} is not in the LUT library")
        strength = value.get("strength", 1.0)
        if not isinstance(strength, (int, float)) or isinstance(strength, bool):
            found.append("grading.strength: expected a number")
        elif not 0.0 <= float(strength) <= 1.0:
            found.append(f"grading.strength: {strength} outside [0, 1]")
    elif axis == "music":
        bpm = _range(value.get("bpm"))
        if bpm is None or bpm[0] <= 0.0:
            found.append("music.bpm: expected a positive [low, high]")
        mode = value.get("mode")
        if mode not in ("maggiore", "minore", "modale"):
            found.append(f"music.mode: {mode!r} is not maggiore/minore/modale")
        energy = value.get("energy")
        if not isinstance(energy, (int, float)) or isinstance(energy, bool):
            found.append("music.energy: expected a number")
        elif not 0.0 <= float(energy) <= 1.0:
            found.append(f"music.energy: {energy} outside [0, 1]")
    else:
        density = value.get("density_per_min")
        if not isinstance(density, (int, float)) or isinstance(density, bool) or float(density) < 0.0:
            found.append("sfx.density_per_min: expected a non-negative number")
        events = value.get("events")
        if not isinstance(events, list) or not events or not all(isinstance(e, str) and e for e in events):
            found.append("sfx.events: expected a non-empty list of names")
        gain = _range(value.get("gain_db"))
        if gain is None or gain[1] > 0.0:
            found.append("sfx.gain_db: expected [low, high] with high <= 0")
    return found


EMOTIONS: Dict[str, Emotion] = {
    "gioia": Emotion(
        name="gioia",
        rhythm={"cuts_per_min": (14.0, 22.0), "shot_s": (1.4, 3.0)},
        grading={"palette": "social_estate", "strength": 1.0},
        music={"bpm": (110.0, 128.0), "mode": "maggiore", "energy": 0.8},
        sfx={"density_per_min": 6.0, "events": ["risata", "passi"], "gain_db": (-20.0, -14.0)},
    ),
    "tenerezza": Emotion(
        name="tenerezza",
        rhythm={"cuts_per_min": (5.0, 9.0), "shot_s": (4.0, 8.0)},
        grading={"palette": "edit_latte", "strength": 1.0},
        music={"bpm": (62.0, 76.0), "mode": "maggiore", "energy": 0.3},
        sfx={"density_per_min": 1.5, "events": ["respiro", "tessuto"], "gain_db": (-26.0, -18.0)},
    ),
    "nostalgia": Emotion(
        name="nostalgia",
        rhythm={"cuts_per_min": (6.0, 11.0), "shot_s": (3.0, 7.0)},
        grading={"palette": "vintage_istante", "strength": 1.0},
        music={"bpm": (66.0, 82.0), "mode": "minore", "energy": 0.35},
        sfx={"density_per_min": 2.0, "events": ["vinile", "vento"], "gain_db": (-26.0, -18.0)},
    ),
    "malinconia": Emotion(
        name="malinconia",
        rhythm={"cuts_per_min": (3.0, 7.0), "shot_s": (6.0, 12.0)},
        grading={"palette": "cinema_nebbia", "strength": 1.0},
        music={"bpm": (54.0, 70.0), "mode": "minore", "energy": 0.2},
        sfx={"density_per_min": 0.8, "events": ["pioggia", "passi lontani"], "gain_db": (-28.0, -20.0)},
    ),
    "tensione": Emotion(
        name="tensione",
        rhythm={"cuts_per_min": (18.0, 30.0), "shot_s": (1.0, 2.5)},
        grading={"palette": "cinema_acciaio", "strength": 1.0},
        music={"bpm": (100.0, 140.0), "mode": "modale", "energy": 0.7},
        sfx={"density_per_min": 9.0, "events": ["ticchettio", "fruscio"], "gain_db": (-22.0, -12.0)},
    ),
    "meraviglia": Emotion(
        name="meraviglia",
        rhythm={"cuts_per_min": (4.0, 8.0), "shot_s": (5.0, 10.0)},
        grading={"palette": "cinema_deserto", "strength": 1.0},
        music={"bpm": (70.0, 90.0), "mode": "maggiore", "energy": 0.5},
        sfx={"density_per_min": 1.0, "events": ["riverbero", "ali"], "gain_db": (-26.0, -16.0)},
    ),
    "rabbia": Emotion(
        name="rabbia",
        rhythm={"cuts_per_min": (26.0, 40.0), "shot_s": (0.6, 1.6)},
        grading={"palette": "social_contrasto", "strength": 1.0},
        music={"bpm": (140.0, 170.0), "mode": "minore", "energy": 0.95},
        sfx={"density_per_min": 12.0, "events": ["impatto", "urlo"], "gain_db": (-14.0, -6.0)},
    ),
    "calma": Emotion(
        name="calma",
        rhythm={"cuts_per_min": (2.0, 5.0), "shot_s": (8.0, 18.0)},
        grading={"palette": "natura_mare", "strength": 1.0},
        music={"bpm": (50.0, 64.0), "mode": "modale", "energy": 0.15},
        sfx={"density_per_min": 0.5, "events": ["onde", "vento"], "gain_db": (-30.0, -22.0)},
    ),
}

FAMILY = tuple(EMOTIONS)


def require(name: str) -> Emotion:
    emotion = EMOTIONS.get(str(name))
    if emotion is None:
        raise EmotionError(
            "EMOTION_UNKNOWN",
            f"unknown emotion {name!r}; the wheel declares {list(FAMILY)}",
        )
    return emotion


def problems(value: Mapping[str, Any]) -> List[str]:
    """Every axis problem of one entry; a missing axis is named, once."""
    if not isinstance(value, Mapping):
        return [f"entry is not an object, got {type(value).__name__}"]
    found: List[str] = []
    for axis in AXIS_NAMES:
        if axis not in value:
            found.append(f"{axis}: axis missing")
            continue
        found.extend(axis_problems(axis, value[axis]))
    unknown = sorted(set(value) - set(AXIS_NAMES) - {"name", "wheel"})
    if unknown:
        found.append(f"unknown axes: {unknown}")
    return found


def validate() -> List[str]:
    found: List[str] = []
    if len(EMOTIONS) != 8:
        found.append(f"{len(EMOTIONS)} emotions, the wheel declares 8")
    for name, emotion in EMOTIONS.items():
        if name != emotion.name:
            found.append(f"{name}: key and name disagree")
        found.extend(f"{name}: {problem}" for problem in problems(emotion.axes()))
        strength = float(emotion.grading.get("strength", 1.0))
        if strength < 1.0:
            found.append(
                f"{name}: grading strength {strength} would force the LUT fallback; "
                "an emotion sheet must render the real palette"
            )
    return found


def refuse(value: Mapping[str, Any], *, where: str = "entry") -> None:
    """Raise ``EMOTION_AXIS`` naming the first missing axis, if any."""
    if isinstance(value, Mapping):
        for axis in AXIS_NAMES:
            if axis not in value:
                raise EmotionError(
                    "EMOTION_AXIS",
                    f"{where}: axis {axis!r} missing; declared axes are {list(AXIS_NAMES)}",
                )
    found = problems(value)
    if found:
        raise EmotionError("EMOTION_INVALID", f"{where}: {'; '.join(found)}")


# --------------------------------------------------------------------------- #
# citazione: brief e EDL
# --------------------------------------------------------------------------- #
def citation(name: str) -> Dict[str, str]:
    emotion = require(name)
    return {"wheel": WHEEL_VERSION, "emotion": emotion.name}


def cite(brief: Dict[str, Any], name: str) -> Dict[str, Any]:
    """Write the citation into a Creative Brief (top-level ``emotion``)."""
    brief["emotion"] = citation(name)
    return brief


def citation_problems(value: Any) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, Mapping):
        return ["emotion: expected an object with wheel and emotion"]
    found: List[str] = []
    if value.get("wheel") != WHEEL_VERSION:
        found.append(f"emotion.wheel: {value.get('wheel')!r} is not {WHEEL_VERSION}")
    if value.get("emotion") not in EMOTIONS:
        found.append(f"emotion.emotion: {value.get('emotion')!r} is not on the wheel")
    return found


def apply_to_edl(timeline: Any, name: str) -> Any:
    """Carry the citation on a Timeline3 through its ``x-`` extension channel."""
    cite = citation(name)
    ext = dict(getattr(timeline, "ext", {}) or {})
    ext[EDL_KEY] = cite
    timeline.ext = ext
    return timeline


def from_edl(timeline: Any) -> Optional[Dict[str, str]]:
    value = (getattr(timeline, "ext", {}) or {}).get(EDL_KEY)
    return dict(value) if isinstance(value, Mapping) else None


# --------------------------------------------------------------------------- #
# foglio degli otto riquadri
# --------------------------------------------------------------------------- #
def _escape(path: Path) -> str:
    text = str(Path(path).resolve()).replace("\\", "/")
    return "'" + text.replace(":", "\\:") + "'"


def grade_filter(name: str, *, cache_dir: Path) -> str:
    plan = lut.grade(require(name).grading["palette"], cache_dir=cache_dir)
    if plan["fallback"]:
        raise EmotionError(
            "EMOTION_FALLBACK",
            f"{name}: the palette {plan['palette']} resolves to {plan['why']}",
        )
    return lut.filter_string(plan)


def sheet(
    clip: Path,
    destination: Path,
    *,
    cache_dir: Path,
    at: float = 0.5,
    tile: Tuple[int, int] = (144, 256),
) -> Path:
    """One frame per emotion, tiled 4x2, written as a single PNG.

    The same frame of the same clip: what changes between tiles is the grading,
    which is the point of the sheet. Returns the path.
    """
    from PIL import Image

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tiles: List[Path] = []
    for name in FAMILY:
        lut.ensure(require(name).grading["palette"], cache_dir)
        target = destination.with_name(f"{destination.stem}-{name}.png")
        proc = subprocess.run(
            [
                ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{float(at):.3f}", "-i", str(clip),
                "-vf", grade_filter(name, cache_dir=cache_dir),
                "-frames:v", "1", str(target),
            ],
            capture_output=True,
        )
        if proc.returncode != 0 or not target.is_file():
            raise EmotionError(
                "EMOTION_RENDER",
                f"{name}: {proc.stderr.decode('utf-8', 'replace')[-200:]}",
            )
        tiles.append(target)

    canvas = Image.new("RGB", (tile[0] * SHEET_COLUMNS, tile[1] * SHEET_ROWS))
    for index, path in enumerate(tiles):
        column, row = index % SHEET_COLUMNS, index // SHEET_COLUMNS
        with Image.open(path) as image:
            canvas.paste(image.convert("RGB"), (column * tile[0], row * tile[1]))
    canvas.save(destination)
    return destination


def sheet_manifest(names: Sequence[str], *, columns: int = SHEET_COLUMNS, rows: int = SHEET_ROWS) -> Dict[str, Any]:
    return {
        "wheel": WHEEL_VERSION,
        "columns": columns,
        "rows": rows,
        "tiles": list(names),
        "cells": [
            {"index": index, "column": index % columns, "row": index // columns, "emotion": name}
            for index, name in enumerate(names)
        ],
    }


__all__ = [
    "AXIS_NAMES",
    "EDL_KEY",
    "EMOTIONS",
    "Emotion",
    "EmotionError",
    "FAMILY",
    "SHEET_COLUMNS",
    "SHEET_ROWS",
    "WHEEL_VERSION",
    "apply_to_edl",
    "axis_problems",
    "citation",
    "citation_problems",
    "cite",
    "from_edl",
    "grade_filter",
    "problems",
    "refuse",
    "require",
    "sheet",
    "sheet_manifest",
    "validate",
]
