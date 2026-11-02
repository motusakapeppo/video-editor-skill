"""D-27: la durata chiesta nel prompt non si butta piu'.

S1 - "30 secondi" diventa `scene.duration_sec = 30`; un prompt senza durata non
     aggiunge il campo, cosi' i payload di prima restano identici.
S2 - il contratto usa quel numero come **tetto**: con due sorgenti da 60 s e una
     richiesta di 10 s, il piano dura 10 s e non 120.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import contract, decide  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def payload(seconds: int, clips: int = 1) -> dict:
    selected = [
        {"asset_id": f"a{index}.mp4", "role": "a_roll", "target_duration_sec": 60.0}
        for index in range(clips)
    ]
    return {
        "contract_version": contract.VERSION,
        "scene": {
            "aspect_ratio": "9:16",
            "resolution": [1080, 1920],
            "engine": "pillow",
            **({"duration_sec": seconds} if seconds else {}),
        },
        "asset_triage": {"selected_assets": selected, "rejected_assets": []},
    }


def plan_for(seconds: int, clips: int = 1):
    sources = {
        f"a{index}.mp4": {
            "path": f"a{index}.mp4",
            "sha256": "0" * 64,
            "duration": 60.0,
        }
        for index in range(clips)
    }
    media = {f"a{index}.mp4": {"duration": 60.0} for index in range(clips)}
    return contract.payload_to_edl_plan(
        payload(seconds, clips), sources, media, fps=30.0
    )


def main() -> int:
    found = decide.parse_and_decide(
        "reel verticale 9:16 da 30 secondi", force_mode="autonomous"
    )
    check(
        "S1 the requested duration reaches the contract",
        found["scene"].get("duration_sec") == 30.0,
        found["scene"].get("duration_sec"),
    )
    without = decide.parse_and_decide("montaggio senza durata", force_mode="autonomous")
    check(
        "S1 no duration, no field: the payload stays as it was",
        "duration_sec" not in (without.get("scene") or {}),
        without.get("scene"),
    )

    capped = plan_for(10, clips=2)
    check(
        "S2 the contract caps the timeline at the requested length",
        abs(float(capped.duration_sec) - 10.0) < 1e-6,
        capped.duration_sec,
    )
    free = plan_for(0, clips=2)
    check(
        "S2 without a request the plan keeps the sources",
        abs(float(free.duration_sec) - 120.0) < 1e-6,
        free.duration_sec,
    )

    from vx import auto as auto_mod

    short = auto_mod.duration_shortfall(60, 21.09)
    check(
        "S3 a delivery shorter than the request is declared with both numbers",
        short is not None and abs(short["shortfall_sec"] - 38.91) < 0.01,
        short,
    )
    check(
        "S3 a delivery that matches is not a shortfall",
        auto_mod.duration_shortfall(30, 30.016) is None,
        auto_mod.duration_shortfall(30, 30.016),
    )
    check(
        "S3 no request, nothing to declare",
        auto_mod.duration_shortfall(None, 21) is None,
        "",
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
