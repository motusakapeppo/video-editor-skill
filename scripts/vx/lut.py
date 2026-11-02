"""vx.lut — la libreria delle palette come dati: categoria, provenienza, licenza.

Una LUT 3D non e' un filtro da inventare a ogni progetto: e' un artefatto con un
autore, una licenza e una fonte. Qui ce ne sono **cinquanta**, divise per
categoria, e ognuna dichiara la sua ricetta. Le ricette sono nostre: i file
``.cube`` non vengono scaricati da nessuna parte, sono *generati* da
:func:`write_cube` a partire dai parametri dichiarati nel registro. La licenza
lo dice: MIT, autore ``vx.lut generator``, fonte ``ricetta v1``. Nessun nome di
pellicola di terzi, nessuna attribuzione inventata.

Tre regole che il codice impone:

* una LUT **assente dal disco** non e' un errore non gestito: si applica il
  ripiego ``eq`` + ``colorbalance`` derivato dalla stessa ricetta, e il referto
  porta ``fallback=True`` con il motivo e la fedelta' dichiarata ``approx``;
* ``protect_skin`` non stimola l'occhio: converte la toppa in CIE Lab, misura il
  delta E 76 prima/dopo, e se supera la tolleranza **abbassa la forza a passi di
  0,05** finche' rientra o tocca zero; la tolleranza e' dichiarata provvisoria,
  perche' il corpus volti di F6 non e' ancora misurato;
* l'ordine dei passi della ricetta e' fisso e documentato (temperatura, lift,
  contrasto, gamma, saturazione): due giri danno lo stesso file, byte per byte.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .contract import VxError

# --------------------------------------------------------------------------- #
# domini: ogni parametro dichiara dove puo' stare
# --------------------------------------------------------------------------- #
TEMP = (-1.0, 1.0)
TINT = (-1.0, 1.0)
CONTRAST = (0.80, 1.40)
SATURATION = (0.00, 1.60)
LIFT = (0.0, 0.12)
GAMMA = (0.80, 1.25)

CUBE_SIZE = 17
STRENGTH_STEP = 0.05

#: Oltre questo delta E 76 una pelle cambia famiglia di colore. **Provvisorio**:
#: il corpus volti di F6 non e' ancora misurato, quindi il numero vale come
#: limite di guardia dichiarato, non come calibrazione.
SKIN_TOLERANCE = 6.0
SKIN_TOLERANCE_STATUS = "provisional"
SKIN_TOLERANCE_NOTE = (
    "provvisorio: da calibrare sul corpus volti di F6, non ancora misurato; "
    "qui e' un limite di guardia dichiarato, non una calibrazione"
)

LICENSE = "MIT"
AUTHOR = "vx.lut generator"
SOURCE = "ricetta v1, sintesi deterministica in vx/lut.py"


class LutError(VxError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Attribution:
    license: str
    author: str
    source: str

    def problems(self) -> List[str]:
        found: List[str] = []
        for field in ("license", "author", "source"):
            if not str(getattr(self, field) or "").strip():
                found.append(f"attribution.{field} is empty")
        return found

    def as_dict(self) -> Dict[str, str]:
        return {"license": self.license, "author": self.author, "source": self.source}


@dataclass(frozen=True)
class Recipe:
    """I cinque passi del grado, nell'ordine in cui vengono applicati."""

    temp: float = 0.0
    tint: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    lift: float = 0.0
    gamma: float = 1.0

    def problems(self, name: str) -> List[str]:
        found: List[str] = []
        for field, (lo, hi) in (
            ("temp", TEMP),
            ("tint", TINT),
            ("contrast", CONTRAST),
            ("saturation", SATURATION),
            ("lift", LIFT),
            ("gamma", GAMMA),
        ):
            value = float(getattr(self, field))
            if not (lo <= value <= hi):
                found.append(f"{name}.{field} = {value} outside [{lo}, {hi}]")
        return found


@dataclass(frozen=True)
class Palette:
    name: str
    category: str
    recipe: Recipe
    attribution: Attribution = Attribution(LICENSE, AUTHOR, SOURCE)

    def filename(self) -> str:
        return f"{self.name}.cube"


