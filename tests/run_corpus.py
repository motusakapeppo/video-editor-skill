"""Suite di riferimento: prova la skill su formati e condizioni diverse.

Ogni clip e' un caso limite scelto perche' ha gia' rotto qualcosa o puo' farlo.
Il runner non guarda l'estetica: guarda i numeri che i cancelli promettono.

  formato      il grafo xfade deve produrre esattamente i frame pianificati
  colore       il Gate 5 deve dare 0 su se stesso e rifiutare un grade alterato
  geometria    il Gate 4 universale deve passare e deve saper rifiutare

Uso:
    python tests/run_corpus.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import contract, gates, layout, motion, edl as edl_mod, ffmpeg as ff  # noqa: E402

FF = shutil.which("ffmpeg") or os.environ.get("VX_FFMPEG")
CORPUS = ROOT / "tests" / "stock_corpus"
WORK = ROOT / "tests" / "_out"
FPS = 25.0
SHIFT = 0.25
PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:38} {detail}")


def probe(path: Path) -> dict:
    from vx import motion as motion_mod

    frames, dur, fps = motion_mod.expected_frames(path)
    pr = ff.run_fp(
        [
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ]
    )
    st = json.loads(pr.stdout)["streams"][0]
    return {
        "w": int(st["width"]),
        "h": int(st["height"]),
        "fps": fps,
        "dur": dur,
        "frames": frames,
    }


def xfade_graph(
    shots, src: Path, *, w: int, h: int, idx: int, shift: float, fps: float
):
    """Mini catena: 3 spezzoni dalla stessa clip, con sovrapposizione a durata fissa."""
    parts = []
    planned = 0.0
    for i, (t0, dur) in enumerate(shots):
        last = i == len(shots) - 1
        ext = 0.0 if last else shift
        f0, f1 = int(round(t0 * fps)), int(round((t0 + dur + ext) * fps))
        parts.append(
            f"[{idx}:v]fps={fps:g},trim=start_frame={f0}:end_frame={f1},"
            f"setpts=PTS-STARTPTS,scale={w}:{h}:flags=bicubic,setsar=1[v{i}]"
        )
        parts.append(
            f"[{idx}:a]atrim=start={t0:.3f}:end={t0 + dur + ext:.3f},"
            f"asetpts=PTS-STARTPTS[a{i}]"
        )
        planned += dur
    prev_v, prev_a, cum = "v0", "a0", shots[0][1]
    for i in range(1, len(shots)):
        parts.append(
            f"[{prev_v}][v{i}]xfade=transition=fade:duration={shift}:offset={cum:.3f}[xv{i}]"
        )
        parts.append(f"[{prev_a}][a{i}]acrossfade=d={shift}:c1=tri:c2=tri[xa{i}]")
        prev_v, prev_a = f"xv{i}", f"xa{i}"
        cum += shots[i][1]
    return ";".join(parts), planned, (prev_v, prev_a)


def test_format(name: str, src: Path) -> None:
    info = probe(src)
    print(f"  {name}: {info['w']}x{info['h']} @{info['fps']:.3f} {info['dur']:.2f}s")
    check(
        "sonda leggibile", info["w"] > 0 and info["dur"] > 0, f"{info['w']}x{info['h']}"
    )

    span = info["dur"] - 0.3
    shot = min(1.4, span / 3.0)
    shots = [(0.1 + i * (shot + 0.1), shot) for i in range(3)]
    w = 480 if info["w"] >= info["h"] else 270
    h = int(round(w * info["h"] / info["w"] / 2) * 2)
    graph, planned, (vt, at) = xfade_graph(
        shots, src, w=w, h=h, idx=0, shift=SHIFT, fps=FPS
    )
    script = WORK / f"{name}_xfade.txt"
    script.write_text(graph, encoding="utf-8", newline="\n")
    out = WORK / f"{name}_xfade.mp4"
    proc = subprocess.run(
        [
            FF,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(src),
            "-filter_complex_script",
            str(script),
            "-map",
            f"[{vt}]",
            "-map",
            f"[{at}]",
            "-t",
            f"{planned:.3f}",
            "-c:v",
            "libx264",
            "-crf",
            "26",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        check("render xfade", False, proc.stderr[-200:])
        return
    got = probe(out)
    expected = round(planned * FPS)
    check(
        "frame = pianificato",
        abs(got["frames"] - expected) <= 1,
        f"{got['frames']} vs {expected}",
    )
    check(
        "durata = pianificata",
        abs(got["dur"] - planned) < 0.06,
        f"{got['dur']:.3f} vs {planned:.3f}",
    )

    try:
        gates.color(out, out)
        check("Gate 5 su se stesso", True, "delta 0")
    except contract.QCFailure:
        check("Gate 5 su se stesso", False, "dovrebbe dare 0")

    altered = WORK / f"{name}_altered.mp4"
    subprocess.run(
        [
            FF,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(out),
            "-vf",
            "eq=brightness=0.12:saturation=1.3,curves=m='0/0 0.5/0.62 1/1'",
            "-c:v",
            "libx264",
            "-crf",
            "26",
            "-preset",
            "veryfast",
            "-c:a",
            "copy",
            str(altered),
        ],
        capture_output=True,
    )
    try:
        gates.color(altered, out)
        check("Gate 5 rifiuta il grade alterato", False, "ha passato: non doveva")
    except contract.QCFailure as exc:
        fails = [c for c in exc.result.get("checks", []) if c["status"] == "fail"]
        check("Gate 5 rifiuta il grade alterato", True, f"{len(fails)} grandezze fuori")


def test_geometry() -> None:
    """Il Gate 4 universale deve funzionare a qualunque risoluzione.

    I box sono in frazioni del canvas: un test che scala i pixel del 3:4 su un 16:9
    misura l'errore del test, non del gate (successo davvero: il testo usciva dal
    canvas e la collisione cadeva fuori dalle due box).
    """
    for w, h, aspect in (
        (1080, 1920, "9:16"),
        (1920, 1080, "16:9"),
        (1080, 1080, "1:1"),
        (1080, 1350, "4:5"),
    ):
        lay = layout.Layout(
            canvas=layout.Rect(0, 0, w, h),
            graphic=None,
            padding=int(0.02 * h),
            margin_lr=int(0.055 * w),
        )

        def box(fx, fy, fw, fh):
            return layout.Rect(int(fx * w), int(fy * h), int(fw * w), int(fh * h))

        text = box(0.06, 0.15, 0.88, 0.30)  # 15%..45% di altezza
        badge_ok = box(0.075, 0.86, 0.17, 0.042)  # 86%..90%: libero
        badge_clash = box(0.10, 0.30, 0.17, 0.042)  # dentro la box del testo

        try:
            gates.layout_elements(lay, {"label": badge_ok}, text_box=text)
            check(f"Gate 4 {aspect} nessuna collisione", True, f"{w}x{h}")
        except contract.QCFailure as exc:
            codes = [c for c in exc.result.get("checks", []) if c["status"] == "fail"]
            check(f"Gate 4 {aspect} nessuna collisione", False, str(codes))

        try:
            gates.layout_elements(lay, {"label": badge_clash}, text_box=text)
            check(f"Gate 4 {aspect} rileva collisione", False, "non l'ha vista")
        except contract.QCFailure:
            check(f"Gate 4 {aspect} rileva collisione", True, "rifiutato")


def main() -> int:
    global WORK
    if not FF:
        print("usage: python tests/run_corpus.py")
        print("       ffmpeg not found: install it, add it to PATH, or set VX_FFMPEG")
        return 2
    WORK.mkdir(parents=True, exist_ok=True)
    clips = [(p.stem, p) for p in sorted(CORPUS.glob("*.mp4"))]
    clips += [(f"real_{p.stem}", p) for p in sorted((CORPUS / "real").glob("*.webm"))]
    print(f"corpus: {len(clips)} clip -> {', '.join(n for n, _ in clips)}\n")
    for name, path in clips:
        test_format(name, path)
        print()
    print("geometria su quattro formati")
    test_geometry()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
