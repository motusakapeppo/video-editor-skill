"""vx.camera — i movimenti di camera come dati, non come rami di codice.

Un movimento e' una **traiettoria di ritaglio**: quattro keyframe (x, y, width,
height) su una clip, resi dall'effetto ``crop`` che il compilatore sa gia'
animare. Nessun movimento e' un filtro nuovo, e nessuno e' un'euristica: ognuno
ha un nome, una famiglia, un innesco (quando un regista lo sceglierebbe), un
tetto di durata e un'ampiezza dichiarata.

Quattro regole, imposte qui e non nei commenti:

* una voce senza innesco, senza famiglia, senza parametri o con tetto nullo fa
  fallire :func:`validate` con il suo codice;
* un nome ignoto e' rifiutato con il nome nel messaggio;
* una pila di quattro movimenti sulla stessa clip e' rifiutata con
  ``STACK_TOO_DEEP``: tre sono gia' un effetto, quattro sono un giro di giostra;
* il costruttore emette un ``Effect`` dell'EDL 3, quindi la prova che il
  movimento si rende e' il compilatore, non questo modulo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .contract import VxError
from .edl3 import Effect, Keyframe, KeyframeTrack

#: Quattro movimenti in pila sono troppi: il tetto e' dichiarato e verificato.
MAX_STACK = 3


class CameraError(VxError):
    """A refused camera move, with a code a caller can branch on."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CameraMove:
    """One named movement: what it is, when to use it, how far it may go."""

    name: str
    family: str
    path: str
    trigger: str
    amplitude: float
    cap_seconds: float = 12.0
    #: Interpolation of the segment between control points.
    interp: str = "linear"

    def problems(self) -> List[str]:
        found: List[str] = []
        if not self.name.strip():
            found.append("name is empty")
        if not self.family.strip():
            found.append(f"{self.name}: family is empty")
        if not self.trigger.strip():
            found.append(f"{self.name}: trigger is empty")
        if not (0.0 < float(self.amplitude) <= 0.35):
            found.append(
                f"{self.name}: amplitude {self.amplitude} outside (0, 0.35]: beyond "
                "a third of the frame it is a punch-in, not a movement"
            )
        if float(self.cap_seconds) <= 0.0:
            found.append(f"{self.name}: cap_seconds must be > 0")
        if self.interp not in ("linear", "bezier", "hold"):
            found.append(f"{self.name}: unknown interpolation {self.interp!r}")
        return found


