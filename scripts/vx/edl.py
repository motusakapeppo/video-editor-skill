"""vx.edl — Edit Decision List per-clip, frame-exact, con regola dell'inazione.

Legge del sistema: il montaggio ritmico e' un'operazione PER-CLIP. Un clip con contrasto
>= 1.35 ha una griglia inattaccabile e viene tagliato sui propri transienti; un clip sotto
soglia resta a durata richiesta, il taglio ritmico e' VIETATO e il fatto viene registrato.
Il disallineamento globale fra clip (session spread) non blocca nulla: non ha un'origine
temporale comune, quindi non e' una misura.

L'EDL e' anche il contratto verso il renderer: contiene in/out per sorgente, posizione
nella timeline, durata e numero di frame, e si valida con il Gate 1 prima di esistere.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from . import contract
from . import gates
from .contract import DEFAULT_THRESHOLDS

CONTRAST_FLOOR = 1.35
SNAP_TOLERANCE_FRAMES = 1


@dataclass
class EdlShot:
    label: str
    src_in: float
    src_out: float
    duration: float
    t_in: float
    t_out: float
    frames: int
    cut_on: str
    contrast: float
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Edl:
    fps: float
    shots: List[EdlShot] = field(default_factory=list)
    total: float = 0.0
    frames: int = 0
    rhythmic_shots: int = 0
    inaction_shots: int = 0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fps": self.fps,
            "total": round(self.total, 3),
            "frames": self.frames,
            "rhythmic_shots": self.rhythmic_shots,
            "inaction_shots": self.inaction_shots,
            "notes": self.notes,
            "shots": [s.to_dict() for s in self.shots],
        }

    def as_cuts(self) -> List[Dict[str, float]]:
        """Ranges consumable by plan.cuts / render (in source time, per label)."""
        return [
            {
                "label": s.label,
                "in": s.src_in,
                "out": s.src_out,
                "t_in": round(s.t_in, 3),
                "t_out": round(s.t_out, 3),
            }
            for s in self.shots
        ]


def _accents(grid, beats_per_bar: int) -> List[tuple[str, float]]:
    bars = grid.beats[::beats_per_bar] if grid and grid.beats else []
    pool: List[tuple[str, float]] = [("bar", round(b, 3)) for b in bars]
    if grid:
        pool += [("beat", round(b, 3)) for b in grid.beats]
        pool += [("onset", round(o, 3)) for o in grid.onsets]
    return pool


def build(
    intents: List[Dict[str, Any]],
    estimates: Dict[str, Any],
    grids: Dict[str, Any],
    *,
    fps: float,
    min_len: float = 2.6,
    max_len: float = 4.4,
    beats_per_bar: int = 4,
) -> Edl:
    """Turn shot intents into a frame-exact EDL, honouring per-clip confidence."""
    edl = Edl(fps=fps)
    cursor = 0.0
    tolerance = SNAP_TOLERANCE_FRAMES / max(fps, 1.0)

    for intent in intents:
        label = str(intent.get("label", ""))
        src_in = float(intent.get("start", 0.0))
        wanted = float(intent.get("duration", (min_len + max_len) / 2))
        estimate = estimates.get(label)
        contrast = float(getattr(estimate, "contrast", 0.0) or 0.0)
        confident = (
            bool(getattr(estimate, "confident", False)) and contrast >= CONTRAST_FLOOR
        )

        if not confident:
            duration = wanted
            cut_on = "inaction"
            note = (
                f"contrasto {contrast:.2f} < {CONTRAST_FLOOR}: taglio ritmico vietato, "
                "durata richiesta mantenuta"
            )
            edl.inaction_shots += 1
            edl.notes.append(f"{label}: {note}")
        else:
            ideal = src_in + wanted
            pool = _accents(grids.get(label), beats_per_bar)
            window = [(kind, a) for kind, a in pool if min_len <= a - src_in <= max_len]
            chosen: Optional[tuple[str, float, bool]] = None
            for kind in ("bar", "beat", "onset"):
                typed = [a for k, a in window if k == kind]
                if not typed:
                    continue
                nearest = min(typed, key=lambda a: abs(a - ideal))
                within = abs(nearest - ideal) <= tolerance
                if within or kind == "onset":
                    chosen = (kind, nearest, within)
                    break
            if chosen is None:
                boundary = ideal
                cut_on = "unsnapped"
                note = "nessun accento nella finestra: durata richiesta"
            else:
                cut_on, boundary, within = chosen
                note = (
                    ""
                    if within
                    else f"accento {cut_on} a {abs(boundary - ideal):.3f}s dall'ideale"
                )
            duration = boundary - src_in
            edl.rhythmic_shots += 1

        frames = int(round(duration * fps))
        edl.shots.append(
            EdlShot(
                label=label,
                src_in=round(src_in, 3),
                src_out=round(src_in + duration, 3),
                duration=round(duration, 3),
                t_in=round(cursor, 3),
                t_out=round(cursor + duration, 3),
                frames=frames,
                cut_on=cut_on,
                contrast=round(contrast, 3),
                note=note,
            )
        )
        cursor += duration

    edl.total = round(cursor, 3)
    edl.frames = sum(s.frames for s in edl.shots)
    expected = int(round(edl.total * fps))
    if edl.frames != expected:
        edl.notes.append(
            f"aritmetica frame: {edl.frames} sommati vs {expected} attesi (scarto {edl.frames - expected})"
        )
    return edl


def validate(edl: Edl, media: Dict[str, Any]) -> Dict[str, Any]:
    """The EDL must survive Gate 1 before anything is rendered."""
    plan = contract.EditPlan(
        cuts=[{"in": s.src_in, "out": s.src_out} for s in edl.shots]
    )
    try:
        result = gates.pre_render(plan, media)
        return {"ok": True, "gate": result.to_dict()}
    except contract.QCFailure as exc:
        return {"ok": False, "gate": exc.result}


def _selftest() -> int:
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    from vx import bpm, grammar

    srcs = {
        "9928": r"D:\Downloads\IMG_9928.MOV",
        "9929": r"D:\Downloads\IMG_9929.MOV",
        "9930": r"D:\Downloads\IMG_9930.MOV",
    }
    estimates, grids = {}, {}
    for key, path in srcs.items():
        estimates[key] = bpm.estimate_best(path, seconds=120)
        grids[key] = grammar.beat_grid(path, max_seconds=200)
        print(
            f"  {key}: {estimates[key].bpm:7.2f} BPM  contrasto {estimates[key].contrast:5.2f}"
        )

    intents = [
        {"label": "9929", "start": 16.0, "duration": 3.0},
        {"label": "9928", "start": 97.0, "duration": 3.56},
        {"label": "9930", "start": 29.0, "duration": 2.67},
        {"label": "9928", "start": 48.0, "duration": 4.19},
        {"label": "9929", "start": 100.0, "duration": 2.72},
        {"label": "9930", "start": 65.0, "duration": 3.88},
        {"label": "9928", "start": 81.0, "duration": 2.81},
    ]
    edl = build(intents, estimates, grids, fps=25.0)
    print(
        f"\nEDL: {len(edl.shots)} spezzoni | totale {edl.total}s | {edl.frames} frame"
    )
    for shot in edl.shots:
        print(
            f"  {shot.label} {shot.src_in:7.2f}->{shot.src_out:7.2f} "
            f"{shot.duration:5.2f}s [{shot.cut_on:8}] c{shot.contrast:.2f} {shot.note}"
        )
    print(
        f"ritmici {edl.rhythmic_shots} | inazione {edl.inaction_shots} | frame attesi {round(edl.total * 25)}"
    )

    media = {"fps": 25.0, "duration": 200.0, "has_audio": True}
    check = validate(edl, media)
    print(
        f"Gate 1: {'PASS' if check['ok'] else 'FAIL ' + str(check['gate'].get('failures'))}"
    )

    weak = {"label": "9928", "start": 97.0, "duration": 3.56}
    fake = dict(estimates)
    fake["9928"] = bpm.Estimate(bpm=estimates["9928"].bpm, contrast=1.10)
    weak_edl = build([weak], fake, grids, fps=25.0)
    ok = weak_edl.inaction_shots == 1 and weak_edl.shots[0].cut_on == "inaction"
    print(
        f"inazione su contrasto 1.10: {'PASS' if ok else 'FAIL'} -> {weak_edl.shots[0].note}"
    )

    exact = edl.frames == int(round(edl.total * 25))
    print(f"aritmetica frame esatta: {'PASS' if exact else 'FAIL'}")
    return 0 if (check["ok"] and ok and exact) else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
