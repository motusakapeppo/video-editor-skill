"""vx.visual — veto grammaticale: non si taglia dentro un movimento di macchina.

Un taglio ritmico perfetto sul beat, piazzato nel mezzo di una panoramica veloce, si legge
come un glitch: l'occhio sta seguendo un movimento e il taglio glielo spezza. La musica
cede alla continuita' visiva.

Il costo non deve esplodere: non serve analizzare il clip intero, serve sapere cosa accade
nell'intorno del taglio. Si campiona una finestra di +/-`radius` secondi e si misurano due
grandezze:

    moto      = differenza media assoluta fra frame consecutivi (0..255)
    nitidezza = varianza del laplaciano (alta = dettaglio, bassa = mosso/sfuocato)

Whip-pan e motion blur hanno la firma **moto alto + nitidezza bassa**. Se il segnale e'
ambiguo (moto medio, nitidezza media) la regola dell'inazione vieta il veto: non si
sacrifica un accento musicale su un sospetto.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List

MOTION_HIGH = 9.0
SHARPNESS_LOW = 45.0
FRAME_WIDTH = 320


@dataclass
class Veto:
    vetoed: bool = False
    motion: float = 0.0
    sharpness: float = 0.0
    samples: int = 0
    reason: str = ""
    decisive: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def inspect_cut(path: str | Path, at: float, *, radius: float = 0.25) -> Veto:
    """Measure motion and sharpness around `at`; veto only when the signature is clear."""
    try:
        import cv2
        import numpy as np
    except Exception as exc:  # noqa: BLE001
        return Veto(reason=f"opencv unavailable: {exc}"[:120])

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return Veto(reason="cannot open the clip")

    start = max(0.0, at - radius)
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
    previous = None
    motions: List[float] = []
    sharpness: List[float] = []
    samples = 0
    while samples < 40:
        ok, frame = cap.read()
        if not ok:
            break
        if cap.get(cv2.CAP_PROP_POS_MSEC) > (at + radius) * 1000.0:
            break
        height = max(1, int(frame.shape[0] * FRAME_WIDTH / frame.shape[1]))
        small = cv2.resize(frame, (FRAME_WIDTH, height))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        sharpness.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
        if previous is not None:
            motions.append(
                float(
                    np.mean(np.abs(gray.astype(np.int16) - previous.astype(np.int16)))
                )
            )
        previous = gray
        samples += 1
    cap.release()

    if samples < 4 or not motions:
        return Veto(reason="not enough frames in the window", samples=samples)

    motion = float(np.mean(motions))
    sharp = float(np.median(sharpness))
    moving = motion >= MOTION_HIGH
    blurred = sharp <= SHARPNESS_LOW
    if moving and blurred:
        return Veto(
            True,
            round(motion, 2),
            round(sharp, 1),
            samples,
            "moto alto con nitidezza bassa: whip-pan o motion blur",
            True,
        )
    if not moving and not blurred:
        return Veto(
            False,
            round(motion, 2),
            round(sharp, 1),
            samples,
            "inquadratura ferma e nitida",
            True,
        )
    return Veto(
        False,
        round(motion, 2),
        round(sharp, 1),
        samples,
        "segnale ambiguo: la regola dell'inazione vieta il veto",
        False,
    )


def veto_fn_for(paths: Dict[str, str], *, radius: float = 0.25):
    """Build a callable (label, time) -> bool for `edl.build`."""
    cache: Dict[tuple[str, float], Veto] = {}

    def veto(label: str, at: float) -> bool:
        key = (label, round(at, 2))
        if key not in cache:
            cache[key] = inspect_cut(paths[label], at, radius=radius)
        return cache[key].vetoed

    veto.report = cache  # type: ignore[attr-defined]
    return veto


def _selftest() -> int:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from vx import bpm, edl as edl_mod, grammar

    srcs = {
        "9928": r"D:\Downloads\IMG_9928.MOV",
        "9929": r"D:\Downloads\IMG_9929.MOV",
        "9930": r"D:\Downloads\IMG_9930.MOV",
    }
    estimates = {k: bpm.estimate_best(p, seconds=90) for k, p in srcs.items()}
    grids = {k: grammar.beat_grid(p, max_seconds=200) for k, p in srcs.items()}
    intents = [
        {"label": "9929", "start": 16.0, "duration": 3.0},
        {"label": "9928", "start": 97.0, "duration": 3.56},
        {"label": "9930", "start": 29.0, "duration": 2.67},
        {"label": "9928", "start": 48.0, "duration": 4.19},
        {"label": "9929", "start": 100.0, "duration": 2.72},
        {"label": "9930", "start": 65.0, "duration": 3.88},
        {"label": "9928", "start": 81.0, "duration": 2.81},
    ]
    print("== veto sui punti di taglio, senza vincolo ==")
    plain = edl_mod.build(intents, estimates, grids, fps=25.0)
    veto = veto_fn_for(srcs)
    for shot in plain.shots:
        verdict = inspect_cut(srcs[shot.label], shot.src_out)
        print(
            f"  {shot.label} cut {shot.src_out:7.2f} [{shot.cut_on:4}] "
            f"moto {verdict.motion:6.2f} nitidezza {verdict.sharpness:7.1f} "
            f"-> {'VETO' if verdict.vetoed else 'ok'} ({verdict.reason})"
        )

    print("\n== EDL con veto attivo ==")
    guarded = edl_mod.build(intents, estimates, grids, fps=25.0, veto_fn=veto)
    for shot in guarded.shots:
        print(
            f"  {shot.label} {shot.src_in:7.2f}->{shot.src_out:7.2f} {shot.duration:5.2f}s "
            f"{shot.frames:3d}f [{shot.cut_on}] {shot.note}"
        )
    print(f"totale {guarded.total}s / {guarded.frames} frame")

    vetoed = sum(1 for s in guarded.shots if s.cut_on == "visual_veto")
    moved = sum(
        1
        for a, b in zip(plain.shots, guarded.shots)
        if abs(a.src_out - b.src_out) > 1e-6
    )
    print(f"\ntagli spostati dal veto: {moved} | marcati visual_veto: {vetoed}")
    ok = guarded.frames == int(round(guarded.total * 25))
    print(f"aritmetica frame: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
