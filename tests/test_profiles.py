"""F26-T01 -- the genre profile registry: the engine stays genre-agnostic.

    python tests/test_profiles.py

D033: the genre is declared data, never a branch. This file proves the data is
complete and usable, and that a document can point at it:

a. exactly nine ids are declared, each resolving to itself;
b. every declared row passes ``check()`` with an empty tuple -- the registry is
   not self-contradictory;
c. an unknown (or blank) reference is refused with ``PROFILE_UNKNOWN``, and the
   refusal names the declared ids;
d. every field of every row sits inside its declared range, the density bands
   are ordered from slow genres to fast ones, the loudness targets are negative
   and the true peaks are <= 0;
e. the named facts: reel and short are vertical 1080x1920, film is 24 fps and
   10 bit, documentario states its own web target and says broadcast wants -23;
f. a document with a valid ``profile_ref`` round-trips and validates cleanly, one
   with an unknown reference is refused by ``validate()`` and by
   ``compile_timeline``, and a document without the field behaves exactly as
   before (the key is absent from ``to_dict``);
g. ``check()`` is alive: each of its nine rules fires on a hand-broken row;
h. importing the registry pulls neither numpy nor the ffmpeg wrapper.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, edl3_render, profiles  # noqa: E402
from vx.edl3 import EDLError  # noqa: E402

PASS = 0
FAIL = 0
CODE = "PROFILE_UNKNOWN"
DECLARED = (
    "reel",
    "short",
    "videoclip",
    "documentario",
    "film",
    "spot",
    "tutorial",
    "evento",
    "trailer",
)


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def document(profile_ref: Any = None) -> Dict[str, Any]:
    """The smallest valid document, optionally pointing at a profile."""
    doc: Dict[str, Any] = {
        "version": edl3.EDL3_VERSION,
        "fps": 25,
        "size": [320, 568],
        "clips": [{"id": "c1", "source": "missing.mp4", "src_in": 0.0, "src_out": 1.0}],
    }
    if profile_ref is not None:
        doc["profile_ref"] = profile_ref
    return doc


def codes(doc: Dict[str, Any]) -> List[str]:
    return [item["code"] for item in edl3.Timeline3.from_dict(doc).validate()]


def compile_code(doc: Dict[str, Any]) -> str:
    try:
        edl3_render.compile_timeline(edl3.Timeline3.from_dict(doc))
    except edl3_render.CompileError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001 - a wrong error type is a failure
        return f"__WRONG_TYPE__{type(exc).__name__}: {exc}"
    return ""


def field_of(problem: str) -> str:
    """``reel.cut_density_min: ...`` -> ``cut_density_min``."""
    head = problem.split(":", 1)[0]
    return head.rsplit(".", 1)[-1]


def mentions(items: Tuple[str, ...], field: str) -> bool:
    return any(field_of(item) == field for item in items)


def main() -> int:
    print("a. the registry declares exactly the nine genres")

    check(
        "a1. names() is the declared registry, in order",
        profiles.names() == DECLARED,
        profiles.names(),
    )
    check(
        "a2. PROFILES holds exactly those nine ids",
        len(profiles.PROFILES) == 9 and set(profiles.PROFILES) == set(DECLARED),
        sorted(profiles.PROFILES),
    )
    check(
        "a3. every id resolves to the row that declares it",
        all(profiles.resolve(name).id == name for name in DECLARED),
        [profiles.resolve(name).id for name in DECLARED],
    )

    print("\nb. every declared row is internally consistent")
    for name in DECLARED:
        row = profiles.resolve(name)
        check(
            f"b. {name} passes check() with an empty tuple",
            profiles.check(row) == (),
            profiles.check(row),
        )

    print("\nc. an unknown reference is refused, naming the declared ids")
    unknown = ""
    try:
        profiles.resolve("podcast")
    except EDLError as exc:
        unknown = exc.code
        detail = exc.detail
    else:
        detail = ""
    check("c1. resolve('podcast') raises EDLError(PROFILE_UNKNOWN)", unknown == CODE, unknown)
    check(
        "c2. the refusal names every declared id",
        all(name in detail for name in DECLARED),
        detail,
    )
    blank = ""
    try:
        profiles.resolve("   ")
    except EDLError as exc:
        blank = exc.code
    check("c3. a blank reference is refused too", blank == CODE, blank)
    check(
        "c4. surrounding whitespace does not change the row",
        profiles.resolve(" film ").id == "film",
        profiles.resolve(" film ").id,
    )

    print("\nd. every field of every row is inside its declared range")
    rows = [profiles.resolve(name) for name in DECLARED]
    for label, predicate in (
        ("fps is a deliverable rate", lambda r: r.fps in profiles.FPS_CHOICES),
        ("canvas is positive", lambda r: r.width > 0 and r.height > 0),
        ("min_seconds does not exceed max_seconds", lambda r: r.min_seconds <= r.max_seconds),
        ("loudness target is negative", lambda r: r.loudness_lufs < 0.0),
        ("true peak is <= 0 dBTP", lambda r: r.true_peak_db <= 0.0),
        ("bit depth is 8 or 10", lambda r: r.bit_depth in profiles.BIT_DEPTHS),
        ("transfer is declared", lambda r: r.transfer in profiles.TRANSFERS),
        ("at least one deliverable", lambda r: len(r.deliverables) >= 1),
        ("every deliverable names codec/container", lambda r: all("/" in item for item in r.deliverables)),
        ("hook fits the minimum length", lambda r: r.hook_seconds <= r.min_seconds),
        ("density band is not inverted", lambda r: r.cut_density_min <= r.cut_density_max),
        ("act_count is not negative", lambda r: r.act_count >= 0),
        ("subtitles mode is declared", lambda r: r.subtitles in profiles.SUBTITLE_MODES),
        ("notes name the row's own LUFS target", lambda r: f"{r.loudness_lufs:g} LUFS" in r.notes),
        ("notes are a sentence, not a placeholder", lambda r: len(r.notes) >= 40),
    ):
        offenders = [r.id for r in rows if not predicate(r)]
        check(f"d. {label}", offenders == [], offenders)

    check(
        "d. the bands are ordered from slow genres to fast ones",
        profiles.PROFILES["evento"].cut_density_max
        < profiles.PROFILES["documentario"].cut_density_max
        < profiles.PROFILES["spot"].cut_density_max
        < profiles.PROFILES["trailer"].cut_density_max
        < profiles.PROFILES["short"].cut_density_max,
        [
            profiles.PROFILES[name].cut_density_max
            for name in ("evento", "documentario", "spot", "trailer", "short")
        ],
    )
    check(
        "d. no genre cuts less than a feature or more than a short",
        profiles.PROFILES["film"].cut_density_min
        < profiles.PROFILES["documentario"].cut_density_min
        <= profiles.PROFILES["spot"].cut_density_min
        < profiles.PROFILES["reel"].cut_density_min,
        [
            profiles.PROFILES[name].cut_density_min
            for name in ("film", "documentario", "spot", "reel")
        ],
    )
    check(
        "d. every band is a range, not a point",
        all(
            profiles.PROFILES[name].cut_density_max
            > profiles.PROFILES[name].cut_density_min
            for name in DECLARED
        ),
        [
            (profiles.PROFILES[name].cut_density_min, profiles.PROFILES[name].cut_density_max)
            for name in DECLARED
        ],
    )
    check(
        "d. every loudness target is negative and every true peak <= 0",
        all(r.loudness_lufs < 0.0 and r.true_peak_db <= 0.0 for r in rows),
        [(r.id, r.loudness_lufs, r.true_peak_db) for r in rows],
    )

    print("\ne. the named facts")
    reel, short = profiles.PROFILES["reel"], profiles.PROFILES["short"]
    film = profiles.PROFILES["film"]
    docu = profiles.PROFILES["documentario"]
    check(
        "e1. reel is vertical 1080x1920",
        (reel.width, reel.height) == (1080, 1920),
        (reel.width, reel.height),
    )
    check(
        "e2. short is vertical 1080x1920",
        (short.width, short.height) == (1080, 1920),
        (short.width, short.height),
    )
    check("e3. film runs at 24 fps", film.fps == 24.0, film.fps)
    check("e4. film is a 10 bit row", film.bit_depth == 10, film.bit_depth)
    check(
        "e5. documentario states its own -16 LUFS web target",
        docu.loudness_lufs == -16.0 and "-16 LUFS" in docu.notes,
        (docu.loudness_lufs, docu.notes),
    )
    check(
        "e6. documentario says broadcast wants -23 LUFS",
        "-23 LUFS" in docu.notes,
        docu.notes,
    )
    check("e7. film cites ATSC A/85", "A/85" in film.notes, film.notes)
    check(
        "e8. the broadcast rows cite EBU R128",
        all("EBU R128" in profiles.PROFILES[name].notes for name in ("spot", "evento", "trailer")),
        [profiles.PROFILES[name].notes for name in ("spot", "evento", "trailer")],
    )

    print("\nf. a document can point at a profile")
    plan = edl3.Timeline3.from_dict(document("documentario"))
    check("f1. the reference reached the object", plan.profile_ref == "documentario", plan.profile_ref)
    check("f2. the document validates cleanly", plan.validate() == [], plan.validate())
    serialised = plan.to_dict()
    check("f3. to_dict carries the reference", serialised.get("profile_ref") == "documentario", serialised.get("profile_ref"))
    restored = edl3.Timeline3.from_dict(serialised)
    check(
        "f4. the reference survives the round trip",
        restored.profile_ref == plan.profile_ref and restored.to_dict() == serialised,
        restored.profile_ref,
    )

    plain = edl3.Timeline3.from_dict(document())
    check("f5. a document without the field is unchanged", plain.profile_ref is None, plain.profile_ref)
    check("f6. ... and does not serialise the key", "profile_ref" not in plain.to_dict(), sorted(plain.to_dict()))
    check("f7. ... and still validates cleanly", plain.validate() == [], plain.validate())

    bad = document("podcast")
    check("f8. an unknown reference is a validation problem", CODE in codes(bad), codes(bad))
    check("f9. compile_timeline refuses it with the same code", compile_code(bad) == CODE, compile_code(bad))
    blank_doc = document("  ")
    check("f10. a blank reference is refused too", codes(blank_doc).count(CODE) >= 1, codes(blank_doc))
    good = document("tutorial")
    check(
        "f11. a valid reference adds no compile-time refusal for it",
        compile_code(good) == "MISSING_SOURCE",
        compile_code(good),
    )

    print("\ng. check() fires on a hand-broken row")
    base = profiles.PROFILES["film"]
    for label, broken, field in (
        ("fps 27", replace(base, fps=27.0), "fps"),
        ("empty canvas", replace(base, width=0), "canvas"),
        ("min above max", replace(base, min_seconds=20000.0), "min_seconds"),
        ("loudness at 0 LUFS", replace(base, loudness_lufs=0.0), "loudness_lufs"),
        ("true peak above 0", replace(base, true_peak_db=0.5), "true_peak_db"),
        ("12 bit", replace(base, bit_depth=12), "bit_depth"),
        ("no deliverable", replace(base, deliverables=()), "deliverables"),
        ("hook longer than the film", replace(base, hook_seconds=6000.0), "hook_seconds"),
        ("inverted density band", replace(base, cut_density_min=12.0, cut_density_max=4.0), "cut_density_min"),
    ):
        items = profiles.check(broken)
        check(f"g. {label} is reported", mentions(items, field), items)

    print("\nh. the registry is import-cheap")
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import vx.profiles; "
            "print('numpy' in sys.modules, 'vx.ffmpeg' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "scripts"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        },
    )
    check(
        "h. importing vx.profiles pulls neither numpy nor vx.ffmpeg",
        probe.stdout.strip() == "False False",
        (probe.stdout.strip(), probe.stderr.strip()[-200:]),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
