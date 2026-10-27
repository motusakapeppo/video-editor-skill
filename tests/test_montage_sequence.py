"""Montage A1: eight clips become eight contiguous layer-0 a_roll clips.

The default triage admits exactly one dialogue-carrying video as a_roll. The
montage mode lifts only that uniqueness rule: every video that clears the
existing gates keeps role ``a_roll``, in discovery order, and the bridge chains
them on layer 0 with ``dst_in``/``dst_out`` accumulating to the exact sum of
their durations. No transition, no effect, ``src_in`` stays 0.0.

python tests/test_montage_sequence.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import asset_triage as at  # noqa: E402
from vx import contract  # noqa: E402
from vx.edl_plan import SourceRef  # noqa: E402

PASS = 0
FAIL = 0

TARGET = (1080, 1920)
DURATIONS = (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def _asset(index: int, duration: float, **kwargs) -> at.AssetInfo:
    payload = {
        "asset_id": f"clip_{index:02d}.mp4",
        "kind": "video",
        "path": f"/pool/clip_{index:02d}.mp4",
        "width": 1080,
        "height": 1920,
        "duration_sec": float(duration),
        "has_audio": True,
    }
    payload.update(kwargs)
    return at.AssetInfo(**payload)


def _pool(durations=DURATIONS) -> list:
    return [_asset(index + 1, duration) for index, duration in enumerate(durations)]


def _payload(assets, *, montage: bool):
    """Synthetic payload: no measurement, so only the declared geometry is read."""
    triage = at.triage_assets(
        assets, TARGET, measure=lambda _asset: {}, montage=montage
    )
    block = triage.payload_block()
    payload = {
        "contract_version": contract.VERSION,
        "scene": {"aspect_ratio": "9:16", "resolution": list(TARGET)},
        "asset_triage": block,
    }
    return payload, block


def _bridge(payload, assets):
    sources = {
        asset.asset_id: SourceRef(
            asset.path,
            hashlib.sha256(asset.asset_id.encode("utf-8")).hexdigest(),
            asset.duration_sec,
        )
        for asset in assets
    }
    media = {
        asset.asset_id: {
            "duration": float(asset.duration_sec),
            "width": int(asset.width),
            "height": int(asset.height),
            "fps": 30.0,
            "has_audio": bool(asset.has_audio),
        }
        for asset in assets
    }
    return contract.payload_to_edl_plan(payload, sources, media, fps=30.0)


def _layer0(plan) -> list:
    return [
        clip
        for track in plan.video_tracks
        for clip in track.clips
        if int(clip.layer) == 0
    ]


def main() -> int:
    assets = _pool()

    # -- montage: every eligible video is a_roll, in discovery order -------- #
    payload, block = _payload(assets, montage=True)
    roles = [entry["role"] for entry in block["selected_assets"]]
    check(
        "montage: 8 a_roll nel payload",
        roles == ["a_roll"] * 8,
        roles,
    )
    check(
        "montage: nessun b_roll residuo",
        "b_roll" not in roles and not block["rejected_assets"],
        roles,
    )

    plan = _bridge(payload, assets)
    clips = _layer0(plan)
    check("montage: 8 clip su layer 0", len(clips) == 8, len(clips))
    check(
        "montage: tutte a layer 0",
        all(int(clip.layer) == 0 for clip in clips),
        [int(clip.layer) for clip in clips],
    )
    check(
        "montage: src_in a 0.0",
        all(float(clip.src_in) == 0.0 for clip in clips),
        [float(clip.src_in) for clip in clips],
    )
    check(
        "montage: sorgenti in ordine di scoperta",
        [clip.source_id for clip in clips] == [item.asset_id for item in assets],
        [clip.source_id for clip in clips],
    )

    contiguous = all(
        abs(float(current.dst_in) - float(previous.dst_out)) <= 1e-9
        for previous, current in zip(clips, clips[1:])
    )
    check(
        "montage: dst_in/dst_out contigui",
        contiguous and float(clips[0].dst_in) == 0.0,
        [(float(c.dst_in), float(c.dst_out)) for c in clips],
    )
    check(
        "montage: durata = somma dei clip",
        abs(float(plan.duration_sec) - sum(DURATIONS)) <= 1e-9,
        (float(plan.duration_sec), sum(DURATIONS)),
    )
    check(
        "montage: durata = somma dei segmenti",
        abs(
            sum(float(c.dst_out) - float(c.dst_in) for c in clips)
            - float(plan.duration_sec)
        )
        <= 1e-9,
        [round(float(c.dst_out) - float(c.dst_in), 3) for c in clips],
    )
    check("montage: EDL valido", plan.validate() == [], plan.validate())
    check(
        "montage: nessuna transizione",
        all(
            clip.transition_in is None and clip.transition_out is None for clip in clips
        ),
        "transitions",
    )
    dialogue = [
        clip
        for track in plan.audio_tracks
        if track.role == "dialogue"
        for clip in track.clips
    ]
    check(
        "montage: bus dialogo rispecchia 8 clip",
        len(dialogue) == 8
        and [clip.source_id for clip in dialogue] == [item.asset_id for item in assets],
        len(dialogue),
    )

    # -- default path: exactly one a_roll ---------------------------------- #
    payload_default, block_default = _payload(assets, montage=False)
    default_roles = [entry["role"] for entry in block_default["selected_assets"]]
    check(
        "default: un solo a_roll",
        default_roles.count("a_roll") == 1,
        default_roles,
    )
    check(
        "default: gli altri restano b_roll, il cap li conta",
        default_roles.count("b_roll") == 3
        and len(block_default["rejected_assets"]) == 4,
        (default_roles, len(block_default["rejected_assets"])),
    )
    # The bridge refuses overlapping b-roll: exercise it on a 2-clip pool.
    pair = _pool((1.5, 2.0))
    payload_pair, _ = _payload(pair, montage=False)
    default_plan = _bridge(payload_pair, pair)
    check(
        "default: una sola clip su layer 0",
        len(_layer0(default_plan)) == 1,
        len(_layer0(default_plan)),
    )

    # -- rejected candidates keep their own code --------------------------- #
    mixed = _pool()
    mixed[3] = _asset(4, 0.4)
    _, block_mixed = _payload(mixed, montage=True)
    reasons = {
        item["asset_id"]: item["rejection_reason"]
        for item in block_mixed["rejected_assets"]
    }
    check(
        "montage: il clip corto resta TOO_SHORT",
        reasons.get("clip_04.mp4") == "TOO_SHORT",
        reasons,
    )
    check(
        "montage: gli altri 7 restano a_roll",
        [entry["role"] for entry in block_mixed["selected_assets"]] == ["a_roll"] * 7,
        [entry["role"] for entry in block_mixed["selected_assets"]],
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
