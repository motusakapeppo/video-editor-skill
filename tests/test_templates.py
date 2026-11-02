"""F25-T12: il registro dei template, con slot, domini e linter come porta.

Scenario:

S1 — il registro conta sei template, in sei famiglie; ognuno dichiara 1-8 slot con
     tipo e ruolo, parametri con dominio e default dentro, un layout per ognuna
     delle quattro proporzioni, e una famiglia; `validate()` e' pulito.
S2 — un template senza slot e' rifiutato con `TEMPLATE_NO_SLOTS` e non entra nel
     conteggio.
S3 — un valore fuori dominio e' `TEMPLATE_PARAM` con il nome del parametro e
     l'intervallo; un parametro non esposto e' rifiutato con il suo nome.
S4 — un template che non passa il linter (layout che dimentica uno slot) resta
     fuori dal registro e dal conteggio, con il motivo conservato.

    python tests/test_templates.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import templates  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def main() -> int:
    # S1 -------------------------------------------------------------------- #
    check(
        "S1 seven templates, six families",
        templates.count() == 7 and all(templates.by_family().values()),
        {family: len(names) for family, names in templates.by_family().items()},
    )
    check("S1 the registry is clean", not templates.validate(), templates.validate())
    bad_slots = [
        name
        for name, template in templates.REGISTRY.items()
        if not 1 <= len(template.slots) <= 8
        or any(slot.type not in templates.SLOT_TYPES or slot.role not in templates.SLOT_ROLES for slot in template.slots)
    ]
    check("S1 every slot declares type and role, inside 1..8", not bad_slots, bad_slots)
    bad_layouts = [
        name
        for name, template in templates.REGISTRY.items()
        if not template.layouts
        or any(set(layout) != {slot.name for slot in template.slots} for layout in template.layouts.values())
    ]
    check("S1 every template declares layouts covering every slot", not bad_layouts, bad_layouts)
    bad_params = [
        f"{name}.{param.name}"
        for name, template in templates.REGISTRY.items()
        for param in template.params
        if not param.contains(param.default)
    ]
    check("S1 every exposed parameter has a domain containing its default", not bad_params, bad_params)

    # S2 -------------------------------------------------------------------- #
    before = templates.count()
    empty = templates.Template(
        name="senza_slot",
        family="title",
        slots=(),
        params=(),
        layouts={ratio: {} for ratio in templates.RATIOS},
    )
    try:
        templates.register(empty)
        code = ""
    except templates.TemplateError as exc:
        code = exc.code
    check("S2 a template without slots is refused", code == "TEMPLATE_NO_SLOTS", code)
    check(
        "S2 and it does not enter the count",
        templates.count() == before and "senza_slot" not in templates.REGISTRY,
        f"{templates.count()} template",
    )
    check(
        "S2 the reason is kept",
        any("slots" in problem for problem in templates.REJECTED.get("senza_slot", [])),
        templates.REJECTED.get("senza_slot", [])[:1],
    )

    # S3 -------------------------------------------------------------------- #
    try:
        templates.resolve("lower_third", {"panel": 4.0})
        code, detail = "", ""
    except templates.TemplateError as exc:
        code, detail = exc.code, exc.detail
    check("S3 a value outside its domain is refused", code == "TEMPLATE_PARAM", code)
    check(
        "S3 and the refusal names the parameter and its interval",
        ".panel" in detail and "[0.2, 0.9]" in detail,
        detail[:70],
    )
    try:
        templates.resolve("lower_third", {"colore": "#fff"})
        unknown_code, unknown_detail = "", ""
    except templates.TemplateError as exc:
        unknown_code, unknown_detail = exc.code, exc.detail
    check(
        "S3 an unexposed parameter is refused with its name",
        unknown_code == "TEMPLATE_PARAM" and "'colore'" in unknown_detail,
        unknown_detail[:60],
    )
    check(
        "S3 and the exposed default resolves",
        templates.resolve("lower_third", {"panel": 0.6}).name == "lower_third",
        "",
    )

    # S4 -------------------------------------------------------------------- #
    before = templates.count()
    broken = templates.Template(
        name="layout_incompleto",
        family="quote",
        slots=(
            templates.Slot("headline", "text", "headline"),
            templates.Slot("credit", "text", "credit"),
        ),
        params=(templates.Param("indent", 0.0, 0.2, 0.05),),
        layouts={
            "16:9": {"headline": (0.1, 0.2, 0.6, 0.2)},
            "9:16": {"headline": (0.1, 0.2, 0.8, 0.2)},
            "1:1": {"headline": (0.1, 0.2, 0.7, 0.2)},
            "4:5": {"headline": (0.1, 0.2, 0.7, 0.2)},
        },
    )
    try:
        templates.register(broken)
        lint_code = ""
    except templates.TemplateError as exc:
        lint_code = exc.code
    check("S4 a template that fails the linter is refused", lint_code == "TEMPLATE_LINT", lint_code)
    check(
        "S4 and it is not counted",
        templates.count() == before and "layout_incompleto" not in templates.REGISTRY,
        templates.count(),
    )
    check(
        "S4 the linter names the missing slot",
        any("slots missing" in problem for problem in templates.REJECTED.get("layout_incompleto", [])),
        templates.REJECTED.get("layout_incompleto", [])[:1],
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
