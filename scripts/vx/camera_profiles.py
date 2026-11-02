"""vx.camera_profiles — pellicola e sensore come dati: grana, gate weave, halation.

Un profilo non e' un filtro: e' una scheda tecnica. Quattro parametri con dominio e
default (grana, tremolio del gate, alonatura, formato), e un insieme dichiarato di
rapporti in cui quel profilo si puo' girare. ``build`` restituisce la catena di
effetti EDL 3 che lo rende: la grana e l'alonatura sono effetti della famiglia
``film`` che esistono gia', il tremolio del gate e' un ``crop`` con keyframe
generati da un seme — quindi due giri con lo stesso seme danno gli stessi
fotogrammi, e un seme diverso da' un altro tremolio.

Regole imposte: un rapporto fuori dall'insieme dichiarato e' rifiutato con il
valore e l'elenco; un parametro fuori dominio e' rifiutato con il suo nome.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .contract import VxError
from .edl3 import Effect, Keyframe, KeyframeTrack


class ProfileError(VxError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Limits:
    """A declared numeric parameter: its domain and the value used if absent."""

    name: str
    lo: float
    hi: float
    default: float

    def contains(self, value: float) -> bool:
        return self.lo <= float(value) <= self.hi


@dataclass(frozen=True)
class CameraProfile:
    name: str
    grain: Limits
    weave: Limits
    halation: Limits
    ratios: Tuple[str, ...]
    why: str

    def limits(self) -> Dict[str, Limits]:
        return {"grain": self.grain, "weave": self.weave, "halation": self.halation}

    def problems(self) -> List[str]:
        found: List[str] = []
        if not self.ratios:
            found.append(f"{self.name}: no aspect ratio declared")
        for key, limit in self.limits().items():
            if not limit.contains(limit.default):
                found.append(
                    f"{self.name}.{key}: default {limit.default} outside "
                    f"[{limit.lo}, {limit.hi}]"
                )
            if limit.lo >= limit.hi:
                found.append(f"{self.name}.{key}: empty domain")
        return found


PROFILES: Dict[str, CameraProfile] = {
    "35mm": CameraProfile(
        name="35mm",
        grain=Limits("grain", 0.05, 0.45, 0.18),
        weave=Limits("weave", 0.0, 1.2, 0.35),
        halation=Limits("halation", 0.0, 0.6, 0.22),
        ratios=("16:9", "2.39:1", "4:5", "9:16", "1:1"),
        why="Pellicola cinematografica: grana fine, gate stabile, alonatura calda sulle alte luci.",
    ),
    "16mm": CameraProfile(
        name="16mm",
        grain=Limits("grain", 0.10, 0.60, 0.30),
        weave=Limits("weave", 0.0, 1.8, 0.60),
        halation=Limits("halation", 0.0, 0.7, 0.30),
        ratios=("16:9", "4:3", "4:5", "9:16"),
        why="Documentario su pellicola: piu' grana, gate meno fermo, formato quasi quadrato.",
    ),
    "8mm": CameraProfile(
        name="8mm",
        grain=Limits("grain", 0.25, 1.00, 0.62),
        weave=Limits("weave", 0.2, 3.0, 1.10),
        halation=Limits("halation", 0.10, 0.9, 0.45),
        ratios=("4:3", "1:1", "4:5", "9:16"),
        why="Super 8: grana grossa, tremolio visibile, alonatura larga: memoria, non nitidezza.",
    ),
    "dv": CameraProfile(
        name="dv",
        grain=Limits("grain", 0.0, 0.25, 0.08),
        weave=Limits("weave", 0.0, 0.6, 0.10),
        halation=Limits("halation", 0.0, 0.2, 0.04),
        ratios=("4:3", "16:9", "9:16"),
        why="Videocamera digitale a nastro: nessuna grana chimica, bordi duri, colori freddi.",
    ),
}

FAMILY = tuple(sorted(PROFILES))


def require(name: str) -> CameraProfile:
    profile = PROFILES.get(str(name))
    if profile is None:
        raise ProfileError(
            "PROFILE_UNKNOWN",
            f"unknown camera profile {name!r}; declared: {sorted(PROFILES)}",
        )
    return profile


def validate() -> List[str]:
    found: List[str] = []
    for name, profile in PROFILES.items():
        if name != profile.name:
            found.append(f"{name}: key and name disagree")
        found.extend(profile.problems())
    return found


def _values(
    profile: CameraProfile,
    grain: Optional[float],
    weave: Optional[float],
    halation: Optional[float],
) -> Tuple[float, float, float]:
    picked = (
        profile.grain.default if grain is None else float(grain),
        profile.weave.default if weave is None else float(weave),
        profile.halation.default if halation is None else float(halation),
    )
    for key, value in zip(("grain", "weave", "halation"), picked):
        limit = profile.limits()[key]
        if not limit.contains(value):
            raise ProfileError(
                "PROFILE_LIMIT",
                f"{profile.name}.{key} = {value} outside [{limit.lo}, {limit.hi}]",
            )
    return picked


def build(
    name: str,
    *,
    duration: float,
    width: int,
    height: int,
    ratio: Optional[str] = None,
    grain: Optional[float] = None,
    weave: Optional[float] = None,
    halation: Optional[float] = None,
    seed: int = 1717,
    beats: int = 4,
) -> List[Effect]:
    """The effect chain of the profile, in application order.

    The gate weave is a ``crop`` whose window is inset by the amplitude and jittered
    by ``beats`` control points drawn from ``random.Random(seed)``: the same seed
    gives the same track, so the same clip renders the same frames twice.
    """
    profile = require(name)
    if ratio is not None and ratio not in profile.ratios:
        raise ProfileError(
            "PROFILE_RATIO",
            f"{profile.name} cannot be framed for {ratio!r}; declared: "
            f"{list(profile.ratios)}",
        )
    seconds = float(duration)
    if seconds <= 0.0:
        raise ProfileError("PROFILE_DURATION", f"duration must be > 0, got {duration!r}")
    grain_value, weave_px, halation_value = _values(profile, grain, weave, halation)

    chain: List[Effect] = []
    if grain_value > 0.0:
        chain.append(Effect(kind="grain", params={"strength": grain_value}))

    if weave_px > 0.0:
        rng = random.Random(int(seed))
        step = seconds / max(1, int(beats))
        amplitude = (weave_px / max(1.0, float(width))) * 2.0
        base = amplitude
        x_keys = []
        y_keys = []
        for index in range(int(beats) + 1):
            t = min(seconds, index * step)
            x_keys.append(
                Keyframe(
                    t=t,
                    value=base + rng.uniform(-amplitude, amplitude) * 0.5,
                    interp="bezier",
                )
            )
            y_keys.append(
                Keyframe(
                    t=t,
                    value=base + rng.uniform(-amplitude, amplitude) * 0.5,
                    interp="bezier",
                )
            )
        chain.append(
            Effect(
                kind="crop",
                params={},
                keyframes={
                    "x": KeyframeTrack(x_keys),
                    "y": KeyframeTrack(y_keys),
                    "width": KeyframeTrack(
                        [Keyframe(t=0.0, value=1.0 - 2 * base, interp="hold")]
                    ),
                    "height": KeyframeTrack(
                        [Keyframe(t=0.0, value=1.0 - 2 * base, interp="hold")]
                    ),
                },
            )
        )

    if halation_value > 0.0:
        chain.append(
            Effect(
                kind="halation",
                params={
                    # The profile declares a strength in 0..1; the halation
                    # effect declares its knee in 0.7..1. The builder maps one
                    # to the other instead of widening the effect's domain.
                    "knee": 0.7 + 0.3 * halation_value,
                    "cool": 0.65 if profile.name == "dv" else 0.45,
                },
            )
        )
    return chain


def describe(name: str) -> Dict[str, object]:
    profile = require(name)
    return {
        "name": profile.name,
        "grain": [profile.grain.lo, profile.grain.hi, profile.grain.default],
        "weave": [profile.weave.lo, profile.weave.hi, profile.weave.default],
        "halation": [profile.halation.lo, profile.halation.hi, profile.halation.default],
        "ratios": list(profile.ratios),
        "why": profile.why,
    }


__all__ = [
    "CameraProfile",
    "FAMILY",
    "Limits",
    "PROFILES",
    "ProfileError",
    "build",
    "describe",
    "require",
    "validate",
]
