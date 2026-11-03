"""F4-T08 â€” the anti-arlecchino density cap, enforced by the compiler.

The rule: no more than 3 visible effect instances may start inside any sliding
10 s window of one timeline. Windows are anchored on each clip's start on the
timeline (``Effect`` declares no per-instance start), both edges of a window
count, and a breach is refused with ``EFFECT_DENSITY``.

Not an ffmpeg test: the cap is structural, so it is measured on documents.

    python tests/test_edl3_density.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from vx import edl3  # noqa: E402
from vx import edl3_render  # noqa: E402
from vx.edl3_render import CompileError  # noqa: E402

import golden_edl3  # noqa: E402

PASS = 0
FAIL = 0
SELF = str(Path(__file__).resolve())
CODE = "EFFECT_DENSITY"


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


# ------------------------------------------------------------------ helpers


def look() -> edl3.Effect:
    """One library look: the declarative entries are what the cap governs."""
    return edl3.Effect("vignette", {})


def looks(count: int) -> List[edl3.Effect]:
    return [look() for _ in range(count)]


def primitives(count: int) -> List[edl3.Effect]:
    """The compositing stack, which is not a look and never counts."""
    pool = [
        edl3.Effect("transform", {"scale": 1.0, "opacity": 0.9}),
        edl3.Effect("crop", {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8}),
        edl3.Effect("color", {"brightness": 0.1}),
        edl3.Effect("blur", {"sigma": 2.0}),
        edl3.Effect("chroma_key", {"similarity": 0.2}),
    ]
    return pool[:count]


def clip(
    cid: str,
    effects: int = 0,
    *,
    src_in: float = 0.0,
    src_out: float = 2.0,
    dst: tuple | None = None,
    source: str = SELF,
    **extra: Any,
) -> edl3.Clip3:
    return edl3.Clip3(
        id=cid,
        source=source,
        src_in=src_in,
        src_out=src_out,
        dst_in=None if dst is None else dst[0],
        dst_out=None if dst is None else dst[1],
        effects=looks(effects),
        **extra,
    )


def timeline(clips: List[edl3.Clip3], **extra: Any) -> edl3.Timeline3:
    return edl3.Timeline3(fps=12, size=(320, 568), clips=clips, **extra)


def density(document: edl3.Timeline3) -> List[Dict[str, str]]:
    return [p for p in document.validate() if p["code"] == CODE]


def details(document: edl3.Timeline3) -> List[str]:
    return edl3.check_effect_density(document)


# --------------------------------------------------------------------- main


def main() -> int:
    print("\n(1) the declared rule and its refusal code")
    check(
        "(a) the cap is 3 instances per 10 s",
        edl3.EFFECT_DENSITY_LIMIT == 3 and edl3.EFFECT_DENSITY_WINDOW == 10.0,
        f"{edl3.EFFECT_DENSITY_LIMIT}/{edl3.EFFECT_DENSITY_WINDOW}s",
    )
    check(
        "(b) EFFECT_DENSITY is a hard code, so compile refuses it",
        CODE in edl3_render.HARD_CODES,
    )
    check(
        "(c) a timeline with no effects reports nothing",
        details(timeline([clip("c1")])) == [],
    )

    print("\n(2) the cap itself")
    three = timeline([clip("c1", 3, src_out=20.0)])
    four = timeline([clip("c1", 4, src_out=20.0)])
    check("(a) exactly 3 effects in one clip is allowed", details(three) == [])
    check("(b) 4 effects in one clip are refused", len(details(four)) == 1)
    check(
        "(c) the message counts the instances",
        "4 effect instances" in details(four)[0],
        details(four)[0],
    )
    check(
        "(d) the message names the window it measured",
        "between 0.000s and 10.000s" in details(four)[0],
    )
    problems = density(four)
    check(
        "(e) validate() reports one EFFECT_DENSITY anchored on clips",
        len(problems) == 1 and problems[0]["where"] == "clips",
        [p["where"] for p in problems],
    )
    refused = None
    try:
        edl3_render.compile_timeline(four)
    except CompileError as exc:
        refused = exc
    check(
        "(f) compile_timeline refuses with EFFECT_DENSITY",
        refused is not None and refused.code == CODE,
        "" if refused is None else refused.code,
    )

    print("\n(3) the window: anchor, slide, inclusive edges")
    edge_in = timeline(
        [
            clip("a", 2, src_out=10.0, dst=(0.0, 10.0)),
            clip("b", 2, src_in=8.0, src_out=18.0, dst=(10.0, 20.0)),
        ]
    )
    check(
        "(a) an instance at t=0 and one at t=10 share a 10 s window",
        len(details(edge_in)) == 1,
        f"{len(details(edge_in))} violation(s)",
    )
    edge_out = timeline(
        [
            clip("a", 2, src_out=10.2, dst=(0.0, 10.2)),
            clip("b", 2, src_in=8.3, src_out=18.1, dst=(10.2, 20.0)),
        ]
    )
    check(
        "(b) at t=0 and t=10.2 they no longer share one",
        details(edge_out) == [],
        f"{len(details(edge_out))} violation(s)",
    )
    spread = timeline(
        [
            clip("a", 1, src_out=5.0, dst=(0.0, 5.0)),
            clip("b", 2, src_in=3.0, src_out=10.0, dst=(5.0, 12.0)),
            clip("c", 1, src_in=10.0, src_out=18.0, dst=(12.0, 20.0)),
        ]
    )
    check(
        "(c) 4 instances spread across 12 s stay under the cap",
        details(spread) == [],
        f"{len(details(spread))} violation(s)",
    )
    check(
        "(d) the count crosses clip boundaries, it is not per clip",
        len(details(edge_in)) == 1 and "a, b" in details(edge_in)[0],
        details(edge_in)[0],
    )

    print("\n(4) the cap is a parameter, the default is the rule")
    check(
        "(a) limit=4 allows the 4-instance document",
        edl3.check_effect_density(four, limit=4) == [],
    )
    narrow = timeline(
        [
            clip("a", 2, src_out=6.0, dst=(0.0, 6.0)),
            clip("b", 2, src_in=4.0, src_out=14.0, dst=(6.0, 16.0)),
        ]
    )
    check(
        "(b) window=5 s does not see a 6 s gap as one window",
        edl3.check_effect_density(narrow, window=5.0) == [],
    )
    check(
        "(c) window=10 s does see it",
        len(edl3.check_effect_density(narrow, window=10.0)) == 1,
    )

    print("\n(5) what does not count")
    texty = timeline(
        [clip("c1", 4, src_out=20.0)],
        text_layers=[
            edl3.TextLayer(text="ciao", time_in=0.0, time_out=2.0),
            edl3.TextLayer(text="mondo", time_in=2.0, time_out=4.0),
        ],
        captions=[edl3.CaptionTrack([edl3.CaptionCue("uno", 0.0, 1.0)])],
    )
    text_problems = density(texty)
    check(
        "(a) text layers and captions are not effects and add nothing",
        len(text_problems) == 1 and "4 effect instances" in text_problems[0]["detail"],
    )
    adjusted = timeline(
        [clip("c1", 1, src_out=20.0)],
        adjustments=[edl3.AdjustmentLayer(track=1, effects=looks(4))],
    )
    check(
        "(b) an adjustment layer has no start of its own and is outside the rule",
        details(adjusted) == [],
    )
    generated = timeline(
        [
            clip(
                "g1",
                4,
                source="",
                src_out=2.0,
                generator={
                    "template": "vx-title-lower-third",
                    "params": {},
                    "engine": "chromium",
                    "alpha": False,
                },
            )
        ]
    )
    check(
        "(c) a generator clip renders a picture, so its effects count",
        len(details(generated)) == 1,
    )
    empty = timeline([clip("c1", 4, source="", src_out=20.0)])
    check(
        "(d) a clip with no picture at all never renders those effects",
        details(empty) == [],
    )

    print("\n(6) nested sequences are measured on their own timeline")
    inner = timeline([clip("i1", 4, src_out=20.0)])
    nested = timeline(
        [clip("o1", source="", src_out=2.0, sequence="inner")],
        sequences={"inner": {"id": "inner", "timeline": inner}},
    )
    nested_problems = density(nested)
    check(
        "(a) the inner timeline is refused at its own prefix",
        len(nested_problems) >= 1
        and nested_problems[0]["where"] == "sequences.inner.timeline.clips",
        [p["where"] for p in nested_problems],
    )
    check(
        "(b) a sequence-only clip counts nothing on the outer timeline",
        [p for p in nested_problems if p["where"] == "clips"] == [],
    )

    print("\n(7) purity")
    before = json.dumps(four.to_dict(), sort_keys=True)
    edl3.check_effect_density(four)
    after = json.dumps(four.to_dict(), sort_keys=True)
    check("(a) the check does not mutate the document", before == after)
    check(
        "(b) it is deterministic",
        edl3.check_effect_density(four) == edl3.check_effect_density(four),
    )

    print("\n(8) the committed fixtures stay under the cap")
    fixture = ROOT / "tests" / "fixtures" / "edl3_generator" / "doc.json"
    committed = edl3.Timeline3.from_dict(json.loads(fixture.read_text(encoding="utf-8")))
    check(
        "(a) tests/fixtures/edl3_generator/doc.json is under the cap",
        details(committed) == [],
    )
    dummy = {
        name: Path(f"{name}.mp4")
        for name in (
            "blue",
            "red",
            "green",
            "grey64",
            "grey128",
            "changing",
            "two_tone",
            "speech",
            "bed",
        )
    }
    over = []
    for name, entry in golden_edl3.EFFECTS.items():
        document = entry["doc_factory"](dummy)
        if details(document):
            over.append(name)
    check("(b) every golden document stays under the cap", over == [], over)

    print("\n(9) the primitives are the stack, not a look")
    stacked = edl3.Clip3(
        id="s1",
        source=SELF,
        src_in=0.0,
        src_out=20.0,
        effects=primitives(5),
    )
    check(
        "(a) five primitives on one clip never count",
        details(timeline([stacked])) == [],
        details(timeline([stacked])),
    )
    mixed = edl3.Clip3(
        id="m1",
        source=SELF,
        src_in=0.0,
        src_out=20.0,
        effects=primitives(5) + looks(3),
    )
    mixed_details = details(timeline([mixed]))
    check(
        "(b) five primitives plus three looks counts only the three",
        mixed_details == [],
        mixed_details,
    )
    over = edl3.Clip3(
        id="o1",
        source=SELF,
        src_in=0.0,
        src_out=20.0,
        effects=primitives(5) + looks(4),
    )
    over_details = details(timeline([over]))
    check(
        "(c) five primitives plus four looks is refused on the four",
        len(over_details) == 1 and "4 effect instances" in over_details[0],
        over_details,
    )

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
