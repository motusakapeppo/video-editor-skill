"""D-28: un master con l'audio che finisce prima del quadro deve essere fermato.

Scenario:

S1 - un master con audio che copre il video passa `master_audio_coverage`.
S2 - un master con 2 s di audio sotto 6 s di quadro fallisce, con i due numeri e
     la differenza: e' il caso che il loudness da solo non vede.
S3 - senza traccia audio il controllo e' saltato, non inventato (un master muto
     e' una scelta dichiarata altrove, non un difetto di copertura).

    python tests/test_qc_audio_coverage.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import ffmpeg as ff, qc  # noqa: E402

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


def master(path: Path, *, video_s: float, audio_s: float) -> Path:
    args = [
        ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=160x284:rate=12:duration={video_s}",
    ]
    if audio_s > 0:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={audio_s}"]
        args += ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-shortest"]
        # ``-shortest`` here would cut the video too, so drop it: map both and
        # let the container carry a short audio stream.
        args = [item for item in args if item != "-shortest"]
        args += ["-t", f"{video_s}"]
    else:
        args += ["-an", "-t", f"{video_s}"]
    args += ["-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True, capture_output=True)
    return path


def audit(path: Path) -> dict:
    try:
        return qc.audit_master(path, target_lufs=-14.0, target_tp=-1.0)
    except qc.QCFailure as exc:  # noqa: PERF203 - the failure carries the report
        return exc.result


def main() -> int:
    try:
        ff.ffmpeg()
    except Exception as exc:  # noqa: BLE001
        skip("whole file", f"no ffmpeg: {type(exc).__name__}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    with tempfile.TemporaryDirectory(prefix="vx-qc-audio-") as raw:
        work = Path(raw)
        good = master(work / "good.mp4", video_s=4.0, audio_s=4.0)
        bad = master(work / "bad.mp4", video_s=6.0, audio_s=2.0)
        silent = master(work / "silent.mp4", video_s=3.0, audio_s=0.0)

        coverage = {c["code"]: c for c in audit(good)["checks"]}
        check(
            "S1 an audio track that covers the picture passes",
            coverage["master_audio_coverage"]["status"] == "pass",
            coverage["master_audio_coverage"]["detail"],
        )

        broken = {c["code"]: c for c in audit(bad)["checks"]}
        check(
            "S2 a short audio track fails, not silently",
            broken["master_audio_coverage"]["status"] == "fail",
            broken["master_audio_coverage"]["detail"],
        )
        check(
            "S2 and the failure names both durations",
            "audio=" in broken["master_audio_coverage"]["detail"]
            and "video=" in broken["master_audio_coverage"]["detail"],
            broken["master_audio_coverage"]["detail"],
        )
        check(
            "S2 the code lands in the failure list",
            "master_audio_coverage" in audit(bad)["failures"],
            audit(bad)["failures"],
        )

        muted = {c["code"]: c for c in audit(silent)["checks"]}
        check(
            "S3 a silent master skips coverage instead of failing it",
            muted["master_audio_coverage"]["status"] == "skipped",
            muted["master_audio_coverage"]["detail"],
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
