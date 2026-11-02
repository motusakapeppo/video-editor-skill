"""F25-T08: la modalita' autonoma come unica eccezione, dichiarata e motivata.

Scenario:

S1 — senza dichiarazione e senza approvazione il render resta rifiutato:
     `KEYFRAMES_NOT_APPROVED`, e il messaggio dice che il brief non dichiara la
     modalita' autonoma.
S2 — `autonomous` presente ma senza motivo (o con `declared` falso) non apre
     niente, e il brief lo segnala: una deroga senza ragione non e' una deroga.
S3 — dichiarata e motivata, il render parte **senza** record di approvazione e
     il referto ripete la dichiarazione in chiaro, con il motivo verbatim.
S4 — il test deve fallire se l'eccezione si applica con `autonomous` assente dal
     documento: un brief che non la contiene, passato al cancello, non apre.

    python tests/test_keygate_autonomous.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import brief, edl3, edl3_render, keygate  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FPS = 12
SIZE = (128, 224)
REASON = "ripresa senza operatore: il committente ha chiesto un primo montaggio da rivedere, non un master"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:62} {reason}")


def timeline(path: Path) -> edl3.Timeline3:
    clips = [
        edl3.Clip3(
            id=f"c{index}",
            source=str(path),
            src_in=float(index),
            src_out=float(index) + 1.0,
            effects=[edl3.Effect(kind="curves", params={"mid": 0.42})],
        )
        for index in range(3)
    ]
    return edl3.Timeline3(fps=FPS, size=SIZE, clips=clips)


def refusal(call) -> tuple:
    try:
        call()
    except keygate.GateError as exc:
        return exc.code, exc.detail
    return "", ""


def main() -> int:
    try:
        ff.ffmpeg()
    except Exception as exc:  # noqa: BLE001
        skip("whole file", f"no ffmpeg: {type(exc).__name__}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    with tempfile.TemporaryDirectory(prefix="vx-autonomous-") as raw:
        work = Path(raw)
        source = work / "source.mp4"
        proc = subprocess.run(
            [
                ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi",
                "-i", f"testsrc2=size={SIZE[0]}x{SIZE[1]}:rate={FPS}:duration=3",
                "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "18", str(source),
            ],
            capture_output=True,
        )
        if proc.returncode != 0:
            check("the fixture builds", False, proc.stderr.decode("utf-8", "replace")[-200:])
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 1

        document = timeline(source)
        record = work / "keyframes.json"
        plan = keygate.plan(document, [0, 1, 2], out_dir=work / "kf")

        # S1 ---------------------------------------------------------------- #
        legal = brief._valid_brief()
        code, detail = refusal(lambda: keygate.check(plan, record, brief=legal))
        check("S1 without approval and without declaration the gate stays closed", code == "KEYFRAMES_NOT_APPROVED", code)
        check(
            "S1 and the refusal says the brief does not declare it",
            "does not declare autonomous" in detail,
            detail[-60:],
        )

        # S2 ---------------------------------------------------------------- #
        empty = brief._valid_brief()
        keygate.declare_autonomous(empty, "   ")
        code, detail = refusal(lambda: keygate.check(plan, record, brief=empty))
        check("S2 a waiver without a reason opens nothing", code == "KEYFRAMES_NOT_APPROVED", code)
        check(
            "S2 and the brief reports the empty reason",
            any("autonomous.why" in error for error in brief.validate(empty)),
            [error for error in brief.validate(empty) if "autonomous" in error],
        )
        falsy = brief._valid_brief()
        falsy["autonomous"] = {"declared": False, "why": REASON}
        code, _ = refusal(lambda: keygate.check(plan, record, brief=falsy))
        check("S2 declared false opens nothing", code == "KEYFRAMES_NOT_APPROVED", code)

        # S3 ---------------------------------------------------------------- #
        declared = brief._valid_brief()
        keygate.declare_autonomous(declared, REASON)
        check("S3 the declaration keeps the brief legal", not brief.validate(declared), brief.validate(declared))
        opened = keygate.check(plan, record, brief=declared)
        check(
            "S3 the gate opens by declared exception, without a record",
            opened["waiver"]["declared"] is True and opened["approved"] is False,
            opened["waiver"]["declared"],
        )
        check(
            "S3 the report repeats the declaration with the reason verbatim",
            REASON in opened["report"] and "modalita' autonoma" in opened["report"],
            opened["report"][:70],
        )
        video = work / "render.mp4"
        edl3_render.render(document, video, workdir=work / "w", audio="drop", settings={})
        check(
            "S3 and the render starts and produces a readable file",
            video.is_file() and video.stat().st_size > 1000,
            f"{video.stat().st_size if video.is_file() else 0} bytes",
        )

        # S4 ---------------------------------------------------------------- #
        smuggled = brief._valid_brief()
        smuggled.pop("autonomous", None)
        code, _ = refusal(lambda: keygate.check(plan, record, brief=smuggled))
        check(
            "S4 the exception cannot apply with autonomous absent from the document",
            code == "KEYFRAMES_NOT_APPROVED",
            code,
        )
        code, _ = refusal(lambda: keygate.check(plan, record))
        check(
            "S4 and not even without a brief at all",
            code == "KEYFRAMES_NOT_APPROVED",
            code,
        )

        # The record still wins when it is there and current: the waiver is not a
        # bypass of the keyframes, it is a different door with a written note.
        keygate.approve(plan, record)
        approved = keygate.check(plan, record, brief=declared)
        check(
            "S4 an approval takes precedence and carries no waiver",
            approved.get("waiver") is None and approved["count"] == 3,
            approved["count"],
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
