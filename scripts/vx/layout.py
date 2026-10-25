"""vx.layout — gerarchia spaziale della delivery: zone, box dichiarati, collisioni.

Perche' esiste
--------------
Il master di Fase 3 aveva i sottotitoli ancorati a una `MarginV` fissa (150 px su
1440) mentre la label "MOTUS" occupa la fascia 1236-1296. Risultato misurato sul
frame a 0:00: 56 px di sovrapposizione garantita, testo che scorreva sotto la
wordmark. Nessuna costante scelta a mano puo' sopravvivere a un cambio di asset:
appena la label si sposta di 20 px il numero diventa sbagliato in silenzio.

Quindi la geometria si **misura** dal PNG, non si dichiara:

1. si apre l'overlay e si prende il canale alpha;
2. si estraggono le componenti connesse (le lettere sono componenti separate);
3. si tengono le componenti il cui centroide cade nella fascia inferiore
   (`GRAPHIC_SPLIT_Y`): sono quelle che possiedono il quadrante basso. La cornice
   perimetrale ha centroide a 0.50 e viene quindi esclusa — e' una cornice, non
   una fascia;
4. l'unione delle componenti tenute e' l'ingombro grafico `graphic`;
5. il testo ha come limite inferiore `text_limit_y = graphic.y - padding`.

Da li' esce la `MarginV` che l'ASS deve usare, e il box che il testo *dichiarera'*
di occupare. Il cancello 4 confronta i due box **prima** di passare il grafo a
ffmpeg: se si intersecano il rendering non parte (exit 6).

Coordinate: sempre pixel del canvas di uscita (1080x1440 nel delivery corrente).
Nessuna normalizzazione implicita: chi chiama passa `out_w`/`out_h`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "Rect",
    "Layout",
    "measure_components",
    "build",
    "font_size_for",
    "DEFAULT_SPLIT_Y",
    "DEFAULT_PADDING_RATIO",
]

# Una componente il cui centroide sta sotto questa frazione dell'altezza
# appartiene alla fascia grafica inferiore. 0.70 lascia fuori la cornice
# perimetrale (centroide 0.50) e prende label + triangolo (0.88 / 0.95).
DEFAULT_SPLIT_Y = 0.70

# Distacco minimo fra il bordo inferiore del testo e il bordo superiore della
# grafica, in frazione dell'altezza del canvas (2% di 1440 = 29 px).
DEFAULT_PADDING_RATIO = 0.02

# Margine laterale del blocco testo, in frazione della larghezza (5.6% di 1080 =
# 60 px). Serve a tenere il testo dentro la cornice, che sta a 20 px dal bordo.
DEFAULT_MARGIN_LR_RATIO = 0.055

# Interlinea di libass per Arial Black: misurata, non assunta. `fs` * 1.20 e'
# l'altezza di riga che libass riserva; il box dichiarato usa lo stesso valore
# additivamente per outline*2, cosi' il box e' sempre >= dell'inchiostro reale.
LINE_HEIGHT = 1.20


@dataclass(frozen=True)
class Rect:
    """Box ascii (x, y, w, h) in output pixels; origine in alto a sinistra."""

    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return max(0, self.w) * max(0, self.h)

    def intersects(self, other: "Rect") -> bool:
        """AABB overlap. Bordi che si toccano non contano come sovrapposizione."""
        return (
            self.x < other.right
            and other.x < self.right
            and self.y < other.bottom
            and other.y < self.bottom
        )

    def intersection(self, other: "Rect") -> Optional["Rect"]:
        if not self.intersects(other):
            return None
        x1, y1 = max(self.x, other.x), max(self.y, other.y)
        x2, y2 = min(self.right, other.right), min(self.bottom, other.bottom)
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def contains(self, other: "Rect") -> bool:
        return (
            other.x >= self.x
            and other.y >= self.y
            and other.right <= self.right
            and other.bottom <= self.bottom
        )

    def to_dict(self) -> Dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass
class Layout:
    """Risultato della misura: zone allocate e limiti che il testo deve rispettare."""

    canvas: Rect
    graphic: Optional[Rect]
    components: List[Dict[str, Any]] = field(default_factory=list)
    padding: int = 0
    margin_lr: int = 0
    declared: int = 0
    measured: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def text_limit_y(self) -> int:
        """Y massima (esclusa) a cui puo' arrivare il bordo inferiore del testo."""
        base = self.graphic.y if self.graphic is not None else self.canvas.bottom
        return base - self.padding

    @property
    def margin_v(self) -> int:
        """MarginV ASS che porta il bordo inferiore del blocco esattamente al limite."""
        return max(0, self.canvas.bottom - self.text_limit_y)

    @property
    def text_width_limit(self) -> int:
        return max(64, self.canvas.w - 2 * self.margin_lr)

    def text_box(
        self,
        *,
        lines: int,
        font_px: int,
        line_px: int,
        outline_px: int = 2,
    ) -> Rect:
        """Box che il blocco testo *dichiara* di occupare con Alignment=2.

        `line_px` e' la larghezza della riga piu' lunga in pixel: il testo e'
        centrato, quindi il box e' centrato. La larghezza **non** viene troncata
        al limite: se il testo e' piu' largo del canvas il box deve dichiararlo,
        cosi' il cancello lo rifiuta invece di nasconderlo con un clip.
        """
        height = int(round(lines * font_px * LINE_HEIGHT + 2 * outline_px))
        bottom = self.canvas.bottom - self.margin_v
        top = bottom - height
        width = int(line_px)
        left = (self.canvas.w - width) // 2
        return Rect(left, top, width, height)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "canvas": self.canvas.to_dict(),
            "graphic": self.graphic.to_dict() if self.graphic else None,
            "text_limit_y": self.text_limit_y,
            "margin_v": self.margin_v,
            "margin_lr": self.margin_lr,
            "padding": self.padding,
            "text_width_limit": self.text_width_limit,
            "components": self.components,
            "declared_overlays": self.declared,
            "measured_overlays": self.measured,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- #
