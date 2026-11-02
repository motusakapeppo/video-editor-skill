"""F25-T01: the camera registry is data, and its refusals are real.

Scenario:

S1 — at least 40 moves, each with family, trigger, amplitude inside (0, 0.35] and a
     cap above zero; `validate()` is empty on the shipped table.
S2 — a move without a trigger fails `validate()` naming itself: the rule is not a
     comment.
S3 — three moves on one clip are legal, four are `STACK_TOO_DEEP` with the names.
S4 — an unknown name is `CAMERA_UNKNOWN` and the message carries the name.
S5 — a built move is a `crop` effect with x/y/width/height tracks, and the EDL 3
     compiler accepts a document that carries it. A move longer than its cap is
     `CAMERA_TOO_LONG`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import camera, edl3, edl3_render  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def main() -> int:
    check(
        "S1 at least forty named moves",
        len(camera.MOVES) >= 40,
        f"{len(camera.MOVES)} move(s)",
    )
    check(
        "S1 every move declares family, trigger, amplitude and cap",
        not camera.validate(),
        camera.validate()[:2],
    )
    check(
        "S1 the families are many, not one",
        len(camera.FAMILIES) >= 8,
        camera.FAMILIES,
    )
    check(
        "S1 every trigger is a sentence, not a placeholder",
        all(len(move.trigger) >= 20 for move in camera.MOVES.values()),
        min(len(move.trigger) for move in camera.MOVES.values()),
    )

    broken = dict(camera.MOVES)
    broken["no_trigger"] = camera.CameraMove(
        name="no_trigger", family="push", path="push", trigger="", amplitude=0.1
    )
    problems = camera.validate(broken)
    check(
        "S2 a move without a trigger fails the validator by name",
        any("no_trigger" in problem and "trigger" in problem for problem in problems),
        problems[:2],
    )

    three = camera.stack(["push_in", "pan_left", "handheld_light"])
    check("S3 three moves on a clip are legal", len(three) == 3, [m.name for m in three])
    try:
        camera.stack(["push_in", "pan_left", "handheld_light", "whip_right"])
        code = ""
    except camera.CameraError as exc:
        code = exc.code
        names = exc.detail
    check("S3 four are STACK_TOO_DEEP", code == "STACK_TOO_DEEP", code)
    check(
        "S3 and the refusal lists them",
        code == "STACK_TOO_DEEP" and "whip_right" in names,
        names[:80] if code else "",
    )

    try:
        camera.build("teleport", duration=2.0)
        unknown = ""
    except camera.CameraError as exc:
        unknown = exc.detail
    check(
        "S4 an unknown name is refused with the name in the message",
        "teleport" in unknown and "unknown camera move" in unknown,
        unknown[:70],
    )

    effect = camera.build("push_in_slow", duration=6.0)
    check(
        "S5 the move is a crop effect with four tracks",
        effect.kind == "crop"
        and sorted(effect.keyframes) == ["height", "width", "x", "y"],
        sorted(effect.keyframes),
    )
    check(
        "S5 its track follows the declared amplitude",
        abs(effect.keyframes["width"].keys[0].value - 1.0) < 1e-9
        and effect.keyframes["width"].keys[-1].value < 1.0,
        [k.value for k in effect.keyframes["width"].keys],
    )

    try:
        camera.build("whip_left", duration=camera.MOVES["whip_left"].cap_seconds + 1.0)
        too_long = ""
    except camera.CameraError as exc:
        too_long = exc.code
    check("S5 a move longer than its cap is refused", too_long == "CAMERA_TOO_LONG", too_long)

    source = ROOT / "tests" / "stock_corpus" / "bright_45.mp4"
    if not source.is_file():
        check("S5 the compile fixture exists", False, str(source))
    else:
        document = edl3.Timeline3(
            fps=24,
            size=(320, 568),
            clips=[
                edl3.Clip3(
                    id="c1",
                    source=str(source),
                    src_in=0.0,
                    src_out=4.0,
                    effects=[camera.build("push_in", duration=4.0)],
                )
            ],
        )
        problems = [
            p["code"] for p in document.validate() if p["code"] in edl3_render.HARD_CODES
        ]
        check("S5 the document validates", not problems, problems)
        plan = edl3_render.compile_timeline(document, audio="drop")
        check(
            "S5 and the compiler emits the crop chain",
            "crop" in plan["filter_script"],
            [line for line in plan["filter_script"].splitlines() if "crop" in line][:1],
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
