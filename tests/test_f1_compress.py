"""F1-T02 -- compression exists only when a measurement justifies it, in `vx auto`.

    python tests/test_f1_compress.py

The single-source path (`plan.build_plan` + `render._audio_chain`) already applies
`acompressor` only when the `audio.lra.wide` finding exists. The automatic path
does not: `bridge._gain_dialogue` measures `input_lra` on the used span and then
throws it away, and `build_edl_filtergraph` never emits a compressor.

Three cases, all injected through `loudness_spans`, so no ffmpeg runs:

a. wide dynamic range (LRA above the contract threshold) -> the clip carries a
   compression decision, the report says so with the measurement, and the graph
   contains one `acompressor` per dialogue clip;
b. narrow dynamic range -> no compressor, and the absence is declared with the
   measured LRA and the threshold that was not crossed;
c. LRA not measured -> no compressor, and the absence is declared as unmeasured
   rather than invented.

The threshold is read from `contract.THRESHOLDS["lra_wide"]`, never hardcoded:
a test that pins its own threshold cannot notice the contract moving.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import bridge, contract, edl_plan, render  # noqa: E402

PASS = 0
FAIL = 0

THRESHOLD_LRA = float(contract.THRESHOLDS["lra_wide"])
MISSING = object()


def flag(clip) -> object:
    """The decision must exist and be a bool; an absent field is not a False."""
    value = getattr(clip, "compress", MISSING)
    return value if isinstance(value, bool) else MISSING


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def analysis() -> dict:
    return {
        "speech_regions": [
            {"start": 0.0, "end": 1.0},
            {"start": 1.8, "end": 4.0},
            {"start": 4.6, "end": 6.0},
        ],
        "dead_air": [
            {"start": 1.0, "end": 1.8, "duration": 0.8},
            {"start": 4.0, "end": 4.6, "duration": 0.6},
        ],
    }


def base_plan() -> edl_plan.EDLPlan:
    source = {"c01": edl_plan.SourceRef(path="c01.mp4", sha256="a" * 64, duration=6.0)}
    video = edl_plan.ClipSegment(
        clip_id="v0_0",
        source_id="c01",
        src_in=0.0,
        src_out=6.0,
        dst_in=0.0,
        dst_out=6.0,
        layer=0,
        role="main_talking_head",
    )
    dialogue = edl_plan.ClipSegment(
        clip_id="a0_0",
        source_id="c01",
        src_in=0.0,
        src_out=6.0,
        dst_in=0.0,
        dst_out=6.0,
        layer=0,
        role="dialogue",
    )
    music = edl_plan.ClipSegment(
        clip_id="a1_0",
        source_id="c01",
        src_in=0.0,
        src_out=6.0,
        dst_in=0.0,
        dst_out=6.0,
        layer=0,
        role="music",
        gain_db=-8.0,
    )
    return edl_plan.EDLPlan(
        fps=25.0,
        duration_sec=6.0,
        aspect_ratio="9:16",
        resolution=(1080, 1920),
        sources=source,
        video_tracks=(edl_plan.Track(0, (video,), "a_roll"),),
        audio_tracks=(
            edl_plan.Track(0, (dialogue,), "dialogue"),
            edl_plan.Track(1, (music,), "music"),
        ),
        ducking=edl_plan.DuckingSpec(),
    )


def recorder(input_i: float, input_lra):
    def measure(path, spans):
        measured = {"input_i": input_i, "input_tp": -20.0}
        if input_lra is not None:
            measured["input_lra"] = input_lra
        return measured

    return measure


def build(input_lra):
    return bridge.build(
        base_plan(),
        {"c01": analysis()},
        target_lufs=-14.0,
        loudness_spans=recorder(-24.85, input_lra),
    )


def script(plan: edl_plan.EDLPlan) -> str:
    return render.build_edl_filtergraph(plan, {"c01": 0}, has_audio=True)


def compress_entries(report: bridge.BridgeReport):
    return [item for item in report.audio if "compress" in item]


def declared(report: bridge.BridgeReport, reason: str):
    return [item for item in report.not_applied if item.get("reason") == reason]


def main() -> int:
    check(
        "0. the contract threshold is a positive number",
        THRESHOLD_LRA > 0.0,
        THRESHOLD_LRA,
    )

    wide_plan, wide_report = build(THRESHOLD_LRA + 3.0)
    wide_clips = list(wide_plan.audio_tracks[0].clips)
    wide_text = script(wide_plan)
    check(
        "a. wide LRA: every dialogue clip carries a compression decision",
        len(wide_clips) == 3 and all(flag(clip) is True for clip in wide_clips),
        [flag(clip) for clip in wide_clips],
    )
    check(
        "a. wide LRA: the report records the measurement that justified it",
        len(compress_entries(wide_report)) == 3
        and all(
            item.get("compress") is True
            and abs(float(item.get("input_lra")) - (THRESHOLD_LRA + 3.0)) <= 1e-9
            for item in compress_entries(wide_report)
        ),
        compress_entries(wide_report)[:1],
    )
    check(
        "a. wide LRA: one acompressor per dialogue clip in the graph",
        wide_text.count("acompressor=") == 3,
        wide_text.count("acompressor="),
    )

    narrow_plan, narrow_report = build(THRESHOLD_LRA - 7.0)
    narrow_clips = list(narrow_plan.audio_tracks[0].clips)
    narrow_text = script(narrow_plan)
    narrow_declared = declared(narrow_report, "compress_declined")
    check(
        "b. narrow LRA: no clip carries a compression decision",
        len(narrow_clips) == 3 and all(flag(clip) is False for clip in narrow_clips),
        [flag(clip) for clip in narrow_clips],
    )
    check(
        "b. narrow LRA: no compressor in the graph",
        "acompressor=" not in narrow_text,
        narrow_text.count("acompressor="),
    )
    check(
        "b. narrow LRA: the absence is declared with the measurement",
        len(narrow_declared) == 3
        and all(
            abs(float(item.get("input_lra")) - (THRESHOLD_LRA - 7.0)) <= 1e-9
            and abs(float(item.get("threshold_lra")) - THRESHOLD_LRA) <= 1e-9
            for item in narrow_declared
        ),
        narrow_declared[:1],
    )
    check(
        "b. narrow LRA: the declaration explains itself",
        len(narrow_declared) == 3
        and all(str(item.get("declared") or "").strip() for item in narrow_declared),
        [item.get("declared") for item in narrow_declared][:1],
    )

    unknown_plan, unknown_report = build(None)
    unknown_clips = list(unknown_plan.audio_tracks[0].clips)
    unknown_declared = declared(unknown_report, "compress_unmeasured")
    check(
        "c. unmeasured LRA: no compressor and no invented decision",
        "acompressor=" not in script(unknown_plan)
        and len(unknown_clips) == 3
        and all(flag(clip) is False for clip in unknown_clips),
        [flag(clip) for clip in unknown_clips],
    )
    check(
        "c. unmeasured LRA: the absence is declared as unmeasured",
        len(unknown_declared) == 3
        and all(item.get("input_lra") is None for item in unknown_declared),
        unknown_declared[:1],
    )

    check(
        "d. music is never compressed by the dialogue decision",
        script(wide_plan).count("acompressor=") == 3
        and not any(flag(clip) is True for clip in wide_plan.audio_tracks[1].clips),
        list(wide_plan.audio_tracks[1].clips)[0].clip_id,
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
