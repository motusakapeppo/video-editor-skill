"""D-24: la stabilizzazione esiste, si misura, e l'intenzione si dichiara.

Scenario:

S1 - ``plan`` propone ma non applica con l'intento di default, e non propone
     nulla quando il jitter e' sotto soglia. Un intento sconosciuto e' rifiutato
     con il suo valore.
S2 - con ``apply`` la clip viene davvero stabilizzata: due passaggi vidstab, il
     jitter **misurato** scende di almeno il 30%, e il referto porta prima/dopo.
S3 - una clip gia' ferma non peggiora: la stabilizzazione non e' un danno
     collaterale su chi non ne aveva bisogno.
S4 - ``apply`` su una clip che non esiste e' un errore dichiarato, non un file
     vuoto che poi si misura zero.

La clip di prova e' costruita nel test (crop con offset casuali): un tremolio
noto, riproducibile, senza dipendere da file fuori dal repo.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import analyze, stabilize  # noqa: E402
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


def shaky(path: Path, amplitude: int = 12) -> Path:
    vf = (
        f"scale={320 + 2 * amplitude}:{180 + 2 * amplitude},"
        f"crop=320:180:x='{amplitude}+random(1)*{amplitude}':y='{amplitude}+random(2)*{amplitude}',"
        "scale=320:180,setsar=1"
    )
    subprocess.run(
        [ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3",
         "-vf", vf, "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def still(path: Path) -> Path:
    subprocess.run(
        [ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3",
         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def main() -> int:
    try:
        ff.ffmpeg()
    except Exception as exc:  # noqa: BLE001
        skip("whole file", f"no ffmpeg: {type(exc).__name__}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    # S1 -------------------------------------------------------------------- #
    proposed = stabilize.plan(jitter=2.0, threshold=1.0)
    check(
        "S1 by default it proposes and does not apply",
        proposed["proposed"] is True and proposed["applied"] is False,
        f"{proposed['intent']}: {proposed['why']}",
    )
    check(
        "S1 the proposal says the movement may be intended",
        "voluto" in proposed["note"],
        proposed["note"],
    )
    quiet = stabilize.plan(jitter=0.2, threshold=1.0, intent="apply")
    check(
        "S1 below the threshold nothing is proposed nor applied",
        quiet["proposed"] is False and quiet["applied"] is False,
        quiet["why"],
    )
    off = stabilize.plan(jitter=9.0, threshold=1.0, intent="off")
    check("S1 off never proposes", off["proposed"] is False, off["intent"])
    try:
        stabilize.plan(jitter=1.0, threshold=1.0, intent="forse")
        code, detail = "", ""
    except stabilize.StabilizeError as exc:
        code, detail = exc.code, exc.detail
    check(
        "S1 an undeclared intent is refused with its value",
        code == "STABILIZE_INTENT" and "forse" in detail,
        code,
    )

    with tempfile.TemporaryDirectory(prefix="vx-stab-") as raw:
        work = Path(raw)
        source = shaky(work / "shaky.mp4")
        before = float(analyze.measure_jitter(source)["score"])
        check("S2 the fixture really shakes", before > 0.5, f"jitter {before:.3f}")

        # S2 ---------------------------------------------------------------- #
        result = stabilize.apply(source, work / "out.mp4")
        check(
            "S2 the stabilization drops the measured jitter by at least 30%",
            result["drop"] >= stabilize.MIN_DROP and result["effective"],
            f"{result['jitter_before']} -> {result['jitter_after']} (-{result['drop'] * 100:.1f}%)",
        )
        check(
            "S2 and the report carries both numbers",
            result["jitter_before"] > 0 and result["jitter_after"] >= 0,
            f"{result['jitter_before']} / {result['jitter_after']}",
        )
        check(
            "S2 the output exists and has frames",
            Path(result["output"]).is_file() and Path(result["output"]).stat().st_size > 1000,
            Path(result["output"]).stat().st_size,
        )

        # S3 ---------------------------------------------------------------- #
        still_clip = still(work / "still.mp4")
        still_before = float(analyze.measure_jitter(still_clip)["score"])
        gate = stabilize.plan(jitter=still_before, threshold=1.0, intent="apply")
        check(
            "S3 on a clip already still the gate refuses to apply",
            gate["applied"] is False and gate["proposed"] is False,
            f"jitter {still_before:.3f} < soglia, applicato={gate['applied']}",
        )
        forced = stabilize.apply(still_clip, work / "still_out.mp4")
        check(
            "S3 and forcing it anyway is recorded as harmful, not as success",
            forced["effective"] is False and forced["drop"] < 0.0,
            f"{forced['jitter_before']:.3f} -> {forced['jitter_after']:.3f} "
            f"({'peggiora' if forced['drop'] < 0 else 'migliora'})",
        )

        # S4 ---------------------------------------------------------------- #
        try:
            stabilize.apply(work / "non-esiste.mp4", work / "x.mp4")
            missing, missing_detail = "", ""
        except stabilize.StabilizeError as exc:
            missing, missing_detail = exc.code, exc.detail
        check(
            "S4 a missing source is refused, not measured as zero",
            missing == "STABILIZE_SOURCE" and "non-esiste" in missing_detail,
            missing,
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
