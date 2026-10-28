"""Generate the effect-library index from the live registry.

The index documents every declarative effect kind once: its family, the trigger
that justifies it, its parameters (name, interval, default), its cap in seconds,
its fallback and its backend. It is never hand-typed; this script reads
``vx.edl3.EFFECT_REGISTRY`` and rewrites the marked block inside
``references/26-edl3-e-ponte.md``.

    python scripts/gen_effect_index.py            # rewrite the block
    python scripts/gen_effect_index.py --check    # exit 1 if the block is stale

Two runs of the writer produce the same bytes: rows are sorted by family then
kind, and every number goes through the renderer's plain-decimal formatter, so
no timestamp, path or float repr can drift between runs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, edl3_render, effects  # noqa: E402

TARGET = ROOT / "references" / "26-edl3-e-ponte.md"
BEGIN = "<!-- BEGIN EFFECT-INDEX (generated, do not edit by hand) -->"
END = "<!-- END EFFECT-INDEX -->"

HEADER = """## 13. Indice della libreria degli effetti

Tabella generata da `vx.edl3.EFFECT_REGISTRY`, non scritta a mano: una riga per
ogni tipo dichiarativo, con famiglia, innesco, parametri (nome, intervallo,
default), tetto in secondi, ripiego e backend. Rigenerare con
`python scripts/gen_effect_index.py`; `--check` fallisce se il file e stantio.
Un tetto `nessuno` significa che il tipo non dichiara un limite di durata
(`cap_seconds` a zero): la disciplina resta quella del registro, non un numero
inventato qui.

| tipo | famiglia | innesco | parametri | tetto | ripiego | backend |
|---|---|---|---|---|---|---|"""


def number(value: float) -> str:
    """Plain decimal, identical to the renderer's filter formatting."""
    return edl3_render.filter_number(value)


def cell(text: str) -> str:
    """One table cell: single line, pipes escaped."""
    return " ".join(str(text).split()).replace("|", "\\|")


def param_cell(spec: edl3.EffectSpec) -> str:
    parts = [
        f"`{param.name}` {number(param.lo)}..{number(param.hi)} "
        f"(default {number(param.default)})"
        for param in spec.params_spec
    ]
    return "<br>".join(parts)


def cap_cell(spec: edl3.EffectSpec) -> str:
    return "nessuno" if spec.cap_seconds <= 0.0 else f"{number(spec.cap_seconds)} s"


def declarative_specs() -> List[edl3.EffectSpec]:
    """Every family entry, sorted by family then kind, core kinds excluded."""
    grouped = effects.families()
    specs = [
        grouped[family][kind]
        for family in sorted(grouped)
        for kind in sorted(grouped[family])
    ]
    for spec in specs:
        registered = edl3.EFFECT_REGISTRY.get(spec.kind)
        if registered is not spec:
            raise SystemExit(f"{spec.kind}: family entry is not the registered spec")
    return specs


def row(spec: edl3.EffectSpec) -> str:
    cells = (
        cell(f"`{spec.kind}`"),
        cell(spec.family),
        cell(spec.trigger),
        cell(param_cell(spec)),
        cell(cap_cell(spec)),
        cell(spec.fallback),
        cell(spec.backend),
    )
    for value in cells:
        if not value:
            raise SystemExit(f"{spec.kind}: empty cell, the row would be a defect")
    return "| " + " | ".join(cells) + " |"


def render() -> str:
    specs = declarative_specs()
    families = sorted({spec.family for spec in specs})
    lines = [HEADER]
    lines.extend(row(spec) for spec in specs)
    lines.append("")
    lines.append(
        f"{len(specs)} tipi dichiarativi in {len(families)} famiglie "
        f"({', '.join(families)}), piu i cinque tipi di contratto "
        "(`transform`, `crop`, `color`, `blur`, `chroma_key`) che vivono nel "
        "registro core con i propri backend e la propria validazione."
    )
    return "\n".join(lines) + "\n"


def splice(document: str, block: str) -> str:
    """Replace the marked block, or append it when the markers are absent."""
    start = document.find(BEGIN)
    if start < 0:
        tail = "" if document.endswith("\n") else "\n"
        return f"{document}{tail}\n---\n\n{BEGIN}\n{block}{END}\n"
    stop = document.find(END, start)
    if stop < 0:
        raise SystemExit(f"{TARGET.name}: begin marker without its end marker")
    return f"{document[:start]}{BEGIN}\n{block}{END}{document[stop + len(END):]}"


def main(argv: List[str]) -> int:
    current = TARGET.read_text(encoding="utf-8")
    block = render()
    updated = splice(current, block)
    if "--check" in argv:
        if updated != current:
            print(f"{TARGET.name}: effect index is stale, run the generator")
            return 1
        print(f"{TARGET.name}: effect index is current")
        return 0
    if updated == current:
        print(f"{TARGET.name}: unchanged, {len(declarative_specs())} rows")
        return 0
    TARGET.write_text(updated, encoding="utf-8", newline="\n")
    print(f"{TARGET.name}: written, {len(declarative_specs())} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
