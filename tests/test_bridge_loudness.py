"""A3: gain is measured on the used span, clamped with a declared shortfall.

python tests/test_bridge_loudness.py

The measurement is injected (``build`` takes ``loudness_spans`` as a
parameter), so no ffmpeg runs: the test checks the formula, the clamp, the
declaration, and that the spans handed to the measurement are the kept ones.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import bridge, edl_plan  # noqa: E402

PASS = 0
FAIL = 0

KEPT = [(0.0, 1.12), (1.68, 4.12), (4.48, 6.0)]
REMOVED = [(1.12, 1.68), (4.12, 4.48)]


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


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


def plan() -> edl_plan.EDLPlan:
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


def recorder(input_i: float, input_tp: float | None = -20.0):
    calls = []

    def measure(path, spans):
        calls.append((path, [tuple(span) for span in spans]))
        measured = {"input_i": input_i, "input_lra": 5.0}
        if input_tp is not None:
            measured["input_tp"] = input_tp
        return measured

    return measure, calls


def broll_plan(dst_in: float = 2.0, length: float = 1.0) -> edl_plan.EDLPlan:
    base = plan()
    broll = edl_plan.ClipSegment(
        clip_id="v1_0",
        source_id="c01",
        src_in=0.0,
        src_out=length,
        dst_in=dst_in,
        dst_out=dst_in + length,
        layer=1,
        role="b_roll",
        transition_in=edl_plan.Transition("fade", 0.5),
    )
    return edl_plan.EDLPlan(
        fps=base.fps,
        duration_sec=base.duration_sec,
        aspect_ratio=base.aspect_ratio,
        resolution=base.resolution,
        sources=dict(base.sources),
        video_tracks=(base.video_tracks[0], edl_plan.Track(1, (broll,), "b_roll")),
        audio_tracks=base.audio_tracks,
        ducking=base.ducking,
    )


def main() -> int:
    measure, calls = recorder(-24.85)
    new_plan, report = bridge.build(
        plan(), {"c01": analysis()}, target_lufs=-14.0, loudness_spans=measure
    )

    dialogue = list(new_plan.audio_tracks[0].clips)
    gains = [item for item in report.audio if "gain_db" in item]
    check("plan valido", new_plan.validate() == [], new_plan.validate())
    check(
        "gain = target - measured",
        all(abs(clip.gain_db - 10.85) <= 1e-9 for clip in dialogue)
        and all(abs(item["gain_db"] - 10.85) <= 1e-9 for item in gains),
        [item["gain_db"] for item in gains],
    )
    check(
        "ogni clip dialogata dichiara la misura",
        len(gains) == len(dialogue) == 3,
        len(gains),
    )
    check(
        "gain scritto sul clip che porta l'audio",
        all(
            abs(clip.gain_db - item["gain_db"]) <= 1e-9
            for clip, item in zip(dialogue, gains)
        )
        and all(clip.clip_id == item["clip_id"] for clip, item in zip(dialogue, gains)),
        [clip.clip_id for clip in dialogue],
    )
    check(
        "nessuna carenza quando il gain e' nel limite",
        all(item["shortfall_lu"] == 0.0 for item in gains)
        and not any(
            item.get("reason") == "gain_beyond_limit" for item in report.not_applied
        ),
        gains,
    )

    measured = [span for _, spans in calls for span in spans]
    check(
        "misurato sui soli span tenuti",
        len(measured) == len(KEPT)
        and all(
            abs(a[0] - b[0]) <= 1e-9 and abs(a[1] - b[1]) <= 1e-9
            for a, b in zip(measured, KEPT)
        ),
        measured,
    )
    check(
        "nessuno span misurato attraversa un silenzio rimosso",
        all(
            not any(
                span[0] < start + 1e-6 and span[1] > end - 1e-6
                for start, end in REMOVED
            )
            for span in measured
        ),
        measured,
    )
    check(
        "durata misurata < clip intera",
        abs(sum(span[1] - span[0] for span in measured) - 5.08) <= 1e-6,
        sum(span[1] - span[0] for span in measured),
    )

    music = list(new_plan.audio_tracks[1].clips)[0]
    check(
        "musica non guadagnata ne toccata dal gain",
        music.gain_db == -8.0 and abs(music.dst_out - 5.08) <= 1e-6,
        (music.gain_db, music.src_out, music.dst_out),
    )

    loud_measure, _ = recorder(-46.0, input_tp=-60.0)
    clamped_plan, clamped_report = bridge.build(
        plan(), {"c01": analysis()}, target_lufs=-14.0, loudness_spans=loud_measure
    )
    clamped = list(clamped_plan.audio_tracks[0].clips)
    limited = [
        item
        for item in clamped_report.not_applied
        if item.get("reason") == "gain_beyond_limit"
    ]
    clamped_gains = [item for item in clamped_report.audio if "gain_db" in item]
    check(
        "gain oltre il tetto viene clampato a 24",
        all(abs(clip.gain_db - 24.0) <= 1e-9 for clip in clamped)
        and all(abs(item["gain_db"] - 24.0) <= 1e-9 for item in clamped_gains),
        [clip.gain_db for clip in clamped],
    )
    check(
        "carenza dichiarata: shortfall_lu = 32 - 24",
        len(limited) == len(clamped_gains) == 3
        and all(abs(item["shortfall_lu"] - 8.0) <= 1e-9 for item in limited)
        and all(abs(item["shortfall_lu"] - 8.0) <= 1e-9 for item in clamped_gains),
        limited,
    )

    peak_measure, _ = recorder(-24.85, input_tp=-10.0)
    peak_plan, peak_report = bridge.build(
        plan(), {"c01": analysis()}, target_lufs=-14.0, loudness_spans=peak_measure
    )
    peak_clips = list(peak_plan.audio_tracks[0].clips)
    peak_entries = [item for item in peak_report.audio if "gain_db" in item]
    peak_declared = [
        item
        for item in peak_report.not_applied
        if item.get("reason") == "gain_limited_by_peak"
    ]
    ceiling = bridge.THRESHOLDS["bridge_gain_tp_ceiling_dbtp"]
    peak_gain = round(ceiling - (-10.0), 2)
    peak_shortfall = round(-14.0 - (-24.85 + peak_gain), 2)
    check(
        "picco che limita: gain = tetto - input_tp",
        all(abs(clip.gain_db - peak_gain) <= 1e-9 for clip in peak_clips)
        and all(abs(item["gain_db"] - peak_gain) <= 1e-9 for item in peak_entries),
        (peak_gain, [item["gain_db"] for item in peak_entries]),
    )
    check(
        "picco che limita: peak_limited, input_tp e loudness_shortfall_lu",
        len(peak_entries) == 3
        and all(item.get("peak_limited") is True for item in peak_entries)
        and all(item.get("input_tp") == -10.0 for item in peak_entries)
        and all(
            abs(item.get("loudness_shortfall_lu", 0.0) - peak_shortfall) <= 1e-9
            for item in peak_entries
        ),
        [item.get("loudness_shortfall_lu") for item in peak_entries],
    )
    check(
        "picco che limita: not_applied dichiara i due numeri",
        len(peak_declared) == 3
        and all(
            abs(item["required_gain_db"] - 10.85) <= 1e-9
            and abs(item["gain_db"] - peak_gain) <= 1e-9
            and abs(item["loudness_shortfall_lu"] - peak_shortfall) <= 1e-9
            for item in peak_declared
        ),
        peak_declared,
    )

    unknown_measure, _ = recorder(-24.85, input_tp=None)
    unknown_plan, unknown_report = bridge.build(
        plan(), {"c01": analysis()}, target_lufs=-14.0, loudness_spans=unknown_measure
    )
    unknown_gains = [item for item in unknown_report.audio if "gain_db" in item]
    check(
        "input_tp assente: il picco non clampa il gain",
        all(
            abs(clip.gain_db - 10.85) <= 1e-9
            for clip in unknown_plan.audio_tracks[0].clips
        )
        and all(abs(item["gain_db"] - 10.85) <= 1e-9 for item in unknown_gains),
        [item["gain_db"] for item in unknown_gains],
    )
    check(
        "input_tp assente: peak_headroom_unknown dichiarato per ogni clip",
        len(
            [
                item
                for item in unknown_report.not_applied
                if item.get("reason") == "peak_headroom_unknown"
            ]
        )
        == 3,
        [item.get("reason") for item in unknown_report.not_applied],
    )

    def with_gain(value: float) -> edl_plan.EDLPlan:
        base = plan()
        dialogue = edl_plan.ClipSegment(
            clip_id="a0_0",
            source_id="c01",
            src_in=0.0,
            src_out=6.0,
            dst_in=0.0,
            dst_out=6.0,
            layer=0,
            role="dialogue",
            gain_db=value,
        )
        return edl_plan.EDLPlan(
            fps=base.fps,
            duration_sec=base.duration_sec,
            aspect_ratio=base.aspect_ratio,
            resolution=base.resolution,
            sources=dict(base.sources),
            video_tracks=base.video_tracks,
            audio_tracks=(
                edl_plan.Track(0, (dialogue,), "dialogue"),
                base.audio_tracks[1],
            ),
            ducking=base.ducking,
        )

    check(
        "clamp coerente con il limite del contratto",
        bridge.GAIN_LIMITS_DB == (-60.0, 24.0)
        and with_gain(-60.0).validate() == []
        and with_gain(-60.5).validate() != [],
        bridge.GAIN_LIMITS_DB,
    )

    try:
        broken_plan, broken_report = bridge.build(
            plan(),
            {"c01": RuntimeError("no decoder")},
            target_lufs=-14.0,
            loudness_spans=recorder(-24.85)[0],
        )
    except Exception as exc:  # noqa: BLE001 - the bridge must not raise here
        check("analisi fallita non solleva", False, f"{type(exc).__name__}: {exc}")
        broken_plan, broken_report = plan(), bridge.BridgeReport()
    else:
        check("analisi fallita non solleva", True, "nessuna eccezione")

    check(
        "analisi fallita: clip intero, non guadagnato, dichiarato",
        len(broken_plan.video_tracks[0].clips) == 1
        and broken_plan.video_tracks[0].clips[0].gain_db == 0.0
        and [item for item in broken_report.audio if "gain_db" in item] == []
        and any(
            str(item.get("reason", "")).startswith("analysis_failed:")
            for item in broken_report.not_applied
        ),
        broken_report.not_applied,
    )

    for bad in (0.0, -75.0):
        bad_plan, bad_report = bridge.build(
            plan(),
            {"c01": analysis()},
            target_lufs=-14.0,
            loudness_spans=recorder(bad)[0],
        )
        bad_dialogue = list(bad_plan.audio_tracks[0].clips)
        invalid = [
            item
            for item in bad_report.not_applied
            if item.get("reason") == "measurement_invalid"
        ]
        check(
            f"input_i {bad:g}: nessun gain applicato",
            [item for item in bad_report.audio if "gain_db" in item] == []
            and all(clip.gain_db == 0.0 for clip in bad_dialogue),
            [item.get("gain_db") for item in bad_report.audio],
        )
        check(
            f"input_i {bad:g}: clip invariato",
            len(bad_dialogue) == len(KEPT)
            and all(
                abs(clip.src_in - span[0]) <= 1e-9
                and abs(clip.src_out - span[1]) <= 1e-9
                for clip, span in zip(bad_dialogue, KEPT)
            ),
            [(clip.src_in, clip.src_out) for clip in bad_dialogue],
        )
        check(
            f"input_i {bad:g}: measurement_invalid con input_i",
            len(invalid) == len(bad_dialogue) == 3
            and all(item.get("input_i") == bad for item in invalid),
            invalid,
        )

    anchored_plan, anchored_report = bridge.build(
        broll_plan(2.0),
        {"c01": analysis()},
        target_lufs=-14.0,
        loudness_spans=recorder(-24.85)[0],
    )
    anchored_track = anchored_plan.video_tracks[1]
    anchored_clip = list(anchored_track.clips)[0]
    uniform = anchored_plan.duration_sec / 2.0
    check(
        "b-roll ancorato tiene il dst_in dopo il taglio",
        abs(anchored_clip.dst_in - 2.0) <= 1e-9
        and abs(anchored_clip.dst_in - uniform) > 0.1,
        (anchored_clip.dst_in, round(uniform, 3)),
    )
    check(
        "b-roll ancorato: plan valido, clip conservato, nessun overlap",
        anchored_plan.validate() == []
        and len(list(anchored_track.clips)) == 1
        and not any(
            item.get("reason") == "broll_overlap_after_cut"
            for item in anchored_report.not_applied
        ),
        anchored_report.not_applied,
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
