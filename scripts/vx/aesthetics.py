"""vx.aesthetics — arricchimento visivo per-spezzone, dentro il grafo unificato.

Ordine nel grafo, e il motivo:

    crop (per-spezzone, dipende dal volto)  ->  scala  ->  duotono  ->  format/setsar

Il crop deve stare prima del concat perche' dipende dal soggetto di quel frammento; il
duotono e' identico per tutti ma applicarlo dopo lo scale costa un quarto dei pixel.

Vincoli duri imposti da `concat`:
    - width/height fissi e assoluti, identici per ogni segmento
    - pixel format identico (yuv420p) e SAR=1
    - x/y clippati dentro la sorgente: un crop fuori bordo produce frame neri o crash
    - coordinate pari: con 4:2:0 le dimensioni dispari corrompono la croma
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

OUT_W = 1080
OUT_H = 1440

# Gli occhi cadono al 25% dell'altezza del crop: la testa sta nel terzo superiore
# e la finestra scende di ~490 px rispetto al vecchio 0.42, recuperando busto e
# braccia. Misurato: con 0.42 quattro spezzoni su sette erano bloccati a y=0
# (nessuna liberta' verticale); con 0.25 si sbloccano.
EYE_LINE = 0.25
MODEL = (
    Path(__file__).resolve().parents[2]
    / "assets"
    / "models"
    / "face_detection_yunet_2023mar.onnx"
)

# Duotono V2 approvato, verificato àncora per àncora su rampa sintetica:
#
#     L_in  L_out   dL      sat     ruolo
#     0.25  0.238   -0.012  0.698   ancora protetta (intoccata)
#     0.50  0.503   +0.003  0.700   ancora protetta (intoccata)
#     0.75  0.775   +0.025  0.458   ancora
#     0.85  0.872   +0.022  0.270   lift solo-alte-luci
#     1.00  0.990   -0.010  0.031   bianco protetto
#
#     |dL|max = 0.025 su tutte le 21 tacche (contratto <= 0.060).
#
# Su cinque frame reali: neri 0.6%, saturazione mezzitoni 0.654, saturazione alte
# luci 0.354 (la bandiera resta bianca), p99 0.821.
#
# Le tre curve per canale sono RISOLTE, non scelte a occhio: dato il livello L e
# la saturazione s, con b = 1-s e g = G/R,
#
#     K = 0.299 + 0.587g + 0.114(1-s)      R = L/K      G = gR      B = (1-s)R
#
# cosi' la luma BT.601 vale L per costruzione. Il master e' identita' fino a 0.50
# (le due ancore protette) e alza solo sopra 0.85, che e' il lift approvato.
# `colorbalance` e' stato misurato e scartato: a L_in 0.50 restituisce
# 0.502/0.502/0.502, cioe' non vira i mezzitoni.
DUOTONE = (
    "curves=m='0/0 0.5/0.50 0.85/0.88 1/1',"
    "hue=s=0,"
    "curves=r='0/0.0400 0.25/0.4401 0.5/0.8989 0.75/0.9419 1/1.0000':"
    "g='0/0.0120 0.25/0.1761 0.5/0.3416 0.75/0.7064 1/0.9900':"
    "b='0/0.0120 0.25/0.1320 0.5/0.2697 0.75/0.4710 1/0.9694',"
    "noise=alls=4:allf=t,vignette=PI/9"
)


def _even(value: float) -> int:
    number = int(round(value))
    return number - (number % 2)


def window(
    src_w: int, src_h: int, *, out_w: int = OUT_W, out_h: int = OUT_H
) -> tuple[int, int]:
    """Largest even crop of the target aspect that fits inside the source."""
    target = out_w / out_h
    if src_w / src_h > target:
        width, height = src_h * target, src_h
    else:
        width, height = src_w, src_w / target
    return min(_even(width), _even(src_w)), min(_even(height), _even(src_h))


@dataclass
class Placement:
    crop_w: int
    crop_h: int
    x: int
    y: int
    face_found: bool
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "crop_w": self.crop_w,
            "crop_h": self.crop_h,
            "x": self.x,
            "y": self.y,
            "face_found": self.face_found,
            "reason": self.reason,
        }


def place(path: str | Path, at: float, *, src_w: int, src_h: int) -> Placement:
    """Crop origin so the eyes sit on the eye line, clamped and even."""
    crop_w, crop_h = window(src_w, src_h)
    max_x = max(0, src_w - crop_w)
    max_y = max(0, src_h - crop_h)
    centre_x, centre_y = max_x // 2, max_y // 2
    try:
        import cv2
    except Exception:  # noqa: BLE001
        return Placement(
            crop_w,
            crop_h,
            _even(centre_x),
            _even(centre_y),
            False,
            "opencv unavailable",
        )

    capture = cv2.VideoCapture(str(path))
    capture.set(cv2.CAP_PROP_POS_MSEC, at * 1000.0)
    ok, frame = capture.read()
    capture.release()
    if not ok or frame is None:
        return Placement(
            crop_w,
            crop_h,
            _even(centre_x),
            _even(centre_y),
            False,
            "frame not readable",
        )

    height, width = frame.shape[:2]
    detector = cv2.FaceDetectorYN.create(
        str(MODEL), "", (width, height), 0.6, 0.3, 5000
    )
    _, faces = detector.detect(frame)
    if faces is None or len(faces) == 0:
        return Placement(
            crop_w, crop_h, _even(centre_x), _even(centre_y), False, "no face: centred"
        )

    row = sorted(faces, key=lambda r: r[2] * r[3], reverse=True)[0]
    centre = float(row[0]) + float(row[2]) / 2.0
    eye = float(row[5])
    x = centre - crop_w / 2.0
    y = eye - EYE_LINE * crop_h
    x = max(0, min(max_x, x))
    y = max(0, min(max_y, y))
    return Placement(crop_w, crop_h, _even(x), _even(y), True, "face detected")


def chain(placement: Placement, *, out_w: int = OUT_W, out_h: int = OUT_H) -> str:
    """crop -> scale -> duotone -> yuv420p -> setsar, in this exact order."""
    return (
        f"crop={placement.crop_w}:{placement.crop_h}:{placement.x}:{placement.y},"
        f"scale={out_w}:{out_h}:flags=lanczos,"
        f"{DUOTONE},format=yuv420p,setsar=1"
    )


@dataclass
class MotionFit:
    """Quanto movimento contiene lo spezzone, e quanto ne puo' seguire il crop."""

    crop_w: int
    crop_h: int
    room_x: int
    room_y: int
    spread_x: float
    spread_y: float
    samples: int
    decision: str
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "crop_w": self.crop_w,
            "crop_h": self.crop_h,
            "room_x": self.room_x,
            "room_y": self.room_y,
            "spread_x": round(self.spread_x, 1),
            "spread_y": round(self.spread_y, 1),
            "samples": self.samples,
            "decision": self.decision,
            "note": self.note,
        }


