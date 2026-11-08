"""A8/A10/A11: semantic B-roll score, camera ops, overlapping B-roll policy.

python tests/test_bridge_broll_policy.py

No ffmpeg, no whisper, no network. The cuts are the A5 fixture's two silences,
so the destination timeline is arithmetic:

    kept  [0.00, 1.12] [1.68, 4.12] [4.48, 6.00]
    dst   [0.00, 1.12] [1.12, 3.56] [3.56, 5.08]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import bridge, contract, edl_plan  # noqa: E402

PASS = 0
FAIL = 0

SPEECH = [(0.0, 1.0), (1.8, 4.0), (4.6, 6.0)]
SILENCES = [(1.0, 1.8), (4.0, 4.6)]
DURATION = 5.08


class FakeTranscript:
    def __init__(self, words):
        self.words = [
            {"start": start, "end": end, "word": token, "probability": probability}
            for start, end, token, probability in words
        ]
        self.segments = []
        self.skipped_reason = ""


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def analysis() -> dict:
    return {
        "speech_regions": [{"start": s, "end": e} for s, e in SPEECH],
        "dead_air": [{"start": s, "end": e, "duration": e - s} for s, e in SILENCES],
    }


def loudness(path, spans):
    return {"input_i": -20.0, "input_tp": -1.0, "input_lra": 5.0}


def dense_words(*, low_until: float = 0.0, low_confidence: float = 1.0):
    words = []
    for start, end in SPEECH:
        cursor = start
        while cursor < end - 1e-9:
            last = min(cursor + 0.1, end)
            confidence = low_confidence if cursor < low_until else 1.0
            words.append((cursor, last, "parola", confidence))
            cursor = last
    return words


def plan(
    brolls=(),
    *,
    a_roll_camera: edl_plan.CameraSpec | None = None,
    b_roll_camera: edl_plan.CameraSpec | None = None,
) -> edl_plan.EDLPlan:
    sources = {
        "c01": edl_plan.SourceRef(
            path="c01.mp4",
            sha256="a" * 64,
            duration=6.0,
            camera=a_roll_camera or edl_plan.CameraSpec(),
        ),
        "b01": edl_plan.SourceRef(
            path="b01.mp4",
            sha256="b" * 64,
            duration=6.0,
            camera=b_roll_camera or edl_plan.CameraSpec(),
        ),
    }
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
    broll_clips = [
        edl_plan.ClipSegment(
            clip_id=clip_id,
            source_id="b01",
            src_in=0.0,
            src_out=length,
            dst_in=dst_in,
            dst_out=dst_in + length,
            layer=1,
            role="b_roll",
        )
        for clip_id, dst_in, length in brolls
    ]
    tracks = [edl_plan.Track(0, (video,), "a_roll")]
    if broll_clips:
        tracks.append(edl_plan.Track(1, tuple(broll_clips), "b_roll"))
    return edl_plan.EDLPlan(
        fps=25.0,
        duration_sec=6.0,
        aspect_ratio="9:16",
        resolution=(1080, 1920),
        sources=sources,
        video_tracks=tuple(tracks),
    )


def build(source_plan, words=None):
    transcripts = None if words is None else {"c01": FakeTranscript(words)}
    return bridge.build(
        source_plan,
        {"c01": analysis()},
        target_lufs=-14.0,
        loudness_spans=loudness,
        transcripts=transcripts,
    )


def reasons(report, name):
    return [item for item in report.not_applied if item.get("reason") == name]


def broll_clips(new_plan):
    return [
        clip
        for track in new_plan.video_tracks
        for clip in track.clips
        if int(getattr(clip, "layer", 0)) >= 1
    ]


def main() -> int:
    threshold = float(contract.THRESHOLDS["broll_min_score"])
    check(
        "soglia broll_min_score dichiarata in [0, 1]",
        0.0 <= threshold <= 1.0,
        threshold,
    )

    # -- A8: score above threshold keeps the clip, score in the report ---- #
    kept_plan, kept = build(plan([("v1_0", 1.30, 0.30)]), dense_words())
    entries = broll_clips(kept_plan)
    check(
        "score sopra soglia: clip tenuto",
        len(entries) == 1 and len(reasons(kept, "broll_below_threshold")) == 0,
        [item.get("reason") for item in kept.not_applied],
    )
    top = kept.broll[0] if kept.broll else {}
    check(
        "score nel referto, normalizzato e sopra soglia",
        top.get("accepted") is True
        and isinstance(top.get("score"), float)
        and threshold <= float(top["score"]) <= 1.0,
        top,
    )

    # -- A8/A13: score sotto soglia ma parlato vicino: si sposta, non si butta -
    near_words = [(start, start + 0.1, "parola", 1.0) for start in [3.0, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8]]
    low_plan, low = build(plan([("v1_0", 1.44, 0.60)]), near_words)
    shifted = reasons(low, "broll_shifted_to_speech")
    moved = broll_clips(low_plan)
    check(
        "score sotto soglia con parlato vicino: il b-roll si sposta",
        len(moved) == 1 and len(shifted) == 1,
        [item.get("reason") for item in low.not_applied],
    )
    check(
        "e il referto dice da dove a dove, col punteggio nuovo",
        bool(shifted)
        and shifted[0].get("from") is not None
        and abs(float(shifted[0].get("to")) - float(shifted[0].get("from"))) > 1e-6
        and float(shifted[0].get("score") or 0.0) >= threshold,
        shifted,
    )
    check(
        "il clip spostato comincia sul parlato",
        bool(moved) and moved[0].dst_in >= 2.0 and moved[0].dst_in <= 3.4,
        moved[0].dst_in if moved else None,
    )

    # -- A8: parlato già sotto la finestra, nessun inizio migliore: si scarta - #
    far_plan, far = build(plan([("v1_0", 1.44, 0.60)]), [(2.00, 2.10, "parola", 0.60)])
    dropped = reasons(far, "broll_below_threshold")
    check(
        "nessun inizio migliore entro la finestra: clip scartato col numero",
        broll_clips(far_plan) == []
        and len(dropped) == 1
        and dropped[0].get("score") == 0.1
        and dropped[0].get("threshold") == threshold,
        dropped,
    )
    check(
        "e la finestra scartata cita le sue ancore",
        len(dropped) == 1
        and dropped[0].get("anchor_ids") is not None
        and dropped[0].get("anchor_id") == (dropped[0]["anchor_ids"] or [None])[0],
        dropped[0] if dropped else None,
    )

    # -- A8: no score keeps it and declares the missing measurement ------- #
    no_score_plan, no_score = build(plan([("v1_0", 1.30, 0.30)]), None)
    unavailable = reasons(no_score, "broll_score_unavailable")
    check(
        "score assente: clip tenuto e dichiarato",
        len(broll_clips(no_score_plan)) == 1
        and len(unavailable) == 1
        and unavailable[0].get("score") is None
        and no_score.broll
        and no_score.broll[0].get("accepted") is True
        and no_score.broll[0].get("score") is None,
        unavailable,
    )

    # -- A11: higher score wins, both ids and both scores declared -------- #
    higher_plan, higher = build(
        plan([("v1_0", 5.00, 1.00), ("v1_1", 1.90, 1.00)]),
        dense_words(low_until=2.46, low_confidence=0.8),
    )
    clashes = reasons(higher, "broll_overlap_after_cut")
    survivors = broll_clips(higher_plan)
    check(
        "sovrapposizione: vince lo score piu' alto",
        len(survivors) == 1
        and survivors[0].clip_id == "v1_1"
        and len(clashes) == 1
        and clashes[0].get("clip_id") == "v1_0"
        and clashes[0].get("other_clip_id") == "v1_1"
        and isinstance(clashes[0].get("score"), float)
        and isinstance(clashes[0].get("other_score"), float)
        and float(clashes[0]["score"]) < float(clashes[0]["other_score"]),
        clashes,
    )
    check(
        "sovrapposizione: lo scartato non riappare e resta nel referto",
        len([item for item in higher.broll if item.get("accepted") is False]) == 1
        and len([item for item in higher.broll if item.get("accepted") is True]) == 1,
        higher.broll,
    )

    # -- A11: equal scores keep the earlier window ------------------------ #
    tie_plan, tie = build(
        plan([("v1_0", 5.00, 1.00), ("v1_1", 1.90, 1.00)]), dense_words()
    )
    ties = reasons(tie, "broll_overlap_after_cut")
    tie_survivors = broll_clips(tie_plan)
    check(
        "sovrapposizione a pari score: resta il piu' antico",
        len(tie_survivors) == 1
        and tie_survivors[0].clip_id == "v1_0"
        and len(ties) == 1
        and ties[0].get("score") == ties[0].get("other_score") == 1.0,
        ties,
    )

    # -- A10: a plan without camera gets one camera_absent ---------------- #
    _, absent = build(plan([("v1_0", 1.30, 0.30)]), dense_words())
    absent_entries = reasons(absent, "camera_absent")
    check(
        "camera assente: una sola dichiarazione, non una per clip",
        len(absent_entries) == 1
        and reasons(absent, "camera_op_dropped") == []
        and reasons(absent, "camera_op_retimed") == []
        and reasons(absent, "camera_op_clamped") == [],
        absent_entries,
    )

    # -- A10: a camera op whose clip the bridge drops is declared --------- #
    _, camera_drop = build(
        plan(
            [("v1_0", 1.44, 0.60)],
            b_roll_camera=edl_plan.CameraSpec(angle_deg=30.0),
        ),
        [(2.00, 2.10, "parola", 0.60)],
    )
    ops = reasons(camera_drop, "camera_op_dropped")
    check(
        "camera op scartata: dichiarata con il tempo originale",
        len(ops) == 1
        and ops[0].get("clip_id") == "v1_0"
        and ops[0].get("original_dst_in") == 1.44
        and reasons(camera_drop, "camera_absent") == [],
        ops,
    )

    # -- A10: a surviving op is re-timed or clamped ----------------------- #
    clamped_plan, clamped = build(
        plan(
            [("v1_0", 1.30, 0.30)],
            a_roll_camera=edl_plan.CameraSpec(focal_length_mm=35.0),
        ),
        dense_words(),
    )
    clamped_ops = reasons(clamped, "camera_op_clamped")
    check(
        "camera op clampata: finestra originale e nuova dichiarate",
        len(clamped_ops) == 1
        and clamped_ops[0].get("original_dst_in") == 0.0
        and clamped_ops[0].get("original_dst_out") == 6.0
        and abs(float(clamped_ops[0]["dst_out"]) - DURATION) <= 1e-6,
        clamped_ops,
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