# misura degli asset
# --------------------------------------------------------------------------- #


def measure_components(png: str | Path, *, alpha_min: int = 8) -> List[Rect]:
    """Componenti connesse del canale alpha di un PNG, come Rect in pixel.

    Ritorna lista vuota (con nessuna eccezione) se il file manca o OpenCV non
    c'e': il chiamante tratta l'assenza come "nessun ingombro", non come errore.
    """
    return [rect for rect, _ratio in _measured(png, alpha_min=alpha_min, split_px=None)]


def _measured(
    png: str | Path, *, alpha_min: int = 8, split_px: Optional[int] = None
) -> List[Tuple[Rect, float]]:
    """Componenti con la frazione di inchiostro sotto `split_px`.

    La classificazione non puo' usare il centro del bbox: un arco, una U o un
    nastro verticale hanno l'inchiostro in basso ma il bbox che sale oltre la
    soglia, e verrebbero esclusi lasciando la loro fascia non riservata. Si conta
    invece dove sta l'inchiostro davvero: `ratio` = pixel sotto lo split / pixel
    totali della componente.
    """
    path = Path(png)
    if not path.is_file():
        return []
    try:
        import cv2  # noqa: PLC0415 - opzionale, come in autoframe
        import numpy as np  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None or img.ndim != 3 or img.shape[2] != 4:
        return []
    mask = (img[:, :, 3] > int(alpha_min)).astype("uint8")
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    out: List[Tuple[Rect, float]] = []
    for i in range(1, count):  # 0 e' il fondo
        x, y, w, h, area = (int(v) for v in stats[i][:5])
        if split_px is None:
            ratio = 1.0 if y + h / 2.0 >= 0 else 0.0
        else:
            rows = np.where(labels == i)[0]
            ratio = float((rows >= int(split_px)).mean()) if rows.size else 0.0
        out.append((Rect(x, y, w, h), ratio))
    return out


def _union(rects: Sequence[Rect]) -> Optional[Rect]:
    if not rects:
        return None
    x1 = min(r.x for r in rects)
    y1 = min(r.y for r in rects)
    x2 = max(r.right for r in rects)
    y2 = max(r.bottom for r in rects)
    return Rect(x1, y1, x2 - x1, y2 - y1)


def build(
    out_w: int,
    out_h: int,
    overlays: Mapping[str, str | Path] | Iterable[str | Path],
    *,
    split_y: float = DEFAULT_SPLIT_Y,
    padding_ratio: float = DEFAULT_PADDING_RATIO,
    margin_lr_ratio: float = DEFAULT_MARGIN_LR_RATIO,
) -> Layout:
    """Misura gli overlay e alloca la fascia inferiore alla grafica.

    `overlays` puo' essere un dict `nome -> path` o un iterabile di path: i nomi
    finiscono nel report, quindi passarli aiuta a leggere il gate.
    """
    out_w, out_h = int(out_w), int(out_h)
    named: Dict[str, str | Path] = (
        dict(overlays)
        if isinstance(overlays, Mapping)
        else {Path(p).stem: p for p in overlays}
    )

    layout = Layout(
        canvas=Rect(0, 0, out_w, out_h),
        graphic=None,
        padding=int(round(out_h * float(padding_ratio))),
        margin_lr=int(round(out_w * float(margin_lr_ratio))),
    )

    band: List[Rect] = []
    split_px = int(round(out_h * float(split_y)))
    layout.declared = len(named)
    for name, path in named.items():
        comps = _measured(path, split_px=split_px)
        if not comps:
            layout.notes.append(f"{name}: nessuna componente alpha misurabile")
            continue
        layout.measured += 1
        for rect, ratio in comps:
            owned = ratio >= 0.5
            layout.components.append(
                {
                    "overlay": name,
                    "rect": rect.to_dict(),
                    "ink_below_split": round(ratio, 4),
                    "in_bottom_band": owned,
                }
            )
            if owned:
                band.append(rect)

    layout.graphic = _union(band)
    if layout.graphic is None:
        layout.notes.append(
            "nessun overlay nella fascia inferiore: il testo usa il bordo canvas"
        )
    else:
        layout.notes.append(
            f"fascia grafica y {layout.graphic.y}-{layout.graphic.bottom} "
            f"({len(band)} componenti); limite testo y < {layout.text_limit_y}"
        )
    return layout


# --------------------------------------------------------------------------- #
# tipografia: quanti pixel puo' essere largo un carattere
# --------------------------------------------------------------------------- #


def font_size_for(
    longest_line: str | int,
    *,
    max_width_px: int,
    char_em: float,
    cap_px: int,
    floor_px: int = 24,
) -> int:
    """Font piu' grande che tiene la riga piu' lunga dentro `max_width_px`.

    `char_em` e' l'avanzamento medio per carattere in em (misurato: 0.50 per
    Arial Black maiuscolo, outline incluso). `cap_px` e' il tetto di design.
    """
    chars = longest_line if isinstance(longest_line, int) else len(longest_line)
    chars = max(1, int(chars))
    if not char_em or char_em <= 0:
        return max(floor_px, cap_px)
    fitted = int(max_width_px / (chars * float(char_em)))
    return max(int(floor_px), min(int(cap_px), fitted))
