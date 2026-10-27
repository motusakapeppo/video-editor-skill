"""Vertical slice for ``vx.edl3``: one effect with keyframes, one transition.

End to end, with a real ffmpeg and real pixels. The checks never trust a filter
string: determinism is measured by hashing captured frames, fade and opacity are
measured as mean luma, and the backend is read from the render report.

    python tests/test_edl3_vertical_slice.py
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402
from vx import edl3_render  # noqa: E402
from vx import golden  # noqa: E402
from vx.ffmpeg import run_ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FPS = 12
SIZE = (320, 568)
DURATION = 2.0
GOLDEN_TIMES = [0.25, 1.0, 1.75]
YAVG = re.compile(r"signalstats\.YAVG=([0-9.]+)")


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:60} {reason}")


# ------------------------------------------------------------------ helpers


def make_source(path: Path, color: str = "white") -> str:
    proc = run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={SIZE[0]}x{SIZE[1]}:r={FPS}:d={DURATION}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        loglevel="error",
    )
    return "" if proc.ok and path.exists() else proc.tail()


def mean_luma(video: Path, t: float) -> float:
    """Mean luma (YAVG) of the frame at ``t``, measured by ffmpeg."""
    proc = run_ff(
        [
            "-ss",
            f"{t:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            "signalstats,metadata=mode=print:file=-",
            "-f",
            "null",
            "-",
        ],
        loglevel="error",
    )
    match = YAVG.search(proc.stdout or "")
    if not proc.ok or match is None:
        raise RuntimeError(f"mean_luma({t}) failed: {proc.tail()}")
    return float(match.group(1))


def opacity_keyframes(points):
    return [edl3.Keyframe(t=t, value=v) for t, v in points]


def transform_effect(points=None, scale: float = 1.0) -> edl3.Effect:
    keyframes = {}
    if points is not None:
        keyframes["opacity"] = edl3.KeyframeTrack(opacity_keyframes(points))
    return edl3.Effect(
        kind="transform",
        params={"scale": scale, "opacity": 1.0},
        keyframes=keyframes,
    )


def make_doc(source: Path, *, effects=None, transition=None) -> edl3.Timeline3:
    clip = edl3.Clip3(
        id="c1",
        source=str(source),
        src_in=0.0,
        src_out=DURATION,
        effects=effects or [],
        transition_in=transition,
    )
    return edl3.Timeline3(fps=FPS, size=SIZE, clips=[clip])


# --------------------------------------------------------------------- main


def main() -> int:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vx_edl3_") as tmp:
        work = Path(tmp)
        source = work / "white.mp4"
        problem = make_source(source)
        if problem:
            skip("source fixture", problem)
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 0

        try:
            run_checks(work, source)
        except Exception:  # noqa: BLE001 - a crash is a failing test, never a skip
            traceback.print_exc()
            check("test harness completed without an unexpected exception", False)

    elapsed = time.monotonic() - started
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  ({elapsed:.1f}s)")
    check("suite stays under the 90 s ceiling", elapsed < 90.0, f"{elapsed:.1f}s")
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


def run_checks(work: Path, source: Path) -> None:
    # --- model round trip ------------------------------------------------
    doc = make_doc(
        source,
        effects=[transform_effect([(0.0, 1.0), (DURATION, 0.35)])],
        transition=edl3.Transition(kind="fade_in", duration=0.5),
    )
    serialised = json.loads(json.dumps(doc.to_dict()))
    reloaded = edl3.Timeline3.from_dict(serialised)
    check(
        "to_dict/from_dict round trip is lossless",
        reloaded.to_dict() == doc.to_dict() and reloaded.version == edl3.EDL3_VERSION,
        reloaded.version,
    )
    track = doc.clips[0].transform().keyframes["opacity"]
    check(
        "linear keyframe sampling hits the midpoint",
        abs(track.sample(1.0) - 0.675) < 1e-6,
        track.sample(1.0),
    )
    hold = edl3.KeyframeTrack(
        [edl3.Keyframe(0.0, 1.0, "hold"), edl3.Keyframe(1.0, 0.0, "hold")]
    )
    check("hold interpolation is a step", hold.sample(0.9) == 1.0, hold.sample(0.9))
    bezier = edl3.KeyframeTrack(
        [edl3.Keyframe(0.0, 0.0, "bezier"), edl3.Keyframe(1.0, 1.0, "bezier")]
    )
    check(
        "bezier ease-in-out samples 0.5 at the midpoint",
        abs(bezier.sample(0.5) - 0.5) < 1e-6,
        bezier.sample(0.5),
    )

    # --- (a) determinism: same document twice -> same frame hashes -------
    first = edl3_render.render(doc, work / "a1.mp4", workdir=work / "a1")
    second = edl3_render.render(doc, work / "a2.mp4", workdir=work / "a2")
    frame_a = golden.capture(work / "a1.mp4", GOLDEN_TIMES, work / "frames_a")
    frame_b = golden.capture(work / "a2.mp4", GOLDEN_TIMES, work / "frames_b")
    hashes_a = [frame["sha256"] for frame in frame_a["frames"]]
    hashes_b = [frame["sha256"] for frame in frame_b["frames"]]
    check(
        "(a) two masters have identical sha256 per frame",
        hashes_a == hashes_b and len(hashes_a) == len(GOLDEN_TIMES),
        [value[:12] for value in hashes_a],
    )
    check(
        "(a) golden.compare finds no pixel difference",
        golden.compare(frame_a, frame_b) == [],
    )
    check(
        "(a) 2-keyframe document reports the expr backend",
        first["backend"] == edl3_render.BACKEND_EXPR,
        first["backend"],
    )

    # --- (b) fade_in is visible and finishes -----------------------------
    fade_doc = make_doc(
        source, transition=edl3.Transition(kind="fade_in", duration=0.5)
    )
    fade_report = edl3_render.render(fade_doc, work / "fade.mp4", workdir=work / "fade")
    luma_fade_early = mean_luma(work / "fade.mp4", 0.05)
    luma_fade_late = mean_luma(work / "fade.mp4", 1.0)
    luma_fade_end = mean_luma(work / "fade.mp4", 1.75)
    check(
        "(b) t=0.05 is darker than t=1.0 by more than 10 luma",
        luma_fade_late - luma_fade_early > 10.0,
        f"{luma_fade_early:.2f} -> {luma_fade_late:.2f} "
        f"(delta {luma_fade_late - luma_fade_early:.2f})",
    )
    check(
        "(b) the fade is finished by t=1.0",
        abs(luma_fade_end - luma_fade_late) < 5.0,
        f"t=1.0 {luma_fade_late:.2f} vs t=1.75 {luma_fade_end:.2f}",
    )
    check(
        "(b) report carries the fade filter",
        bool(fade_report["clips"][0]["fade_filter"]),
        fade_report["clips"][0]["fade_filter"],
    )

    # --- (c) opacity keyframes change real luminance ---------------------
    opacity_doc = make_doc(
        source, effects=[transform_effect([(0.0, 1.0), (DURATION, 0.35)])]
    )
    opacity_report = edl3_render.render(
        opacity_doc, work / "opacity.mp4", workdir=work / "opacity"
    )
    luma_start = mean_luma(work / "opacity.mp4", 0.1)
    luma_end = mean_luma(work / "opacity.mp4", 1.9)
    check(
        "(c) opacity 1.0 -> 0.35 makes the end measurably darker",
        luma_start - luma_end > 10.0,
        f"{luma_start:.2f} -> {luma_end:.2f} (delta {luma_start - luma_end:.2f})",
    )
    check(
        "(c) opacity is real alpha, not a dropped filter",
        opacity_report["clips"][0]["opacity_mode"] == "keyframed"
        and luma_end < luma_start * 0.8,
        f"end/start={luma_end / max(luma_start, 1e-9):.3f}",
    )

    # --- (d) unsupported effect is refused, not skipped ------------------
    # F2-T02 made ``blur`` a renderable registry kind (static gblur); the
    # declared placeholder is now ``chroma_key``, which the compiler must still
    # refuse rather than drop.
    blur_doc = make_doc(
        source,
        effects=[edl3.Effect(kind="chroma_key", params={}, keyframes={})],
    )
    blur_code = ""
    blur_output = work / "blur.mp4"
    try:
        edl3_render.render(blur_doc, blur_output, workdir=work / "blur")
    except edl3_render.CompileError as exc:
        blur_code = exc.code
    except Exception as exc:  # noqa: BLE001 - wrong type is a failure
        blur_code = f"__WRONG_TYPE__{type(exc).__name__}: {exc}"
    check(
        "(d) kind='chroma_key' is refused with UNSUPPORTED_EFFECT",
        blur_code == "UNSUPPORTED_EFFECT",
        blur_code,
    )
    check("(d) refused document wrote no master", not blur_output.exists())

    # --- (e) backend rule is declared in the report ----------------------
    six_points = [
        (0.0, 1.0),
        (0.4, 0.9),
        (0.8, 0.75),
        (1.2, 0.6),
        (1.6, 0.45),
        (DURATION, 0.35),
    ]
    six_doc = make_doc(source, effects=[transform_effect(six_points)])
    six_report = edl3_render.render(six_doc, work / "six.mp4", workdir=work / "six")
    check(
        "(e) 2-keyframe report says expr",
        opacity_report["backend"] == edl3_render.BACKEND_EXPR,
        opacity_report["backend"],
    )
    check(
        "(e) 6-keyframe report says sendcmd",
        six_report["backend"] == edl3_render.BACKEND_SENDCMD,
        six_report["backend"],
    )
    check(
        "(e) both reports name an existing master",
        Path(opacity_report["output"]).exists() and Path(six_report["output"]).exists(),
    )
    check(
        "(e) sendcmd clip is keyframed, not static",
        six_report["clips"][0]["opacity_mode"] == "keyframed"
        and six_report["clips"][0]["keyframes"] == 6,
        six_report["clips"][0]["opacity_mode"],
    )
    luma_six_start = mean_luma(work / "six.mp4", 0.1)
    luma_six_end = mean_luma(work / "six.mp4", 1.9)
    check(
        "(e) sendcmd render also changes real luminance",
        luma_six_start - luma_six_end > 10.0,
        f"{luma_six_start:.2f} -> {luma_six_end:.2f}",
    )

    # --- (f) validate() rejects broken documents -------------------------
    out_of_order = make_doc(
        source, effects=[transform_effect([(1.0, 1.0), (0.5, 0.35)])]
    )
    codes = [problem["code"] for problem in out_of_order.validate()]
    check(
        "(f) out-of-order keyframes report KEYFRAME_ORDER",
        "KEYFRAME_ORDER" in codes,
        codes,
    )
    too_long = make_doc(
        source, transition=edl3.Transition(kind="fade_in", duration=5.0)
    )
    codes = [problem["code"] for problem in too_long.validate()]
    check(
        "(f) a 5 s transition on a 2 s clip reports TRANSITION_TOO_LONG",
        "TRANSITION_TOO_LONG" in codes,
        codes,
    )
    check(
        "(f) the valid slice document validates cleanly",
        doc.validate() == [],
        doc.validate(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
