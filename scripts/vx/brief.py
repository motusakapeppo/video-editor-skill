"""vx.brief — the ``vx.visual/1.0`` document and its contract.

Why: a visual decision is only auditable if every axis carries the measurement it
came from, the threshold that measurement was compared against, and whether the
effect was actually executed. This module builds that document and rejects any
shape that would let a naked number or an unexecuted effect through.

The brief is deliberately dumb: it stores decisions, it does not make them. The
decision modules (palette, gradeplan, animplan, ...) fill it; ``prove`` checks it.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional

from .registry import ANIMATIONS, AXES, SCHEMA

PROVENANCE_PREFIXES = ("corpus_hash:", "measured:", "declared:")

_MEASUREMENT_KEYS = (
    "transient_ratio",
    "transient_p33",
    "transient_p67",
    "hue_mass_top3",
    "ink_coverage_pct",
    "subject_offset",
    "onsets_per_s",
    "first_speech_onset_s",
    "first3s_motion",
    "face_present",
    "luma_p50",
    "contrast",
)

_DRIVERS = {
    "palette": "hue_mass_top3",
    "grade": "luma_p50",
    "animation": "transient_ratio",
    "cut_density": "onsets_per_s",
    "composition": "subject_offset",
    "typography": "ink_coverage_pct",
}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_hex64(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _provenance_ok(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(PROVENANCE_PREFIXES)


def new_brief(project_id: str, source_sha256: str, corpus_hash: str) -> Dict[str, Any]:
    """Return an empty ``vx.visual/1.0`` document.

    Every axis starts in inaction (confidence 0.5, applied false) because nothing
    has been measured yet. ``measurements`` is a skeleton: the caller fills the
    numbers, this function never invents them.
    """
    corpus_prov = f"corpus_hash:{corpus_hash}"
    choices: Dict[str, Dict[str, Any]] = {
        "palette": {
            "driver": _DRIVERS["palette"],
            "value": "neutral",
            "provenance": corpus_prov,
            "inaction": True,
            "confidence": 0.5,
            "applied": False,
        },
        "grade": {
            "driver": _DRIVERS["grade"],
            "value": "none",
            "provenance": corpus_prov,
            "inaction": True,
            "confidence": 0.5,
            "applied": False,
        },
        "animation": {
            "driver": _DRIVERS["animation"],
            "value": "none",
            "provenance": corpus_prov,
            "inaction": True,
            "confidence": 0.5,
            "applied": False,
            "name": "none",
            "count": 0,
            "direction_basis": "asserted",
        },
        "cut_density": {
            "driver": _DRIVERS["cut_density"],
            "value": 2.0,
            "provenance": corpus_prov,
            "inaction": True,
            "confidence": 0.5,
            "applied": False,
        },
        "composition": {
            "driver": _DRIVERS["composition"],
            "value": "center",
            "provenance": "declared:1/20 width",
            "inaction": True,
            "confidence": 0.5,
            "applied": False,
        },
        "typography": {
            "driver": _DRIVERS["typography"],
            "value": "keep",
            "provenance": "measured:delivery",
            "inaction": True,
            "confidence": 0.5,
            "applied": False,
        },
    }
    return {
        "schema": SCHEMA,
        "project_id": str(project_id),
        "source_sha256": str(source_sha256),
        "corpus_hash": str(corpus_hash),
        "choices": choices,
        "measurements": {
            "transient_ratio": None,
            "transient_p33": None,
            "transient_p67": None,
            "hue_mass_top3": [],
            "ink_coverage_pct": None,
            "subject_offset": None,
            "onsets_per_s": None,
            "first_speech_onset_s": None,
            "first3s_motion": None,
            "face_present": False,
            "luma_p50": None,
            "contrast": None,
        },
        "reasons": [],
        "executed": [],
        "skeleton": "deferred",
        "distinct_surface": {
            "ok": False,
            "scope": "surface_only",
            "families": [],
            "geometry_available": False,
        },
        "docs_match": {
            "ok": False,
            "doc_sha256": "",
            "ledger_sha256": "",
        },
    }


def add_reason(
    brief: Dict[str, Any],
    axis: str,
    driver: str,
    value: Any,
    threshold: Any,
    provenance: str,
    decision: str,
    direction_basis: Optional[str] = None,
) -> Dict[str, Any]:
    """Append one reason. The axis must be one of the six; nothing else is legal."""
    if axis not in AXES:
        raise ValueError(f"asse sconosciuto: {axis!r}")
    reason: Dict[str, Any] = {
        "axis": axis,
        "driver": driver,
        "value": value,
        "threshold": threshold,
        "provenance": provenance,
        "decision": decision,
    }
    if direction_basis is not None:
        reason["direction_basis"] = direction_basis
    brief.setdefault("reasons", []).append(reason)
    return brief


def add_executed(
    brief: Dict[str, Any],
    axis: str,
    name: str,
    engine: str,
    filter_text: str,
    rc: int,
    frames: int,
    at: Optional[float] = None,
) -> Dict[str, Any]:
    """Append one executed effect. ``filter`` is the key the contract names."""
    if axis not in AXES:
        raise ValueError(f"asse sconosciuto: {axis!r}")
    brief.setdefault("executed", []).append(
        {
            "axis": axis,
            "name": name,
            "engine": engine,
            "filter": filter_text,
            "rc": rc,
            "frames": frames,
            "at": at,
        }
    )
    return brief


def validate(brief: Dict[str, Any]) -> List[str]:
    """Return every contract violation; an empty list means the brief is legal."""
    errors: List[str] = []
    if not isinstance(brief, dict):
        return ["brief non è un oggetto"]

    if brief.get("schema") != SCHEMA:
        errors.append(f"schema deve essere {SCHEMA}")
    if not isinstance(brief.get("project_id"), str) or not brief.get("project_id"):
        errors.append("project_id mancante")
    if not _is_hex64(brief.get("source_sha256")):
        errors.append("source_sha256 deve essere 64 esadecimali")
    if not _is_hex64(brief.get("corpus_hash")):
        errors.append("corpus_hash deve essere 64 esadecimali")

    choices = brief.get("choices")
    if not isinstance(choices, dict):
        errors.append("choices mancante o non è un oggetto")
        choices = {}
    if set(choices) != set(AXES):
        missing = sorted(set(AXES) - set(choices))
        extra = sorted(set(choices) - set(AXES))
        if missing:
            errors.append(f"assi mancanti: {missing}")
        if extra:
            errors.append(f"assi sconosciuti: {extra}")

    for axis in AXES:
        choice = choices.get(axis)
        if not isinstance(choice, dict):
            errors.append(f"{axis}: scelta mancante")
            continue
        for field in (
            "driver",
            "value",
            "provenance",
            "inaction",
            "confidence",
            "applied",
        ):
            if field not in choice:
                errors.append(f"{axis}: campo {field} mancante")
        if not isinstance(choice.get("driver"), str) or not choice.get("driver"):
            errors.append(f"{axis}: driver non valido")
        if choice.get("value") is None:
            errors.append(f"{axis}: value mancante")
        if not _provenance_ok(choice.get("provenance")):
            errors.append(
                f"{axis}: provenienza non ammessa: {choice.get('provenance')!r}"
            )
        if not isinstance(choice.get("inaction"), bool):
            errors.append(f"{axis}: inaction non booleano")
        confidence = choice.get("confidence")
        applied = choice.get("applied")
        if not _is_number(confidence) or not (0.0 <= float(confidence) <= 1.0):
            errors.append(f"{axis}: confidence fuori da [0, 1]")
        elif not isinstance(applied, bool):
            errors.append(f"{axis}: applied non booleano")
        else:
            uncertain = 0.2 <= float(confidence) <= 0.8
            if uncertain and applied:
                errors.append(
                    f"{axis}: confidence {confidence} in [0.2, 0.8] ma applied true"
                )
            if not uncertain and not applied:
                errors.append(
                    f"{axis}: confidence {confidence} fuori [0.2, 0.8] ma applied false"
                )
            if (
                isinstance(choice.get("inaction"), bool)
                and choice["inaction"] == applied
            ):
                errors.append(f"{axis}: inaction deve essere l'opposto di applied")
        if axis == "animation":
            if choice.get("name") not in ANIMATIONS:
                errors.append(
                    f"animation: nome non nel vocabolario: {choice.get('name')!r}"
                )
            count = choice.get("count")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                errors.append("animation: count deve essere un intero >= 0")
            if choice.get("direction_basis") != "asserted":
                errors.append("animation: direction_basis deve essere 'asserted'")

    measurements = brief.get("measurements")
    if not isinstance(measurements, dict):
        errors.append("measurements mancante o non è un oggetto")
        measurements = {}
    for key in _MEASUREMENT_KEYS:
        if key not in measurements:
            errors.append(f"measurements.{key} mancante")
    for key in (
        "transient_ratio",
        "transient_p33",
        "transient_p67",
        "ink_coverage_pct",
        "onsets_per_s",
        "first_speech_onset_s",
        "first3s_motion",
        "luma_p50",
        "contrast",
    ):
        value = measurements.get(key)
        if value is not None and not _is_number(value):
            errors.append(f"measurements.{key} deve essere un numero o null")
    if not isinstance(measurements.get("face_present"), bool):
        errors.append("measurements.face_present deve essere booleano")
    hue = measurements.get("hue_mass_top3")
    if not isinstance(hue, list) or len(hue) != 3:
        errors.append("measurements.hue_mass_top3 deve avere 3 coppie")
    else:
        for i, pair in enumerate(hue):
            if not isinstance(pair, dict):
                errors.append(f"measurements.hue_mass_top3[{i}] non è un oggetto")
                continue
            if not _is_number(pair.get("hue")):
                errors.append(f"measurements.hue_mass_top3[{i}].hue non numerico")
            if not _is_number(pair.get("mass")):
                errors.append(f"measurements.hue_mass_top3[{i}].mass non numerico")
    offset = measurements.get("subject_offset")
    if offset is not None:
        if not isinstance(offset, dict):
            errors.append("measurements.subject_offset deve essere {x,y} o null")
        else:
            for key in ("x", "y"):
                if not _is_number(offset.get(key)):
                    errors.append(f"measurements.subject_offset.{key} non numerico")

    reasons = brief.get("reasons")
    if not isinstance(reasons, list) or len(reasons) != 6:
        errors.append("reasons deve avere 6 voci")
        reasons = reasons if isinstance(reasons, list) else []
    seen_reason_axes: List[str] = []
    for i, reason in enumerate(reasons):
        if not isinstance(reason, dict):
            errors.append(f"reasons[{i}] non è un oggetto")
            continue
        axis = reason.get("axis")
        if axis not in AXES:
            errors.append(f"reasons[{i}]: asse sconosciuto {axis!r}")
        else:
            seen_reason_axes.append(axis)
        for field in ("driver", "value", "threshold", "provenance", "decision"):
            if field not in reason:
                errors.append(f"reasons[{i}]: campo {field} mancante")
        if not _provenance_ok(reason.get("provenance")):
            errors.append(f"reasons[{i}]: provenienza non ammessa")
        if not isinstance(reason.get("decision"), str) or not reason.get("decision"):
            errors.append(f"reasons[{i}]: decision non valida")
        if "direction_basis" in reason and reason["direction_basis"] != "asserted":
            errors.append(f"reasons[{i}]: direction_basis deve essere 'asserted'")
    if len(reasons) == 6 and sorted(seen_reason_axes) != sorted(AXES):
        errors.append("reasons deve coprire i sei assi, uno per asse")

    executed = brief.get("executed")
    if not isinstance(executed, list) or len(executed) != 6:
        errors.append("executed deve avere 6 voci")
        executed = executed if isinstance(executed, list) else []
    seen_exec_axes: List[str] = []
    for i, item in enumerate(executed):
        if not isinstance(item, dict):
            errors.append(f"executed[{i}] non è un oggetto")
            continue
        axis = item.get("axis")
        if axis not in AXES:
            errors.append(f"executed[{i}]: asse sconosciuto {axis!r}")
        else:
            seen_exec_axes.append(axis)
        if not isinstance(item.get("name"), str) or not item.get("name"):
            errors.append(f"executed[{i}]: name non valido")
        if not isinstance(item.get("engine"), str) or not item.get("engine"):
            errors.append(f"executed[{i}]: engine non valido")
        if not isinstance(item.get("filter"), str):
            errors.append(f"executed[{i}]: filter non è una stringa")
        if not isinstance(item.get("rc"), int) or isinstance(item.get("rc"), bool):
            errors.append(f"executed[{i}]: rc non intero")
        if not isinstance(item.get("frames"), int) or isinstance(
            item.get("frames"), bool
        ):
            errors.append(f"executed[{i}]: frames non intero")
        at = item.get("at")
        if at is not None and not _is_number(at):
            errors.append(f"executed[{i}]: at deve essere un numero o null")
    if len(executed) == 6 and sorted(seen_exec_axes) != sorted(AXES):
        errors.append("executed deve coprire i sei assi, uno per asse")

    if brief.get("skeleton") != "deferred":
        errors.append("skeleton deve essere 'deferred'")

    surface = brief.get("distinct_surface")
    if not isinstance(surface, dict):
        errors.append("distinct_surface mancante")
    else:
        if not isinstance(surface.get("ok"), bool):
            errors.append("distinct_surface.ok deve essere booleano")
        if surface.get("scope") != "surface_only":
            errors.append("distinct_surface.scope deve essere 'surface_only'")
        if not isinstance(surface.get("families"), list):
            errors.append("distinct_surface.families deve essere una lista")
        if not isinstance(surface.get("geometry_available"), bool):
            errors.append("distinct_surface.geometry_available deve essere booleano")

    docs = brief.get("docs_match")
    if not isinstance(docs, dict):
        errors.append("docs_match mancante")
    else:
        if not isinstance(docs.get("ok"), bool):
            errors.append("docs_match.ok deve essere booleano")
        for key in ("doc_sha256", "ledger_sha256"):
            if not isinstance(docs.get(key), str):
                errors.append(f"docs_match.{key} deve essere una stringa")

    return errors


def to_json(brief: Dict[str, Any]) -> str:
    """Canonical JSON: sorted keys, stable whitespace, trailing newline."""
    return json.dumps(brief, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _valid_brief() -> Dict[str, Any]:
    """Build a fully populated brief used only by the selftest."""
    brief = new_brief("selftest", "a" * 64, "b" * 64)
    brief["measurements"] = {
        "transient_ratio": 1.4,
        "transient_p33": 1.1,
        "transient_p67": 1.8,
        "hue_mass_top3": [
            {"hue": 12, "mass": 0.42},
            {"hue": 210, "mass": 0.18},
            {"hue": 96, "mass": 0.09},
        ],
        "ink_coverage_pct": 0.62,
        "subject_offset": {"x": 0.07, "y": -0.02},
        "onsets_per_s": 2.4,
        "first_speech_onset_s": 0.8,
        "first3s_motion": 0.22,
        "face_present": True,
        "luma_p50": 0.48,
        "contrast": 0.71,
    }
    for axis in AXES:
        choice = brief["choices"][axis]
        choice["confidence"] = 0.9
        choice["applied"] = True
        choice["inaction"] = False
        add_reason(
            brief,
            axis,
            choice["driver"],
            choice["value"],
            {"p33": 0.1, "p67": 0.2},
            choice["provenance"],
            "misura sopra soglia",
            direction_basis="asserted" if axis == "animation" else None,
        )
        add_executed(
            brief,
            axis,
            {
                "palette": "palette",
                "grade": "curve",
                "animation": "fade",
                "cut_density": "snap",
                "composition": "crop",
                "typography": "font",
            }[axis],
            "ffmpeg",
            "null",
            0,
            1,
            at=0.0,
        )
    brief["choices"]["animation"]["name"] = "fade"
    brief["choices"]["animation"]["count"] = 1
    return brief


def _selftest() -> int:
    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:44} {detail}")

    valid = _valid_brief()
    errors = validate(valid)
    check("brief valido accettato", errors == [], str(errors))

    canonical = to_json(valid)
    check(
        "to_json stabile",
        canonical == to_json(copy.deepcopy(valid)) and canonical.endswith("\n"),
        f"{len(canonical)} byte",
    )

    missing = copy.deepcopy(valid)
    del missing["choices"]["palette"]
    check(
        "asse mancante rifiutato", validate(missing) != [], str(validate(missing)[:1])
    )

    bad_prov = copy.deepcopy(valid)
    bad_prov["choices"]["grade"]["provenance"] = "naked:0.5"
    check(
        "provenienza nuda rifiutata",
        validate(bad_prov) != [],
        str(validate(bad_prov)[:1]),
    )

    uncertain = copy.deepcopy(valid)
    uncertain["choices"]["palette"]["confidence"] = 0.5
    uncertain["choices"]["palette"]["applied"] = True
    check(
        "confidence 0.5 con applied true rifiutata",
        validate(uncertain) != [],
        str(validate(uncertain)[:1]),
    )

    measured = copy.deepcopy(valid)
    measured["choices"]["animation"]["direction_basis"] = "measured"
    check(
        "direction_basis 'measured' rifiutata",
        validate(measured) != [],
        str(validate(measured)[:1]),
    )

    print(f"{'OK' if failures == 0 else 'FALLITO'} — {failures} fallimenti")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="vx.brief selftest")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    parser.print_help()
    raise SystemExit(2)
