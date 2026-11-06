"""vx.feedback — il giudizio umano, conservato fuori dal codice.

La skill deve imparare da ogni lavoro **senza** che ogni lavoro modifichi il
codice. A fine giro si chiede una cosa sola: un voto da 1 a 5. Il voto, con il
contesto che il lavoro ha gia' misurato, finisce in un registro separato —
``<root>/feedback.jsonl``, default ``~/.vx`` — una riga JSON per giudizio,
appesa e mai riscritta. Il registro non vive nella workdir: un lavoro non tocca
ne' il codice ne' il proprio report, e il giudizio resta leggibile anche dopo
che la workdir e' stata cancellata.

Il buco che questo modulo chiude: nessuna metrica della skill (equilibrio del
montaggio, scarto della stima, violazioni) e' mai stata validata contro il
giudizio di una persona. ``report`` mette le due cose vicine — media per esito,
media per equilibrio, MAPE stima/consuntivo, correlazione equilibrio/voto — e
dichiara quando il numero non basta: sotto ``CORRELATION_MIN_N`` valutazioni la
correlazione **non e' conclusiva**, e la frase esce insieme al numero invece di
lasciare che qualcuno la scambi per una prova.

Forma di una riga (``vx.feedback/1``)::

    {"schema": "vx.feedback/1", "job_id": "...", "prompt": "...",
     "created_utc": "...", "rating": 1..5, "note": "...",
     "context": {"outcome": "done|stopped|failed|incomplete",
                 "balance": "low|ok|high", "violations": ["..."],
                 "estimate_seconds": 274, "actual_seconds": 312,
                 "waiting_seconds": 180,
                 "font": "Archivo Black", "effects": 3, "shots": 24,
                 "duration_sec": 30.4}}

``actual_seconds`` e' il tempo **della macchina**: le attese ai cancelli stanno in
``waiting_seconds``, a parte. Sommare tutto il blocco ``timings`` del report
contava due volte (``total`` e' gia' il totale) e misurava anche il tempo in cui
la persona pensava: due lavori identici risultavano uno piu' lento dell'altro
solo perche' il telefono era rimasto in tasca.

``append`` e' atomico: la riga si scrive in append sotto un lock di file, con
``flush`` + ``fsync`` — non si riscrive il file intero, quindi due processi che
registrano insieme non si perdono una riga a vicenda. ``pending`` e' il segnale
che ``auto`` lascia a fine lavoro nella workdir (``feedback_request.json``):
dice alla vista "puoi chiedere", e non blocca mai niente.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from . import degrade

SCHEMA = "vx.feedback/1"

#: Il registro e il suo lock. Il lock e' un file a parte: il registro resta
#: leggibile con ``cat``/``jq`` anche mentre qualcuno scrive.
REGISTER_NAME = "feedback.jsonl"
LOCK_NAME = "feedback.lock"

#: Il segnale che la vista puo' chiedere un voto (scritto da ``auto``).
REQUEST_NAME = "feedback_request.json"

#: Dove vive il registro: ``--root``, poi l'ambiente, poi la home.
ROOT_ENV = "VX_FEEDBACK_ROOT"
DEFAULT_ROOT_DIR = ".vx"

RATINGS: Tuple[int, ...] = (1, 2, 3, 4, 5)
OUTCOMES: Tuple[str, ...] = ("done", "stopped", "failed", "incomplete")
BALANCES: Tuple[str, ...] = ("low", "ok", "high")

#: L'asse su cui si misura l'equilibrio: e' un ordinale dichiarato, non una
#: misura. ``low`` = montaggio troppo lento, ``high`` = troppo frenetico.
BALANCE_AXIS: Dict[str, float] = {"low": -1.0, "ok": 0.0, "high": 1.0}

#: Sotto questa soglia la correlazione si dichiara non conclusiva. Dieci e' una
#: convenzione dichiarata, non un teorema: serve a impedire che tre voti
#: diventino una "scoperta".
CORRELATION_MIN_N = 10

#: Il lock di processo: su Windows ``msvcrt.locking`` e' per handle, e due
#: thread dello stesso processo devono comunque mettersi in fila.
_THREAD_LOCK = threading.Lock()

#: Un dizionario per (registro, mtime, dimensione): la UI chiede lo stato ogni
#: secondo e non deve rileggere il registro a ogni giro. Chi legge non lo modifica.
_REPORT_CACHE: Dict[Tuple[str, int, int], Dict[str, Any]] = {}


# --------------------------------------------------------------------------- #
# dove sta il registro
# --------------------------------------------------------------------------- #


def root_dir(root: Optional[Any] = None) -> Path:
    """La radice del registro: argomento, ambiente, ``~/.vx`` — in quest'ordine."""
    if root:
        return Path(root)
    env = str(os.environ.get(ROOT_ENV) or "").strip()
    if env:
        return Path(env)
    return Path.home() / DEFAULT_ROOT_DIR


