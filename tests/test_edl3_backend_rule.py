"""F2-T12 gate - every registry entry has a backend rule, not just a string.

``vx.edl3.select_backend(kind, keyframe_count, duration)`` is the pure rule.
This file generates its cases from :data:`vx.edl3.EFFECT_REGISTRY`, so a new
kind without a rule fails here naming the kind, and it cross-checks the rule
against :func:`vx.edl3_render.select_backend` so the two cannot drift.

    python tests/test_edl3_backend_rule.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402
from vx import edl3_render  # noqa: E402

PASS = 0
FAIL = 0
DURATION = 2.0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def effect_for(kind: str, keys: int) -> edl3.Effect:
    """A registry-valid effect with ``keys`` keyframes on its animated track."""
    spec = edl3.EFFECT_REGISTRY[kind]
    param = spec.animated_params[0]
    value = 0.0 if kind == "color" else 0.5
    points = [
        edl3.Keyframe(t=index * 0.1, value=value) for index in range(keys)
    ]
    return edl3.Effect(
        kind=kind,
        params={},
        keyframes={param: edl3.KeyframeTrack(points)},
    )


def rule(kind: str, keys: int) -> str:
    try:
        return edl3.select_backend(kind, keys, DURATION)
    except edl3.EDLError as exc:
        return f"__REFUSED__{exc.code}"


def main() -> int:
    animatable = sorted(
        kind for kind, spec in edl3.EFFECT_REGISTRY.items() if spec.animatable
    )
    static = sorted(
        kind for kind, spec in edl3.EFFECT_REGISTRY.items() if not spec.animatable
    )

    print("\n-- (a) every animatable entry has a rule at 1, 3 and 5 keyframes")
    check("the registry has animatable entries to test", bool(animatable), animatable)
    for kind in animatable:
        for keys in (1, 3, 5):
            mechanism = rule(kind, keys)
            check(
                f"{kind}: {keys} keyframe(s) return a mechanism",
                bool(mechanism) and not mechanism.startswith("__REFUSED__"),
                mechanism,
            )

    print("\n-- (b) the rule returns what the renderer already picks")
    for kind in animatable:
        for keys in (1, 2, 3, 4, 5, 6):
            mechanism = rule(kind, keys)
            chosen = edl3_render.select_backend(effect_for(kind, keys))["chosen"]
            check(
                f"{kind}: {keys} keyframe(s) match the renderer",
                mechanism == chosen,
                f"rule={mechanism} renderer={chosen}",
            )

    print("\n-- (c) a static entry returns its declared backend")
    for kind in static:
        spec = edl3.EFFECT_REGISTRY[kind]
        mechanism = rule(kind, 0)
        check(
            f"{kind}: static entry returns {spec.backend!r}",
            mechanism == spec.backend,
            mechanism,
        )

    print("\n-- (d) thresholds and flexible kinds come from one number")
    check(
        "edl3.SENDCMD_THRESHOLD equals edl3_render.SENDCMD_THRESHOLD",
        edl3.SENDCMD_THRESHOLD == edl3_render.SENDCMD_THRESHOLD,
        f"{edl3.SENDCMD_THRESHOLD} vs {edl3_render.SENDCMD_THRESHOLD}",
    )
    render_kinds = {kind for kind, _param in edl3_render.SENDCMD_TRACKS}
    check(
        "edl3.FLEXIBLE_EXPR_KINDS equals the renderer's flexible kinds",
        edl3.FLEXIBLE_EXPR_KINDS == render_kinds,
        f"{sorted(edl3.FLEXIBLE_EXPR_KINDS)} vs {sorted(render_kinds)}",
    )
    if "transform" in edl3.EFFECT_REGISTRY:
        check(
            "transform flips to sendcmd exactly above the threshold",
            rule("transform", edl3.SENDCMD_THRESHOLD) == "expr"
            and rule("transform", edl3.SENDCMD_THRESHOLD + 1) == "sendcmd",
            f"{rule('transform', edl3.SENDCMD_THRESHOLD)} / "
            f"{rule('transform', edl3.SENDCMD_THRESHOLD + 1)}",
        )
    if "crop" in edl3.EFFECT_REGISTRY:
        check(
            "crop keeps its time-variable expr at 5 keyframes",
            rule("crop", 5) == "expr",
            rule("crop", 5),
        )

    print("\n-- (e) texts are composited, unknown kinds are refused")
    check(
        "a text layer returns overlay",
        edl3.select_backend("text", 2, DURATION) == edl3.TEXT_BACKEND,
        edl3.TEXT_BACKEND,
    )
    check(
        "overlay is not a registry backend (text is its own entity)",
        edl3.TEXT_BACKEND not in edl3.BACKENDS,
        sorted(edl3.BACKENDS),
    )
    refused = ""
    try:
        edl3.select_backend("no_such_effect", 2, DURATION)
    except edl3.EDLError as exc:
        refused = exc.code
    check(
        "an unknown kind is refused with EFFECT_UNKNOWN",
        refused == "EFFECT_UNKNOWN",
        refused,
    )

    print("\n-- (f) duration is accepted and does not flip the mechanism")
    for kind in animatable:
        for keys in (1, 3, 5):
            short = edl3.select_backend(kind, keys, 0.25)
            long = edl3.select_backend(kind, keys, 600.0)
            check(
                f"{kind}: {keys} keyframe(s) are duration-invariant",
                short == long,
                f"{short} / {long}",
            )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
