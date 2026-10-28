"""F4-T03 gate — the colour family holds together and every chain renders.

:mod:`vx.effects.color` declares twelve grade entries as data. This file pins
that declaration the way the geometry gate pins its own family:

* the twelve kinds exist, are owned by ``color`` and reach the registry;
* every entry is a static ``filter`` entry with a fallback, a cap, notes and a
  one-sentence trigger;
* every ``ParamSpec`` is coherent and every template placeholder names a
  declared parameter, in both directions;
* every template formats at its defaults, at ``lo`` and at ``hi``, and every
  ffmpeg filter it names exists in this build;
* the compiler routes each kind to ``filter`` and refuses a keyframed instance
  with ``EFFECT_NOT_ANIMATABLE``;
* every chain actually renders a frame that differs from the un-effected frame
  (sha256), because a template that formats but changes nothing is not an
  effect.

    python tests/test_effects_color.py
"""

from __future__ import annotations

import hashlib
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402
from vx import edl3_render  # noqa: E402
from vx import effects  # noqa: E402
from vx.effects import color  # noqa: E402
from vx.ffmpeg import ffmpeg as ffmpeg_binary  # noqa: E402
from vx.ffmpeg import run_ff  # noqa: E402

PASS = 0
FAIL = 0
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
SOURCE = "testsrc2=s=320x568:r=12:d=1"

COLOR_KINDS = (
    "curves",
    "hueshift",
    "selective_color",
    "white_balance",
    "duotone",
    "posterize",
    "threshold",
    "negative",
    "sepia",
    "teal_orange",
    "bleach_bypass",
    "day_for_night",
)


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


# ------------------------------------------------------------------ helpers


def document(effects_: List[Mapping[str, Any]]) -> edl3.Timeline3:
    return edl3.Timeline3.from_dict(
        {
            "version": edl3.EDL3_VERSION,
            "fps": 12,
            "size": [320, 568],
            "clips": [
                {
                    "id": "c1",
                    "source": "unused.mp4",
                    "src_in": 0.0,
                    "src_out": 1.0,
                    "layer": 0,
                    "dst_in": None,
                    "dst_out": None,
                    "effects": list(effects_),
                }
            ],
        }
    )


def chain_filters(fragment: str) -> List[str]:
    """The filter names in one chain, ignoring commas inside quoted arguments."""
    names: List[str] = []
    segment: List[str] = []
    quoted = False
    for char in fragment:
        if char == "'":
            quoted = not quoted
        if char == "," and not quoted:
            names.append("".join(segment))
            segment = []
            continue
        segment.append(char)
    names.append("".join(segment))
    return [name.split("=")[0].strip() for name in names if name.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_frame(fragment: str, target: Path) -> Tuple[bool, str]:
    """Render one frame through ``fragment``; return (ok, detail)."""
    proc = run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            SOURCE,
            "-vf",
            fragment,
            "-frames:v",
            "1",
            "-y",
            str(target),
        ],
        loglevel="error",
    )
    if not proc.ok or not target.exists():
        return False, str(proc.tail())[:200]
    if target.stat().st_size == 0:
        return False, "empty png"
    return True, f"{target.stat().st_size} B"


# ------------------------------------------------------------------- checks


def check_family() -> None:
    print("\n-- (a) the family declares the twelve colour kinds")
    check(
        "the color module declares exactly twelve entries",
        len(color.ENTRIES) == 12,
        len(color.ENTRIES),
    )
    check(
        "the twelve declared colour kinds are present",
        set(COLOR_KINDS) <= set(color.ENTRIES),
        sorted(set(COLOR_KINDS) - set(color.ENTRIES)),
    )
    check(
        "every entry is keyed by its own kind",
        all(kind == spec.kind for kind, spec in color.ENTRIES.items()),
        [kind for kind, spec in color.ENTRIES.items() if kind != spec.kind],
    )
    check(
        "no colour kind collides with the geometry family",
        not (set(color.ENTRIES) & set(effects.families().get("geometry", {}))),
        sorted(set(color.ENTRIES) & set(effects.families().get("geometry", {}))),
    )


