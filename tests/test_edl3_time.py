"""F2-T05 — speed ramp cascade, audio policy and remap curve, with real pixels.

The maths is checked without ffmpeg. Two real renders prove the retiming: a 2 s
clip remapped to end at 1 s must last half as long, and its frame count must match
a natively 1 s clip within one frame. Every encode writes a filter script and
calls ``-filter_complex_script``.

    python tests/test_edl3_time.py
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx.edl3_time import (  # noqa: E402
    MAX_FACTOR,
    MIN_FACTOR,
    TOLERANCE,
    AudioPolicy,
    RemapCurve,
    TimeError,
    atempo_chain,
    audio_fragment,
    ramp_fragment,
)
from vx.ffmpeg import run_ff, run_fp  # noqa: E402

PASS = 0
FAIL = 0
FPS = 12
SIZE = (160, 120)
RATIO_TABLE = [0.3, 0.5, 0.75, 1.0, 1.7, 2.0, 3.5, 8.0, 16.0]


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def refusal(call) -> str:
    try:
        call()
    except TimeError as exc:
        return exc.code
    return ""


# ------------------------------------------------------------------ helpers


def encode(work: Path, name: str, script: str, input_args: list[str]) -> Path:
    """Write the graph to a script file and encode it; never uses ``-vf``."""
    script_path = work / f"{name}.filter.txt"
    script_path.write_text(script + "\n", encoding="utf-8", newline="\n")
    output = work / f"{name}.mp4"
    args = list(input_args) + [
        "-filter_complex_script",
        str(script_path),
        "-map",
        "[out]",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "1",
        str(output),
    ]
    proc = run_ff(args, loglevel="error")
    if not proc.ok or not output.exists():
        raise RuntimeError(f"encode {name} failed: {proc.tail()}")
    return output


def lavfi_input(color: str, duration: float) -> list[str]:
    return [
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={SIZE[0]}x{SIZE[1]}:r={FPS}:d={duration}",
    ]


def graph_from_plan(plan: dict, *, source: str = "0:v", out: str = "out") -> str:
    """Assemble a filtergraph from a plan dict without recomputing any maths."""
    chains = list(plan["video_filters"])
    parts: list[str] = []
    if len(chains) > 1 and plan.get("split"):
        pads = [f"p{index}" for index in range(len(chains))]
        parts.append(f"[{source}]{plan['split']}" + "".join(f"[{pad}]" for pad in pads))
        sources = pads
    else:
        sources = [source] * len(chains)
    labels: list[str] = []
    for index, (src, chain) in enumerate(zip(sources, chains)):
        label = f"seg{index}"
        parts.append(f"[{src}]{chain}[{label}]")
        labels.append(label)
    tail = "".join(f"[{label}]" for label in labels)
    tail += plan["concat"] if plan.get("concat") else "null"
    if plan.get("post"):
        tail += "," + ",".join(plan["post"])
    parts.append(tail + f"[{out}]")
    return ";\n".join(parts)


def probe_video(path: Path) -> tuple[int, float]:
    proc = run_fp(
        [
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames:format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    if not proc.ok:
        raise RuntimeError(f"probe failed: {proc.tail()}")
    data = json.loads(proc.stdout)
    frames = int(data["streams"][0]["nb_read_frames"])
    duration = float(data["format"]["duration"])
    return frames, duration


# --------------------------------------------------------------------- main


def main() -> int:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vx_edl3_time_") as tmp:
        work = Path(tmp)
        try:
            run_maths()
            run_renders(work)
        except Exception as exc:  # noqa: BLE001 - a crash is a failing test
            traceback.print_exc()
            check(
                f"harness completed without an exception ({type(exc).__name__})", False
            )

    elapsed = time.monotonic() - started
    check("suite stays under the 20 s ceiling", elapsed < 20.0, f"{elapsed:.1f}s")
    print(f"\n{PASS} PASS / {FAIL} FAIL  ({elapsed:.1f}s)")
    return 0 if FAIL == 0 else 1


def run_maths() -> None:
    # --- (a) cascade -----------------------------------------------------
    check(
        "(a) atempo_chain(4.0) is [2.0, 2.0]",
        atempo_chain(4.0) == [2.0, 2.0],
        atempo_chain(4.0),
    )
    check(
        "(a) atempo_chain(0.25) is [0.5, 0.5]",
        atempo_chain(0.25) == [0.5, 0.5],
        atempo_chain(0.25),
    )
    check(
        "(a) atempo_chain(1.7) is [1.7]", atempo_chain(1.7) == [1.7], atempo_chain(1.7)
    )

    products_ok = True
    bounds_ok = True
    detail = ""
    for ratio in RATIO_TABLE:
        cascade = atempo_chain(ratio)
        product = math.prod(cascade) if cascade else 1.0
        inside = all(MIN_FACTOR <= factor <= MAX_FACTOR for factor in cascade)
        if abs(product - ratio) > TOLERANCE:
            products_ok = False
            detail += f"{ratio}: product {product}; "
        if not inside:
            bounds_ok = False
            detail += f"{ratio}: out of bounds {cascade}; "
    check(
        "(a) cascade product matches the ratio table within 1e-6",
        products_ok,
        detail or f"{len(RATIO_TABLE)} ratios",
    )
    check(
        "(a) every factor sits inside [MIN_FACTOR, MAX_FACTOR]",
        bounds_ok,
        f"[{MIN_FACTOR}, {MAX_FACTOR}]",
    )

    codes = [
        refusal(lambda: atempo_chain(0.0)),
        refusal(lambda: atempo_chain(-1.0)),
        refusal(lambda: atempo_chain(float("nan"))),
        refusal(lambda: atempo_chain(float("inf"))),
    ]
    check(
        "(a) ratio 0, -1, NaN and inf are refused",
        codes == ["ATEMPO_RATIO_INVALID"] * 4,
        codes,
    )
    check(
        "(a) an inverted bound pair is refused with ATEMPO_BOUNDS",
        refusal(lambda: atempo_chain(2.0, min_factor=2.0, max_factor=0.5))
        == "ATEMPO_BOUNDS",
    )

    # --- (b) audio policies ----------------------------------------------
    fragments = {
        policy: audio_fragment(policy, 4.0, sample_rate=48000) for policy in AudioPolicy
    }
    check(
        "(b) every policy returns a non-empty list of filters",
        all(
            isinstance(fragment, list) and len(fragment) > 0
            for fragment in fragments.values()
        ),
        {policy.value: list(fragment) for policy, fragment in fragments.items()},
    )
    check(
        "(b) segments policy emits one atempo per cascade factor",
        list(fragments[AudioPolicy.SEGMENTS]) == ["atempo=2", "atempo=2"],
        list(fragments[AudioPolicy.SEGMENTS]),
    )
    check(
        "(b) silence policy mutes and declares the mute",
        list(fragments[AudioPolicy.SILENCE]) == ["volume=0"]
        and "muted" in fragments[AudioPolicy.SILENCE].to_dict()["note"],
    )
    pitch = fragments[AudioPolicy.PITCH]
    pitch_payload = pitch.to_dict()
    check(
        "(b) pitch policy is asetrate+aresample at the sample rate",
        list(pitch) == ["asetrate=192000,aresample=48000"],
        list(pitch),
    )
    check(
        "(b) pitch policy declares that the pitch moved",
        pitch_payload["pitch_moved"] is True
        and "NOT corrected" in pitch_payload["note"],
        pitch_payload["note"],
    )
    check(
        "(b) an unknown policy name is refused",
        refusal(lambda: audio_fragment("stretch", 2.0, sample_rate=48000))
        == "AUDIO_POLICY_UNKNOWN",
    )

    # --- (c) remap curve -------------------------------------------------
    curve = RemapCurve([(0.0, 0.0), (1.0, 2.0), (2.0, 3.0)])
    exact = [curve.sample(t) for t in (0.0, 1.0, 2.0)]
    check("(c) sampling is exact at the keyframes", exact == [0.0, 2.0, 3.0], exact)
    between = [curve.sample(t) for t in (0.25, 0.5, 1.5)]
    check(
        "(c) sampling is linear between keyframes", between == [0.5, 1.0, 2.5], between
    )
    check(
        "(c) durations come from the curve",
        curve.duration_timeline == 2.0 and curve.duration_source == 3.0,
        [curve.duration_timeline, curve.duration_source],
    )

    broken = RemapCurve([(1.0, 1.0), (0.5, 0.35)])
    codes = [problem["code"] for problem in broken.validate()]
    check(
        "(c) a non-monotonic curve reports REMAP_ORDER", "REMAP_ORDER" in codes, codes
    )
    check(
        "(c) sampling a broken curve raises REMAP_ORDER",
        refusal(lambda: broken.sample(0.75)) == "REMAP_ORDER",
    )
    duplicate = RemapCurve([(0.0, 0.0), (1.0, 1.0), (1.0, 2.0)])
    duplicate_codes = [problem["code"] for problem in duplicate.validate()]
    check(
        "(c) a duplicate time reports REMAP_DUPLICATE",
        "REMAP_DUPLICATE" in duplicate_codes,
        duplicate_codes,
    )


def run_renders(work: Path) -> None:
    source = encode(
        work,
        "source_2s",
        "[0:v]format=yuv420p[out]",
        lavfi_input("gray", 2.0),
    )
    native = encode(
        work,
        "native_1s",
        "[0:v]format=yuv420p[out]",
        lavfi_input("gray", 1.0),
    )

    # --- (d) a 2x remap halves the duration ------------------------------
    plan = ramp_fragment(RemapCurve([(0.0, 0.0), (1.0, 2.0)]), fps=FPS)
    remapped = encode(work, "remap_2x", graph_from_plan(plan), ["-i", str(source)])

    source_frames, source_duration = probe_video(source)
    remap_frames, remap_duration = probe_video(remapped)
    native_frames, native_duration = probe_video(native)
    frame_slack = 2.0 / FPS

    check(
        "(d) the 2 s source is 2 s",
        abs(source_duration - 2.0) <= frame_slack,
        f"{source_duration:.3f}s",
    )
    check(
        "(d) the 2x remap lasts half the source within 2 frames",
        abs(remap_duration - source_duration / 2.0) <= frame_slack,
        f"{source_duration:.3f}s -> {remap_duration:.3f}s (slack {frame_slack:.3f}s)",
    )
    check(
        "(d) the plan declared a 1 s timeline at ratio 2",
        abs(plan["duration"] - 1.0) <= 1e-9 and plan["ratio"] == 2.0,
        [plan["duration"], plan["ratio"]],
    )

    # --- (e) frame count matches a native 1 s clip -----------------------
    check(
        "(e) the 2x render keeps as many frames as a native 1 s clip (within 1)",
        abs(remap_frames - native_frames) <= 1,
        f"remap {remap_frames} vs native {native_frames} "
        f"(native {native_duration:.3f}s, source {source_frames} frames)",
    )
    check(
        "(e) the remap render is not an empty file",
        remapped.stat().st_size > 0,
        remapped.stat().st_size,
    )


if __name__ == "__main__":
    raise SystemExit(main())
