"""F26-T04 -- a declared profile also checks the edit's structure.

    python tests/test_structure.py

The numbers in ``vx.profiles`` used to be documentation only. This file proves
they now judge a document that points at them, always from the document itself:

a. a document inside its band passes, and the compiler moves on to its sources;
b. a document twice too fast is refused, quoting the measured rate and the band;
c. a document too short for its profile is refused on duration alone;
d. a first visible change later than the declared hook is refused;
e. a document without ``profile_ref`` is never structure-checked, while
   ``check_structure`` itself still measures it honestly;
f. the real reel-shaped edit (40 clips, 29.8 s, 13.1 cuts per 10 s) passes the
   6..14 band and is refused by the 6..12 band -- the measurement is not bent;
g. a one-shot document has no cut and no hook, and says both;
h. a text card up from frame 0 is a hook at 0.0;
i. an interview-driven profile reports an unbacked grammar and accepts a
   dialogue audio bus.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, edl3_render, profiles, structure  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def build(
    durations: Sequence[float],
    *,
    profile: Optional[str] = None,
    text: Optional[List[Dict[str, Any]]] = None,
    audio: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """A document whose layer 0 tiles the timeline, one clip per duration."""
    clips: List[Dict[str, Any]] = []
    start = 0.0
    for index, length in enumerate(durations):
        clips.append(
            {
                "id": f"c{index}",
                "source": f"s{index}.mp4",
                "src_in": 0.0,
                "src_out": length,
                "dst_in": start,
                "dst_out": start + length,
            }
        )
        start += length
    doc: Dict[str, Any] = {
        "version": edl3.EDL3_VERSION,
        "fps": 30,
        "size": [1080, 1920],
        "clips": clips,
    }
    if profile is not None:
        doc["profile_ref"] = profile
    if text is not None:
        doc["text_layers"] = text
    if audio is not None:
        doc["audio"] = audio
    return doc


def timeline(doc: Dict[str, Any]) -> edl3.Timeline3:
    return edl3.Timeline3.from_dict(doc)


def codes(doc: Dict[str, Any]) -> List[str]:
    return [item["code"] for item in timeline(doc).validate()]


def shape(doc: Dict[str, Any], name: str) -> tuple:
    return structure.check_structure(timeline(doc), profiles.resolve(name))


def compile_code(doc: Dict[str, Any]) -> str:
    try:
        edl3_render.compile_timeline(timeline(doc))
    except edl3_render.CompileError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001 - a wrong error type is a failure
        return f"__WRONG_TYPE__{type(exc).__name__}: {exc}"
    return ""


def has(details: Sequence[str], code: str) -> bool:
    return any(item.startswith(f"{code}:") for item in details)


#: 25 s, 20 cuts: 8.0 cuts per 10 s, first cut at 1.25 s -- inside reel.
INSIDE = [1.25] + [1.1875] * 20
#: 30 s, 39 cuts: 13.0 cuts per 10 s -- too fast for spot (3..8).
TOO_FAST = [0.75] * 40
#: 300 s, 29 cuts: 0.97 per 10 s, inside film's band; only the length is wrong.
TOO_SHORT_FILM = [10.0] * 30
#: 20 s, 20 cuts: 10.0 per 10 s (inside short), but the first cut lands at 4.0 s.
LATE_HOOK = [4.0] + [0.8] * 20
#: The real reel-shaped edit: 40 clips over 29.8 s, 39 cuts, 13.1 per 10 s.
REEL_SHAPED = [0.745] * 40
#: 600 s, 60 cuts: 1.0 per 10 s, inside documentario's band.
DOCU = [600.0 / 61.0] * 61


def main() -> int:
    print("a. a document inside its band passes")
    inside = build(INSIDE, profile="reel")
    check("a1. reel: 25 s and 20 cuts is clean", shape(inside, "reel") == (), shape(inside, "reel"))
    check("a2. validate() adds no structural code", codes(inside) == [], codes(inside))
    check(
        "a3. the compiler moves on to the sources",
        compile_code(inside) == "MISSING_SOURCE",
        compile_code(inside),
    )

    print("\nb. a document twice too fast is refused, with the measured rate")
    fast = build(TOO_FAST, profile="spot")
    fast_details = shape(fast, "spot")
    check("b1. exactly one problem is reported", len(fast_details) == 1, fast_details)
    check("b2. it is STRUCTURE_DENSITY", has(fast_details, "STRUCTURE_DENSITY"), fast_details)
    check("b3. it quotes the measured 13.0 cuts per 10 s", any("13.0 cuts per 10s" in item for item in fast_details), fast_details)
    check("b4. it quotes the 3-8 band", any("3-8 band" in item for item in fast_details), fast_details)
    check("b5. validate() reports it", "STRUCTURE_DENSITY" in codes(fast), codes(fast))
    check(
        "b6. the length is not blamed as well",
        "STRUCTURE_DURATION" not in codes(fast),
        codes(fast),
    )
    check("b7. the compiler refuses it", compile_code(fast) == "STRUCTURE_DENSITY", compile_code(fast))

    print("\nc. a document too short for its profile is refused")
    short_film = build(TOO_SHORT_FILM, profile="film")
    film_details = shape(short_film, "film")
    check("c1. exactly one problem is reported", len(film_details) == 1, film_details)
    check("c2. it is STRUCTURE_DURATION", has(film_details, "STRUCTURE_DURATION"), film_details)
    check("c3. it quotes 300.000 s against the 4800-10800 window", any("300.000s" in item and "4800-10800s" in item for item in film_details), film_details)
    check("c4. validate() reports it", "STRUCTURE_DURATION" in codes(short_film), codes(short_film))
    check("c5. the compiler refuses it", compile_code(short_film) == "STRUCTURE_DURATION", compile_code(short_film))

    print("\nd. a hook that arrives too late is refused")
    late = build(LATE_HOOK, profile="short")
    late_details = shape(late, "short")
    check("d1. the rhythm itself is inside the band", "STRUCTURE_DENSITY" not in codes(late), codes(late))
    check("d2. exactly one problem is reported", len(late_details) == 1, late_details)
    check("d3. it is STRUCTURE_HOOK at 4.000 s", has(late_details, "STRUCTURE_HOOK") and "4.000s" in late_details[0], late_details)
    check("d4. the compiler refuses it", compile_code(late) == "STRUCTURE_HOOK", compile_code(late))

    print("\ne. a document without profile_ref is never checked")
    plain = build(TOO_SHORT_FILM)
    check("e1. validate() is clean", codes(plain) == [], codes(plain))
    check("e2. no structural code appears", not any(code.startswith("STRUCTURE_") for code in codes(plain)), codes(plain))
    check(
        "e3. check_structure itself still measures it honestly",
        has(structure.check_structure(timeline(plain), profiles.resolve("film")), "STRUCTURE_DURATION"),
        structure.check_structure(timeline(plain), profiles.resolve("film")),
    )
    check("e4. the compiler fails on the missing source, not structure", compile_code(plain) == "MISSING_SOURCE", compile_code(plain))

    print("\nf. the real reel-shaped edit: 40 cuts in 29.8 s")
    reel_shaped = build(REEL_SHAPED, profile="short")
    check("f1. 40 clips tile 29.8 s", abs(timeline(reel_shaped).duration - 29.8) < 1e-9, timeline(reel_shaped).duration)
    check("f2. the 6..14 short band accepts it", shape(reel_shaped, "short") == (), shape(reel_shaped, "short"))
    measured = 39 * structure.DENSITY_WINDOW / timeline(reel_shaped).duration
    check("f3. the measured rate is 13.1 cuts per 10 s", abs(measured - 13.1) < 0.05, measured)
    check("f4. ... and it sits inside 6..14", 6.0 <= measured <= 14.0, measured)
    check("f5. a reel profile's 6..12 band refuses it, unmoved", has(shape(reel_shaped, "reel"), "STRUCTURE_DENSITY"), shape(reel_shaped, "reel"))
    check("f6. validate() with the short profile is clean", codes(reel_shaped) == [], codes(reel_shaped))

    print("\ng. a one-shot document has no cut and no hook")
    one_shot = build([20.0], profile="reel")
    one_details = shape(one_shot, "reel")
    check("g1. both problems are named", has(one_details, "STRUCTURE_DENSITY") and has(one_details, "STRUCTURE_HOOK"), one_details)
    check("g2. the density detail measures 0.0 cuts", any("0.0 cuts per 10s" in item for item in one_details), one_details)
    check("g3. the hook detail says nothing visibly changes", any("no visible change" in item for item in one_details), one_details)

    print("\nh. a text card up from frame 0 is a hook at 0.0")
    hookless = build([5.0] + [1.0] * 15, profile="short")
    check("h1. without the card the hook lands at 5.000 s", has(shape(hookless, "short"), "STRUCTURE_HOOK") and "5.000s" in shape(hookless, "short")[0], shape(hookless, "short"))
    carded = build([5.0] + [1.0] * 15, profile="short", text=[{"text": "HOOK", "in": 0.0, "out": 2.0}])
    check("h2. with the card the document is clean", shape(carded, "short") == (), shape(carded, "short"))

    print("\ni. an interview-driven profile needs the document's own evidence")
    docu = build(DOCU, profile="documentario")
    check("i1. an unbacked documentary grammar is reported", codes(docu) == ["STRUCTURE_INTERVIEW"], codes(docu))
    dialogue = [
        {
            "role": "dialogue",
            "clips": [{"source": "take.wav", "src_in": 0.0, "src_out": 5.0, "dst_in": 0.0, "dst_out": 5.0}],
        }
    ]
    backed = build(DOCU, profile="documentario", audio=dialogue)
    check("i2. a dialogue audio bus backs it", codes(backed) == [], codes(backed))
    check("i3. beat alignment is never guessed", all("STRUCTURE_BEAT" not in code for code in codes(inside)), codes(inside))

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