def motion_fit(
    path: str | Path,
    t_in: float,
    t_out: float,
    *,
    src_w: int,
    src_h: int,
    samples: int = 5,
) -> MotionFit:
    """Misura l'escursione del soggetto e decide se il crop puo' seguirlo.

    Il crop ha liberta' solo se e' piu' piccolo della sorgente: `room_x` e
    `room_y` sono i pixel di pan disponibili. Se lo spostamento del soggetto
    eccede la stanza il pan non puo' compensare, e la decisione lo dichiara
    invece di fingere una correzione che non esiste.
    """
    crop_w, crop_h = window(src_w, src_h)
    room_x = max(0, int(src_w) - crop_w)
    room_y = max(0, int(src_h) - crop_h)
    span = max(1e-6, float(t_out) - float(t_in))
    stamps = [float(t_in) + span * i / max(1, samples - 1) for i in range(samples)]

    fit = MotionFit(crop_w, crop_h, room_x, room_y, 0.0, 0.0, 0, "static")
    try:
        import cv2
    except Exception:  # noqa: BLE001
        fit.note = "opencv unavailable: moto non misurato"
        return fit

    capture = cv2.VideoCapture(str(path))
    detector = None
    xs: list[float] = []
    ys: list[float] = []
    for at in stamps:
        capture.set(cv2.CAP_PROP_POS_MSEC, at * 1000.0)
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        height, width = frame.shape[:2]
        if detector is None:
            detector = cv2.FaceDetectorYN.create(
                str(MODEL), "", (width, height), 0.6, 0.3, 5000
            )
        _, faces = detector.detect(frame)
        if faces is None or len(faces) == 0:
            continue
        row = sorted(faces, key=lambda r: r[2] * r[3], reverse=True)[0]
        xs.append(float(row[0]) + float(row[2]) / 2.0)
        ys.append(float(row[5]))
    capture.release()

    fit.samples = len(xs)
    if len(xs) < 2:
        fit.note = "meno di 2 volti rilevati: moto non stimabile"
        return fit

    fit.spread_x = max(xs) - min(xs)
    fit.spread_y = max(ys) - min(ys)

    if room_x <= 0 and room_y <= 0:
        fit.decision = "static"
        fit.note = (
            "crop = intera sorgente: nessun pixel di pan disponibile. "
            "L'inquadratura e' quella della camera, non dell'edit"
        )
    elif fit.spread_x <= room_x and fit.spread_y <= room_y:
        fit.decision = "pan"
        fit.note = "il soggetto resta dentro la stanza di pan"
    else:
        fit.decision = "static"
        fit.note = (
            f"escursione {fit.spread_x:.0f}x{fit.spread_y:.0f} px supera la stanza "
            f"{room_x}x{room_y} px: il pan non puo' compensare, si tiene il centro"
        )
    return fit


