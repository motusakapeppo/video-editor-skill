"""F1-T03 -- the report declares SNR and the music ratio, with the threshold used.

    python tests/test_f1_measure.py

Both numbers already exist and are thrown away before the report:

* ``snr_db`` is measured by ``audio.noise_floor_and_speech`` and survives only in
  ``bridge_report.restore`` (per clip), never in ``auto_report.json``;
* ``music_ratio`` is measured by ``audio.analyze_audio`` for every a_roll source
  and is discarded when ``_bridge_plan`` returns.

The contract this file pins:

a. ``auto.audio_summary(analyses, ceiling_dbtp)`` returns the ``audio`` block with
   ``snr_db`` (the worst source), ``music_ratio`` (the most musical source) and
   the two thresholds, while keeping ``ceiling_dbtp`` and ``limiter``;
b. with no measurement the values are ``None`` -- never 0.0, never invented -- and
   the thresholds are still declared;
c. the same function reads dict entries and ``contract.AudioAnalysis`` objects,
   and skips a failed analysis instead of crashing;
d. there is exactly one declared threshold per value: the repo already carries
   0.45, 0.5 and 0.6 for the same music number, and a fourth is a defect.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, contract  # noqa: E402

PASS = 0
FAIL = 0

SNR_THRESHOLD = float(contract.THRESHOLDS["noise_snr_low"])
MUSIC_THRESHOLD = float(contract.THRESHOLDS["triage_music_ratio_low"])


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def main() -> int:
    summary_of = getattr(auto, "audio_summary", None)
    check(
        "0. auto.audio_summary exists",
        callable(summary_of),
        type(summary_of).__name__ if summary_of is not None else "missing",
    )
    if not callable(summary_of):
        print(f"\n{PASS} PASS / {FAIL} FAIL")
        return 1

    analyses = {
        "c01": {"snr_db": 18.4, "music_ratio": 0.72},
        "c02": {"snr_db": 9.1, "music_ratio": 0.31},
        "c03": RuntimeError("no decoder"),
    }
    block = summary_of(analyses, -0.9)

    check(
        "a. snr_db is the worst source",
        block.get("snr_db") == 9.1,
        block.get("snr_db"),
    )
    check(
        "a. music_ratio is the most musical source",
        block.get("music_ratio") == 0.72,
        block.get("music_ratio"),
    )
    check(
        "a. the SNR threshold is the contract one",
        block.get("snr_threshold_db") == SNR_THRESHOLD,
        block.get("snr_threshold_db"),
    )
    check(
        "a. the music threshold is the contract one",
        block.get("music_ratio_threshold") == MUSIC_THRESHOLD,
        block.get("music_ratio_threshold"),
    )
    check(
        "a. the existing audio keys survive",
        block.get("ceiling_dbtp") == -0.9 and block.get("limiter") == "alimiter",
        (block.get("ceiling_dbtp"), block.get("limiter")),
    )
    check(
        "a. a failed analysis is skipped, not summed as zero",
        block.get("snr_db") == 9.1 and block.get("music_ratio") == 0.72,
        block,
    )

    empty = summary_of({}, None)
    check(
        "b. no measurement -> None, never an invented zero",
        empty.get("snr_db") is None and empty.get("music_ratio") is None,
        (empty.get("snr_db"), empty.get("music_ratio")),
    )
    check(
        "b. no ceiling -> no limiter",
        empty.get("ceiling_dbtp") is None and empty.get("limiter") is None,
        (empty.get("ceiling_dbtp"), empty.get("limiter")),
    )
    check(
        "b. the thresholds are declared even with no value",
        empty.get("snr_threshold_db") == SNR_THRESHOLD
        and empty.get("music_ratio_threshold") == MUSIC_THRESHOLD,
        (empty.get("snr_threshold_db"), empty.get("music_ratio_threshold")),
    )

    dataclass_block = summary_of(
        {"c01": contract.AudioAnalysis(snr_db=4.2, music_ratio=0.9)}, None
    )
    check(
        "c. AudioAnalysis objects are read like dicts",
        dataclass_block.get("snr_db") == 4.2
        and dataclass_block.get("music_ratio") == 0.9,
        (dataclass_block.get("snr_db"), dataclass_block.get("music_ratio")),
    )

    threshold_keys = {key for key in block if "threshold" in key}
    check(
        "d. exactly one threshold key per value",
        threshold_keys == {"snr_threshold_db", "music_ratio_threshold"},
        sorted(threshold_keys),
    )
    check(
        "d. the contract values are the ones this repo declares",
        SNR_THRESHOLD == 12.0 and MUSIC_THRESHOLD == 0.5,
        (SNR_THRESHOLD, MUSIC_THRESHOLD),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