CATEGORIES = (
    "cinema",
    "editoriale",
    "vintage",
    "log",
    "social",
    "noir",
    "natura",
)

#: (nome, categoria, temp, tint, contrasto, saturazione, lift, gamma)
_TABLE: Tuple[Tuple[str, str, float, float, float, float, float, float], ...] = (
    # cinema: contrasto fermo, tinta fredda o calda ma sempre dentro il colore
    ("cinema_teal_orange", "cinema", -0.35, 0.05, 1.18, 1.10, 0.02, 1.00),
    ("cinema_nebbia", "cinema", -0.20, -0.10, 0.94, 0.82, 0.06, 1.05),
    ("cinema_ambra", "cinema", 0.45, 0.10, 1.10, 0.95, 0.03, 0.98),
    ("cinema_acciaio", "cinema", -0.55, 0.00, 1.22, 0.72, 0.00, 0.96),
    ("cinema_deserto", "cinema", 0.60, 0.15, 1.06, 0.88, 0.05, 1.02),
    ("cinema_foresta", "cinema", -0.10, -0.20, 1.14, 0.90, 0.02, 0.94),
    ("cinema_notte_ciano", "cinema", -0.70, 0.05, 1.20, 0.80, 0.01, 0.90),
    ("cinema_sale", "cinema", 0.10, 0.00, 0.90, 0.75, 0.09, 1.08),
    ("cinema_ruggine", "cinema", 0.55, -0.05, 1.12, 1.00, 0.02, 1.00),
    ("cinema_ghiaccio", "cinema", -0.65, -0.05, 1.08, 0.70, 0.04, 1.06),
    # editoriale: piu' luce, contrasto basso, la pagina deve respirare
    ("edit_bianco", "editoriale", 0.05, 0.00, 0.88, 0.85, 0.10, 1.10),
    ("edit_carta", "editoriale", 0.15, 0.05, 0.92, 0.80, 0.08, 1.06),
    ("edit_pulito", "editoriale", 0.00, 0.00, 1.00, 1.00, 0.00, 1.00),
    ("edit_aria", "editoriale", -0.10, 0.00, 0.86, 0.78, 0.11, 1.12),
    ("edit_pietra", "editoriale", -0.05, -0.05, 0.95, 0.72, 0.06, 1.04),
    ("edit_sole_basso", "editoriale", 0.40, 0.08, 0.98, 0.92, 0.05, 1.02),
    ("edit_ombra_lunga", "editoriale", -0.15, 0.00, 1.05, 0.86, 0.03, 0.98),
    ("edit_latte", "editoriale", 0.20, 0.02, 0.84, 0.70, 0.12, 1.14),
    # vintage: il nero non e' nero, il colore e' scivolato
    ("vintage_cromo", "vintage", 0.25, -0.10, 0.92, 0.65, 0.10, 1.08),
    ("vintage_sbiadito", "vintage", 0.10, 0.00, 0.82, 0.55, 0.12, 1.12),
    ("vintage_marrone", "vintage", 0.50, 0.10, 0.95, 0.70, 0.09, 1.04),
    ("vintage_ottanta", "vintage", -0.15, 0.20, 1.02, 1.25, 0.07, 0.98),
    ("vintage_settanta", "vintage", 0.35, -0.15, 0.90, 0.60, 0.11, 1.10),
    ("vintage_kodak_via", "vintage", 0.30, 0.05, 1.06, 0.95, 0.06, 1.00),
    ("vintage_istante", "vintage", 0.45, 0.00, 0.88, 0.75, 0.12, 1.06),
    ("vintage_polvere", "vintage", 0.20, -0.05, 0.86, 0.58, 0.10, 1.14),
    ("vintage_ambra_scura", "vintage", 0.55, 0.08, 1.00, 0.68, 0.08, 0.96),
    ("vintage_lavaggio", "vintage", -0.05, 0.05, 0.80, 0.50, 0.12, 1.18),
    # log: piatti di proposito, il contrasto lo mette il montaggio
    ("log_cine_a", "log", -0.10, 0.00, 0.84, 0.62, 0.08, 1.10),
    ("log_cine_b", "log", 0.10, 0.00, 0.82, 0.66, 0.09, 1.12),
    ("log_piatto", "log", 0.00, 0.00, 0.80, 0.60, 0.10, 1.15),
    ("log_verde", "log", -0.05, -0.25, 0.86, 0.70, 0.07, 1.08),
    ("log_caldo", "log", 0.20, 0.05, 0.88, 0.72, 0.06, 1.06),
    ("log_freddo", "log", -0.30, 0.00, 0.85, 0.64, 0.08, 1.09),
    # social: saturazione alta ma non tossica, neri chiusi
    ("social_pop", "social", 0.05, 0.05, 1.12, 1.32, 0.02, 1.00),
    ("social_estate", "social", 0.40, 0.12, 1.08, 1.25, 0.03, 1.02),
    ("social_gelido", "social", -0.45, 0.00, 1.15, 1.20, 0.01, 0.98),
    ("social_cielo", "social", -0.25, -0.08, 1.05, 1.18, 0.04, 1.04),
    ("social_terra", "social", 0.50, -0.10, 1.10, 1.15, 0.03, 0.99),
    ("social_neon", "social", -0.35, 0.25, 1.22, 1.40, 0.00, 0.96),
    ("social_morbido", "social", 0.15, 0.02, 0.96, 1.10, 0.06, 1.06),
    ("social_contrasto", "social", 0.00, 0.00, 1.30, 1.08, 0.00, 0.94),
    # noir: neri fondi, colore quasi assente, un solo canale che resta
    ("noir_bianco_nero", "noir", 0.00, 0.00, 1.32, 0.00, 0.00, 0.92),
    ("noir_seppia", "noir", 0.55, 0.10, 1.25, 0.25, 0.02, 0.95),
    ("noir_blu", "noir", -0.60, 0.00, 1.28, 0.35, 0.00, 0.90),
    ("noir_ferro", "noir", -0.20, 0.00, 1.35, 0.20, 0.00, 0.88),
    # natura: verdi e azzurri tenuti separati, mai fusi in un unico verde
    ("natura_bosco", "natura", -0.08, -0.18, 1.04, 0.95, 0.04, 1.02),
    ("natura_prato", "natura", 0.08, -0.22, 0.98, 1.05, 0.05, 1.04),
    ("natura_mare", "natura", -0.40, -0.05, 1.10, 1.00, 0.02, 0.98),
    ("natura_sabbia", "natura", 0.35, 0.10, 0.94, 0.88, 0.08, 1.06),
    ("natura_pioggia", "natura", -0.30, -0.12, 0.92, 0.78, 0.07, 1.08),
)

