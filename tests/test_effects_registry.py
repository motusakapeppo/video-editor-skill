"""F4-T01 gate â€” the declarative effect schema holds together.

The registry is no longer five hand-written kinds: :mod:`vx.effects` declares
families as data, each entry publishing its parameters, its ffmpeg template and
the planner hint that should trigger it. This file pins that declaration:

* the registry grew, the five contract kinds kept their params and backends;
* every declarative entry is complete (family, trigger, fallback, cap, notes);
* every ``ParamSpec`` is coherent (lo <= default <= hi) and every template
  placeholder names a declared parameter, and every declared parameter is used;
* the formatter emits plain decimals, never an exponent or a trailing zero;
* the compiler routes every declarative kind to the ``filter`` backend and
  refuses a keyframed instance with ``EFFECT_NOT_ANIMATABLE``;
* an out-of-range value is refused with the exact message the contract states.

    python tests/test_effects_registry.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402
from vx import edl3_render  # noqa: E402
from vx import effects  # noqa: E402

PASS = 0
FAIL = 0
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
#: Placeholders the emitter fills from the plan, not from the node: the canvas
#: the clip is staged at. They are not ``ParamSpec`` entries because no document
#: may set them — ``zoompan`` and ``dolly_zoom`` ask the plan for its size
#: instead of letting ``zoompan`` fall back to its 1280x720 default, which a
#: 1080x1920 stage then stretched to 1920 lines (D-10).
RESERVED = {"canvas_w", "canvas_h"}
#: The canvas ``document()`` below declares, so a template is formatted for the
#: size it would really be rendered at.
DOC_CANVAS = (320, 568)


GEOMETRY_KINDS = (
    "zoompan",
    "rotate",
    "flip_h",
    "flip_v",
    "mirror",
    "shake",
    "whip_pan",
    "dolly_zoom",
    "tilt_shift",
    "fisheye",
    "pixelate",
    "swirl",
)
CORE = {
    "transform": (("scale", "opacity"), "expr"),
    "crop": (("x", "y", "width", "height"), "expr"),
    "color": (("brightness", "contrast", "saturation"), "sendcmd"),
    "blur": (("sigma",), "expr"),
    "chroma_key": (("similarity", "blend"), "frames"),
}


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


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


def family_specs() -> Dict[str, edl3.EffectSpec]:
    grouped = effects.families()
    flat: Dict[str, edl3.EffectSpec] = {}
    for entries in grouped.values():
        flat.update(entries)
    return flat


def check_registry() -> None:
    print("\n-- (a) the registry is the core five plus the declared families")
    check(
        "the registry declares at least 17 kinds",
        len(edl3.EFFECT_REGISTRY) >= 17,
        len(edl3.EFFECT_REGISTRY),
    )
    check(
        "every core kind survived the merge",
        set(CORE) <= set(edl3.EFFECT_REGISTRY),
        sorted(set(CORE) - set(edl3.EFFECT_REGISTRY)),
    )
    grouped = effects.families()
    check(
        "the family loader finds the geometry module",
        "geometry" in grouped,
        sorted(grouped),
    )
    check(
        "only declared family modules are loaded",
        set(grouped) <= set(effects.FAMILY_MODULES),
        sorted(grouped),
    )
    check(
        "geometry declares at least 12 entries",
        len(grouped.get("geometry", {})) >= 12,
        len(grouped.get("geometry", {})),
    )
    check(
        "the twelve declared geometry kinds are present",
        set(GEOMETRY_KINDS) <= set(grouped.get("geometry", {})),
        sorted(set(GEOMETRY_KINDS) - set(grouped.get("geometry", {}))),
    )
    check(
        "every entry is registered under its own kind",
        all(
            kind == spec.kind for kind, spec in edl3.EFFECT_REGISTRY.items()
        ),
        [kind for kind, spec in edl3.EFFECT_REGISTRY.items() if kind != spec.kind],
    )


def check_metadata(flat: Mapping[str, edl3.EffectSpec]) -> None:
    print("\n-- (b) every declarative entry publishes the planning metadata")
    check(
        "every declarative entry names its family",
        all(spec.family for spec in flat.values()),
        [kind for kind, spec in flat.items() if not spec.family],
    )
    check(
        "the family field matches the group it was loaded from",
        all(
            spec.family == family
            for family, entries in effects.families().items()
            for spec in entries.values()
        ),
        [
            f"{kind}: {spec.family}"
            for family, entries in effects.families().items()
            for kind, spec in entries.items()
            if spec.family != family
        ],
    )
    check(
        "every declarative entry carries a one-line trigger",
        all(spec.trigger and "\n" not in spec.trigger for spec in flat.values()),
        [kind for kind, spec in flat.items() if not spec.trigger],
    )
    check(
        "every declarative entry declares a known fallback",
        {spec.fallback for spec in flat.values()} <= {"skip", "static"},
        sorted({spec.fallback for spec in flat.values()}),
    )
    check(
        "every declarative entry declares a non-negative cap",
        all(spec.cap_seconds >= 0.0 for spec in flat.values()),
        {kind: spec.cap_seconds for kind, spec in flat.items() if spec.cap_seconds < 0},
    )
    check(
        "every declarative entry records its measured constraint",
        all(spec.notes.strip() for spec in flat.values()),
        [kind for kind, spec in flat.items() if not spec.notes.strip()],
    )
    check(
        "every declarative entry uses the filter backend",
        all(spec.backend == edl3.BACKEND_FILTER for spec in flat.values()),
        {kind: spec.backend for kind, spec in flat.items() if spec.backend != "filter"},
    )
    check(
        "no declarative entry claims to animate",
        all(not spec.animatable and not spec.animated_params for spec in flat.values()),
        [
            kind
            for kind, spec in flat.items()
            if spec.animatable or spec.animated_params
        ],
    )


def check_params(flat: Mapping[str, edl3.EffectSpec]) -> None:
    print("\n-- (c) every ParamSpec is coherent and used")
    check(
        "every declarative entry declares one to three parameters",
        all(1 <= len(spec.params_spec) <= 3 for spec in flat.values()),
        {kind: len(spec.params_spec) for kind, spec in flat.items()},
    )
    check(
        "the derived name tuple is exactly the params_spec order",
        all(
            spec.params == tuple(param.name for param in spec.params_spec)
            for spec in flat.values()
        ),
        [kind for kind, spec in flat.items() if not spec.params_spec],
    )
    bounded = [
        f"{kind}.{param.name}"
        for kind, spec in flat.items()
        for param in spec.params_spec
        if not (param.lo <= param.default <= param.hi)
    ]
    check("every default sits inside its declared range", bounded == [], bounded)
    steps = [
        f"{kind}.{param.name}"
        for kind, spec in flat.items()
        for param in spec.params_spec
        if param.step < 0.0
    ]
    check("no parameter declares a negative step", steps == [], steps)
    duplicates = [
        kind
        for kind, spec in flat.items()
        if len({param.name for param in spec.params_spec}) != len(spec.params_spec)
    ]
    check("no entry declares a parameter twice", duplicates == [], duplicates)
    check(
        "the edge of every declared range is accepted",
        all(
            param.contains(param.lo) and param.contains(param.hi)
            for spec in flat.values()
            for param in spec.params_spec
        ),
        [
            f"{kind}.{param.name}"
            for kind, spec in flat.items()
            for param in spec.params_spec
            if not (param.contains(param.lo) and param.contains(param.hi))
        ],
    )


def check_templates(flat: Mapping[str, edl3.EffectSpec]) -> None:
    print("\n-- (d) every template is declared, complete and formats")
    check(
        "every declarative entry carries an ffmpeg template",
        all(spec.filter_template.strip() for spec in flat.values()),
        [kind for kind, spec in flat.items() if not spec.filter_template.strip()],
    )
    undeclared = [
        f"{kind}: {sorted(used - set(spec.params) - RESERVED)}"
        for kind, spec in flat.items()
        for used in [set(PLACEHOLDER.findall(spec.filter_template))]
        if used - set(spec.params) - RESERVED
    ]
    check(
        "every placeholder names a declared parameter or a reserved canvas size",
        undeclared == [],
        undeclared,
    )

    unused = [
        f"{kind}: {sorted(set(spec.params) - used)}"
        for kind, spec in flat.items()
        for used in [set(PLACEHOLDER.findall(spec.filter_template))]
        if set(spec.params) - used
    ]
    check(
        "every declared parameter appears in its template",
        unused == [],
        unused,
    )
    broken: List[str] = []
    for kind, spec in flat.items():
        try:
            fragment = edl3_render.filter_fragment(
                edl3.Effect(kind=kind), spec, DOC_CANVAS
            )
        except Exception as exc:  # noqa: BLE001 - a template that cannot format fails
            broken.append(f"{kind}: {type(exc).__name__}: {exc}")
            continue
        if "{" in fragment or "}" in fragment:
            broken.append(f"{kind}: unformatted placeholder in {fragment!r}")
    check(
        "every template formats at its declared defaults",
        broken == [],
        broken,
    )
    check(
        "number formatting strips the trailing zero",
        edl3_render.filter_number(1.0) == "1"
        and edl3_render.filter_number(0.35) == "0.35"
        and edl3_render.filter_number(16.0) == "16",
        [
            edl3_render.filter_number(1.0),
            edl3_render.filter_number(0.35),
            edl3_render.filter_number(16.0),
        ],
    )
    scientific = [
        f"{kind}.{param.name}={edl3_render.filter_number(param.default)}"
        for kind, spec in flat.items()
        for param in spec.params_spec
        if "e" in edl3_render.filter_number(param.default).lower()
    ]
    check(
        "no formatted parameter uses scientific notation",
        scientific == [],
        scientific,
    )
    pixelate = edl3.EFFECT_REGISTRY["pixelate"]
    check(
        "pixelate formats its declared size into the filter",
        edl3_render.filter_fragment(edl3.Effect(kind="pixelate"), pixelate, DOC_CANVAS)
        == "pixelize=w=16:h=16:mode=avg",
        edl3_render.filter_fragment(edl3.Effect(kind="pixelate"), pixelate, DOC_CANVAS),
    )
    check(
        "an explicit parameter overrides the default",
        edl3_render.filter_fragment(
            edl3.Effect(kind="pixelate", params={"size": 8.0}), pixelate, DOC_CANVAS
        )
        == "pixelize=w=8:h=8:mode=avg",
        edl3_render.filter_fragment(
            edl3.Effect(kind="pixelate", params={"size": 8.0}), pixelate, DOC_CANVAS
        ),
    )


def check_core_entries() -> None:
    print("\n-- (e) the five contract kinds are untouched by the families")
    for kind, (params, backend) in CORE.items():
        spec = edl3.EFFECT_REGISTRY[kind]
        check(
            f"{kind} keeps its params and backend",
            spec.params == params and spec.backend == backend,
            f"{list(spec.params)} / {spec.backend}",
        )
    check(
        "transform still animates opacity and not scale",
        edl3.EFFECT_REGISTRY["transform"].animated_params == ("opacity",),
        edl3.EFFECT_REGISTRY["transform"].animated_params,
    )
    check(
        "chroma_key keeps its empty fallback",
        edl3.EFFECT_REGISTRY["chroma_key"].fallback == "",
        repr(edl3.EFFECT_REGISTRY["chroma_key"].fallback),
    )
    owners = {
        kind
        for entries in effects.families().values()
        for kind in entries
    }
    check(
        "no family claims a core kind",
        not (owners & set(CORE)),
        sorted(owners & set(CORE)),
    )


def check_routing(flat: Mapping[str, edl3.EffectSpec]) -> None:
    print("\n-- (f) the compiler routes every declarative kind to the filter backend")
    check(
        "the two modules agree on the filter backend name",
        edl3.BACKEND_FILTER == edl3_render.BACKEND_FILTER == "filter",
        f"{edl3.BACKEND_FILTER} / {edl3_render.BACKEND_FILTER}",
    )
    check(
        "every declarative kind is renderable",
        set(flat) <= set(edl3.RENDERABLE_EFFECTS),
        sorted(set(flat) - set(edl3.RENDERABLE_EFFECTS)),
    )
    check(
        "chroma_key stays unrenderable",
        "chroma_key" not in edl3.RENDERABLE_EFFECTS,
        "chroma_key" in edl3.RENDERABLE_EFFECTS,
    )
    check(
        "every declarative kind is in the compiler's supported list",
        set(flat) <= set(edl3_render.SUPPORTED_EFFECTS),
        sorted(set(flat) - set(edl3_render.SUPPORTED_EFFECTS)),
    )
    check(
        "every declarative kind registers exactly the filter backend",
        all(
            edl3_render.IMPLEMENTED_BACKENDS.get(kind) == frozenset({"filter"})
            for kind in flat
        ),
        {kind: sorted(edl3_render.IMPLEMENTED_BACKENDS.get(kind, ())) for kind in flat},
    )
    check(
        "the pure rule returns filter for a static declarative kind",
        all(
            edl3.select_backend(kind, 0, 1.0) == "filter" for kind in flat
        ),
        {kind: edl3.select_backend(kind, 0, 1.0) for kind in flat},
    )
    check(
        "the compiler's route chooses filter for every declarative kind",
        all(
            edl3_render.select_backend(edl3.Effect(kind=kind))["chosen"] == "filter"
            for kind in flat
        ),
        {
            kind: edl3_render.select_backend(edl3.Effect(kind=kind))["chosen"]
            for kind in flat
        },
    )


def check_validation() -> None:
    print("\n-- (g) the generic validator enforces the declared ranges")
    param = edl3.EFFECT_REGISTRY["zoompan"].param("zoom")
    assert param is not None
    expected = f"zoompan.zoom=9.0 outside [{param.lo:g}, {param.hi:g}]"
    check(
        "an out-of-range value returns the declared-range message",
        edl3.param_domain_problem("zoompan", "zoom", 9.0) == expected,
        edl3.param_domain_problem("zoompan", "zoom", 9.0),
    )
    problems = document([{"kind": "zoompan", "params": {"zoom": 9.0}}]).validate()
    ranges = [
        problem
        for problem in problems
        if problem["code"] == "EFFECT_RANGE" and problem["where"].endswith("zoom")
    ]
    check(
        "a document with that value reports EFFECT_RANGE with the same detail",
        len(ranges) == 1 and ranges[0]["detail"] == expected,
        ranges,
    )
    check(
        "an unknown name on a declarative kind is named, not guessed",
        edl3.param_domain_problem("zoompan", "nope", 1.0)
        == "zoompan.nope is not a declared parameter",
        edl3.param_domain_problem("zoompan", "nope", 1.0),
    )
    check(
        "an in-range value is accepted",
        edl3.param_domain_problem("zoompan", "zoom", param.default) is None,
        edl3.param_domain_problem("zoompan", "zoom", param.default),
    )
    check(
        "the core kinds keep their own explicit messages",
        edl3.param_domain_problem("blur", "sigma", 0.0)
        == "blur.sigma=0.0 outside (0, 100]",
        edl3.param_domain_problem("blur", "sigma", 0.0),
    )


def check_keyframes() -> None:
    print("\n-- (h) a keyframed declarative instance is refused, never flattened")
    keyed = document(
        [
            {
                "kind": "zoompan",
                "params": {},
                "keyframes": {
                    "zoom": [
                        {"t": 0.0, "value": 1.2},
                        {"t": 0.5, "value": 2.0},
                    ]
                },
            }
        ]
    )
    codes = [problem["code"] for problem in keyed.validate()]
    details = [problem["detail"] for problem in keyed.validate()]
    check(
        "validation reports EFFECT_NOT_ANIMATABLE for the keyframed instance",
        codes.count("EFFECT_NOT_ANIMATABLE") == 1,
        codes,
    )
    check(
        "the validation detail names the kind",
        any("zoompan" in detail for detail in details),
        details,
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
    check(
        "the refusal names the kind",
        "zoompan" in message,
        message,
    )


def check_loader() -> None:
    print("\n-- (a2) an absent family is skipped, a broken one is not")
    real = effects.importlib.import_module

    def absent(name: str) -> Any:
        if name == "vx.effects.color":
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real(name)

    effects.importlib.import_module = absent
    try:
        found = effects.families()
    finally:
        effects.importlib.import_module = real
    check(
        "an absent family module is skipped silently",
        "color" not in found and "geometry" in found,
        sorted(found),
    )

    def broken(name: str) -> Any:
        if name == "vx.effects.color":
            raise ModuleNotFoundError("No module named 'numpy'", name="numpy")
        return real(name)

    effects.importlib.import_module = broken
    raised = ""
    try:
        effects.families()
    except ModuleNotFoundError as exc:
        raised = exc.name or ""
    finally:
        effects.importlib.import_module = real
    check(
        "a family module broken for another reason is not swallowed",
        raised == "numpy",
        raised or "silently skipped",
    )


def main() -> int:
    flat = family_specs()
    check_registry()
    check_loader()
    check_metadata(flat)
    check_params(flat)
    check_templates(flat)
    check_core_entries()
    check_routing(flat)
    check_validation()
    check_keyframes()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

