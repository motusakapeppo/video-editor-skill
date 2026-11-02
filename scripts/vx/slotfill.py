"""vx.slotfill — riempire gli slot di un template dai ruoli della MaterialMap.

La MaterialMap e' il progetto del cliente: per ogni **ruolo** (``background``,
``headline``, ``badge``, ...) un materiale con il suo tipo. Il riempimento
accoppia ruolo e slot:

* uno slot prende il materiale del suo ruolo, se il tipo combacia;
* un tipo sbagliato e' ``SLOT_TYPE`` e nomina lo slot, il tipo che chiedeva e
  quello che ha ricevuto;
* gli slot senza materiale restano dichiarati ``unfilled``, non riempiti a caso.

``render`` mette i materiali nei riquadri del layout, in pixel, e produce un
fotogramma: il test misura il MAD **dentro** la zona dello slot contro lo stesso
fotogramma senza riempimento, cosi' la prova e' sul frame e non sul parametro.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import templates
from . import ffmpeg as ff
from .contract import VxError

MATERIAL_KINDS = ("image", "video", "logo", "color", "text")
#: A logo is an image with a declared meaning; a slot that takes an image can
#: take a logo, the reverse is not true.
COMPATIBLE = {
    "image": ("image", "logo"),
    "video": ("video",),
    "logo": ("logo",),
    "color": ("color",),
    "text": ("text",),
}


class SlotError(VxError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Material:
    path: str
    kind: str

    def problem(self) -> Optional[str]:
        if self.kind not in MATERIAL_KINDS:
            return f"kind {self.kind!r} is not declared"
        if self.kind in ("image", "video", "logo") and not Path(self.path).is_file():
            return f"{self.kind} file not found: {self.path}"
        if self.kind == "color" and not str(self.path).startswith("#"):
            return f"color {self.path!r} is not #rrggbb"
        return None


class MaterialMap(dict):
    """role -> :class:`Material`, with the kinds the fill knows how to place."""

    def add(self, role: str, path: str, kind: str = "image") -> "MaterialMap":
        self[str(role)] = Material(path=str(path), kind=str(kind))
        return self

    def problems(self) -> List[str]:
        found: List[str] = []
        for role, material in self.items():
            problem = material.problem()
            if problem:
                found.append(f"{role}: {problem}")
        return found


@dataclass(frozen=True)
class Fill:
    slot: str
    role: str
    kind: str
    source: str
    box: Tuple[float, float, float, float]


def fill(
    template: templates.Template,
    materials: Mapping[str, Material],
    *,
    ratio: str = "16:9",
) -> Dict[str, Any]:
    """Match materials to slots by role, at one ratio."""
    layout = template.layouts.get(ratio)
    if layout is None:
        raise SlotError(
            "SLOT_RATIO",
            f"{template.name} has no layout for {ratio!r}; declared: {sorted(template.layouts)}",
        )
    filled: List[Fill] = []
    unfilled: List[Dict[str, str]] = []
    for slot in template.slots:
        material = materials.get(slot.role)
        if material is None:
            unfilled.append({"slot": slot.name, "role": slot.role, "why": "nessun materiale per il ruolo"})
            continue
        if material.kind not in COMPATIBLE.get(slot.type, ()):
            raise SlotError(
                "SLOT_TYPE",
                f"{template.name}.{slot.name} takes a {slot.type} but the material for role "
                f"{slot.role!r} is a {material.kind}",
            )
        box = layout[slot.name]
        filled.append(
            Fill(slot=slot.name, role=slot.role, kind=material.kind, source=material.path, box=box)
        )
    return {
        "template": template.name,
        "ratio": ratio,
        "fills": filled,
        "unfilled": unfilled,
        "filled": len(filled),
    }


def _pixels(box: Sequence[float], size: Tuple[int, int]) -> Tuple[int, int, int, int]:
    width, height = size
    x = int(round(float(box[0]) * width))
    y = int(round(float(box[1]) * height))
    w = max(2, int(round(float(box[2]) * width)))
    h = max(2, int(round(float(box[3]) * height)))
    return x, y, min(w, width - x), min(h, height - y)


def render(
    result: Mapping[str, Any],
    *,
    size: Tuple[int, int],
    out: Path,
    background: str = "#101010",
) -> Path:
    """Place every image, logo and colour fill into its box, on one frame."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    inputs: List[str] = [
        "-f", "lavfi",
        "-i", f"color=c={background}:s={int(size[0])}x{int(size[1])}:d=1:r=1",
    ]
    graph: List[str] = []
    current = "[0:v]"
    index = 1
    for item in result.get("fills") or []:
        fill = item if isinstance(item, Fill) else Fill(**item)
        x, y, w, h = _pixels(fill.box, size)
        if fill.kind in ("image", "logo", "video"):
            if fill.kind == "video":
                raise SlotError("SLOT_RENDER", f"{fill.slot}: video fills need the timeline renderer")
            inputs.extend(["-i", str(fill.source)])
            graph.append(f"[{index}:v]scale={w}:{h}:force_original_aspect_ratio=decrease[src{index}]")
            graph.append(f"{current}[src{index}]overlay={x}:{y}[v{index}]")
            current = f"[v{index}]"
            index += 1
        elif fill.kind == "color":
            graph.append(f"{current}drawbox=x={x}:y={y}:w={w}:h={h}:color={fill.source}:t=fill[v{index}]")
            current = f"[v{index}]"
            index += 1
        else:
            continue
    if not graph:
        raise SlotError("SLOT_RENDER", "no fill can reach a frame (text fills need a text renderer)")
    graph.append(f"{current}format=rgb24[vout]")
    command = [
        ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
        *inputs,
        "-filter_complex", ";".join(graph),
        "-map", "[vout]", "-frames:v", "1", str(out),
    ]
    proc = subprocess.run(command, capture_output=True)
    if proc.returncode != 0 or not out.is_file():
        raise SlotError("SLOT_RENDER", proc.stderr.decode("utf-8", "replace")[-300:])
    return out


__all__ = [
    "COMPATIBLE",
    "Fill",
    "MATERIAL_KINDS",
    "Material",
    "MaterialMap",
    "SlotError",
    "fill",
    "render",
]
