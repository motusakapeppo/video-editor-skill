"""F25-T07: il cancello dei keyframe prima di ogni render completo.

Scenario:

S1 — `plan` estrae 3 keyframe dalla timeline vera (un PNG per taglio, sha256
     diversi) e l'impronta e' deterministica: due giri, stesso digest.
S2 — senza approvazione il render e' rifiutato con `KEYFRAMES_NOT_APPROVED` e il
     numero mancante; una richiesta di 2 o 7 keyframe e' `KEYFRAME_COUNT` col
     numero chiesto.
S3 — con l'approvazione il cancello si apre e il render completo parte davvero:
     il file esiste e si legge.
S4 — cambiare un keyframe dopo l'approvazione riapre il cancello: stesso numero
     di keyframe, hash diverso, `KEYFRAMES_NOT_APPROVED`.

    python tests/test_keygate.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, edl3_render, keygate  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FPS = 12
SIZE = (128, 224)


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:60} {reason}")


def source(work: Path) -> Path:
    target = work / "source.mp4"
    proc = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi",
            "-i", f"testsrc2=size={SIZE[0]}x{SIZE[1]}:rate={FPS}:duration=3",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "18", str(target),
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("cannot build the source")
    return target


def timeline(path: Path) -> edl3.Timeline3:
    clips = [
        edl3.Clip3(
            id=f"c{index}",
            source=str(path),
            src_in=float(index),
            src_out=float(index) + 1.0,
            effects=[edl3.Effect(kind="curves", params={"mid": 0.40 + 0.05 * index})],
        )
        for index in range(3)
    ]
    return edl3.Timeline3(fps=FPS, size=SIZE, clips=clips)


def main() -> int:
    try:
        ff.ffmpeg()
    except Exception as exc:  # noqa: BLE001
        skip("whole file", f"no ffmpeg: {type(exc).__name__}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    with tempfile.TemporaryDirectory(prefix="vx-keygate-") as raw:
        work = Path(raw)
        document = timeline(source(work))
        record = work / "keyframes.json"

        # S1 ---------------------------------------------------------------- #
        first = keygate.plan(document, [0, 1, 2], out_dir=work / "kf")
        second = keygate.plan(document, [0, 1, 2], out_dir=work / "kf2")
        check(
            "S1 three keyframes are extracted, one per cut",
            first.count == 3 and all(Path(frame["path"]).is_file() for frame in first.frames),
            [Path(frame["path"]).name for frame in first.frames],
        )
        check(
            "S1 the three frames are different",
            len({frame["sha256"] for frame in first.frames}) == 3,
            [frame["sha256"][:8] for frame in first.frames],
        )
        check(
            "S1 the fingerprint is deterministic",
            first.digest == second.digest,
            first.digest[:12],
        )

        # S2 ---------------------------------------------------------------- #
        try:
            keygate.check(first, record)
            missing_code, missing_detail = "", ""
        except keygate.GateError as exc:
            missing_code, missing_detail = exc.code, exc.detail
        check(
            "S2 a render without approval is refused",
            missing_code == "KEYFRAMES_NOT_APPROVED",
            missing_code,
        )
        check(
            "S2 and the refusal counts the missing approvals",
            "0 of 3" in missing_detail,
            missing_detail[:70],
        )

        for count in (2, 7):
            try:
                keygate.plan(document, list(range(count)), out_dir=work / f"bad{count}")
                count_code, count_detail = "", ""
            except keygate.GateError as exc:
                count_code, count_detail = exc.code, exc.detail
            check(
                f"S2 {count} keyframes are refused with the number",
                count_code == "KEYFRAME_COUNT" and str(count) in count_detail,
                count_detail[:70],
            )

        # S3 ---------------------------------------------------------------- #
        keygate.approve(first, record)
        opened = keygate.check(first, record)
        check(
            "S3 the approved gate opens and reports the count",
            opened["approved"] is True and opened["count"] == 3,
            opened["count"],
        )
        video = work / "render.mp4"
        edl3_render.render(document, video, workdir=work / "w-render", audio="drop", settings={})
        check(
            "S3 the full render starts and produces a readable file",
            video.is_file() and video.stat().st_size > 1000,
            f"{video.stat().st_size if video.is_file() else 0} bytes",
        )

        # S4 ---------------------------------------------------------------- #
        changed = timeline(source(work))
        changed.clips[1].effects = [edl3.Effect(kind="curves", params={"mid": 0.75})]
        moved = keygate.plan(changed, [0, 1, 2], out_dir=work / "kf-changed")
        check(
            "S4 moving a keyframe changes the fingerprint",
            moved.digest != first.digest and moved.count == first.count,
            f"{first.digest[:8]} -> {moved.digest[:8]}",
        )
        try:
            keygate.check(moved, record)
            reopened_code, reopened_detail = "", ""
        except keygate.GateError as exc:
            reopened_code, reopened_detail = exc.code, exc.detail
        check(
            "S4 and the gate closes again on the approved record",
            reopened_code == "KEYFRAMES_NOT_APPROVED",
            reopened_code,
        )
        check(
            "S4 the refusal says the keyframes changed",
            "changed after" in reopened_detail,
            reopened_detail[:80],
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
