"""vx.degrade — gli accessori che cedono, senza portarsi via il montaggio.

La regola e' una sola: un lavoro puo' fermarsi per la sorgente illeggibile, per
ffmpeg assente, per un piano che non regge o per la guardia alla consegna.
Tutto il resto — pannello, registro delle sessioni, learning, feedback,
decisioni, verifica — e' accessorio: se cede, il lavoro prosegue e il report lo
dichiara in ``degraded``.

Questo modulo e' il posto dove quel cedimento si annota. Non scrive su disco,
non importa niente dal resto della skill e non solleva mai: chi lo chiama sta
gia' gestendo un errore e non deve trovarne un secondo. Un nome si annota una
volta sola (il primo motivo vince), cosi' un pannello che fallisce a ogni fase
non riempie il report di righe uguali.

    degrade.note("panel", exc)          # registra il motivo, non rilancia
    degrade.merge_into(report)          # report["degraded"] = ["panel", ...]
    degrade.clear()                     # solo per i test: bag vuota

Il report porta due chiavi: ``degraded`` (i nomi, in ordine di comparsa) e
``degraded_why`` (nome -> ``Tipo: messaggio``). La seconda non e' un doppione:
il nome dice *cosa* si e' perso, il motivo dice *perche'*.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Iterable, List, Mapping

__all__ = ["clear", "merge_into", "names", "note", "snapshot", "text"]

_LOCK = threading.Lock()
_NOTES: Dict[str, str] = {}


def note(name: Any, exc: Any = None, why: Any = "") -> str:
    """Annota il cedimento di ``name`` e restituisce il motivo; non solleva.

    ``exc`` puo' essere un'eccezione (il tipo e il messaggio finiscono nel
    motivo) o una stringa gia' pronta. La prima annotazione di un nome vince:
    le successive si scartano, perche' un accessorio che fallisce dieci volte
    e' un guasto solo.
    """
    label = str(name or "accessorio").strip() or "accessorio"
    if isinstance(exc, BaseException):
        reason = f"{type(exc).__name__}: {exc}"
    elif exc:
        reason = str(exc)
    else:
        reason = str(why or "").strip()
    reason = reason.strip() or "motivo non dichiarato"
    try:
        with _LOCK:
            _NOTES.setdefault(label, reason)
    except Exception:  # noqa: BLE001 - nemmeno il registro dei guasti puo' rompere
        return reason
    return reason


def snapshot() -> Dict[str, str]:
    """Copia dei cedimenti annotati finora: nome -> motivo."""
    try:
        with _LOCK:
            return dict(_NOTES)
    except Exception:  # noqa: BLE001
        return {}


def names() -> List[str]:
    """I nomi degradati, in ordine di comparsa."""
    return list(snapshot())


def clear() -> None:
    """Svuota la bag. Serve ai test: un processo reale la lascia com'e'."""
    try:
        with _LOCK:
            _NOTES.clear()
    except Exception:  # noqa: BLE001
        pass


def text(report: Mapping[str, Any]) -> str:
    """Una riga discreta per chi legge i log: nomi e motivi, niente pila."""
    if not isinstance(report, Mapping):
        return ""
    reasons = report.get("degraded_why")
    rows: List[str] = []
    for name in report.get("degraded") or []:
        why = reasons.get(str(name), "") if isinstance(reasons, Mapping) else ""
        rows.append(f"{name} ({why})" if why else str(name))
    return ", ".join(rows)


def merge_into(report: Any, extra: Iterable[Any] = ()) -> Any:
    """Porta i cedimenti annotati dentro ``report``, senza sovrascrivere i suoi.

    ``report`` non e' un dizionario: si restituisce com'e', perche' questo
    modulo non deve essere il punto in cui un chiamante scopre un TypeError.
    """
    if not isinstance(report, dict):
        return report
    try:
        found = snapshot()
    except Exception:  # noqa: BLE001
        found = {}
    for name in extra or ():
        label = str(name or "").strip()
        if label:
            found.setdefault(label, "motivo non dichiarato")
    declared = report.get("degraded")
    order: List[str] = [str(item) for item in declared] if isinstance(declared, list) else []
    reasons: Dict[str, str] = {}
    if isinstance(report.get("degraded_why"), Mapping):
        reasons.update({str(key): str(value) for key, value in report["degraded_why"].items()})
    for name, why in found.items():
        if name not in order:
            order.append(name)
        reasons.setdefault(name, why)
    report["degraded"] = order
    if reasons:
        report["degraded_why"] = reasons
    return report
