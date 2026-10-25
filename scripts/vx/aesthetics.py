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
EYE_LINE = 0.42
MODEL = (
    Path(__file__).resolve().parents[2]
    / "assets"
    / "models"
    / "face_detection_yunet_2023mar.onnx"
)

DUOTONE = (
    "curves=all='0/0 0.35/0.20 0.72/0.88 1/1',"
    "hue=s=0,eq=contrast=1.08,"
    "curves=r='0/0.031 0.502/0.588 1/0.922':"
    "g='0/0.024 0.502/0.094 1/0.871':"
    "b='0/0.024 0.502/0.078 1/0.804',"
    "noise=alls=9:allf=t,vignette=PI/5"
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


def _selftest() -> int:
    import json as _json
    import subprocess
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from vx import bpm, contract, edl as edl_mod, gates, grammar

    src = Path(r"D:\Downloads\IMG_9928.MOV")
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
    script = Path(r"D:\video_work\vox\fase3\shot_graph.txt")
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(graph, encoding="utf-8", newline="\n")

    out = Path(r"D:\video_work\vox\fase3\shot_aesthetic.mp4")
    ff = r"C:\Users\motus\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
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
        workdir=Path(r"D:\video_work\vox\fase3"),
        planned_total=shot.duration,
    )
    for check in result.checks:
        print(f"  [{check.status:7}] {check.code:24} {check.value} {check.detail}")
    print("esito:", "PASS" if result.ok else "FAIL")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
