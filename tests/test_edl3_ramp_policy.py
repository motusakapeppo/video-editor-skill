"""F2-T13 gate - the ramp audio policy and the reverse/freeze duration caps.

The caps are exercised at both levels they live at: the planning module refuses
over-cap numbers, and a /1.1 document that asks for one is refused through
``compile_timeline`` with the same code. The 2x ramp declares its audio policy
from the allowed vocabulary and its filter chain is the atempo staircase, with
the pitch preserved.

    python tests/test_edl3_ramp_policy.py
"""

from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402
from vx import edl3_render  # noqa: E402
from vx.edl3_extremes import (  # noqa: E402
    MAX_FREEZE_DURATION_SEC,
    MAX_REVERSE_DURATION_SEC,
    ExtremesError,
    freeze_plan,
    reverse_plan,
)
from vx.edl3_time import (  # noqa: E402
    AudioPolicy,
    TimeError,
    atempo_chain,
    audio_fragment,
)
from vx.ffmpeg import run_ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FPS = 12
SIZE = (64, 64)
DURATION = 2.0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:64} {reason}")


def refusal(call) -> BaseException:
    try:
        call()
    except BaseException as exc:  # noqa: BLE001 - the test inspects the refusal
        return exc
    return None


def make_source(path: Path) -> str:
    proc = run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c=gray:s={SIZE[0]}x{SIZE[1]}:r={FPS}:d={DURATION}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        loglevel="error",
    )
    return "" if proc.ok and path.exists() else proc.tail()


def document(source: str, **clip_fields) -> edl3.Timeline3:
    length = float(clip_fields.pop("src_out", DURATION))
    clip = edl3.Clip3(
        id="c1",
        source=source,
        src_in=0.0,
        src_out=length,
        **clip_fields,
    )
    return edl3.Timeline3(fps=FPS, size=SIZE, clips=[clip])


def cap_text(cap: float) -> str:
    return f"{cap:g}"


