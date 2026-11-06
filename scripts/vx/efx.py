"""vx.efx — deterministic library effects for a plan, at a declared density.

A recipe says ``effects_per_shot: 1.0`` and this module turns that number into
``vx.effects`` specs, one entry per clip, ready for ``render_edl`` (which reads
``plan.effects[clip_id]``). Four rules, and none of them is negotiable:

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

4. **No effect without proof (C8, legge A).** In strict mode every spec carries
   an ``evidence`` list measured *on that clip*: ``shake`` only when the jitter
   is above the declared threshold, ``warm``/``cool`` only when the colour cast
   is outside its band, ``zoom``/``push_in``/... only when a face is actually
   tracked, ``vignette`` only when the border is brighter than the centre,
   ``noir``/``sepia`` only when saturation is low. A roll with no proof is
   dropped and counted in ``dropped_no_evidence``. The number of **distinct
   creative treatments** per reel never exceeds
   ``THRESHOLDS["max_creative_treatments"]``: the next new name is refused and
   reported in ``rejected_creative``.

The cost of each spec is :func:`vx.effects.compose.cost`; a spec above the
declared ceiling (``render.effect_cost_budget``) is replaced by the cheapest
usable spec instead of being rendered at a loss, and the substitution is
reported. ``render.effect_budget`` is a different number on purpose: it counts
how many looks may start inside a 10 s window, and this module never reads it.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "LEGACY_NAMES",
    "PALETTE_NAMES",
    "EffectError",
    "audit",
    "frame_measures",
    "generate",
    "palette",
    "per_clip_count",
    "summary",
]

#: The historical names this generator may reach for. Deliberately visual-only:
#: ``time`` looks (slowmo, freeze) retime a clip and would fight the beat grid,
#: ``text_pop`` would write a sentence nobody asked for, ``crossfade`` needs a
#: second source a clip does not have, ``grain``/``particles`` are declared but
#: not executed by the bridge. ``fade_in``/``fade_out`` are gone too: a fade is
#: a temporal statement that no per-clip measurement can justify here.
PALETTE_NAMES: Tuple[str, ...] = (
    "zoom",
    "push_in",
    "punch",
    "pull_out",
    "zoom_out",
    "kenburns",
    "shake",
    "vignette",
    "warm",
    "cool",
    "sepia",
    "noir",
)

#: Names kept for callers that pinned the previous palette (tests, reports).
LEGACY_NAMES: Tuple[str, ...] = ("fade_in", "fade_out")

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


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _threshold(key: str) -> float:
    from .contract import THRESHOLDS  # noqa: PLC0415 - keep import cost at call time

    return float(THRESHOLDS[key])


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


# --------------------------------------------------------------------------- #
# legge A: la prova per ogni effetto
# --------------------------------------------------------------------------- #

#: What a name needs before it may be applied. ``face`` means a tracked face on
#: the clip (the bridge's reframe carried keyframes), not a guess.
_EVIDENCE_RULES: Dict[str, str] = {
    "zoom": "face",
    "push_in": "face",
    "punch": "face",
    "pull_out": "face",
    "zoom_out": "face",
    "kenburns": "face",
    "shake": "jitter",
    "vignette": "border",
    "warm": "warm",
    "cool": "cool",
    "sepia": "saturation",
    "noir": "saturation",
}


def _evidence_for(
    name: str,
    measures: Mapping[str, Any],
    clip_id: str,
) -> Optional[List[Dict[str, Any]]]:
    """The measured reason ``name`` may be applied to this clip, or ``None``.

    Every record is ``{kind, measured, threshold, clip_id, why}``: the report can
    be audited without opening the frame again. A missing measurement is a
    missing proof, never a default zero.
    """
    rule = _EVIDENCE_RULES.get(name)
    if rule is None:  # pragma: no cover - palette and rules are pinned together
        return None
    if rule == "face":
        faces = _number(measures.get("faces"))
        if faces is not None and faces >= 1.0:
            return [
                {
                    "kind": "face_track",
                    "clip_id": clip_id,
                    "measured": int(faces),
                    "threshold": 1,
                    "why": f"{int(faces)} volto/i inseguiti dalla finestra di ritaglio",
                }
            ]
        return None
    if rule == "jitter":
        value = _number(measures.get("jitter"))
        limit = _threshold("effects_shake_jitter_min")
        if value is not None and value >= limit:
            return [
                {
                    "kind": "jitter",
                    "clip_id": clip_id,
                    "measured": round(value, 3),
                    "threshold": limit,
                    "why": f"jitter {value:.2f} >= {limit:.2f}: la camera trema",
                }
            ]
        return None
    if rule == "border":
        value = _number(measures.get("border_ratio"))
        limit = _threshold("effects_vignette_border_ratio")
        if value is not None and value >= limit:
            return [
                {
                    "kind": "border_brighter",
                    "clip_id": clip_id,
                    "measured": round(value, 3),
                    "threshold": limit,
                    "why": (
                        f"bordo/luminosita' centro {value:.2f} >= {limit:.2f}: "
                        "il bordo e' piu' chiaro del centro"
                    ),
                }
            ]
        return None
    if rule == "warm":
        value = _number(measures.get("cast_rg"))
        limit = _threshold("effects_color_cast_min")
        if value is not None and value >= limit:
            return [
                {
                    "kind": "color_cast",
                    "clip_id": clip_id,
                    "measured": round(value, 2),
                    "threshold": limit,
                    "channel": "rg",
                    "why": f"cast R-G {value:.1f} >= {limit:.1f}: banda calda",
                }
            ]
        return None
    if rule == "cool":
        value = _number(measures.get("cast_by"))
        limit = _threshold("effects_color_cast_min")
        if value is not None and value >= limit:
            return [
                {
                    "kind": "color_cast",
                    "clip_id": clip_id,
                    "measured": round(value, 2),
                    "threshold": limit,
                    "channel": "by",
                    "why": f"cast B-Y {value:.1f} >= {limit:.1f}: banda fredda",
                }
            ]
        return None
    # saturation: noir / sepia
    value = _number(measures.get("saturation"))
    limit = _threshold("effects_saturation_max")
    if value is not None and value <= limit:
        return [
            {
                "kind": "low_saturation",
                "clip_id": clip_id,
                "measured": round(value, 3),
                "threshold": limit,
                "why": f"saturazione {value:.2f} <= {limit:.2f}: immagine quasi neutra",
            }
        ]
    return None


def frame_measures(path: Any) -> Dict[str, Any]:
    """Luma of the border against the centre, measured on a sampled frame.

    ``border_ratio`` is ``mean(border) / mean(centre)``: above
    ``effects_vignette_border_ratio`` the border is lighter than the centre and a
    vignette has something to correct. A frame that cannot be read returns an
    empty dict — the proof is then absent, never invented.
    """
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:  # noqa: BLE001
        return {}
    try:
        gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    except Exception:  # noqa: BLE001
        return {}
    if gray is None:
        return {}
    height, width = gray.shape[:2]
    if height < 8 or width < 8:
        return {}
    band_y = max(1, int(round(height * 0.12)))
    band_x = max(1, int(round(width * 0.12)))
    centre = gray[band_y : height - band_y, band_x : width - band_x]
    if centre.size == 0:
        return {}
    mask = np.ones(gray.shape, dtype=bool)
    mask[band_y : height - band_y, band_x : width - band_x] = False
    border = gray[mask]
    if border.size == 0:
        return {}
    centre_mean = float(centre.mean())
    if centre_mean <= 1e-6:
        return {}
    return {
        "border_luma": round(float(border.mean()), 3),
        "center_luma": round(centre_mean, 3),
        "border_ratio": round(float(border.mean()) / centre_mean, 4),
        "mean_luma": round(float(gray.mean()) / 255.0, 4),
    }


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
            if int(getattr(clip, "layer", 0) or 0) != 0:
                continue
            clips.append(clip)
    return clips


def _creative_limit(value: Any = None) -> int:
    if value is None:
        from .contract import THRESHOLDS  # noqa: PLC0415

        value = THRESHOLDS["max_creative_treatments"]
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        raise EffectError(f"tetto dei trattamenti creativi non numerico: {value!r}")


def generate(
    plan: Any,
    per_shot: Any,
    *,
    budget: Any = None,
    salt: Any = "",
    evidence: Optional[Mapping[str, Mapping[str, Any]]] = None,
    strict: bool = False,
    max_creative: Any = None,
) -> Dict[str, Any]:
    """``{clip_id: [spec, ...]}`` plus the numbers that justify it.

    ``strict`` turns on C8's law A: a spec is only emitted when
    ``_evidence_for`` finds a measurement **for that clip** in ``evidence``, and
    no more than ``max_creative`` distinct names may be used on the reel. The
    legacy mode (``strict=False``) keeps the historical behaviour for callers
    that have no measurements to offer; production goes through ``strict``.

    Returns a payload dict: ``table`` (the mapping to attach as
    ``plan.effects``), ``clips``, ``specs``, ``names``, ``cost``, ``budget``,
    ``substituted``, ``dropped_no_evidence``, ``rejected_creative``,
    ``creative_distinct``, ``creative_max`` and the palette's ``skipped`` rows.
    ``table`` is empty when the density is zero — the caller must not attach it.
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
    creative_max = _creative_limit(max_creative)

    clips = _video_clips(plan)
    table: Dict[str, List[Dict[str, Any]]] = {}
    names: List[str] = []
    total = 0.0
    substituted: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    distinct: List[str] = []

    for index, clip in enumerate(clips):
        clip_id = str(getattr(clip, "clip_id", "") or "")
        if not clip_id:
            continue
        count = per_clip_count(per_shot, clip_id, index, salt)
        if count <= 0:
            continue
        specs: List[Dict[str, Any]] = []
        measures = (evidence or {}).get(clip_id) if evidence is not None else None
        for slot in range(count):
            if strict:
                proof_rows: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
                for row in usable:
                    if not measures:
                        break
                    proof = _evidence_for(str(row["name"]), measures, clip_id)
                    if proof:
                        proof_rows.append((row, proof))
                if not proof_rows:
                    dropped.append(
                        {
                            "clip_id": clip_id,
                            "slot": slot,
                            "reason": (
                                "nessuna misura sulla clip: la spec non ha prova"
                                if not measures
                                else "nessuna delle misure della clip supera la soglia "
                                "dell'effetto"
                            ),
                        }
                    )
                    continue
                roll = _unit("pick", salt, clip_id, index, slot)
                row, proof = proof_rows[
                    min(len(proof_rows) - 1, int(roll * len(proof_rows)))
                ]
                name = str(row["name"])
                if name not in distinct and len(distinct) >= creative_max:
                    rejected.append(
                        {
                            "clip_id": clip_id,
                            "slot": slot,
                            "name": name,
                            "reason": (
                                f"quarto trattamento creativo distinto: "
                                f"'{name}' oltre il tetto di {creative_max} "
                                f"({', '.join(distinct)})"
                            ),
                        }
                    )
                    continue
                if float(row["cost"]) > limit:
                    cheap = min(
                        proof_rows,
                        key=lambda item: (float(item[0]["cost"]), str(item[0]["name"])),
                    )
                    row, proof = cheap[0], cheap[1]
                    name = str(row["name"])
                    substituted.append(
                        {"clip_id": clip_id, "slot": slot, "name": name}
                    )
                spec = dict(row["spec"])
                spec["evidence"] = [dict(item) for item in proof]
                specs.append(spec)
                names.append(name)
                total += float(row["cost"])
                if name not in distinct:
                    distinct.append(name)
                continue
            row, swapped = _pick(usable, limit, clip_id, index, slot, salt)
            spec = dict(row["spec"])
            specs.append(spec)
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
        "dropped_no_evidence": dropped,
        "rejected_creative": rejected,
        "creative_distinct": len(distinct),
        "creative_max": creative_max,
        "creative_names": distinct,
        "skipped": skipped,
        "per_shot": per_shot,
        "salt": str(salt),
        "strict": bool(strict),
    }


