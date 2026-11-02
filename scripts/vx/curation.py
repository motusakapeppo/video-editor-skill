"""vx.curation — un template entra solo se e' curato: golden, parametri, linter, critico.

La porta del registro non e' "il codice gira". Un template entra se porta tutte
e quattro le cose:

1. il **linter** verde (slot, layout, domini);
2. i **parametri esposti** dichiarati, con il loro dominio;
3. un **golden** su disco, e lo sha256 nel record deve essere quello del file:
   se il golden cambia, la curatela e' scaduta;
4. il **critico** che approva, e il critico qui e' una misura, non un gusto: il
   golden viene misurato contro il layout, ogni riquadro deve avere il colore
   dello slot, e la cornice non deve essere uniforme. Se la misura non torna,
   non c'e' approvazione.

Il conteggio dichiarato conta i **disegni**, non le varianti: due template con la
stessa geometria e gli stessi parametri sono lo stesso disegno con due nomi, e
il secondo si registra come variante senza gonfiare il numero.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import templates
from .contract import VxError

SCHEMA = "vx.curation/1"
RECORD_DIR = Path("tests") / "fixtures" / "templates"
GOLDEN_DIR = RECORD_DIR
GOLDEN_RATIO = "16:9"
GOLDEN_SIZE = (640, 360)
COLOUR_TOLERANCE = 8.0
MIN_STD = 2.0
MIN_SLOT_SHARE = 0.25

#: Curated templates, and the variants that were admitted but do not count.
REGISTRY: Dict[str, templates.Template] = {}
VARIANTS: Dict[str, str] = {}
REFUSED: Dict[str, List[str]] = {}


class CurationError(VxError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _design_key(template: templates.Template) -> str:
    """Geometry and parameters: the same design with two names is one design."""
    payload = {
        "family": template.family,
        "slots": [(slot.name, slot.type, slot.role) for slot in template.slots],
        "params": [(param.name, param.lo, param.hi, param.default) for param in template.params],
        "layouts": {
            ratio: {name: list(box) for name, box in layout.items()}
            for ratio, layout in sorted(template.layouts.items())
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _measure(golden: Path, template: templates.Template) -> Tuple[Dict[str, Any], List[str]]:
    """The critic: does the golden actually show this layout?"""
    import numpy as np
    from PIL import Image

    problems: List[str] = []
    with Image.open(golden) as image:
        pixels = np.asarray(image.convert("RGB"), dtype=float)
    height, width = pixels.shape[0], pixels.shape[1]
    layout = template.layouts[GOLDEN_RATIO]
    shares: Dict[str, float] = {}
    for index, slot in enumerate(template.slots):
        x, y, w, h = layout[slot.name]
        left, top = int(round(x * width)), int(round(y * height))
        right = min(width, left + max(2, int(round(w * width))))
        bottom = min(height, top + max(2, int(round(h * height))))
        crop = pixels[top:bottom, left:right]
        if crop.size == 0:
            problems.append(f"{slot.name}: empty box")
            continue
        want = templates.SLOT_COLOURS[index % len(templates.SLOT_COLOURS)]
        target = np.array(
            [int(want[channel : channel + 2], 16) for channel in (1, 3, 5)], dtype=float
        )
        near = np.abs(crop - target).max(axis=2) <= COLOUR_TOLERANCE
        share = float(near.mean())
        shares[slot.name] = round(share, 4)
        # Un riquadro coperto in parte da uno slot disegnato dopo resta visibile:
        # si chiede che il colore dello slot ci sia, non che riempia tutto.
        if share < MIN_SLOT_SHARE:
            problems.append(
                f"{slot.name}: the golden shows {share:.0%} of {want}, expected "
                f"at least {MIN_SLOT_SHARE:.0%}"
            )
    spread = float(pixels.reshape(-1, 3).std())
    if spread < MIN_STD:
        problems.append(f"golden is uniform (std {spread:.2f})")
    return {"share": shares, "std": round(spread, 3)}, problems


def approve(
    template: templates.Template,
    golden: Path,
    record: Path,
    *,
    why: str,
) -> Dict[str, Any]:
    """Check all four conditions and write the curation record."""
    golden, record = Path(golden), Path(record)
    problems = templates.lint(template)
    if problems:
        raise CurationError("TEMPLATE_UNCURATED", f"{template.name}: linter: {problems[0]}")
    if not template.params:
        raise CurationError("TEMPLATE_UNCURATED", f"{template.name}: no exposed parameters")
    if not golden.is_file():
        raise CurationError("TEMPLATE_UNCURATED", f"{template.name}: golden missing at {golden}")
    measured, critic_problems = _measure(golden, template)
    if critic_problems:
        raise CurationError(
            "TEMPLATE_UNCURATED", f"{template.name}: critic: {critic_problems[0]}"
        )
    if not str(why).strip():
        raise CurationError("TEMPLATE_UNCURATED", f"{template.name}: the critic gave no reason")
    root = Path(__file__).resolve().parents[2]
    try:
        stored = golden.resolve().relative_to(root).as_posix()
    except ValueError:
        stored = golden.as_posix()
    payload = {
        "schema": SCHEMA,
        "template": template.name,
        "golden": stored,
        "golden_sha256": _sha(golden),
        "ratio": GOLDEN_RATIO,
        "params": [param.name for param in template.params],
        "critic": {"verdict": "approved", "why": str(why), **measured},
    }
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return payload


def admit(template: templates.Template, record: Path) -> str:
    """Register a curated template; return ``"design"`` or ``"variant"``."""
    record = Path(record)
    if not record.is_file():
        REFUSED[template.name] = ["no curation record"]
        raise CurationError(
            "TEMPLATE_UNCURATED", f"{template.name}: no curation record at {record}"
        )
    try:
        payload = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        REFUSED[template.name] = [f"record unreadable: {exc}"]
        raise CurationError("TEMPLATE_UNCURATED", f"{template.name}: record unreadable") from exc

    problems: List[str] = []
    if payload.get("schema") != SCHEMA:
        problems.append(f"schema {payload.get('schema')!r} is not {SCHEMA}")
    if payload.get("template") != template.name:
        problems.append(f"record names {payload.get('template')!r}")
    lint_problems = templates.lint(template)
    problems.extend(f"linter: {problem}" for problem in lint_problems)
    if sorted(payload.get("params") or []) != sorted(param.name for param in template.params):
        problems.append("exposed parameters differ from the record")
    golden = Path(str(payload.get("golden") or ""))
    if not golden.is_absolute():
        golden = Path(__file__).resolve().parents[2] / golden
    if not golden.is_file():
        problems.append(f"golden missing at {golden}")
    elif _sha(golden) != payload.get("golden_sha256"):
        problems.append("golden changed since curation")
    critic = payload.get("critic") or {}
    if not isinstance(critic, Mapping) or critic.get("verdict") != "approved":
        problems.append("the critic did not approve")
    elif not str(critic.get("why") or "").strip():
        problems.append("the critic gave no reason")

    if problems:
        REFUSED[template.name] = problems
        raise CurationError("TEMPLATE_UNCURATED", f"{template.name}: {problems[0]}")

    key = _design_key(template)
    for name, existing in REGISTRY.items():
        if _design_key(existing) == key:
            VARIANTS[template.name] = name
            REFUSED.pop(template.name, None)
            return "variant"
    REGISTRY[template.name] = template
    templates.REGISTRY[template.name] = template
    REFUSED.pop(template.name, None)
    return "design"


def count() -> int:
    """Declared count: designs only, variants never inflate it."""
    return len(REGISTRY)


def validate() -> List[str]:
    problems: List[str] = []
    seen: Dict[str, str] = {}
    for name, template in REGISTRY.items():
        key = _design_key(template)
        if key in seen:
            problems.append(f"{name}: a variant of {seen[key]} is counted as a design")
        seen[key] = name
        problems.extend(f"{name}: {problem}" for problem in templates.lint(template))
    return problems


__all__ = [
    "COLOUR_TOLERANCE",
    "CurationError",
    "GOLDEN_RATIO",
    "GOLDEN_SIZE",
    "MIN_SLOT_SHARE",
    "MIN_STD",
    "RECORD_DIR",
    "REFUSED",
    "REGISTRY",
    "SCHEMA",
    "VARIANTS",
    "admit",
    "approve",
    "count",
    "validate",
]
