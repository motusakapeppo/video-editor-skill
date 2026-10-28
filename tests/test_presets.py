"""F27-T02 -- the preset registry: a look is a base plus exposed knobs (D034).

    python tests/test_presets.py

D034: a preset is a starting point, not a stamp. This file proves the declared
registry is complete and usable, and that its validation is load-bearing:

a. at least fifteen presets resolve, across the five categories with three rows
   each, in declaration order;
b. every declared row passes ``check()`` with an empty tuple, and ``check_all()``
   is empty for every id -- the registry is not self-contradictory;
c. every row is checked against the *live* effect registry, never a hardcoded
   table: the kind exists, its backend is ``filter``, every base key is declared
   and inside its ``ParamSpec``, every exposed range stays inside that same
   ``ParamSpec``, and every exposed default is its base value;
d. every note says what the look is, that the numbers are a starting point and
   not a law, and no two rows share a label;
e. an unknown or blank id is refused with ``PRESET_UNKNOWN``, naming every
   declared id, and ``by_category()`` covers every row exactly once;
f. ``check()`` is alive: each rule fires on a hand-broken copy built with
   ``dataclasses.replace`` -- the registry itself is never edited;
g. importing the registry pulls neither numpy nor the ffmpeg wrapper.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import presets  # noqa: E402
from vx.edl3 import BACKEND_FILTER, EFFECT_REGISTRY, EDLError  # noqa: E402
from vx.effect_spec import EffectSpec  # noqa: E402

PASS = 0
FAIL = 0
CODE = "PRESET_UNKNOWN"


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def declared(preset: presets.Preset) -> Optional[EffectSpec]:
    """The live effect spec a row points at, or ``None`` when it points nowhere."""
    return EFFECT_REGISTRY.get(preset.effect)


def rows() -> List[presets.Preset]:
    return [presets.resolve(name) for name in presets.names()]


def main() -> int:
    print("a. the registry declares fifteen presets across five categories")
    all_rows = rows()
    grouped = presets.by_category()
    check("a1. names() is the registry, in declaration order", presets.names() == tuple(presets.PRESETS), presets.names())
    check("a2. at least fifteen presets are declared", len(presets.PRESETS) >= 15, len(presets.PRESETS))
    check(
        "a3. every id resolves to the row that declares it",
        all(presets.resolve(name).id == name for name in presets.names()),
        [presets.resolve(name).id for name in presets.names()],
    )
    check("a4. by_category covers exactly the five declared categories", set(grouped) == set(presets.CATEGORIES), sorted(grouped))
    counts = {category: len(items) for category, items in grouped.items()}
    check("a5. every category has at least three presets", all(count >= 3 for count in counts.values()), counts)
    listed = [preset.id for items in grouped.values() for preset in items]
    check("a6. by_category lists every preset exactly once", sorted(listed) == sorted(presets.names()), listed)
    check(
        "a7. every row is filed under a declared category",
        all(preset.category in presets.CATEGORIES for preset in all_rows),
        [preset.id for preset in all_rows if preset.category not in presets.CATEGORIES],
    )

    print("\nb. every declared row is internally consistent")
    for preset in all_rows:
        problems = presets.check(preset)
        check(f"b. {preset.id} passes check() with an empty tuple", problems == (), problems)
    report = presets.check_all()
    check("b. check_all() keys are exactly the declared ids", tuple(report) == presets.names(), list(report))
    check("b. check_all() reports no problem for any preset", all(not items for items in report.values()), report)

    print("\nc. every row agrees with the live effect registry")
    missing = [preset.id for preset in all_rows if declared(preset) is None]
    check("c1. every effect kind exists in EFFECT_REGISTRY", missing == [], missing)
    wrong_backend = [
        (preset.id, preset.effect, declared(preset).backend if declared(preset) else "")
        for preset in all_rows
        if declared(preset) is None or declared(preset).backend != BACKEND_FILTER
    ]
    check("c2. every effect uses the filter backend", wrong_backend == [], wrong_backend)

    undeclared_base = [
        (preset.id, name)
        for preset in all_rows
        if declared(preset) is not None
        for name in preset.base
        if declared(preset).param(name) is None
    ]
    check("c3. every base key is a declared parameter", undeclared_base == [], undeclared_base)
    out_of_range_base = [
        (preset.id, name, value)
        for preset in all_rows
        if declared(preset) is not None
        for name, value in preset.base.items()
        if (param := declared(preset).param(name)) is not None and not param.contains(value)
    ]
    check("c4. every base value is inside its effect's ParamSpec range", out_of_range_base == [], out_of_range_base)

    exposed = [(preset, param) for preset in all_rows for param in preset.exposed]
    undeclared_exposed = [
        (preset.id, param.name)
        for preset, param in exposed
        if declared(preset) is None or declared(preset).param(param.name) is None
    ]
    check("c5. every exposed parameter is declared by its effect", undeclared_exposed == [], undeclared_exposed)
    escaping = [
        (preset.id, param.name, param.minimum, param.maximum)
        for preset, param in exposed
        if declared(preset) is not None
        and declared(preset).param(param.name) is not None
        and param.minimum is not None
        and param.maximum is not None
        and (
            param.minimum < declared(preset).param(param.name).lo
            or param.maximum > declared(preset).param(param.name).hi
        )
    ]
    check("c6. every exposed range sits inside the ParamSpec range", escaping == [], escaping)
    drifting = [
        (preset.id, param.name, param.default, preset.base.get(param.name))
        for preset, param in exposed
        if param.default != preset.base.get(param.name)
    ]
    check("c7. every exposed default equals its base value", drifting == [], drifting)
    outside = [
        (preset.id, param.name, param.default)
        for preset, param in exposed
        if param.minimum is not None
        and param.maximum is not None
        and not (param.minimum <= float(param.default) <= param.maximum)
    ]
    check("c8. every exposed default is inside its own exposed range", outside == [], outside)
    no_knobs = [preset.id for preset in all_rows if not preset.exposed]
    check("c9. every preset exposes at least one parameter", no_knobs == [], no_knobs)
    bad_kind = [
        (preset.id, param.name, param.kind)
        for preset, param in exposed
        if param.kind not in presets.PARAM_KINDS
    ]
    check("c10. every exposed kind is a declared value kind", bad_kind == [], bad_kind)

    print("\nd. every note states the look and that the numbers are a starting point")
    check(
        "d1. every note says 'starting point' and 'not a law'",
        all("starting point" in preset.notes and "not a law" in preset.notes for preset in all_rows),
        [preset.id for preset in all_rows if "starting point" not in preset.notes or "not a law" not in preset.notes],
    )
    check(
        "d2. every note is one sentence, not a placeholder",
        all(len(preset.notes) >= 100 and preset.notes.endswith("not a law.") and ". " not in preset.notes for preset in all_rows),
        [(preset.id, len(preset.notes)) for preset in all_rows if len(preset.notes) < 100],
    )
    labels = [preset.label for preset in all_rows]
    check("d3. no two rows share a label", len(set(labels)) == len(labels), sorted(labels))

    print("\ne. an unknown reference is refused, naming the declared ids")
    code = ""
    detail = ""
    try:
        presets.resolve("kodak_portra")
    except EDLError as exc:
        code, detail = exc.code, exc.detail
    check("e1. resolve('kodak_portra') raises EDLError(PRESET_UNKNOWN)", code == CODE, code)
    check("e2. the refusal names every declared id", all(name in detail for name in presets.names()), detail)
    blank = ""
    try:
        presets.resolve("   ")
    except EDLError as exc:
        blank = exc.code
    check("e3. a blank reference is refused too", blank == CODE, blank)
    check("e4. surrounding whitespace does not change the row", presets.resolve(" lens_wide ").id == "lens_wide", presets.resolve(" lens_wide ").id)

    print("\nf. check() fires on a hand-broken copy")
    base = presets.PRESETS["slow_push"]
    unknown_effect = presets.check(replace(base, effect="warp_drive"))
    check("f1. an unknown effect is reported", any("unknown effect" in item for item in unknown_effect), unknown_effect)
    broken_base = presets.check(replace(base, base={"zoom": 99.0}))
    check(
        "f2. an out-of-range base value is reported",
        any(item.startswith("slow_push.base.zoom") and "outside" in item for item in broken_base),
        broken_base,
    )
    undeclared_key = presets.check(replace(base, base={"zoom": 1.12, "warp": 1.0}))
    check("f3. an undeclared base key is reported", any("not a declared" in item for item in undeclared_key), undeclared_key)
    knob = replace(base, exposed=(replace(base.exposed[0], default=2.0),))
    drifting = presets.check(knob)
    check("f4. an exposed default that is not the base value is reported", any("is not the base value" in item for item in drifting), drifting)
    no_knobs = presets.check(replace(base, exposed=()))
    check("f5. an empty exposed list is reported", any("no parameter is exposed" in item for item in no_knobs), no_knobs)
    escaping = presets.check(replace(base, exposed=(replace(base.exposed[0], minimum=5.0, maximum=6.0),)))
    check("f6. an exposed range outside the ParamSpec is reported", any("escapes" in item for item in escaping), escaping)
    wrong_backend = presets.check(replace(base, effect="blur"))
    check("f7. an effect that is not the filter backend is reported", any("not the filter backend" in item for item in wrong_backend), wrong_backend)
    check("f8. the registry itself was never edited", presets.check(presets.PRESETS["slow_push"]) == (), presets.check(presets.PRESETS["slow_push"]))

    print("\ng. the registry is import-cheap")
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import vx.presets; print('numpy' in sys.modules, 'vx.ffmpeg' in sys.modules)",
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
        "g. importing vx.presets pulls neither numpy nor vx.ffmpeg",
        probe.stdout.strip() == "False False",
        (probe.stdout.strip(), probe.stderr.strip()[-200:]),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
