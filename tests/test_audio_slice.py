"""La fetta continua di un brano e il suo punto di giunzione.

Il caso: un loop di 50,88 s con un salto di 4,2 volte il movimento normale del
segnale -- un clic a ogni giro. Sei millisecondi di dissolvenza lo portano a zero.

S1 - la fetta dura quello che dichiara, campione-accurata.
S2 - le dissolvenze chiudono il salto e il report lo dice con il numero.
S3 - senza dissolvenze il salto si misura lo stesso: non si finge continuita'.
S4 - argomenti impossibili si rifiutano prima di scrivere un file.
S5 - il comando esce 5 con --loop-safe quando la giunzione si sente.
"""

from __future__ import annotations

import math
import subprocess
import sys
import tempfile
import wave
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import audio_slice  # noqa: E402
from vx.ffmpeg import ffmpeg  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def write_tone(path: Path, seconds: float, frequency: float = 440.0, rate: int = 48000) -> Path:
    """Un seno PCM16 mono: campionabile, e con una fase misurabile."""
    frames = int(round(seconds * rate))
    values = array("h")
    for index in range(frames):
        values.append(int(round(32767 * math.sin(2 * math.pi * frequency * index / rate))))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(values.tobytes())
    return path


def write_jump(path: Path, rate: int = 8000) -> Path:
    """Una rampa da +0,9 a -0,9: si muove piano, ma il giro salta di 1,8."""
    frames = 400
    values = array("h")
    for index in range(frames):
        position = 1.0 - 2.0 * index / float(frames - 1)
        values.append(int(round(29490 * position)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(values.tobytes())
    return path


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-slice-") as raw:
        work = Path(raw)
        source = write_tone(work / "song.wav", 3.0)

        # -- S1: la fetta --------------------------------------------------- #
        result = audio_slice.slice_audio(source, 0.5, 2.5, work / "loop.wav")
        check("S1 la fetta dura 2,000 s", abs(result["duration"] - 2.0) < 1e-6, result["duration"])
        measured = result["seam"]
        check(
            "S1 la fetta e' campione-accurata",
            measured["samples"] == 2 * measured["sample_rate"],
            f"{measured['samples']} campioni a {measured['sample_rate']} Hz",
        )
        check(
            "S1 il report dichiara le dissolvenze (zero)",
            result["fade_in"] == 0.0 and result["fade_out"] == 0.0,
            (result["fade_in"], result["fade_out"]),
        )

        # -- S2: le dissolvenze chiudono il salto --------------------------- #
        faded = audio_slice.slice_audio(
            source, 0.5, 2.5, work / "loop_faded.wav", fade_in=0.006, fade_out=0.006
        )
        check(
            "S2 con 6 ms ai bordi la giunzione e' continua",
            faded["seam"]["continuous"] is True,
            faded["seam"]["ratio"],
        )
        check(
            "S2 il salto e' misurato, non azzerato per decreto",
            0.0 < faded["seam"]["typical_step"],
            faded["seam"]["typical_step"],
        )

        # -- S3: un salto vero si vede -------------------------------------- #
        jump = audio_slice.seam(write_jump(work / "jump.wav"))
        check(
            "S3 un salto vero non passa per continuo",
            jump["continuous"] is False and jump["ratio"] > 1.0,
            jump["ratio"],
        )

        # -- S4: argomenti impossibili -------------------------------------- #
        for label, call in (
            ("fine prima dell'inizio", lambda: audio_slice.slice_audio(source, 2.0, 1.0, work / "x.wav")),
            ("dissolvenze piu' lunghe della fetta", lambda: audio_slice.slice_audio(
                source, 0.0, 1.0, work / "x.wav", fade_in=0.8, fade_out=0.8
            )),
            ("sorgente assente", lambda: audio_slice.slice_audio(
                work / "nope.wav", 0.0, 1.0, work / "x.wav"
            )),
        ):
            try:
                call()
                refused = False
            except (ValueError, RuntimeError):
                refused = True
            check(f"S4 rifiuta: {label}", refused)

        # -- S5: --loop-safe esce 5 ----------------------------------------- #
        script = ROOT / "scripts" / "vx.py"
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "audio",
                "slice",
                "--source",
                str(write_jump(work / "jump2.wav")),
                "--start",
                "0.0",
                "--end",
                "0.02",
                "--out",
                str(work / "safe.wav"),
                "--loop-safe",
                "--json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        check("S5 --loop-safe esce 5 su un salto vero", proc.returncode == 5, proc.returncode)
        check(
            "S5 il referto dice il rapporto misurato",
            '"ratio"' in proc.stdout and '"continuous": false' in proc.stdout,
            proc.stdout[:160],
        )
        good = subprocess.run(
            [
                sys.executable,
                str(script),
                "audio",
                "slice",
                "--source",
                str(source),
                "--start",
                "0.5",
                "--end",
                "2.5",
                "--out",
                str(work / "safe2.wav"),
                "--fade-in",
                "0.006",
                "--fade-out",
                "0.006",
                "--loop-safe",
                "--json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        check("S5 --loop-safe esce 0 quando il giro e' continuo", good.returncode == 0, good.returncode)

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