def check_metadata(entries: Mapping[str, edl3.EffectSpec]) -> None:
    print("\n-- (b) every entry publishes the planning metadata")
    check(
        "every entry names the color family",
        all(spec.family == "color" for spec in entries.values()),
        {kind: spec.family for kind, spec in entries.items() if spec.family != "color"},
    )
    check(
        "every entry uses the filter backend",
        all(spec.backend == edl3.BACKEND_FILTER for spec in entries.values()),
        {
            kind: spec.backend
            for kind, spec in entries.items()
            if spec.backend != edl3.BACKEND_FILTER
        },
    )
    check(
        "no entry claims to animate",
        all(
            not spec.animatable and not spec.animated_params
            for spec in entries.values()
        ),
        [
            kind
            for kind, spec in entries.items()
            if spec.animatable or spec.animated_params
        ],
    )
    check(
        "every entry declares a known fallback",
        {spec.fallback for spec in entries.values()} <= {"skip", "static"},
        sorted({spec.fallback for spec in entries.values()}),
    )
    check(
        "every entry declares a non-negative cap",
        all(spec.cap_seconds >= 0.0 for spec in entries.values()),
        {
            kind: spec.cap_seconds
            for kind, spec in entries.items()
            if spec.cap_seconds < 0
        },
    )
    check(
        "every entry records its measured constraint",
        all(spec.notes.strip() for spec in entries.values()),
        [kind for kind, spec in entries.items() if not spec.notes.strip()],
    )
    check(
        "every entry carries a one-line trigger sentence",
        all(
            spec.trigger.strip()
            and "\n" not in spec.trigger
            and spec.trigger.strip().endswith(".")
            for spec in entries.values()
        ),
        [kind for kind, spec in entries.items() if not spec.trigger.strip()],
    )
    check(
        "no two entries share a trigger",
        len({spec.trigger for spec in entries.values()}) == len(entries),
        len(entries) - len({spec.trigger for spec in entries.values()}),
    )


def check_params(entries: Mapping[str, edl3.EffectSpec]) -> None:
    print("\n-- (c) every ParamSpec is coherent")
    check(
        "every entry declares one to three parameters",
        all(1 <= len(spec.params_spec) <= 3 for spec in entries.values()),
        {kind: len(spec.params_spec) for kind, spec in entries.items()},
    )
    check(
        "the derived name tuple is exactly the params_spec order",
        all(
            spec.params == tuple(param.name for param in spec.params_spec)
            for spec in entries.values()
        ),
        [kind for kind, spec in entries.items() if not spec.params_spec],
    )
    bounded = [
        f"{kind}.{param.name}"
        for kind, spec in entries.items()
        for param in spec.params_spec
        if not (param.lo < param.default < param.hi)
    ]
    check("every default sits strictly inside its range", bounded == [], bounded)
    constant = [
        f"{kind}.{param.name}"
        for kind, spec in entries.items()
        for param in spec.params_spec
        if param.lo == param.hi and param.default != param.lo
    ]
    check(
        "a constant range only exists when the default is that constant",
        constant == [],
        constant,
    )
    steps = [
        f"{kind}.{param.name}"
        for kind, spec in entries.items()
        for param in spec.params_spec
        if param.step < 0.0
    ]
    check("no parameter declares a negative step", steps == [], steps)
    duplicates = [
        kind
        for kind, spec in entries.items()
        if len({param.name for param in spec.params_spec}) != len(spec.params_spec)
    ]
    check("no entry declares a parameter twice", duplicates == [], duplicates)


