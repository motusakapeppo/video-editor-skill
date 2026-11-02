"""vx.stabilize — stabilizzare davvero, e dichiarare se il movimento era voluto.

Il ponte dichiarava ``stabilize deshake`` senza applicare nulla. Qui la
stabilizzazione esiste, in due passaggi ``vidstab`` (stima del moto, poi
compensazione), e c'e' una regola sola che decide:

* ``propose`` (default) - il referto propone, il filtro non parte;
* ``apply`` - il filtro parte, e il referto dice che e' partito;
* ``off`` - non si propone nemmeno.

Il motivo della regola e' misurato (D-24): una panoramica a mano libera e un
tremolio hanno la stessa energia di movimento, e tre estimatori non li separano
(2,46 / 3,21 / 2,12). L'unica cosa che li distingue e' **l'intenzione**, e
l'intenzione non si deduce dal segnale: si dichiara.

``apply`` restituisce il referto con il jitter prima e dopo, misurati sullo
stesso estratto: una stabilizzazione che non abbassa il numero non e' una
stabilizzazione, e il chiamante lo vede.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from . import analyze
from . import ffmpeg as ff
from .contract import VxError

INTENTS = ("propose", "apply", "off")
DEFAULT_INTENT = "propose"
DEFAULT_SMOOTHING = 15
DEFAULT_ZOOM = 2
#: Sotto questa riduzione relativa il trattamento non ha fatto nulla di utile.
MIN_DROP = 0.30


class StabilizeError(VxError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def plan(
    *,
    jitter: Optional[float],
    threshold: float,
    intent: Optional[str] = None,
) -> Dict[str, Any]:
    """What the report must say, given the measurement and the declared intent."""
    chosen = str(intent) if intent is not None else DEFAULT_INTENT
    if chosen not in INTENTS:
        raise StabilizeError(
            "STABILIZE_INTENT",
            f"intent {chosen!r} is not declared; the three are {list(INTENTS)}",
        )
    proposed = chosen != "off" and jitter is not None and float(jitter) >= float(threshold)
    applied = proposed and chosen == "apply"
    return {
        "intent": chosen,
        "jitter": None if jitter is None else round(float(jitter), 3),
        "threshold": float(threshold),
        "proposed": proposed,
        "applied": applied,
        "why": (
            "nessuna misura: niente da proporre"
            if jitter is None
            else f"jitter {float(jitter):.2f} {'>=' if proposed else '<'} soglia {float(threshold):.2f}"
        ),
        "note": (
            "il movimento puo' essere voluto: la proposta va approvata, non eseguita"
            if proposed and not applied
            else ""
        ),
    }


def _escape(path: Path) -> str:
    return str(Path(path).resolve()).replace("\\", "/").replace(":", "\\:")


def apply(
    source: Path,
    destination: Path,
    *,
    smoothing: int = DEFAULT_SMOOTHING,
    zoom: int = DEFAULT_ZOOM,
) -> Dict[str, Any]:
    """Two-pass vidstab, then measure the result: the effect or nothing."""
    source, destination = Path(source), Path(destination)
    if not source.is_file():
        raise StabilizeError("STABILIZE_SOURCE", f"clip non trovato: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    transforms = destination.with_suffix(".trf")
    detect = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source),
            "-vf", f"vidstabdetect=shakiness=6:accuracy=15:result='{_escape(transforms)}'",
            "-f", "null", "-",
        ],
        capture_output=True,
    )
    if detect.returncode != 0 or not transforms.is_file():
        raise StabilizeError(
            "STABILIZE_DETECT", detect.stderr.decode("utf-8", "replace")[-300:]
        )
    transform = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source),
            "-vf", f"vidstabtransform=input='{_escape(transforms)}':smoothing={int(smoothing)}:zoom={int(zoom)}",
            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            "-an", str(destination),
        ],
        capture_output=True,
    )
    transforms.unlink(missing_ok=True)
    if transform.returncode != 0 or not destination.is_file():
        raise StabilizeError(
            "STABILIZE_TRANSFORM", transform.stderr.decode("utf-8", "replace")[-300:]
        )
    before = float(analyze.measure_jitter(source).get("score") or 0.0)
    after = float(analyze.measure_jitter(destination).get("score") or 0.0)
    drop = 0.0 if before <= 0.0 else 1.0 - after / before
    return {
        "source": str(source),
        "output": str(destination),
        "jitter_before": round(before, 3),
        "jitter_after": round(after, 3),
        "drop": round(drop, 4),
        "effective": drop >= MIN_DROP,
        "smoothing": int(smoothing),
        "zoom": int(zoom),
    }


__all__ = [
    "DEFAULT_INTENT",
    "DEFAULT_SMOOTHING",
    "DEFAULT_ZOOM",
    "INTENTS",
    "MIN_DROP",
    "StabilizeError",
    "apply",
    "plan",
]
