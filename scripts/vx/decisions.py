"""vx.decisions — il registro unico delle scelte, con la prova che le sostiene.

Ogni voce risponde a una domanda e dichiara quattro cose: cosa si e' scelto,
quanta fiducia merita, su quale evidenza si regge e cosa la falsificherebbe.
Il registro non decide: rende controllabile una decisione presa altrove. Una
voce senza prova non e' una decisione, e' un'opinione; per questo
:func:`record` la rifiuta invece di registrarla.

La forma di una voce::

    {"id": "font",
     "question": "quale famiglia per un horror condensato?",
     "chosen": "Archivo Black",
     "confidence": "high" | "medium" | "low",
     "evidence": ["larghezza condensed misurata 0.422 em", "energia 0.82"],
     "alternatives": [{"option": "Bebas Neue", "why_not": "score 2.0 contro 4.0"}],
     "alternatives_note": "",   # obbligatoria solo se alternatives e' vuota
     "falsified_if": "il titolo risulta illeggibile a 1080p o fuori tono",
     "by": "auto" | "user" | "timeout"}

Regole, tutte verificate da :func:`validate`:

* ``evidence`` non e' mai vuota e nessuna riga e' vuota;
* ``falsified_if`` non e' mai vuoto;
* ``confidence`` e' ``low`` quando l'evidenza e' debole: nessuna riga porta un
  numero o un fatto misurabile. Una fiducia ``high``/``medium`` su una prova
  debole e' un errore, non una sfumatura;
* ``alternatives`` puo' essere vuota solo se non c'erano alternative reali, e
  allora ``alternatives_note`` dice perche';
* ``by`` dice chi ha scelto: ``auto``, ``user`` o ``timeout``. La forma non
  cambia col grado di autonomia, cambia solo questo campo.

Il modulo e' puro: nessuna rete, nessun file, nessun import del resto della
skill. Cosi' il registro si puo' provare senza un render.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

CONFIDENCES = ("high", "medium", "low")
DECIDERS = ("auto", "user", "timeout")

#: Le parole che rendono una riga un fatto del lavoro anche senza cifre. Non
#: sono un vocabolario aperto: bastano a distinguere "score 4.0" da "sembra
#: adatto", e chi vuole una fiducia alta scrive comunque il numero.
_FACT_WORDS = (
    "misurat",
    "rilevat",
    "calcolat",
    "verificat",
    "confront",
    "campion",
    "score",
    "punteggio",
    "licen",
    "budget",
    "costo",
    "secondi",
    "fps",
    "frame",
    "shot",
    "clip",
    "timeout",
    "errore",
    "soglia",
    "regola",
    "digest",
    "sha256",
)


class DecisionError(ValueError):
    """Una voce che non rispetta il contratto del registro."""


def is_weak(evidence: Any) -> bool:
    """Vero quando nessuna riga porta un numero o un fatto verificabile.

    L'evidenza vuota e' debole per definizione; una riga fatta di sole parole
    generiche ("sembra adatto", "scelta ragionevole") non sostiene una
    fiducia alta.
    """
    if isinstance(evidence, str):
        rows = [evidence]
    elif isinstance(evidence, Mapping):
        rows = list(evidence.values())
    else:
        rows = list(evidence or ())
    if not rows:
        return True
    for row in rows:
        text = str(row).strip().lower()
        if not text:
            continue
        if any(char.isdigit() for char in text):
            return False
        if any(word in text for word in _FACT_WORDS):
            return False
    return True


def _text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DecisionError(f"{field}: non puo' essere vuoto")
    return text


def _evidence_rows(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if isinstance(value, Mapping):
        value = list(value.values())
    if not isinstance(value, Iterable) or isinstance(value, (bytes, bytearray)):
        raise DecisionError("evidence: deve essere una lista di righe")
    rows: List[str] = []
    for item in value:
        row = str(item or "").strip()
        if not row:
            raise DecisionError("evidence: nessuna riga puo' essere vuota")
        rows.append(row)
    if not rows:
        raise DecisionError("evidence: una voce senza prova non e' una decisione")
    return rows


def _alternatives(value: Any) -> List[Dict[str, str]]:
    if value is None:
        value = []
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray)):
        raise DecisionError("alternatives: deve essere una lista")
    rows: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise DecisionError("alternatives: ogni alternativa deve essere un oggetto")
        option = _text(item.get("option"), "alternatives.option")
        why_not = _text(item.get("why_not"), f"alternatives[{option}].why_not")
        rows.append({"option": option, "why_not": why_not})
    return rows


def _confidence(value: Any, evidence: Sequence[str]) -> str:
    weak = is_weak(evidence)
    if value is None:
        if weak:
            return "low"
        return "high" if len(evidence) >= 2 else "medium"
    confidence = str(value).strip().lower()
    if confidence not in CONFIDENCES:
        raise DecisionError(
            f"confidence: {confidence!r} non ammessa (high, medium, low)"
        )
    if weak and confidence != "low":
        raise DecisionError(
            "confidence: l'evidenza e' debole, la fiducia deve essere low "
            "(e la ragione va scritta in evidence)"
        )
    return confidence


def _decider(value: Any) -> str:
    by = str(value or "auto").strip().lower()
    if by not in DECIDERS:
        raise DecisionError(f"by: {by!r} non ammesso (auto, user, timeout)")
    return by


def validate(entry: Mapping[str, Any]) -> Dict[str, Any]:
    """Controlla una voce e la restituisce normalizzata; solleva se non regge.

    Non riempie i buchi: un'evidenza mancante e' un errore, non una stringa
    vuota da ignorare. La sola cosa dedotta e' ``confidence`` quando il
    chiamante non la dichiara: in quel caso l'evidenza decide (debole -> low).
    """
    if not isinstance(entry, Mapping):
        raise DecisionError("una voce deve essere un oggetto")
    decision_id = _text(entry.get("id"), "id")
    if not all(char.isalnum() or char in "_-." for char in decision_id):
        raise DecisionError(f"id: {decision_id!r} non e' un identificatore")
    question = _text(entry.get("question"), "question")
    chosen = _text(entry.get("chosen"), "chosen")
    evidence = _evidence_rows(entry.get("evidence"))
    falsified_if = _text(entry.get("falsified_if"), "falsified_if")
    confidence = _confidence(entry.get("confidence"), evidence)
    by = _decider(entry.get("by"))
    alternatives = _alternatives(entry.get("alternatives"))
    note = str(entry.get("alternatives_note") or "").strip()
    if not alternatives and not note:
        raise DecisionError(
            "alternatives_note: senza alternative va detto perche' non ce n'erano"
        )
    return {
        "id": decision_id,
        "question": question,
        "chosen": chosen,
        "confidence": confidence,
        "evidence": evidence,
        "alternatives": alternatives,
        "alternatives_note": note,
        "falsified_if": falsified_if,
        "by": by,
    }


def record(
    *,
    id: str,
    question: str,
    chosen: str,
    evidence: Any,
    falsified_if: str,
    confidence: Optional[str] = None,
    alternatives: Any = None,
    alternatives_note: str = "",
    by: str = "auto",
) -> Dict[str, Any]:
    """Costruisce una voce valida; ``confidence`` assente la deduce dall'evidenza."""
    return validate(
        {
            "id": id,
            "question": question,
            "chosen": chosen,
            "confidence": confidence,
            "evidence": evidence,
            "alternatives": alternatives if alternatives is not None else [],
            "alternatives_note": alternatives_note,
            "falsified_if": falsified_if,
            "by": by,
        }
    )