def shot_graph(
    shot: Any,
    src_index: int,
    src_w: int,
    src_h: int,
    *,
    path: str | Path,
    fps: float = 25.0,
    out_w: int = OUT_W,
    out_h: int = OUT_H,
    index: int = 0,
) -> tuple[str, Placement]:
    """Video chain for one segment plus the matching audio window."""
    placement = place(
        path, (shot.src_in + shot.src_out) / 2.0, src_w=src_w, src_h=src_h
    )
    first = int(round(shot.src_in * fps))
    last = first + int(shot.frames)
    video = (
        f"[{src_index}:v]fps={int(round(fps))},"
        f"trim=start_frame={first}:end_frame={last},setpts=PTS-STARTPTS,"
        f"{chain(placement, out_w=out_w, out_h=out_h)}[v{index}]"
    )
    audio = (
        f"[{src_index}:a]aresample=48000,"
        f"atrim=start={first / fps:.6f}:end={last / fps:.6f},"
        f"asetpts=PTS-STARTPTS[a{index}]"
    )
    return video + ";" + audio, placement


def _selftest(src: Path, workdir: Path, ffmpeg: str) -> int:
    import json as _json
    import subprocess
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from vx import bpm, contract, edl as edl_mod, gates, grammar

    est = bpm.estimate_best(str(src), seconds=90)
    grid = grammar.beat_grid(str(src), max_seconds=200)
    intent = [{"label": "9928", "start": 97.0, "duration": 3.56}]
    edl = edl_mod.build(intent, {"9928": est}, {"9928": grid}, fps=25.0)
    shot = edl.shots[0]
    print(
        f"spezzone: {shot.src_in}->{shot.src_out} {shot.frames} frame [{shot.cut_on}]"
    )

    video_part, placement = shot_graph(shot, 0, 2160, 3840, path=src, index=0)
    print(
        f"crop: {placement.crop_w}x{placement.crop_h} @ {placement.x},{placement.y} "
        f"(volto: {placement.face_found})"
    )
    graph = video_part + f";[v0][a0]concat=n=1:v=1:a=1[v][a]"
    script = workdir / "shot_graph.txt"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(graph, encoding="utf-8", newline="\n")

    out = workdir / "shot_aesthetic.mp4"
    ff = ffmpeg
    proc = subprocess.run(
        [
            ff,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-filter_complex_script",
            str(script),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "medium",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(f"ffmpeg rc={proc.returncode}")
    if proc.returncode != 0:
        print(proc.stderr[-900:])
        return 1

    result = gates.post_render(
        contract.EditPlan(),
        {"fps": 25.0, "duration": 200.0},
        out,
        workdir=workdir,
        planned_total=shot.duration,
    )
    for check in result.checks:
        print(f"  [{check.status:7}] {check.code:24} {check.value} {check.detail}")
    print("esito:", "PASS" if result.ok else "FAIL")
    return 0 if result.ok else 1


if __name__ == "__main__":
    import argparse
    import os
    import shutil

    parser = argparse.ArgumentParser(description="vx.aesthetics self-test")
    parser.add_argument("--src", default=os.environ.get("VX_DEMO_SRC"))
    parser.add_argument("--workdir", default=os.environ.get("VX_DEMO_WORK"))
    parser.add_argument(
        "--ffmpeg",
        default=shutil.which("ffmpeg") or os.environ.get("VX_FFMPEG"),
    )
    args = parser.parse_args()
    if not args.src or not args.workdir or not args.ffmpeg:
        print(
            "usage: python -m vx.aesthetics --src <video> --workdir <dir> "
            "[--ffmpeg <path>]"
        )
        print("       (or set VX_DEMO_SRC, VX_DEMO_WORK, VX_FFMPEG)")
        raise SystemExit(2)
    raise SystemExit(_selftest(Path(args.src), Path(args.workdir), args.ffmpeg))
