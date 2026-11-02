"""vx.multiratio — un progetto, piu' proporzioni, e una cache che si condivide.

Da un progetto escono tre formati. Ogni proporzione chiede il suo layout:

* se il template ha quel layout, si rende;
* se non ce l'ha, lo si **dichiara** (``layout_missing``, con il ripiego
  proposto) e non si ritaglia in silenzio: un 16:9 schiacciato in 9:16 non e' la
  stessa scena, e' un'altra scena senza permesso;
* i materiali si preparano una volta sola: la cache e' chiavata sullo sha256
  dell'asset e vive fra le proporzioni, quindi il secondo e il terzo formato
  riusano il primo invece di ripagarlo.

:func:`render_all` conta gli stadi eseguiti e i riusi, cosi' il test puo'
verificare il riuso invece di sperarlo.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import slotfill, templates
from .contract import VxError

DEFAULT_RATIOS = ("16:9", "9:16", "1:1")


class RatioError(VxError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass
class SharedCache:
    """Prepared assets, keyed by content: the same file is prepared once."""

    root: Optional[Path] = None
    stages: int = 0
    hits: int = 0
    _memory: Dict[str, Path] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.root is not None:
            self.root = Path(self.root)
            self.root.mkdir(parents=True, exist_ok=True)

    def prepare(self, asset: Path) -> Path:
        asset = Path(asset)
        key = _sha256(asset)
        if key in self._memory:
            self.hits += 1
            return self._memory[key]
        if self.root is not None:
            target = self.root / f"{key}{asset.suffix.lower()}"
            if target.is_file():
                self.hits += 1
                self._memory[key] = target
                return target
        else:
            target = asset
        self.stages += 1
        if self.root is not None:
            shutil.copyfile(asset, target)
        self._memory[key] = target
        return target

    def stats(self) -> Dict[str, int]:
        return {"stages": self.stages, "hits": self.hits}


def render_all(
    template: templates.Template,
    materials: Mapping[str, slotfill.Material],
    *,
    ratios: Sequence[str] = DEFAULT_RATIOS,
    sizes: Optional[Mapping[str, Tuple[int, int]]] = None,
    out_dir: Path,
    cache: Optional[SharedCache] = None,
    background: str = "#101010",
) -> Dict[str, Any]:
    out_dir = Path(out_dir)
    cache = cache or SharedCache(out_dir / "cache")
    sizes = dict(sizes or {})
    frames: List[Dict[str, Any]] = []
    degradations: List[Dict[str, Any]] = []
    for ratio in ratios:
        size = sizes.get(ratio, (1280, 720) if ratio == "16:9" else (720, 1280) if ratio == "9:16" else (900, 900))
        if ratio not in template.layouts:
            degradations.append(
                {
                    "ratio": ratio,
                    "code": "layout_missing",
                    "template": template.name,
                    "declared": sorted(template.layouts),
                    "fallback": "pillarbox dall'orizzontale, da approvare",
                    "frame": None,
                }
            )
            continue
        result = slotfill.fill(template, materials, ratio=ratio)
        prepared = []
        for item in result["fills"]:
            fill = item if isinstance(item, slotfill.Fill) else slotfill.Fill(**item)
            if fill.kind in ("image", "logo"):
                prepared.append(
                    slotfill.Fill(
                        slot=fill.slot,
                        role=fill.role,
                        kind=fill.kind,
                        source=str(cache.prepare(Path(fill.source))),
                        box=fill.box,
                    )
                )
            else:
                prepared.append(fill)
        frame = out_dir / f"{template.name}-{ratio.replace(':', 'x')}.png"
        try:
            slotfill.render(
                {"fills": prepared},
                size=(int(size[0]), int(size[1])),
                out=frame,
                background=background,
            )
        except slotfill.SlotError as exc:
            if exc.code != "SLOT_RENDER":
                raise
            frame = None
        frames.append(
            {
                "ratio": ratio,
                "size": [int(size[0]), int(size[1])],
                "frame": str(frame) if frame else None,
                "filled": result["filled"],
                "unfilled": result["unfilled"],
            }
        )
    return {
        "template": template.name,
        "ratios": list(ratios),
        "frames": frames,
        "degradations": degradations,
        "cache": cache.stats(),
    }


__all__ = [
    "DEFAULT_RATIOS",
    "RatioError",
    "SharedCache",
    "render_all",
]
