"""F16-T04: un candidato hardware passa solo se il fotogramma decodificato regge.

Scenari:

S1 — `verify_encoder('libx264')` (il riferimento contro se stesso) da' MAD 0,0 e
     passa: la misura e' onesta.
S2 — un candidato vero (libx265, o l'hardware se c'e') porta un MAD misurato e un
     verdetto; se supera la soglia il motivo nomina il numero.
S3 — un candidato degradato di proposito (scala 32x18 e CRF 51) supera la soglia:
     il cancello **puo'** fallire, quindi non e' aggirabile.
S4 — un encoder che non esiste e' rifiutato con un motivo, non ignorato.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import accel  # noqa: E402
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


def frame_of(video: Path, target: Path) -> Path:
    proc = subprocess.run(
        [ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-frames:v", "1", str(target)],
        capture_output=True,
    )
    if proc.returncode != 0 or not target.is_file():
        raise RuntimeError("cannot decode")
    return target


def main() -> int:
    try:
        binary = ff.ffmpeg()
    except Exception as exc:  # noqa: BLE001
        skip("whole file", f"no ffmpeg: {type(exc).__name__}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0
    if not binary:
        skip("whole file", "no ffmpeg")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    reference = accel.verify_encoder("libx264")
    check(
        "S1 the software reference passes against itself",
        reference["ok"] is True and reference["mad"] == 0.0,
        f"mad={reference['mad']}",
    )
    check(
        "S1 the verdict carries the declared threshold",
        reference["threshold"] == accel.ENCODER_MAD_MAX,
        reference["threshold"],
    )

    for candidate in ("libx265", "h264_nvenc", "hevc_nvenc"):
        if candidate not in accel.ENCODERS:
            continue
        verdict = accel.verify_encoder(candidate)
        if verdict["mad"] is None:
            skip(f"S2 {candidate}", verdict["reason"][:60])
            continue
        check(
            f"S2 {candidate} carries a measured verdict",
            isinstance(verdict["mad"], float)
            and verdict["ok"] == (verdict["mad"] <= verdict["threshold"]),
            f"mad={verdict['mad']} ok={verdict['ok']}",
        )

    with tempfile.TemporaryDirectory(prefix="vx-fidelity-test-") as raw:
        work = Path(raw)
        good = work / "good.mp4"
        bad = work / "bad.mp4"
        for path, extra in ((good, []), (bad, ["-vf", "scale=32:18,scale=320:180", "-crf", "51"])):
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
                    accel.FIDELITY_SOURCE,
                    "-c:v",
                    "libx264",
                    *(["-crf", "24"] if not extra else []),
                    *extra,
                    "-pix_fmt",
                    "yuv420p",
                    str(path),
                ],
                capture_output=True,
            )
            if proc.returncode != 0 or not path.is_file():
                check("the fixtures build", False, path.name)
                print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
                return 1

        reference_frame = frame_of(good, work / "good.png")
        bad_frame = frame_of(bad, work / "bad.png")
        mad = accel.compare_frames(reference_frame, bad_frame)
        check(
            "S3 a deliberately degraded encode exceeds the threshold",
            mad > accel.ENCODER_MAD_MAX,
            f"mad={mad:.2f} > {accel.ENCODER_MAD_MAX:.2f}",
        )

    unknown = accel.verify_encoder("definitely_not_an_encoder")
    check(
        "S4 an unknown encoder is refused with a reason",
        unknown["ok"] is False and bool(unknown["reason"]),
        unknown["reason"][:60],
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
