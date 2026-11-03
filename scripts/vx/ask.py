"""vx.ask — una domanda all'operatore, nella vista, con una scadenza.

Il canale e' un file, non la chat: ``ask`` scrive
``<workdir>/questions/<id>.json`` con la domanda e le sue opzioni, poi guarda
``<workdir>/questions/<id>.answer.json`` una volta al secondo. Chi risponde e'
la vista (``vx serve``), che chiama :func:`answer`; se nessuno risponde entro il
timeout si usa ``default`` e lo si dichiara (``answered_by="default"``).

Ogni scrittura e' atomica (file temporaneo + ``replace``) e *fail-soft*: una
cartella non scrivibile non ferma il lavoro, fa tornare subito il default con
l'errore accanto. Un file troncato o illeggibile e' una domanda che non c'e'
ancora, non un'eccezione.

Solo stdlib: nessuna rete, nessun server, nessun import della pipeline.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Sequence as _Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

__all__ = [
    "ANSWER_SCHEMA",
    "ANSWER_SUFFIX",
    "DEFAULT_TIMEOUT",
    "POLL_SEC",
    "QUESTION_DIR",
    "QUESTION_SCHEMA",
    "answer",
    "answer_path",
    "ask",
    "normalize_options",
    "pending",
    "question_path",
]

#: La cartella delle domande, dentro la workdir del lavoro.
QUESTION_DIR = "questions"

#: La forma dei due file. Il suffisso della risposta non puo' essere scambiato
#: per una domanda: ``pending`` lo esclude per nome.
QUESTION_SCHEMA = "vx.question/1"
ANSWER_SCHEMA = "vx.answer/1"
ANSWER_SUFFIX = ".answer.json"

#: Quanto si aspetta una persona prima di usare il default, e ogni quanto lo si
#: guarda. Il polling e' a un secondo perche' la vista scrive una volta sola.
DEFAULT_TIMEOUT = 180.0
POLL_SEC = 1.0

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_id(value: Any) -> str:
    """Un id che non puo' uscire dalla cartella ``questions``.

    Gli id veri sono slug (``target_format``): la sostituzione e' solo una
    cintura contro un id costruito con ``..`` o con un separatore di percorso.
    """
    text = _SAFE.sub("_", str(value or "").strip()).strip("._")
    return text or "domanda"


def normalize_options(options: Any) -> List[Dict[str, str]]:
    """Opzioni nella forma promessa: ``{"value","label","detail"}``.

    Una stringa diventa ``value == label``, un oggetto tiene i suoi campi. Un
    valore vuoto non e' un'opzione: si scarta. L'ordine e' quello ricevuto, e i
    valori duplicati non si ripetono (la vista disegna un pulsante per valore).
    """
    out: List[Dict[str, str]] = []
    seen: set = set()
    for item in options if isinstance(options, _Sequence) and not isinstance(
        options, (str, bytes)
    ) else ():
        if isinstance(item, Mapping):
            value = str(item.get("value", item.get("label", "")) or "").strip()
            label = str(item.get("label") or value).strip()
            detail = str(item.get("detail") or "").strip()
        else:
            value = str(item or "").strip()
            label = value
            detail = ""
        if not value or value in seen:
            continue
        seen.add(value)
        out.append({"value": value, "label": label or value, "detail": detail})
    return out


def _folder(workdir: Any) -> Path:
    return Path(workdir) / QUESTION_DIR


def question_path(workdir: Any, id: Any) -> Path:
    return _folder(workdir) / f"{_safe_id(id)}.json"


def answer_path(workdir: Any, id: Any) -> Path:
    return _folder(workdir) / f"{_safe_id(id)}{ANSWER_SUFFIX}"


def _read_json(path: Path) -> Dict[str, Any]:
    """Un oggetto JSON, o ``{}``: troncato, assente e illeggibile sono uguali."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, Mapping) else {}


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> Optional[str]:
    """Scrive il JSON tutto o niente; ``None`` se e' andata, il motivo se no.

    Il temporaneo ha un nome unico per processo e per giro: due domande in
    parallelo non si sovrascrivono il file di appoggio.
    """
    temporary: Optional[Path] = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(path)
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return f"{type(exc).__name__}: {exc}"
    return None


def _default_result(
    id: str,
    question: Any,
    options: List[Dict[str, str]],
    default: Any,
    *,
    waited: float,
    timed_out: bool,
    error: str = "",
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "id": id,
        "question": str(question or ""),
        "options": options,
        "value": default,
        "default": default,
        "answered_by": "default",
        "answered": False,
        "timed_out": bool(timed_out),
        "waited_sec": round(max(0.0, float(waited)), 3),
    }
    if error:
        result["error"] = error
        result["degraded"] = True
    return result


def _valid_answer(path: Path, values: Sequence[str]) -> Optional[str]:
    """Il valore dell'answer file se e' uno dei valori ammessi, altrimenti None."""
    record = _read_json(path)
    if not record:
        return None
    raw = record.get("value")
    value = str(raw) if raw is not None else ""
    return value if value in values else None