def audit(table: Any, *, max_creative: Any = None) -> Dict[str, Any]:
    """Legge A sul piano finale: ogni spec ha una prova, i nomi restano <= tetto.

    ``table`` e' ``plan.effects``. Una spec senza ``evidence`` non e' un dettaglio
    da correggere dopo: il montaggio non e' consegnabile. Il tetto dei nomi
    distinti e' quello del contratto, non un numero locale.
    """
    creative_max = _creative_limit(max_creative)
    specs = [
        spec
        for per_clip in (table or {}).values()
        for spec in per_clip
        if isinstance(spec, Mapping)
    ]
    without: List[Dict[str, Any]] = []
    names: List[str] = []
    for spec in specs:
        name = str(spec.get("id") or spec.get("name") or "")
        if name and name not in names:
            names.append(name)
        proof = spec.get("evidence")
        if not isinstance(proof, (list, tuple)) or not proof:
            without.append({"name": name, "why": "spec senza evidence misurata"})
    violations: List[Dict[str, Any]] = []
    if without:
        violations.append(
            {
                "code": "effect_without_evidence",
                "count": len(without),
                "why": (
                    f"{len(without)} spec applicate senza una misura che le "
                    "giustifichi"
                ),
                "specs": without,
            }
        )
    if len(names) > creative_max:
        violations.append(
            {
                "code": "creative_over_cap",
                "measured": len(names),
                "threshold": creative_max,
                "names": names,
                "why": (
                    f"{len(names)} trattamenti creativi distinti oltre il tetto "
                    f"di {creative_max}: {', '.join(names)}"
                ),
            }
        )
    return {
        "ok": not violations,
        "specs": len(specs),
        "without_evidence": without,
        "creative_distinct": len(names),
        "creative_max": creative_max,
        "names": names,
        "violations": violations,
    }


def summary(result: Mapping[str, Any]) -> Dict[str, Any]:
    """The report-ready view of :func:`generate` (no specs, no private detail)."""
    counts: Dict[str, int] = {}
    for name in result.get("names") or ():
        counts[str(name)] = counts.get(str(name), 0) + 1
    dropped = list(result.get("dropped_no_evidence") or ())
    rejected = list(result.get("rejected_creative") or ())
    return {
        "per_shot": result.get("per_shot"),
        "clips": int(result.get("clips") or 0),
        "specs": int(result.get("specs") or 0),
        "cost": float(result.get("cost") or 0.0),
        "budget": float(result.get("budget") or 0.0),
        "names": counts,
        "substituted": list(result.get("substituted") or ()),
        "dropped_no_evidence": len(dropped),
        "dropped": dropped,
        "rejected_creative": rejected,
        "creative_distinct": int(result.get("creative_distinct") or 0),
        "creative_max": int(result.get("creative_max") or 0),
        "creative_names": list(result.get("creative_names") or ()),
        "skipped": list(result.get("skipped") or ()),
        "salt": str(result.get("salt") or ""),
        "strict": bool(result.get("strict")),
    }