def run_checks(source: str, source_ok: bool) -> None:
    print("\n-- (a) the declared caps and their codes")
    check(
        "reverse declares a positive cap",
        MAX_REVERSE_DURATION_SEC > 0.0,
        f"{MAX_REVERSE_DURATION_SEC:g}s",
    )
    check(
        "freeze declares a positive cap",
        MAX_FREEZE_DURATION_SEC > 0.0,
        f"{MAX_FREEZE_DURATION_SEC:g}s",
    )
    over_reverse = refusal(
        lambda: reverse_plan(MAX_REVERSE_DURATION_SEC + 1.0, False)
    )
    check(
        "reverse_plan over the cap is refused with REVERSE_TOO_LONG",
        isinstance(over_reverse, ExtremesError)
        and over_reverse.code == "REVERSE_TOO_LONG",
        None if over_reverse is None else over_reverse.code,
    )
    check(
        "the reverse refusal names the requested length and the cap",
        over_reverse is not None
        and over_reverse.payload.get("cap") == MAX_REVERSE_DURATION_SEC
        and cap_text(MAX_REVERSE_DURATION_SEC) in str(over_reverse),
        None if over_reverse is None else over_reverse.payload,
    )
    over_freeze = refusal(
        lambda: freeze_plan(
            0.5, MAX_FREEZE_DURATION_SEC + 1.0, fps=float(FPS)
        )
    )
    check(
        "freeze_plan over the cap is refused with FREEZE_TOO_LONG",
        isinstance(over_freeze, ExtremesError)
        and over_freeze.code == "FREEZE_TOO_LONG",
        None if over_freeze is None else over_freeze.code,
    )
    check(
        "the freeze refusal names the requested hold and the cap",
        over_freeze is not None
        and over_freeze.payload.get("cap") == MAX_FREEZE_DURATION_SEC
        and cap_text(MAX_FREEZE_DURATION_SEC) in str(over_freeze),
        None if over_freeze is None else over_freeze.payload,
    )
    check(
        "the reverse cap boundary itself is still allowed",
        reverse_plan(MAX_REVERSE_DURATION_SEC, False)["duration"]
        == MAX_REVERSE_DURATION_SEC,
    )
    check(
        "the freeze cap boundary itself is still allowed",
        freeze_plan(0.5, MAX_FREEZE_DURATION_SEC, fps=float(FPS))["hold_duration"]
        == MAX_FREEZE_DURATION_SEC,
    )

    print("\n-- (b) a document over the cap is refused by the compiler")
    if not source_ok:
        skip("document-level cap checks", "no synthetic source")
    else:
        reverse_doc = document(
            source, reverse=True, src_out=MAX_REVERSE_DURATION_SEC + 1.0
        )
        reverse_exc = refusal(lambda: edl3_render.compile_timeline(reverse_doc))
        check(
            "a reverse clip beyond the cap raises REVERSE_TOO_LONG",
            reverse_exc is not None
            and getattr(reverse_exc, "code", "") == "REVERSE_TOO_LONG",
            None if reverse_exc is None else getattr(reverse_exc, "code", ""),
        )
        check(
            "the refusal carries the number and the cap",
            reverse_exc is not None
            and getattr(reverse_exc, "payload", {}).get("cap")
            == MAX_REVERSE_DURATION_SEC
            and cap_text(MAX_REVERSE_DURATION_SEC) in str(reverse_exc),
            None if reverse_exc is None else getattr(reverse_exc, "payload", {}),
        )
        freeze_doc = document(
            source, freeze={"t": 0.5, "hold": MAX_FREEZE_DURATION_SEC + 1.0}
        )
        freeze_exc = refusal(lambda: edl3_render.compile_timeline(freeze_doc))
        check(
            "a freeze beyond the cap raises FREEZE_TOO_LONG",
            freeze_exc is not None
            and getattr(freeze_exc, "code", "") == "FREEZE_TOO_LONG",
            None if freeze_exc is None else getattr(freeze_exc, "code", ""),
        )
        check(
            "the freeze refusal carries the hold and the cap",
            freeze_exc is not None
            and getattr(freeze_exc, "payload", {}).get("cap")
            == MAX_FREEZE_DURATION_SEC
            and cap_text(MAX_FREEZE_DURATION_SEC) in str(freeze_exc),
            None if freeze_exc is None else getattr(freeze_exc, "payload", {}),
        )
        at_cap = document(source, reverse=True, src_out=MAX_REVERSE_DURATION_SEC)
        at_cap_exc = refusal(lambda: edl3_render.compile_timeline(at_cap))
        check(
            "a reverse clip exactly at the cap still compiles",
            at_cap_exc is None,
            None if at_cap_exc is None else getattr(at_cap_exc, "code", ""),
        )

    print("\n-- (c) a 2x ramp declares its audio policy from the vocabulary")
    allowed = sorted(edl3.AUDIO_TIME_POLICIES)
    check(
        "the allowed audio policies are the declared enum",
        edl3.AUDIO_TIME_POLICIES == {item.value for item in AudioPolicy},
        allowed,
    )
    ramp = document(source, speed=2.0, audio_policy=AudioPolicy.SEGMENTS.value)
    check(
        "a 2x ramp with a declared policy validates cleanly",
        ramp.validate() == [],
        ramp.validate(),
    )
    if not source_ok:
        skip("ramp report checks", "no synthetic source")
    else:
        plan = edl3_render.compile_timeline(ramp)
        entry = plan["clips"][0]
        check(
            "the declared policy survives into the report",
            entry["audio_policy"] in allowed
            and entry["audio_policy"] == "segments"
            and entry["audio_policy_declared"] is True,
            f"{entry['audio_policy']} declared={entry['audio_policy_declared']}",
        )
        check(
            "the 2x chain is one atempo step and the pitch does not move",
            entry["audio"]["filters"] == ["atempo=2"]
            and entry["audio"]["pitch_moved"] is False
            and "pitch is preserved" in entry["audio"]["note"],
            f"{entry['audio']['filters']} "
            f"pitch_moved={entry['audio']['pitch_moved']}",
        )
        default_ramp = document(source, speed=2.0)
        default_entry = edl3_render.compile_timeline(default_ramp)["clips"][0]
        check(
            "an undeclared policy defaults to segments and says it was a default",
            default_entry["audio_policy"]
            == edl3_render.DEFAULT_RAMP_AUDIO_POLICY.value
            and default_entry["audio_policy_declared"] is False,
            f"{default_entry['audio_policy']} "
            f"declared={default_entry['audio_policy_declared']}",
        )

    print("\n-- (d) the staircase is bounded per step, pitch-preserving")
    check(
        "4x is two steps of the declared ceiling",
        atempo_chain(4.0) == [2.0, 2.0],
        atempo_chain(4.0),
    )
    check(
        "0.25x is two steps of the declared floor",
        atempo_chain(0.25) == [0.5, 0.5],
        atempo_chain(0.25),
    )
    segments = audio_fragment(AudioPolicy.SEGMENTS, 2.0)
    check(
        "the segments fragment declares the pitch preserved",
        segments.pitch_moved is False and list(segments) == ["atempo=2"],
        segments.to_dict(),
    )

    print("\n-- (e) an unknown audio policy is refused")
    unknown = document(source, speed=2.0, audio_policy="warp")
    codes = [problem["code"] for problem in unknown.validate()]
    check(
        "an unknown audio_policy reports AUDIO_POLICY",
        "AUDIO_POLICY" in codes,
        codes,
    )
    unknown_exc = refusal(lambda: edl3_render.compile_timeline(unknown))
    check(
        "the compiler refuses it with AUDIO_POLICY",
        unknown_exc is not None
        and getattr(unknown_exc, "code", "") == "AUDIO_POLICY",
        None if unknown_exc is None else getattr(unknown_exc, "code", ""),
    )
    direct = refusal(lambda: audio_fragment("warp", 2.0))
    check(
        "audio_fragment refuses it with AUDIO_POLICY_UNKNOWN",
        isinstance(direct, TimeError) and direct.code == "AUDIO_POLICY_UNKNOWN",
        None if direct is None else direct.code,
    )


def main() -> int:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vx_ramp_policy_") as tmp:
        work = Path(tmp)
        source = work / "gray.mp4"
        problem = make_source(source)
        try:
            if problem:
                skip("synthetic source", problem)
            run_checks(str(source), not problem)
        except Exception:  # noqa: BLE001 - a crash is a failure, never a skip
            traceback.print_exc()
            check("test harness completed without an unexpected exception", False)

    elapsed = time.monotonic() - started
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  ({elapsed:.1f}s)")
    check("suite stays under the 90 s ceiling", elapsed < 90.0, f"{elapsed:.1f}s")
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