def register_path(root: Optional[Any] = None) -> Path:
    return root_dir(root) / REGISTER_NAME


def request_path(workdir: Any) -> Path:
    return Path(workdir) / REQUEST_NAME


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _number(value: Any) -> Optional[float]:
    """Un float finito, o ``None``: un campo assente non e' uno zero."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(data) if isinstance(data, Mapping) else {}


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> Path:
    """Un JSON leggibile per intero anche mentre un altro processo lo guarda."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    handle = tempfile.NamedTemporaryFile(
        "w",
        dir=str(target.parent),
        prefix=target.name + ".",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
        newline="\n",
    )
    tmp = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


# --------------------------------------------------------------------------- #
# il voto
# --------------------------------------------------------------------------- #


def validate_rating(value: Any) -> int:
    """Un voto 1..5, o ``ValueError``: fuori intervallo non si registra niente.

    Accetta ``4``, ``4.0`` e ``"4"`` (la query string della vista e' testo), ma
    non ``4.5``, ``0``, ``6`` o ``"boh"``. Un voto inventato non si conserva:
    il registro serve a misurare, non a riempire una tabella.
    """
    if isinstance(value, bool) or value is None:
        raise ValueError("il voto deve essere un intero da 1 a 5")
    if isinstance(value, str):
        text = value.strip()
        if not re.fullmatch(r"[+-]?\d+", text):
            raise ValueError(f"voto non numerico: {value!r} (ammessi 1..5)")
        number = int(text)
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"voto non intero: {value!r} (ammessi 1..5)")
        number = int(value)
    elif isinstance(value, int):
        number = value
    else:
        raise ValueError(f"voto non numerico: {value!r} (ammessi 1..5)")
    if number < RATINGS[0] or number > RATINGS[-1]:
        raise ValueError(
            f"voto fuori intervallo: {number} (ammessi {RATINGS[0]}..{RATINGS[-1]})"
        )
    return number