def ask(
    workdir: Any,
    id: Any,
    question: Any,
    options: Any,
    *,
    default: Any = None,
    context: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
    poll: float = POLL_SEC,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """Scrivi la domanda e aspetta la risposta, al massimo ``timeout`` secondi.

    Ritorna ``{"id","question","options","value","default","answered_by",
    "timed_out","waited_sec"}``. ``answered_by`` vale ``"user"`` quando la
    risposta e' arrivata e valida, ``"default"`` quando si e' scaduti (o quando
    la domanda non si e' potuta scrivere). Alla fine la domanda chiusa sparisce
    dalla vista: il file della domanda si toglie, quello della risposta resta.

    ``clock``, ``sleep`` e ``poll`` sono iniettabili: i test provano la scadenza
    senza aspettare davvero.
    """
    qid = _safe_id(id)
    normalized = normalize_options(options)
    values = [item["value"] for item in normalized]
    payload = {
        "id": qid,
        "question": str(question or ""),
        "options": normalized,
        "default": default,
        "created_utc": _now(),
        "context": dict(context) if isinstance(context, Mapping) else {},
    }

    started = clock()
    failure = _atomic_write(question_path(workdir, qid), payload)
    if failure:
        return _default_result(
            qid, question, normalized, default, waited=0.0, timed_out=False,
            error=f"domanda non scritta: {failure}",
        )

    answer_file = answer_path(workdir, qid)
    try:
        limit = max(0.0, float(timeout))
    except (TypeError, ValueError):
        limit = DEFAULT_TIMEOUT
    try:
        pause = max(0.001, float(poll))
    except (TypeError, ValueError):
        pause = POLL_SEC
    # Un orologio fermo non deve diventare un blocco: anche il numero di giri
    # e' finito, non solo il tempo che passa.
    attempts = int(limit / pause) + 2
    while attempts > 0:
        attempts -= 1
        value = _valid_answer(answer_file, values)
        if value is not None:
            waited = max(0.0, clock() - started)
            try:
                question_path(workdir, qid).unlink(missing_ok=True)
            except OSError:
                pass
            return {
                "id": qid,
                "question": str(question or ""),
                "options": normalized,
                "value": value,
                "default": default,
                "answered_by": "user",
                "answered": True,
                "timed_out": False,
                "waited_sec": round(waited, 3),
            }
        remaining = limit - max(0.0, clock() - started)
        if remaining <= 0:
            break
        sleep(min(pause, remaining))

    # Un'ultima occhiata: la risposta puo' essere arrivata sull'ultimo giro.
    value = _valid_answer(answer_file, values)
    waited = max(0.0, clock() - started)
    try:
        question_path(workdir, qid).unlink(missing_ok=True)
    except OSError:
        pass
    if value is not None:
        return {
            "id": qid,
            "question": str(question or ""),
            "options": normalized,
            "value": value,
            "default": default,
            "answered_by": "user",
            "answered": True,
            "timed_out": False,
            "waited_sec": round(waited, 3),
        }
    return _default_result(
        qid, question, normalized, default, waited=waited, timed_out=True
    )


def answer(workdir: Any, id: Any, value: Any) -> Dict[str, Any]:
    """Rispondi a una domanda aperta, se ``value`` e' una delle sue opzioni.

    Un valore inventato non scrive niente: la vista lo deve sapere, perche' una
    risposta fuori elenco non e' una scelta. La risposta e' scritta in modo
    atomico e poi la domanda si chiude (il suo file si toglie).
    """
    qid = _safe_id(id)
    record = _read_json(question_path(workdir, qid))
    if not record:
        return {"ok": False, "id": qid, "reason": "domanda non trovata"}
    normalized = normalize_options(record.get("options"))
    values = [item["value"] for item in normalized]
    wanted = str(value) if value is not None else ""
    if wanted not in values:
        return {
            "ok": False,
            "id": qid,
            "value": wanted,
            "reason": "valore non fra le opzioni",
            "options": values,
        }
    failure = _atomic_write(
        answer_path(workdir, qid),
        {
            "schema": ANSWER_SCHEMA,
            "id": qid,
            "value": wanted,
            "answered_utc": _now(),
        },
    )
    if failure:
        return {
            "ok": False,
            "id": qid,
            "degraded": True,
            "reason": f"risposta non scritta: {failure}",
        }
    try:
        question_path(workdir, qid).unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True, "id": qid, "value": wanted}


def pending(workdir: Any) -> List[Dict[str, Any]]:
    """Le domande aperte nella workdir, nella forma che la vista pubblica.

    Ogni voce porta ``id``, ``question``, ``options``, ``default``,
    ``waiting_for`` (secondi), ``answered`` e ``answer``. Un file troncato o
    illeggibile non e' una domanda: si salta. La cartella assente e' una lista
    vuota, mai un errore.
    """
    folder = _folder(workdir)
    try:
        files = sorted(folder.glob("*.json"))
    except OSError:
        return []
    now = time.time()
    entries: List[Dict[str, Any]] = []
    seen: set = set()
    for path in files:
        if path.name.endswith(ANSWER_SUFFIX) or path.name.endswith(".tmp"):
            continue
        record = _read_json(path)
        if not record:
            continue
        qid = _safe_id(record.get("id") or path.name[: -len(".json")])
        if qid in seen:
            continue
        seen.add(qid)
        normalized = normalize_options(record.get("options"))
        reply = _read_json(answer_path(workdir, qid))
        try:
            age = max(0.0, now - path.stat().st_mtime)
        except OSError:
            age = 0.0
        entries.append(
            {
                "id": qid,
                "question": str(record.get("question") or ""),
                "options": normalized,
                "default": record.get("default"),
                "waiting_for": round(age, 1),
                "answered": bool(reply) and reply.get("value") is not None,
                "answer": reply.get("value") if reply else None,
            }
        )
    return entries
