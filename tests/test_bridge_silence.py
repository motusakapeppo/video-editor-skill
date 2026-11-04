"""A5: silence cuts come from the VAD spans and never leave them.

python tests/test_bridge_silence.py

No ffmpeg, no whisper, no real media: the analysis dict is synthetic and the
loudness measurement is a stub. The test proves the derivation (windows ->
removals -> keeps -> timeline), the hard gate, and the declared merge-back.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import bridge, contract, edl_plan  # noqa: E402

PASS = 0
FAIL = 0

SILENCES = [(1.0, 1.8), (4.0, 4.6)]
SPEECH = [(0.0, 1.0), (1.8, 4.0), (4.6, 6.0)]


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def analysis(speech=SPEECH) -> dict:
    return {
        "speech_regions": [{"start": s, "end": e} for s, e in speech],
        "dead_air": [{"start": s, "end": e, "duration": e - s} for s, e in SILENCES],
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


def stub_loudness(path, spans):
    return {"input_i": -20.0, "input_tp": -1.0, "input_lra": 5.0}


def raises(label: str, fn) -> None:
    try:
        fn()
    except contract.VxError as exc:
        check(label, True, str(exc)[:60])
    except Exception as exc:  # noqa: BLE001 - any other error is a failure
        check(label, False, f"{type(exc).__name__}: {exc}")
    else:
        check(label, False, "no exception")


def inside(t: float, spans) -> bool:
    return any(start <= t <= end for start, end in spans)


def main() -> int:
    original = plan()
    new_plan, report = bridge.build(
        original,
        {"c01": analysis()},
        target_lufs=-14.0,
        loudness_spans=stub_loudness,
    )

    check("plan valido dopo il taglio", new_plan.validate() == [], new_plan.validate())
    check(
        "input non mutato",
        len(original.video_tracks[0].clips) == 1
        and original.video_tracks[0].clips[0].dst_out == 6.0,
        [c.dst_out for c in original.video_tracks[0].clips],
    )

    video = list(new_plan.video_tracks[0].clips)
    dialogue = list(new_plan.audio_tracks[0].clips)
    check("tre frammenti tenuti", len(video) == 3, len(video))
    check(
        "taglio dentro lo span misurato",
        all(inside(cut["t"], SILENCES) for cut in report.cuts)
        and len(report.cuts) == 2,
        report.cuts,
    )
    check(
        "dst contigui da 0",
        all(
            abs(clip.dst_in - prev) <= 1e-6
            for clip, prev in zip(video[1:], [c.dst_out for c in video])
        )
        and video[0].dst_in == 0.0,
        [(c.dst_in, c.dst_out) for c in video],
    )
    total = sum(clip.src_out - clip.src_in for clip in video)
    check(
        "durata == somma dei frammenti",
        abs(new_plan.duration_sec - total) <= 1e-6
        and abs(new_plan.duration_sec - 5.08) <= 1e-6,
        (new_plan.duration_sec, total),
    )
    check(
        "nessun frammento contiene un silenzio rimosso",
        all(
            not any(
                clip.src_in < start + 1e-6 and clip.src_out > end - 1e-6
                for start, end in [(1.12, 1.68), (4.12, 4.48)]
            )
            for clip in video
        ),
        [(c.src_in, c.src_out) for c in video],
    )
    check(
        "dialogo specchia il video 1:1",
        len(dialogue) == len(video)
        and all(
            abs(a.dst_in - v.dst_in) <= 1e-9
            and abs(a.dst_out - v.dst_out) <= 1e-9
            and abs(a.src_in - v.src_in) <= 1e-9
            for a, v in zip(dialogue, video)
        ),
        [(a.dst_in, a.dst_out) for a in dialogue],
    )
    ids = [
        clip.clip_id
        for track in new_plan.video_tracks + new_plan.audio_tracks
        for clip in track.clips
    ]
    check("clip_id unici", len(ids) == len(set(ids)), ids)
    check(
        "not_applied sempre presente",
        "not_applied" in report.to_dict()
        and isinstance(report.to_dict()["not_applied"], list),
        sorted(report.to_dict()),
    )
    anchors = {anchor.anchor_id: anchor for anchor in report.anchors}
    check(
        "ogni taglio cita un'ancora che contiene il taglio",
        all(
            cut["anchor_id"] in anchors
            and anchors[cut["anchor_id"]].t0 <= cut["t"] <= anchors[cut["anchor_id"]].t1
            for cut in report.cuts
        ),
        [a.anchor_id for a in report.anchors],
    )

    with tempfile.TemporaryDirectory(prefix="vx-bridge-") as raw:
        path = report.write(Path(raw) / "bridge_report.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        check(
            "report scritto con not_applied",
            data.get("schema") == bridge.BRIDGE_SCHEMA
            and isinstance(data.get("not_applied"), list),
            data.get("schema"),
        )

    raises(
        "gate: rimozione fuori dallo span -> VxError",
        lambda: bridge.assert_removals_within(
            [{"start": 2.0, "end": 2.2}], [{"start": 1.0, "end": 1.8}]
        ),
    )

    patched = bridge._candidate_removals
    bridge._candidate_removals = lambda windows, pad, silence_min, src_in, src_out: [
        (2.0, 2.2)
    ]
    try:
        raises(
            "build: derivazione fuori span -> VxError",
            lambda: bridge.build(
                plan(),
                {"c01": analysis()},
                target_lufs=-14.0,
                loudness_spans=stub_loudness,
            ),
        )
    finally:
        bridge._candidate_removals = patched

    short = analysis(speech=[(0.0, 1.0), (1.8, 2.0), (2.6, 6.0)])
    merged_plan, merged_report = bridge.build(
        plan(), {"c01": short}, target_lufs=-14.0, loudness_spans=stub_loudness
    )
    merged_video = list(merged_plan.video_tracks[0].clips)
    declared = [
        item
        for item in merged_report.not_applied
        if item.get("reason") == "fragment_below_min"
    ]
    check(
        "frammento da 0.2s rientra nel vicino",
        len(merged_video) == 2
        and abs(merged_video[0].src_out - 2.12) <= 1e-6
        and abs(merged_video[1].src_in - 2.48) <= 1e-6
        and abs(merged_plan.duration_sec - 5.64) <= 1e-6,
        [(c.src_in, c.src_out) for c in merged_video],
    )
    check(
        "rientro dichiarato in not_applied",
        len(declared) == 1
        and declared[0].get("anchor_id", "").startswith("a_silence_")
        and abs(float(declared[0]["t0"]) - 1.68) <= 1e-6,
        declared,
    )
    check("un solo taglio applicato", len(merged_report.cuts) == 1, merged_report.cuts)

    pad = float(contract.THRESHOLDS["silence_pad"])
    silence_min = float(contract.THRESHOLDS["silence_min"])
    windows = [(0.0, 1.0), (5.0, 6.0)]
    derived = bridge._candidate_removals(windows, pad, silence_min, 0.0, 6.0)
    check(
        "finestra su src_in/src_out: pad solo sul lato con contenuto",
        len(derived) == 2
        and abs(derived[0][0] - 0.0) <= 1e-9
        and abs(derived[0][1] - 0.88) <= 1e-9
        and abs(derived[1][0] - 5.12) <= 1e-9
        and abs(derived[1][1] - 6.0) <= 1e-9,
        derived,
    )
    check(
        "il pad resta sul lato contenuto (coda 1.0-0.12, testa 5.0+0.12)",
        abs(derived[0][1] - (windows[0][1] - pad)) <= 1e-9
        and abs(derived[1][0] - (windows[1][0] + pad)) <= 1e-9,
        derived,
    )

    edge_analysis = {
        "speech_regions": [{"start": 1.0, "end": 5.0}],
        "dead_air": [
            {"start": 0.0, "end": 1.0, "duration": 1.0},
            {"start": 5.0, "end": 6.0, "duration": 1.0},
        ],
    }
    edge_plan, edge_report = bridge.build(
        plan(), {"c01": edge_analysis}, target_lufs=-14.0, loudness_spans=stub_loudness
    )
    edge = list(edge_plan.video_tracks[0].clips)
    check(
        "silenzio iniziale 0.0..1.0 tagliato per intero",
        len(edge) == 1
        and abs(edge[0].src_in - 0.88) <= 1e-6
        and abs(edge[0].dst_in - 0.0) <= 1e-6,
        [(c.src_in, c.src_out) for c in edge],
    )
    check(
        "silenzio finale 5.0..6.0 tagliato per intero",
        abs(edge[-1].src_out - 5.12) <= 1e-6
        and abs(edge_plan.duration_sec - 4.24) <= 1e-6,
        (edge[-1].src_out, edge_plan.duration_sec),
    )
    check(
        "i due tagli di testa e coda sono nel report",
        sorted(round(cut["t"], 3) for cut in edge_report.cuts) == [0.0, 5.12],
        edge_report.cuts,
    )
    check(
        "nessun rientro per frammento corto su testa e coda",
        not any(
            item.get("reason") == "fragment_below_min"
            for item in edge_report.not_applied
        )
        and edge_plan.validate() == [],
        edge_report.not_applied,
    )

    # Una finestra interamente silenziosa: il taglio non deve lasciare zero
    # frame (il job si fermava con "lost every frame"), ne' un frammento da
    # 0,7 ms. La finestra torna intera e ogni rinuncia e' dichiarata.
    import dataclasses

    base = plan()
    length = 0.521739
    whole = dataclasses.replace(
        base,
        duration_sec=length,
        video_tracks=(
            edl_plan.Track(
                0,
                (
                    dataclasses.replace(
                        base.video_tracks[0].clips[0],
                        src_out=length,
                        dst_out=length,
                    ),
                ),
                "a_roll",
            ),
        ),
        audio_tracks=(
            edl_plan.Track(
                0,
                (
                    dataclasses.replace(
                        base.audio_tracks[0].clips[0],
                        src_out=length,
                        dst_out=length,
                    ),
                ),
                "dialogue",
            ),
            edl_plan.Track(
                1,
                (
                    dataclasses.replace(
                        base.audio_tracks[1].clips[0],
                        src_out=length,
                        dst_out=length,
                    ),
                ),
                "music",
            ),
        ),
    )
    silent = {
        "speech_regions": [],
        "dead_air": [{"start": 0.0, "end": 0.521, "duration": 0.521}],
    }
    try:
        whole_plan, whole_report = bridge.build(
            whole, {"c01": silent}, target_lufs=-14.0, loudness_spans=stub_loudness
        )
    except contract.VxError as exc:
        check("finestra tutta silenziosa: nessun abort", False, str(exc)[:80])
        whole_plan, whole_report = None, None
    else:
        check("finestra tutta silenziosa: nessun abort", True)
    if whole_plan is not None:
        kept = list(whole_plan.video_tracks[0].clips)
        shortest = min(
            float(c.src_out) - float(c.src_in) for c in kept
        ) if kept else 0.0
        check(
            "finestra tutta silenziosa: la finestra torna intera",
            len(kept) == 1
            and abs(kept[0].src_in - 0.0) <= 1e-6
            and abs(kept[0].src_out - 0.521739) <= 1e-6
            and abs(whole_plan.duration_sec - 0.521739) <= 1e-6,
            [(c.src_in, c.src_out) for c in kept],
        )
        check(
            "finestra tutta silenziosa: nessun frammento sotto il minimo",
            shortest >= 0.05,
            shortest,
        )
        check(
            "finestra tutta silenziosa: dichiarata in not_applied",
            any(
                item.get("reason") in ("window_has_no_keep", "fragment_below_min")
                for item in whole_report.not_applied
            ),
            whole_report.not_applied,
        )
        check(
            "finestra tutta silenziosa: nessun taglio vantato",
            whole_report.cuts == [],
            whole_report.cuts,
        )
        check(
            "finestra tutta silenziosa: piano ancora valido",
            whole_plan.validate() == [],
            whole_plan.validate(),
        )

    # Copertura esatta: il silenzio arriva a fine finestra, non resta **nessun**
    # keep. E' il caso che abortiva il job ("lost every frame").
    covering = dict(silent)
    covering["dead_air"] = [{"start": 0.0, "end": 0.521739, "duration": 0.521739}]
    try:
        cover_plan, cover_report = bridge.build(
            whole, {"c01": covering}, target_lufs=-14.0, loudness_spans=stub_loudness
        )
    except contract.VxError as exc:
        check("silenzio che copre tutto: nessun abort", False, str(exc)[:80])
        cover_plan = None
    else:
        check("silenzio che copre tutto: nessun abort", True)
    if cover_plan is not None:
        kept = list(cover_plan.video_tracks[0].clips)
        check(
            "silenzio che copre tutto: la finestra torna intera",
            len(kept) == 1 and abs(kept[0].src_out - 0.521739) <= 1e-6,
            [(c.src_in, c.src_out) for c in kept],
        )
        check(
            "silenzio che copre tutto: dichiarato in not_applied",
            any(
                item.get("reason") in ("window_has_no_keep", "fragment_below_min")
                for item in cover_report.not_applied
            ),
            cover_report.not_applied,
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
