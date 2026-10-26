"""Phase 3.1 multi-source evidence law: red contract for v1.4.0.

Run from the repo root:

    python tests/test_evidence_sources.py

Plain script: no pytest, no network. It must run to completion even while
``contract.validate_evidence`` still lacks the ``sources`` keyword and
``contract.VERSION`` is still 1.3.0. Missing-kwarg and wrong-version cases
print FAIL with a readable reason instead of raising out of ``main``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import contract, decide  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0

_MISSING = object()


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def skip(label: str, reason: object = "") -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:58} {reason}")


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

FINDING = {
    "id": "brief.autonomous",
    "declared": True,
    "span": [0.0, 5.0],
    "severity": "info",
    "metric": "brief.mode",
    "value": "autonomous",
    "threshold": "n/a",
}
DURATION = 5.0
SOURCES = {"src_01": {"path": "stage/main.mp4", "sha256": "a" * 64}}


def _witness(source_id=_MISSING) -> dict:
    witness = {"finding_id": "brief.autonomous", "t0": 0.0, "t1": 5.0}
    if source_id is not _MISSING:
        witness["source_id"] = source_id
    return witness


def _plan(witness: dict, enabled: bool = True) -> dict:
    return {
        "output": {"aspect": "9:16"},
        "treatments": [
            {
                "name": "color_fix",
                "enabled": enabled,
                "reason": {"finding_id": "brief.autonomous"},
                "evidence": [witness],
            }
        ],
    }


# --------------------------------------------------------------------------- #
# guarded contract calls
# --------------------------------------------------------------------------- #


def _evidence(plan, findings, duration=None, sources=_MISSING):
    """Call validate_evidence; return (errors, failure_reason)."""
    try:
        if sources is _MISSING:
            return contract.validate_evidence(plan, findings, duration), None
        return (
            contract.validate_evidence(plan, findings, duration, sources=sources),
            None,
        )
    except TypeError as exc:
        text = str(exc)
        if "sources" in text:
            return None, "kwarg sources assente"
        return None, f"TypeError: {text}"
    except AttributeError as exc:
        return None, f"AttributeError: {exc}"


def _validate_plan(plan, report):
    """Call validate_plan; return (errors, failure_reason)."""
    try:
        return contract.validate_plan(plan, report), None
    except TypeError as exc:
        text = str(exc)
        if "sources" in text:
            return None, "kwarg sources assente"
        return None, f"TypeError: {text}"
    except AttributeError as exc:
        return None, f"AttributeError: {exc}"


def _mentions(errors, needle: str) -> bool:
    return any(needle in str(error) for error in (errors or []))


def _case(label, plan, findings, duration, sources, predicate) -> None:
    errors, reason = _evidence(plan, findings, duration, sources)
    if reason:
        check(label, False, reason)
        return
    check(label, predicate(errors), errors)


# --------------------------------------------------------------------------- #
# E1-E8: validate_evidence
# --------------------------------------------------------------------------- #


def contract_cases() -> None:
    print("contract: multi-source evidence")

    _case(
        "E1 v1 compat: no sources, no source_id",
        _plan(_witness()),
        [FINDING],
        DURATION,
        _MISSING,
        lambda errors: errors == [],
    )
    _case(
        "E2 v1 compat: no sources, explicit source_id",
        _plan(_witness("src_01")),
        [FINDING],
        DURATION,
        _MISSING,
        lambda errors: errors == [],
    )
    _case(
        "E3 strict: missing source_id",
        _plan(_witness()),
        [FINDING],
        DURATION,
        SOURCES,
        lambda errors: _mentions(errors, "source_id"),
    )
    _case(
        "E4 strict: valid source_id",
        _plan(_witness("src_01")),
        [FINDING],
        DURATION,
        SOURCES,
        lambda errors: errors == [],
    )
    _case(
        "E5 strict: unknown source_id",
        _plan(_witness("src_99")),
        [FINDING],
        DURATION,
        SOURCES,
        lambda errors: _mentions(errors, "src_99"),
    )

    finding_src = dict(FINDING, source_id="src_01")
    _case(
        "E6 finding source_id mismatch",
        _plan(_witness("src_02")),
        [finding_src],
        DURATION,
        SOURCES,
        lambda errors: _mentions(errors, "source_id"),
    )
    _case(
        "E7 finding source_id match",
        _plan(_witness("src_01")),
        [finding_src],
        DURATION,
        SOURCES,
        lambda errors: errors == [],
    )

    null_witness = {"finding_id": "brief.autonomous", "t0": None, "t1": None}
    _case(
        "E8 disabled treatment exempt",
        _plan(null_witness, enabled=False),
        [FINDING],
        DURATION,
        SOURCES,
        lambda errors: errors == [],
    )


# --------------------------------------------------------------------------- #
# E9: validate_plan
# --------------------------------------------------------------------------- #


def plan_cases() -> None:
    print("\ncontract: validate_plan sources")
    report = {"source": {"duration": DURATION}, "findings": [FINDING]}

    plan_missing = {**_plan(_witness()), "sources": SOURCES}
    errors, reason = _validate_plan(plan_missing, report)
    if reason:
        check("E9a validate_plan rejects missing source_id", False, reason)
    else:
        check(
            "E9a validate_plan rejects missing source_id",
            _mentions(errors, "source_id"),
            errors,
        )

    plan_valid = {**_plan(_witness("src_01")), "sources": SOURCES}
    errors, reason = _validate_plan(plan_valid, report)
    if reason:
        check("E9b validate_plan accepts valid source_id", False, reason)
    else:
        check("E9b validate_plan accepts valid source_id", errors == [], errors)


# --------------------------------------------------------------------------- #
# E10: version bump
# --------------------------------------------------------------------------- #


def version_case() -> None:
    print("\ncontract: version")
    try:
        version = contract.VERSION
        doc = contract.__doc__ or ""
    except AttributeError as exc:
        check("E10 contract VERSION 1.4.0", False, f"AttributeError: {exc}")
        return
    ok = version == "1.4.0" and "FROZEN interface v1.4.0" in doc
    check("E10 contract VERSION 1.4.0", ok, f"VERSION={version!r}")


# --------------------------------------------------------------------------- #
# E11-E13: producer
# --------------------------------------------------------------------------- #

MEDIA = {"duration": 10.0, "width": 1920, "height": 1080, "has_audio": True}


def _payload() -> dict:
    return {
        "contract_version": contract.VERSION,
        "execution_context": {
            "mode": "autonomous",
            "specificity_score": 0.0,
            "creative_rationale": {
                "genre_detected": "tiktok_promo",
                "pacing": {
                    "bpm_target": 120,
                    "cut_policy": "beat_synced",
                    "rationale": "ritmo dedotto",
                },
                "color_palette": {
                    "type": "duotone",
                    "shadows": "#1A100C",
                    "highlights": "#FFE8C8",
                    "rationale": "duotone autonomo",
                },
                "typography": {
                    "font_family": "Antonio",
                    "size_px": 72,
                    "rationale": "font certificato",
                },
                "rd_search_required": False,
                "rd_search_conducted": False,
                "rd_search_sources": [],
            },
        },
        "scene": {
            "aspect_ratio": "9:16",
            "resolution": [1080, 1920],
            "engine": "pillow",
        },
    }


def _producer(media_info):
    """Call the public producer; return (plan, failure_reason)."""
    try:
        return decide.payload_to_edit_plan(_payload(), media_info), None
    except Exception as exc:  # noqa: BLE001 - producer failures become SKIP
        return None, f"{type(exc).__name__}: {exc}"


def _color_witness(built):
    treatments = getattr(built, "treatments", None)
    if treatments is None and isinstance(built, dict):
        treatments = built.get("treatments")
    for treatment in treatments or []:
        if isinstance(treatment, dict) and treatment.get("name") == "color_fix":
            evidence = treatment.get("evidence") or []
            if evidence and isinstance(evidence[0], dict):
                return evidence[0]
    return None


def producer_cases() -> None:
    print("\nproducer: source_id propagation")

    built, reason = _producer(MEDIA)
    witness = None
    if reason:
        skip("E11 producer v1: no source_id", reason)
    else:
        witness = _color_witness(built)
        if witness is None:
            skip("E11 producer v1: no source_id", "color_fix witness assente")
        else:
            check(
                "E11 producer v1: no source_id",
                "source_id" not in witness,
                witness,
            )

    built_src, reason_src = _producer({**MEDIA, "source_id": "src_01"})
    if reason_src:
        skip("E12 producer multi: source_id", reason_src)
    else:
        witness_src = _color_witness(built_src)
        if witness_src is None:
            skip("E12 producer multi: source_id", "color_fix witness assente")
        else:
            check(
                "E12 producer multi: source_id",
                witness_src.get("source_id") == "src_01",
                witness_src,
            )

    if witness is None:
        skip("E13 producer v1: exact witness dict", "E11 non ha prodotto un witness")
    else:
        expected = {"finding_id": "brief.autonomous", "t0": 0.0, "t1": 10.0}
        check("E13 producer v1: exact witness dict", witness == expected, witness)


def main() -> int:
    contract_cases()
    plan_cases()
    version_case()
    producer_cases()
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
