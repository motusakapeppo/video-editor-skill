"""F2-T06 — reverse and freeze plans, with real pixels and a declared cap.

The caps are checked without ffmpeg. Two real renders prove the visuals: after
reversing a black-then-white clip the head is bright and the tail is dark, and a
one-second freeze holds one frame identically (frame hashes equal, MAD <= 2).
Every encode writes a filter script and calls ``-filter_complex_script``.

    python tests/test_edl3_extremes.py
"""

from __future__ import annotations

import hashlib
import re
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx.edl3_extremes import (  # noqa: E402
    MAX_FREEZE_DURATION_SEC,
    MAX_REVERSE_DURATION_SEC,
    ExtremesError,
    freeze_plan,
    reverse_plan,
)
from vx.ffmpeg import run_ff  # noqa: E402

PASS = 0
FAIL = 0
FPS = 12
SIZE = (160, 120)
YAVG = re.compile(r"signalstats\.YAVG=([0-9.]+)")


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def refusal(call) -> ExtremesError | None:
    try:
        call()
    except ExtremesError as exc:
        return exc
    return None


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


def lavfi_input(source: str) -> list[str]:
    return ["-f", "lavfi", "-i", source]


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
            "-filter_complex",
            "[0:v]signalstats,metadata=mode=print:file=-[out]",
            "-map",
            "[out]",
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


