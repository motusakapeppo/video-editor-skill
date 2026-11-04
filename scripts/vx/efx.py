"""vx.efx — deterministic library effects for a plan, at a declared density.

A recipe says ``effects_per_shot: 1.0`` and this module turns that number into
``vx.effects`` specs, one entry per clip, ready for ``render_edl`` (which reads
``plan.effects[clip_id]``). Three rules, and none of them is negotiable:

1. **Only names the library already has.** The palette is built by expanding
   historical names from :mod:`vx.effects.compose` (``zoom``, ``punch``,
   ``grade`` looks...) and keeping only the specs whose primitives the timeline
   bridge actually executes (:data:`vx.effects.ffmpeg.TIMELINE_PRIMITIVES`).
   A name that does not expand, or that expands into a primitive the bridge
   declares but cannot render, is *skipped and reported* — never invented.

2. **Deterministic.** The choice is a sha256 of ``salt|clip_id|index|slot``, so
   the same plan and the same density produce the same specs on every run, on
   every machine, with no RNG state to seed.

3. **Zero means zero.** ``effects_per_shot <= 0`` returns an empty table, and
   the caller never attaches it to the plan: the filter graph stays byte for
   byte the one this engine shipped.

The cost of each spec is :func:`vx.effects.compose.cost`; a spec above the
declared ceiling (``render.effect_cost_budget``) is replaced by the cheapest
usable spec instead of being rendered at a loss, and the substitution is
reported. ``render.effect_budget`` is a different number on purpose: it counts
how many looks may start inside a 10 s window, and this module never reads it.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

__all__ = [
    "EffectError",
    "PALETTE_NAMES",
    "generate",
    "palette",
    "per_clip_count",
    "summary",
]

#: The historical names this generator may reach for. Deliberately visual-only:
#: ``time`` looks (slowmo, freeze) retime a clip and would fight the beat grid,
#: ``text_pop`` would write a sentence nobody asked for, ``crossfade`` needs a
#: second source a clip does not have, ``grain``/``particles`` are declared but
#: not executed by the bridge.
PALETTE_NAMES: Tuple[str, ...] = (
    "zoom",
    "push_in",
    "punch",
    "pull_out",
    "zoom_out",
    "kenburns",
    "shake",
    "fade_in",
    "fade_out",
    "vignette",
    "warm",
    "cool",
    "sepia",
    "noir",
)

#: Hard ceiling on specs per clip, whatever a caller asks for. ``recipe``
#: already validates ``effects_per_shot`` to 0..4; this is the second guard so a
#: hand-built call cannot build a 40-stage chain.
MAX_PER_CLIP = 4


class EffectError(ValueError):
    """A spec could not be built from the library. Never downgraded to ''."""


def _unit(*parts: Any) -> float:
    """A stable number in [0, 1) from a tuple of labels."""
    blob = "|".join(str(part) for part in parts).encode("utf-8")
    digest = hashlib.sha256(blob).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def palette() -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """``(usable, skipped)`` — every palette name, with the reason it was cut.

    ``usable`` rows are ``{"name", "spec", "cost"}``; ``skipped`` rows are
    ``{"name", "reason"}``. Nothing is dropped in silence: the caller puts both
    lists in the report.
    """
    from .effects import compose  # noqa: PLC0415 - keep import cost at call time
    from .effects import ffmpeg as effect_ffmpeg  # noqa: PLC0415

    allowed = set(effect_ffmpeg.TIMELINE_PRIMITIVES)
    usable: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for name in PALETTE_NAMES:
        try:
            spec = compose.expand(name)
        except compose.SpecError as exc:
            skipped.append({"name": name, "reason": f"nome non espandibile: {exc}"})
            continue
        kinds = {
            str(primitive.get("kind") or "")
            for primitive in spec.get("primitives") or ()
            if primitive.get("enabled", True)
        }
        unsupported = sorted(kinds - allowed)
        if unsupported:
            skipped.append(
                {
                    "name": name,
                    "reason": (
                        "primitiva non eseguibile dal ponte timeline: "
                        + ", ".join(unsupported)
                    ),
                }
            )
            continue
        issues = compose.validate(spec)
        if issues:
            skipped.append(
                {
                    "name": name,
                    "reason": "specifica non valida: "
                    + "; ".join(str(issue.message) for issue in issues),
                }
            )
            continue
        usable.append({"name": name, "spec": spec, "cost": float(compose.cost(spec))})
    usable.sort(key=lambda row: (row["cost"], row["name"]))
    return usable, skipped


def per_clip_count(per_shot: Any, clip_id: Any, index: int, salt: Any = "") -> int:
    """How many specs one clip gets: the integer part, plus a seeded remainder.

    ``1.0`` -> one per clip; ``0.5`` -> about half the clips get one; ``0`` ->
    none. The remainder uses the same hash as the choice, so it is stable.
    """
    try:
        value = max(0.0, float(per_shot))
    except (TypeError, ValueError):
        raise EffectError(f"effects_per_shot non e' un numero: {per_shot!r}")
    whole = min(MAX_PER_CLIP, int(value // 1))
    fraction = value - int(value // 1)
    extra = 0
    if fraction > 0.0 and whole < MAX_PER_CLIP:
        if _unit("count", salt, clip_id, index) < fraction:
            extra = 1
    return whole + extra


def _pick(
    usable: Sequence[Mapping[str, Any]],
    budget: float,
    clip_id: Any,
    index: int,
    slot: int,
    salt: Any,
) -> Tuple[Dict[str, Any], bool]:
    """One spec for one slot, and whether the ceiling forced a substitution.

    The roll happens over the **whole** palette, so a heavy look is reachable;
    only if its cost is above the ceiling it is replaced by the cheapest usable
    one, and the replacement is reported instead of happening silently.
    """
    roll = _unit("pick", salt, clip_id, index, slot)
    row = usable[min(len(usable) - 1, int(roll * len(usable)))]
    if float(row["cost"]) > budget:
        cheap = min(usable, key=lambda item: (float(item["cost"]), str(item["name"])))
        return dict(cheap), True
    return dict(row), False


def _video_clips(plan: Any) -> List[Any]:
    clips: List[Any] = []
    for track in getattr(plan, "video_tracks", ()) or ():
        for clip in getattr(track, "clips", ()) or ():
            clips.append(clip)
    return clips


def generate(
    plan: Any,
    per_shot: Any,
    *,
    budget: Any = None,
    salt: Any = "",
) -> Dict[str, Any]:
    """``{clip_id: [spec, ...]}`` plus the numbers that justify it.

    Returns a payload dict: ``table`` (the mapping to attach as
    ``plan.effects``), ``clips``, ``specs``, ``names``, ``cost``, ``budget``,
    ``substituted`` and the palette's ``skipped`` rows. ``table`` is empty when
    the density is zero — the caller must not attach it at all.
    """
    from . import settings as settings_mod  # noqa: PLC0415

    usable, skipped = palette()
    if not usable:  # pragma: no cover - the library ships entries, a test pins it
        raise EffectError(
            "nessun nome della palette e' eseguibile dal ponte timeline: "
            + "; ".join(f"{row['name']}: {row['reason']}" for row in skipped)
        )
    if budget is None:
        budget = settings_mod.get("render.effect_cost_budget", 1.8)
    try:
        limit = float(budget)
    except (TypeError, ValueError):
        raise EffectError(f"budget effetti non numerico: {budget!r}")
    if limit < 0:
        raise EffectError(f"budget effetti negativo: {limit}")
    cheapest = min(float(row["cost"]) for row in usable)
    if cheapest > limit:
        raise EffectError(
            f"budget effetti {limit:g} sotto il costo minimo della palette "
            f"({cheapest:g}, {min(usable, key=lambda row: row['cost'])['name']})"
        )

    clips = _video_clips(plan)
    table: Dict[str, List[Dict[str, Any]]] = {}
    names: List[str] = []
    total = 0.0
    substituted: List[Dict[str, Any]] = []
    for index, clip in enumerate(clips):
        clip_id = str(getattr(clip, "clip_id", "") or "")
        if not clip_id:
            continue
        count = per_clip_count(per_shot, clip_id, index, salt)
        specs: List[Dict[str, Any]] = []
        for slot in range(count):
            row, swapped = _pick(usable, limit, clip_id, index, slot, salt)
            specs.append(row["spec"])
            names.append(str(row["name"]))
            total += float(row["cost"])
            if swapped:
                substituted.append(
                    {"clip_id": clip_id, "slot": slot, "name": row["name"]}
                )
        if specs:
            table[clip_id] = specs

    return {
        "table": table,
        "clips": len(table),
        "specs": sum(len(specs) for specs in table.values()),
        "names": names,
        "cost": round(total, 3),
        "budget": limit,
        "substituted": substituted,
        "skipped": skipped,
        "per_shot": per_shot,
        "salt": str(salt),
    }


def summary(result: Mapping[str, Any]) -> Dict[str, Any]:
    """The report-ready view of :func:`generate` (no specs, no private detail)."""
    counts: Dict[str, int] = {}
    for name in result.get("names") or ():
        counts[str(name)] = counts.get(str(name), 0) + 1
    return {
        "per_shot": result.get("per_shot"),
        "clips": int(result.get("clips") or 0),
        "specs": int(result.get("specs") or 0),
        "cost": float(result.get("cost") or 0.0),
        "budget": float(result.get("budget") or 0.0),
        "names": counts,
        "substituted": list(result.get("substituted") or ()),
        "skipped": list(result.get("skipped") or ()),
        "salt": str(result.get("salt") or ""),
    }