#: name -> (family, path, amplitude, cap_seconds, trigger, interp)
_TABLE: Sequence[Tuple[str, str, str, float, float, str]] = (
    ("push_in", "push", "push", 0.10, 12.0, "Avvicinati al soggetto mentre parla: attenzione senza taglio."),
    ("push_in_fast", "push", "push", 0.16, 4.0, "Spinta breve su un dettaglio, entro una battuta."),
    ("push_in_slow", "push", "push", 0.06, 30.0, "Spinta lunga su un'inquadratura che deve respirare."),
    ("push_in_center", "push", "push", 0.12, 12.0, "Spinta centrata, quando il soggetto e' gia' in mezzo."),
    ("pull_out", "pull", "pull", 0.10, 12.0, "Allontanati per rivelare il contesto dopo una frase."),
    ("pull_out_fast", "pull", "pull", 0.16, 4.0, "Rivelazione rapida: il contesto conta piu' del volto."),
    ("pull_out_slow", "pull", "pull", 0.06, 30.0, "Chiusura lenta: lascia lo spettatore con la scena."),
    ("pull_out_wide", "pull", "pull", 0.20, 8.0, "Allargamento deciso su un finale di blocco."),
    ("pan_left", "pan", "pan_left", 0.12, 8.0, "Segui un gesto o un oggetto che esce a sinistra."),
    ("pan_right", "pan", "pan_right", 0.12, 8.0, "Segui un gesto o un oggetto che esce a destra."),
    ("pan_left_slow", "pan", "pan_left", 0.07, 20.0, "Panoramica lenta su un ambiente."),
    ("pan_right_slow", "pan", "pan_right", 0.07, 20.0, "Panoramica lenta nella direzione opposta."),
    ("tilt_up", "tilt", "tilt_up", 0.10, 8.0, "Sali dal dettaglio al volto, o dal volto all'ambiente."),
    ("tilt_down", "tilt", "tilt_down", 0.10, 8.0, "Scendi dal volto alle mani o all'oggetto."),
    ("tilt_up_slow", "tilt", "tilt_up", 0.06, 20.0, "Salita lenta: introduce un luogo."),
    ("tilt_down_slow", "tilt", "tilt_down", 0.06, 20.0, "Discesa lenta: chiude su un dettaglio."),
    ("whip_left", "whip", "pan_left", 0.30, 2.0, "Frusta: cambia scena con l'energia di un taglio."),
    ("whip_right", "whip", "pan_right", 0.30, 2.0, "Frusta nella direzione opposta."),
    ("whip_up", "whip", "tilt_up", 0.28, 2.0, "Frusta verticale verso l'alto, su un beat."),
    ("whip_down", "whip", "tilt_down", 0.28, 2.0, "Frusta verticale verso il basso."),
    ("orbit_left", "orbit", "orbit_left", 0.10, 6.0, "Orbita finta: gira attorno al soggetto senza muovere la camera."),
    ("orbit_right", "orbit", "orbit_right", 0.10, 6.0, "Orbita finta nel verso opposto."),
    ("orbit_left_wide", "orbit", "orbit_left", 0.16, 6.0, "Orbita ampia: il contesto gira con il soggetto."),
    ("orbit_right_wide", "orbit", "orbit_right", 0.16, 6.0, "Orbita ampia nel verso opposto."),
    ("handheld_light", "handheld", "handheld", 0.02, 20.0, "Micro-movimento a mano: toglie la freddezza del cavalletto."),
    ("handheld_medium", "handheld", "handheld", 0.035, 20.0, "A mano dichiarato: reportage, non studio."),
    ("handheld_heavy", "handheld", "handheld", 0.05, 12.0, "A mano marcato: urgenza, verita' grezza."),
    ("shake_light", "handheld", "shake", 0.03, 6.0, "Scossa breve: un impatto, una porta che sbatte."),
    ("shake_heavy", "handheld", "shake", 0.06, 4.0, "Scossa decisa: solo su un evento, mai su un volto."),
    ("crane_up", "crane", "crane_up", 0.12, 10.0, "Gru' finta: sali e allarga, come una gru."),
    ("crane_down", "crane", "crane_down", 0.12, 10.0, "Gru' finta in discesa."),
    ("crane_up_wide", "crane", "crane_up", 0.18, 8.0, "Gru' ampia: apre un blocco."),
    ("crane_down_wide", "crane", "crane_down", 0.18, 8.0, "Gru' ampia in chiusura."),
    ("zoom_in", "zoom", "zoom_in", 0.12, 6.0, "Zoom dichiarato: la lente cambia, non la distanza."),
    ("zoom_out", "zoom", "zoom_out", 0.12, 6.0, "Zoom indietro dichiarato."),
    ("zoom_in_slow", "zoom", "zoom_in", 0.07, 20.0, "Zoom lento: tensione che cresce."),
    ("zoom_out_slow", "zoom", "zoom_out", 0.07, 20.0, "Zoom lento in uscita."),
    ("reveal_left", "reveal", "reveal_left", 0.14, 8.0, "Scopri quello che sta a sinistra del campo."),
    ("reveal_right", "reveal", "reveal_right", 0.14, 8.0, "Scopri quello che sta a destra."),
    ("reveal_up", "reveal", "reveal_up", 0.14, 8.0, "Scopri quello che sta sopra l'inquadratura."),
    ("reveal_down", "reveal", "reveal_down", 0.14, 8.0, "Scopri quello che sta sotto."),
)

