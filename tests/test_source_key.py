"""Real-world filenames become valid, unique, deterministic EDL source keys.

The triage report keeps the asset id exactly as discovery produced it
("WhatsApp Video 2026-10-26 at 22.59.10.mp4"); the EDL may only carry
``[A-Za-z0-9_.-]+``. ``contract.source_key`` is the projection between the two
and ``payload_to_edl_plan`` applies it once, in payload order.

No ffmpeg, no probe: a fake ``probe_asset`` yields the geometry the real one
would, so ``discover_assets`` still names the assets from the directory listing.

python tests/test_source_key.py
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import asset_triage, contract, edl_plan  # noqa: E402

PASS = 0
FAIL = 0

KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

WHATSAPP = "WhatsApp Video 2026-10-26 at 22.59.10.mp4"
PARENS = "clip (1).mp4"
ACCENTS = "perché è così.mp4"
CLEANISH = "ok_name-1.2.mp4"
MUSIC = "perché è così.m4a"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def _asset(
    asset_id: str, *, role=None, kind: str = "video", duration: float = 4.0
) -> asset_triage.AssetInfo:
    return asset_triage.AssetInfo(
        asset_id=asset_id,
        kind=kind,
        path=asset_id,
        width=1080,
        height=1920,
        duration_sec=duration,
        has_audio=True,
        role=role,
    )


def _payload(assets, *, montage: bool = False):
    triage = asset_triage.triage_assets(
        assets, (1080, 1920), measure=lambda _asset: {}, montage=montage
    )
    return {
        "contract_version": contract.VERSION,
        "scene": {"aspect_ratio": "9:16", "resolution": [1080, 1920]},
        "asset_triage": triage.payload_block(),
    }


def _bridge(payload, assets):
    sources = {
        asset.asset_id: edl_plan.SourceRef(
            path=asset.path, sha256="a" * 64, duration=asset.duration_sec
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


def _clip_keys(plan):
    return [
        clip.source_id
        for track in plan.video_tracks + plan.audio_tracks
        for clip in track.clips
    ]


class _Staged:
    """The one attribute ``auto._staged_path`` reads from an IngestResult."""

    def __init__(self, staged_path: str) -> None:
        self.staged_path = staged_path


def _fake_probe(path):
    return contract.ProbeResult(
        path=str(path),
        duration=4.0,
        fps=30.0,
        width=1080,
        height=1920,
        has_audio=True,
        kind="video",
    )


def main() -> int:
    # -- the helper itself -------------------------------------------------- #
    check(
        "helper: WhatsApp -> slug",
        contract.source_key(WHATSAPP) == "WhatsApp_Video_2026-10-26_at_22.59.10.mp4",
        contract.source_key(WHATSAPP),
    )
    check(
        "helper: parentesi -> slug",
        contract.source_key(PARENS) == "clip_1_.mp4",
        contract.source_key(PARENS),
    )
    check(
        "helper: accenti -> slug",
        contract.source_key(ACCENTS) == "perch_cos_.mp4",
        contract.source_key(ACCENTS),
    )
    check(
        "helper: nome pulito invariato",
        contract.source_key(CLEANISH) == CLEANISH,
        contract.source_key(CLEANISH),
    )
    check(
        "helper: solo caratteri illeciti -> asset",
        contract.source_key("  ...  ") == "asset",
        contract.source_key("  ...  "),
    )
    taken = set()
    first = contract.source_key("a b.mp4", taken)
    taken.add(first)
    second = contract.source_key("a_b.mp4", taken)
    check(
        "helper: collisione suffissata",
        (first, second) == ("a_b.mp4", "a_b.mp4_2"),
        (first, second),
    )
    taken.add(second)
    check(
        "helper: terza collisione _3",
        contract.source_key("a+b.mp4", taken) == "a_b.mp4_3",
        contract.source_key("a+b.mp4", taken),
    )
    try:
        contract.source_key("")
    except contract.VxError:
        check("helper: asset_id vuoto rifiutato", True)
    except Exception as exc:  # noqa: BLE001
        check("helper: asset_id vuoto rifiutato", False, type(exc).__name__)
    else:
        check("helper: asset_id vuoto rifiutato", False, "no exception")

    # -- payload with the four real-world names ----------------------------- #
    names = [WHATSAPP, PARENS, ACCENTS, CLEANISH]
    assets = [_asset(name) for name in names]
    payload = _payload(assets, montage=True)
    block = payload["asset_triage"]
    selected_ids = [entry["asset_id"] for entry in block["selected_assets"]]
    check(
        "payload: tutti e quattro gli asset",
        sorted(selected_ids) == sorted(names),
        selected_ids,
    )

    plan = _bridge(payload, assets)
    errors = plan.validate()
    check("EDL valido con nomi reali", errors == [], errors)
    keys = list(plan.sources)
    check("sources: chiavi legali", all(KEY_RE.match(key) for key in keys), keys)
    check(
        "ogni clip risolve la sua chiave",
        all(key in plan.sources for key in _clip_keys(plan)),
        sorted(set(_clip_keys(plan))),
    )
    check("quattro chiavi distinte", len(set(keys)) == len(keys) == 4, keys)
    check(
        "report: asset id intatti",
        set(selected_ids) == set(names),
        "nomi originali nel payload",
    )
    check(
        "report: il nome WhatsApp resta umano",
        WHATSAPP in selected_ids and WHATSAPP not in plan.sources,
        selected_ids,
    )

    again = _bridge(payload, assets)
    check(
        "deterministico: to_dict identico",
        again.to_dict() == plan.to_dict(),
        "due build",
    )

    # -- collision inside one payload --------------------------------------- #
    colliding = [_asset("a b.mp4"), _asset("a_b.mp4")]
    collision_payload = _payload(colliding, montage=True)
    collision_plan = _bridge(collision_payload, colliding)
    collision_keys = sorted(collision_plan.sources)
    check(
        "collisione: due chiavi distinte",
        collision_keys == ["a_b.mp4", "a_b.mp4_2"],
        collision_keys,
    )
    check(
        "collisione: entrambe le clip risolvono",
        collision_plan.validate() == []
        and set(_clip_keys(collision_plan)) == set(collision_keys),
        _clip_keys(collision_plan),
    )
    check(
        "collisione: deterministica",
        _bridge(collision_payload, colliding).to_dict() == collision_plan.to_dict(),
        "due build",
    )

    # -- b_roll and music go through the same mapping ----------------------- #
    mixed = [
        _asset(WHATSAPP, role="a_roll"),
        _asset(PARENS, role="b_roll"),
        _asset(MUSIC, role="music", kind="audio", duration=8.0),
    ]
    mixed_plan = _bridge(_payload(mixed), mixed)
    music_key = contract.source_key(MUSIC)
    broll_key = contract.source_key(PARENS)
    check(
        "b_roll e music usano le stesse chiavi",
        mixed_plan.validate() == []
        and broll_key in mixed_plan.sources
        and music_key in mixed_plan.sources
        and PARENS not in mixed_plan.sources,
        sorted(mixed_plan.sources),
    )
    check(
        "musica: clip sulla chiave slug",
        mixed_plan.audio_tracks[1].clips[0].source_id == music_key,
        mixed_plan.audio_tracks[1].clips[0].source_id,
    )

    # -- clean names keep the key they always had --------------------------- #
    clean_plan = _bridge(_payload([_asset("c01.mp4")]), [_asset("c01.mp4")])
    check(
        "c01.mp4 conserva la chiave storica",
        list(clean_plan.sources) == ["c01.mp4"]
        and clean_plan.video_tracks[0].clips[0].source_id == "c01.mp4",
        list(clean_plan.sources),
    )

    # -- the real failing shape: a listing of eight WhatsApp files ---------- #
    listing_names = [
        f"WhatsApp Video 2026-10-26 at 22.5{index}.10.mp4" for index in range(8)
    ]
    original_probe = asset_triage.probe.probe_asset
    asset_triage.probe.probe_asset = _fake_probe
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, name in enumerate(listing_names):
                (root / name).write_bytes(b"x" * (64 + index))
            listing = asset_triage.discover_assets(root, measure=lambda _asset: {})
        listing_ids = [asset.asset_id for asset in listing]
        listing_plan = _bridge(_payload(listing, montage=True), listing)
    finally:
        asset_triage.probe.probe_asset = original_probe

    check(
        "discover: asset id = nome file",
        sorted(listing_ids) == sorted(listing_names),
        listing_ids,
    )
    check(
        "listing reale: EDL valido",
        listing_plan.validate() == [],
        listing_plan.validate(),
    )
    check(
        "listing reale: 8 chiavi legali e distinte",
        len(listing_plan.sources) == 8
        and len(set(listing_plan.sources)) == 8
        and all(KEY_RE.match(key) for key in listing_plan.sources),
        sorted(listing_plan.sources),
    )

    # -- the auto pipeline looks media up by clip.source_id, so it re-keys ---- #
    from vx import auto  # noqa: E402  (heavy import: only this check needs it)

    media_info_map = {
        asset.asset_id: {"duration": 4.0, "width": 1080, "height": 1920}
        for asset in assets
    }
    staged = {asset.asset_id: _Staged(asset.asset_id) for asset in assets}
    rekeyed = auto._media_by_source_key(plan, staged, media_info_map)
    check(
        "auto: media riallineati alle chiavi EDL",
        set(rekeyed) == set(plan.sources)
        and all(rekeyed[key]["width"] == 1080 for key in rekeyed),
        sorted(rekeyed),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
