"""F2-T10 / T11 -- EDL 3 schema 1.1 is additive: nine optional fields, old documents valid.

    python tests/test_edl3_v11.py

The rule the schema now follows (D028): a new field is optional with a default, so
the version moves by a minor and NO migrations are needed; unknown ``x-`` fields
are preserved verbatim; a major is reserved for a change that is not compatible.

What this file proves:

a. both ``vx.edl3/1`` and ``vx.edl3/1.1`` validate, and an unknown version does not;
b. a ``/1`` document re-serialises as ``/1`` -- the reader never upgrades it;
c. all nine fields validate together and survive the round trip;
d. an ``x-`` field survives the round trip at clip and at document level;
e. every violation has its own code: four movements in a stack, a stack entry
   without a kind, an exposed param without type or default, an unknown
   provenance kind, an incomplete attribution, a non-``x-`` unknown field, an
   incomplete variant.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def document(version: str = "vx.edl3/1") -> dict:
    return {
        "version": version,
        "fps": 25,
        "size": [320, 568],
        "clips": [
            {"id": "c1", "source": "a.mp4", "src_in": 0.0, "src_out": 1.0}
        ],
    }


def full_document() -> dict:
    doc = document("vx.edl3/1.1")
    doc["clips"][0].update(
        {
            "preset_ref": "push-in",
            "stack": [{"kind": "push", "amount": 0.2}, {"kind": "handheld"}],
            "synthetic": True,
            "provenance": [
                {"kind": "finding", "id": "f1", "source": "measure:luma"}
            ],
            "template_ref": "vx-title-lower-third",
            "exposed_params": {
                "title": {"type": "string", "default": "Ciao"},
                "accent": {
                    "type": "color",
                    "default": "#FFD400",
                    "values": ["#FFD400", "#38E1FF"],
                },
            },
            "attribution": [
                {
                    "licence": "CC-BY-4.0",
                    "author": "Someone",
                    "source": "freesound",
                    "url": "https://example.org/track",
                }
            ],
            "x-studio": {"seed": 7},
        }
    )
    doc["brand_ref"] = "brand.json"
    doc["variant"] = {"id": "v2", "source_project": "p1", "axis": "hook"}
    doc["x-note"] = "kept"
    return doc


def codes(doc: dict) -> list:
    return [item["code"] for item in edl3.Timeline3.from_dict(doc).validate()]


def main() -> int:
    check(
        "0. the schema moved to an additive minor",
        edl3.EDL3_VERSION in edl3.EDL3_VERSIONS
        and {"vx.edl3/1", "vx.edl3/1.1"} <= set(edl3.EDL3_VERSIONS),
        edl3.EDL3_VERSION,
    )
    check("a. a /1 document validates", codes(document()) == [], codes(document()))
    check(
        "a. a /1.1 document validates",
        codes(full_document()) == [],
        codes(full_document()),
    )
    unknown = document("vx.edl3/2")
    check(
        "a. an unknown version is refused",
        "SCHEMA_VERSION" in codes(unknown),
        codes(unknown),
    )

    old = edl3.Timeline3.from_dict(document())
    check(
        "b. a /1 document is not silently upgraded",
        old.to_dict()["version"] == "vx.edl3/1",
        old.to_dict()["version"],
    )

    plan = edl3.Timeline3.from_dict(full_document())
    clip = plan.clips[0]
    check(
        "c. every field reached the object",
        clip.preset_ref == "push-in"
        and len(clip.stack) == 2
        and clip.synthetic is True
        and clip.provenance[0]["id"] == "f1"
        and clip.template_ref == "vx-title-lower-third"
        and clip.exposed_params["title"]["default"] == "Ciao"
        and clip.attribution[0]["licence"] == "CC-BY-4.0"
        and plan.brand_ref == "brand.json"
        and plan.variant["axis"] == "hook",
        (clip.preset_ref, clip.template_ref, plan.brand_ref),
    )
    restored = edl3.Timeline3.from_dict(plan.to_dict())
    back = restored.clips[0]
    check(
        "c. every field survives the round trip",
        restored.validate() == []
        and back.preset_ref == clip.preset_ref
        and back.stack == clip.stack
        and back.synthetic == clip.synthetic
        and back.provenance == clip.provenance
        and back.template_ref == clip.template_ref
        and back.exposed_params == clip.exposed_params
        and back.attribution == clip.attribution
        and restored.brand_ref == plan.brand_ref
        and restored.variant == plan.variant,
        [item["code"] for item in restored.validate()],
    )
    check(
        "d. an unknown x- field survives at both levels",
        back.ext.get("x-studio") == {"seed": 7}
        and restored.ext.get("x-note") == "kept",
        (back.ext, restored.ext),
    )

    four = copy.deepcopy(full_document())
    four["clips"][0]["stack"] = [{"kind": "a"}, {"kind": "b"}, {"kind": "c"}, {"kind": "d"}]
    check(
        "e. four movements in a stack are refused",
        "STACK_TOO_DEEP" in codes(four),
        codes(four),
    )
    no_kind = copy.deepcopy(full_document())
    no_kind["clips"][0]["stack"] = [{"amount": 0.2}]
    check("e. a movement without kind is refused", "STACK_SHAPE" in codes(no_kind), codes(no_kind))

    no_type = copy.deepcopy(full_document())
    no_type["clips"][0]["exposed_params"]["title"] = {"default": "x"}
    check(
        "e. an exposed param without type is refused",
        "EXPOSED_PARAM_SHAPE" in codes(no_type),
        codes(no_type),
    )
    no_default = copy.deepcopy(full_document())
    no_default["clips"][0]["exposed_params"]["title"] = {"type": "string"}
    check(
        "e. an exposed param without default is refused",
        "EXPOSED_PARAM_SHAPE" in codes(no_default),
        codes(no_default),
    )

    bad_provenance = copy.deepcopy(full_document())
    bad_provenance["clips"][0]["provenance"] = [
        {"kind": "vibe", "id": "x", "source": "y"}
    ]
    check(
        "e. an unknown provenance kind is refused",
        "PROVENANCE_SHAPE" in codes(bad_provenance),
        codes(bad_provenance),
    )

    bad_attribution = copy.deepcopy(full_document())
    del bad_attribution["clips"][0]["attribution"][0]["url"]
    check(
        "e. an attribution without url is refused",
        "ATTRIBUTION_SHAPE" in codes(bad_attribution),
        codes(bad_attribution),
    )

    stray = edl3.Timeline3(
        fps=25,
        size=(320, 568),
        clips=[edl3.Clip3(id="c1", source="a.mp4", src_in=0.0, src_out=1.0, ext={"foo": 1})],
    )
    check(
        "e. an unknown field without the x- prefix is refused",
        "EXT_KEY" in [item["code"] for item in stray.validate()],
        [item["code"] for item in stray.validate()],
    )

    bad_variant = copy.deepcopy(full_document())
    bad_variant["variant"] = {"id": "v2"}
    check(
        "e. an incomplete variant is refused",
        "VARIANT_SHAPE" in codes(bad_variant),
        codes(bad_variant),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