MOVES: Dict[str, CameraMove] = {
    row[0]: CameraMove(
        name=row[0],
        family=row[1],
        path=row[2],
        amplitude=row[3],
        cap_seconds=row[4],
        trigger=row[5],
        interp="bezier" if row[1] in ("whip", "crane", "reveal") else "linear",
    )
    for row in _TABLE
}

FAMILIES = tuple(sorted({move.family for move in MOVES.values()}))


def validate(moves: Optional[Dict[str, CameraMove]] = None) -> List[str]:
    """Every problem of the registry, as strings that name the move."""
    table = MOVES if moves is None else moves
    found: List[str] = []
    for name, move in table.items():
        if name != move.name:
            found.append(f"{name}: key and name disagree ({move.name})")
        found.extend(move.problems())
    return found


def require(name: str) -> CameraMove:
    """The named move, or ``CAMERA_UNKNOWN`` with the catalogue size."""
    move = MOVES.get(str(name))
    if move is None:
        raise CameraError(
            "CAMERA_UNKNOWN",
            f"unknown camera move {name!r}; the registry declares {len(MOVES)}: "
            f"{sorted(MOVES)}",
        )
    return move


def stack(moves: Sequence[str]) -> List[CameraMove]:
    """Three movements may ride the same clip; four never do."""
    resolved = [require(name) for name in moves]
    if len(resolved) > MAX_STACK:
        raise CameraError(
            "STACK_TOO_DEEP",
            f"{len(resolved)} camera moves on one clip ({', '.join(m.name for m in resolved)}); "
            f"the declared maximum is {MAX_STACK}",
        )
    return resolved


def _points(path: str, amplitude: float) -> List[Tuple[float, float, float, float, float]]:
    """``(t_share, x, y, width, height)`` control points, all normalised.

    ``t_share`` runs 0..1 over the shot. The crop keeps the subject framed: a push
    shrinks the window around the centre, a pan slides it, an orbit walks it
    around, and the handheld variants alternate small offsets deterministically
    (no random: the same clip renders the same frames twice).
    """
    a = float(amplitude)
    if path == "push":
        return [(0.0, 0.0, 0.0, 1.0, 1.0), (1.0, a / 2, a / 2, 1 - a, 1 - a)]
    if path == "pull":
        return [(0.0, a / 2, a / 2, 1 - a, 1 - a), (1.0, 0.0, 0.0, 1.0, 1.0)]
    if path == "pan_left":
        return [(0.0, a, a / 2, 1 - a, 1 - a), (1.0, 0.0, a / 2, 1 - a, 1 - a)]
    if path == "pan_right":
        return [(0.0, 0.0, a / 2, 1 - a, 1 - a), (1.0, a, a / 2, 1 - a, 1 - a)]
    if path == "tilt_up":
        return [(0.0, a / 2, a, 1 - a, 1 - a), (1.0, a / 2, 0.0, 1 - a, 1 - a)]
    if path == "tilt_down":
        return [(0.0, a / 2, 0.0, 1 - a, 1 - a), (1.0, a / 2, a, 1 - a, 1 - a)]
    if path == "orbit_left":
        return [
            (0.0, 0.0, a / 2, 1 - a, 1 - a),
            (0.5, a / 2, 0.0, 1 - a, 1 - a),
            (1.0, a, a / 2, 1 - a, 1 - a),
        ]
    if path == "orbit_right":
        return [
            (0.0, a, a / 2, 1 - a, 1 - a),
            (0.5, a / 2, 0.0, 1 - a, 1 - a),
            (1.0, 0.0, a / 2, 1 - a, 1 - a),
        ]
    if path == "handheld":
        return [
            (0.0, a / 2, a / 2, 1 - a, 1 - a),
            (0.25, a, a / 2, 1 - a, 1 - a),
            (0.5, a / 2, 0.0, 1 - a, 1 - a),
            (0.75, 0.0, a / 2, 1 - a, 1 - a),
            (1.0, a / 2, a / 2, 1 - a, 1 - a),
        ]
    if path == "shake":
        return [
            (0.0, a / 2, a / 2, 1 - a, 1 - a),
            (0.2, a, 0.0, 1 - a, 1 - a),
            (0.4, 0.0, a, 1 - a, 1 - a),
            (0.6, a, a, 1 - a, 1 - a),
            (0.8, 0.0, 0.0, 1 - a, 1 - a),
            (1.0, a / 2, a / 2, 1 - a, 1 - a),
        ]
    if path == "crane_up":
        return [(0.0, a / 2, a, 1 - a, 1 - a), (1.0, 0.0, 0.0, 1.0, 1.0)]
    if path == "crane_down":
        return [(0.0, 0.0, 0.0, 1.0, 1.0), (1.0, a / 2, a, 1 - a, 1 - a)]
    if path == "zoom_in":
        return [(0.0, 0.0, 0.0, 1.0, 1.0), (1.0, a / 2, a / 2, 1 - a, 1 - a)]
    if path == "zoom_out":
        return [(0.0, a / 2, a / 2, 1 - a, 1 - a), (1.0, 0.0, 0.0, 1.0, 1.0)]
    if path == "reveal_left":
        return [(0.0, a, a / 2, 1 - a, 1 - a), (1.0, a / 2, a / 2, 0.85, 0.85)]
    if path == "reveal_right":
        return [(0.0, 0.0, a / 2, 1 - a, 1 - a), (1.0, a / 2, a / 2, 0.85, 0.85)]
    if path == "reveal_up":
        return [(0.0, a / 2, a, 1 - a, 1 - a), (1.0, a / 2, a / 2, 0.85, 0.85)]
    if path == "reveal_down":
        return [(0.0, a / 2, 0.0, 1 - a, 1 - a), (1.0, a / 2, a / 2, 0.85, 0.85)]
    raise CameraError("CAMERA_PATH", f"unknown path {path!r}")