def check_templates(entries: Mapping[str, edl3.EffectSpec]) -> None:
    print("\n-- (d) every template is declared, complete and formats")
    check(
        "every entry carries an ffmpeg template",
        all(spec.filter_template.strip() for spec in entries.values()),
        [kind for kind, spec in entries.items() if not spec.filter_template.strip()],
    )
    undeclared = [
        f"{kind}: {sorted(used - set(spec.params))}"
        for kind, spec in entries.items()
        for used in [set(PLACEHOLDER.findall(spec.filter_template))]
        if used - set(spec.params)
    ]
    check("every placeholder names a declared parameter", undeclared == [], undeclared)
    unused = [
        f"{kind}: {sorted(set(spec.params) - used)}"
        for kind, spec in entries.items()
        for used in [set(PLACEHOLDER.findall(spec.filter_template))]
        if set(spec.params) - used
    ]
    check("every declared parameter appears in its template", unused == [], unused)

    default_fragments: Dict[str, str] = {}
    broken: List[str] = []
    for kind, spec in entries.items():
        try:
            fragment = edl3_render.filter_fragment(edl3.Effect(kind=kind), spec)
        except Exception as exc:  # noqa: BLE001 - a template that cannot format fails
            broken.append(f"{kind}: {type(exc).__name__}: {exc}")
            continue
        default_fragments[kind] = fragment
        if "{" in fragment or "}" in fragment:
            broken.append(f"{kind}: unformatted placeholder in {fragment!r}")
    check("every template formats at its declared defaults", broken == [], broken)
    check(
        "no two entries format to the same chain",
        len(set(default_fragments.values())) == len(entries),
        len(entries) - len(set(default_fragments.values())),
    )

    edges: List[str] = []
    for kind, spec in entries.items():
        for name, value in (
            [(param.name, param.lo) for param in spec.params_spec]
            + [(param.name, param.hi) for param in spec.params_spec]
        ):
            try:
                fragment = edl3_render.filter_fragment(
                    edl3.Effect(kind=kind, params={name: value}), spec
                )
            except Exception as exc:  # noqa: BLE001 - an edge that cannot format fails
                edges.append(f"{kind}.{name}={value}: {type(exc).__name__}: {exc}")
                continue
            if "{" in fragment or "}" in fragment:
                edges.append(f"{kind}.{name}={value}: unformatted {fragment!r}")
    check("every template formats at both edges of every range", edges == [], edges)
    check(
        "no formatted value uses scientific notation",
        all(
            "e" not in edl3_render.filter_number(param.default).lower()
            for spec in entries.values()
            for param in spec.params_spec
        ),
        [
            f"{kind}.{param.name}"
            for kind, spec in entries.items()
            for param in spec.params_spec
            if "e" in edl3_render.filter_number(param.default).lower()
        ],
    )

    names: List[str] = []
    for fragment in default_fragments.values():
        names.extend(chain_filters(fragment))
    missing: List[str] = []
    for name in sorted(set(names)):
        proc = run_ff(["-hide_banner", "-h", f"filter={name}"], loglevel="error")
        if not proc.ok:
            missing.append(name)
    check(
        "every ffmpeg filter named in a template exists in this build",
        missing == [],
        (
            f"{len(set(names))} filter(s) used; missing {missing}"
            if missing
            else sorted(set(names))
        ),
    )


def check_registry(entries: Mapping[str, edl3.EffectSpec]) -> None:
    print("\n-- (e) the family reaches the registry and the compiler")
    grouped = effects.families()
    check(
        "the family loader finds the color module",
        "color" in grouped,
        sorted(grouped),
    )
    check(
        "the loader reads back the same twelve entries",
        grouped.get("color") == dict(color.ENTRIES),
        sorted(set(color.ENTRIES) - set(grouped.get("color", {}))),
    )
    check(
        "every colour kind is in the merged registry under its own spec",
        all(
            edl3.EFFECT_REGISTRY.get(kind) == spec
            for kind, spec in color.ENTRIES.items()
        ),
        [
            kind
            for kind, spec in color.ENTRIES.items()
            if edl3.EFFECT_REGISTRY.get(kind) != spec
        ],
    )
    check(
        "every colour kind is renderable and supported",
        set(color.ENTRIES) <= set(edl3.RENDERABLE_EFFECTS)
        and set(color.ENTRIES) <= set(edl3_render.SUPPORTED_EFFECTS),
        sorted(set(color.ENTRIES) - set(edl3.RENDERABLE_EFFECTS)),
    )
    check(
        "every colour kind registers exactly the filter backend",
        all(
            edl3_render.IMPLEMENTED_BACKENDS.get(kind) == frozenset({"filter"})
            for kind in color.ENTRIES
        ),
        {
            kind: sorted(edl3_render.IMPLEMENTED_BACKENDS.get(kind, ()))
            for kind in color.ENTRIES
        },
    )
    check(
        "the compiler's route chooses filter for every colour kind",
        all(
            edl3_render.select_backend(edl3.Effect(kind=kind))["chosen"] == "filter"
            for kind in color.ENTRIES
        ),
        {
            kind: edl3_render.select_backend(edl3.Effect(kind=kind))["chosen"]
            for kind in color.ENTRIES
        },
    )


