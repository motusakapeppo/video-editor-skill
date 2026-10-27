"""Render-time estimator: medians over history, defaults declared, no sklearn.

python tests/test_estimate.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import estimate  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def row(
    duration: float,
    seconds: float,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
    preset: str = "medium",
    x264_vs_hw: str = "x264",
    crf: int = 18,
) -> dict:
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "duration_sec": duration,
        "crf": crf,
        "preset": preset,
        "x264_vs_hw": x264_vs_hw,
        "seconds": seconds,
    }


def job(
    duration: float,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
    preset: str = "medium",
    x264_vs_hw: str = "x264",
) -> dict:
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "duration_sec": duration,
        "crf": 18,
        "preset": preset,
        "x264_vs_hw": x264_vs_hw,
    }


def relative_error(predicted: float, truth: float) -> float:
    return abs(predicted - truth) / truth


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx_estimate_") as tmp:
        work = Path(tmp)

        # --- empty history: declared default, explicit note -----------------
        empty_model = estimate.fit([])
        seconds, note = estimate.predict(empty_model, job(40.0))
        check("empty history -> model has no observations", empty_model["n"] == 0)
        check(
            "empty history -> declared default seconds",
            seconds == estimate.DEFAULT_SECONDS,
            seconds,
        )
        check("empty history -> note says no_history", "no_history" in note, note)
        check(
            "default lies inside the declared range",
            estimate.DEFAULT_RANGE_SEC[0] <= seconds <= estimate.DEFAULT_RANGE_SEC[1],
        )
        missing_path = work / "does_not_exist.jsonl"
        seconds_missing, note_missing = estimate.predict(
            estimate.fit(missing_path), job(40.0)
        )
        check(
            "absent history file behaves like empty history",
            seconds_missing == seconds and "no_history" in note_missing,
            note_missing,
        )

        # --- synthetic trend: seconds proportional to duration -------------
        durations = [6.0, 9.0, 12.0, 18.0, 24.0, 30.0, 36.0]
        rate = 0.9
        history = [row(d, d * rate) for d in durations]
        model = estimate.fit(history)
        check(
            "history with 7 renders fits 1 bucket",
            len(model["buckets"]) == 1,
            model["buckets"].keys(),
        )

        held_out = 45.0
        truth = held_out * rate
        predicted, note_held = estimate.predict(model, job(held_out))
        error = relative_error(predicted, truth)
        check(
            "held-out duration within 25% of trend",
            error <= 0.25,
            f"pred={predicted} truth={truth} err={error:.3f}",
        )
        check(
            "held-out note names the bucket",
            "bucket=x264|medium" in note_held,
            note_held,
        )

        # --- monotonic in duration ----------------------------------------
        predictions = [
            estimate.predict(model, job(d))[0] for d in (5.0, 10.0, 20.0, 40.0, 90.0)
        ]
        check(
            "prediction is monotonic in duration",
            all(a < b for a, b in zip(predictions, predictions[1:])),
            predictions,
        )

        # --- resolution scaling -------------------------------------------
        hd = estimate.predict(model, job(20.0, width=1920, height=1080))[0]
        uhd = estimate.predict(model, job(20.0, width=3840, height=2160))[0]
        check(
            "4x pixels -> 4x predicted seconds",
            abs(uhd / hd - 4.0) < 0.01,
            round(uhd / hd, 4),
        )

        # --- codec buckets stay separate ----------------------------------
        mixed = [row(d, d * 1.6) for d in durations] + [
            row(d, d * 0.4, x264_vs_hw="hw") for d in durations
        ]
        mixed_model = estimate.fit(mixed)
        software = estimate.predict(mixed_model, job(20.0))[0]
        hardware = estimate.predict(mixed_model, job(20.0, x264_vs_hw="hw"))[0]
        check(
            "hardware job predicted faster than software job",
            hardware < software,
            (hardware, software),
        )
        check(
            "hardware prediction uses the hw bucket",
            "hw|medium" in estimate.predict(mixed_model, job(20.0, x264_vs_hw="hw"))[1],
        )
        check(
            "unseen preset falls back and says so",
            "fallback"
            in estimate.predict(mixed_model, job(20.0, preset="veryslow"))[1],
        )

        # --- JSONL round-trip through record() ----------------------------
        history_path = work / "render_history.jsonl"
        for d in durations:
            estimate.record(history_path, job(d), d * rate)
        loaded = estimate.load_history(history_path)
        check(
            "record() appends one JSONL row per render",
            len(loaded) == len(durations),
            len(loaded),
        )
        from_disk = estimate.fit(history_path)
        predicted_disk, _ = estimate.predict(from_disk, job(held_out))
        check(
            "history reloaded from disk predicts within 25%",
            relative_error(predicted_disk, truth) <= 0.25,
            predicted_disk,
        )
        check(
            "model survives a JSON round-trip",
            estimate.predict(json.loads(json.dumps(model)), job(held_out))[0]
            == predicted,
        )

        # --- junk rows are counted, not crashed on ------------------------
        dirty = history[:-1] + [
            row(0.0, 10.0),
            {"width": 1920, "height": 1080, "fps": 30, "duration_sec": 10.0},
            {
                "width": "x",
                "height": None,
                "fps": 30,
                "duration_sec": 10.0,
                "seconds": 3.0,
            },
        ]
        dirty_model = estimate.fit(dirty)
        check(
            "invalid rows are skipped and counted",
            dirty_model["n"] == len(history) - 1 and dirty_model["skipped"] == 3,
            (dirty_model["n"], dirty_model["skipped"]),
        )

        # --- begin/finish contract used by auto ---------------------------
        run_dir = work / "auto_run"
        fragment = estimate.begin(
            type(
                "Plan",
                (),
                {"resolution": [1920, 1080], "fps": 30.0, "duration_sec": 20.0},
            )(),
            run_dir,
            crf=18,
            preset="medium",
        )
        check(
            "begin() reports an estimate before the render",
            fragment["predicted_seconds"] > 0
            and fragment["actual_seconds"] is None
            and "_started" in fragment,
            fragment["predicted_seconds"],
        )
        finished = estimate.finish(fragment)
        check(
            "finish() records actual seconds and drops private keys",
            finished["actual_seconds"] is not None
            and "_started" not in finished
            and finished["error_ratio"] is not None,
            (finished["actual_seconds"], finished["error_ratio"]),
        )
        check(
            "finish() appends the render to history",
            len(estimate.load_history(run_dir / estimate.HISTORY_FILENAME)) == 1,
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