def build(
    name: str, *, duration: float, intensity: Optional[float] = None
) -> Effect:
    """The EDL 3 effect that performs the named move on a clip of ``duration``.

    ``intensity`` scales the declared amplitude (0..2x) with the same cap, so a
    planner can say "half a push" without inventing a new name.
    """
    move = require(name)
    seconds = float(duration)
    if seconds <= 0.0:
        raise CameraError("CAMERA_DURATION", f"duration must be > 0, got {duration!r}")
    if seconds > move.cap_seconds:
        raise CameraError(
            "CAMERA_TOO_LONG",
            f"{move.name} is declared up to {move.cap_seconds} s, the clip lasts "
            f"{seconds:.3f} s: a movement that outlives its shot is a drift",
        )
    amplitude = float(move.amplitude if intensity is None else move.amplitude * intensity)
    if not (0.0 < amplitude <= 0.35):
        raise CameraError(
            "CAMERA_AMPLITUDE",
            f"{move.name}: amplitude {amplitude:.4f} outside (0, 0.35]",
        )
    points = _points(move.path, amplitude)
    series = dict(zip(("t", "x", "y", "width", "height"), zip(*points)))
    tracks: Dict[str, KeyframeTrack] = {}
    for column in ("x", "y", "width", "height"):
        tracks[column] = KeyframeTrack(
            [
                Keyframe(t=float(t) * seconds, value=float(value), interp=move.interp)
                for t, value in zip(series["t"], series[column])
            ]
        )
    return Effect(kind="crop", params={}, keyframes=tracks)


def describe(name: str) -> Dict[str, object]:
    """The catalogue row, ready for the report."""
    move = require(name)
    return {
        "name": move.name,
        "family": move.family,
        "trigger": move.trigger,
        "amplitude": move.amplitude,
        "cap_seconds": move.cap_seconds,
        "interp": move.interp,
    }


__all__ = [
    "CameraError",
    "CameraMove",
    "FAMILIES",
    "MAX_STACK",
    "MOVES",
    "build",
    "describe",
    "require",
    "stack",
    "validate",
]
