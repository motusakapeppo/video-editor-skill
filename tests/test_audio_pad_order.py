"""D-28: `apad` dopo un `amix` ha bisogno del formato dichiarato prima.

Scenario:

S1 - un `amix` di frammenti ritardati seguito da `apad=whole_dur` **senza**
     `aformat` non si allunga: lo stream non ha formato ne' durata nota, e il pad
     resta fermo (misurato: 4,019 s su 6 richiesti).
S2 - con `aformat` **prima** di `apad`, il pad arriva: 6,000 s.
S3 - con una sorgente sana, la catena completa (frammenti, adelay, amix,
     aformat, apad, atrim) copre la durata chiesta.

Il test costruisce i casi con ffmpeg: nessuna asserzione sul codice interno, solo
sul numero che esce dal file.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import ffmpeg as ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:60} {reason}")


def audio_duration(path: Path) -> float:
    proc = subprocess.run(
        [
            ff.ffprobe(), "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=duration", "-of", "csv=p=0", str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


FRAGMENTS = (
    "[0:a]atrim=0:1,asetpts=PTS-STARTPTS,adelay=0|0[f0];"
    "[0:a]atrim=1:2,asetpts=PTS-STARTPTS,adelay=3000|3000[f1];"
    "[f0][f1]amix=inputs=2:dropout_transition=0:normalize=0"
)
FMT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


def run(name: str, graph: str, work: Path) -> float:
    out = work / f"{name}.m4a"
    proc = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=duration=4",
            "-filter_complex", graph, "-map", "[aout]", "-c:a", "aac", str(out),
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-200:])
    return audio_duration(out)


def main() -> int:
    try:
        ff.ffmpeg()
    except Exception as exc:  # noqa: BLE001
        skip("whole file", f"no ffmpeg: {type(exc).__name__}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    with tempfile.TemporaryDirectory(prefix="vx-apad-") as raw:
        work = Path(raw)

        without = run("senza", FRAGMENTS + ",apad=whole_dur=6[aout]", work)
        with_format = run("con", FRAGMENTS + f",{FMT},apad=whole_dur=6[aout]", work)

        check(
            "S1 apad without a declared format does not extend the mix",
            without < 5.0,
            f"{without:.3f}s su 6 richiesti",
        )
        check(
            "S2 with the format first, the pad arrives",
            abs(with_format - 6.0) < 0.1,
            f"{with_format:.3f}s",
        )
        check(
            "S2 and the two differ, so the order is what matters",
            with_format > without,
            f"{without:.3f} -> {with_format:.3f}",
        )

        full = run(
            "completo",
            FRAGMENTS + f",{FMT},apad=whole_dur=6,atrim=start=0:end=6[aout]",
            work,
        )
        check(
            "S3 the full chain covers the requested duration",
            abs(full - 6.0) < 0.1,
            f"{full:.3f}s",
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