PALETTES: Dict[str, Palette] = {
    name: Palette(name=name, category=category, recipe=Recipe(temp, tint, contrast, saturation, lift, gamma))
    for name, category, temp, tint, contrast, saturation, lift, gamma in _TABLE
}

FAMILY = tuple(sorted(PALETTES))
MIN_PALETTES = 50


# --------------------------------------------------------------------------- #
# la ricetta: cinque passi, ordine fisso
# --------------------------------------------------------------------------- #
def _clamp(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def transform(rgb: Sequence[float], recipe: Recipe, strength: float = 1.0) -> Tuple[float, float, float]:
    """Grado di un colore secondo la ricetta, mescolato con l'identita' da ``strength``.

    Ordine: guadagni di temperatura/tinta, lift dei neri, contrasto attorno a 0,5,
    gamma, saturazione rispetto alla luma Rec.709. Fuori da ``[0,1]`` si taglia:
    un ``.cube`` non sa rappresentare l'overrange, e fingere di si' sarebbe peggio.
    """
    red, green, blue = (float(rgb[0]), float(rgb[1]), float(rgb[2]))
    warm = 0.12 * recipe.temp
    green_shift = 0.04 * recipe.tint

    red = red * (1.0 + warm + green_shift)
    green = green * (1.0 - 0.02 * recipe.tint)
    blue = blue * (1.0 - warm + green_shift)

    if recipe.lift > 0.0:
        red = recipe.lift + red * (1.0 - recipe.lift)
        green = recipe.lift + green * (1.0 - recipe.lift)
        blue = recipe.lift + blue * (1.0 - recipe.lift)

    red = (red - 0.5) * recipe.contrast + 0.5
    green = (green - 0.5) * recipe.contrast + 0.5
    blue = (blue - 0.5) * recipe.contrast + 0.5

    red = _clamp(red) ** recipe.gamma
    green = _clamp(green) ** recipe.gamma
    blue = _clamp(blue) ** recipe.gamma

    luma = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    red = luma + (red - luma) * recipe.saturation
    green = luma + (green - luma) * recipe.saturation
    blue = luma + (blue - luma) * recipe.saturation

    graded = (_clamp(red), _clamp(green), _clamp(blue))
    if strength >= 1.0:
        return graded
    weight = max(0.0, min(1.0, float(strength)))
    original = (_clamp(float(rgb[0])), _clamp(float(rgb[1])), _clamp(float(rgb[2])))
    return tuple(
        original[i] * (1.0 - weight) + graded[i] * weight for i in range(3)
    )  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# il file .cube: 17^3, r che gira piu' veloce (spec Iridas)
# --------------------------------------------------------------------------- #
def write_cube(name: str, destination: Path) -> Path:
    palette = require(name)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(palette_cube_text(palette), encoding="utf-8", newline="\n")
    return destination


def palette_cube_text(palette: Palette) -> str:
    lines = [
        f"# {palette.name} — {palette.category}",
        f"# license: {palette.attribution.license}",
        f"# author: {palette.attribution.author}",
        f"# source: {palette.attribution.source}",
        "TITLE \"{}\"".format(palette.name),
        f"LUT_3D_SIZE {CUBE_SIZE}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]
    step = 1.0 / (CUBE_SIZE - 1)
    recipe = palette.recipe
    for blue_index in range(CUBE_SIZE):
        blue = blue_index * step
        for green_index in range(CUBE_SIZE):
            green = green_index * step
            for red_index in range(CUBE_SIZE):
                red = red_index * step
                out = transform((red, green, blue), recipe)
                lines.append(f"{out[0]:.6f} {out[1]:.6f} {out[2]:.6f}")
    return "\n".join(lines) + "\n"


def ensure(name: str, cache_dir: Path) -> Path:
    """Il file della palette, scritto se manca. Rigenerarlo da' gli stessi byte."""
    palette = require(name)
    target = Path(cache_dir) / palette.filename()
    if not target.is_file() or target.stat().st_size == 0:
        write_cube(name, target)
    return target


# --------------------------------------------------------------------------- #
# ripiego dichiarato: eq + colorbalance dalla stessa ricetta
# --------------------------------------------------------------------------- #
def fallback_filters(recipe: Recipe, strength: float = 1.0) -> List[str]:
    """La stessa ricetta in due filtri ffmpeg, dichiarati approssimati.

    ``eq`` prende luminosita', contrasto, saturazione e gamma; ``colorbalance``
    prende la temperatura come bilanciamento fra ombre e alte luci. Non e' la
    LUT: e' la sua ombra, e il referto lo dice. Una forza parziale avvicina i
    parametri all'identita' invece di essere ignorata.
    """
    weight = max(0.0, min(1.0, float(strength)))
    recipe = Recipe(
        temp=recipe.temp * weight,
        tint=recipe.tint * weight,
        contrast=1.0 + (recipe.contrast - 1.0) * weight,
        saturation=1.0 + (recipe.saturation - 1.0) * weight,
        lift=recipe.lift * weight,
        gamma=1.0 + (recipe.gamma - 1.0) * weight,
    )
    brightness = 0.06 * recipe.lift - 0.04 * (recipe.contrast - 1.0)
    gamma = max(0.6, min(1.6, 1.0 / max(0.4, recipe.gamma)))
    eq = (
        "eq=brightness={:.4f}:contrast={:.4f}:saturation={:.4f}:gamma={:.4f}".format(
            brightness, recipe.contrast, recipe.saturation, gamma
        )
    )
    warm = 0.12 * recipe.temp
    green_shift = 0.04 * recipe.tint
    shadows_r = max(-0.5, min(0.5, warm))
    shadows_b = max(-0.5, min(0.5, -warm))
    highs_r = max(-0.5, min(0.5, warm * 0.6 + green_shift))
    highs_g = max(-0.5, min(0.5, green_shift * 0.5))
    highs_b = max(-0.5, min(0.5, -warm * 0.6 + green_shift))
    balance = (
        "colorbalance=rs={:.4f}:bs={:.4f}:gm={:.4f}:rh={:.4f}:gh={:.4f}:bh={:.4f}".format(
            shadows_r, shadows_b, green_shift * 0.5, highs_r, highs_g, highs_b
        )
    )
    return [eq, balance]


def grade(name: str, *, cache_dir: Path, strength: float = 1.0) -> Dict[str, object]:
    """Il piano di grado di una palette: LUT reale o ripiego dichiarato."""
    palette = require(name)
    weight = max(0.0, min(1.0, float(strength)))
    target = Path(cache_dir) / palette.filename()
    if target.is_file() and target.stat().st_size > 0:
        if weight < 1.0:
            # ``lut3d`` ha un solo ingresso: non sa mescolare con l'originale,
            # quindi una forza parziale usa il ripiego approssimato invece di
            # fingere una miscela che nessun filtro sta facendo.
            return {
                "palette": palette.name,
                "category": palette.category,
                "cube": str(target),
                "filters": fallback_filters(palette.recipe, weight),
                "fallback": True,
                "why": (
                    f"forza {weight:.2f} < 1.0: lut3d non sa mescolare con "
                    "l'originale in un solo ingresso, si usa il ripiego "
                    "approssimato finche' la forza non torna 1.0"
                ),
                "fidelity": "approx",
                "strength": weight,
                "attribution": palette.attribution.as_dict(),
            }
        return {
            "palette": palette.name,
            "category": palette.category,
            "cube": str(target),
            "filters": [f"lut3d=file={_escape(target)}:interp=tetrahedral"],
            "fallback": False,
            "why": "",
            "fidelity": "lut3d",
            "strength": weight,
            "attribution": palette.attribution.as_dict(),
        }
    return {
        "palette": palette.name,
        "category": palette.category,
        "cube": "",
        "filters": fallback_filters(palette.recipe, weight),
        "fallback": True,
        "why": f"la LUT non e' sul disco ({target}); si applica eq + colorbalance",
        "fidelity": "approx",
        "strength": weight,
        "attribution": palette.attribution.as_dict(),
    }


def _escape(path: Path) -> str:
    # ffmpeg vuole i due punti di C: protetti dentro il valore del filtro
    text = str(Path(path).resolve()).replace("\\", "/")
    return "'" + text.replace(":", "\\:") + "'"


def filter_string(plan: Dict[str, object]) -> str:
    filters = list(plan.get("filters") or [])
    if not filters:
        raise LutError("LUT_EMPTY", "the grade plan carries no filter")
    return ",".join(str(item) for item in filters)


# --------------------------------------------------------------------------- #
# pelle: delta E 76 misurato, non stimato
# --------------------------------------------------------------------------- #
def _to_lab(rgb: Sequence[float]) -> Tuple[float, float, float]:
    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(_clamp(float(c))) for c in rgb)
    x = (0.4124 * red + 0.3576 * green + 0.1805 * blue) / 0.95047
    y = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    z = (0.0193 * red + 0.1192 * green + 0.9505 * blue) / 1.08883

    def f(t: float) -> float:
        return t ** (1.0 / 3.0) if t > 0.008856 else 7.787 * t + 16.0 / 116.0

    fx, fy, fz = f(x), f(y), f(z)
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


def delta_e(first: Sequence[float], second: Sequence[float]) -> float:
    lab_a, lab_b = _to_lab(first), _to_lab(second)
    return math.sqrt(sum((lab_a[i] - lab_b[i]) ** 2 for i in range(3)))


def protect_skin(
    name: str,
    patch: Sequence[float],
    *,
    tolerance: float = SKIN_TOLERANCE,
    strength: float = 1.0,
) -> Dict[str, object]:
    """Misura il delta E sulla toppa e abbassa la forza finche' rientra.

    ``patch`` e' un colore sRGB in 0..1, tipicamente la media di una toppa presa
    da un fotogramma. Il risultato porta sempre il delta E misurato a forza 1.0,
    quello alla forza applicata, la tolleranza e il suo stato.
    """
    palette = require(name)
    patch = tuple(_clamp(float(c)) for c in patch)
    if len(patch) != 3:
        raise LutError("LUT_PATCH", f"the skin patch needs three channels, got {len(patch)}")
    if tolerance <= 0.0:
        raise LutError("LUT_TOLERANCE", f"tolerance must be > 0, got {tolerance!r}")

    full = delta_e(patch, transform(patch, palette.recipe, 1.0))
    applied = max(0.0, min(1.0, float(strength)))
    measured = delta_e(patch, transform(patch, palette.recipe, applied))
    steps = 0
    while measured > tolerance and applied > 0.0:
        applied = round(max(0.0, applied - STRENGTH_STEP), 4)
        measured = delta_e(patch, transform(patch, palette.recipe, applied))
        steps += 1

    return {
        "palette": palette.name,
        "patch": [round(c, 6) for c in patch],
        "delta_e_full": round(full, 4),
        "delta_e": round(measured, 4),
        "tolerance": float(tolerance),
        "tolerance_status": SKIN_TOLERANCE_STATUS,
        "tolerance_note": SKIN_TOLERANCE_NOTE,
        "strength_requested": round(max(0.0, min(1.0, float(strength))), 4),
        "strength": applied,
        "steps": steps,
        "inside": measured <= tolerance,
    }


# --------------------------------------------------------------------------- #
# registro
# --------------------------------------------------------------------------- #
def require(name: str) -> Palette:
    palette = PALETTES.get(str(name))
    if palette is None:
        raise LutError(
            "LUT_UNKNOWN",
            f"unknown palette {name!r}; {len(PALETTES)} declared, e.g. {list(FAMILY[:3])}",
        )
    return palette


def validate() -> List[str]:
    found: List[str] = []
    seen: Dict[str, str] = {}
    for name, palette in PALETTES.items():
        if name != palette.name:
            found.append(f"{name}: key and name disagree")
        if palette.category not in CATEGORIES:
            found.append(f"{name}: unknown category {palette.category!r}")
        found.extend(f"{name}: {p}" for p in palette.attribution.problems())
        found.extend(palette.recipe.problems(name))
        if name in seen:
            found.append(f"{name}: declared twice")
        seen[name] = palette.category
    if len(PALETTES) < MIN_PALETTES:
        found.append(f"{len(PALETTES)} palettes, the library declares at least {MIN_PALETTES}")
    return found


def by_category() -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = {category: [] for category in CATEGORIES}
    for name in FAMILY:
        grouped[PALETTES[name].category].append(name)
    return grouped


def describe(name: str) -> Dict[str, object]:
    palette = require(name)
    return {
        "name": palette.name,
        "category": palette.category,
        "recipe": {
            "temp": palette.recipe.temp,
            "tint": palette.recipe.tint,
            "contrast": palette.recipe.contrast,
            "saturation": palette.recipe.saturation,
            "lift": palette.recipe.lift,
            "gamma": palette.recipe.gamma,
        },
        "attribution": palette.attribution.as_dict(),
        "file": palette.filename(),
    }


def count() -> int:
    return len(PALETTES)


__all__ = [
    "AUTHOR",
    "Attribution",
    "CATEGORIES",
    "CUBE_SIZE",
    "FAMILY",
    "LICENSE",
    "LutError",
    "MIN_PALETTES",
    "PALETTES",
    "Palette",
    "Recipe",
    "SKIN_TOLERANCE",
    "SKIN_TOLERANCE_NOTE",
    "SKIN_TOLERANCE_STATUS",
    "SOURCE",
    "by_category",
    "count",
    "delta_e",
    "describe",
    "ensure",
    "fallback_filters",
    "filter_string",
    "grade",
    "palette_cube_text",
    "protect_skin",
    "require",
    "transform",
    "validate",
    "write_cube",
]
