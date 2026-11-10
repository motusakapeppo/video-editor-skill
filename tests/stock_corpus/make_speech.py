"""Il girato di prova **con parlato e pause**: sintetizzato, non scaricato.

Serve a due test che le misure sintetiche non possono fare: la coda che il ponte
riempie col parlato successivo (e la sua trascrizione a parte) e il punteggio dei
b-roll sulle parole vere. Il parlato viene da SAPI (Windows), le pause dalla
concatenazione con silenzio, il video da un pattern in movimento.

Se la voce o ffmpeg non ci sono, :func:`build` restituisce ``False``: il test si
dichiara e salta, la trascrizione non si finge.

Uso:
    python tests/stock_corpus/make_speech.py [--force]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

NAME = "speech_pauses_70s.mp4"
GAP_SEC = 2.5
COPIES = 3
SENTENCES = (
    "This is the first sentence of the test, with clear and distinct words.",
    "The second sentence talks about a road at night with lights and rain.",
    "The third sentence closes the story and invites you to follow the channel.",
)
SPEAK_SCRIPT = """$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices = $s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo }
if (-not $voices) { throw "no SAPI voice installed" }
$i = 0
foreach ($line in @(
  "__S1__",
  "__S2__",
  "__S3__"
)) {
  $i++
  $s.SetOutputToWaveFile((Join-Path $args[0] "s$i.wav"))
  $s.Speak($line)
}
$s.Dispose()
"""


def _powershell() -> str | None:
    for name in ("powershell", "pwsh"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _ffmpeg() -> str | None:
    from vx import ffmpeg as ff  # noqa: PLC0415 - solo quando si costruisce

    try:
        return ff.ffmpeg()
    except Exception:  # noqa: BLE001
        return shutil.which("ffmpeg")


def _run(args: list) -> bool:
    proc = subprocess.run(args, check=False, capture_output=True)
    return proc.returncode == 0


def synthesize(parts: Path) -> bool:
    """Tre frasi, un wav ciascuna, con la voce di sistema."""
    shell = _powershell()
    if shell is None:
        return False
    script = parts / "speak.ps1"
    body = (
        SPEAK_SCRIPT.replace("__S1__", SENTENCES[0])
        .replace("__S2__", SENTENCES[1])
        .replace("__S3__", SENTENCES[2])
    )
    script.write_text(body.replace("$args[0]", f"'{parts}'"), encoding="utf-8")
    return _run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    ) and all((parts / f"s{i}.wav").is_file() for i in (1, 2, 3))


def build(dest: Path | None = None, *, force: bool = False) -> bool:
    """Costruisce il girato e restituisce ``True`` se esiste e si legge."""
    target = Path(dest) if dest is not None else Path(__file__).resolve().parent / NAME
    if target.is_file() and not force:
        return True
    ff = _ffmpeg()
    if ff is None:
        return False
    with tempfile.TemporaryDirectory(prefix="vx-speech-parts-") as raw:
        parts = Path(raw)
        if not synthesize(parts):
            return False
        silence = parts / "sil.wav"
        if not _run(
            [ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-t", str(GAP_SEC), "-i", "anullsrc=r=22050:cl=mono", str(silence)]
        ):
            return False
        inputs = []
        for index in range(1, 4):
            inputs += ["-i", str(parts / f"s{index}.wav"), "-i", str(silence)]
        # s1 sil s2 sil s3 sil: il silenzio finale separa una copia dall'altra.
        stream = "[0:a][1:a][2:a][3:a][4:a][5:a]concat=n=6:v=0:a=1[a]"
        block = parts / "block.wav"
        if not _run(
            [ff, "-y", "-hide_banner", "-loglevel", "error", *inputs,
             "-filter_complex", stream, "-map", "[a]", "-ar", "22050", "-ac", "1",
             str(block)]
        ):
            return False
        listing = parts / "list.txt"
        listing.write_text(
            "".join(f"file '{block.as_posix()}'\n" for _ in range(COPIES)),
            encoding="utf-8",
        )
        triple = parts / "triple.wav"
        if not _run(
            [ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
             "-safe", "0", "-i", str(listing), "-c", "copy", str(triple)]
        ):
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        ok = _run(
            [ff, "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc2=size=480x852:rate=15:duration=70",
             "-i", str(triple), "-map", "0:v", "-map", "1:a",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "48k",
             "-shortest", str(target)]
        )
        return bool(ok and target.is_file())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="girato di prova con parlato e pause")
    parser.add_argument("--force", action="store_true", help="ricostruisce anche se esiste")
    args = parser.parse_args(argv)
    target = Path(__file__).resolve().parent / NAME
    if build(target, force=args.force):
        print(f"pronto: {target}")
        return 0
    print("non costruito: serve SAPI (Windows) e ffmpeg")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