class Registry:
    """L'elenco ordinato delle voci, senza id ripetuti.

    ``add`` accetta una voce gia' costruita o gli stessi argomenti di
    :func:`record`. Un id doppio e' un errore: due risposte alla stessa domanda
    non si possono leggere come una sola.
    """

    def __init__(self, entries: Any = None) -> None:
        self._entries: List[Dict[str, Any]] = []
        for entry in entries or ():
            self.add(entry)

    def add(self, entry: Optional[Mapping[str, Any]] = None, **fields: Any) -> Dict[str, Any]:
        if entry is None:
            item = record(**fields)
        else:
            if fields:
                raise DecisionError("add: o una voce o i campi, non entrambi")
            item = validate(entry)
        if any(existing["id"] == item["id"] for existing in self._entries):
            raise DecisionError(f"id: {item['id']!r} e' gia' nel registro")
        self._entries.append(item)
        return item

    def extend(self, entries: Any) -> "Registry":
        for entry in entries or ():
            self.add(entry)
        return self

    def as_list(self) -> List[Dict[str, Any]]:
        """Copia serializzabile: il chiamante non puo' mutare il registro per sbaglio."""
        return [dict(item) for item in self._entries]

    def by_id(self, decision_id: str) -> Optional[Dict[str, Any]]:
        for item in self._entries:
            if item["id"] == decision_id:
                return dict(item)
        return None

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self.as_list())

    def __repr__(self) -> str:  # pragma: no cover - solo per diagnosi
        return f"Registry({len(self._entries)} voci)"


def validate_all(entries: Any) -> List[Dict[str, Any]]:
    """Valida un elenco gia' costruito; utile a chi legge un report."""
    registry = Registry()
    for entry in entries or ():
        registry.add(entry)
    return registry.as_list()


__all__ = [
    "CONFIDENCES",
    "DECIDERS",
    "DecisionError",
    "Registry",
    "is_weak",
    "record",
    "validate",
    "validate_all",
]