def grab_frame(work: Path, video: Path, t: float, name: str, *, raw: bool) -> Path:
    """One decoded frame at ``t``: raw bytes for hashing, PNG for MAD."""
    if raw:
        path = work / f"{name}.rgb"
        tail = ["-pix_fmt", "rgb24", "-f", "rawvideo", "-y", str(path)]
    else:
        path = work / f"{name}.png"
        tail = ["-vf", "format=rgb24", "-f", "image2", "-y", str(path)]
    proc = run_ff(
        ["-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1"] + tail,
        loglevel="error",
    )
    if not proc.ok or not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"grab_frame({t}) failed: {proc.tail()}")
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mad_y(first: Path, second: Path) -> float:
    """Mean absolute difference in luma between two stills, measured by ffmpeg."""
    proc = run_ff(
        [
            "-i",
            str(first),
            "-i",
            str(second),
            "-filter_complex",
            "[0:v]format=yuv420p[x];[1:v]format=yuv420p[y];"
            "[x][y]blend=all_mode=difference,signalstats,"
            "metadata=mode=print:file=-[out]",
            "-map",
            "[out]",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        loglevel="error",
    )
    match = YAVG.search(proc.stdout or "")
    if not proc.ok or match is None:
        raise RuntimeError(f"mad_y failed: {proc.tail()}")
    return float(match.group(1))


# --------------------------------------------------------------------- main


def main() -> int:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vx_edl3_extremes_") as tmp:
        work = Path(tmp)
        try:
            run_plans()
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


def run_plans() -> None:
    # --- (f) declared caps -----------------------------------------------
    plan = reverse_plan(5.0, False)
    check(
        "(f) reverse_plan(5.0) is allowed",
        plan["video_filters"] == ["reverse"]
        and plan["audio_filters"] == []
        and plan["duration"] == 5.0,
        plan["video_filters"],
    )
    check(
        "(f) reverse_plan states the RAM consequence in one line",
        "\n" not in plan["memory_note"] and "RAM" in plan["memory_note"],
        plan["memory_note"],
    )
    check(
        "(f) reverse_plan adds areverse only when the clip has audio",
        reverse_plan(5.0, True)["audio_filters"] == ["areverse"],
    )
    check(
        "(f) reverse_plan is pure (same inputs, same dict)",
        reverse_plan(5.0, True) == reverse_plan(5.0, True),
    )

    too_long = refusal(lambda: reverse_plan(600.0, False))
    check(
        "(f) reverse_plan(600.0) is refused with REVERSE_TOO_LONG",
        too_long is not None and too_long.code == "REVERSE_TOO_LONG",
        None if too_long is None else too_long.code,
    )
    check(
        "(f) the refusal carries the requested number and the cap",
        too_long is not None
        and too_long.payload.get("duration") == 600.0
        and too_long.payload.get("cap") == MAX_REVERSE_DURATION_SEC
        and "600" in str(too_long),
        None if too_long is None else too_long.payload,
    )
    check(
        "(f) the cap boundary itself is still allowed",
        reverse_plan(MAX_REVERSE_DURATION_SEC, False)["duration"]
        == MAX_REVERSE_DURATION_SEC,
    )

    freeze = freeze_plan(0.5, 1.0, fps=FPS)
    check(
        "(f) freeze_plan holds the declared frame, not the raw time",
        freeze["frame_index"] == 6 and freeze["freeze_at"] == 0.5,
        [freeze["frame_index"], freeze["freeze_at"]],
    )
    check(
        "(f) freeze_plan uses tpad stop_mode=clone",
        any(
            "tpad=stop_mode=clone:stop_duration=1" in chain
            for chain in freeze["video_filters"]
        ),
        freeze["video_filters"],
    )
    check(
        "(f) freeze_plan is pure (same inputs, same dict)",
        freeze_plan(0.5, 1.0, fps=FPS) == freeze_plan(0.5, 1.0, fps=FPS),
    )
    over = refusal(lambda: freeze_plan(0.5, 301.0, fps=FPS))
    check(
        "(f) a hold over the cap is refused with FREEZE_TOO_LONG",
        over is not None and over.code == "FREEZE_TOO_LONG",
        None if over is None else over.code,
    )
    check(
        "(f) the freeze refusal carries the requested number",
        over is not None
        and over.payload.get("hold_duration") == 301.0
        and "301" in str(over)
        and "300" in str(over),
        None if over is None else over.payload,
    )
    check(
        "(f) a zero hold is refused, not rendered as no-op",
        (refusal(lambda: freeze_plan(0.5, 0.0, fps=FPS)) or ExtremesError("", "")).code
        == "EXTREMES_HOLD_INVALID",
    )
    check(
        "(f) the freeze cap boundary itself is still allowed",
        freeze_plan(0.5, MAX_FREEZE_DURATION_SEC, fps=FPS)["hold_duration"]
        == MAX_FREEZE_DURATION_SEC,
    )


def run_renders(work: Path) -> None:
    # --- (g) reverse turns the head bright and the tail dark -------------
    black_white = encode(
        work,
        "black_white_1s",
        "[0:v]trim=start=0:end=0.5,setpts=PTS-STARTPTS[b];\n"
        "[1:v]trim=start=0:end=0.5,setpts=PTS-STARTPTS[w];\n"
        "[b][w]concat=n=2:v=1:a=0[out]",
        lavfi_input(f"color=c=black:s={SIZE[0]}x{SIZE[1]}:r={FPS}:d=0.5")
        + lavfi_input(f"color=c=white:s={SIZE[0]}x{SIZE[1]}:r={FPS}:d=0.5"),
    )
    reverse = reverse_plan(1.0, False)
    reversed_video = encode(
        work,
        "reversed_1s",
        graph_from_plan(reverse),
        ["-i", str(black_white)],
    )
    head = mean_luma(reversed_video, 0.1)
    tail = mean_luma(reversed_video, 0.9)
    check(
        "(g) after reverse the frame at 0.1 s is bright",
        head > 150.0,
        f"YAVG(0.1)={head:.2f}",
    )
    check(
        "(g) after reverse the frame at 0.9 s is dark",
        tail < 60.0,
        f"YAVG(0.9)={tail:.2f}",
    )
    check(
        "(g) the two measurements straddle the white/black cut",
        head - tail > 100.0,
        f"{head:.2f} vs {tail:.2f}",
    )

    # --- (h) freeze clones one frame, identical bytes --------------------
    source = encode(
        work,
        "changing_2s",
        "[0:v]format=yuv420p[out]",
        lavfi_input(f"testsrc2=s={SIZE[0]}x{SIZE[1]}:r={FPS}:d=2"),
    )
    freeze = freeze_plan(0.5, 1.0, fps=FPS)
    frozen_video = encode(
        work,
        "frozen_2s",
        graph_from_plan(freeze),
        ["-i", str(source)],
    )

    early_raw = grab_frame(work, frozen_video, 0.6, "freeze_060", raw=True)
    late_raw = grab_frame(work, frozen_video, 1.4, "freeze_140", raw=True)
    hash_early = sha256(early_raw)
    hash_late = sha256(late_raw)
    check(
        "(h) frames at 0.6 s and 1.4 s share one hash",
        hash_early == hash_late,
        f"{hash_early[:16]} vs {hash_late[:16]}",
    )

    early_png = grab_frame(work, frozen_video, 0.6, "freeze_060", raw=False)
    original_png = grab_frame(work, source, 0.5, "original_050", raw=False)
    difference = mad_y(early_png, original_png)
    check(
        "(h) the held frame equals the original frame at 0.5 s (MAD <= 2)",
        difference <= 2.0,
        f"MAD={difference:.3f}",
    )

    later_png = grab_frame(work, source, 1.5, "original_150", raw=False)
    drift = mad_y(original_png, later_png)
    check(
        "(h) the source really changes over time, so the hold is meaningful",
        drift > 2.0,
        f"MAD(0.5, 1.5)={drift:.3f}",
    )


if __name__ == "__main__":
    raise SystemExit(main())
