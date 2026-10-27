"""A6: reframe keyframes come from the face track, only from kept fragments.

python tests/test_bridge_reframe.py

No ffmpeg, no OpenCV, no real media: the track is synthetic and the loudness
measurement is a stub. The test proves the three laws the feature rests on:

  * every keyframe sits on a real track sample, shifted to destination time;
  * a sample inside a removed silence produces no keyframe, and the loss is
    declared;
  * a clip without a usable track is declared, never silent, and a list that
    cannot animate is skipped instead of emitted as a broken filter.
"""

from __future__ import annotations

import inspect
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import bridge, edl_plan, render  # noqa: E402

PASS = 0
FAIL = 0

SRC_W, SRC_H = 1920, 1080
SPEECH = [(0.0, 1.0), (1.8, 4.0), (4.6, 6.0)]
SILENCE = [(1.0, 1.8), (4.0, 4.6)]
STEP = 0.25
SAMPLE_TIMES = [round(index * STEP, 4) for index in range(int(6.0 / STEP) + 1)]


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def analysis() -> dict:
    return {
        "speech_regions": [{"start": s, "end": e} for s, e in SPEECH],
        "dead_air": [{"start": s, "end": e, "duration": e - s} for s, e in SILENCE],
    }


def plan() -> edl_plan.EDLPlan:
    source = {"c01": edl_plan.SourceRef(path="c01.mp4", sha256="a" * 64, duration=6.0)}
    video = edl_plan.ClipSegment(
        "v0_0", "c01", 0.0, 6.0, 0.0, 6.0, 0, "main_talking_head"
    )
    dialogue = edl_plan.ClipSegment("a0_0", "c01", 0.0, 6.0, 0.0, 6.0, 0, "dialogue")
    music = edl_plan.ClipSegment(
        "a1_0", "c01", 0.0, 6.0, 0.0, 6.0, 0, "music", None, None, -8.0
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


def track(static: bool = False, samples: bool = True) -> dict:
    """One synthetic face moving across a 1920x1080 frame."""
    rows = []
    for t in SAMPLE_TIMES:
        centre = 700.0 if static else 700.0 + 250.0 * math.sin(t * 2.0)
        rows.append(
            {
                "t": t,
                "x": round(centre - 150.0, 4),
                "y": 300.0,
                "w": 300.0,
                "h": 300.0,
                "eye_y": 380.0,
                "mar": 0.22 if int(t * 4) % 2 else 0.10,
                "score": 0.9,
            }
        )
    return {
        "tracks": [
            {
                "id": 0,
                "start": 0.0,
                "end": 6.0,
                "samples": rows if samples else [],
                "mean_area": 90000.0,
                "duration_s": 6.0,
                "centrality": 0.8,
                "score": 9.5,
                "persistence": 1.0,
            }
        ]
        if samples
        else [],
        "framing": "single",
        "span_x_ratio": 0.0,
        "width": SRC_W,
        "height": SRC_H,
    }


def build(faces: object) -> tuple:
    return bridge.build(
        plan(),
        {"c01": analysis()},
        target_lufs=-14.0,
        loudness_spans=lambda path, spans: {"input_i": -20.0},
        faces={"c01": faces},
    )


def layer0(new_plan: edl_plan.EDLPlan) -> list:
    return [
        clip
        for track_item in new_plan.video_tracks
        for clip in track_item.clips
        if int(clip.layer) == 0
    ]


def main() -> int:
    new_plan, report = build(track())
    clips = layer0(new_plan)
    check("un frammento per ogni taglio", len(clips) == 3, [c.clip_id for c in clips])
    check("una voce reframe per frammento", len(report.reframe) == len(clips))
    by_id = {item["clip_id"]: item for item in report.reframe}
    check(
        "ogni voce cita clip, sorgente e dichiarazione",
        all(
            item["clip_id"] in {clip.clip_id for clip in clips}
            and item["source_id"] == "c01"
            and bool(item["declared"])
            for item in report.reframe
        ),
    )

    inside, on_sample, moving = True, True, False
    for clip in clips:
        item = by_id[clip.clip_id]
        xs = [key["x"] for key in item["keyframes"]]
        moving = moving or (bool(xs) and max(xs) - min(xs) > 0.05)
        for key in item["keyframes"]:
            if not (clip.dst_in - 1e-6 <= key["dst_t"] <= clip.dst_out + 1e-6):
                inside = False
            source_t = clip.src_in + (key["dst_t"] - clip.dst_in)
            if not any(abs(source_t - value) <= 1e-3 for value in SAMPLE_TIMES):
                on_sample = False
    check("keyframe dentro il frammento, a tempo di destinazione", inside)
    check("nessun keyframe dove non c'e' un campione", on_sample)
    check("il centro si muove: la clip e' animata", moving)

    for clip in clips:
        item = by_id[clip.clip_id]
        count = sum(
            1
            for value in SAMPLE_TIMES
            if clip.src_in - 1e-6 <= value <= clip.src_out + 1e-6
        )
        check(
            f"frames <= campioni del frammento {clip.clip_id}",
            item["frames"] <= count and item["frames"] > 0,
            (item["frames"], count),
        )
        check(
            f"keyframe <= campioni (decimazione) {clip.clip_id}",
            len(item["keyframes"]) <= item["frames"],
            len(item["keyframes"]),
        )

    gaps = [
        (clips[index].src_out, clips[index + 1].src_in)
        for index in range(len(clips) - 1)
    ]
    orphans = sum(
        1
        for value in SAMPLE_TIMES
        if not any(clip.src_in - 1e-6 <= value <= clip.src_out + 1e-6 for clip in clips)
    )
    declared = [
        item
        for item in report.not_applied
        if item.get("reason") == "reframe_keyframes_in_silence"
    ]
    check(
        "i campioni nel silenzio sono dichiarati, non silenziosi",
        bool(declared) and declared[0]["count"] == orphans and orphans > 0,
        (orphans, declared),
    )
    keyed_sources = [
        clip.src_in + (key["dst_t"] - clip.dst_in)
        for clip in clips
        for key in by_id[clip.clip_id]["keyframes"]
    ]
    check(
        "nessun keyframe nasce dentro un buco fra due frammenti",
        all(
            not (start < value < end) for value in keyed_sources for start, end in gaps
        ),
        gaps,
    )
    check(
        "la scala e' quella del crop 9:16 su sorgente 16:9",
        all(
            key["scale"] == 1.0 for item in report.reframe for key in item["keyframes"]
        ),
        keyed_sources[:1],
    )

    _empty, empty_report = build({"tracks": [], "width": SRC_W, "height": SRC_H})
    check(
        "clip senza traccia: voce vuota, non silenzio",
        len(empty_report.reframe) == 3
        and all(item["keyframes"] == [] for item in empty_report.reframe),
        [item["frames"] for item in empty_report.reframe],
    )
    no_track = [
        item
        for item in empty_report.not_applied
        if item.get("reason") == "reframe_no_face_track"
    ]
    check(
        "clip senza traccia: not_applied per ogni clip",
        len(no_track) == 3,
        no_track[:1],
    )

    _static_plan, static_report = build(track(static=True))
    degenerate = [
        item
        for item in static_report.not_applied
        if item.get("reason") == "reframe_degenerate_keyframes"
    ]
    check(
        "keyframe degeneri scartati e dichiarati",
        len(degenerate) == 3
        and all(item["keyframes"] == [] for item in static_report.reframe),
        (len(degenerate), [item["detail"] for item in degenerate[:1]]),
    )

    _plain, plain_report = bridge.build(
        plan(),
        {"c01": analysis()},
        target_lufs=-14.0,
        loudness_spans=lambda path, spans: {"input_i": -20.0},
    )
    check("senza faces il reframe resta spento", plain_report.reframe == [])

    base = render.build_edl_filtergraph(new_plan, {"c01": 0}, has_audio=False)
    check(
        "default identico byte per byte",
        base == render.build_edl_filtergraph(new_plan, {"c01": 0}, has_audio=False),
    )
    check("default: nessun crop animato", "if(lt(" not in base, base[-90:])

    clip_id = clips[0].clip_id
    keyframes = by_id[clip_id]["keyframes"]
    graph = render.build_edl_filtergraph(
        new_plan, {"c01": 0}, has_audio=False, reframe={clip_id: keyframes}
    )
    animated = [part for part in graph.split(";") if "if(lt(" in part]
    check("una sola clip animata", len(animated) == 1, len(animated))
    check(
        "la clip animata usa cover + crop + rescale",
        "force_original_aspect_ratio=increase,crop=" in graph
        and "scale=1080:1920:flags=bicubic" in graph,
        graph[:120],
    )
    only_first = render.build_edl_filtergraph(
        new_plan, {"c01": 0}, has_audio=False, reframe={clip_id: keyframes[:1]}
    )
    check("un solo punto non e' una animazione", only_first == base)

    check(
        "render_edl accetta reframe con default None",
        inspect.signature(render.render_edl).parameters["reframe"].default is None,
    )
    check(
        "build_edl_filtergraph accetta reframe con default None",
        inspect.signature(render.build_edl_filtergraph).parameters["reframe"].default
        is None,
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
