"""scene_payload -> EditPlan: execute the duotone, declare what cannot be executed."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import asset_triage, contract, decide, render  # noqa: E402

PASS = 0
FAIL = 0
MEDIA = {"duration": 10.0, "width": 1920, "height": 1080, "has_audio": True}


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:58} {detail}")


def main() -> int:
    payload = decide.parse_and_decide(
        "un reel con stile vintage e transizioni dinamiche"
    )
    plan = contract.payload_to_edit_plan(payload, MEDIA)
    check("output aspect 9:16", plan.output.get("aspect") == "9:16", plan.output)
    check(
        "output 1080x1920",
        (plan.output.get("width"), plan.output.get("height")) == (1080, 1920),
        plan.output,
    )
    check(
        "tagli sulla griglia di battuta, non un taglio unico",
        len(plan.cuts) > 1
        and all(cut.get("provenance", {}).get("kind") == "beat" for cut in plan.cuts[:-1]),
        f"{len(plan.cuts)} tagli",
    )
    check(
        "copertura contigua della sorgente",
        plan.cuts[0]["in"] == 0.0
        and plan.cuts[-1]["out"] == 10.0
        and all(
            abs(a["out"] - b["in"]) < 1e-6
            for a, b in zip(plan.cuts, plan.cuts[1:])
        ),
        (plan.cuts[0]["in"], plan.cuts[-1]["out"]),
    )

    color = next((t for t in plan.treatments if t.get("name") == "color_fix"), None)
    check("color_fix presente e attivo", bool(color and color.get("enabled")), color)
    filter_text = ((color or {}).get("params") or {}).get("filter", "")
    check(
        "il duotone e' un filtro eseguibile",
        "hue=s=0" in filter_text and "curves=" in filter_text,
        filter_text,
    )
    check(
        "il render compone davvero il filtro",
        "curves=" in (render._eq_chain(plan) or ""),
        render._eq_chain(plan),
    )
    check(
        "evidence autonoma",
        (color or {}).get("evidence")
        == [{"finding_id": "brief.autonomous", "t0": 0.0, "t1": 10.0}],
        (color or {}).get("evidence"),
    )

    finding = (plan.brief.get("autonomous_findings") or [None])[0] or {}
    check("finding dichiarato", finding.get("declared") is True, finding)
    check("span del finding", finding.get("span") == [0.0, 10.0], finding.get("span"))

    report = {"source": {"duration": 10.0}, "findings": [finding]}
    errors = contract.validate_plan(plan.to_dict(), report)
    check("validate_plan accetta il bridge", errors == [], errors)
    missing = contract.validate_plan(
        plan.to_dict(), {"source": {"duration": 10.0}, "findings": []}
    )
    check("senza il finding il piano e' invalido", bool(missing), missing)

    bridge = plan.brief.get("bridge") or {}
    unsupported = {item.get("field") for item in bridge.get("unsupported") or []}
    check(
        "BPM dichiarato eseguito, non rimandato",
        "pacing.bpm_target" not in unsupported
        and "beat_grid" in (bridge.get("executed") or []),
        (unsupported, bridge.get("executed")),
    )
    check(
        "font dichiarato non eseguito",
        "typography.font_family" in unsupported,
        unsupported,
    )
    check(
        "output e cut eseguiti", {"output", "cut"} <= set(bridge.get("executed") or [])
    )
    check(
        "payload senza asset_triage resta valido",
        decide.validate_scene_payload(payload) == [],
        decide.validate_scene_payload(payload),
    )

    music = asset_triage.AssetInfo(
        asset_id="music-1",
        kind="audio",
        path="music-1.wav",
        duration_sec=30.0,
        has_audio=True,
        role="music",
    )
    pooled = decide.parse_and_decide("un reel con stile vintage", asset_pool=[music])
    pooled_plan = contract.payload_to_edit_plan(pooled, MEDIA)
    pooled_bridge = pooled_plan.brief.get("bridge") or {}
    pooled_unsupported = {
        item.get("field"): item.get("reason")
        for item in pooled_bridge.get("unsupported") or []
    }
    check(
        "asset_triage dichiarato non eseguito",
        "asset_triage" in pooled_unsupported,
        sorted(pooled_unsupported),
    )
    check(
        "reason nomina la timeline multitrack",
        "multitrack" in (pooled_unsupported.get("asset_triage") or ""),
        pooled_unsupported.get("asset_triage"),
    )

    strict = decide.parse_and_decide(
        "Reel 1080x1920, font Antonio, duotone #000000 / #FF3333, "
        "un taglio per battuta a 120 bpm, durata 15 sec, engine=pillow"
    )
    strict_plan = contract.payload_to_edit_plan(
        strict, {"duration": 15.0, "width": 1920, "height": 1080}
    )
    check(
        "strict con colore custom: nessun color_fix",
        not any(t.get("name") == "color_fix" for t in strict_plan.treatments),
        strict_plan.treatments,
    )

    try:
        contract.payload_to_edit_plan(payload, {})
        check("durata mancante rifiutata", False, "no exception")
    except contract.VxError:
        check("durata mancante rifiutata", True, "VxError")

    again = contract.payload_to_edit_plan(payload, MEDIA)
    check("bridge puro", plan.to_dict() == again.to_dict())

    no_declared = dict(finding)
    no_declared.pop("declared", None)
    bad = plan.to_dict()
    errors = contract.validate_evidence(bad, [no_declared], 10.0)
    check(
        "brief.autonomous senza declared rifiutato",
        any("declared" in error for error in errors),
        errors,
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
