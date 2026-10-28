"""F4-T05 gate — the twelve film / light entries hold together.

:mod:`vx.effects.film` declares the stock, grain and gate looks as data: one
static ffmpeg chain each, with declared parameters, a planner trigger and notes
that record what the single chain actually does. This file pins that
declaration:

* the family loads, carries exactly the twelve declared kinds and owns none of
  another family's kinds;
* every entry is complete (family, trigger, fallback, cap, notes) and
  registered under its own kind;
* every ``ParamSpec`` is coherent, and the template's placeholders are exactly
  the declared parameter names;
* every template formats at its defaults and with an explicit override, and
  names only real ffmpeg filters;
* every template is one linear chain — no ``split``, ``blend`` or labelled
  graph — which is what the compiler's single-stage backend can emit;
* every entry routes to the ``filter`` backend, refuses keyframes, and lands in
  the compiled graph of a real EDL 3 document.

    python tests/test_effects_film.py
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

FILM_KINDS = (
    "grain",
    "vignette",
    "halation",
    "glow",
    "soft_focus",
    "bokeh",
    "light_leak",
    "lens_flare",
    "fog",
    "dreamcore",
    "x_ray",
    "infrared",
)
#: The two entries built on ``geq``: a per-pixel read, so they carry a cap and
#: are skipped past it instead of being paid for on a long clip.
PER_PIXEL = ("light_leak", "lens_flare")
#: Filters a linear chain may name here. Anything else is an unverified guess.
ALLOWED_FILTERS = (
    "blend",
    "boxblur",
    "colorbalance",
    "colorchannelmixer",
    "curves",
    "edgedetect",
    "eq",
    "gblur",
    "geq",
    "hue",
    "lagfun",
    "lut",
    "lutrgb",
    "negate",
    "noise",
    "split",
    "tmix",
    "unsharp",
    "vignette",
)
#: A graph would need a label (``[a]``) or a ``split``/``blend`` pair. This
#: family's backend is a single chain, so none of those may appear.
GRAPH_MARKERS = ("[", "]", "split", "blend")


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def entries() -> Dict[str, edl3.EffectSpec]:
    grouped = effects.families()
    return dict(grouped.get("film", {}))


def chain(template: str) -> List[str]:
    """Split a template into filter names, ignoring commas inside quotes."""
    parts: List[str] = []
    current: List[str] = []
    quoted = False
    for char in template:
        if char == "'":
            quoted = not quoted
        if char == "," and not quoted:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [part.split("=", 1)[0].strip() for part in parts if part.strip()]


def document(effects_: List[Mapping[str, Any]], source: str) -> edl3.Timeline3:
    return edl3.Timeline3.from_dict(
        {
            "version": edl3.EDL3_VERSION,
            "fps": 12,
            "size": [320, 568],
            "clips": [
                {
                    "id": "c1",
                    "source": source,
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


def check_family(flat: Mapping[str, edl3.EffectSpec]) -> None:
    print("\n-- (a) the family loads with exactly the twelve declared kinds")
    grouped = effects.families()
    check("the film module is loaded", "film" in grouped, sorted(grouped))
    check(
        "film declares exactly the twelve kinds",
        set(flat) == set(FILM_KINDS) and len(flat) == 12,
        sorted(set(FILM_KINDS) ^ set(flat)),
    )
    check(
        "every entry is the module's own object, registered under its kind",
        all(
            edl3.EFFECT_REGISTRY.get(kind) is spec and spec.kind == kind
            for kind, spec in flat.items()
        ),
        [
            kind
            for kind, spec in flat.items()
            if edl3.EFFECT_REGISTRY.get(kind) is not spec or spec.kind != kind
        ],
    )
    owned = {
        kind
        for name, table in grouped.items()
        if name != "film"
        for kind in table
    }
    check(
        "the family claims no kind another family already declares",
        not (set(flat) & owned),
        sorted(set(flat) & owned),
    )


def check_metadata(flat: Mapping[str, edl3.EffectSpec]) -> None:
    print("\n-- (b) every entry publishes the planning metadata")
    check(
        "every entry names the film family",
        all(spec.family == "film" for spec in flat.values()),
        {kind: spec.family for kind, spec in flat.items() if spec.family != "film"},
    )
    check(
        "every entry carries a one-line trigger",
        all(spec.trigger and "\n" not in spec.trigger for spec in flat.values()),
        [kind for kind, spec in flat.items() if not spec.trigger],
    )
    check(
        "every entry records what its chain does in notes",
        all(spec.notes.strip() for spec in flat.values()),
        [kind for kind, spec in flat.items() if not spec.notes.strip()],
    )
    check(
        "every fallback is one the contract knows",
        {spec.fallback for spec in flat.values()} <= {"skip", "static"},
        sorted({spec.fallback for spec in flat.values()}),
    )
    check(
        "every entry is declared only on the filter backend",
        all(spec.backend == edl3.BACKEND_FILTER for spec in flat.values()),
        {kind: spec.backend for kind, spec in flat.items() if spec.backend != "filter"},
    )
    check(
        "no film entry claims to animate",
        all(not spec.animatable and not spec.animated_params for spec in flat.values()),
        [kind for kind, spec in flat.items() if spec.animatable],
    )
    check(
        "no declared cap is negative",
        all(spec.cap_seconds >= 0.0 for spec in flat.values()),
        {kind: spec.cap_seconds for kind, spec in flat.items() if spec.cap_seconds < 0},
    )
    check(
        "the per-pixel geq entries are capped and skipped",
        all(
            flat[kind].cap_seconds > 0.0 and flat[kind].fallback == "skip"
            for kind in PER_PIXEL
        ),
        {kind: (flat[kind].cap_seconds, flat[kind].fallback) for kind in PER_PIXEL},
    )


def check_params(flat: Mapping[str, edl3.EffectSpec]) -> None:
    print("\n-- (c) every declared parameter is coherent")
    check(
        "every entry declares one to three parameters",
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
    check(
        "every default sits inside its declared range",
        all(
            param.lo <= param.default <= param.hi
            for spec in flat.values()
            for param in spec.params_spec
        ),
        [
            f"{kind}.{param.name}"
            for kind, spec in flat.items()
            for param in spec.params_spec
            if not (param.lo <= param.default <= param.hi)
        ],
    )
    check(
        "every range is non-degenerate and every step is non-negative",
        all(
            param.lo < param.hi and param.step >= 0.0
            for spec in flat.values()
            for param in spec.params_spec
        ),
        [
            f"{kind}.{param.name}"
            for kind, spec in flat.items()
            for param in spec.params_spec
            if not (param.lo < param.hi and param.step >= 0.0)
        ],
    )
    check(
        "no entry declares a parameter twice",
        all(
            len({param.name for param in spec.params_spec}) == len(spec.params_spec)
            for spec in flat.values()
        ),
        [
            kind
            for kind, spec in flat.items()
            if len({param.name for param in spec.params_spec}) != len(spec.params_spec)
        ],
    )
    check(
        "the edge of every declared range is accepted, and one past it is not",
        all(
            param.contains(param.lo)
            and param.contains(param.hi)
            and not param.contains(param.hi + 1.0)
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
    print("\n-- (d) every template is one real linear chain")
    check(
        "every entry carries a template",
        all(spec.filter_template.strip() for spec in flat.values()),
        [kind for kind, spec in flat.items() if not spec.filter_template.strip()],
    )
    placeholders = {
        kind: set(PLACEHOLDER.findall(spec.filter_template))
        for kind, spec in flat.items()
    }
    check(
        "every placeholder names a declared parameter",
        all(used <= set(flat[kind].params) for kind, used in placeholders.items()),
        {
            kind: sorted(used - set(flat[kind].params))
            for kind, used in placeholders.items()
            if used - set(flat[kind].params)
        },
    )
    check(
        "every declared parameter is used by its template",
        all(
            used == set(flat[kind].params) for kind, used in placeholders.items()
        ),
        {
            kind: sorted(set(flat[kind].params) - used)
            for kind, used in placeholders.items()
            if set(flat[kind].params) - used
        },
    )
    broken: List[str] = []
    for kind, spec in flat.items():
        try:
            fragment = edl3_render.filter_fragment(edl3.Effect(kind=kind), spec)
        except Exception as exc:  # noqa: BLE001 - a template that cannot format fails
            broken.append(f"{kind}: {type(exc).__name__}: {exc}")
            continue
        if "{" in fragment or "}" in fragment:
            broken.append(f"{kind}: unformatted placeholder in {fragment!r}")
    check("every template formats at its declared defaults", broken == [], broken)

    names = {kind: chain(spec.filter_template) for kind, spec in flat.items()}
    check(
        "every template names at least one filter",
        all(len(parts) >= 1 for parts in names.values()),
        {kind: parts for kind, parts in names.items() if not parts},
    )
    unknown = {
        kind: sorted(set(parts) - set(ALLOWED_FILTERS))
        for kind, parts in names.items()
        if set(parts) - set(ALLOWED_FILTERS)
    }
    check("every template names only real ffmpeg filters", unknown == {}, unknown)
    graph = {
        kind: [marker for marker in GRAPH_MARKERS if marker in spec.filter_template]
        for kind, spec in flat.items()
        if any(marker in spec.filter_template for marker in GRAPH_MARKERS)
    }
    check(
        "every template is a linear chain, not a labelled graph",
        graph == {},
        graph,
    )
    check(
        "no template asks for text or a font",
        all(
            "drawtext" not in spec.filter_template
            and "fontfile" not in spec.filter_template
            and "font=" not in spec.filter_template
            for spec in flat.values()
        ),
        [
            kind
            for kind, spec in flat.items()
            if "drawtext" in spec.filter_template
            or "fontfile" in spec.filter_template
        ],
    )
    check(
        "an explicit parameter overrides the declared default",
        edl3_render.filter_fragment(
            edl3.Effect(kind="grain", params={"strength": 40.0}), flat["grain"]
        )
        == "noise=alls=40:allf=t+u",
        edl3_render.filter_fragment(
            edl3.Effect(kind="grain", params={"strength": 40.0}), flat["grain"]
        ),
    )
    check(
        "the default fragment is the declared template at its defaults",
        edl3_render.filter_fragment(edl3.Effect(kind="vignette"), flat["vignette"])
        == "vignette=angle=0.6",
        edl3_render.filter_fragment(edl3.Effect(kind="vignette"), flat["vignette"]),
    )
    scientific = [
        f"{kind}.{param.name}={edl3_render.filter_number(param.default)}"
        for kind, spec in flat.items()
        for param in spec.params_spec
        if "e" in edl3_render.filter_number(param.default).lower()
    ]
    check(
        "no parameter formats into scientific notation",
        scientific == [],
        scientific,
    )


def check_routing(flat: Mapping[str, edl3.EffectSpec]) -> None:
    print("\n-- (e) every entry routes to the filter backend and refuses keyframes")
    check(
        "every film kind is renderable",
        set(flat) <= set(edl3.RENDERABLE_EFFECTS),
        sorted(set(flat) - set(edl3.RENDERABLE_EFFECTS)),
    )
    check(
        "every film kind is in the compiler's supported list",
        set(flat) <= set(edl3_render.SUPPORTED_EFFECTS),
        sorted(set(flat) - set(edl3_render.SUPPORTED_EFFECTS)),
    )
    check(
        "every film kind registers exactly the filter backend",
        all(
            edl3_render.IMPLEMENTED_BACKENDS.get(kind) == frozenset({"filter"})
            for kind in flat
        ),
        {
            kind: sorted(edl3_render.IMPLEMENTED_BACKENDS.get(kind, ()))
            for kind in flat
            if edl3_render.IMPLEMENTED_BACKENDS.get(kind) != frozenset({"filter"})
        },
    )
    check(
        "the pure rule returns filter for every film kind",
        all(edl3.select_backend(kind, 0, 1.0) == "filter" for kind in flat),
        {kind: edl3.select_backend(kind, 0, 1.0) for kind in flat},
    )
    check(
        "an out-of-range film value returns the declared-range message",
        edl3.param_domain_problem("grain", "strength", 900.0)
        == "grain.strength=900.0 outside [0, 100]",
        edl3.param_domain_problem("grain", "strength", 900.0),
    )
    check(
        "an unknown name on a film kind is named, not guessed",
        edl3.param_domain_problem("grain", "size", 1.0)
        == "grain.size is not a declared parameter",
        edl3.param_domain_problem("grain", "size", 1.0),
    )
    code = ""
    message = ""
    keyed = document(
        [
            {
                "kind": "grain",
                "params": {},
                "keyframes": {"strength": [{"t": 0.0, "value": 10.0}, {"t": 0.5, "value": 30.0}]},
            }
        ],
        str(Path(__file__).resolve()),
    )
    try:
        edl3_render.compile_timeline(keyed)
    except edl3_render.CompileError as exc:
        code = exc.code
        message = str(exc)
    check(
        "a keyframed film instance is refused with EFFECT_NOT_ANIMATABLE",
        code == "EFFECT_NOT_ANIMATABLE",
        code or "no refusal",
    )
    check("the refusal names the kind", "grain" in message, message)


def check_compile(flat: Mapping[str, edl3.EffectSpec]) -> None:
    print("\n-- (f) every template lands in a compiled EDL 3 graph")
    # compile_timeline is pure: it only checks that the source exists, it never
    # opens it. This file is a stable existing path to satisfy that check.
    source = str(Path(__file__).resolve())
    missing: List[str] = []
    absent: List[str] = []
    restore = 0
    for kind, spec in flat.items():
        fragment = edl3_render.filter_fragment(edl3.Effect(kind=kind), spec)
        plan: Dict[str, Any] = {}
        try:
            plan = edl3_render.compile_timeline(
                document([{"kind": kind, "params": {}}], source)
            )
        except Exception as exc:  # noqa: BLE001 - a kind that cannot compile fails
            missing.append(f"{kind}: {type(exc).__name__}: {exc}")
            continue
        text = repr(plan)
        if fragment not in text:
            absent.append(kind)
        if "settb=1/12" in text:
            restore += 1
    check("every film kind compiles in a real document", missing == [], missing)
    check(
        "every film fragment appears in the compiled graph",
        absent == [],
        absent,
    )
    check(
        "the stage's canvas and rate restore follows every film fragment",
        restore == len(flat),
        f"{restore}/{len(flat)}",
    )


def main() -> int:
    flat = entries()
    if not flat:
        print("  [FAIL] the film family is empty")
        return 1
    check_family(flat)
    check_metadata(flat)
    check_params(flat)
    check_templates(flat)
    check_routing(flat)
    check_compile(flat)
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
