"""Corpus di riferimento per provare la skill su formati e condizioni diverse.

Sintetico e deterministico di proposito: niente download, niente licenze, stesso
byte in ogni build. I video stock veri servono a scoprire cose che non immaginiamo,
ma non possono essere la suite di regressione — cambierebbero sotto i piedi.

Ogni clip copre una condizione che ha gia' rotto qualcosa o che potrebbe:

  landscape_169   formato orizzontale, movimento lento
  vertical_916    verticale stretto, movimento veloce
  square_11       quadrato, immagine quasi ferma
  bright_45       alto contrasto alternato: il caso peggiore per la leggibilita'
  dark_916        quasi nero: il caso peggiore per il colore

Uso:
    python tests/stock_corpus/make_corpus.py [--force]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
FF = r"C:\Users\motus\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"

SPECS = [
    (
        "landscape_169",
        1920,
        1080,
        8.0,
        30.0,
        "testsrc2=size={w}x{h}:rate={fps}:duration={dur}",
    ),
    (
        "vertical_916",
        1080,
        1920,
        6.0,
        25.0,
        "testsrc2=size={w}x{h}:rate={fps}:duration={dur},rotate=0.35*t:c=none:ow=iw:oh=ih",
    ),
    (
        "square_11",
        1080,
        1080,
        7.0,
        24.0,
        "smptebars=size={w}x{h}:rate={fps}:duration={dur}",
    ),
    (
        "bright_45",
        1080,
        1350,
        5.0,
        25.0,
        "color=c=white:size={w}x{h}:rate={fps}:duration={dur}",
    ),
    (
        "dark_916",
        1080,
        1920,
        5.0,
        25.0,
        "color=c=0x0a0a0a:size={w}x{h}:rate={fps}:duration={dur}",
    ),
]


def build(name, w, h, dur, fps, src, *, force=False) -> Path:
    dst = OUT / f"{name}.mp4"
    if dst.is_file() and not force:
        print(f"  {dst.name:24} gia' presente")
        return dst
    vf = src.format(w=w, h=h, fps=fps, dur=dur)
    if name == "bright_45":
        vf = (
            f"color=c=white:size={w}x{h}:rate={fps}:duration={dur},"
            f"geq=lum='if(lt(mod(T,1),0.5),235,16)':cb=128:cr=128"
        )
    if name == "dark_916":
        vf = (
            f"color=c=0x0a0a0a:size={w}x{h}:rate={fps}:duration={dur},"
            f"noise=alls=6:allf=t"
        )
    audio = f"sine=frequency=440:sample_rate=48000:duration={dur}"
    cmd = [
        FF,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        vf,
        "-f",
        "lavfi",
        "-i",
        audio,
        "-shortest",
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(dst),
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        print(f"  {name}: FALLITO\n{proc.stderr[-400:]}")
        return dst
    print(
        f"  {dst.name:24} {w}x{h} @{fps:g} {dur:g}s  {dst.stat().st_size / 1e6:.1f} MB"
    )
    return dst


def main() -> int:
    force = "--force" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"corpus in {OUT}")
    for name, w, h, dur, fps, src in SPECS:
        build(name, w, h, dur, fps, src, force=force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
