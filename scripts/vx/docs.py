"""vx.docs — the documentation the ledger can check.

Why: a brief without documentation is a black box, and documentation that
describes effects the renderer never executed is worse than none. This module
renders the brief into a Markdown section whose hash the brief itself carries,
so ``prove.docs_match`` can refuse any text that drifts from the ledger.

The three mandatory sentences are the ones the verdict pretends: the asserted
animation direction, the surface-only scope of ``distinct_surface``, and the
declared reduction of scope on decorations. They live in
``prove.MANDATORY_SENTENCES``; this module renders them verbatim and never
paraphrases them.
"""

from __future__ import annotations

from typing import Any, Dict

from . import prove
from .registry import AXES

_AXIS_LABELS = {
    "palette": "Palette",
    "grade": "Grade",
    "animation": "Animazione",
    "cut_density": "Densita' dei tagli",
    "composition": "Inquadratura",
    "typography": "Tipografia",
}


def hash_ledger(brief: Dict[str, Any]) -> str:
    """sha256 of the canonical ``executed`` list — the ledger fingerprint."""
    executed = brief.get("executed") or []
    return prove._sha256_text(prove.canonical_json(executed))


def _executed_by_axis(brief: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in brief.get("executed") or []:
        if isinstance(item, dict) and item.get("axis") in AXES:
            out[item["axis"]] = item
    return out


def render(brief: Dict[str, Any]) -> str:
    """Render the brief as a Markdown section.

    One block per axis: driver, value, provenance, the decision sentence from
    ``reasons``, and whether the axis actually executed. Then the ledger hash
    and the three mandatory sentences, verbatim.
    """
    choices = brief.get("choices") or {}
    reasons = {
        r.get("axis"): r for r in (brief.get("reasons") or []) if isinstance(r, dict)
    }
    executed = _executed_by_axis(brief)

    lines: list[str] = ["## Direzione visiva adattiva", ""]
    lines.append(
        f"Progetto: `{brief.get('project_id', '')}` — schema `{brief.get('schema', '')}`"
    )
    lines.append("")
    for axis in AXES:
        choice = choices.get(axis) or {}
        reason = reasons.get(axis) or {}
        run = executed.get(axis) or {}
        name = run.get("name") or "none"
        executed_flag = "eseguito" if name != "none" else "non eseguito"
        lines.append(f"### {_AXIS_LABELS[axis]} (`{axis}`)")
        lines.append("")
        lines.append(f"- driver: `{choice.get('driver')}`")
        lines.append(f"- valore: `{choice.get('value')}`")
        lines.append(f"- provenienza: `{choice.get('provenance')}`")
        decision = reason.get("decision") or choice.get("inaction_reason") or ""
        if decision:
            lines.append(f"- decisione: {decision}")
        filter_text = run.get("filter") or "null"
        lines.append(
            f"- esecuzione: {executed_flag} (nome `{name}`, filtro `{filter_text}`, "
            f"rc {run.get('rc')}, frame {run.get('frames')})"
        )
        lines.append("")

    lines.append(f"Hash del ledger: `{hash_ledger(brief)}`")
    lines.append("")
    for sentence in prove.MANDATORY_SENTENCES:
        lines.append(sentence)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def match(brief: Dict[str, Any], text: str) -> bool:
    """Delegate to the prover: the gate lives in one place only."""
    return prove.docs_match(brief, text)
