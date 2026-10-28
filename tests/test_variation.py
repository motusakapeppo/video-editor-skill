"""F27-T04 -- the variation engine is seeded, bounded and honest (D034 #3).

    python tests/test_variation.py

D034 #3: the same seed gives the same variants, and no variant leaves the range a
preset exposes. This file proves the engine, never the registry:

a. ``variants`` is deterministic: same seed, same tuple; a different seed moves
   the first variant; the call never mutates ``PRESETS`` or a base dict;
b. 105 variants across every declared preset: every key is exposed, every number
   stays inside its exposed range, every variant is non-empty, merges to a value
   different from the base in at least one key, and the variants of one call are
   distinct;
c. a preset with nothing exposed (built with ``dataclasses.replace``) and one
   whose only knob cannot move are refused with ``PRESET_NOT_VARIABLE``; a count
   below one is refused with ``VARIANT_COUNT``; an unknown id still gets
   ``PRESET_UNKNOWN``;
d. ``describe`` names the preset and every changed key, hides an unchanged
   override and refuses what the merge refuses;
e. the same seed printed under two ``PYTHONHASHSEED`` values is byte-identical --
   the only honest proof that hash randomisation cannot leak into the draw.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import presets  # noqa: E402
from vx.edl3 import EDLError  # noqa: E402

PASS = 0
FAIL = 0
ENV = {
    **os.environ,
    "PYTHONPATH": str(ROOT / "scripts"),
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
}


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:70} {detail}")


def raised(call, *args) -> EDLError:
    """The EDLError ``call`` raises; a call that does not raise is a failure here."""
    try:
        call(*args)
    except EDLError as exc:
        return exc
    return EDLError("NO_ERROR", "the call was accepted")


def exposed_names(name: str) -> List[str]:
    return [param.name for param in presets.PRESETS[name].exposed]


def main() -> int:
    print("a. the same seed is the same tuple, a different seed is not")
    first = presets.variants("slow_push", 7)
    again = presets.variants("slow_push", 7)
    check("a1. the same seed returns an equal tuple", first == again, first)
    check("a2. the same seed returns the same object values, not a shared tuple", first is not again, first is again)
    check("a3. the return value is a tuple of dicts", isinstance(first, tuple) and all(isinstance(v, dict) for v in first), type(first).__name__)
    check("a4. the default count is three", len(first) == 3, len(first))
    wide = presets.variants("palette_bleach", 11, 5)
    check("a5. an explicit count is honoured", len(wide) == 5, len(wide))
    check("a6. the same seed and count are equal on a three-knob preset", wide == presets.variants("palette_bleach", 11, 5), wide)
    other = presets.variants("slow_push", 8)
    check(
        "a7. a different seed gives a different first variant",
        other[0] != first[0],
        f"seed 7 {first[0]} vs seed 8 {other[0]}",
    )
    check("a8. a different seed gives a different tuple", other != first, other)
    snapshot = copy.deepcopy(presets.PRESETS["palette_bleach"].base)
    presets.variants("palette_bleach", 3, 9)
    check("a9. no base dict is mutated", presets.PRESETS["palette_bleach"].base == snapshot, presets.PRESETS["palette_bleach"].base)
    check("a10. PRESETS still holds every declared row", tuple(presets.PRESETS) == presets.names(), len(presets.PRESETS))

    print("\nb. 105 variants across every preset stay inside their declared ranges")
    all_variants: List[Dict[str, Any]] = []
    empty: List[Any] = []
    stray: List[Any] = []
    out_of_range: List[Any] = []
    not_number: List[Any] = []
    merge_errors: List[Any] = []
    not_different: List[Any] = []
    not_distinct: List[Any] = []
    carries_base_value: List[Any] = []
    for name in presets.names():
        preset = presets.PRESETS[name]
        params = {param.name: param for param in preset.exposed}
        made = presets.variants(name, 7, 7)
        all_variants.extend(made)
        if len(set(tuple(sorted(variant.items())) for variant in made)) != len(made):
            not_distinct.append((name, made))
        for variant in made:
            if not variant:
                empty.append((name, variant))
            for key, value in variant.items():
                param = params.get(key)
                if param is None:
                    stray.append((name, key))
                    continue
                if value == preset.base[key]:
                    carries_base_value.append((name, key, value))
                if param.kind == "number":
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        not_number.append((name, key, value))
                    elif not (param.minimum <= value <= param.maximum):
                        out_of_range.append((name, key, value, param.minimum, param.maximum))
                elif param.kind == "bool" and value is not (not preset.base[key]):
                    out_of_range.append((name, key, value))
            try:
                merged = presets.merge(name, variant)
            except EDLError as exc:
                merge_errors.append((name, variant, exc.code))
            else:
                if merged == preset.base:
                    not_different.append((name, variant))
    check("b1. 105 variants were produced across fifteen presets", len(all_variants) == 105, len(all_variants))
    check("b2. no variant is empty", empty == [], empty)
    check("b3. every key is a parameter the preset exposes", stray == [], stray)
    check("b4. every number is inside its exposed range", out_of_range == [], out_of_range)
    check("b5. every number is a real number, never a boolean", not_number == [], not_number)
    check("b6. no variant carries a value equal to the base", carries_base_value == [], carries_base_value)
    check("b7. every variant merges without a refusal", merge_errors == [], merge_errors)
    check("b8. every merged variant differs from the base", not_different == [], not_different)
    check("b9. the seven variants of one call are distinct", not_distinct == [], not_distinct)
    mismatched = [
        (name, variant)
        for name in presets.names()
        for variant in presets.variants(name, 5, 3)
        if set(variant)
        != {
            key
            for key, value in presets.merge(name, variant).items()
            if value != presets.PRESETS[name].base[key]
        }
    ]
    check("b10. a variant is exactly the difference from the base", mismatched == [], mismatched)

    print("\nc. a preset that cannot vary, and a count that is not a count, are refused")
    stamp = replace(presets.PRESETS["slow_push"], exposed=())
    stamp_error = raised(presets.variants, stamp, 7)
    check("c1. a preset with nothing exposed is PRESET_NOT_VARIABLE", stamp_error.code == "PRESET_NOT_VARIABLE", stamp_error.code)
    check("c2. the refusal names the preset", "slow_push" in stamp_error.detail, stamp_error.detail)
    check("c3. the refusal points at the exposed list", stamp_error.where == "exposed", stamp_error.where)
    stuck_param = replace(presets.PRESETS["slow_push"].exposed[0], minimum=1.12, maximum=1.12)
    stuck = replace(presets.PRESETS["slow_push"], exposed=(stuck_param,))
    stuck_error = raised(presets.variants, stuck, 7)
    check("c4. a knob pinned to its base cannot vary either", stuck_error.code == "PRESET_NOT_VARIABLE", stuck_error.code)
    zero = raised(presets.variants, "slow_push", 7, 0)
    check("c5. count=0 is VARIANT_COUNT", zero.code == "VARIANT_COUNT", zero.code)
    check("c6. the count refusal names the count it got", "0" in zero.detail, zero.detail)
    check("c7. the count refusal points at count", zero.where == "count", zero.where)
    negative = raised(presets.variants, "slow_push", 7, -2)
    check("c8. a negative count is refused the same way", negative.code == "VARIANT_COUNT", negative.code)
    unknown = raised(presets.variants, "kodak_portra_400", 7)
    check("c9. an unknown preset is still PRESET_UNKNOWN", unknown.code == "PRESET_UNKNOWN", unknown.code)

    print("\nd. describe names the preset and every changed key")
    line = presets.describe("slow_push", {"zoom": 1.31})
    check("d1. one change reads as preset, key, base, arrow, value", line == "slow_push: zoom 1.12 -> 1.31", line)
    two = presets.describe("palette_bleach", {"crush": 0.18, "lift": 0.9})
    check("d2. the preset is named first", two.startswith("palette_bleach:"), two)
    check("d3. every changed key is named with its move", "crush 0.15 -> 0.18" in two and "lift 0.86 -> 0.9" in two, two)
    check("d4. the changed keys keep the base's order", two.index("crush") < two.index("lift"), two)
    check("d5. no override is no change", presets.describe("slow_push", {}) == "slow_push: base unchanged", presets.describe("slow_push", {}))
    check("d6. an override equal to the base is not a change", presets.describe("slow_push", {"zoom": 1.12}) == "slow_push: base unchanged", presets.describe("slow_push", {"zoom": 1.12}))
    described = presets.describe("slow_push", first[0])
    check("d7. a produced variant describes as the same preset", described.startswith("slow_push: "), described)
    check("d8. a produced variant's only key is named", all(key in described for key in first[0]), described)
    bad_key = raised(presets.describe, "slow_push", {"tilt": 0.3})
    check("d9. describe refuses an unexposed key like merge", bad_key.code == "PARAM_NOT_EXPOSED", bad_key.code)
    bad_range = raised(presets.describe, "slow_push", {"zoom": 9.0})
    check("d10. describe refuses an out-of-range value like merge", bad_range.code == "PARAM_OUT_OF_RANGE", bad_range.code)

    print("\ne. PYTHONHASHSEED cannot reach the draw")
    probe = (
        "import json; from vx import presets;"
        "print(json.dumps(presets.variants('palette_bleach', 7, 5)))"
    )
    runs = []
    for hash_seed in ("0", "31337"):
        done = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            env={**ENV, "PYTHONHASHSEED": hash_seed},
        )
        runs.append((hash_seed, done.returncode, done.stdout.strip(), done.stderr.strip()[-200:]))
    check("e1. both subprocesses exited cleanly", all(code == 0 for _, code, _, _ in runs), [(seed, code) for seed, code, _, _ in runs])
    check("e2. both printed a non-empty variant list", all(out.startswith('[{"') for _, _, out, _ in runs), [out[:60] for _, _, out, _ in runs])
    check(
        "e3. PYTHONHASHSEED=0 and PYTHONHASHSEED=31337 print byte-identical variants",
        runs[0][2] == runs[1][2],
        [(seed, out[:120]) for seed, _, out, _ in runs],
    )
    check("e4. the subprocess output parses as five override dicts", len(json.loads(runs[0][2])) == 5, runs[0][2][:120])

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
