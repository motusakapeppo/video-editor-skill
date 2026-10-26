"""scene payload v1.5.0 + asset_triage -> EDLPlan vx.edl/2.2."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import asset_triage, contract, decide, edl_plan  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def _pool():
    return [
        asset_triage.AssetInfo(
            "a.mp4", "video", "a.mp4", 1080, 1920, 6.0, True, role="a_roll"
        ),
        asset_triage.AssetInfo(
            "b.png", "image", "b.png", 1080, 1920, 0.0, False, role="b_roll"
        ),
        asset_triage.AssetInfo(
            "m.m4a", "audio", "m.m4a", 0, 0, 10.0, True, role="music"
        ),
    ]


def _sources():
    return {
        "a.mp4": edl_plan.SourceRef(path="a.mp4", sha256="a" * 64, duration=6.0),
        "b.png": edl_plan.SourceRef(path="b.png", sha256="b" * 64, duration=3.0),
        "m.m4a": edl_plan.SourceRef(path="m.m4a", sha256="c" * 64, duration=10.0),
    }


def _media():
    return {
        "a.mp4": contract.ProbeResult(path="a.mp4", duration=6.0),
        "b.png": contract.ProbeResult(path="b.png", duration=3.0),
        "m.m4a": contract.ProbeResult(path="m.m4a", duration=10.0),
    }


def _raises(label: str, fn) -> None:
    try:
        fn()
    except contract.VxError as exc:
        check(label, True, str(exc)[:60])
    except Exception as exc:  # noqa: BLE001
        check(label, False, type(exc).__name__)
    else:
        check(label, False, "no exception")


def _payload(pool):
    payload = decide.parse_and_decide("un reel", asset_pool=pool)
    triage = asset_triage.triage_assets(pool, (1080, 1920), measure=lambda asset: {})
    payload["asset_triage"] = triage.payload_block()
    return payload


def main() -> int:
    pool = _pool()
    payload = _payload(pool)
    block = payload.get("asset_triage") or {}
    check("payload porta asset_triage", bool(block), sorted(block))
    block_errors = asset_triage.validate_payload_block(block)
    check("blocco triage valido", block_errors == [], block_errors)
    roles = [entry.get("role") for entry in block.get("selected_assets") or []]
    check(
        "triage seleziona a_roll/b_roll/music",
        sorted(roles) == ["a_roll", "b_roll", "music"],
        roles,
    )

    plan = contract.payload_to_edl_plan(payload, _sources(), _media(), fps=30.0)
    errors = plan.validate()
    check("plan valido", errors == [], errors)
    check(
        "contract_version 2.2.0",
        plan.contract_version == "2.2.0",
        plan.contract_version,
    )
    check("duration 6.0", plan.duration_sec == 6.0, plan.duration_sec)

    layer0 = plan.video_tracks[0].clips
    check("layer 0: un solo clip", len(layer0) == 1, len(layer0))
    check(
        "layer 0: main_talking_head 0..6",
        bool(layer0)
        and layer0[0].role == "main_talking_head"
        and (layer0[0].dst_in, layer0[0].dst_out) == (0.0, 6.0),
        (layer0[0].role, layer0[0].dst_in, layer0[0].dst_out) if layer0 else None,
    )

    dialogue = plan.audio_tracks[0]
    dclip = dialogue.clips[0] if dialogue.clips else None
    check("bus dialogo: ruolo dialogue", dialogue.role == "dialogue", dialogue.role)
    check(
        "bus dialogo: specchia A-roll",
        dclip is not None
        and dclip.source_id == "a.mp4"
        and dclip.role == "dialogue"
        and (dclip.src_in, dclip.src_out) == (0.0, 6.0)
        and (dclip.dst_in, dclip.dst_out) == (0.0, 6.0),
        dclip,
    )

    music = plan.audio_tracks[1]
    mclip = music.clips[0] if music.clips else None
    check("traccia musica: ruolo music", music.role == "music", music.role)
    check(
        "musica: gain -8.0 e dst 0..6",
        mclip is not None
        and mclip.role == "music"
        and mclip.gain_db == -8.0
        and (mclip.dst_in, mclip.dst_out) == (0.0, 6.0),
        mclip,
    )
    check("ducking dichiarato", plan.ducking == edl_plan.DuckingSpec(), plan.ducking)

    layer1 = plan.video_tracks[1].clips
    bclip = layer1[0] if layer1 else None
    check("layer 1: un b_roll", len(layer1) == 1, len(layer1))
    check(
        "b_roll: fade 0.5",
        bclip is not None
        and bclip.role == "b_roll"
        and bclip.transition_in is not None
        and bclip.transition_in.type == "fade"
        and bclip.transition_in.duration_sec == 0.5,
        bclip.transition_in if bclip else None,
    )
    check(
        "sources: solo i tre riferiti",
        set(plan.sources) == {"a.mp4", "b.png", "m.m4a"},
        sorted(plan.sources),
    )

    bad = asset_triage.AssetInfo(
        "bad.mp4",
        "video",
        "bad.mp4",
        1080,
        1920,
        6.0,
        True,
        role="a_roll",
        metrics={"probe_error": "boom"},
    )
    bad_payload = _payload(pool + [bad])
    bad_block = bad_payload.get("asset_triage") or {}
    rejected_ids = [
        entry.get("asset_id") for entry in bad_block.get("rejected_assets") or []
    ]
    check("asset illeggibile scartato", "bad.mp4" in rejected_ids, rejected_ids)
    bad_plan = contract.payload_to_edl_plan(bad_payload, _sources(), _media(), fps=30.0)
    clip_sources = {
        clip.source_id
        for track in bad_plan.video_tracks + bad_plan.audio_tracks
        for clip in track.clips
    }
    check(
        "scartato fuori da sources e clip",
        "bad.mp4" not in bad_plan.sources and "bad.mp4" not in clip_sources,
        sorted(clip_sources),
    )
    check(
        "nota scarti ignorati: 1",
        any("asset scartati ignorati: 1" in note for note in bad_plan.notes),
        bad_plan.notes,
    )

    round_trip = edl_plan.EDLPlan.from_dict(plan.to_dict()).to_dict()
    check("round-trip identico", round_trip == plan.to_dict(), "from_dict(to_dict())")
    again = contract.payload_to_edl_plan(payload, _sources(), _media(), fps=30.0)
    check("deterministico", again.to_dict() == plan.to_dict(), "due chiamate")

    anchored = contract.payload_to_edl_plan(
        payload, _sources(), _media(), fps=30.0, anchors=[("b.png", 4.5)]
    )
    aclip = anchored.video_tracks[1].clips[0]
    check(
        "ancora 4.5 clampata a 3.0",
        (aclip.dst_in, aclip.dst_out) == (3.0, 6.0),
        f"clamp: dst_in={aclip.dst_in}",
    )

    no_block = dict(payload)
    no_block.pop("asset_triage", None)
    _raises(
        "payload senza asset_triage",
        lambda: contract.payload_to_edl_plan(no_block, _sources(), _media(), fps=30.0),
    )

    missing_source = _sources()
    missing_source.pop("a.mp4")
    _raises(
        "a_roll senza SourceRef",
        lambda: contract.payload_to_edl_plan(
            payload, missing_source, _media(), fps=30.0
        ),
    )

    no_a = dict(payload)
    no_a["asset_triage"] = {
        "selected_assets": [
            entry
            for entry in block.get("selected_assets") or []
            if entry.get("role") != "a_roll"
        ],
        "rejected_assets": block.get("rejected_assets") or [],
    }
    _raises(
        "nessun a_roll",
        lambda: contract.payload_to_edl_plan(no_a, _sources(), _media(), fps=30.0),
    )

    two_music = dict(payload)
    two_music["asset_triage"] = {
        "selected_assets": list(block.get("selected_assets") or [])
        + [{"asset_id": "m2.m4a", "role": "music", "target_duration_sec": 10.0}],
        "rejected_assets": [],
    }
    _raises(
        "due tracce musicali",
        lambda: contract.payload_to_edl_plan(two_music, _sources(), _media(), fps=30.0),
    )

    short_pool = [
        asset_triage.AssetInfo(
            "a.mp4", "video", "a.mp4", 1080, 1920, 2.0, True, role="a_roll"
        ),
        asset_triage.AssetInfo(
            "b.png", "image", "b.png", 1080, 1920, 0.0, False, role="b_roll"
        ),
    ]
    short_plan = contract.payload_to_edl_plan(
        _payload(short_pool),
        {
            "a.mp4": edl_plan.SourceRef(path="a.mp4", sha256="a" * 64, duration=2.0),
            "b.png": edl_plan.SourceRef(path="b.png", sha256="b" * 64, duration=3.0),
        },
        {
            "a.mp4": contract.ProbeResult(path="a.mp4", duration=2.0),
            "b.png": contract.ProbeResult(path="b.png", duration=3.0),
        },
        fps=25.0,
    )
    short_b = short_plan.video_tracks[1].clips[0]
    check(
        "b-roll più lungo della timeline viene accorciato",
        short_plan.validate() == []
        and abs((short_b.dst_out - short_b.dst_in) - 2.0) < 1e-9,
        (short_b.dst_in, short_b.dst_out),
    )

    music_pool = [
        asset_triage.AssetInfo(
            "a.mp4", "video", "a.mp4", 1080, 1920, 6.0, True, role="a_roll"
        ),
        asset_triage.AssetInfo(
            "m.m4a", "audio", "m.m4a", 0, 0, 1.0, True, role="music"
        ),
    ]
    music_plan = contract.payload_to_edl_plan(
        _payload(music_pool),
        {
            "a.mp4": edl_plan.SourceRef(path="a.mp4", sha256="a" * 64, duration=6.0),
            "m.m4a": edl_plan.SourceRef(path="m.m4a", sha256="c" * 64, duration=1.0),
        },
        {
            "a.mp4": contract.ProbeResult(path="a.mp4", duration=6.0),
            "m.m4a": contract.ProbeResult(path="m.m4a", duration=1.0),
        },
        fps=25.0,
    )
    short_music = music_plan.audio_tracks[1].clips[0]
    check(
        "musica più corta: clip accorciato al media",
        music_plan.validate() == [] and abs(short_music.dst_out - 1.0) < 1e-9,
        (short_music.src_out, short_music.dst_out),
    )
    check(
        "musica più corta: carenza dichiarata nelle note",
        any("più corta della timeline" in note for note in music_plan.notes),
        list(music_plan.notes),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
