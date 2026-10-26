"""Dual regime: STRICT executes the contract, AUTONOMOUS chooses and justifies."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import asset_triage, contract, decide  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:56} {detail}")


def main() -> int:
    strict = (
        "Reel 1080x1920, font Antonio, duotone #000000 / #FF3333, "
        "un taglio per battuta a 120 bpm, durata 15 sec, engine=pillow"
    )
    score, matches = decide.calculate_specificity_score(strict)
    check("strict: specificita' >= 0.7", score >= 0.7, score)
    check("strict: 7 parametri espliciti", len(matches) == 7, sorted(matches))
    payload = decide.parse_and_decide(strict)
    check(
        "strict: mode",
        payload["execution_context"]["mode"] == "strict",
        payload["execution_context"]["mode"],
    )
    check(
        "strict: contract_version",
        payload["contract_version"] == contract.VERSION,
        payload["contract_version"],
    )
    check("strict: aspect 9:16", payload["scene"]["aspect_ratio"] == "9:16")
    check(
        "strict: validator verde",
        decide.validate_scene_payload(payload) == [],
        decide.validate_scene_payload(payload),
    )

    ambiguous = (
        "Reel 1080x1920, font Antonio, stile vintage, 120 bpm, 15 sec, engine=pillow"
    )
    try:
        decide.parse_and_decide(ambiguous)
        check("strict: vintage non quantificato blocca", False, "no exception")
    except decide.AmbiguousContractError as exc:
        check(
            "strict: vintage non quantificato blocca",
            "[vague_vintage]" in str(exc),
            str(exc),
        )

    auto = decide.parse_and_decide("un reel con stile vintage e transizioni dinamiche")
    ctx = auto["execution_context"]
    rationale = ctx["creative_rationale"]
    check("autonomous: mode", ctx["mode"] == "autonomous", ctx["mode"])
    check(
        "autonomous: genre reel_promo",
        rationale["genre_detected"] == "reel_promo",
        rationale["genre_detected"],
    )
    check(
        "autonomous: duotone dedotto",
        rationale["color_palette"]["type"] == "duotone",
        rationale["color_palette"],
    )
    check(
        "autonomous: shadows dedotte",
        rationale["color_palette"]["shadows"] == "#1A100C",
        rationale["color_palette"].get("shadows"),
    )
    check(
        "autonomous: 120 BPM",
        rationale["pacing"]["bpm_target"] == 120,
        rationale["pacing"],
    )
    check("autonomous: 9:16 da reel", auto["scene"]["aspect_ratio"] == "9:16")
    check(
        "autonomous: piattaforma nota, niente R&D",
        rationale["rd_search_conducted"] is False,
        rationale["rd_search_conducted"],
    )

    unknown = decide.parse_and_decide(
        "Crea un video per schermo olografico promozionale"
    )
    unknown_rd = unknown["execution_context"]["creative_rationale"]
    check(
        "autonomous: target ignoto richiede R&D",
        unknown_rd["rd_search_required"] is True
        and unknown_rd["rd_search_conducted"] is False,
        unknown_rd,
    )
    check(
        "autonomous: validator verde",
        decide.validate_scene_payload(unknown) == [],
        decide.validate_scene_payload(unknown),
    )

    neutral = decide.parse_and_decide(
        "Reel 1080x1920, font Antonio, 120 bpm, 15 sec, engine=pillow"
    )
    neutral_color = neutral["execution_context"]["creative_rationale"]["color_palette"]
    check(
        "strict senza colore: type none", neutral_color["type"] == "none", neutral_color
    )
    check(
        "strict senza colore: rationale onesto",
        "nessun colore" in neutral_color["rationale"],
        neutral_color["rationale"],
    )

    fallback = decide.parse_and_decide("un reel con font BebasNeue", assets=["Antonio"])
    check(
        "autonomous: font mancante -> Antonio tracciato",
        fallback["execution_context"]["creative_rationale"]["typography"]["font_family"]
        == "Antonio",
        fallback["execution_context"]["creative_rationale"]["typography"],
    )

    missing_asset = (
        "Reel 1080x1920, font BebasNeue, #000000, 120 bpm, 15 sec, engine=pillow"
    )
    try:
        decide.parse_and_decide(missing_asset, assets=["Antonio"])
        check("strict: asset mancante blocca", False, "no exception")
    except decide.AmbiguousContractError as exc:
        check(
            "strict: asset mancante blocca",
            "[asset_missing]" in str(exc),
            str(exc),
        )

    forced_auto = decide.parse_and_decide(ambiguous, force_mode="autonomous")
    check(
        "force_mode autonomous: salta il gate strict",
        forced_auto["execution_context"]["mode"] == "autonomous",
        forced_auto["execution_context"]["mode"],
    )
    ambiguous_score, _ = decide.calculate_specificity_score(ambiguous)
    check(
        "force_mode non altera lo score",
        forced_auto["execution_context"]["specificity_score"] == ambiguous_score,
        forced_auto["execution_context"]["specificity_score"],
    )

    try:
        decide.parse_and_decide("un reel con stile vintage", force_mode="strict")
        check("force_mode strict: vintage blocca ancora", False, "no exception")
    except decide.AmbiguousContractError as exc:
        check(
            "force_mode strict: vintage blocca ancora",
            "[vague_vintage]" in str(exc),
            str(exc),
        )

    try:
        decide.parse_and_decide("un reel", force_mode="loose")
        check("force_mode invalido rifiutato", False, "no exception")
    except contract.VxError as exc:
        check("force_mode invalido rifiutato", "force_mode" in str(exc), str(exc))

    check(
        "senza pool nessun blocco asset_triage",
        "asset_triage" not in auto,
        sorted(auto),
    )

    music = asset_triage.AssetInfo(
        asset_id="music-1",
        kind="audio",
        path="music-1.wav",
        duration_sec=30.0,
        has_audio=True,
        role="music",
    )
    pooled = decide.parse_and_decide(
        "un reel con font BebasNeue", assets=["Antonio"], asset_pool=[music]
    )
    expected_block = asset_triage.triage_assets([music], (1080, 1920)).payload_block()
    check(
        "asset_pool: blocco identico a payload_block()",
        pooled.get("asset_triage") == expected_block,
        pooled.get("asset_triage"),
    )
    check(
        "asset_pool: validator verde",
        decide.validate_scene_payload(pooled) == [],
        decide.validate_scene_payload(pooled),
    )
    bad_block = copy.deepcopy(pooled)
    bad_block["asset_triage"]["selected_assets"][0]["role"] = ""
    check(
        "asset_triage: errore prefissato",
        any(
            error.startswith("asset_triage.selected_assets[0].role:")
            for error in decide.validate_scene_payload(bad_block)
        ),
        decide.validate_scene_payload(bad_block),
    )
    check(
        "asset_pool vuoto: nessun blocco",
        "asset_triage"
        not in decide.parse_and_decide("un reel con stile vintage", asset_pool=[]),
    )
    check(
        "asset_pool non tocca i font",
        pooled["execution_context"]["creative_rationale"]["typography"]["font_family"]
        == "Antonio",
        pooled["execution_context"]["creative_rationale"]["typography"],
    )

    broken = copy.deepcopy(auto)
    broken["execution_context"]["mode"] = "weird"
    check(
        "validator: mode invalido",
        any("mode" in error for error in decide.validate_scene_payload(broken)),
        decide.validate_scene_payload(broken),
    )

    broken_scene = copy.deepcopy(auto)
    broken_scene["scene"]["aspect_ratio"] = "3:2"
    check(
        "validator: aspect invalido",
        any(
            "aspect_ratio" in error
            for error in decide.validate_scene_payload(broken_scene)
        ),
        decide.validate_scene_payload(broken_scene),
    )

    broken_rd = copy.deepcopy(auto)
    broken_rd["execution_context"]["creative_rationale"]["rd_search_conducted"] = True
    check(
        "validator: R&D dichiarato senza fonti",
        any(
            "rd_search_sources" in error
            for error in decide.validate_scene_payload(broken_rd)
        ),
        decide.validate_scene_payload(broken_rd),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
