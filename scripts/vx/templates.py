"""vx.templates — il registro dei template per compito: slot, parametri, layout.

Un template non e' un disegno: e' un contratto. Ogni voce dichiara

* da 1 a 8 **slot**, ognuno con un tipo (testo, immagine, video, logo, colore,
  forma) e un ruolo (titolo, corpo, invito, credito, sfondo, decoro, badge);
* i **parametri esposti**, ognuno con dominio e default dentro il dominio;
* un **layout** per ogni proporzione dichiarata (16:9, 9:16, 1:1, 4:5), con il
  riquadro di ogni slot in frazioni di frame;
* una **famiglia**.

Il linter e' la porta: un template senza slot, con un layout che dimentica uno
slot o con un riquadro fuori dal frame **non entra nel registro**, e quindi non
entra nel conteggio. Un valore fuori dominio e' rifiutato con il nome del
parametro e il suo intervallo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .contract import VxError

SLOT_TYPES = ("text", "image", "video", "logo", "color", "shape")
SLOT_ROLES = (
    "headline",
    "body",
    "cta",
    "credit",
    "background",
    "decoration",
    "badge",
)
RATIOS = ("16:9", "9:16", "1:1", "4:5")
MIN_SLOTS = 1
MAX_SLOTS = 8
FAMILIES = ("lower_third", "title", "quote", "stat", "product", "credits")


class TemplateError(VxError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Slot:
    name: str
    type: str
    role: str


@dataclass(frozen=True)
class Param:
    name: str
    lo: float
    hi: float
    default: float

    def contains(self, value: Any) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return self.lo <= float(value) <= self.hi


Box = Tuple[float, float, float, float]


@dataclass(frozen=True)
class Template:
    name: str
    family: str
    slots: Tuple[Slot, ...]
    params: Tuple[Param, ...]
    layouts: Dict[str, Dict[str, Box]]
    why: str = ""

    def slot(self, name: str) -> Optional[Slot]:
        for slot in self.slots:
            if slot.name == name:
                return slot
        return None

    def param(self, name: str) -> Optional[Param]:
        for param in self.params:
            if param.name == name:
                return param
        return None


#: Only linted templates live here; a template that fails the linter is kept in
#: :data:`REJECTED` with the reasons, and never counted.
REGISTRY: Dict[str, Template] = {}
REJECTED: Dict[str, List[str]] = {}


def _boxes(*boxes: Tuple[str, float, float, float, float]) -> Dict[str, Box]:
    return {name: (x, y, w, h) for name, x, y, w, h in boxes}


def lint(template: Template) -> List[str]:
    """Every reason this template cannot enter the registry."""
    problems: List[str] = []
    if not template.name:
        problems.append("name is empty")
    if template.family not in FAMILIES:
        problems.append(f"family {template.family!r} is not declared")
    if not MIN_SLOTS <= len(template.slots) <= MAX_SLOTS:
        problems.append(
            f"{len(template.slots)} slots: the registry accepts {MIN_SLOTS}-{MAX_SLOTS}"
        )
    seen_slots: Dict[str, int] = {}
    for slot in template.slots:
        seen_slots[slot.name] = seen_slots.get(slot.name, 0) + 1
        if slot.type not in SLOT_TYPES:
            problems.append(f"slot {slot.name!r}: type {slot.type!r} is not declared")
        if slot.role not in SLOT_ROLES:
            problems.append(f"slot {slot.name!r}: role {slot.role!r} is not declared")
    for name, count in seen_slots.items():
        if count > 1:
            problems.append(f"slot {name!r} declared {count} times")

    seen_params: Dict[str, int] = {}
    for param in template.params:
        seen_params[param.name] = seen_params.get(param.name, 0) + 1
        if param.lo >= param.hi:
            problems.append(f"param {param.name!r}: empty domain [{param.lo}, {param.hi}]")
        if not param.contains(param.default):
            problems.append(
                f"param {param.name!r}: default {param.default} outside [{param.lo}, {param.hi}]"
            )
    for name, count in seen_params.items():
        if count > 1:
            problems.append(f"param {name!r} declared {count} times")

    if not template.layouts:
        problems.append("layouts: no layout declared")
    for ratio in template.layouts:
        if ratio not in RATIOS:
            problems.append(f"layout {ratio!r}: ratio is not declared")
    for ratio, layout in template.layouts.items():
        missing = [slot.name for slot in template.slots if slot.name not in layout]
        if missing:
            problems.append(f"layout {ratio}: slots missing {missing}")
        extra = sorted(set(layout) - {slot.name for slot in template.slots})
        if extra:
            problems.append(f"layout {ratio}: slots not declared {extra}")
        for name, (x, y, width, height) in layout.items():
            if width <= 0.0 or height <= 0.0:
                problems.append(f"layout {ratio}.{name}: empty box")
            if x < 0.0 or y < 0.0 or x + width > 1.0001 or y + height > 1.0001:
                problems.append(
                    f"layout {ratio}.{name}: box ({x}, {y}, {width}, {height}) is outside the frame"
                )
    return problems


def register(template: Template) -> Template:
    """Add a template to the registry, or refuse it and keep the reason."""
    problems = lint(template)
    if problems:
        REJECTED[template.name] = problems
        first = problems[0]
        if "slots:" in first or "0 slots" in first:
            raise TemplateError("TEMPLATE_NO_SLOTS", f"{template.name}: {'; '.join(problems)}")
        raise TemplateError("TEMPLATE_LINT", f"{template.name}: {'; '.join(problems)}")
    REGISTRY[template.name] = template
    REJECTED.pop(template.name, None)
    return template


def resolve(name: str, values: Optional[Mapping[str, Any]] = None) -> Template:
    """The template, with every override inside its declared domain."""
    template = REGISTRY.get(str(name))
    if template is None:
        raise TemplateError(
            "TEMPLATE_UNKNOWN",
            f"unknown template {name!r}; registered: {sorted(REGISTRY)}",
        )
    for key, value in (values or {}).items():
        param = template.param(key)
        if param is None:
            raise TemplateError(
                "TEMPLATE_PARAM",
                f"{template.name}: {key!r} is not an exposed parameter; declared: "
                f"{[item.name for item in template.params]}",
            )
        if not param.contains(value):
            raise TemplateError(
                "TEMPLATE_PARAM",
                f"{template.name}.{key} = {value!r} outside [{param.lo}, {param.hi}]",
            )
    return template


def validate() -> List[str]:
    problems: List[str] = []
    for name, template in REGISTRY.items():
        if name != template.name:
            problems.append(f"{name}: key and name disagree")
        problems.extend(f"{name}: {problem}" for problem in lint(template))
    return problems


def count() -> int:
    return len(REGISTRY)


def by_family() -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = {family: [] for family in FAMILIES}
    for name, template in REGISTRY.items():
        grouped[template.family].append(name)
    return grouped


# --------------------------------------------------------------------------- #
# i template
# --------------------------------------------------------------------------- #
def _slots(*items: Tuple[str, str, str]) -> Tuple[Slot, ...]:
    return tuple(Slot(*item) for item in items)


def _params(*items: Tuple[str, float, float, float]) -> Tuple[Param, ...]:
    return tuple(Param(*item) for item in items)


_TEMPLATES: Tuple[Template, ...] = (
    Template(
        name="lower_third",
        family="lower_third",
        slots=_slots(("headline", "text", "headline"), ("body", "text", "body")),
        params=_params(("panel", 0.2, 0.9, 0.55), ("inset", 0.0, 0.12, 0.04)),
        layouts={
            "16:9": _boxes(
                ("headline", 0.08, 0.74, 0.52, 0.10), ("body", 0.08, 0.84, 0.52, 0.06)
            ),
            "9:16": _boxes(
                ("headline", 0.08, 0.70, 0.84, 0.10), ("body", 0.08, 0.80, 0.84, 0.06)
            ),
            "1:1": _boxes(
                ("headline", 0.08, 0.74, 0.84, 0.10), ("body", 0.08, 0.84, 0.84, 0.06)
            ),
            "4:5": _boxes(
                ("headline", 0.08, 0.72, 0.84, 0.10), ("body", 0.08, 0.82, 0.84, 0.06)
            ),
        },
        why="Il nome e la qualifica: due slot, un pannello, nessuna decorazione.",
    ),
    Template(
        name="title_card",
        family="title",
        slots=_slots(("headline", "text", "headline"), ("badge", "shape", "badge")),
        params=_params(("tracking", 0.0, 0.2, 0.06), ("size", 0.5, 1.6, 1.0)),
        layouts={
            "16:9": _boxes(
                ("headline", 0.10, 0.38, 0.80, 0.20), ("badge", 0.10, 0.60, 0.16, 0.05)
            ),
            "9:16": _boxes(
                ("headline", 0.08, 0.44, 0.84, 0.16), ("badge", 0.08, 0.62, 0.24, 0.04)
            ),
            "1:1": _boxes(
                ("headline", 0.08, 0.40, 0.84, 0.18), ("badge", 0.08, 0.60, 0.20, 0.05)
            ),
            "4:5": _boxes(
                ("headline", 0.08, 0.42, 0.84, 0.18), ("badge", 0.08, 0.62, 0.20, 0.05)
            ),
        },
        why="Apertura con un titolo e una targhetta: il minimo per dire di cosa si parla.",
    ),
    Template(
        name="quote",
        family="quote",
        slots=_slots(
            ("headline", "text", "headline"),
            ("credit", "text", "credit"),
            ("mark", "shape", "decoration"),
        ),
        params=_params(("indent", 0.0, 0.2, 0.08), ("line", 0.0, 0.08, 0.02)),
        layouts={
            "16:9": _boxes(
                ("headline", 0.12, 0.28, 0.70, 0.30),
                ("credit", 0.12, 0.60, 0.50, 0.06),
                ("mark", 0.06, 0.24, 0.04, 0.10),
            ),
            "9:16": _boxes(
                ("headline", 0.10, 0.26, 0.80, 0.30),
                ("credit", 0.10, 0.58, 0.60, 0.06),
                ("mark", 0.06, 0.22, 0.05, 0.08),
            ),
            "1:1": _boxes(
                ("headline", 0.10, 0.28, 0.80, 0.28),
                ("credit", 0.10, 0.58, 0.60, 0.06),
                ("mark", 0.05, 0.24, 0.04, 0.08),
            ),
            "4:5": _boxes(
                ("headline", 0.10, 0.27, 0.80, 0.30),
                ("credit", 0.10, 0.59, 0.60, 0.06),
                ("mark", 0.05, 0.23, 0.04, 0.08),
            ),
        },
        why="Una citazione con la sua firma e un segno grafico: tre slot, non uno in piu'.",
    ),
    Template(
        name="stat",
        family="stat",
        slots=_slots(
            ("headline", "text", "headline"),
            ("body", "text", "body"),
            ("unit", "text", "badge"),
        ),
        params=_params(("scale", 1.0, 3.0, 1.8)),
        layouts={
            "16:9": _boxes(
                ("headline", 0.10, 0.30, 0.50, 0.26),
                ("body", 0.10, 0.58, 0.50, 0.08),
                ("unit", 0.62, 0.32, 0.12, 0.10),
            ),
            "9:16": _boxes(
                ("headline", 0.08, 0.34, 0.70, 0.22),
                ("body", 0.08, 0.58, 0.70, 0.08),
                ("unit", 0.08, 0.66, 0.16, 0.05),
            ),
            "1:1": _boxes(
                ("headline", 0.08, 0.32, 0.60, 0.24),
                ("body", 0.08, 0.58, 0.60, 0.08),
                ("unit", 0.70, 0.34, 0.14, 0.08),
            ),
            "4:5": _boxes(
                ("headline", 0.08, 0.33, 0.64, 0.24),
                ("body", 0.08, 0.59, 0.64, 0.08),
                ("unit", 0.72, 0.35, 0.14, 0.07),
            ),
        },
        why="Un numero grande, la sua spiegazione e l'unita' misurata accanto.",
    ),
    Template(
        name="product_card",
        family="product",
        slots=_slots(
            ("media", "image", "background"),
            ("headline", "text", "headline"),
            ("body", "text", "body"),
            ("cta", "text", "cta"),
            ("logo", "logo", "badge"),
        ),
        params=_params(("card", 0.3, 0.95, 0.62), ("radius", 0.0, 0.08, 0.02)),
        layouts={
            "16:9": _boxes(
                ("media", 0.04, 0.10, 0.46, 0.80),
                ("headline", 0.54, 0.18, 0.40, 0.12),
                ("body", 0.54, 0.32, 0.40, 0.14),
                ("cta", 0.54, 0.50, 0.24, 0.08),
                ("logo", 0.88, 0.06, 0.08, 0.08),
            ),
            "9:16": _boxes(
                ("media", 0.04, 0.04, 0.92, 0.46),
                ("headline", 0.06, 0.54, 0.88, 0.10),
                ("body", 0.06, 0.66, 0.88, 0.12),
                ("cta", 0.06, 0.80, 0.40, 0.06),
                ("logo", 0.84, 0.52, 0.12, 0.05),
            ),
            "1:1": _boxes(
                ("media", 0.04, 0.04, 0.92, 0.52),
                ("headline", 0.06, 0.60, 0.88, 0.10),
                ("body", 0.06, 0.72, 0.88, 0.10),
                ("cta", 0.06, 0.84, 0.36, 0.06),
                ("logo", 0.84, 0.58, 0.10, 0.06),
            ),
            "4:5": _boxes(
                ("media", 0.04, 0.04, 0.92, 0.54),
                ("headline", 0.06, 0.61, 0.88, 0.10),
                ("body", 0.06, 0.72, 0.88, 0.10),
                ("cta", 0.06, 0.84, 0.36, 0.06),
                ("logo", 0.84, 0.58, 0.10, 0.06),
            ),
        },
        why="Prodotto a sinistra, testo e invito a destra: cinque slot, ognuno con il suo posto.",
    ),
    Template(
        name="credits",
        family="credits",
        slots=_slots(
            ("headline", "text", "headline"),
            ("body", "text", "body"),
            ("foot", "text", "credit"),
        ),
        params=_params(("lead", 0.0, 0.2, 0.05), ("speed", 0.2, 1.0, 0.4)),
        layouts={
            "16:9": _boxes(
                ("headline", 0.10, 0.30, 0.40, 0.10),
                ("body", 0.10, 0.42, 0.40, 0.26),
                ("foot", 0.10, 0.78, 0.60, 0.05),
            ),
            "9:16": _boxes(
                ("headline", 0.08, 0.24, 0.60, 0.08),
                ("body", 0.08, 0.34, 0.84, 0.34),
                ("foot", 0.08, 0.82, 0.84, 0.05),
            ),
            "1:1": _boxes(
                ("headline", 0.08, 0.26, 0.50, 0.08),
                ("body", 0.08, 0.36, 0.84, 0.32),
                ("foot", 0.08, 0.80, 0.84, 0.05),
            ),
            "4:5": _boxes(
                ("headline", 0.08, 0.25, 0.50, 0.08),
                ("body", 0.08, 0.35, 0.84, 0.33),
                ("foot", 0.08, 0.80, 0.84, 0.05),
            ),
        },
        why="Titoli di coda: chi ha fatto cosa, con la riga di chiusura in fondo.",
    ),
    Template(
        name="banner",
        family="title",
        slots=_slots(
            ("media", "image", "background"),
            ("headline", "text", "headline"),
        ),
        params=_params(("tracking", 0.0, 0.2, 0.04)),
        layouts={
            "16:9": _boxes(
                ("media", 0.0, 0.0, 1.0, 1.0),
                ("headline", 0.06, 0.40, 0.88, 0.18),
            ),
        },
        why="Solo orizzontale, per scelta: un banner non si gira in verticale, si dichiara.",
    ),
)


SLOT_COLOURS = (
    "#e4572e", "#17bebb", "#ffc914", "#76b041",
    "#2e86ab", "#a29bfe", "#ff6f91", "#b8b8ff",
)


def render_golden(
    template: "Template",
    *,
    ratio: str = "16:9",
    size: Tuple[int, int] = (640, 360),
    out: Path,
    background: str = "#101010",
) -> Path:
    """Draw each slot box in its own colour: the golden proves the geometry."""
    import subprocess

    from . import ffmpeg as ff

    layout = template.layouts.get(ratio)
    if layout is None:
        raise TemplateError("TEMPLATE_LINT", f"{template.name} has no layout for {ratio}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    width, height = int(size[0]), int(size[1])
    parts = []
    for index, slot in enumerate(template.slots):
        x, y, w, h = layout[slot.name]
        parts.append(
            "drawbox=x={}:y={}:w={}:h={}:color={}:t=fill".format(
                int(round(x * width)),
                int(round(y * height)),
                max(2, int(round(w * width))),
                max(2, int(round(h * height))),
                SLOT_COLOURS[index % len(SLOT_COLOURS)],
            )
        )
    proc = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={background}:s={width}x{height}:d=1:r=1",
            "-vf", ",".join(parts), "-frames:v", "1", str(out),
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not out.is_file():
        raise TemplateError("TEMPLATE_GOLDEN", proc.stderr.decode("utf-8", "replace")[-200:])
    return out


def _bootstrap() -> None:
    for template in _TEMPLATES:
        register(template)


_bootstrap()


__all__ = [
    "FAMILIES",
    "MAX_SLOTS",
    "MIN_SLOTS",
    "Param",
    "RATIOS",
    "SLOT_COLOURS",
    "REGISTRY",
    "REJECTED",
    "SLOT_ROLES",
    "SLOT_TYPES",
    "Slot",
    "Template",
    "TemplateError",
    "by_family",
    "count",
    "lint",
    "register",
    "render_golden",
    "resolve",
    "validate",
]
