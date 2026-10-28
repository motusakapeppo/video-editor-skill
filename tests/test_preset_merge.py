"""F27-T03 -- a preset use is base plus override, and only the differences count.

    python tests/test_preset_merge.py

D034: a preset is a starting point. This file proves the merge that turns one use
of it into values is pure, validated and recorded:

a. ``merge`` returns the base with the overrides applied, keeps the base's key
   order, and returns the base unchanged for an empty override -- the registry and
   its base dicts are never mutated;
b. ``difference`` records only the values that differ from the base, in the base's
   order, so an override equal to the base is not a change;
c. the refusals are load-bearing: ``PRESET_UNKNOWN`` for an unknown id,
   ``PARAM_NOT_EXPOSED`` for a key the preset does not expose, ``PARAM_OUT_OF_RANGE``
   for a value outside the exposed range (naming value and range, never rounding it
   into the range), ``PARAM_TYPE`` for a bool where a number is declared or the
   reverse;
d. ``effect_for`` names the registry kind the preset instantiates plus its params;
e. a ``vx.edl3/1.2`` document whose clip names a valid ``preset_ref`` validates
   clean, and one with an unexposed key, an unknown preset, an out-of-range value or
   a wrong type is refused with the merge's code at the clip's ``where``; a 1.1
   document keeps its earlier, inert annotation;
f. the provenance record ``{"preset": <id>, "changed": difference(...)}`` is what the
   clip produces, the clip's ``provenance`` accepts it, and it survives a round trip.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, presets  # noqa: E402
from vx.edl3 import BACKEND_FILTER, EFFECT_REGISTRY, EDLError  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:66} {detail}")


def raised(call, *args) -> EDLError:
    """The EDLError ``call`` raises; a call that does not raise is a failure here."""
    try:
        call(*args)
    except EDLError as exc:
        return exc
    return EDLError("NO_ERROR", "the call was accepted")


def document(version: str = "vx.edl3/1.2", **clip_fields: Any) -> Dict[str, Any]:
    clip: Dict[str, Any] = {"id": "c1", "source": "a.mp4", "src_in": 0.0, "src_out": 1.0}
    clip.update(clip_fields)
    return {"version": version, "fps": 25, "size": [320, 568], "clips": [clip]}


def problems(doc: Dict[str, Any]) -> List[Dict[str, str]]:
    return edl3.Timeline3.from_dict(doc).validate()


def codes(doc: Dict[str, Any]) -> List[str]:
    return [item["code"] for item in problems(doc)]


def main() -> int:
    print("a. merge is base plus override, and it never mutates the registry")
    one = presets.merge("slow_push", {"zoom": 1.2})
    check("a1. one override replaces exactly its own value", one == {"zoom": 1.2}, one)
    merged = presets.merge("palette_bleach", {"crush": 0.18})
    check("a2. a value not overridden keeps the base", merged["saturation"] == 0.22, merged)
    check("a3. every unmentioned key keeps the base", merged["lift"] == 0.86, merged)
    empty = presets.merge("whip_pan_cut", {})
    check("a4. an empty override returns the base unchanged", empty == {"speed": 12.0, "window": 0.6}, empty)
    check(
        "a5. an empty override keeps the base's key order",
        list(presets.merge("palette_bleach", {})) == list(presets.PRESETS["palette_bleach"].base),
        list(presets.merge("palette_bleach", {})),
    )
    check("a6. an override keeps the base's key order", list(merged) == ["saturation", "crush", "lift"], list(merged))
    check(
        "a7. the merged dict is a fresh copy, not the base itself",
        presets.merge("slow_push", {}) is not presets.PRESETS["slow_push"].base,
        presets.merge("slow_push", {}) is presets.PRESETS["slow_push"].base,
    )
    merged["crush"] = 0.19
    merged["saturation"] = 0.5
    check(
        "a8. editing the merged dict does not reach the registry",
        presets.PRESETS["palette_bleach"].base == {"saturation": 0.22, "crush": 0.15, "lift": 0.86},
        presets.PRESETS["palette_bleach"].base,
    )
    check(
        "a9. the declared base still validates after the merges",
        presets.check(presets.PRESETS["palette_bleach"]) == (),
        presets.check(presets.PRESETS["palette_bleach"]),
    )
    held = presets.merge("handheld_drift", {"amplitude": 6})
    check(
        "a10. a number arrives as it was given, uncoerced",
        held["amplitude"] == 6 and isinstance(held["amplitude"], int),
        (held["amplitude"], type(held["amplitude"]).__name__),
    )
    edges = [presets.merge("slow_push", {"zoom": edge})["zoom"] for edge in (1.02, 1.35)]
    check("a11. both exposed bounds are inclusive", edges == [1.02, 1.35], edges)

    print("\nb. difference records only the values that moved off the base")
    none = presets.difference("slow_push", {})
    check("b1. a use with no override has an empty difference", none == {}, none)
    only = presets.difference("palette_bleach", {"crush": 0.18})
    check("b2. only the overridden key is recorded", only == {"crush": 0.18}, only)
    same = presets.difference("slow_push", {"zoom": 1.12})
    check("b3. an override equal to the base is not a difference", same == {}, same)
    moved = presets.difference("light_overcast", {"saturation": 0.7, "density": 0.15})
    check(
        "b4. two changed keys are recorded in the base's order",
        moved == {"density": 0.15, "saturation": 0.7} and list(moved) == ["density", "saturation"],
        moved,
    )
    check(
        "b5. the difference is exactly the merged values that moved",
        presets.difference("slow_push", {"zoom": 1.2}) == {"zoom": 1.2},
        presets.difference("slow_push", {"zoom": 1.2}),
    )

    print("\nc. a use that breaks the schema is refused, never repaired")
    unknown = raised(presets.merge, "kodak_portra_400", {})
    check("c1. an unknown preset is PRESET_UNKNOWN", unknown.code == "PRESET_UNKNOWN", unknown.code)
    check(
        "c2. the refusal names every declared id",
        all(name in unknown.detail for name in presets.names()),
        unknown.detail,
    )
    via_effect = raised(presets.effect_for, "kodak_portra_400", {}).code
    check("c3. effect_for refuses an unknown preset too", via_effect == "PRESET_UNKNOWN", via_effect)
    via_diff = raised(presets.difference, "kodak_portra_400", {}).code
    check("c4. difference refuses an unknown preset too", via_diff == "PRESET_UNKNOWN", via_diff)
    unexposed = raised(presets.merge, "slow_push", {"tilt": 0.2})
    check("c5. an unexposed key is PARAM_NOT_EXPOSED", unexposed.code == "PARAM_NOT_EXPOSED", unexposed.code)
    check(
        "c6. the refusal names the preset and the key",
        "slow_push" in unexposed.detail and "'tilt'" in unexposed.detail,
        unexposed.detail,
    )
    check("c7. the refusal points at the key it refused", unexposed.where == "exposed_params.tilt", unexposed.where)
    high = raised(presets.merge, "slow_push", {"zoom": 1.4})
    check("c8. a value above the range is PARAM_OUT_OF_RANGE", high.code == "PARAM_OUT_OF_RANGE", high.code)
    check(
        "c9. the refusal names preset, key, value and the declared range",
        all(piece in high.detail for piece in ("slow_push", "'zoom'", "1.4", "1.02", "1.35")),
        high.detail,
    )
    low = raised(presets.merge, "slow_push", {"zoom": 1.0}).code
    check("c10. a value below the range is refused the same way", low == "PARAM_OUT_OF_RANGE", low)
    hair = raised(presets.merge, "slow_push", {"zoom": 1.3500001}).code
    check("c11. a hair above the maximum is refused, not rounded into it", hair == "PARAM_OUT_OF_RANGE", hair)
    wrong_bool = raised(presets.merge, "slow_push", {"zoom": True})
    check("c12. a bool where a number is declared is PARAM_TYPE", wrong_bool.code == "PARAM_TYPE", wrong_bool.code)
    check(
        "c13. the type refusal says which kinds it compared",
        "number" in wrong_bool.detail and "bool" in wrong_bool.detail,
        wrong_bool.detail,
    )
    wrong_str = raised(presets.merge, "slow_push", {"zoom": "1.2"}).code
    check("c14. a string where a number is declared is PARAM_TYPE", wrong_str == "PARAM_TYPE", wrong_str)
    diff_bad_key = raised(presets.difference, "slow_push", {"tilt": 0.2}).code
    check("c15. difference refuses an unexposed key instead of dropping it", diff_bad_key == "PARAM_NOT_EXPOSED", diff_bad_key)

    print("\nd. effect_for names the registry effect a use instantiates")
    effect = presets.effect_for("lens_long", {})
    check("d1. the kind is the preset's declared effect", effect["kind"] == "bokeh", effect["kind"])
    check("d2. the params are the preset's base", effect["params"] == {"radius": 20.0, "power": 1.8}, effect["params"])
    spec = EFFECT_REGISTRY.get(effect["kind"])
    check(
        "d3. the declared kind really exists in the effect registry",
        spec is not None and spec.backend == BACKEND_FILTER,
        None if spec is None else spec.backend,
    )
    wider = presets.effect_for("lens_long", {"radius": 24.0})["params"]
    check("d4. an override reaches the effect's params", wider["radius"] == 24.0, wider)

    print("\ne. a document with a preset_ref is validated against the preset")
    clean = document(preset_ref="slow_push", exposed_params={"zoom": 1.2})
    check("e1. a valid preset use validates clean", codes(clean) == [], codes(clean))
    produced = edl3.Timeline3.from_dict(clean).clips[0].preset_provenance()
    check(
        "e2. the clip produces the record for that use",
        produced == {"preset": "slow_push", "changed": {"zoom": 1.2}},
        produced,
    )
    base_only = document(preset_ref="slow_push", exposed_params={})
    clip = edl3.Timeline3.from_dict(base_only).clips[0]
    check("e3. an empty override validates clean", codes(base_only) == [], codes(base_only))
    check(
        "e4. the empty override merges to the base unchanged",
        presets.merge(clip.preset_ref, clip.preset_overrides()) == presets.PRESETS["slow_push"].base,
        presets.merge(clip.preset_ref, clip.preset_overrides()),
    )
    check("e5. the empty override records no change", clip.preset_provenance() == {"preset": "slow_push", "changed": {}}, clip.preset_provenance())
    found = problems(document(preset_ref="kodak_portra_400"))
    check("e6. an unknown preset is refused with PRESET_UNKNOWN", [item["code"] for item in found] == ["PRESET_UNKNOWN"], [item["code"] for item in found])
    check("e7. the refusal carries the clip's where", found[0]["where"] == "clips[0].preset_ref", found[0]["where"])
    found = problems(document(preset_ref="slow_push", exposed_params={"tilt": 0.2}))
    check(
        "e8. an unexposed key is refused with PARAM_NOT_EXPOSED",
        [item["code"] for item in found] == ["PARAM_NOT_EXPOSED"],
        [item["code"] for item in found],
    )
    check(
        "e9. that refusal carries the clip's where and the key",
        found[0]["where"] == "clips[0].exposed_params.tilt" and "'tilt'" in found[0]["detail"],
        (found[0]["where"], found[0]["detail"]),
    )
    found = problems(document(preset_ref="slow_push", exposed_params={"zoom": 1.4}))
    check(
        "e10. an out-of-range override is refused with PARAM_OUT_OF_RANGE",
        [item["code"] for item in found] == ["PARAM_OUT_OF_RANGE"],
        [item["code"] for item in found],
    )
    check(
        "e11. the range refusal names the preset and the clip's where",
        "slow_push" in found[0]["detail"] and found[0]["where"] == "clips[0].exposed_params.zoom",
        (found[0]["where"], found[0]["detail"]),
    )
    found = problems(document(preset_ref="slow_push", exposed_params={"zoom": True}))
    check(
        "e12. a bool for a number is refused with PARAM_TYPE and the clip's where",
        [(item["code"], item["where"]) for item in found] == [("PARAM_TYPE", "clips[0].exposed_params.zoom")],
        [(item["code"], item["where"]) for item in found],
    )
    old = document("vx.edl3/1.1", preset_ref="kodak_portra_400")
    check("e13. a 1.1 document keeps its inert preset annotation", codes(old) == [], codes(old))

    print("\nf. the provenance record is produced, accepted and round-tripped")
    templated = edl3.Clip3(
        id="gen",
        src_in=0.0,
        src_out=1.0,
        template_ref="vx-title-lower-third",
        preset_ref="slow_push",
        exposed_params={"title": "Ciao"},
    )
    check("f1. a template clip exposes no preset override of its own", templated.preset_overrides() == {}, templated.preset_overrides())
    bare = edl3.Clip3(id="c", source="a.mp4", src_in=0.0, src_out=1.0)
    check("f2. a clip without a preset has no preset record", bare.preset_provenance() is None, bare.preset_provenance())
    record = {"preset": "slow_push", "changed": {"zoom": 1.2}}
    carried = document(preset_ref="slow_push", exposed_params={"zoom": 1.2}, provenance=[record])
    check("f3. the clip's provenance accepts that record", codes(carried) == [], codes(carried))
    restored = edl3.Timeline3.from_dict(edl3.Timeline3.from_dict(carried).to_dict()).clips[0]
    check("f4. the record survives the round trip", dict(restored.provenance[0]) == record, restored.provenance)
    evidence = document(provenance=[{"kind": "finding", "id": "f1", "source": "measure:luma"}])
    check("f5. the earlier evidence record is still accepted", codes(evidence) == [], codes(evidence))
    nameless = document(provenance=[{"preset": "", "changed": {}}])
    check("f6. a preset record without a preset is refused", codes(nameless) == ["PROVENANCE_SHAPE"], codes(nameless))
    no_changed = document(provenance=[{"preset": "slow_push"}])
    check("f7. a preset record without its changed object is refused", codes(no_changed) == ["PROVENANCE_SHAPE"], codes(no_changed))

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
