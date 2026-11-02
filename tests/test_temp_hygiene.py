"""D-11: the analysis helpers do not leave their scratch behind.

Every site that used ``tempfile.mkdtemp`` and never removed it is exercised here
with a real call, and the Windows temp directory is compared before and after.
A folder whose name starts with ``vx`` that appears during the run is a leak: the
suite once left 2034 of them and 2.5 GB.

python tests/test_temp_hygiene.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import accel, bpm, grammar, vad  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:56} {reason}")


def scratch_names() -> set[str]:
    """Every top-level temp entry the project could have created."""
    root = Path(tempfile.gettempdir())
    try:
        entries = list(root.iterdir())
    except OSError:
        return set()
    return {
        entry.name
        for entry in entries
        if entry.is_dir() and entry.name.lower().startswith("vx")
    }


def pulse(tmp: Path) -> Path:
    """A one second tone with a clear attack, enough for a beat grid."""
    target = tmp / "pulse.wav"
    proc = subprocess.run(
        [
            ff.ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.0",
            "-ar",
            "22050",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 or not target.is_file():
        raise RuntimeError(proc.stderr[-200:] or "ffmpeg could not build the tone")
    return target


def main() -> int:
    try:
        binary = ff.ffmpeg()
    except Exception as exc:  # noqa: BLE001 - a missing encoder is a declared skip
        skip("whole file", f"no ffmpeg: {type(exc).__name__}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0
    if not binary:
        skip("whole file", "no ffmpeg")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    with tempfile.TemporaryDirectory(prefix="vx-hygiene-src-") as raw:
        source = pulse(Path(raw))
        before = scratch_names()

        try:
            grid = grammar.beat_grid(str(source), max_seconds=2.0)
            check(
                "beat_grid decodes and returns a grid",
                isinstance(grid.bpm, float) and not grid.method.startswith("decode"),
                f"bpm={grid.bpm} method={grid.method}",
            )

            estimate = bpm.estimate(str(source), seconds=2.0)
            check(
                "bpm.estimate decodes and returns a number",
                estimate is not None and not estimate.reason.startswith("decode"),
                f"bpm={estimate.bpm} reason={estimate.reason}",
            )

            regions = vad.detect(str(source))
            check(
                "vad.detect reads the pcm",
                regions is not None and "decode" not in (regions.reason or ""),
                f"engine={regions.engine} reason={regions.reason}",
            )

            encoded = accel.test_encode("libx264", frames=3)
            check(
                "test_encode actually encodes",
                bool(encoded.get("ok")),
                f"ok={encoded.get('ok')} ms={encoded.get('ms')}",
            )
        except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
            check("the helpers run", False, f"{type(exc).__name__}: {exc}")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 1

        after = scratch_names()

    leaked = sorted(after - before)
    check(
        "no vx* folder survives the helpers",
        not leaked,
        ",".join(leaked[:6]) or "none",
    )
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