def make_record(
    *,
    job_id: Any = "",
    rating: Any,
    prompt: Any = "",
    note: Any = "",
    context: Optional[Mapping[str, Any]] = None,
    created_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Un record nella forma ``vx.feedback/1``, con il voto gia' validato."""
    return {
        "schema": SCHEMA,
        "job_id": str(job_id or ""),
        "prompt": str(prompt or ""),
        "created_utc": str(created_utc or utc_now()),
        "rating": validate_rating(rating),
        "note": str(note or ""),
        "context": dict(context or {}),
    }


# --------------------------------------------------------------------------- #
# appeso, sotto lock
# --------------------------------------------------------------------------- #


def _lock_handle(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_handle(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _locked(lock_path: Path) -> Iterator[None]:
    """Il lock di file (fra processi) piu' quello di thread (dentro il processo).

    Se il file system non sa fare il lock, si perde solo la garanzia fra
    processi: l'append resta sotto il lock di thread e la riga e' gia' formata.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK:
        handle = open(lock_path, "a+b")
        locked = False
        try:
            try:
                _lock_handle(handle)
                locked = True
            except OSError:
                locked = False
            yield
        finally:
            if locked:
                try:
                    _unlock_handle(handle)
                except OSError:
                    pass
            handle.close()


def append(record: Mapping[str, Any], root: Optional[Any] = None) -> Dict[str, Any]:
    """Appende **una** riga al registro e la restituisce normalizzata.

    Non riscrive il file: apre in append, scrive la riga, ``flush`` e ``fsync``
    sotto lock. Un voto fuori 1..5 solleva ``ValueError`` prima di toccare il
    disco. Un registro illeggibile non si corregge qui: si appende in coda.

    Un registro che non si scrive (permessi, disco pieno, cartella che non si
    crea) **non** solleva: la riga non entra, il motivo finisce in
    ``stored: false`` e in :data:`vx.degrade`, e chi ha chiesto il voto resta
    vivo. Il giudizio e' accessorio; il lavoro no.
    """
    if not isinstance(record, Mapping):
        raise ValueError("il record del giudizio deve essere un oggetto JSON")
    declared = str(record.get("schema") or SCHEMA)
    if declared != SCHEMA:
        raise ValueError(f"schema non riconosciuto: {declared} (atteso {SCHEMA})")
    raw_context = record.get("context")
    normalized = make_record(
        job_id=record.get("job_id", ""),
        rating=record.get("rating"),
        prompt=record.get("prompt", ""),
        note=record.get("note", ""),
        context=raw_context if isinstance(raw_context, Mapping) else {},
        created_utc=record.get("created_utc"),
    )
    target = register_path(root)
    line = json.dumps(normalized, ensure_ascii=False, default=str) + "\n"
    try:
        with _locked(target.parent / LOCK_NAME):
            with open(target, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
    except OSError as exc:
        reason = f"{type(exc).__name__}: {exc}"
        degrade.note("feedback", exc)
        stored = dict(normalized)
        stored["stored"] = False
        stored["error"] = reason
        return stored
    return normalized


# --------------------------------------------------------------------------- #
# lettura del registro
# --------------------------------------------------------------------------- #


def _read_register(path: Path) -> Tuple[List[Dict[str, Any]], int]:
    """I record validi e quante righe non si leggono.

    Una riga troncata (crash a meta' append) o un voto fuori intervallo si
    saltano: perdere il registro intero per una riga rotta sarebbe peggio.
    """
    records: List[Dict[str, Any]] = []
    broken = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return records, broken
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            rating = validate_rating(data.get("rating"))
        except (ValueError, TypeError, AttributeError):
            broken += 1
            continue
        if not isinstance(data, Mapping):
            broken += 1
            continue
        item = dict(data)
        item["rating"] = rating
        context = item.get("context")
        item["context"] = dict(context) if isinstance(context, Mapping) else {}
        records.append(item)
    return records, broken


def read_all(root: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Tutti i giudizi validi, nell'ordine in cui sono stati scritti."""
    records, _broken = _read_register(register_path(root))
    return records


def latest(
    root: Optional[Any] = None, job_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """L'ultimo giudizio, eventualmente di un lavoro solo."""
    wanted = str(job_id or "")
    for record in reversed(read_all(root)):
        if wanted and str(record.get("job_id") or "") != wanted:
            continue
        return record
    return None


# --------------------------------------------------------------------------- #
# aggregati
# --------------------------------------------------------------------------- #


def _mean(values: Sequence[float]) -> Optional[float]:
    return round(float(statistics.fmean(values)), 3) if values else None


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Il coefficiente di Pearson, o ``None`` se non e' calcolabile.

    Con meno di tre coppie, o con una serie piatta (varianza zero), il numero
    non esiste: restituire 0.0 direbbe "nessuna relazione" quando la verita' e'
    "non misurabile".
    """
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0.0 or var_y <= 0.0:
        return None
    r = cov / math.sqrt(var_x * var_y)
    return round(max(-1.0, min(1.0, r)), 4)


def _plural(count: int) -> str:
    return f"{count} {'valutazione' if count == 1 else 'valutazioni'}"


def _group_mean(
    records: Sequence[Mapping[str, Any]], key: str, order: Sequence[str]
) -> Dict[str, Dict[str, Any]]:
    buckets: Dict[str, List[float]] = {name: [] for name in order}
    for record in records:
        context = record.get("context") if isinstance(record.get("context"), Mapping) else {}
        name = str(context.get(key) or "").strip().lower()
        if name in buckets:
            buckets[name].append(float(record["rating"]))
    return {
        name: {"n": len(values), "mean": _mean(values)} for name, values in buckets.items()
    }


def _mape(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Lo scarto percentuale assoluto medio fra stima e consuntivo.

    Solo le coppie con entrambi i numeri e un consuntivo positivo entrano nel
    conto: una stima senza misura non e' uno zero, e' un dato mancante.
    """
    errors: List[float] = []
    deltas: List[float] = []
    for record in records:
        context = record.get("context") if isinstance(record.get("context"), Mapping) else {}
        estimate = _number(context.get("estimate_seconds"))
        actual = _number(context.get("actual_seconds"))
        if estimate is None or actual is None or actual <= 0:
            continue
        errors.append(abs(estimate - actual) / actual * 100.0)
        deltas.append(estimate - actual)
    return {
        "n": len(errors),
        "mean_percent": round(statistics.fmean(errors), 1) if errors else None,
        "median_percent": round(statistics.median(errors), 1) if errors else None,
        "mean_error_sec": round(statistics.fmean(deltas), 1) if deltas else None,
    }


def _correlation(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Quanto il "balance" misurato somiglia al voto, con n e la frase onesta."""
    xs: List[float] = []
    ys: List[float] = []
    for record in records:
        context = record.get("context") if isinstance(record.get("context"), Mapping) else {}
        name = str(context.get("balance") or "").strip().lower()
        if name not in BALANCE_AXIS:
            continue
        xs.append(BALANCE_AXIS[name])
        ys.append(float(record["rating"]))
    n = len(xs)
    value = pearson(xs, ys)
    conclusive = n >= CORRELATION_MIN_N
    if conclusive:
        note = (
            f"con {_plural(n)} la correlazione e' sopra la soglia dichiarata "
            f"({CORRELATION_MIN_N}); resta un'associazione, non una prova"
        )
    else:
        note = (
            f"con {_plural(n)} la correlazione non e' conclusiva "
            f"(servono almeno {CORRELATION_MIN_N} valutazioni con balance)"
        )
    return {
        "n": n,
        "pearson": value,
        "conclusive": conclusive,
        "min_n": CORRELATION_MIN_N,
        "encoding": dict(BALANCE_AXIS),
        "note": note,
        "caveat": (
            "i voti sono volontari e il campione e' piccolo: la correlazione dice "
            "dove guardare, non cosa correggere"
        ),
    }


def _build_report(path: Path) -> Dict[str, Any]:
    records, broken = _read_register(path)
    ratings = [float(record["rating"]) for record in records]
    distribution = {str(value): 0 for value in RATINGS}
    for record in records:
        distribution[str(record["rating"])] += 1
    stamps = sorted(
        str(record.get("created_utc") or "") for record in records if record.get("created_utc")
    )
    return {
        "root": str(path),
        "count": len(records),
        "mean": _mean(ratings),
        "distribution": distribution,
        "by_outcome": _group_mean(records, "outcome", OUTCOMES),
        "by_balance": _group_mean(records, "balance", BALANCES),
        "mape": _mape(records),
        "correlation": _correlation(records),
        "window": {
            "first_utc": stamps[0] if stamps else "",
            "last_utc": stamps[-1] if stamps else "",
        },
        "broken_lines": broken,
    }


def _empty_report(path: Path) -> Dict[str, Any]:
    """Gli aggregati di un registro che non si legge: zeri, non un'eccezione."""
    return {
        "root": str(path),
        "count": 0,
        "mean": None,
        "distribution": {str(value): 0 for value in RATINGS},
        "by_outcome": _group_mean([], "outcome", OUTCOMES),
        "by_balance": _group_mean([], "balance", BALANCES),
        "mape": _mape([]),
        "correlation": _correlation([]),
        "window": {"first_utc": "", "last_utc": ""},
        "broken_lines": 0,
    }


def report(root: Optional[Any] = None) -> Dict[str, Any]:
    """Gli aggregati del registro, in una forma che sopravvive a ``json.dumps``.

    Con zero valutazioni non si inventa niente: medie ``None``, distribuzione a
    zero, e la frase "la correlazione non e' conclusiva" gia' dentro ``note``.
    Un registro che non si legge (permessi, file troncato in modo irrecuperabile)
    da' lo stesso report vuoto con ``error``, non un'eccezione: chi guarda la
    vista vede zeri, non una pagina rotta.
    """
    path = register_path(root)
    try:
        stat = path.stat()
        key = (str(path), int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        key = (str(path), 0, 0)
    cached = _REPORT_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        data = _build_report(path)
    except Exception as exc:  # noqa: BLE001 - un registro rotto risponde zeri
        degrade.note("feedback", exc)
        data = _empty_report(path)
        data["error"] = f"{type(exc).__name__}: {exc}"
    _REPORT_CACHE.clear()
    _REPORT_CACHE[key] = data
    return data


def report_text(data: Mapping[str, Any]) -> str:
    """Il referto per chi legge il terminale: italiano, numeri, e la cautela."""
    lines: List[str] = []
    count = int(data.get("count") or 0)
    mean = _number(data.get("mean"))
    if count and mean is not None:
        lines.append(f"Giudizi umani: {count} - media {mean:.2f}/5")
    else:
        lines.append("Giudizi umani: nessuno nel registro")
    distribution = data.get("distribution") or {}
    lines.append(
        "Distribuzione: "
        + "  ".join(f"{value}:{distribution.get(str(value), 0)}" for value in RATINGS)
    )
    for title, name, order in (
        ("Per esito", "by_outcome", OUTCOMES),
        ("Per equilibrio", "by_balance", BALANCES),
    ):
        groups = data.get(name) or {}
        parts: List[str] = []
        for key in order:
            item = groups.get(key) or {}
            group_n = int(item.get("n") or 0)
            if not group_n:
                continue
            group_mean = _number(item.get("mean"))
            shown = f"{group_mean:.2f}" if group_mean is not None else "-"
            parts.append(f"{key} n={group_n} media {shown}")
        lines.append(f"{title}: " + (", ".join(parts) if parts else "nessuna valutazione"))
    mape = data.get("mape") or {}
    mape_n = int(mape.get("n") or 0)
    if mape_n:
        percent = _number(mape.get("mean_percent")) or 0.0
        bias = _number(mape.get("mean_error_sec")) or 0.0
        lines.append(
            f"Stima/consuntivo: MAPE {percent:.1f}% su {mape_n} "
            f"{'lavoro' if mape_n == 1 else 'lavori'} "
            f"(errore medio {bias:+.1f} s, positivo = stima alta)"
        )
    else:
        lines.append("Stima/consuntivo: nessuna coppia stimata/misurata")
    correlation = data.get("correlation") or {}
    n = int(correlation.get("n") or 0)
    value = _number(correlation.get("pearson"))
    note = str(correlation.get("note") or "")
    if value is None:
        lines.append(f"Correlazione equilibrio/voto: r non calcolabile su n={n} - {note}")
    else:
        lines.append(f"Correlazione equilibrio/voto: r={value:+.2f} su n={n} - {note}")
    caveat = str(correlation.get("caveat") or "")
    if caveat:
        lines.append(caveat)
    broken = int(data.get("broken_lines") or 0)
    if broken:
        lines.append(f"Righe illeggibili saltate: {broken}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# il contesto che accompagna il voto
# --------------------------------------------------------------------------- #


def _codes(items: Any) -> List[str]:
    """I codici delle violazioni, senza doppioni e nell'ordine in cui arrivano."""
    found: List[str] = []
    for item in items if isinstance(items, (list, tuple)) else []:
        code = ""
        if isinstance(item, Mapping):
            code = str(item.get("code") or "").strip()
        elif isinstance(item, str):
            code = item.strip()
        if code and code not in found:
            found.append(code)
    return found


def outcome_from_report(report: Any) -> str:
    """L'esito che il report dichiara, senza promettere piu' di quanto ha fatto.

    L'ordine e' quello dei fatti: un ``ok: false`` e' un fallimento, una guardia
    che non passa pure, un master annunciato ma assente e' ``incomplete``. Solo
    l'esito ``done`` richiede tutte le prove insieme.
    """
    data = report if isinstance(report, Mapping) else {}
    if not data:
        return "incomplete"
    suggested = str(data.get("outcome_suggested") or "").strip().lower()
    outcome = suggested if suggested in OUTCOMES else ""
    if not outcome:
        if data.get("ok") is False:
            outcome = "failed"
        else:
            guard_section = data.get("guard")
            if (
                isinstance(guard_section, Mapping)
                and not guard_section.get("skipped")
                and guard_section.get("ok") is False
            ):
                outcome = "failed"
            verification = data.get("verification")
            if (
                outcome != "failed"
                and isinstance(verification, Mapping)
                and verification
                and verification.get("ok") is False
            ):
                outcome = "failed"
            if outcome != "failed" and data.get("delivered") is False:
                outcome = "failed"
            if not outcome:
                outcome = "done"
    master = str(data.get("master") or "").strip()
    if not master:
        return "incomplete" if outcome == "done" else outcome
    try:
        if not Path(master).is_file():
            return "incomplete"
    except OSError:
        return "incomplete"
    return outcome


def context_from_report(report: Any) -> Dict[str, Any]:
    """Il contesto del record, letto dal referto che il lavoro ha gia' scritto.

    Nessun campo si inventa: se il report non ha la stima, il contesto porta
    ``None`` e la MAPE salta quella riga invece di contare uno zero.
    """
    data = report if isinstance(report, Mapping) else {}
    estimate = data.get("estimate") if isinstance(data.get("estimate"), Mapping) else {}
    effects_block = estimate.get("effects") if isinstance(estimate.get("effects"), Mapping) else {}
    coherence = data.get("coherence") if isinstance(data.get("coherence"), Mapping) else {}
    metrics = coherence.get("metrics") if isinstance(coherence.get("metrics"), Mapping) else {}
    font_block = data.get("font") if isinstance(data.get("font"), Mapping) else {}
    timings = data.get("timings") if isinstance(data.get("timings"), Mapping) else {}

    seconds = [_number(value) for value in timings.values()]
    seconds = [value for value in seconds if value is not None]
    # Il tempo del giudizio e' quello **della macchina**: sommare tutte le chiavi
    # di `timings` contava due volte (`total` e' gia' il totale) e ci infilava
    # dentro le attese ai cancelli. Misurato il 2026-11-06 su un lavoro vero:
    # 1457.7 s invece di 706.9, e 526.9 s di macchina vera.
    machine = _number(timings.get("machine_total"))
    waiting = _number(timings.get("waiting_total"))
    if machine is not None:
        source = "machine_total"
    else:
        # Report vecchio, senza i campi nuovi: si ricava dal totale dichiarato,
        # oppure (ultima risorsa) dalla somma degli stadi, che e' il calcolo che
        # c'era prima. La riga dichiara da dove viene il numero invece di farlo
        # sembrare misurato.
        declared = _number(timings.get("total"))
        waiting = waiting if waiting is not None else 0.0
        if declared is not None:
            machine = max(0.0, declared - waiting)
            source = "total_minus_waiting"
        else:
            machine = round(sum(seconds), 3) if seconds else None
            source = "stage_sum" if seconds else "unknown"
    actual = round(machine, 3) if machine is not None else None

    guard_block = data.get("guard") if isinstance(data.get("guard"), Mapping) else {}
    guard_violations = guard_block.get("violations")
    coherence_violations = coherence.get("violations")
    violations = _codes(
        (list(guard_violations) if isinstance(guard_violations, (list, tuple)) else [])
        + (list(coherence_violations) if isinstance(coherence_violations, (list, tuple)) else [])
    )

    balance = str(metrics.get("balance") or "ok").strip().lower()
    if balance not in BALANCE_AXIS:
        balance = "ok"

    shots = _number(metrics.get("shots"))
    effects = _number(effects_block.get("count"))
    duration = _number(data.get("timeline_sec"))
    estimate_seconds = _number(estimate.get("seconds"))

    return {
        "outcome": outcome_from_report(data),
        "balance": balance,
        "violations": violations,
        "estimate_seconds": estimate_seconds,
        "actual_seconds": actual,
        "waiting_seconds": round(waiting, 3) if waiting is not None else None,
        "actual_source": source,
        "font": str(font_block.get("family") or ""),
        "effects": int(effects) if effects is not None else None,
        "shots": int(shots) if shots is not None else None,
        "duration_sec": duration,
    }


def request_context(workdir: Any) -> Dict[str, Any]:
    """Cosa serve per registrare un voto su un lavoro: job, prompt, contesto.

    La fonte e' ``feedback_request.json`` se c'e' (il contesto e' gia'
    compilato da ``auto``), altrimenti il report nella workdir. Un lavoro senza
    ne' l'uno ne' l'altro produce un contesto vuoto, non un errore.
    """
    directory = Path(workdir)
    request = _read_json(request_path(directory))
    report_data: Dict[str, Any] = {}
    for name in ("auto_report.json", "report.json"):
        report_data = _read_json(directory / name)
        if report_data:
            break
    raw_context = request.get("context")
    context = (
        dict(raw_context)
        if isinstance(raw_context, Mapping)
        else context_from_report(report_data) if report_data else {}
    )
    prompt = str(request.get("prompt") or report_data.get("prompt") or "")
    if not prompt:
        prompt = str(_read_json(directory / "progress.json").get("prompt") or "")
    job_id = str(request.get("job_id") or report_data.get("job_id") or directory.name)
    return {"job_id": job_id, "prompt": prompt, "context": context}


# --------------------------------------------------------------------------- #
# il segnale per la vista
# --------------------------------------------------------------------------- #


def pending(
    workdir: Any,
    context: Optional[Mapping[str, Any]] = None,
    *,
    job_id: Any = "",
    prompt: Any = "",
    refresh: bool = False,
) -> Dict[str, Any]:
    """Legge — o scrive — ``<workdir>/feedback_request.json``.

    Con ``context`` compilato scrive il segnale (``asked: true``) e il contesto,
    cosi' la vista sa che puo' chiedere senza ricostruire niente. Senza
    contesto si limita a leggere: se il file non c'e' risponde ``asked: false``.
    ``refresh`` riscrive un segnale che c'e' gia': serve a ``auto`` quando gira
    di nuovo sulla stessa workdir, perche' il contesto da votare e' quello
    dell'ultimo giro, non del primo. Non blocca e non solleva: una workdir non
    scrivibile e' un ``error`` nel ritorno, non un'eccezione che ferma la vista.
    """
    directory = Path(workdir)
    path = request_path(directory)
    existing = _read_json(path)
    if existing and not (refresh and context is not None):
        return existing
    if context is None:
        return {"asked": False, "created_utc": None, "path": str(path)}
    payload: Dict[str, Any] = {
        "asked": True,
        "created_utc": utc_now(),
        "job_id": str(job_id or directory.name),
        "prompt": str(prompt or ""),
        "context": dict(context),
    }
    try:
        _write_json_atomic(path, payload)
    except Exception as exc:  # noqa: BLE001 - il segnale non e' il lavoro
        degrade.note("feedback", exc)
        return {
            "asked": False,
            "created_utc": None,
            "path": str(path),
            "error": f"{type(exc).__name__}: {exc}",
        }
    return payload


__all__ = [
    "BALANCES",
    "BALANCE_AXIS",
    "CORRELATION_MIN_N",
    "DEFAULT_ROOT_DIR",
    "LOCK_NAME",
    "OUTCOMES",
    "RATINGS",
    "REGISTER_NAME",
    "REQUEST_NAME",
    "ROOT_ENV",
    "SCHEMA",
    "append",
    "context_from_report",
    "latest",
    "make_record",
    "outcome_from_report",
    "pearson",
    "pending",
    "read_all",
    "register_path",
    "report",
    "report_text",
    "request_context",
    "request_path",
    "root_dir",
    "utc_now",
    "validate_rating",
]