def check_refusals() -> None:
    print("\n-- (f) keyframes and out-of-range values are refused, never flattened")
    keyed = document(
        [
            {
                "kind": "day_for_night",
                "params": {},
                "keyframes": {
                    "brightness": [
                        {"t": 0.0, "value": -0.1},
                        {"t": 0.5, "value": -0.4},
                    ]
                },
            }
        ]
    )
    codes = [problem["code"] for problem in keyed.validate()]
    check(
        "validation reports EFFECT_NOT_ANIMATABLE for a keyframed colour node",
        codes.count("EFFECT_NOT_ANIMATABLE") == 1,
        codes,
    )
    code = ""
    message = ""
    try:
        edl3_render.compile_timeline(keyed)
    except edl3_render.CompileError as exc:
        code = exc.code
        message = str(exc)
    check(
        "the compiler refuses it with EFFECT_NOT_ANIMATABLE",
        code == "EFFECT_NOT_ANIMATABLE",
        code or "no refusal",
    )
    check("the refusal names the kind", "day_for_night" in message, message)

    spec = color.ENTRIES["posterize"]
    step = spec.param("step")
    assert step is not None
    expected = f"posterize.step=2.0 outside [{step.lo:g}, {step.hi:g}]"
    check(
        "an out-of-range colour value returns the declared-range message",
        edl3.param_domain_problem("posterize", "step", 2.0) == expected,
        edl3.param_domain_problem("posterize", "step", 2.0),
    )
    check(
        "an in-range colour value is accepted",
        edl3.param_domain_problem("posterize", "step", step.default) is None,
        edl3.param_domain_problem("posterize", "step", step.default),
    )
    check(
        "an unknown name on a colour kind is named, not guessed",
        edl3.param_domain_problem("posterize", "nope", 1.0)
        == "posterize.nope is not a declared parameter",
        edl3.param_domain_problem("posterize", "nope", 1.0),
    )


def check_render(entries: Mapping[str, edl3.EffectSpec]) -> None:
    print("\n-- (g) every chain renders a frame that differs from the base")
    with tempfile.TemporaryDirectory(prefix="vx_color_render_") as tmp:
        work = Path(tmp)
        base = work / "base.png"
        ok, detail = render_frame("null", base)
        check("the un-effected frame renders", ok, detail)
        base_hash = sha256(base) if ok else ""

        differs = 0
        sizes: List[str] = []
        for kind, spec in entries.items():
            fragment = edl3_render.filter_fragment(edl3.Effect(kind=kind), spec)
            target = work / f"{kind}.png"
            rendered, note = render_frame(fragment, target)
            changed = rendered and sha256(target) != base_hash
            differs += bool(changed)
            sizes.append(f"{kind}={target.stat().st_size}B" if rendered else kind)
            check(
                f"{kind} changes the picture",
                changed,
                note if rendered else f"render failed: {note}",
            )
        check(
            "all twelve chains differ from the base frame",
            differs == len(entries),
            f"{differs}/{len(entries)}; {', '.join(sizes)}",
        )
    check(
        "the ffmpeg binary is the one this build reports",
        bool(ffmpeg_binary()),
        ffmpeg_binary(),
    )


def main() -> int:
    entries = dict(color.ENTRIES)
    print(f"ffmpeg: {ffmpeg_binary()}")
    check_family()
    check_metadata(entries)
    check_params(entries)
    check_templates(entries)
    check_registry(entries)
    check_refusals()
    check_render(entries)
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
