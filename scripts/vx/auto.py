"""vx.auto — one command from a prompt and a folder to a delivered master.

Fixed order, no stage skipped:

    discover -> triage -> decision -> staging -> EDL -> grammar -> Gate 1
    -> render (master loudness) -> master QC -> deliverables -> report

Exit codes are the protocol's, not this module's invention: a usage error is a
``VxError`` (2), a failed Gate 1 or master QC is a ``QCFailure`` (5), a failed
render is a ``RenderFailure`` (6). Nothing is swallowed here; the exception
travels to ``cli.main`` unchanged. The only exception translated is
``GrammarError``, which has no exit code of its own and is reported by
``cmd_auto`` as a usage error with the violations attached.

The source folder is read-only. Every staged file, render and report lives
under ``--workdir``; the master is the only file written outside it, at
``--out``. The sha256 of every input is taken before staging and re-checked at
the end: a changed source is a hard QC failure, never a warning.

Crash injection, for the recovery tests only
--------------------------------------------
Every stage boundary in this module goes through ``_emit`` (or ``_notify``,
which writes the event and then calls the observer). When ``VX_CRASH_AFTER``
is set, the process dies there with ``os._exit(70)``: no ``finally``, no lock
release, no journal close - a real crash, not an exception.

    VX_CRASH_AFTER=qc           die right after the ``qc`` boundary is written
    VX_CRASH_AFTER=render:2     die after the 2nd boundary named ``render``

The value is ``<stage>[:<n>]``; ``n`` defaults to 1. The boundary is written
to ``events.jsonl`` and flushed *before* the exit, so the file says exactly
where the process died and the exit code 70 says it died on purpose. Unset,
``_crash_point`` returns on its first lookup and nothing else runs.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import (
    ask,
    asset_triage,
    contract,
    creative,
    decide,
    decisions,
    degrade,
    deliverables,
    edl_plan,
    efx,
    estimate,
    feedback,
    ffmpeg as ff,
    gates,
    grammar,
    guard,
    ingest,
    keygate,
    lock,
    probe,
    profile_master,
    profiles,
    proxy as proxy_mod,
    qc,
    recipe,
    render,
    resources,
    settings,
    subs,
    textbrief,
)

# The delivery spec the renderer is asked to hit and the QC gate re-measures.
TARGET_LUFS = -14.0
TARGET_TP = -1.0

#: La ricetta: il layer dei suoi settings si scrive qui, dove
#: ``settings.build(settings_file=...)`` lo legge; ``combined`` e' il file che
#: somma il ``--settings`` dell'utente (sotto) e la ricetta (sopra).
RECIPE_SETTINGS_NAME = "recipe_settings.json"
RECIPE_SETTINGS_COMBINED_NAME = "recipe_settings_combined.json"

#: Il cancello dei font: la proposta che ``fontproposal`` scrive, la scelta che
#: la vista deposita e quanto si aspetta una persona prima di decidere da soli.
FONT_PROPOSAL_NAME = "font_proposal.json"
FONT_CHOICE_NAME = "font_choice.json"
FONT_WAIT_DEFAULT = 180.0

#: Il cancello dei fotogrammi chiave: il piano che ``auto`` propone, il segnale
#: che il render e' fermo in attesa, e il record che l'approvazione deposita.
#: ``keyframes.json`` e' nella forma di ``keygate``: chi approva scrive li'.
KEYFRAMES_DIR = "keyframes"
KEYFRAMES_PENDING_NAME = "pending.json"
KEYFRAMES_WAITING_NAME = "waiting.json"
KEYFRAMES_WAIT_SCHEMA = "vx.keygate-wait/1"
KEYFRAMES_PLAN_SCHEMA = "vx.keyframes-plan/1"
KEY_GATE_WAIT_DEFAULT = FONT_WAIT_DEFAULT
#: Tre fotogrammi: la regola di ``keygate`` (sotto tre non si vede un montaggio).
KEYFRAME_PICKS = (0.25, 0.5, 0.75)

#: Le domande del triage che prima finivano in chat. Ora passano da ``vx.ask``:
#: la vista le mostra con le opzioni, e senza vista si prende il default
#: dichiarandolo (``answered_by="default"``), come per gli altri cancelli.
TRIAGE_WAIT_DEFAULT = 180.0

#: Un cancello aspetta solo se qualcuno puo' rispondere. La vista (``vx serve``)
#: lascia questo timbro nella workdir ogni volta che serve ``/api/state``; il
#: lavoro lo legge e decide se aprire il cancello. Un timbro vecchio non conta:
#: la soglia e' ``serve.client_ttl_sec`` (90 s), cosi' una vista chiusa smette
#: di far aspettare un lavoro nuovo.
VIEW_CLIENT_NAME = "view_client.json"
VIEW_CLIENT_SCHEMA = "vx.viewclient/1"
VIEW_CLIENT_TTL_DEFAULT = 90.0

#: Lo stato dei cancelli che la vista disegna (``gates[].open``/``wait_sec``):
#: chi aspetta, da quando, fino a quando, e con quale scelta si e' chiuso.
GATE_STATUS_NAME = "gate_wait.json"
GATE_STATUS_SCHEMA = "vx.gatewait/1"

TRIAGE_QUESTIONS: List[Dict[str, Any]] = [
    {
        "id": "target_format",
        "question": "In che formato esce il video?",
        "options": [
            {"value": "9:16", "label": "9:16 verticale", "detail": "Reel, TikTok, Shorts"},
            {"value": "16:9", "label": "16:9 orizzontale", "detail": "YouTube, vlog"},
            {"value": "1:1", "label": "1:1 quadrato", "detail": "feed"},
            {"value": "4:5", "label": "4:5 verticale stretto", "detail": "carosello"},
        ],
        "default": "9:16",
    },
    {
        "id": "hook",
        "question": "Cosa metto nei primi 3 secondi?",
        "options": [
            {"value": "testo", "label": "testo", "detail": "una card in apertura"},
            {"value": "auto", "label": "auto", "detail": "decide il montaggio"},
            {"value": "nessuno", "label": "nessuno", "detail": "si parte col girato"},
        ],
        "default": "auto",
    },
    {
        "id": "subtitles",
        "question": "Che sottotitoli vuoi?",
        "options": [
            {"value": "karaoke", "label": "karaoke", "detail": "parola per parola"},
            {"value": "clean", "label": "clean", "detail": "righe pulite"},
            {"value": "nessuno", "label": "nessuno", "detail": "nessun testo"},
        ],
        "default": "karaoke",
    },
]


def view_client_recent(
    workdir: Any,
    *,
    ttl: Optional[float] = None,
    now: Optional[float] = None,
) -> bool:
    """Vero se la vista ha chiesto ``/api/state`` per questa workdir da poco.

    Il timbro e' ``<workdir>/view_client.json``, scritto da ``vx serve``; non e'
    un'ipotesi sul pannello: un ``progress.html`` esiste anche quando nessuno lo
    guarda. Un file assente, illeggibile o piu' vecchio di ``ttl`` risponde
    ``False`` — e allora il cancello non deve far aspettare nessuno.
    """
    if ttl is None:
        try:
            ttl = float(settings.get("serve.client_ttl_sec", VIEW_CLIENT_TTL_DEFAULT))
        except (TypeError, ValueError):
            ttl = VIEW_CLIENT_TTL_DEFAULT
    try:
        limit = float(ttl)
    except (TypeError, ValueError):
        limit = VIEW_CLIENT_TTL_DEFAULT
    if limit <= 0:
        return False
    path = Path(workdir) / VIEW_CLIENT_NAME
    record: Any = {}
    try:
        record = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        record = {}
    stamp: Optional[float] = None
    if isinstance(record, Mapping):
        try:
            stamp = float(record.get("at_epoch"))
        except (TypeError, ValueError):
            stamp = None
    if stamp is None:
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return False
    current = time.time() if now is None else float(now)
    age = current - stamp
    # Un orologio leggermente avanti non e' un cliente del futuro: la tolleranza
    # copre solo lo scarto fra i due processi, non una data inventata.
    return -5.0 <= age <= limit


def view_is_attached(
    workdir: Any,
    *,
    panel: Any = None,
    view_url: str = "",
) -> bool:
    """C'e' qualcuno che puo' rispondere a un cancello di questo lavoro?

    Due casi soli: una vista esplicitamente agganciata (``--watch`` ha gia' un
    URL) oppure un client che ha letto lo stato da poco. Il pannello da solo non
    basta: scrive un HTML anche quando la pagina non e' aperta da nessuno.
    """
    if str(view_url or "").strip():
        return True
    # Il pannello e' solo un file: se un client ha letto ``/api/state`` da poco,
    # quella e' la prova che qualcuno puo' rispondere, anche con ``--no-panel``.
    return view_client_recent(workdir)


def _record_gate(
    workdir: Any,
    gate: str,
    *,
    open: bool,
    asked: bool,
    wait_sec: float = 0.0,
    choice: Any = None,
    answered_by: str = "",
    expired: bool = False,
) -> None:
    """Aggiorna ``gate_wait.json``: e' cio' che la vista legge come ``gates``.

    Fail-soft: un timbro che non si scrive non ferma il cancello. La voce resta
    anche da chiusa (``open=False``) perche' il report e la vista devono poter
    dire *come* si e' chiusa, non solo che non aspetta piu'.
    """
    path = Path(workdir) / GATE_STATUS_NAME
    try:
        current = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        current = {}
    gates = dict(current.get("gates") or {}) if isinstance(current, Mapping) else {}
    now = time.time()
    try:
        wait = max(0.0, float(wait_sec))
    except (TypeError, ValueError):
        wait = 0.0
    entry: Dict[str, Any] = {
        "open": bool(open),
        "asked": bool(asked),
        "wait_sec": round(wait, 3),
        "expired": bool(expired),
        "updated_epoch": round(now, 3),
    }
    if open and asked and wait > 0:
        entry["until_epoch"] = round(now + wait, 3)
    if choice is not None:
        entry["choice"] = choice
    if answered_by:
        entry["answered_by"] = answered_by
        entry["by"] = answered_by
    gates[str(gate)] = entry
    try:
        _write_json_atomic(
            path,
            {"schema": GATE_STATUS_SCHEMA, "gates": gates},
        )
    except OSError:
        pass


def _gate_by(value: Any) -> str:
    """Traduce i nomi storici (``default``, ``auto_after_timeout``) in tre soli."""
    text = str(value or "").strip()
    if text == "user":
        return "user"
    if text in ("timeout", "auto_after_timeout"):
        return "timeout"
    return "auto"


def gates_report(
    *,
    questions: Any = None,
    font_section: Any = None,
    key_section: Any = None,
    plan_section: Any = None,
) -> Dict[str, Any]:
    """Il riepilogo dei cancelli: chi ha chiesto, chi ha risposto, quanto.

    ``waited_sec`` e' il tempo realmente speso, non il timeout dichiarato; per
    il triage le tre domande girano in parallelo, quindi vale la piu' lunga.
    ``total_waited_sec`` somma i cancelli in sequenza: e' il numero che dice se
    un giro non interattivo ha davvero aspettato qualcuno.
    """
    question_rows = [
        dict(item) for item in (questions or []) if isinstance(item, Mapping)
    ]
    triage_waited = max(
        [float(item.get("waited_sec") or 0.0) for item in question_rows] or [0.0]
    )
    triage_by = "auto"
    if any(_gate_by(item.get("answered_by")) == "user" for item in question_rows):
        triage_by = "user"
    elif any(bool(item.get("expired")) for item in question_rows):
        triage_by = "timeout"
    triage = {
        "asked": any(bool(item.get("asked")) for item in question_rows),
        "answered_by": triage_by,
        "by": triage_by,
        "waited_sec": round(triage_waited, 3),
        "expired": any(bool(item.get("expired")) for item in question_rows),
        "choice": {
            str(item.get("id") or ""): item.get("value") for item in question_rows
        },
        "questions": question_rows,
    }

    def summary(section: Any, choice_key: str = "choice") -> Dict[str, Any]:
        data = section if isinstance(section, Mapping) else {}
        by = _gate_by(data.get("answered_by") or data.get("selected_by"))
        return {
            "asked": bool(data.get("asked")),
            "answered_by": by,
            "by": by,
            "waited_sec": round(float(data.get("waited_sec") or 0.0), 3),
            "expired": bool(data.get("expired")),
            "choice": data.get(choice_key),
        }

    font = summary(font_section)
    keyframes = summary(key_section)
    plan = summary(plan_section)
    total = (
        float(triage["waited_sec"])
        + float(font["waited_sec"])
        + float(keyframes["waited_sec"])
        + float(plan["waited_sec"])
    )
    return {
        "total_waited_sec": round(total, 3),
        "total_sec": round(total, 3),
        "items": {
            "triage": triage,
            "font": font,
            "keyframes": keyframes,
            "plan": plan,
        },
    }


def loudness_target(document: Any = None) -> tuple[float, float]:
    """(LUFS, dBTP) declared by ``document.profile_ref``, else the -14/-1 default.

    The document is the payload the pipeline was built from (a mapping) or a
    ``Timeline3``-like object. An unknown id is not swallowed: ``profiles.resolve``
    refuses it with ``PROFILE_UNKNOWN``, the same code the document validator
    uses, so a typo can never silently fall back to the social target.
    """
    ref = None
    if isinstance(document, Mapping):
        ref = document.get("profile_ref")
    elif document is not None:
        ref = getattr(document, "profile_ref", None)
    if ref is None or not str(ref).strip():
        return float(TARGET_LUFS), float(TARGET_TP)
    return profile_master.target_for(str(ref))


#: Quando c'e' un narratore, le righe tecniche tacciono: una voce sola.
_NARRATING = False
#: Chi ha gia' prenotato lo stdout (il worker) lo dichiara qui, per la durata
#: della chiamata: non e' una variabile d'ambiente, che resterebbe attaccata a
#: ogni processo che importa il modulo.
_STDOUT_RESERVED = False


def _log(msg: str) -> None:
    if _NARRATING and msg.startswith("[auto]"):
        return
    print(msg, file=sys.stderr, flush=True)


#: Crash injection for the recovery tests: ``<stage>[:<n>]``, see the module
#: docstring. It is a test switch, not a feature: unset, nothing happens here.
CRASH_AFTER_ENV = "VX_CRASH_AFTER"
CRASH_EXIT_CODE = 70

#: Boundaries already seen by ``_crash_point`` in this process, per stage name.
#: Used only to resolve ``render:2``; with the variable unset the dict is never
#: touched, which is also how the test proves the switch is inert.
_CRASH_SEEN: Dict[str, int] = {}


def _crash_point(stage: str) -> None:
    """Die at this stage boundary when ``VX_CRASH_AFTER`` names it.

    Called by ``_emit`` *after* the event is written and flushed, so the log
    on disk ends with the boundary the process died on. ``os._exit`` skips
    every ``finally``, the lock release and the journal close on purpose: the
    test needs a real crash, not a tidy exception.
    """
    spec = os.environ.get(CRASH_AFTER_ENV)
    if not spec:
        return
    name, _, occurrence = spec.partition(":")
    if name.strip() != str(stage):
        return
    try:
        wanted = max(1, int(occurrence.strip() or 1))
    except ValueError:
        wanted = 1
    seen = _CRASH_SEEN.get(stage, 0) + 1
    _CRASH_SEEN[stage] = seen
    if seen >= wanted:
        os._exit(CRASH_EXIT_CODE)


def _emit(log: Optional[Any], stage: str, status: str, detail: str = "") -> None:
    """Write one boundary to the event log; a broken log never breaks a render."""
    if log is not None:
        try:
            log.emit(stage, f"auto.{stage}", status, detail=detail)
        except Exception:  # noqa: BLE001
            pass
    _crash_point(stage)


#: Sotto questa differenza la durata consegnata e' quella chiesta.
DURATION_SHORTFALL_TOLERANCE = 0.5

#: Gli stadi che il profilo dichiara sempre, anche a zero: una voce assente e una
#: voce a zero non sono la stessa cosa quando si legge dove e' andato il tempo.
TIMING_STAGES = (
    "triage",
    "questions",
    "probe",
    "hash",
    "ingest",
    "proxy",
    "audio",
    "analysis",
    "faces",
    "speech",
    "measure_wall",
    "plan",
    "bridge",
    "coherence",
    "plan_choice",
    "creative",
    "font",
    "typography",
    "keyframes",
    "render",
    "qc",
    "deliverables",
)


def _as_seconds(value: Any) -> float:
    """Secondi da un valore qualsiasi, senza inventare: ``0`` se non e' un numero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 0.0
    return seconds if seconds > 0 else 0.0


class _Clock:
    """Wall time per stage, accumulated. Measurement only: no call is reordered.

    Il totale e' quello del processo (``_run_locked``): ``unaccounted`` e' quello
    che nessuno stadio ha rivendicato, cosi' un buco si vede invece di sparire
    dentro la somma. Gli stadi annidati (audio/faces/analysis/speech dentro il
    bridge) si contano a parte e il bridge tiene il proprio totale: le due letture
    servono a dire sia "quanto costa il ponte" sia "quanto costa ogni misura".
    """

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.stages: Dict[str, float] = {}
        #: Secondi passati **aspettando una persona** (cancelli): non sono tempo
        #: di macchina e non devono entrare ne' nella calibrazione della stima
        #: ne' nel giudizio sulla velocita' di un lavoro.
        self.waiting: float = 0.0
        #: Dichiarazioni che non sono tempi (memoria vista, worker, priorita'):
        #: viaggiano con il profilo perche' il report le deve alla stessa riga.
        self.facts: Dict[str, Any] = {}

    @contextmanager
    def time(self, stage: str):
        begin = time.monotonic()
        try:
            yield
        finally:
            self.add(stage, time.monotonic() - begin)

    def add(self, stage: str, seconds: float) -> None:
        self.stages[stage] = self.stages.get(stage, 0.0) + float(seconds)

    def waited(self, seconds: Any) -> None:
        """Dichiara secondi di attesa umana: fuori dal tempo di macchina.

        Il cancello misura quanto ha aspettato (``gates.total_waited_sec``); quel
        numero finisce qui e non dentro uno stadio, altrimenti "quanto costa
        questo lavoro" dipende da quanto il telefono e' rimasto in tasca.
        """
        value = _as_seconds(seconds)
        if value > 0:
            self.waiting += value

    def as_dict(self) -> Dict[str, float]:
        total = time.monotonic() - self.started
        out: Dict[str, float] = {}
        for stage in TIMING_STAGES:
            out[stage] = round(self.stages.get(stage, 0.0), 3)
        for stage, seconds in self.stages.items():
            if stage not in out:
                out[stage] = round(seconds, 3)
        out["total"] = round(total, 3)
        claimed = sum(
            seconds
            for stage, seconds in self.stages.items()
            if stage != "measure_wall"
        )
        out["unaccounted"] = round(max(0.0, total - claimed), 3)
        # Il conto che serve davvero: quanto ha lavorato la macchina, e quanto
        # ha aspettato una persona. `total` resta il tempo di parete.
        waiting = min(round(self.waiting, 3), round(total, 3))
        out["waiting_total"] = waiting
        out["machine_total"] = round(max(0.0, total - waiting), 3)
        return out


def duration_shortfall(requested: Any, delivered: Any) -> Optional[Dict[str, Any]]:
    """The gap between what was asked and what the timeline can deliver.

    Una richiesta di 60 s su materiale che ne regge 21 non e' un successo: e' una
    consegna piu' corta del chiesto, e va dichiarata con i due numeri.
    """
    try:
        want = float(requested)
        got = float(delivered)
    except (TypeError, ValueError):
        return None
    if want <= 0 or got <= 0:
        return None
    gap = want - got
    if gap <= DURATION_SHORTFALL_TOLERANCE:
        return None
    return {
        "requested_sec": round(want, 3),
        "delivered_sec": round(got, 3),
        "shortfall_sec": round(gap, 3),
    }


def snap_to_frames(seconds: Any, fps: Any) -> float:
    """``N`` seconds on the frame grid: ``round(N * fps) / fps``.

    Una durata chiesta e' un numero di fotogrammi, non un numero decimale: 15 s a
    25 fps sono 375 frame e a 30 fps sono 450. Il piano usa questo valore, cosi'
    la fine della timeline cade su un frame intero invece che su un istante che
    l'encoder deve arrotondare per conto suo.
    """
    try:
        value = float(seconds)
        rate = float(fps)
    except (TypeError, ValueError):
        return 0.0
    if value <= 0 or rate <= 0:
        return 0.0
    return round(value * rate) / rate


def frame_count(seconds: Any, fps: Any) -> int:
    """Quanti frame interi copre ``seconds`` a ``fps`` (0 se non misurabile)."""
    try:
        value = float(seconds)
        rate = float(fps)
    except (TypeError, ValueError):
        return 0
    if value <= 0 or rate <= 0:
        return 0
    return int(round(value * rate))


def duration_tail(
    requested: Any,
    actual: Any,
    fps: Any,
    *,
    material_sec: Any = None,
) -> Optional[Dict[str, Any]]:
    """Il taglio della coda: chiesto, consegnato, frame, e perche'.

    La coda e' l'unico segmento che si puo' accorciare (o allungare) per chiudere
    la timeline su un frame intero: la griglia di battuta resta quella, cambia
    solo l'ultima battuta. Quando il materiale non arriva alla richiesta il
    ``note`` lo dice con il massimo raggiungibile, invece di far finta che la
    timeline sia quella chiesta.
    """
    try:
        want = float(requested)
        got = float(actual)
        rate = float(fps)
    except (TypeError, ValueError):
        return None
    if want <= 0 or got <= 0 or rate <= 0:
        return None
    want_frames = frame_count(want, rate)
    got_frames = frame_count(got, rate)
    target = snap_to_frames(want, rate)
    reachable = abs(got - target) <= (0.5 / rate) + 1e-6
    note = ""
    if not reachable:
        note = (
            "materiale insufficiente: massimo raggiungibile {got:.3f}s "
            "({frames} frame), richiesti {want:g}s"
        ).format(got=got, frames=got_frames, want=want)
        if material_sec is not None:
            try:
                note = (
                    "materiale insufficiente: massimo raggiungibile {got:.3f}s "
                    "({frames} frame); materiale utilizzabile {mat:.3f}s, "
                    "richiesti {want:g}s"
                ).format(
                    got=got,
                    frames=got_frames,
                    mat=float(material_sec),
                    want=want,
                )
            except (TypeError, ValueError):
                pass
    elif got_frames > want_frames:
        note = (
            "ultima battuta allungata di {delta:.3f}s per chiudere a {frames} "
            "frame"
        ).format(delta=got - want, frames=got_frames)
    elif got_frames < want_frames:
        note = (
            "ultima battuta accorciata di {delta:.3f}s per chiudere a {frames} "
            "frame"
        ).format(delta=want - got, frames=got_frames)
    else:
        note = "ultima battuta sulla griglia: {frames} frame esatti".format(
            frames=got_frames
        )
    if material_sec is not None and reachable:
        try:
            note += f"; materiale utilizzabile {float(material_sec):.3f}s"
        except (TypeError, ValueError):
            pass
    return {
        "requested": round(want, 6),
        "actual": round(got, 6),
        "frames": got_frames,
        "reachable": reachable,
        "note": note,
    }


def timeline_declaration(
    requested: Any,
    actual: Any,
    fps: Any,
    *,
    material_sec: Any = None,
) -> Optional[Dict[str, Any]]:
    """Il blocco ``timeline`` del report, con la coda dichiarata.

    ``{"tail": {requested, actual, frames, reachable, note}, "frame_target_sec": N}``
    e' la forma che il report scrive: la coda e' l'ultima battuta, non un numero
    nascosto dentro il piano.
    """
    tail = duration_tail(requested, actual, fps, material_sec=material_sec)
    if tail is None:
        return None
    return {
        "tail": tail,
        "frame_target_sec": round(snap_to_frames(requested, fps), 6),
    }


def final_outcome(report: Any) -> str:
    """L'esito del lavoro, sempre valorizzato.

    ``delivered`` quando master e verifiche passano; ``failed`` quando la
    consegna e' fermata (guardia, verifica, oppure ``ok: false``); ``incomplete``
    quando il lavoro si e' interrotto prima di decidere. Nessun caso in cui un
    controllo fallisce e il file risulta consegnato senza una spiegazione.
    """
    data = report if isinstance(report, Mapping) else {}
    if data.get("ok") is False:
        return "failed"
    if data.get("delivered") is False:
        return "failed"
    verification = data.get("verification")
    if isinstance(verification, Mapping) and verification.get("ok") is False:
        return "failed"
    if data.get("delivered") is True:
        return "delivered"
    return "incomplete"


def _notify(
    progress: Optional[Callable[[float, str], None]],
    percent: float,
    stage: str,
    *,
    log: Optional[Any] = None,
    status: str = "done",
    detail: str = "",
) -> None:
    """Call the observer, swallowing anything it raises: a broken observer must
    never break a render. The same boundary is written to the event log first, so
    a crash during a stage still leaves that stage's ``start`` on disk."""
    _emit(log, stage, status, detail)
    if progress is None:
        return
    try:
        progress(percent, stage)
    except Exception:  # noqa: BLE001
        pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _staged_path(result: Any) -> Path:
    """``IngestResult`` names the staged file ``staged_path``; keep ``path`` as a fallback."""
    value = getattr(result, "staged_path", None) or getattr(result, "path", None)
    if not value:
        raise contract.VxError("staging non ha restituito un percorso")
    return Path(value)


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _stream_durations(master: Any) -> Dict[str, float]:
    """Durata misurata per tipo di stream (prima occorrenza di ogni tipo)."""
    proc = ff.run_fp(
        [
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,duration",
            "-of",
            "json",
            str(master),
        ]
    )
    if not proc.ok:
        return {}
    try:
        data = json.loads(proc.stdout or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    out: Dict[str, float] = {}
    for item in data.get("streams") or ():
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("codec_type") or "")
        try:
            value = float(item.get("duration"))
        except (TypeError, ValueError):
            continue
        if kind and value > 0 and kind not in out:
            out[kind] = value
    return out


def _container_duration(master: Any) -> Optional[float]:
    proc = ff.run_fp(
        ["-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(master)]
    )
    if not proc.ok or not proc.stdout:
        return None
    try:
        return float(proc.stdout.strip().splitlines()[0])
    except (TypeError, ValueError, IndexError):
        return None


def conform_master_duration(master: Any, duration: Any, fps: Any) -> Dict[str, Any]:
    """Chiude il master sul frame della timeline, senza ricodificare il video.

    L'audio in AAC puo' superare l'ultimo frame (padding dell'encoder): il file
    risulterebbe lungo 15.1 s su una timeline da 15.0 e la verifica lo
    boccierebbe per un difetto del contenitore. Qui il taglio e' sul contenitore:
    stream copy, nessuna perdita, e il risultato viene rimisurato. Se il video e'
    gia' intero bastano ``-t`` + ``-shortest``; altrimenti ``-t`` basta. Non
    inventa durata: se il file resta fuori tolleranza lo dichiara.
    """
    answer: Dict[str, Any] = {"applied": False, "why": ""}
    try:
        want = float(duration)
        rate = float(fps)
    except (TypeError, ValueError):
        return answer
    if want <= 0 or rate <= 0:
        return answer
    before = _container_duration(master)
    if before is None:
        answer["why"] = "durata del contenitore non misurabile"
        return answer
    tolerance = 0.5 / rate
    answer["container_before_sec"] = round(before, 3)
    if abs(before - want) <= tolerance + 1e-6:
        answer["container_after_sec"] = round(before, 3)
        answer["why"] = "già sul frame: nessun taglio"
        return answer
    streams = _stream_durations(master)
    video_end = streams.get("video")
    temporary = Path(str(master)).with_name(Path(str(master)).stem + ".conform.mp4")
    args: List[Any] = [
        "-i",
        str(master),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c",
        "copy",
        "-t",
        f"{want:.6f}",
    ]
    if video_end is not None and video_end >= want - tolerance:
        # Il video e' gia' intero: -shortest taglia il padding audio sul suo
        # ultimo frame invece di lasciare il contenitore piu' lungo.
        args += ["-shortest"]
    args += ["-movflags", "+faststart", str(temporary)]
    proc = ff.run_ff(args, loglevel="error")
    if not proc.ok or not temporary.is_file() or temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        answer["why"] = f"taglio coda non eseguito: {proc.tail(3)}"
        return answer
    os.replace(temporary, master)
    after = _container_duration(master)
    answer["container_after_sec"] = round(after, 3) if after is not None else None
    answer["applied"] = True
    answer["why"] = (
        f"coda audio tagliata a {want:.3f}s (era {before:.3f}s)"
        if after is not None and abs(after - want) <= tolerance + 1e-6
        else f"coda tagliata a {want:.3f}s, il contenitore misura {after}"
    )
    return answer


def fill_upscale(src_size: Sequence[int], target_size: Sequence[int]) -> float:
    """How far the source has to grow to cover the canvas, declared in the report.

    The cover scale needs ``max(width ratio, height ratio)``; a source that
    already covers the target needs no upscale at all, so the value is clamped
    to exactly ``1.0`` instead of being reported as a fraction below it. The
    arithmetic is pure Python: no probe, no subprocess, no invented number.
    """
    src_w, src_h = (int(value) for value in src_size)
    target_w, target_h = (int(value) for value in target_size)
    if src_w <= 0 or src_h <= 0:
        raise contract.VxError("fill: geometria sorgente non positiva")
    factor = max(target_w / float(src_w), target_h / float(src_h))
    if factor <= 1.0:
        return 1.0
    return round(factor, 3)


def _fill_declaration(
    plan: Any, media_info_map: Mapping[str, Mapping[str, Any]], mode: str
) -> List[Dict[str, Any]]:
    """One entry per layer-0 (a_roll) clip: policy, geometry, declared upscale."""
    target = [int(value) for value in plan.resolution]
    entries: List[Dict[str, Any]] = []
    for track in getattr(plan, "video_tracks", ()):
        for clip in getattr(track, "clips", ()):
            if int(getattr(clip, "layer", 0)) != 0:
                continue
            media = media_info_map.get(clip.source_id)
            if media is None:
                raise contract.VxError(
                    f"fill: nessun media_info per {clip.source_id!r}"
                )
            src = [int(media.get("width") or 0), int(media.get("height") or 0)]
            entries.append(
                {
                    "clip_id": clip.clip_id,
                    "mode": mode,
                    "src_size": src,
                    "target_size": list(target),
                    "upscale": fill_upscale(src, target),
                }
            )
    return entries


def _fill_mode(value: Any) -> str:
    """A usage error (exit 2), not a render failure: the flag is wrong, not ffmpeg."""
    mode = str(value or "fit").strip().lower()
    if mode not in render.FILL_POLICIES:
        raise contract.VxError(
            f"--fill {mode!r} non in {'/'.join(render.FILL_POLICIES)}"
        )
    return mode


def _resolve_workdir(args: Any) -> Path:
    explicit = getattr(args, "workdir", None)
    if explicit:
        return Path(explicit)
    out = Path(args.out)
    return out.parent / f"{out.stem}-auto"


def progress_path(args: Any) -> Path:
    """Il file ``-progress`` di ffmpeg per questo lavoro.

    Sta accanto al grafo del render (``<workdir>/render/``) perche' e' li' che
    ``render_edl`` scrive i suoi passaggi; ``vx serve`` lo legge per dire a che
    punto e' il render invece di stimarlo.
    """
    return _resolve_workdir(args) / "render" / "ffmpeg-progress.txt"


#: Cosa dichiara la guardia quando la si salta di proposito.
GUARD_SKIP_WHY = "--no-guard: guardia di consegna saltata su richiesta"


def run_guard(args: Any, workdir: Any) -> Dict[str, Any]:
    """L'audit di ``vx.guard`` sulla workdir, nella forma che va nel report.

    La guardia e' un giudizio sulla consegna, non un passo del render: non
    solleva mai. Se non si puo' eseguire lo dichiara ``skipped`` col motivo,
    perche' un audit rotto non trasforma un video in un fallimento.
    """
    if bool(getattr(args, "no_guard", False)):
        return {
            "ok": True,
            "skipped": True,
            "why": GUARD_SKIP_WHY,
            "violations": [],
            "warnings": [],
        }
    try:
        audit = guard.audit(Path(workdir))
    except Exception as exc:  # noqa: BLE001 - l'audit non e' la consegna
        degrade.note("guard", exc)
        return {
            "ok": True,
            "skipped": True,
            "degraded": True,
            "why": f"guardia non eseguibile: {type(exc).__name__}: {exc}",
            "violations": [],
            "warnings": [],
        }
    violations = [dict(item) for item in audit.get("violations") or []]
    warnings = [dict(item) for item in audit.get("warnings") or []]
    ok = bool(audit.get("ok"))
    return {
        "ok": ok,
        "skipped": False,
        "why": "" if ok else "il lavoro non passa l'audit: non e' consegnabile",
        "violations": violations,
        "warnings": warnings,
        "dir": str(audit.get("dir") or workdir),
        "signature": list(audit.get("signature") or []),
        "exit_code": int(audit.get("exit_code") or 0),
    }


def apply_guard(
    report: Dict[str, Any], section: Mapping[str, Any]
) -> Dict[str, Any]:
    """Scrive l'esito della guardia nel report e decide se il lavoro e' ``done``.

    Il master che esiste e il lavoro consegnabile sono due cose diverse: con
    violazioni il report dice ``outcome_suggested: "failed"`` e porta il motivo,
    senza fermare il processo. ``delivered`` e' la sola chiave che risponde alla
    domanda "questo lavoro si puo' consegnare?".
    """
    report["guard"] = dict(section)
    delivered = bool(section.get("skipped") or section.get("ok"))
    report["delivered"] = delivered
    report["outcome"] = "delivered" if delivered else "failed"
    if delivered:
        report["outcome_suggested"] = "done"
        return report
    report["outcome_suggested"] = "failed"
    violations = [
        item for item in section.get("violations") or [] if isinstance(item, Mapping)
    ]
    codes = ", ".join(str(item.get("code")) for item in violations if item.get("code"))
    count = len(violations)
    reason = (
        f"guardia: {count} violazion{'e' if count == 1 else 'i'}"
        + (f" ({codes})" if codes else "")
        + " — il lavoro non e' consegnabile"
    )
    report.setdefault("failures", []).append(reason)
    return report


def guard_line(section: Any) -> str:
    """La riga di narrazione: chiara, e solo quando c'e' qualcosa da dire."""
    if not isinstance(section, Mapping):
        return ""
    if section.get("skipped"):
        return f"Guardia: saltata ({section.get('why') or '--no-guard'})"
    if section.get("ok"):
        return ""
    count = len(section.get("violations") or [])
    if count == 1:
        return "Guardia: 1 violazione, il lavoro non e' consegnabile"
    if count > 1:
        return f"Guardia: {count} violazioni, il lavoro non e' consegnabile"
    return "Guardia: il lavoro non e' consegnabile"


def write_feedback_request(
    report_data: Any, workdir: Any, prompt: Any = ""
) -> Dict[str, Any]:
    """Lascia nella workdir il contesto per chiedere un voto, senza fermare niente.

    ``feedback_request.json`` porta quello che il lavoro ha gia' misurato
    (esito, equilibrio, violazioni, stima e consuntivo, font, effetti, shot,
    durata): la vista puo' chiedere ``1..5`` senza ricostruire il report e senza
    che questo giro tocchi il codice. Non blocca, non apre console, e un
    registro irraggiungibile resta un ``error`` nel ritorno.
    """
    context = feedback.context_from_report(report_data)
    return feedback.pending(
        workdir,
        context,
        job_id=Path(workdir).name,
        prompt=str(prompt or ""),
        refresh=True,
    )


#: Il prefisso dell'asset_id della traccia passata con ``--music``: non e' un
#: percorso relativo alla cartella asset (il brano puo' stare ovunque), quindi
#: non puo' chiamarsi come un file scoperto.
MUSIC_ASSET_PREFIX = "music:"


def _music_asset_id(path: Path) -> str:
    return MUSIC_ASSET_PREFIX + path.name


def _music_asset(path: Path, *, order_index: int = 10**9) -> asset_triage.AssetInfo:
    """La traccia come asset del pool: si legge, non si scrive (regola zero).

    Il file non deve stare dentro ``--assets``: ``--music`` e' un percorso
    esplicito del brano. L'id porta un prefisso per non confondersi con i file
    scoperti e il ruolo e' gia' ``music``, cosi' il triage non deve indovinare.
    """
    try:
        info = probe.probe_asset(str(path))
    except Exception as exc:  # noqa: BLE001 - un file illeggibile e' un errore d'uso
        raise contract.VxError(
            f"--music: {path} non leggibile come audio "
            f"({type(exc).__name__}: {exc})"
        ) from exc
    if str(getattr(info, "kind", "")) != "audio" or not bool(
        getattr(info, "has_audio", False)
    ):
        raise contract.VxError(
            f"--music: {path} non e' una traccia audio "
            f"(tipo {getattr(info, 'kind', 'sconosciuto')!r})"
        )
    duration = float(getattr(info, "duration", 0.0) or 0.0)
    if not math.isfinite(duration) or duration <= 0:
        raise contract.VxError(f"--music: {path} ha durata zero: non e' un brano")
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return asset_triage.AssetInfo(
        asset_id=_music_asset_id(path),
        kind="audio",
        path=str(path),
        duration_sec=duration,
        has_audio=True,
        sha256=_sha256(path),
        size=size,
        role="music",
        order_index=order_index,
    )


def measure_music_bpm(path: Path) -> Dict[str, Any]:
    """BPM misurato dalla traccia, con il metodo che l'ha misurato.

    ``bpm`` resta ``None`` quando la griglia non ha ne' tempo ne' battute: un
    numero inventato sarebbe peggio di un montaggio senza griglia. La funzione
    non solleva sui problemi di decodifica: il chiamante deve poter dichiarare
    ``bpm_source: "unavailable"`` invece di morire su una traccia strana.
    """
    try:
        grid = grammar.beat_grid(str(path))
    except Exception as exc:  # noqa: BLE001 - la misura mancante e' un dato
        return {
            "bpm": None,
            "measured": False,
            "method": f"error:{type(exc).__name__}",
            "confidence": 0.0,
            "beats": 0,
            "duration": None,
            "note": (
                "BPM non misurabile dalla traccia "
                f"({type(exc).__name__}: {exc}): griglia spenta, taglio sul contenuto"
            ),
        }
    bpm = float(getattr(grid, "bpm", 0.0) or 0.0)
    beats = len(getattr(grid, "beats", ()) or ())
    method = str(getattr(grid, "method", "") or "")
    if bpm <= 0 or beats < 2:
        why = "nessun tempo" if bpm <= 0 else f"{beats} battute"
        return {
            "bpm": None,
            "measured": False,
            "method": method,
            "confidence": round(float(getattr(grid, "confidence", 0.0) or 0.0), 3),
            "beats": beats,
            "duration": round(float(getattr(grid, "duration", 0.0) or 0.0), 3),
            "note": (
                f"BPM non misurabile dalla traccia ({why}, metodo {method or 'nessuno'}): "
                "griglia spenta, il montaggio segue il contenuto"
            ),
        }
    return {
        "bpm": round(bpm, 2),
        "measured": True,
        "method": method,
        "confidence": round(float(getattr(grid, "confidence", 0.0) or 0.0), 3),
        "beats": beats,
        "duration": round(float(getattr(grid, "duration", 0.0) or 0.0), 3),
        "note": "",
    }


def _declared_bpm(args: Any) -> Optional[float]:
    raw = getattr(args, "bpm", None)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise contract.VxError(f"--bpm deve essere un numero > 0, ricevuto {raw!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise contract.VxError(f"--bpm deve essere un numero > 0, ricevuto {raw!r}")
    return value


def _music_plan(args: Any) -> Dict[str, Any]:
    """Il brano richiesto, il BPM e la sua provenienza, prima del triage.

    Precedenza: ``--bpm`` dichiarato vince sulla traccia; senza, vince la misura
    della traccia; se la traccia non e' misurabile il BPM e' ``None`` e la
    provenienza ``unavailable`` (la griglia resta spenta e il report lo dice).
    Il file si legge soltanto: nessuna scrittura, nessuna copia in ``--assets``.
    """
    declared = _declared_bpm(args)
    raw = getattr(args, "music", None)
    section: Dict[str, Any] = {
        "requested": bool(raw),
        "path": None,
        "duration": None,
        "bpm": None,
        "bpm_source": "declared" if declared is not None else "not_requested",
        "declared_bpm": declared,
        "measured": None,
        "method": None,
        "confidence": None,
        "beats": None,
        "sha256": None,
        "read_only": True,
        "replaced": [],
        "note": "",
        "asset": None,
    }
    if not raw:
        if declared is not None:
            section.update({"bpm": declared, "bpm_source": "declared"})
        return section

    path = Path(str(raw)).expanduser()
    if not path.is_file():
        raise contract.VxError(
            f"--music: file inesistente: {path} "
            "(il brano si legge soltanto: nessun master scritto)"
        )
    asset = _music_asset(path)
    measured = measure_music_bpm(path)
    section.update(
        {
            "path": str(path),
            "duration": round(float(asset.duration_sec), 3),
            "sha256": asset.sha256 or _sha256(path),
            "measured_bpm": measured.get("bpm"),
            "measured": bool(measured.get("measured")),
            "method": measured.get("method"),
            "confidence": measured.get("confidence"),
            "beats": measured.get("beats"),
            "note": str(measured.get("note") or ""),
            "asset": asset,
        }
    )
    if declared is not None:
        section.update({"bpm": declared, "bpm_source": "declared"})
        section["note"] = (
            f"BPM dichiarato {declared:g}: vince sulla traccia "
            f"(misurata {measured.get('bpm') if measured.get('bpm') else 'non misurabile'})"
        )
    elif measured.get("bpm"):
        section.update({"bpm": measured["bpm"], "bpm_source": "music"})
    else:
        section.update({"bpm": None, "bpm_source": "unavailable"})
    return section


def _discover(
    args: Any, music: Optional[Mapping[str, Any]] = None
) -> List[asset_triage.AssetInfo]:
    try:
        assets = asset_triage.discover_assets(
            args.assets, manifest=getattr(args, "manifest", None)
        )
    except ValueError as exc:
        raise contract.VxError(f"--assets: {exc}") from exc
    if not assets:
        raise contract.VxError(f"--assets: nessun asset riconosciuto in {args.assets}")
    if music and music.get("requested"):
        # ``--music`` e' il letto musicale, non uno dei tanti: gli altri audio
        # del pool (manifest compreso) non entrano, e il report dice quali.
        resolved = Path(str(music["path"])).resolve()
        kept: List[asset_triage.AssetInfo] = []
        replaced: List[str] = []
        for asset in assets:
            try:
                same = Path(asset.path).resolve() == resolved
            except OSError:
                same = False
            if same or (asset.kind == "audio" and asset.role != "dialogue"):
                replaced.append(asset.asset_id)
                continue
            kept.append(asset)
        if not any(Path(item.path).resolve() == resolved for item in kept):
            asset = music.get("asset")
            if not isinstance(asset, asset_triage.AssetInfo):
                asset = _music_asset(Path(str(music["path"])))
            kept.append(asset)
        if isinstance(music, dict):
            music["replaced"] = replaced
        assets = kept
    return assets


def _rejection_digest(rejected: Sequence[Mapping[str, Any]]) -> str:
    """Reasons with their counts and, for geometry, the numbers behind them.

    An error that only says "nessun video selezionato" hides the cause and
    leaves the operator to guess. This digest names every reason, how many
    assets carry it, and — for the resolution gate — how far the material would
    have to grow and on which canvas.
    """
    counts: Dict[str, int] = {}
    for item in rejected:
        reason = str(item.get("rejection_reason") or "")
        counts[reason] = counts.get(reason, 0) + 1
    parts: List[str] = []
    for reason in sorted(counts):
        count = counts[reason]
        detail = ""
        rows = [
            dict(item.get("details") or {})
            for item in rejected
            if item.get("rejection_reason") == reason
        ]
        if reason == "RESOLUTION_TOO_LOW" and rows:
            needed = max(float(row.get("upscale") or 0.0) for row in rows)
            limit = min(float(row.get("max_upscale") or 0.0) for row in rows)
            sizes = sorted(
                {
                    f"{int(row.get('src_w') or 0)}x{int(row.get('src_h') or 0)}"
                    for row in rows
                }
            )
            canvas = next(
                (
                    f"{int(row.get('target_w') or 0)}x{int(row.get('target_h') or 0)}"
                    for row in rows
                    if row.get("target_w")
                ),
                "",
            )
            detail = (
                f" (upscale {needed:.2f}x > limite {limit:.2f}x"
                + (f", sorgenti {', '.join(sizes)}" if sizes else "")
                + (f" su {canvas}" if canvas else "")
                + ")"
            )
        parts.append(f"{count}x {reason}{detail}")
    return ", ".join(parts)


def _upscale_remedy(rejected: Sequence[Mapping[str, Any]]) -> str:
    """The exact flag that would admit this material, with its number."""
    rows = [
        dict(item.get("details") or {})
        for item in rejected
        if item.get("rejection_reason") == "RESOLUTION_TOO_LOW"
    ]
    if not rows:
        return ""
    needed = max(float(row.get("upscale") or 0.0) for row in rows)
    return (
        f"--override thresholds.triage_max_upscale={needed + 0.1:.2f} "
        f"(serve {needed:.2f}x), oppure --assets con materiale piu' grande"
    )


def _no_video_error(
    selected: Sequence[Mapping[str, Any]],
    rejected: Sequence[Mapping[str, Any]],
) -> contract.VxError:
    """Why no video reached the timeline, with numbers and the way out."""
    digest = _rejection_digest(rejected)
    remedy = _upscale_remedy(rejected)
    images = any(entry.get("type") == "image" for entry in selected)
    if images:
        head = "serve --fps: il pool contiene immagini e nessun video da cui dedurre il frame rate"
    else:
        head = "serve --fps: nessun video selezionato da cui dedurre il frame rate"
    message = head
    if digest:
        message += f" — scarti: {digest}"
    if remedy:
        message += f". Rimedio: {remedy}"
    return contract.VxError(message)


def _fps(
    args: Any,
    selected: List[Mapping[str, Any]],
    assets_by_id: Mapping[str, asset_triage.AssetInfo],
    rejected: Sequence[Mapping[str, Any]] = (),
) -> float:
    """The frame rate comes from the flag, then from a selected video, never a constant."""
    explicit = getattr(args, "fps", None)
    if explicit is not None:
        value = float(explicit)
        if value <= 0:
            raise contract.VxError(f"--fps deve essere > 0, ricevuto {explicit!r}")
        return value

    for entry in selected:
        if entry.get("type") != "video":
            continue
        asset = assets_by_id.get(entry.get("asset_id"))
        if asset is None:
            continue
        info = probe.probe_asset(asset.path)
        if float(info.fps or 0.0) > 0:
            return float(info.fps)

    raise _no_video_error(selected, rejected)


#: How many a_rolls the opening rule looks at. Beyond this the manifest order is
#: the editorial decision and re-rolling the dice on sharpness would only make
#: the cut unpredictable.
OPENING_CANDIDATES = 5


def _clip_sharpness(asset: asset_triage.AssetInfo) -> Optional[Dict[str, Any]]:
    """The triage measure of a clip: Laplacian variance, median of 5 frames.

    Same function and same unit the gate uses, so the number that picks the
    opening is the number that would have rejected the shot. A dark clip whose
    variance is not reliable (D-21) is declared unknown instead of ranked.
    """
    try:
        from . import visual
    except Exception:  # noqa: BLE001 - a missing module is a declared absence
        return None
    try:
        measured = visual.measure_sharpness(asset.path)
    except Exception:  # noqa: BLE001
        return None
    method = str(measured.get("method") or "")
    if int(measured.get("samples") or 0) <= 0 or method.startswith("unavailable"):
        return None
    if not measured.get("reliable", True):
        return None
    return {
        "sharpness": round(float(measured["sharpness"]), 2),
        "samples": int(measured.get("samples") or 0),
        "mean_luma": round(float(measured.get("mean_luma") or 0.0), 2),
        "method": method,
    }


def opening_section(
    selected: Sequence[Mapping[str, Any]],
    assets_by_id: Mapping[str, asset_triage.AssetInfo],
    *,
    threshold: float,
    limit: int = OPENING_CANDIDATES,
) -> Optional[Dict[str, Any]]:
    """Pick the opening a_roll by measured sharpness, not by file name.

    Clips below ``triage_sharpness_low`` are unusable as an opening: they are
    excluded while a usable one exists. When every candidate is below the
    threshold the best of them still opens the reel — never the worst — and the
    report says ``quality: "below_threshold"``. The remaining a_rolls keep their
    order (the manifest's, when there is one), so only position 0 moves.
    """
    a_rolls = [entry for entry in selected if entry.get("role") == "a_roll"]
    if not a_rolls:
        return None
    head = a_rolls[: max(1, int(limit))]
    measured: List[Tuple[Mapping[str, Any], Dict[str, Any]]] = []
    unknown: List[str] = []
    for entry in head:
        asset_id = str(entry.get("asset_id") or "")
        asset = assets_by_id.get(asset_id)
        value = _clip_sharpness(asset) if asset is not None else None
        if value is None:
            unknown.append(asset_id)
            continue
        measured.append((entry, value))
    usable = [row for row in measured if row[1]["sharpness"] >= float(threshold)]
    pool = usable or measured
    if not pool:
        return {
            "rule": "sharpness",
            "metric": "laplacian_variance_median",
            "threshold": float(threshold),
            "candidates": len(head),
            "considered": [],
            "not_evaluated": unknown,
            "chosen": None,
            "quality": "unknown",
            "moved": False,
            "reason": (
                "nitidezza non misurabile sulle prime "
                f"{len(head)} clip: ordine lasciato com'e'"
            ),
        }
    best_entry, best_value = pool[0]
    for entry, value in pool[1:]:
        if value["sharpness"] > best_value["sharpness"]:
            best_entry, best_value = entry, value
    first_before = str(a_rolls[0].get("asset_id") or "")
    chosen_id = str(best_entry.get("asset_id") or "")
    reordered = [best_entry] + [
        entry for entry in a_rolls if entry is not best_entry
    ]
    moved = chosen_id != first_before
    quality = "measured" if usable else "below_threshold"
    if not moved:
        reason = (
            f"la prima clip ({chosen_id}, nitidezza {best_value['sharpness']:.1f}) "
            "era gia' la piu' nitida: ordine e qualita' coincidono"
        )
    else:
        reason = (
            f"apertura scelta per nitidezza ({chosen_id}, "
            f"{best_value['sharpness']:.1f} contro {first_before}); le altre "
            "clip restano nell'ordine dichiarato"
        )
    return {
        "rule": "sharpness",
        "metric": "laplacian_variance_median",
        "threshold": float(threshold),
        "candidates": len(head),
        "considered": [
            {
                "asset_id": str(entry.get("asset_id") or ""),
                "sharpness": value["sharpness"],
            }
            for entry, value in measured
        ],
        "not_evaluated": unknown,
        "chosen": {"asset_id": chosen_id, "sharpness": best_value["sharpness"]},
        "first_before": first_before,
        "moved": moved,
        "quality": quality,
        "reordered": reordered,
        "reason": reason,
    }


def promote_a_roll(
    selected: Sequence[Mapping[str, Any]],
    assets_by_id: Mapping[str, Any],
    requested_sec: Any,
    *,
    montage: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Porta in a_roll il video che serve, **prima** del montaggio, dichiarandolo.

    Il criterio dei ruoli e' uno solo: un video con audio diventa a_roll, tutto il
    resto b_roll. Con un girato in cui il secondo video non ha audio, 15 s di
    materiale davano 4.65 s di reel e il lavoro finiva `NON-PUBBLICABILE` per
    durata: il materiale c'era, era solo assegnato a un ruolo che il montaggio
    non usa.

    Qui, se gli a_roll non coprono la durata chiesta, si promuovono i video in
    b_roll dal piu' lungo, e si dichiara ogni promozione con il perche'. Se il
    materiale non basta comunque, `still_short_sec` lo dice **prima** del render.
    """
    entries = [dict(entry) for entry in selected]
    want = None
    try:
        want = float(requested_sec) if requested_sec is not None else None
    except (TypeError, ValueError):
        want = None

    def seconds_of(entry: Mapping[str, Any]) -> float:
        value = entry.get("target_duration_sec")
        planned = (
            float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else 0.0
        )
        asset = assets_by_id.get(str(entry.get("asset_id") or ""))
        source = float(getattr(asset, "duration_sec", 0.0) or 0.0)
        # Un video vale quello che dura davvero, non lo slot di b-roll che gli
        # era stato assegnato: il secondo video del caso del 4 novembre aveva
        # 9.97 s di girato e uno slot da 3.0 s.
        return max(planned, source) if source else planned

    a_rolls = [entry for entry in entries if entry.get("role") == "a_roll"]
    before = round(sum(seconds_of(entry) for entry in a_rolls), 3)
    block: Dict[str, Any] = {
        "rule": "a_roll con audio; promozione dichiarata se la durata non regge",
        "requested_sec": None if want is None else round(want, 3),
        "a_roll_sec_before": before,
        "a_roll_sec_after": before,
        "promoted": [],
        "still_short_sec": 0.0,
        "montage": bool(montage),
    }
    if want is None or want <= 0 or before >= want:
        return entries, block

    candidates = [
        entry
        for entry in entries
        if entry.get("role") not in ("a_roll", "music")
        and str(getattr(assets_by_id.get(str(entry.get("asset_id") or "")), "kind", ""))
        == "video"
    ]
    candidates.sort(key=seconds_of, reverse=True)
    total = before
    for entry in candidates:
        if total >= want:
            break
        gained = seconds_of(entry)
        if gained <= 0:
            continue
        entry["role"] = "a_roll"
        total += gained
        block["promoted"].append(
            {
                "asset_id": entry.get("asset_id"),
                "seconds": round(gained, 3),
                "why": (
                    "gli a_roll coprivano "
                    f"{total - gained:.3f} s su {want:.3f} richiesti: senza "
                    "questo video il reel sarebbe stato piu' corto del chiesto"
                ),
            }
        )
    block["a_roll_sec_after"] = round(total, 3)
    block["still_short_sec"] = round(max(0.0, want - total), 3)
    return entries, block


def apply_opening(
    block: Dict[str, Any],
    assets_by_id: Mapping[str, asset_triage.AssetInfo],
    *,
    threshold: float,
) -> Tuple[List[Any], Optional[Dict[str, Any]]]:
    """Reorder ``block["selected_assets"]`` so the measured opening comes first."""
    selected = list(block.get("selected_assets") or [])
    section = opening_section(selected, assets_by_id, threshold=threshold)
    if not section:
        return selected, None
    reordered = section.pop("reordered", [])
    if not section.get("moved"):
        return selected, section
    queue = list(reordered)
    merged: List[Any] = []
    for entry in selected:
        if entry.get("role") == "a_roll" and queue:
            merged.append(queue.pop(0))
        else:
            merged.append(entry)
    block["selected_assets"][:] = merged
    return merged, section


def _composition_facts(
    selected: Sequence[Mapping[str, Any]],
    assets_by_id: Mapping[str, asset_triage.AssetInfo],
    in_points: Mapping[str, Mapping[str, Any]],
    opening: Optional[Mapping[str, Any]],
    beat_provenance: Mapping[str, Any],
    requested_sec: Any,
    fps: float,
    media_info_map: Mapping[str, Any],
    *,
    beat_block: Optional[Mapping[str, Any]] = None,
    expectations: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Le misure che la composizione può leggere, prima del piano.

    Niente stime: durate dal probe, nitidezza dal profilo per-frame (a_roll) o
    dal campione del triage (b_roll), corse utili dichiarate dal probe, BPM dalla
    traccia. Un valore assente resta assente — ``compose`` declinerà la leva.

    L'apertura ha due misure possibili: quella del triage (mediana di 5 frame) e
    quella del profilo per-frame. Se il triage non è affidabile — succede sui
    girati scuri — si usa la seconda, con la **sua** soglia preferita, e il
    report dice quale delle due ha deciso.
    """

    def number(value: Any) -> Optional[float]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    def seconds_of(entry: Mapping[str, Any], asset_id: str) -> Optional[float]:
        info = media_info_map.get(asset_id)
        value = getattr(info, "duration", None) if info is not None else None
        if value is None and isinstance(info, Mapping):
            value = info.get("duration")
        if value is None:
            value = entry.get("target_duration_sec")
        measured = number(value)
        return None if measured is None else round(measured, 3)

    a_roll: List[Dict[str, Any]] = []
    b_roll: List[Dict[str, Any]] = []
    section = opening if isinstance(opening, Mapping) else {}
    chosen = section.get("chosen") if isinstance(section.get("chosen"), Mapping) else {}
    opening_asset = str((chosen or {}).get("asset_id") or "")
    if not opening_asset:
        # Il triage non ha potuto scegliere: l'apertura è la prima a_roll
        # nell'ordine dichiarato, e la sua misura viene dal profilo per-frame.
        first_entry = next(
            (item for item in selected if item.get("role") == "a_roll"), None
        )
        if first_entry is not None:
            opening_asset = str(first_entry.get("asset_id") or "")
    for entry in selected:
        asset_id = str(entry.get("asset_id") or "")
        if not asset_id:
            continue
        role = str(entry.get("role") or "")
        if role == "a_roll":
            row = in_points.get(asset_id) or {}
            runs = []
            for item in row.get("usable_runs") or ():
                if not isinstance(item, Mapping):
                    continue
                start, end = number(item.get("start")), number(item.get("end"))
                if start is None or end is None or end - start <= 1e-9:
                    continue
                runs.append({"start": round(start, 3), "end": round(end, 3)})
            profile = row.get("profile") if isinstance(row.get("profile"), Mapping) else {}
            thresholds = row.get("thresholds") if isinstance(row.get("thresholds"), Mapping) else {}
            samples = []
            if asset_id == opening_asset:
                # Solo l'apertura porta la curva: serve a spostare il punto di
                # presa quando il gancio non ha spazio dopo il picco.
                for item in row.get("samples") or ():
                    if not isinstance(item, Mapping):
                        continue
                    at, value = number(item.get("t")), number(item.get("sharpness"))
                    if at is None or value is None:
                        continue
                    samples.append([round(at, 3), round(value, 3)])
            a_roll.append(
                {
                    "asset_id": asset_id,
                    "seconds": seconds_of(entry, asset_id),
                    "sharpness": number(row.get("sharpness")),
                    "src_in": number(row.get("src_in")),
                    "usable_runs": runs,
                    "samples": samples,
                    "preferred": number(profile.get("threshold_preferred"))
                    or number(thresholds.get("preferred")),
                }
            )
        elif role == "b_roll":
            asset = assets_by_id.get(asset_id)
            measured = _clip_sharpness(asset) if asset is not None else None
            b_roll.append(
                {
                    "asset_id": asset_id,
                    "seconds": seconds_of(entry, asset_id),
                    "sharpness": number((measured or {}).get("sharpness")),
                }
            )

    beat = beat_provenance if isinstance(beat_provenance, Mapping) else {}
    subdivision = 1
    if isinstance(beat_block, Mapping):
        raw_subdivision = beat_block.get("subdivision")
        if (
            isinstance(raw_subdivision, int)
            and not isinstance(raw_subdivision, bool)
            and raw_subdivision in (1, 2, 4)
        ):
            subdivision = raw_subdivision
    requested = number(requested_sec) or 0.0

    # L'apertura: se il triage l'ha scelta, la sua misura è quella; altrimenti
    # conta la misura del profilo per-frame, con la sua soglia preferita.
    opening_sharpness = number(chosen.get("sharpness"))
    opening_threshold = number(section.get("threshold"))
    opening_measure = "triage"
    if not str(chosen.get("asset_id") or ""):
        fallback = next(
            (item for item in a_roll if item["asset_id"] == opening_asset), None
        )
        if fallback is not None:
            opening_sharpness = number(fallback.get("sharpness"))
            opening_threshold = number(fallback.get("preferred"))
            opening_measure = "profile"

    return {
        "fps": float(fps),
        "duration_sec": round(requested, 3),
        "opening": {
            "asset_id": opening_asset,
            "sharpness": opening_sharpness,
            "threshold": opening_threshold,
            "measure": opening_measure,
            "quality": str(section.get("quality") or ""),
        },
        "a_roll": a_roll,
        "b_roll": b_roll,
        "beat": {
            "enabled": bool(beat.get("enabled")),
            "bpm": number(beat.get("bpm")),
            "source": str(beat.get("source") or ""),
            "subdivision": subdivision,
        },
        "expectations": dict(expectations or {}),
    }


#: Frames sampled inside the opening shot when looking for the cover. The first
#: second of a phone clip is often a slow zoom out of a blur; the cover must not
#: be that frame if a sharp one exists in the same shot.
THUMBNAIL_SAMPLES = 12


def _yunet_detector(width: int, height: int) -> Any:
    """A single-image YuNet detector, or ``None`` when the asset is absent."""
    try:
        import cv2
        from . import faces
    except Exception:  # noqa: BLE001
        return None
    if not faces.yunet_available():
        return None
    try:
        return cv2.FaceDetectorYN.create(
            faces.model_path(), "", (int(width), int(height)), 0.6, 0.3, 5000
        )
    except Exception:  # noqa: BLE001
        return None


def _face_count(detector: Any, frame: Any) -> Optional[int]:
    if detector is None:
        return None
    try:
        _, detections = detector.detect(frame)
    except Exception:  # noqa: BLE001
        return None
    if detections is None:
        return 0
    return int(len(detections))


def _frame_sharpness(gray: Any) -> Optional[float]:
    """Laplacian variance on one frame — the gate's own number."""
    try:
        import cv2
    except Exception:  # noqa: BLE001
        return None
    try:
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Il punto di presa: la clip si sceglie per qualita', il punto per misura
# --------------------------------------------------------------------------- #

#: Passo del profilo di nitidezza usato per scegliere il punto di presa. 0.15 s
#: sono ~4 fotogrammi a 30 fps: abbastanza fitto per non saltare il passaggio da
#: sfocatura a fuoco, abbastanza largo per non decodificare ogni frame.
IN_POINT_STEP_SEC = 0.15

#: Quota della distribuzione della clip sotto cui un fotogramma e' "sotto la
#: qualita' preferita": 35% del 75° percentile. La varianza del Laplaciano
#: dipende da risoluzione e compressione (360x640 da WhatsApp non arriva a 50),
#: quindi la soglia assoluta del triage non puo' decidere da sola cosa buttare.
IN_POINT_RELATIVE_SHARE = 0.35

#: Pavimento assoluto, molto basso: sotto questo numero il fotogramma e' nero,
#: vetro o rumore, non immagine. E' l'unico giudizio che non dipende dalla clip:
#: la varianza del Laplaciano di un 360x640 da telefono sta intorno a 30-60
#: anche quando l'immagine e' buona, quindi una soglia alta qui svuoterebbe il
#: montaggio invece di proteggerlo.
IN_POINT_ABSOLUTE_FLOOR = 5.0

#: Secondi di materiale che devono restare dopo il punto di presa: meno di una
#: battuta non e' un piano, e' un fotogramma.
IN_POINT_MIN_KEEP_SEC = 0.6

#: Quota massima di materiale che il filtro puo' togliere a una clip. Oltre
#: questa soglia il montaggio terrebbe troppo poco: il tratto scartato rientra
#: come ``below_preferred``, dichiarato, e la timeline resta pubblicabile.
IN_POINT_MAX_REMOVAL = 0.25

#: Due campioni consecutivi sotto questo scarto medio (scala 0..255) sono lo
#: stesso fotogramma: e' la definizione di freeze usata qui, non un pan lento.
IN_POINT_FREEZE_DIFF = 0.5


def percentile(values: Sequence[float], share: float) -> float:
    """Percentile lineare (stessa definizione di ``numpy.percentile``), senza numpy."""
    rows = sorted(float(value) for value in values)
    if not rows:
        return 0.0
    if len(rows) == 1:
        return rows[0]
    position = max(0.0, min(1.0, float(share))) * (len(rows) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return rows[low]
    weight = position - low
    return rows[low] * (1.0 - weight) + rows[high] * weight


def coherence_black_luma() -> float:
    """La soglia del nero del cancello di coerenza: una sola verita', non due."""
    from . import coherence

    return float(coherence.BLACK_LUMA)


def _frozen_flags(
    samples: Sequence[Mapping[str, Any]], *, step: float, freeze_seconds: float
) -> List[bool]:
    """Dove un fermo immagine dura abbastanza da non essere materiale."""
    count = len(samples)
    frozen = [False] * count
    start: Optional[int] = None
    for index in range(1, count + 1):
        static = False
        if index < count:
            diff = samples[index].get("diff")
            static = diff is not None and float(diff) <= IN_POINT_FREEZE_DIFF
        if static:
            if start is None:
                start = index - 1
        elif start is not None:
            if (index - 1 - start) * float(step) >= float(freeze_seconds):
                for mark in range(start, index):
                    frozen[mark] = True
            start = None
    return frozen


def _clip_thresholds(samples: Sequence[Mapping[str, Any]]) -> Dict[str, float]:
    """Le due soglie della clip: assoluta (pavimento) e relativa (distribuzione)."""
    values = [float(row.get("sharpness") or 0.0) for row in samples]
    p75 = percentile(values, 0.75)
    return {
        "absolute": float(IN_POINT_ABSOLUTE_FLOOR),
        "relative": round(float(IN_POINT_RELATIVE_SHARE) * p75, 3),
        "preferred": round(
            max(float(IN_POINT_ABSOLUTE_FLOOR), float(IN_POINT_RELATIVE_SHARE) * p75), 3
        ),
        "p75": round(p75, 3),
    }


def _usable_runs(
    samples: Sequence[Mapping[str, Any]],
    *,
    step: float,
    threshold: float,
    black_luma: float,
    freeze_seconds: float,
) -> List[Dict[str, Any]]:
    """Le corse continue di campioni utilizzabili, con inizio e fine in secondi.

    Un campione e' utilizzabile quando sta sopra ``threshold`` (il pavimento
    assoluto), la luma non e' nera e il fotogramma non e' dentro un freeze
    abbastanza lungo da contare (``freeze_seconds``). Il freeze si misura sullo
    scarto fra campioni consecutivi: una zoomata sfocata cambia i pixel, un
    fermo immagine no.
    """
    count = len(samples)
    frozen = _frozen_flags(samples, step=step, freeze_seconds=freeze_seconds)
    usable = [
        (
            bool(frozen[index]) is False
            and float(samples[index].get("sharpness") or 0.0) >= float(threshold)
            and float(samples[index].get("luma") or 0.0) >= float(black_luma)
        )
        for index in range(count)
    ]
    runs: List[Dict[str, Any]] = []
    first: Optional[int] = None
    for index in range(count + 1):
        if index < count and usable[index]:
            if first is None:
                first = index
        elif first is not None:
            peak = max(
                float(samples[mark].get("sharpness") or 0.0)
                for mark in range(first, index)
            )
            runs.append(
                {
                    "from": first,
                    "to": index - 1,
                    "start": float(samples[first]["t"]),
                    "end": float(samples[index - 1]["t"]) + float(step),
                    "seconds": (index - first) * float(step),
                    "sharpness": round(peak, 3),
                }
            )
            first = None
    return runs


def choose_in_point(
    samples: Sequence[Mapping[str, Any]],
    *,
    step: float = IN_POINT_STEP_SEC,
    black_luma: float,
    freeze_seconds: float,
    min_keep: float = IN_POINT_MIN_KEEP_SEC,
    duration: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Il punto di presa scelto per misura dentro una clip, o ``None``.

    La soglia non e' quella assoluta del triage: la varianza laplaciana dipende
    da risoluzione e compressione, e su un 360x640 da telefono finirebbe sotto
    50 quasi tutto, anche materiale buono a occhio. Qui contano due numeri
    dichiarati per clip:

    * ``relative`` = 35% del 75° percentile della curva (``preferred``);
    * ``absolute`` = :data:`IN_POINT_ABSOLUTE_FLOOR`, il pavimento nero/vetro.

    Regola, in ordine:

    * se la clip **apre gia'** sopra la soglia preferita, il punto resta 0: il
      piano di un materiale buono non cambia;
    * altrimenti si prende il **picco di nitidezza** della parte preferita che
      lascia almeno ``min_keep`` secondi dopo di se' (il primo fotogramma di un
      telefono e' spesso uno zoom sfocato);
    * si tolgono solo i tratti **davvero inutilizzabili** (sotto il pavimento,
      neri o in freeze), e mai piu' di :data:`IN_POINT_MAX_REMOVAL` della clip:
      oltre quella quota il tratto rientra, dichiarato ``below_preferred``;
    * nessun campione sopra il pavimento: ``None``, la clip va esclusa.

    Il materiale utile e' la finestra ``[usable_start, usable_end]``; la durata
    dei segmenti resta quella della griglia, si sposta solo il punto di presa.
    """
    rows = list(samples)
    if not rows:
        return None
    total = float(duration) if duration is not None else float(rows[-1]["t"]) + float(step)
    total = max(float(step), total)
    thresholds = _clip_thresholds(rows)
    preferred = float(thresholds["preferred"])
    floor = float(thresholds["absolute"])
    if float(thresholds["p75"]) < floor:
        return None
    frozen = _frozen_flags(rows, step=step, freeze_seconds=freeze_seconds)

    def _ok(index: int) -> bool:
        row = rows[index]
        return (
            not frozen[index]
            and float(row.get("luma") or 0.0) >= float(black_luma)
            and float(row.get("sharpness") or 0.0) >= floor
        )

    usable_index = [index for index in range(len(rows)) if _ok(index)]
    if not usable_index:
        return None
    usable_start = float(rows[usable_index[0]]["t"])
    usable_end = min(total, float(rows[usable_index[-1]]["t"]) + float(step))
    if usable_end - usable_start <= 0.0:
        return None
    removed = max(0.0, total - (usable_end - usable_start))
    kept_head = 0.0
    if total > 0 and removed / total > float(IN_POINT_MAX_REMOVAL):
        # Mai svuotare: oltre la quota ammessa il tratto scartato rientra.
        usable_start = 0.0
        usable_end = total
        kept_head = removed
    window = max(1e-6, usable_end - usable_start)
    usable_fraction = round(window / total, 3) if total > 0 else 0.0
    raw_runs = _usable_runs(
        rows,
        step=step,
        threshold=floor,
        black_luma=black_luma,
        freeze_seconds=freeze_seconds,
    )
    usable_runs: List[Dict[str, Any]] = []
    for run in raw_runs:
        run_start = max(0.0, min(float(run["start"]), total))
        run_end = max(run_start, min(float(run["end"]), total))
        if run_end - run_start <= 1e-9:
            continue
        usable_runs.append(
            {
                "start": round(run_start, 6),
                "end": round(run_end, 6),
                "sharpness": round(float(run.get("sharpness") or 0.0), 3),
            }
        )
    if not usable_runs:
        usable_runs.append(
            {
                "start": round(usable_start, 6),
                "end": round(usable_end, 6),
                "sharpness": round(float(max(
                    (float(row.get("sharpness") or 0.0) for row in rows),
                    default=0.0,
                )), 3),
            }
        )
    if kept_head:
        # Oltre la quota di scarto ammessa la finestra intera rientra: le corse
        # dichiarate sono allora tutto il materiale tenuto, non solo quello
        # sopra il pavimento.
        usable_runs = [
            {
                "start": 0.0,
                "end": round(total, 6),
                "sharpness": round(float(max(
                    (float(row.get("sharpness") or 0.0) for row in rows),
                    default=0.0,
                )), 3),
                "kept_below_preferred": True,
            }
        ]
    preferred_count = sum(
        1
        for index in usable_index
        if float(rows[index].get("sharpness") or 0.0) >= preferred
    )
    below_fraction = round(
        max(0.0, window - preferred_count * float(step)) / total, 3
    ) if total > 0 else 0.0
    preferred_index = [
        index
        for index in usable_index
        if float(rows[index].get("sharpness") or 0.0) >= preferred
    ]
    wrap_start = (
        round(float(rows[preferred_index[0]]["t"]), 6)
        if preferred_index
        else round(usable_start, 6)
    )
    head = rows[0]
    head_ok = (
        0 in usable_index
        and float(head.get("sharpness") or 0.0) >= preferred
        and float(usable_end) + 1e-9 >= float(min_keep)
    )
    if head_ok:
        return {
            "src_in": 0.0,
            "usable_start": 0.0,
            "usable_end": round(usable_end, 6),
            "wrap_start": 0.0,
            "sharpness": round(float(head.get("sharpness") or 0.0), 3),
            "moved": False,
            "short_window": False,
            "usable_fraction": usable_fraction,
            "below_preferred_fraction": below_fraction,
            "thresholds": thresholds,
            "kept_below_preferred": bool(kept_head),
            "usable_runs": usable_runs,
            "why": (
                f"la clip e' gia' sopra la soglia preferita dal primo fotogramma "
                f"(nitidezza {float(head.get('sharpness') or 0.0):.1f} >= "
                f"{preferred:.1f}): punto invariato"
            ),
        }
    pool = preferred_index or usable_index
    best: Optional[Mapping[str, Any]] = None
    for index in pool:
        row = rows[index]
        if float(usable_end) - float(row["t"]) + 1e-9 < float(min_keep):
            continue
        if best is None or float(row.get("sharpness") or 0.0) > float(
            best.get("sharpness") or 0.0
        ):
            best = row
    short_window = best is None
    if best is None:
        best = max(
            (rows[index] for index in pool),
            key=lambda row: float(row.get("sharpness") or 0.0),
        )
    src_in = round(float(best["t"]), 6)
    return {
        "src_in": src_in,
        "usable_start": round(usable_start, 6),
        "usable_end": round(usable_end, 6),
        "wrap_start": wrap_start,
        "sharpness": round(float(best.get("sharpness") or 0.0), 3),
        "moved": src_in > 1e-9,
        "short_window": bool(short_window),
        "usable_fraction": usable_fraction,
        "below_preferred_fraction": below_fraction,
        "thresholds": thresholds,
        "kept_below_preferred": bool(kept_head),
        "usable_runs": usable_runs,
        "why": (
            f"picco di nitidezza {float(best.get('sharpness') or 0.0):.1f} "
            f"(soglia preferita {preferred:.1f} = 35% del 75° percentile "
            f"{float(thresholds['p75']):.1f}; pavimento {floor:.1f}) dentro la "
            f"finestra utile [{usable_start:.2f}, {usable_end:.2f}]s"
            + (
                f": la finestra resta {usable_end - src_in:.2f}s, sotto la "
                f"battuta di {float(min_keep):.2f}s (dichiarato)"
                if short_window
                else f": restano {usable_end - src_in:.2f}s di materiale"
            )
        ),
    }


def measure_frame_profile(
    path: Any, *, step: float = IN_POINT_STEP_SEC
) -> Optional[Dict[str, Any]]:
    """Il profilo per-frame di una sorgente, o ``None`` se non si misura.

    Stessa metrica del triage e del cancello di coerenza (varianza del
    Laplaciano sul grigio), campionata ogni ``step`` secondi. Senza OpenCV o
    senza frame leggibili non si inventa niente: ``None``, e il piano prosegue
    col comportamento di sempre dichiarando ``in_point: not_measured``.

    **Una sola passata sequenziale.** Prima si faceva un ``set(POS_MSEC)`` per
    ogni campione: ogni seek riparte dal keyframe, e su un girato di 32 s
    costava 54 s (7,5× più lento) con un difetto serio — il frame restituito
    non era sempre quello del tempo chiesto (coda di B-frame), quindi il profilo
    misurava *un altro* fotogramma e lo etichettava col tempo sbagliato. Qui si
    decodifica una volta sola, si prende il frame più vicino a ogni istante
    chiesto e si calcolano le stesse tre misure. Stesso input, stesse misure,
    stessi campioni: solo senza seek.
    """
    try:
        import cv2
        import numpy as np
    except Exception:  # noqa: BLE001 - senza OpenCV non c'e' profilo
        return None
    try:
        capture = cv2.VideoCapture(str(path))
    except Exception:  # noqa: BLE001
        return None
    if not capture.isOpened():
        capture.release()
        return None
    samples: List[Dict[str, Any]] = []
    previous: Any = None
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        duration = (frames / fps) if fps > 0.0 and frames > 0.0 else 0.0
        if duration <= 0.0:
            return None
        targets: List[float] = []
        at = 0.0
        while at < duration - 1e-6:
            targets.append(round(at, 4))
            at += float(step)
        if not targets:
            return None
        # Il frame che "sta" a un istante è quello più vicino: si accetta mezzo
        # frame di tolleranza, così un 25 fps campionato a 0,15 s non salta mai
        # un campione per un arrotondamento.
        half = 0.5 / fps if fps > 0.0 else 1e-6
        next_target = 0
        index = 0
        while next_target < len(targets):
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            moment = index / fps if fps > 0.0 else 0.0
            while (
                next_target < len(targets)
                and targets[next_target] <= moment + half
            ):
                current = targets[next_target]
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                luma = float(gray.mean()) / 255.0
                small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
                small = small.astype("float32")
                diff = (
                    255.0
                    if previous is None
                    else float(np.mean(np.abs(small - previous)))
                )
                previous = small
                samples.append(
                    {
                        "t": current,
                        "sharpness": round(sharpness, 3),
                        "luma": round(luma, 5),
                        "diff": round(diff, 3),
                    }
                )
                next_target += 1
            index += 1
    except Exception:  # noqa: BLE001 - una decodifica rotta non inventa un profilo
        return None
    finally:
        capture.release()
    if not samples:
        return None
    return {
        "method": "laplacian_variance",
        "metric": "sharpness",
        "step_sec": float(step),
        "duration": round(duration, 4),
        "samples": samples,
    }


def frame_choice(
    selected: Sequence[Mapping[str, Any]],
    assets_by_id: Mapping[str, asset_triage.AssetInfo],
    staged: Mapping[str, Any],
    *,
    black_luma: float,
    freeze_seconds: float,
    measure: Optional[Callable[[Any], Optional[Dict[str, Any]]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """``asset_id -> punto di presa`` per gli a_roll, misurato o dichiarato.

    Per ogni a_roll si misura il profilo della sorgente **messa in scena** (la
    stessa che la coerenza campiona) e si sceglie il punto con
    :func:`choose_in_point`. Se il profilo non esiste, la voce e'
    ``quality: "not_measured"`` e il piano usa il comportamento di sempre. Se
    nessun istante sta sopra il pavimento assoluto, la voce e'
    ``excluded: True`` con la misura che lo dice: chi costruisce il piano la
    toglie e la dichiara.
    """
    read = measure or measure_frame_profile
    out: Dict[str, Dict[str, Any]] = {}
    for entry in selected:
        if entry.get("role") != "a_roll":
            continue
        asset_id = str(entry.get("asset_id") or "")
        if not asset_id or asset_id in out:
            continue
        asset = assets_by_id.get(asset_id)
        path = ""
        result = staged.get(asset_id)
        if result is not None:
            path = str(_staged_path(result))
        elif asset is not None:
            path = str(asset.path or "")
        profile = read(path) if path else None
        if not profile:
            out[asset_id] = {
                "quality": "not_measured",
                "in_point": "not_measured",
                "src_in": 0.0,
                "sharpness": None,
                "usable_start": None,
                "usable_end": None,
                "excluded": False,
                "moved": False,
                "samples": [],
                "usable_runs": [],
                "why": (
                    "not_measured: profilo per-frame non disponibile (analisi "
                    "non eseguita o proxy assente), punto di presa non misurato, "
                    "piano invariato"
                ),
            }
            continue
        samples = list(profile.get("samples") or [])
        chosen = choose_in_point(
            samples,
            step=float(profile.get("step_sec") or IN_POINT_STEP_SEC),
            black_luma=float(black_luma),
            freeze_seconds=float(freeze_seconds),
            duration=profile.get("duration"),
        )
        if chosen is None:
            best = max(
                (float(item.get("sharpness") or 0.0) for item in samples), default=0.0
            )
            thresholds = _clip_thresholds(samples)
            out[asset_id] = {
                "quality": "unusable",
                "in_point": "unusable",
                "src_in": 0.0,
                "sharpness": round(best, 3),
                "usable_start": 0.0,
                "usable_end": 0.0,
                "excluded": True,
                "moved": False,
                "thresholds": thresholds,
                "samples": samples,
                "usable_runs": [],
                "why": (
                    f"nessun istante utilizzabile: nitidezza massima {best:.1f}, "
                    f"75° percentile {float(thresholds['p75']):.1f} sotto il "
                    f"pavimento {float(thresholds['absolute']):.1f} "
                    f"({len(samples)} campioni)"
                ),
                "profile": {
                    "method": str(profile.get("method") or ""),
                    "samples": len(samples),
                    "step_sec": float(profile.get("step_sec") or IN_POINT_STEP_SEC),
                    "threshold_absolute": float(thresholds["absolute"]),
                    "threshold_relative": float(thresholds["relative"]),
                    "threshold_preferred": float(thresholds["preferred"]),
                    "curve": [
                        [round(float(item["t"]), 3), round(float(item["sharpness"]), 2)]
                        for item in samples
                    ],
                },
            }
            continue
        out[asset_id] = {
            **chosen,
            "quality": "measured",
            "in_point": round(float(chosen["src_in"]), 6),
            "excluded": False,
            "samples": samples,
            "profile": {
                "method": str(profile.get("method") or ""),
                "samples": len(samples),
                "step_sec": float(profile.get("step_sec") or IN_POINT_STEP_SEC),
                "threshold_absolute": float(chosen["thresholds"]["absolute"]),
                "threshold_relative": float(chosen["thresholds"]["relative"]),
                "threshold_preferred": float(chosen["thresholds"]["preferred"]),
                "curve": [
                    [round(float(item["t"]), 3), round(float(item["sharpness"]), 2)]
                    for item in samples
                ],
            },
        }
    return out


def opening_in_point(
    choice: Mapping[str, Dict[str, Any]], asset_id: Any
) -> Dict[str, Any]:
    """``opening.in_point``: il punto d'ingresso con la sua nitidezza e il perche'.

    Tre campi sempre presenti (``src_in``, ``sharpness``, ``why``), il profilo
    quando e' stato misurato, ``not_measured`` quando non c'era niente da
    misurare. Una clip scartata non ha un punto d'ingresso: lo dice.
    """
    row = choice.get(str(asset_id or "")) if choice else None
    if not isinstance(row, Mapping):
        return {
            "src_in": None,
            "sharpness": None,
            "why": "not_measured: nessun profilo per la clip d'apertura",
            "quality": "not_measured",
        }
    quality = str(row.get("quality") or "")
    out: Dict[str, Any] = {
        "src_in": row.get("src_in"),
        "sharpness": row.get("sharpness"),
        "why": str(row.get("why") or ""),
        "quality": quality,
        "moved": bool(row.get("moved")),
    }
    if quality == "measured":
        out["in_point"] = row.get("in_point")
        out["usable_start"] = row.get("usable_start")
        out["usable_end"] = row.get("usable_end")
        out["usable_fraction"] = row.get("usable_fraction")
        out["threshold_absolute"] = (row.get("thresholds") or {}).get("absolute")
        out["threshold_relative"] = (row.get("thresholds") or {}).get("relative")
        out["profile"] = row.get("profile")
    elif quality == "not_measured":
        out["in_point"] = "not_measured"
    elif quality == "unusable":
        out["in_point"] = "unusable"
        out["profile"] = row.get("profile")
    return out


def segment_quality(
    plan: Any,
    choice: Mapping[str, Dict[str, Any]],
    *,
    step: float = IN_POINT_STEP_SEC,
    owner: Optional[Mapping[str, str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Classifica il **punto di presa** di ogni segmento layer-0.

    ``below_preferred`` sono i segmenti che partono sotto la soglia preferita
    della propria clip ma sopra il pavimento: restano nel montaggio, dichiarati.
    ``unusable`` sono quelli che partono sotto il pavimento: non dovrebbero
    esistere, e se esistono il report lo dice invece di nasconderli.

    ``owner`` riporta lo ``source_id`` dell'EDL all'``asset_id`` del payload:
    senza, una clip con spazi nel nome non verrebbe trovata e il report
    direbbe zero segmenti ``below_preferred`` anche quando ci sono.
    """
    out: Dict[str, List[Dict[str, Any]]] = {"below_preferred": [], "unusable": []}
    for track in getattr(plan, "video_tracks", ()) or ():
        for clip in getattr(track, "clips", ()) or ():
            if int(getattr(clip, "layer", 0) or 0) != 0:
                continue
            source_id = str(getattr(clip, "source_id", ""))
            asset_id = (owner or {}).get(source_id) or source_id
            row = choice.get(asset_id) or {}
            samples = row.get("samples") or []
            if not samples:
                continue
            at = float(getattr(clip, "src_in", 0.0) or 0.0)
            nearest = min(samples, key=lambda item: abs(float(item["t"]) - at))
            value = round(float(nearest.get("sharpness") or 0.0), 2)
            thresholds = row.get("thresholds") or {}
            preferred = float(thresholds.get("preferred") or 0.0)
            floor = float(thresholds.get("absolute") or 0.0)
            record = {
                "clip_id": str(getattr(clip, "clip_id", "") or ""),
                "asset_id": asset_id,
                "src_in": round(at, 3),
                "sharpness": value,
                "threshold_preferred": preferred,
            }
            if value < floor:
                out["unusable"].append(record)
            elif value < preferred:
                out["below_preferred"].append(record)
    return out


def usable_material_sec(
    choice: Mapping[str, Dict[str, Any]],
    selected: Sequence[Mapping[str, Any]],
    assets_by_id: Mapping[str, asset_triage.AssetInfo],
) -> float:
    """I secondi davvero montabili: la somma delle corse utilizzabili a-roll.

    Non e' la somma dei target (che puo' essere piu' lunga del materiale buono)
    e non e' la durata del piano (che puo' fermarsi prima): e' il tetto reale
    che il report deve dichiarare. Se una clip non e' stata misurata si usa il
    comportamento di sempre (``min(target, durata)``).
    """
    total = 0.0
    for entry in selected:
        if entry.get("role") != "a_roll":
            continue
        asset_id = entry.get("asset_id")
        row = choice.get(str(asset_id or "")) or {}
        runs = row.get("usable_runs") or []
        if runs:
            for run in runs:
                try:
                    span = float(run.get("end")) - float(run.get("start"))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(span) and span > 0.0:
                    total += span
            continue
        try:
            start = float(row.get("usable_start"))
            end = float(row.get("usable_end"))
        except (TypeError, ValueError):
            start = end = 0.0
        if end > start:
            total += end - start
            continue
        asset = assets_by_id.get(asset_id)
        if asset is None:
            continue
        try:
            target = float(entry.get("target_duration_sec"))
        except (TypeError, ValueError):
            target = float(asset.duration_sec)
        total += min(target, float(asset.duration_sec))
    return round(total, 6)


def plan_runs_used(
    plan: Any,
    choice: Mapping[str, Dict[str, Any]],
    owner: Mapping[str, str],
) -> int:
    """Quante corse utilizzabili distinte il piano ha davvero toccato."""
    used = set()
    for track in getattr(plan, "video_tracks", ()) or ():
        for clip in getattr(track, "clips", ()) or ():
            if int(getattr(clip, "layer", 0) or 0) != 0:
                continue
            source_id = str(getattr(clip, "source_id", "") or "")
            asset_id = owner.get(source_id) or source_id
            row = choice.get(asset_id) or {}
            runs = list(row.get("usable_runs") or ())
            if not runs:
                try:
                    runs = [
                        {
                            "start": float(row.get("usable_start")),
                            "end": float(row.get("usable_end")),
                        }
                    ]
                except (TypeError, ValueError):
                    continue
            at_in = float(getattr(clip, "src_in", 0.0) or 0.0)
            at_out = float(getattr(clip, "src_out", 0.0) or 0.0)
            for index, run in enumerate(runs):
                try:
                    run_start = float(run.get("start"))
                    run_end = float(run.get("end"))
                except (TypeError, ValueError):
                    continue
                if at_out > run_start + 1e-6 and at_in < run_end - 1e-6:
                    used.add((asset_id, index))
                    break
    return len(used)


def plan_declaration(
    choice: Mapping[str, Dict[str, Any]],
    *,
    segments: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    plan: Any = None,
    material_sec: Optional[float] = None,
    runs_used: Optional[int] = None,
) -> Dict[str, Any]:
    """La sezione ``report["plan"]``: soglie per clip, punti di presa, scarti."""
    in_points: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for asset_id, row in choice.items():
        quality = str(row.get("quality") or "")
        thresholds = row.get("thresholds") or {}
        if quality == "not_measured":
            in_points.append(
                {
                    "asset_id": asset_id,
                    "in_point": "not_measured",
                    "why": str(row.get("why") or ""),
                }
            )
            continue
        if quality == "unusable":
            excluded.append(
                {
                    "asset_id": asset_id,
                    "reason": "unusable",
                    "measure": {
                        "metric": "laplacian_variance",
                        "threshold_absolute": thresholds.get("absolute"),
                        "threshold_relative": thresholds.get("relative"),
                        "p75": thresholds.get("p75"),
                        "sharpness": row.get("sharpness"),
                        "samples": (row.get("profile") or {}).get("samples"),
                    },
                    "why": str(row.get("why") or ""),
                }
            )
            continue
        in_points.append(
            {
                "asset_id": asset_id,
                "in_point": row.get("in_point"),
                "src_in": row.get("src_in"),
                "sharpness": row.get("sharpness"),
                "usable_start": row.get("usable_start"),
                "usable_end": row.get("usable_end"),
                "usable_fraction": row.get("usable_fraction"),
                "below_preferred_fraction": row.get("below_preferred_fraction"),
                "threshold_absolute": thresholds.get("absolute"),
                "threshold_relative": thresholds.get("relative"),
                "threshold_preferred": thresholds.get("preferred"),
                "p75": thresholds.get("p75"),
                "kept_below_preferred": bool(row.get("kept_below_preferred")),
                "moved": bool(row.get("moved")),
                "usable_runs": list(row.get("usable_runs") or ()),
                "why": str(row.get("why") or ""),
            }
        )
    below = list((segments or {}).get("below_preferred") or ())
    unusable = list((segments or {}).get("unusable") or ())
    declaration: Dict[str, Any] = {
        "metric": "laplacian_variance",
        "threshold_absolute": float(IN_POINT_ABSOLUTE_FLOOR),
        "threshold_relative_share": float(IN_POINT_RELATIVE_SHARE),
        "max_removal": float(IN_POINT_MAX_REMOVAL),
        "in_points": in_points,
        "excluded": excluded,
        "excluded_count": len(excluded),
        "below_preferred": below,
        "below_preferred_count": len(below),
        "unusable_segments": unusable,
        "unusable_segment_count": len(unusable),
    }
    if runs_used is not None:
        declaration["runs_used"] = int(runs_used)
    if plan is not None:
        duration = float(getattr(plan, "duration_sec", 0.0) or 0.0)
        declaration["timeline_sec"] = round(duration, 3)
        if material_sec is not None and float(material_sec) > 0:
            declaration["material_sec"] = round(float(material_sec), 3)
            declaration["duration_retained"] = round(
                min(1.0, duration / float(material_sec)), 4
            )
    return declaration


def _opening_clip(plan: Any) -> Optional[Any]:
    """The clip that opens the timeline: earliest layer-0 destination time."""
    best = None
    for track in getattr(plan, "video_tracks", ()) or ():
        for clip in getattr(track, "clips", ()) or ():
            if int(getattr(clip, "layer", 0) or 0) != 0:
                continue
            if float(getattr(clip, "dst_out", 0.0) or 0.0) <= 0.0:
                continue
            if best is None or float(clip.dst_in) < float(best.dst_in):
                best = clip
    return best


def _timeline_clip_at(plan: Any, at_s: float) -> Optional[Any]:
    """Il clip layer-0 che copre ``at_s`` sulla timeline (o l'apertura)."""
    for track in getattr(plan, "video_tracks", ()) or ():
        for clip in getattr(track, "clips", ()) or ():
            if int(getattr(clip, "layer", 0) or 0) != 0:
                continue
            start = float(getattr(clip, "dst_in", 0.0) or 0.0)
            end = float(getattr(clip, "dst_out", 0.0) or 0.0)
            if start <= float(at_s) < end:
                return clip
    return _opening_clip(plan)


#: I check che possono solo consigliare, mai fermare la consegna. Oggi nessun
#: check di ``verification`` e' consultivo: si tiene la lista esplicita perche'
#: un giudizio ``weak_open`` (coerenza dell'apertura) non e' una prova sul file e
#: vive fuori da ``verification``. Un check marcato ``advisory`` nel verdetto non
#: blocca, e il report lo dice.
ADVISORY_CHECKS: Tuple[str, ...] = ()


def verification_failures(verification: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """I check falliti che bloccano la consegna (gli advisory restano fuori)."""
    failed: List[Mapping[str, Any]] = []
    for item in verification.get("checks") or ():
        if not isinstance(item, Mapping) or item.get("passed"):
            continue
        check_id = str(item.get("id") or "")
        if item.get("advisory") or check_id in ADVISORY_CHECKS:
            continue
        failed.append(item)
    return failed


def _tolerance_line(item: Mapping[str, Any]) -> str:
    """La tolleranza usata da un check, in chiaro, quando il check la dichiara."""
    expected = item.get("expected")
    if not isinstance(expected, Mapping):
        return ""
    frames = expected.get("tolerance_frames")
    fps = expected.get("fps")
    if frames is not None and fps:
        try:
            return " (tolleranza {frames:g} frame = {sec:.4f}s @{fps:g}fps)".format(
                frames=float(frames),
                sec=float(frames) / float(fps),
                fps=float(fps),
            )
        except (TypeError, ValueError):
            return ""
    seconds = expected.get("tolerance_sec")
    if seconds is not None:
        try:
            return f" (tolleranza {float(seconds):.4f}s)"
        except (TypeError, ValueError):
            return ""
    return ""


def apply_verification(
    report: Dict[str, Any], verification: Mapping[str, Any]
) -> Dict[str, Any]:
    """Scrive il verdetto e ferma la consegna se un controllo fallisce.

    ``delivered`` risponde alla domanda "questo lavoro si consegna?". Un solo
    check bloccante fallito e' la prova che il lavoro non e' consegnabile: il
    master resta sul disco (la vista lo mostra come NON RIUSCITO) ma nessun
    report lo dichiara consegnato. Il motivo viaggia in chiaro in ``failures``
    con l'id del check e la tolleranza usata. Un check ``advisory`` non ferma la
    consegna e il report lo dichiara; se il verdetto dichiara ``ok: false`` per
    colpa loro, ``verification.ok`` viene riallineato ai soli bloccanti.
    """
    section = dict(verification) if isinstance(verification, Mapping) else {}
    report["verification"] = section
    failed = verification_failures(section)
    advisory_failed = [
        item
        for item in section.get("checks") or ()
        if isinstance(item, Mapping)
        and not item.get("passed")
        and (item.get("advisory") or str(item.get("id")) in ADVISORY_CHECKS)
    ]
    if advisory_failed:
        section["advisory"] = [
            str(item.get("id")) for item in advisory_failed
        ]
    if not failed:
        # Nessun bloccante: il verdetto e' verde anche se un advisory ha ceduto.
        if advisory_failed and section.get("ok") is False:
            section["ok"] = True
        return report
    section["ok"] = False
    section["blocking"] = [str(item.get("id")) for item in failed]
    report["delivered"] = False
    report["outcome_suggested"] = "failed"
    report["outcome"] = "failed"
    for item in failed:
        check_id = str(item.get("id") or "check")
        why = str(item.get("why") or "controllo fallito")
        reason = f"verification: {check_id} — {why}{_tolerance_line(item)}"
        if reason not in (report.get("failures") or []):
            report.setdefault("failures", []).append(reason)
    return report


def _text_background(
    plan: Any,
    at_s: float,
    *,
    out_w: int,
    out_h: int,
    dst: Path,
) -> Optional[Path]:
    """Il fotogramma del piano all'istante del testo, sulla tela di uscita.

    Il testo si sceglie prima del render, quindi il fondo non puo' essere il
    master: si campiona il clip che la timeline mette in quell'istante, alla
    stessa tela (fill + crop). Se il clip non ha un file, il chiamante riceve
    ``None`` e la scelta lo dichiara invece di inventare una misura.
    """
    clip = _timeline_clip_at(plan, at_s)
    if clip is None:
        return None
    source_id = str(getattr(clip, "source_id", "") or "")
    ref = (getattr(plan, "sources", {}) or {}).get(source_id)
    path = str(getattr(ref, "path", "") or "")
    if not path or not Path(path).is_file():
        return None
    src_in = float(getattr(clip, "src_in", 0.0) or 0.0)
    dst_in = float(getattr(clip, "dst_in", 0.0) or 0.0)
    src_at = src_in + max(0.0, float(at_s) - dst_in)
    try:
        return subs.sample_frame(path, src_at, out_w=out_w, out_h=out_h, dst=dst)
    except Exception:  # noqa: BLE001 - un frame non leggibile non ferma il montaggio
        return None


def pick_cover_frame(
    frames: Sequence[Mapping[str, Any]], threshold: float
) -> Tuple[Mapping[str, Any], str, bool]:
    """Choose the cover among measured frames: usable first, face, then sharpest.

    Order of the two preferences matters: a frame below ``triage_sharpness_low``
    is what the gate calls unusable, so a face never rescues it. Among the usable
    frames a face wins (a cover of skin is a better cover than a wall), and if no
    frame is usable the sharpest of them all still is the cover — never the
    worst, and the caller declares ``below_threshold``.
    """
    usable = [
        row for row in frames if float(row.get("sharpness") or 0.0) >= float(threshold)
    ]
    with_face = [row for row in usable if (row.get("faces") or 0) > 0]
    pool = with_face or usable or list(frames)
    if not pool:
        raise ValueError("nessun frame da cui scegliere la copertina")
    best = pool[0]
    for row in pool[1:]:
        if float(row["sharpness"]) > float(best["sharpness"]):
            best = row
    quality = (
        "measured"
        if float(best["sharpness"]) >= float(threshold)
        else "below_threshold"
    )
    return best, quality, bool(with_face)


def thumbnail_frame(
    plan: Any,
    assets_by_id: Mapping[str, asset_triage.AssetInfo],
    opening: Optional[Mapping[str, Any]],
    *,
    threshold: float,
) -> Optional[Dict[str, Any]]:
    """The sharpest usable frame of the opening clip, with its source time.

    The cover is a deliverable, so it cannot be a frame the coherence gate calls
    unusable: frames inside the opening shot are measured, the ones with a face
    are preferred (when the detector is available), and the sharpest wins. When
    every frame is below ``triage_sharpness_low`` the best of them is still used
    and ``quality`` says ``below_threshold`` — a declared choice, not a silent
    blurry cover.
    """
    try:
        import cv2
    except Exception:  # noqa: BLE001 - without OpenCV the frame cannot be measured
        return None
    clip = _opening_clip(plan)
    if clip is None:
        return None
    chosen = (opening or {}).get("chosen") or {}
    asset_id = str(chosen.get("asset_id") or "")
    asset = assets_by_id.get(asset_id)
    if asset is None or not str(asset.path or ""):
        return None
    source_id = str(getattr(clip, "source_id", "") or "")
    # The "opening clip" is the whole opening shot: with a beat recipe the plan
    # slices it into many short clips that all read the same source in order.
    # The cover is chosen over the window the cut actually uses, not over the
    # first slice — the first 0.6 s of a phone clip are often a blurry zoom.
    siblings = [
        item
        for track in getattr(plan, "video_tracks", ()) or ()
        for item in getattr(track, "clips", ()) or ()
        if int(getattr(item, "layer", 0) or 0) == 0
        and str(getattr(item, "source_id", "") or "") == source_id
    ] or [clip]
    src_in = min(float(getattr(item, "src_in", 0.0) or 0.0) for item in siblings)
    src_out = max(float(getattr(item, "src_out", 0.0) or 0.0) for item in siblings)
    if src_out <= src_in:
        return None
    head = min(siblings, key=lambda item: float(getattr(item, "dst_in", 0.0) or 0.0))
    dst_base = float(getattr(head, "dst_in", 0.0) or 0.0)
    src_base = float(getattr(head, "src_in", 0.0) or 0.0)
    capture = cv2.VideoCapture(str(asset.path))
    if not capture.isOpened():
        capture.release()
        return None
    frames: List[Dict[str, Any]] = []
    first_frame: Optional[float] = None
    detector = None
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, src_in) * 1000.0)
        ok, frame = capture.read()
        if ok and frame is not None:
            first_frame = _frame_sharpness(
                cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            )
        for step in range(1, THUMBNAIL_SAMPLES + 1):
            at = src_in + (src_out - src_in) * (step / (THUMBNAIL_SAMPLES + 1.0))
            capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, at) * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            value = _frame_sharpness(gray)
            if value is None:
                continue
            if detector is None:
                detector = _yunet_detector(frame.shape[1], frame.shape[0])
            frames.append(
                {
                    "t_src": round(at, 3),
                    "sharpness": round(value, 2),
                    "faces": _face_count(detector, frame),
                }
            )
    finally:
        capture.release()
    if not frames:
        return None
    best, quality, face_preferred = pick_cover_frame(frames, threshold)
    dst = dst_base + (best["t_src"] - src_base)
    duration = float(getattr(plan, "duration_sec", 0.0) or 0.0)
    if duration > 0:
        dst = max(0.0, min(dst, max(0.0, duration - 0.05)))
    return {
        "clip_id": str(getattr(head, "clip_id", "") or ""),
        "source_id": source_id,
        "asset_id": asset.asset_id,
        "t_src": float(best["t_src"]),
        "t": round(float(dst), 3),
        "window": [round(src_in, 3), round(src_out, 3)],
        "sharpness": float(best["sharpness"]),
        "first_frame_sharpness": (
            None if first_frame is None else round(float(first_frame), 2)
        ),
        "faces": best.get("faces"),
        "face_preferred": bool(face_preferred),
        "threshold": float(threshold),
        "quality": quality,
        "samples": len(frames),
    }


def write_thumbnail(master: Any, at: float, target: Any) -> bool:
    """Re-extract the cover at the chosen instant, keeping the same JPEG path."""
    target = Path(target)
    temporary = target.with_name(target.name + ".tmp.jpg")
    try:
        proc = subprocess.run(
            [
                ff.ffmpeg(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{float(at):.3f}",
                "-i",
                str(master),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-update",
                "1",
                str(temporary),
            ],
            capture_output=True,
        )
    except OSError:
        temporary.unlink(missing_ok=True)
        return False
    if proc.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        return False
    temporary.replace(target)
    return True


def _timeline_est(
    selected: List[Mapping[str, Any]],
    assets_by_id: Mapping[str, asset_triage.AssetInfo],
    rejected: Sequence[Mapping[str, Any]] = (),
) -> float:
    total = 0.0
    for entry in selected:
        if entry.get("role") != "a_roll":
            continue
        asset_id = entry.get("asset_id")
        asset = assets_by_id.get(asset_id)
        if asset is None:
            raise contract.VxError(f"asset {asset_id!r} assente dal pool")
        try:
            target = float(entry.get("target_duration_sec"))
        except (TypeError, ValueError) as exc:
            raise contract.VxError(
                f"asset {asset_id!r}: target_duration_sec non numerico"
            ) from exc
        total += min(target, float(asset.duration_sec))
    if total <= 0:
        digest = _rejection_digest(rejected)
        remedy = _upscale_remedy(rejected)
        detail = f" — scarti: {digest}" if digest else ""
        if remedy:
            detail += f". Rimedio: {remedy}"
        raise contract.VxError(
            "timeline stimata non positiva: nessun a_roll utilizzabile" + detail
        )
    return total


def _specs(
    selected: List[Mapping[str, Any]],
    assets_by_id: Mapping[str, asset_triage.AssetInfo],
    fps: float,
    timeline_est: float,
) -> Dict[str, Dict[str, Any]]:
    """Only images need a spec: they have no duration of their own."""
    specs: Dict[str, Dict[str, Any]] = {}
    for entry in selected:
        asset_id = entry.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise contract.VxError("asset selezionato senza asset_id")
        if entry.get("type") != "image":
            specs[asset_id] = {}
            continue
        asset = assets_by_id.get(asset_id)
        if asset is None:
            raise contract.VxError(f"immagine {asset_id!r} assente dal pool")
        if int(asset.width) <= 0 or int(asset.height) <= 0:
            raise contract.VxError(f"immagine {asset_id!r}: geometria non valida")
        try:
            target = float(entry.get("target_duration_sec"))
        except (TypeError, ValueError) as exc:
            raise contract.VxError(
                f"immagine {asset_id!r}: target_duration_sec non numerico"
            ) from exc
        duration = min(target, timeline_est)
        if duration <= 0:
            raise contract.VxError(f"immagine {asset_id!r}: durata non positiva")
        specs[asset_id] = {
            "kind": "image",
            "width": int(asset.width),
            "height": int(asset.height),
            "fps": float(fps),
            "duration": duration,
            "treatment": entry.get("treatment") or "ken_burns_zoom_in",
        }
    return specs


def _stage(
    sources_map: Mapping[str, str],
    specs: Mapping[str, Mapping[str, Any]],
    stage_dir: Path,
) -> Dict[str, ingest.IngestResult]:
    try:
        return ingest.ingest_sources(sources_map, stage_dir, specs=specs)
    except ingest.IngestError as exc:
        raise contract.VxError(f"staging fallito: {exc}") from exc


def _source_refs(
    staged: Mapping[str, ingest.IngestResult],
) -> tuple[Dict[str, edl_plan.SourceRef], Dict[str, Dict[str, Any]]]:
    """Probe the staged file, hash it, and describe it as Gate 1 expects (dicts)."""
    refs: Dict[str, edl_plan.SourceRef] = {}
    media: Dict[str, Dict[str, Any]] = {}
    for asset_id, result in staged.items():
        path = _staged_path(result)
        info = probe.probe_asset(path)
        refs[asset_id] = edl_plan.SourceRef(
            path=str(path),
            sha256=_sha256(path),
            duration=float(info.duration),
        )
        media[asset_id] = {
            "duration": float(info.duration),
            "width": int(info.width),
            "height": int(info.height),
            "fps": float(info.fps),
            "has_audio": bool(info.has_audio),
        }
    return refs, media


def _media_by_source_key(
    plan: Any,
    staged: Mapping[str, ingest.IngestResult],
    media_info_map: Mapping[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Re-key the probe results by the EDL ``source_id`` the plan cites.

    The triage report and ``selected[]`` keep the human-readable asset id; the
    EDL carries the slugged key ``contract.source_key`` builds. Gate 1, the fill
    declaration and the face geometry look their media up by ``clip.source_id``,
    so the map has to follow the plan: ``SourceRef.path`` is the staged file the
    asset produced.
    """
    owner = {str(_staged_path(result)): asset_id for asset_id, result in staged.items()}
    media: Dict[str, Dict[str, Any]] = {}
    for key, ref in plan.sources.items():
        path = str(getattr(ref, "path", "") or "")
        asset_id = owner.get(path)
        if asset_id is None:
            raise contract.VxError(
                f"source {key!r}: nessun file in staging per {path!r}"
            )
        info = media_info_map.get(asset_id)
        if info is None:
            raise contract.VxError(
                f"source {key!r}: nessun media_info per {asset_id!r}"
            )
        media[key] = dict(info)
    return media


def _source_hashes(assets: List[asset_triage.AssetInfo]) -> Dict[str, str]:
    """sha256 of exactly the assets that will enter the master.

    Discovery can surface hundreds of files; hashing a rejected asset is 100 MB
    of reads for a number no gate ever consults. The caller passes ``selected``,
    and the same list is re-checked after the render.
    """
    return {asset.asset_id: _sha256(Path(asset.path)) for asset in assets}


def _selected_asset_infos(
    selected: Sequence[Mapping[str, Any]],
    assets_by_id: Mapping[str, asset_triage.AssetInfo],
) -> List[asset_triage.AssetInfo]:
    """The pool objects behind ``selected[]``, in order, without duplicates."""
    out: List[asset_triage.AssetInfo] = []
    seen: set[str] = set()
    for entry in selected:
        asset_id = entry.get("asset_id")
        if not isinstance(asset_id, str) or asset_id in seen:
            continue
        asset = assets_by_id.get(asset_id)
        if asset is not None:
            seen.add(asset_id)
            out.append(asset)
    return out


def _assert_sources_unchanged(
    assets: List[asset_triage.AssetInfo], before: Mapping[str, str]
) -> None:
    changed: List[str] = []
    for asset in assets:
        try:
            current = _sha256(Path(asset.path))
        except OSError:
            current = ""
        if current != before.get(asset.asset_id):
            changed.append(asset.asset_id)
    if changed:
        raise contract.QCFailure(
            {
                "ok": False,
                "failures": ["source_sha_unchanged"],
                "checks": [
                    {
                        "code": "source_sha_unchanged",
                        "status": "fail",
                        "detail": "sorgenti modificate: " + ", ".join(changed),
                    }
                ],
                "changed": changed,
            }
        )


#: Fino a questa durata la finestra di trascrizione legge **tutta** la sorgente.
#: Il ponte può riempire la coda con parlato che sta oltre il piano (l'ultimo
#: tratto utile dopo una pausa): senza il testo quel parlato resterebbe senza
#: sottotitoli. Su una sorgente corta — un reel — il costo è qualche secondo; su
#: una lunga si resta alla finestra del piano e il report dichiara
#: ``captions_tail_uncovered``.
CAPTION_FULL_READ_SEC = 60.0


def _caption_window(plan: Any, source_id: str) -> Optional[float]:
    """How far into `source_id` the plan actually reads.

    Captions only ever anchor to a clip's own window, so a source used for the
    first eight seconds does not need the other 171 transcribed. The bound is
    the largest ``src_out`` the plan cites for that source; ``None`` means the
    plan does not use it at all and the caller keeps today's full read.

    Su una sorgente entro :data:`CAPTION_FULL_READ_SEC` si legge tutto: la coda
    che il ponte riempie col parlato successivo deve poter avere i suoi
    sottotitoli, e quel parlato non era nel piano.
    """
    latest: Optional[float] = None
    for tracks in (
        getattr(plan, "video_tracks", ()) or (),
        getattr(plan, "audio_tracks", ()) or (),
    ):
        for track in tracks:
            for clip in getattr(track, "clips", ()) or ():
                if str(getattr(clip, "source_id", "")) != source_id:
                    continue
                try:
                    value = float(getattr(clip, "src_out", 0.0))
                except (TypeError, ValueError):
                    continue
                latest = value if latest is None else max(latest, value)
    ref = None
    sources = getattr(plan, "sources", None)
    if isinstance(sources, Mapping):
        ref = sources.get(source_id)
    try:
        total = float(getattr(ref, "duration", 0.0) or 0.0)
    except (TypeError, ValueError):
        total = 0.0
    if total and total <= CAPTION_FULL_READ_SEC:
        return total
    return latest


def _a_roll_sources(plan: Any) -> List[str]:
    seen: List[str] = []
    for track in getattr(plan, "video_tracks", ()):
        for clip in getattr(track, "clips", ()):
            if int(getattr(clip, "layer", 0)) != 0:
                continue
            source_id = getattr(clip, "source_id", None)
            if isinstance(source_id, str) and source_id and source_id not in seen:
                seen.append(source_id)
    return seen


MEASURE_KINDS = ("analysis", "faces", "audio")

#: Il pavimento dei thread ffmpeg per worker di misura. Su materiale 4K due
#: thread sono un collo di bottiglia (29,6 s contro 8,6 s su 20 s); il numero
#: vero lo decide `_threads_for` dalla larghezza della sorgente, poi `_measure_all`
#: lo taglia sul numero di worker per non oversubscribare i core.
WORKER_FFMPEG_THREADS = 2

#: Sotto questo lato lungo una proxy 640 costa piu' di quanto la misura
#: risparmia: il decode e' gia' piccolo, e l'encode non si ripaga. Il lato lungo,
#: non la larghezza: un verticale 1080x1920 e' tanto caro quanto un 1920x1080.
PROXY_MIN_WIDTH = 1920

#: La proxy esiste solo per questo: le metriche px a 320 px. Non tocca il
#: master, che resta libx264 alla risoluzione piena.
PROXY_WIDTH = 640

#: Sotto questa durata l'encode della proxy non si ripaga: su due secondi il
#: decode a piena risoluzione costa meno dell'encode piu' il decode della proxy.
PROXY_MIN_SECONDS = 8.0


def _threads_for(kind: str, width: int) -> int:
    """Thread ffmpeg proporzionali alla sorgente, non un 2 fisso."""
    if kind != "analysis":
        return WORKER_FFMPEG_THREADS
    try:
        width = int(width)
    except (TypeError, ValueError):
        width = 0
    if width >= 3000:
        return 8
    if width >= 1900:
        return 6
    if width >= 1200:
        return 4
    return WORKER_FFMPEG_THREADS


def _cap_threads(jobs: Sequence[Any], workers: int) -> List[Any]:
    """Never let `workers` processes each take the whole machine."""
    cpu = os.cpu_count() or 4
    share = max(WORKER_FFMPEG_THREADS, cpu // max(1, int(workers)))
    capped: List[Any] = []
    for job in jobs:
        if len(job) >= 5:
            try:
                base = int(job[4])
            except (TypeError, ValueError):
                base = WORKER_FFMPEG_THREADS
            job = tuple(job[:4]) + (max(WORKER_FFMPEG_THREADS, min(base, share)),)
        capped.append(job)
    return capped

#: Sotto questa durata complessiva delle sorgenti il processo in più costa più di
#: quanto misura: l'avvio di un interprete (cv2, numpy, ffmpeg) e' ~2 s cadauno, e
#: su clip da due secondi la versione sequenziale e' piu' veloce. Misurato, non
#: stimato: 2 s di materiale, 5.3 s in fila contro 20.3 s in parallelo.
PARALLEL_MIN_SECONDS = 10.0


def _measure_workers(jobs: int, *, concurrent: int = 0) -> Dict[str, Any]:
    """Il budget di worker per questa macchina, con i motivi del numero."""
    return resources.worker_budget(jobs, concurrent=concurrent)


def _measure_seconds(
    sources: Sequence[str], media: Optional[Mapping[str, Any]]
) -> float:
    """How much material is about to be measured, in seconds of timeline source."""
    total = 0.0
    for source_id in sources:
        info = (media or {}).get(source_id) or {}
        try:
            total += max(0.0, float(info.get("duration") or 0.0))
        except (TypeError, ValueError):
            continue
    return total


def _measure_task(job: Any) -> Dict[str, Any]:
    """One measurement, in this process. Never raises: the failure is a value.

    ``bridge.build`` treats a source that could not be measured as a declared
    skip, so the task returns ``{"error": str}`` instead of a traceback that
    would have to be rebuilt in the parent. Le impostazioni effettive arrivano
    da fuori: un processo figlio non eredita la cascata ``--settings``/
    ``--override`` della madre, e misure con soglie diverse non sarebbero le
    stesse misure.

    Un figlio tiene un thread OpenCV e due ffmpeg: sei processi che si
    moltiplicano i thread sono piu' rumorosi, non piu' veloci.
    """
    kind, path, settings_blob = job[0], job[1], job[2]
    proxy_path = job[3] if len(job) > 3 else None
    threads = job[4] if len(job) > 4 else WORKER_FFMPEG_THREADS
    try:
        threads = int(threads)
    except (TypeError, ValueError):
        threads = WORKER_FFMPEG_THREADS
    started = time.monotonic()
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    try:
        try:  # OpenCV crea i suoi thread: uno per processo e' la regola.
            import cv2  # type: ignore

            cv2.setNumThreads(1)
        except Exception:  # noqa: BLE001 - senza OpenCV si misura in modo degradato
            pass
        from . import settings as settings_mod

        settings_mod.apply(dict(settings_blob))
        # Il pin vale per questa misura e basta: nessun ``VX_FFMPEG_THREADS``
        # nell'ambiente, che resterebbe attivo anche per il render successivo
        # nello stesso processo e cambierebbe il bitstream.
        with resources.ffmpeg_thread_limit(threads):
            if kind == "audio":
                from . import audio

                value: Any = audio.analyze_audio(path)
            elif kind == "analysis":
                from . import analyze

                value = analyze.analyze_video(
                    path, proxy_path=proxy_path or None
                )
            elif kind == "faces":
                from . import faces

                value = faces.detect_faces_tracked(
                    path, sample_fps=contract.SAMPLE_FPS_FACE
                )
            else:  # pragma: no cover - guarded by MEASURE_KINDS
                raise ValueError(f"misura sconosciuta: {kind!r}")
    except Exception as exc:  # noqa: BLE001 - the bridge turns it into a declaration
        return {
            "kind": kind,
            "error": str(exc) or type(exc).__name__,
            "seconds": time.monotonic() - started,
        }
    return {"kind": kind, "value": value, "seconds": time.monotonic() - started}


def _settings_blob() -> Dict[str, Any]:
    """The live cascade, bookkeeping excluded: ``apply`` recomputes those itself."""
    return {
        key: value
        for key, value in dict(settings.current() or {}).items()
        if key not in ("_fingerprint", "schema")
    }


def _measure_all(
    jobs: Sequence[Any],
    *,
    parallel: bool,
    clock: _Clock,
    workdir: Any = "",
) -> List[Dict[str, Any]]:
    """Run every (source, kind) measurement and return one result per job, in order.

    Parallel over processes bounded by :func:`_measure_workers`; a pool that
    cannot start falls back to the serial path instead of failing the run.
    Il tempo di ogni misura e' quello del processo che l'ha eseguita; in
    parallelo la quota attribuita a ogni stadio e' ripartita sul muro del
    blocco, cosi' la tabella somma ancora al totale.

    Il budget si calcola prima di partire e si scrive nel profilo: core logici,
    RAM libera, altri lavori pesanti in corso. Un numero di worker senza il suo
    motivo non si puo' discutere.
    """
    concurrent = resources.heavy_jobs()
    budget = _measure_workers(len(jobs), concurrent=concurrent)
    budget["mode"] = "parallelo" if (parallel and len(jobs) > 1) else "in fila"
    if budget["mode"] == "in fila" and not parallel:
        budget["reasons"] = list(budget.get("reasons") or []) + [
            f"materiale sotto {PARALLEL_MIN_SECONDS:g} s: un processo in meno costa meno"
        ]
    clock.facts["resources"] = budget
    jobs = _cap_threads(
        jobs,
        int(budget["workers"]) if (parallel and len(jobs) > 1) else 1,
    )
    if any(len(job) >= 5 for job in jobs):
        clock.facts["ffmpeg_threads"] = max(int(job[4]) for job in jobs if len(job) >= 5)
    if not parallel or len(jobs) < 2:
        results = []
        for job in jobs:
            kind = job[0]
            started = time.monotonic()
            result = _measure_task(job)
            clock.add(kind, time.monotonic() - started)
            results.append(result)
        return results

    workers = int(budget["workers"])
    started = time.monotonic()
    results: Optional[List[Dict[str, Any]]] = None
    try:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor

        context = multiprocessing.get_context("spawn")
        with resources.register_job("analysis", workdir):
            with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
                results = list(pool.map(_measure_task, list(jobs), chunksize=1))
    except Exception as exc:  # noqa: BLE001 - a broken pool is slow, not fatal
        _log(f"[auto] misure in parallelo non disponibili ({exc}); una alla volta")
        results = None
    if results is None:
        results = []
        for job in jobs:
            kind = job[0]
            one = time.monotonic()
            results.append(_measure_task(job))
            clock.add(kind, time.monotonic() - one)
        return results

    wall = time.monotonic() - started
    spent = sum(float(item.get("seconds") or 0.0) for item in results)
    for item in results:
        owned = float(item.get("seconds") or 0.0)
        share = wall * (owned / spent) if spent > 0 else 0.0
        clock.add(str(item["kind"]), share)
    clock.add("measure_wall", wall)
    return results


def _source_duration(media: Optional[Mapping[str, Any]], source_id: str) -> float:
    info = (media or {}).get(source_id) or {}
    try:
        return max(0.0, float(info.get("duration") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _source_width(media: Optional[Mapping[str, Any]], source_id: str) -> int:
    """Long edge of the source: the dimension that sets the decode cost."""
    info = (media or {}).get(source_id) or {}
    try:
        width = max(0, int(info.get("width") or 0))
    except (TypeError, ValueError):
        width = 0
    try:
        height = max(0, int(info.get("height") or 0))
    except (TypeError, ValueError):
        height = 0
    return max(width, height)


def _source_proxies(
    paths: Mapping[str, str],
    media: Optional[Mapping[str, Any]],
    workdir: Any,
    clock: "_Clock",
) -> tuple[Dict[str, str], Dict[str, str]]:
    """One 640 proxy per measured source, only where it pays for itself.

    The pixel metrics are scale-normalised, so measuring a 4K file on a 640 px
    proxy is the same verdict for a fraction of the decode. A source whose proxy
    cannot be built is not fatal: the analysis falls back to the full-resolution
    path, and the profile says which source took it.
    """
    proxies: Dict[str, str] = {}
    notes: Dict[str, str] = {}
    if not workdir:
        return proxies, notes
    root = Path(workdir) / "proxy"
    for index, (source_id, path) in enumerate(paths.items()):
        width = _source_width(media, source_id)
        if not path:
            continue
        if width and width < PROXY_MIN_WIDTH:
            notes[source_id] = f"non necessaria ({width} px)"
            continue
        duration = _source_duration(media, source_id)
        if duration and duration < PROXY_MIN_SECONDS:
            notes[source_id] = f"non necessaria ({duration:.1f} s)"
            continue
        dst = root / f"measure_{index}.mp4"
        try:
            with clock.time("proxy"):
                data = proxy_mod.make_proxy(
                    path, dst, width=min(PROXY_WIDTH, width or PROXY_WIDTH)
                )
            proxies[source_id] = str(data.get("path") or dst)
            notes[source_id] = "creata"
        except Exception as exc:  # noqa: BLE001 - fallback is today's path
            notes[source_id] = f"assente, misura a piena risoluzione: {exc}"
    return proxies, notes


def subs_requested(args: Any) -> bool:
    """I sottotitoli si chiedono col flag **o** a parole nel prompt.

    `--no-subs` vince sempre (è un rifiuto esplicito); `--subs` accende; altrimenti
    conta la richiesta scritta: "con sottotitoli", "sottotitolato", "subtitles".
    Prima il prompt non veniva letto e il lavoro usciva senza testo dichiarando
    "no speech", che era falso: lo stadio del parlato non era mai partito.
    """
    if bool(getattr(args, "no_subs", False)):
        return False
    if bool(getattr(args, "subs", False)):
        return True
    prompt = str(getattr(args, "prompt", "") or "").lower()
    return any(
        needle in prompt
        for needle in ("sottotitol", "subtitl", "subtitle", "didascal")
    )


def _bridge_plan(
    plan: Any,
    *,
    captions: bool = True,
    media: Optional[Mapping[str, Any]] = None,
    target_lufs: float = TARGET_LUFS,
    clock: Optional[_Clock] = None,
    workdir: Any = "",
) -> Any:
    """Run the bridge: analyses, transcripts, faces and motion per a_roll source.

    A transcription is attempted only when ``captions`` is on. ``subs.transcribe``
    never raises, but it reports a skip as ``skipped_reason``; both that and any
    exception are handed to ``bridge.build`` as a value, which turns them into a
    ``transcript_failed`` declaration instead of a dead run.

    Face detection and the silent video analysis feed A6 and A7 the same way: a
    detector without OpenCV, a missing model or a clip the analyser cannot decode
    travels as the exception it raised and becomes a declared skip, never a crash
    and never a filter applied without a finding. ``media`` supplies the frame
    size of each staged source, because face boxes are pixels and the crop the
    renderer animates is normalised.

    Le tre misure per sorgente sono indipendenti e pure: sotto
    ``PARALLEL_MIN_SECONDS`` di materiale girano in fila, sopra girano in
    processi paralleli (max ``cpu - 2``) e i risultati si ricompongono
    nell'ordine delle sorgenti. Stesso file, stesse funzioni, stesse soglie:
    il piano non cambia, cambia quanto ci mette.
    """
    from . import audio, bridge, subs

    prof = clock or _Clock()
    analyses: Dict[str, Any] = {}
    transcripts: Dict[str, Any] = {}
    face_tracks: Dict[str, Any] = {}
    motion: Dict[str, Any] = {}
    sources = _a_roll_sources(plan)
    paths = {
        source_id: str(getattr(plan.sources.get(source_id), "path", "") or "")
        for source_id in sources
    }
    blob = _settings_blob()
    proxies, proxy_notes = _source_proxies(paths, media, workdir, prof)
    if proxy_notes:
        prof.facts["proxies"] = proxy_notes
    keys: List[tuple] = [
        (kind, source_id)
        for source_id in sources
        for kind in MEASURE_KINDS
        if paths[source_id]
    ]
    jobs = [
        (
            kind,
            paths[source_id],
            blob,
            proxies.get(source_id),
            _threads_for(kind, _source_width(media, source_id)),
        )
        for kind, source_id in keys
    ]
    for source_id in sources:
        if not paths[source_id]:
            # Senza un file non c'e' misura: la sorgente resta una dichiarazione.
            reason = RuntimeError(f"source {source_id!r}: nessun percorso")
            analyses[source_id] = reason
            motion[source_id] = reason
            face_tracks[source_id] = reason
    measured = _measure_all(
        jobs,
        parallel=_measure_seconds(sources, media) >= PARALLEL_MIN_SECONDS,
        clock=prof,
        workdir=workdir,
    )
    by_key: Dict[tuple, Dict[str, Any]] = {
        key: item for key, item in zip(keys, measured)
    }
    for source_id in sources:
        if not paths[source_id]:
            continue
        for kind, bucket in (
            ("audio", analyses),
            ("analysis", motion),
            ("faces", face_tracks),
        ):
            item = by_key.get((kind, source_id))
            if item is None:
                bucket[source_id] = RuntimeError(f"nessuna misura {kind}")
            elif "error" in item:
                bucket[source_id] = RuntimeError(str(item["error"]))
            else:
                bucket[source_id] = item["value"]
        tracked = face_tracks.get(source_id)
        if not isinstance(tracked, BaseException):
            info = (media or {}).get(source_id) or {}
            payload: Dict[str, Any] = dict(tracked.to_dict())
            payload["width"] = info.get("width")
            payload["height"] = info.get("height")
            cuts = getattr(motion.get(source_id), "scene_cuts", None)
            if cuts:
                payload["scene_cuts"] = list(cuts)
            face_tracks[source_id] = payload
        if not captions:
            continue
        try:
            with prof.time("speech"):
                # Whisper e' lungo e in-process: la macchina deve restare
                # usabile mentre lavora, e il report lo dichiara.
                lowered = resources.set_current_priority(True)
                try:
                    transcript = subs.transcribe(
                        paths[source_id],
                        max_duration=_caption_window(plan, source_id),
                    )
                finally:
                    resources.set_current_priority(False)
            if lowered:
                prof.facts["priority"] = "below_normal"
            prof.facts.setdefault("whisper", {})[source_id] = {
                "device": str(getattr(transcript, "device", "") or ""),
                "model": str(getattr(transcript, "model", "") or ""),
            }
            reason = str(getattr(transcript, "skipped_reason", "") or "")
            if reason:
                raise RuntimeError(reason)
            transcripts[source_id] = transcript
        except Exception as exc:  # noqa: BLE001 - the bridge declares it
            transcripts[source_id] = exc
    with prof.time("bridge"):
        new_plan, report = bridge.build(
            plan,
            analyses,
            target_lufs=float(target_lufs),
            loudness_spans=audio.loudness_spans,
            transcripts=transcripts if captions else None,
            faces=face_tracks,
            motion=motion,
        )
    return new_plan, report, analyses, motion


def audio_summary(
    analyses: Mapping[str, Any],
    ceiling_dbtp: Optional[float],
) -> Dict[str, Any]:
    """The audio block of the report: measured values, each with its threshold.

    ``snr_db`` takes the worst source and ``music_ratio`` the most musical one,
    because averaging would hide both: the report has to name the clip that needed
    denoise most and the one that is a bed candidate. A source whose analysis
    raised is skipped, never counted as zero. With no measurement the value is
    None and the threshold is still declared, so the absence stays readable.
    """

    def field(item: Any, name: str) -> Optional[float]:
        value = (
            item.get(name) if isinstance(item, Mapping) else getattr(item, name, None)
        )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    snr = [
        value
        for value in (field(item, "snr_db") for item in analyses.values())
        if value is not None
    ]
    music = [
        value
        for value in (field(item, "music_ratio") for item in analyses.values())
        if value is not None
    ]
    return {
        "ceiling_dbtp": ceiling_dbtp,
        "limiter": "alimiter" if ceiling_dbtp is not None else None,
        "snr_db": round(min(snr), 2) if snr else None,
        "snr_threshold_db": float(contract.THRESHOLDS["noise_snr_low"]),
        "music_ratio": round(max(music), 3) if music else None,
        "music_ratio_threshold": float(contract.THRESHOLDS["triage_music_ratio_low"]),
    }


def music_summary(plan: Any, report: Any) -> List[Dict[str, Any]]:
    """One entry per music bed: what the bridge measured and the ducking it applied.

    The block is a list because a bed is a track, not a slot: two beds can carry
    different gain, different loop decisions and, above all, different ducking.
    A report that flattens them to one number cannot be audited.
    """
    specs = {
        str(getattr(spec, "target_role", "")): spec
        for spec in getattr(plan, "ducking_tracks", ()) or ()
    }
    default_spec = getattr(plan, "ducking", None)
    measured = {
        item.get("clip_id"): item
        for item in (getattr(report, "audio", ()) or ())
        if str(item.get("role", "")).startswith("music")
    }
    beds: List[Dict[str, Any]] = []
    for track in getattr(plan, "audio_tracks", ()):
        role = str(getattr(track, "role", "") or "")
        if not (role == "music" or role.startswith("music_")):
            continue
        spec = specs.get(role, default_spec)
        for clip in getattr(track, "clips", ()):
            item = measured.get(clip.clip_id) or {}
            ref = getattr(plan, "sources", {}).get(clip.source_id)
            beds.append(
                {
                    "role": role,
                    "track_id": int(track.track_id),
                    "clip_id": clip.clip_id,
                    "source_id": clip.source_id,
                    "sha256": str(getattr(ref, "sha256", "") or ""),
                    "gain_db": float(clip.gain_db),
                    "looped": item.get("looped"),
                    "loops": item.get("loops"),
                    "crossfade_sec": item.get("crossfade_sec"),
                    "ducking_threshold": (
                        round(float(spec.threshold), 6) if spec is not None else None
                    ),
                    "ducking_ratio": (
                        round(float(spec.ratio), 6) if spec is not None else None
                    ),
                    "ducking_ratio": (float(spec.ratio) if spec is not None else None),
                }
            )
    return beds


def _beat_report(
    plan: Any,
    provenance: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> Dict[str, Any]:
    """Quanti tagli ha prodotto la griglia e quanto sono densi.

    ``cuts`` e' il numero di confini interni alla traccia principale (clip meno
    uno): un numero che si puo' confrontare col BPM, non un'impressione.
    """
    duration = float(getattr(plan, "duration_sec", 0.0) or 0.0)
    tracks = [
        track
        for track in getattr(plan, "video_tracks", ()) or ()
        if str(getattr(track, "role", "")) == "a_roll"
    ]
    clips = sum(len(getattr(track, "clips", ()) or ()) for track in tracks)
    cuts = sum(
        max(0, len(getattr(track, "clips", ()) or ()) - 1) for track in tracks
    )
    block = payload.get("beat_grid") if isinstance(payload, Mapping) else None
    block = block if isinstance(block, Mapping) else {}
    return {
        "bpm": provenance.get("bpm"),
        "bpm_source": provenance.get("bpm_source"),
        "enabled": bool(provenance.get("enabled")),
        "mode": str(block.get("mode") or "auto"),
        "clips": clips,
        "cuts": cuts,
        "density_per_sec": round(cuts / duration, 3) if duration > 0 else None,
    }


def _audio_tracks_report(plan: Any) -> List[Dict[str, Any]]:
    """La timeline audio in chiaro: bus, clip e sorgenti (il letto e' ``music``)."""
    rows: List[Dict[str, Any]] = []
    for track in getattr(plan, "audio_tracks", ()) or ():
        clips = list(getattr(track, "clips", ()) or ())
        rows.append(
            {
                "track_id": int(getattr(track, "track_id", 0) or 0),
                "role": str(getattr(track, "role", "") or ""),
                "clips": len(clips),
                "source_ids": sorted(
                    {str(clip.source_id) for clip in clips if getattr(clip, "source_id", "")}
                ),
                "dst_in": round(min((float(c.dst_in) for c in clips), default=0.0), 3),
                "dst_out": round(max((float(c.dst_out) for c in clips), default=0.0), 3),
                "gain_db": round(
                    min((float(c.gain_db) for c in clips), default=0.0), 2
                ),
            }
        )
    return rows


def _render_effects(report: Any) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """The render parameters the report declared: keyframes and resolved filters.

    Only what the bridge measured reaches the graph: a clip without keyframes is
    absent from ``reframe`` and keeps its fill policy, and a clip without a
    justified finding is absent from ``restore``.
    """
    reframe: Dict[str, Any] = {
        item["clip_id"]: item["keyframes"]
        for item in getattr(report, "reframe", ())
        if item.get("keyframes")
    }
    restore: Dict[str, Any] = {
        item["clip_id"]: {
            "denoise": item.get("denoise_filter") if item.get("denoise") else None,
            "stabilize": bool(item.get("stabilize")),
        }
        for item in getattr(report, "restore", ())
    }
    return reframe, restore


def _shorten_variety(plan: Any, workdir: Path) -> Tuple[Any, Dict[str, Any]]:
    """C8, legge B: applica ``coherence.shorten_plan`` prima del ponte.

    Il taglio delle ripetizioni deve avvenire **prima** che il ponte costruisca
    caption, reframe e tracce audio: accorciare dopo disallineerebbe tutto. I
    fotogrammi sono quelli campionati sullo stesso piano che si sta per montare.
    Un accorciamento impossibile non e' un ``ok``: il blocco porta il motivo e
    ``removed: 0``.
    """
    from . import coherence  # noqa: PLC0415 - import cost at call time

    try:
        # I fotogrammi campionati sono un intermedio: vanno sotto `cache/`, che e'
        # una cartella dichiarata del lavoro. Scritti in `<workdir>/variety/`
        # facevano comparire un artefatto in piu' e il test di crash (che
        # controlla il layout della workdir) lo segnalava come corpo estraneo.
        frames = coherence.sample_frames(plan, workdir / "cache" / "variety")
        candidate, block = coherence.shorten_plan(plan, frames)
    except Exception as exc:  # noqa: BLE001 - il montaggio prosegue, il report no
        degrade.note("variety", exc)
        return plan, {
            "removed": 0,
            "duration_before": round(float(getattr(plan, "duration_sec", 0.0)), 3),
            "duration_after": round(float(getattr(plan, "duration_sec", 0.0)), 3),
            "threshold": int(contract.THRESHOLDS["variety_repeat_max"]),
            "min_duration": float(contract.THRESHOLDS["variety_min_duration_sec"]),
            "below_minimum": False,
            "remaining_runs": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if block.get("removed"):
        _say(
            f"Varieta': {block['removed']} inquadrature ripetute tolte, "
            f"{block['duration_before']:.2f}s -> {block['duration_after']:.2f}s "
            f"(soglia {block['threshold']})"
        )
    return candidate, block


def _effect_evidence(
    plan: Any,
    motion: Mapping[str, Any],
    bridge_report: Any = None,
    frames: Optional[Mapping[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Le misure per clip che rendono ammissibile una spec (C8, legge A).

    Ogni clip di layer 0 porta solo cio' che e' stato misurato su di lei:
    jitter e cast colore dall'analisi della sorgente, volti inseguiti dai
    keyframe del ponte, bordo/centro dal fotogramma campionato dalla coerenza.
    Un dato assente resta assente: nessun default zero.
    """
    tracked = {
        str(item.get("clip_id"))
        for item in getattr(bridge_report, "reframe", ()) or ()
        if item.get("keyframes")
    }
    out: Dict[str, Dict[str, Any]] = {}
    for track in getattr(plan, "video_tracks", ()) or ():
        for clip in getattr(track, "clips", ()) or ():
            if int(getattr(clip, "layer", 0) or 0) != 0:
                continue
            clip_id = str(getattr(clip, "clip_id", "") or "")
            if not clip_id:
                continue
            measures: Dict[str, Any] = {}
            source_id = str(getattr(clip, "source_id", "") or "")
            analysis = motion.get(source_id)
            if analysis is not None and not isinstance(analysis, BaseException):
                to_dict = getattr(analysis, "to_dict", None)
                data = to_dict() if callable(to_dict) else analysis
                if isinstance(data, Mapping):
                    jitter = data.get("jitter")
                    if isinstance(jitter, Mapping):
                        measures["jitter"] = jitter.get("score")
                    color = data.get("color")
                    if isinstance(color, Mapping):
                        measures["cast_rg"] = color.get("cast_rg")
                        measures["cast_by"] = color.get("cast_by")
                        measures["saturation"] = color.get("saturation")
            measures["faces"] = 1 if clip_id in tracked else 0
            if frames and clip_id in frames:
                measures.update(efx.frame_measures(frames[clip_id]))
            out[clip_id] = measures
    return out


def _coherence_section(
    args: Any,
    workdir: Path,
    plan: Any,
    bridge_report: Any,
) -> Tuple[Dict[str, Any], Dict[int, str]]:
    """Il referto di coerenza, calcolato prima del render.

    Ritorna ``(referto, fotogrammi)``: i PNG campionati servono alla legge A
    (prova del bordo) senza estrarli una seconda volta. Senza flag il lavoro
    prosegue anche con violazioni: il report e la narrazione devono dire quali
    sono, non bloccare in silenzio ne' fingere un successo pulito.
    ``--free``/``--no-coherence`` spegne i controlli e lo dichiara
    (``disabled``), perche' un ``ok`` senza controllo non e' un ``ok``.
    """
    from . import coherence

    frames: Dict[int, str] = {}
    if bool(getattr(args, "free", False) or getattr(args, "no_coherence", False)):
        section = coherence.disabled("--free/--no-coherence")
        _say(f"Coerenza: {section['reason']}")
        return section, frames
    try:
        frames = coherence.sample_frames(plan, workdir / "coherence")
        section = coherence.evaluate(
            plan,
            bridge=bridge_report,
            frames=frames,
            text_required=bool(getattr(args, "subs", False)),
        )
    except Exception as exc:  # noqa: BLE001 - un referto rotto non ferma il render
        reason = f"referto non calcolato: {type(exc).__name__}: {exc}"
        _say(f"Coerenza: {reason}")
        return {
            "ok": False,
            "violations": [],
            "error": reason,
            "metrics": {
                "checked": False,
                "unknown": [{"check": "coherence", "reason": reason}],
            },
        }, {}
    metrics = section.get("metrics") or {}
    if section.get("ok"):
        _say(
            "Coerenza: ok "
            f"({metrics.get('shots')} shot, varieta' {metrics.get('variety')}, "
            f"{metrics.get('justified_cuts')}/{metrics.get('cuts')} tagli giustificati)"
        )
    else:
        codes = ", ".join(str(item.get("code")) for item in section["violations"])
        _say(f"Coerenza: {len(section['violations'])} violazioni — {codes}")
        for item in section["violations"]:
            _say(f"  {item.get('code')}: {item.get('evidence')}")
    return section, frames


def _say(msg: str) -> None:
    """Una riga per chi guarda: stdout col narratore, stderr con ``--json``.

    Con ``--json`` lo stdout e' il documento, quindi l'avviso non puo' finirci;
    il narratore invece parla proprio li' e la riga va letta insieme alle altre.
    Chi ha gia' riservato lo stdout per i fatti suoi (il worker, che per
    contratto deve restare muto) lo dichiara con ``VX_STDOUT_RESERVED``.
    """
    stream = sys.stdout if (_NARRATING and not _STDOUT_RESERVED) else sys.stderr
    try:
        print(msg, file=stream, flush=True)
    except Exception:  # noqa: BLE001 - uno stream chiuso non ferma il montaggio
        pass


def font_network_available(timeout: float = 0.8) -> bool:
    """Se il catalogo risponde, in meno di un secondo e senza scaricare niente.

    La proposta di font e' l'unico punto che puo' uscire in rete; quando il
    socket non si apre si resta sul pool locale invece di aspettare un timeout
    di download per ogni famiglia.
    """
    import socket

    try:
        with socket.create_connection(("raw.githubusercontent.com", 443), timeout):
            return True
    except OSError:
        return False


def _rationale(payload: Any) -> Mapping[str, Any]:
    """Il blocco creativo del payload, o un vuoto: qui non si inventa nulla."""
    if not isinstance(payload, Mapping):
        return {}
    context = payload.get("execution_context")
    if not isinstance(context, Mapping):
        return {}
    rationale = context.get("creative_rationale")
    return rationale if isinstance(rationale, Mapping) else {}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


# ----------------------------------------------------------- the decision log


def _display(value: Any, unit: str = "") -> str:
    """Un numero leggibile in una prova; ``n/d`` quando non c'e' misura."""
    try:
        return f"{float(value):g}{unit}"
    except (TypeError, ValueError):
        return "n/d"


def _decider(selected_by: Any) -> str:
    """Traduce la provenienza della sezione in ``auto``/``user``/``timeout``."""
    text = str(selected_by or "").strip().lower()
    if text == "user":
        return "user"
    if "timeout" in text:
        return "timeout"
    return "auto"


def _decision_font(
    section: Any,
    *,
    requirement: str,
    energy: float,
    plan: Any,
) -> Dict[str, Any]:
    """La famiglia scelta, con i punteggi e la licenza che l'hanno sostenuta."""
    data = section if isinstance(section, Mapping) else {}
    candidates = [
        item for item in (data.get("candidates") or ()) if isinstance(item, Mapping)
    ]
    family = str(data.get("family") or "").strip()
    pool = str(data.get("pool") or "locale")
    selected_by = str(data.get("selected_by") or "auto")
    resolution = tuple(getattr(plan, "resolution", ()) or ())
    width = int(resolution[0]) if len(resolution) > 0 else 0
    height = int(resolution[1]) if len(resolution) > 1 else 0
    frame = f"{width}x{height}" if width and height else "il formato di consegna"
    falsified = (
        f"il titolo a {frame} risulta illeggibile o fuori tono rispetto al brief"
    )
    if family:
        item = next(
            (c for c in candidates if str(c.get("family") or "") == family), {}
        )
        score = item.get("score")
        evidence = [
            f"richiesta '{requirement or 'montaggio'}' con energia {float(energy):.3f}",
            f"{len(candidates)} candidati commerciali dal pool {pool}; "
            f"{family} score {_display(score)}",
        ]
        licence = str(data.get("licence") or "").strip()
        if licence:
            evidence.append(f"licenza {licence} dichiarata dalla proposta")
        waited = float(data.get("waited_sec") or 0.0)
        if waited > 0:
            evidence.append(
                f"attesa {waited:g} s prima della scelta ({selected_by})"
            )
        fallback = str(data.get("fallback_reason") or "").strip()
        if fallback:
            evidence.append(f"ripiego sul preset: {fallback}")
        confidence = (
            "high"
            if len(candidates) >= 2 and score is not None and not fallback
            else "medium"
        )
        if selected_by == "auto_after_timeout":
            confidence = "medium"
        alternatives = [
            {
                "option": str(other.get("family") or ""),
                "why_not": (
                    f"score {_display(other.get('score'))} contro "
                    f"{_display(score)}"
                ),
            }
            for other in candidates
            if str(other.get("family") or "") != family
        ]
        return decisions.record(
            id="font",
            question=f"quale famiglia per {requirement or 'il montaggio'}?",
            chosen=family,
            confidence=confidence,
            evidence=evidence,
            alternatives=alternatives,
            alternatives_note=(
                ""
                if alternatives
                else "un solo candidato commerciale utilizzabile: nessuna alternativa reale"
            ),
            falsified_if=falsified,
            by=_decider(selected_by),
        )
    error = str(data.get("error") or "nessun font proposto").strip()
    return decisions.record(
        id="font",
        question=f"quale famiglia per {requirement or 'il montaggio'}?",
        chosen="preset del contratto",
        confidence="low",
        evidence=[
            f"0 candidati utilizzabili dal pool {pool}",
            f"motivo: {error}",
        ],
        alternatives=[
            {
                "option": "forzare un font del catalogo",
                "why_not": "nessuna licenza commerciale verificata nella proposta",
            }
        ],
        falsified_if=falsified,
        by=_decider(selected_by),
    )


def _decision_keyframes(section: Any, plan: Any) -> Dict[str, Any]:
    """I fotogrammi: quanti, a che istanti, e se qualcuno li ha approvati."""
    data = section if isinstance(section, Mapping) else {}
    count = int(data.get("count") or 0)
    approved = bool(data.get("approved"))
    selected_by = str(data.get("selected_by") or "none")
    duration = float(getattr(plan, "duration_sec", 0.0) or 0.0)
    digest = str(data.get("digest") or "")
    waited = float(data.get("waited_sec") or 0.0)
    reason = str(data.get("reason") or "").strip()
    falsified = (
        "un fotogramma approvato non corrisponde al clip/istante dichiarato "
        "o il digest non copre la timeline renderizzata"
    )
    if count <= 0:
        return decisions.record(
            id="keyframes",
            question="quali fotogrammi mostrare prima del render?",
            chosen="nessun fotogramma: si prosegue",
            confidence="low",
            evidence=[
                f"0 fotogrammi proposti su una timeline di {duration:.2f} s",
                f"motivo: {reason or 'piano dei fotogrammi non disponibile'}",
            ],
            alternatives=[],
            alternatives_note=(
                "nessun piano da approvare: non c'erano fotogrammi fra cui scegliere"
            ),
            falsified_if=falsified,
            by=_decider(selected_by),
        )
    chosen = (
        f"{count} fotogrammi approvati"
        if approved
        else f"{count} fotogrammi, render senza approvazione"
    )
    evidence = [
        f"{count} fotogrammi ai quartili 25/50/75% di {duration:.2f} s",
        f"digest {digest[:12] or 'n/d'} su {len(data.get('frames') or ())} PNG",
    ]
    if waited > 0:
        evidence.append(f"attesa {waited:g} s ({selected_by})")
    if not approved and reason:
        evidence.append(f"nessuna approvazione: {reason}")
    confidence = "high" if approved else ("low" if selected_by == "timeout" else "medium")
    return decisions.record(
        id="keyframes",
        question="quali fotogrammi mostrare prima del render?",
        chosen=chosen,
        confidence=confidence,
        evidence=evidence,
        alternatives=(
            [
                {
                    "option": "aspettare oltre il tetto",
                    "why_not": f"il render sarebbe rimasto fermo oltre {waited:g} s",
                }
            ]
            if selected_by == "timeout"
            else []
        ),
        alternatives_note=(
            ""
            if selected_by == "timeout"
            else "nessuna alternativa reale: il cancello ha un tetto e non blocca il render"
        ),
        falsified_if=falsified,
        by=_decider(selected_by),
    )


def _decision_plan(plan_choice: Any, estimate_section: Any) -> Dict[str, Any]:
    """Full o fast: la stima, il tetto e cosa si e' rinunciato a fare."""
    choice = plan_choice if isinstance(plan_choice, Mapping) else {}
    info = estimate_section if isinstance(estimate_section, Mapping) else {}
    chosen = str(choice.get("chosen") or "full")
    selected_by = str(choice.get("selected_by") or "budget")
    budget = choice.get("budget_seconds")
    high = info.get("high")
    low = info.get("low")
    resolution = info.get("resolution") or ()
    if len(resolution) >= 2:
        frame = f"{int(resolution[0])}x{int(resolution[1])}"
    else:
        frame = "n/d"
    evidence = []
    if high is not None and budget is not None:
        evidence.append(
            f"stima {_display(low)}-{_display(high)} s contro budget "
            f"{_display(budget)} s"
        )
    evidence.append(
        f"preset {info.get('preset') or 'n/d'}, risoluzione {frame}, "
        f"calibrato={bool(info.get('calibrated'))}"
    )
    traded = list(choice.get("traded") or ())
    if chosen == "fast":
        evidence.append(
            f"risparmio stimato {_display(choice.get('saved_seconds_estimate'), ' s')}; "
            f"{len(traded)} compromessi: {', '.join(str(item) for item in traded[:3])}"
        )
    else:
        evidence.append(
            f"piano completo: {_display(info.get('seconds'), ' s')} stimati, "
            f"{int(choice.get('asked') and 1 or 0)} domande poste"
        )
    if selected_by == "timeout":
        confidence = "low"
    elif selected_by == "user":
        confidence = "medium"
    elif high is not None and budget is not None and float(high) <= float(budget):
        confidence = "high"
    else:
        confidence = "medium"
    falsified = (
        f"il master supera il budget di {_display(budget, ' s')} o la versione "
        "rapida cambia la struttura di layer 0"
    )
    if chosen == "fast":
        alternatives = [
            {
                "option": "full",
                "why_not": (
                    f"la stima alta {_display(high, ' s')} supera il budget di "
                    f"{_display(budget, ' s')}"
                ),
            }
        ]
    elif selected_by == "budget":
        alternatives = [
            {
                "option": "fast",
                "why_not": "il piano completo sta nel budget: la versione rapida non serviva",
            }
        ]
    else:
        alternatives = [
            {
                "option": "fast",
                "why_not": "la scelta umana ha tenuto il piano completo; la versione "
                "rapida avrebbe abbassato risoluzione e preset",
            }
        ]
    return decisions.record(
        id="plan",
        question="piano completo o versione rapida?",
        chosen=f"{chosen} ({selected_by})",
        confidence=confidence,
        evidence=evidence,
        alternatives=alternatives,
        falsified_if=falsified,
        by=_decider(selected_by),
    )


def _decision_coherence(section: Any) -> Dict[str, Any]:
    """Le violazioni trovate e la scelta fra accettare, correggere e spegnere."""
    data = section if isinstance(section, Mapping) else {}
    metrics = data.get("metrics") if isinstance(data.get("metrics"), Mapping) else {}
    violations = [
        item for item in (data.get("violations") or ()) if isinstance(item, Mapping)
    ]
    unknown = list(metrics.get("unknown") or ())
    checked = not unknown and metrics.get("variety") is not None
    falsified = (
        "un controllo rifatto sul master trova una violazione che il referto non elenca"
    )
    if data.get("disabled"):
        return decisions.record(
            id="coherence",
            question="il montaggio rispetta le regole di coerenza?",
            chosen="--free: controlli spenti",
            confidence="low",
            evidence=[
                "0 controlli eseguiti e 0 fotogrammi misurati: nessuna prova disponibile",
                f"motivo dichiarato: {data.get('reason') or '--free'}",
            ],
            alternatives=[
                {
                    "option": "eseguire i controlli",
                    "why_not": "la richiesta esplicita li ha spenti per questo giro",
                }
            ],
            falsified_if=falsified,
            by="user",
        )
    if data.get("error"):
        return decisions.record(
            id="coherence",
            question="il montaggio rispetta le regole di coerenza?",
            chosen="referto non calcolato: si prosegue",
            confidence="low",
            evidence=[
                f"errore: {data.get('error')}",
                "0 violazioni lette perche' il referto non esiste",
            ],
            alternatives=[
                {
                    "option": "fermare il render",
                    "why_not": "un referto rotto non e' una violazione del montaggio",
                }
            ],
            falsified_if=falsified,
            by="auto",
        )
    shots = metrics.get("shots")
    variety = metrics.get("variety")
    justified = metrics.get("justified_cuts")
    cuts = metrics.get("cuts")
    if violations:
        codes = ", ".join(str(item.get("code")) for item in violations)
        evidence = [
            f"{len(violations)} violazioni: {codes}",
            f"{_display(shots)} shot, varieta' {_display(variety)}, "
            f"{_display(justified)}/{_display(cuts)} tagli giustificati",
        ]
        if unknown:
            evidence.append(
                f"controllo parziale: {len(unknown)} verifiche non eseguite "
                f"({', '.join(str(item.get('check')) for item in unknown[:3])})"
            )
        chosen = f"accettare {len(violations)} violazioni e dichiararle"
        confidence = "medium" if checked else "low"
        alternatives = [
            {
                "option": "correggere il piano",
                "why_not": "auto non riscrive la timeline dopo il cancello: servirebbe un nuovo giro",
            },
            {
                "option": "--free",
                "why_not": "spegnere il controllo lascerebbe la violazione nel master",
            },
        ]
    else:
        evidence = [
            f"0 violazioni su {_display(shots)} shot; varieta' {_display(variety)}",
            f"{_display(justified)}/{_display(cuts)} tagli giustificati",
        ]
        if unknown:
            evidence.append(
                f"controllo parziale: {len(unknown)} verifiche non eseguite "
                f"({', '.join(str(item.get('check')) for item in unknown[:3])})"
            )
        chosen = "accettare il piano: 0 violazioni"
        confidence = "high" if checked else "low"
        alternatives = [
            {
                "option": "--free",
                "why_not": "non serviva spegnere un controllo che non ha trovato violazioni",
            }
        ]
    return decisions.record(
        id="coherence",
        question="il montaggio rispetta le regole di coerenza?",
        chosen=chosen,
        confidence=confidence,
        evidence=evidence,
        alternatives=alternatives,
        falsified_if=falsified,
        by="auto",
    )


def _decision_effects(
    bridge: Any,
    estimate_section: Any,
    payload: Any,
) -> Dict[str, Any]:
    """La composizione degli effetti: cosa serviva, quanto costa, con quale tesi."""
    data = bridge if isinstance(bridge, Mapping) else {}
    info = estimate_section if isinstance(estimate_section, Mapping) else {}
    effects = info.get("effects") if isinstance(info.get("effects"), Mapping) else {}
    enabled = bool(data.get("enabled"))
    cuts = int(data.get("cuts") or 0)
    reframe = int(data.get("reframe") or 0)
    restore = int(data.get("restore") or 0)
    captions = int(data.get("captions") or 0)
    count = int(effects.get("count") or 0)
    cost = effects.get("cost")
    cost_seconds = effects.get("cost_seconds")
    factor = effects.get("factor")
    rationale = _rationale(payload)
    pacing = rationale.get("pacing") if isinstance(rationale.get("pacing"), Mapping) else {}
    color = (
        rationale.get("color_palette")
        if isinstance(rationale.get("color_palette"), Mapping)
        else {}
    )
    thesis = [
        str(rationale.get("genre_detected") or "").strip(),
        f"{int(float(pacing.get('bpm_target') or 0))} bpm"
        if pacing.get("bpm_target")
        else "",
        f"taglio {pacing.get('cut_policy')}".strip()
        if pacing.get("cut_policy")
        else "",
        f"palette {color.get('type')}".strip() if color.get("type") else "",
    ]
    thesis_text = ", ".join(item for item in thesis if item) or (
        "nessuna tesi dichiarata nel payload"
    )
    cost_line = (
        f"costo effetti {_display(cost)} -> {_display(cost_seconds, ' s')} "
        f"(fattore {_display(factor)})"
    )
    falsified = (
        f"il costo misurato nel render supera {_display(cost_seconds, ' s')} "
        "o un effetto dichiarato non si vede nel master"
    )
    if not enabled:
        return decisions.record(
            id="effects",
            question="quale composizione di effetti serve al montaggio?",
            chosen="nessun effetto del ponte (--no-bridge)",
            confidence="low",
            evidence=[
                f"0 tagli, 0 gain e 0 caption dal ponte; {count} effetti nel piano",
                cost_line,
                f"tesi: {thesis_text}",
            ],
            alternatives=[
                {
                    "option": "passare dal ponte",
                    "why_not": "--no-bridge lo esclude per questo giro",
                }
            ],
            falsified_if=falsified,
            by="auto",
        )
    needs = []
    if reframe:
        needs.append(f"reframe su {reframe} clip")
    if restore:
        needs.append(f"restore su {restore} clip")
    if captions:
        needs.append(f"caption su {captions} clip")
    chosen = (
        " + ".join(
            part
            for part in (
                f"reframe {reframe}" if reframe else "",
                f"restore {restore}" if restore else "",
                f"caption {captions}" if captions else "",
            )
            if part
        )
        or "nessun effetto extra"
    )
    evidence = [
        f"{reframe} reframe, {restore} restore, {captions} caption su {cuts} tagli",
        cost_line,
        f"serviva: {', '.join(needs) if needs else 'nessun effetto richiesto dal ponte'}",
        f"tesi: {thesis_text}",
    ]
    confidence = "high" if count > 0 and cost_seconds is not None else "medium"
    alternatives = (
        [
            {
                "option": "nessun effetto",
                "why_not": f"serviva {', '.join(needs)}",
            }
        ]
        if needs
        else [
            {
                "option": "applicare tutti gli effetti",
                "why_not": f"il costo {_display(cost_seconds, ' s')} non era giustificato",
            }
        ]
    )
    return decisions.record(
        id="effects",
        question="quale composizione di effetti serve al montaggio?",
        chosen=chosen,
        confidence=confidence,
        evidence=evidence,
        alternatives=alternatives,
        falsified_if=falsified,
        by="auto",
    )


def _creative_requirements(prompt: Any, payload: Any) -> Dict[str, Any]:
    """Cosa il prompt chiede, nella forma che ``creative.score`` legge.

    Solo fatti: la durata viene dallo ``scene`` gia' deciso, coro e titolo dalla
    presenza delle parole nel prompt. ``chorus_present``/``titles_present``
    restano ``None`` quando il giro non li ha misurati: valgono 0.5, mai un
    premio inventato.
    """
    text = " ".join(str(prompt or "").lower().split())
    scene = payload.get("scene") if isinstance(payload, Mapping) else None
    requested = scene.get("duration_sec") if isinstance(scene, Mapping) else None
    if not (
        isinstance(requested, (int, float))
        and not isinstance(requested, bool)
        and requested > 0
    ):
        requested = None
    return {
        "duration_sec": float(requested) if requested is not None else None,
        "chorus_required": any(
            word in text for word in ("coro", "chorus", "ritornello")
        ),
        "chorus_present": None,
        "titles_required": 1
        if any(word in text for word in ("titolo", "title", "titoli"))
        else 0,
        "titles_present": None,
        "prompt": str(prompt or ""),
    }


def _run_creative(
    args: Any,
    plan: Any,
    coherence_section: Any,
    estimate_section: Any,
    payload: Any,
    *,
    captions: bool = False,
) -> tuple[Dict[str, Any], Any]:
    """Due piani leciti, un punteggio, una scelta. Restituisce (sezione, piano).

    Con i sottotitoli il montaggio non si riordina (le caption sono ancorate
    alla timeline): si offre un solo candidato e lo si dichiara. Senza
    sottotitoli si offrono le varianti che il piano permette.
    """
    requirement = _creative_requirements(getattr(args, "prompt", ""), payload)
    variants = creative.candidates(plan, n=1 if captions else 2)
    scored = creative.score_variants(
        variants, coherence_section, estimate_section, requirement
    )
    pick = creative.choose(scored)
    chosen = next(
        (item for item in scored if str(item.get("id")) == pick["chosen"]),
        scored[0],
    )
    chosen_plan = chosen.get("plan") or plan
    errors = list(chosen_plan.validate()) if hasattr(chosen_plan, "validate") else []
    fallback = ""
    if errors:
        # Non deve succedere (le varianti si validano prima di entrare), ma una
        # scelta non applicabile non si applica: si tiene il piano di partenza.
        fallback = "scelta non applicata: " + "; ".join(str(item) for item in errors)
        chosen_plan = plan
    section = {
        "chosen": str(pick["chosen"]),
        "applied": str(chosen.get("id") or "base") if not errors else "base",
        "thesis": str(chosen.get("thesis") or ""),
        "why": str(pick["why"]),
        "scores": pick["scores"],
        "variants": [
            {
                "id": str(item.get("id") or ""),
                "thesis": str(item.get("thesis") or ""),
                "changes": [str(change) for change in item.get("changes") or ()],
                "total": float(item.get("total") or 0.0),
            }
            for item in scored
        ],
        "requirement": requirement,
        "captions": bool(captions),
    }
    if fallback:
        section["fallback"] = fallback
    return section, chosen_plan


def _decision_creative(section: Any) -> Dict[str, Any]:
    """La voce del registro: la variante scelta, i punteggi, l'alternativa."""
    data = section if isinstance(section, Mapping) else {}
    chosen = str(data.get("chosen") or "base")
    scores = data.get("scores") if isinstance(data.get("scores"), Mapping) else {}
    mine = scores.get(chosen) if isinstance(scores.get(chosen), Mapping) else {}
    total = mine.get("total")
    total_text = f"{float(total):.3f}" if isinstance(total, (int, float)) else "n/d"
    parts = mine.get("parts") if isinstance(mine.get("parts"), Mapping) else {}
    evidence = [f"punteggio {chosen}: {total_text}"]
    for key in ("coherence", "prompt", "cost", "novelty"):
        value = parts.get(key)
        if isinstance(value, (int, float)):
            evidence.append(f"{key} {float(value):.3f}")
    variants = [
        item for item in (data.get("variants") or ()) if isinstance(item, Mapping)
    ]
    others = [item for item in variants if str(item.get("id") or "") != chosen]
    for item in others:
        evidence.append(
            f"alternativa {item.get('id')}: {float(item.get('total') or 0.0):.3f}"
        )
    if others:
        alternatives = [
            {
                "option": str(item.get("id") or "alternativa"),
                "why_not": str(item.get("thesis") or "punteggio piu' basso"),
            }
            for item in others
        ]
        alternatives_note = ""
    else:
        alternatives = []
        alternatives_note = (
            "un solo candidato: i sottotitoli sono ancorati alla timeline, "
            "il montaggio non si riordina"
            if data.get("captions")
            else "il piano non ammetteva una seconda variante lecita"
        )
    return decisions.record(
        id="creative",
        question="quale variante del montaggio rendere?",
        chosen=f"{chosen}: {data.get('thesis') or 'piano invariato'}",
        evidence=evidence,
        alternatives=alternatives,
        alternatives_note=alternatives_note,
        falsified_if=(
            "la variante scelta non si valida o il suo punteggio rifatto sulle "
            "stesse metriche non e' il piu' alto"
        ),
        by="auto",
    )


def build_decisions(
    *,
    font_section: Any = None,
    key_section: Any = None,
    plan_choice: Any = None,
    estimate_section: Any = None,
    coherence_section: Any = None,
    bridge_summary: Any = None,
    plan: Any = None,
    payload: Any = None,
    requirement: str = "",
    energy: float = 0.0,
    creative_section: Any = None,
) -> List[Dict[str, Any]]:
    """Il registro delle decisioni di un giro, nell'ordine del report.

    Gli stessi punti che il giro attraversa (font, fotogrammi, piano,
    coerenza, varianti, effetti) entrano qui gia' misurati. Se una sezione
    manca, la voce corrispondente non si inventa: si registra che non c'e' una
    prova, con ``confidence: low``. La voce ``creative`` compare solo se il giro
    ha davvero confrontato due piani.
    """
    registry = decisions.Registry()
    builders = (
        (
            "font",
            f"quale famiglia per {requirement or 'il montaggio'}?",
            lambda: _decision_font(
                font_section, requirement=requirement, energy=energy, plan=plan
            ),
        ),
        (
            "keyframes",
            "quali fotogrammi mostrare prima del render?",
            lambda: _decision_keyframes(key_section, plan),
        ),
        (
            "plan",
            "piano completo o versione rapida?",
            lambda: _decision_plan(plan_choice, estimate_section),
        ),
        (
            "coherence",
            "il montaggio rispetta le regole di coerenza?",
            lambda: _decision_coherence(coherence_section),
        ),
        (
            "effects",
            "quale composizione di effetti serve al montaggio?",
            lambda: _decision_effects(bridge_summary, estimate_section, payload),
        ),
    )
    if creative_section is not None:
        # La sesta voce esiste solo quando il giro ha davvero confrontato due
        # piani: senza sezione non si inventa un confronto che non c'e' stato.
        builders = builders[:4] + (
            (
                "creative",
                "quale variante del montaggio rendere?",
                lambda: _decision_creative(creative_section),
            ),
        ) + builders[4:]
    for decision_id, question, build in builders:
        try:
            registry.add(build())
        except Exception as exc:  # noqa: BLE001 - un registro rotto non ferma il render
            registry.add(
                decisions.record(
                    id=decision_id,
                    question=question,
                    chosen="non registrata",
                    confidence="low",
                    evidence=[
                        f"costruzione fallita: {type(exc).__name__}: {exc}",
                    ],
                    alternatives_note=(
                        "nessuna alternativa registrata: la voce non si e' potuta costruire"
                    ),
                    falsified_if=(
                        "la decisione non e' verificabile finche' la voce non viene ricostruita"
                    ),
                    by="auto",
                )
            )
    return registry.as_list()


def font_requirement(prompt: Any, payload: Any, plan: Any) -> str:
    """La richiesta da dare a ``fontproposal``: prompt piu' tipo di contenuto.

    Il solo prompt non basta: un reel "120 bpm, duotone, taglio a battuta" chiede
    un carattere diverso da un montaggio lento. I token arrivano dal payload gia'
    deciso e dal piano gia' costruito, non da una seconda interpretazione.
    """
    parts: List[str] = []
    text = " ".join(str(prompt or "").split())
    if text:
        parts.append(text)
    rationale = _rationale(payload)
    pacing = rationale.get("pacing")
    pacing = pacing if isinstance(pacing, Mapping) else {}
    bpm = pacing.get("bpm_target")
    if isinstance(bpm, (int, float)) and not isinstance(bpm, bool) and bpm > 0:
        parts.append(f"{int(bpm)} bpm")
    cut = str(pacing.get("cut_policy") or "").strip()
    if cut and cut != "standard":
        parts.append(cut.replace("_", " "))
    color = rationale.get("color_palette")
    if isinstance(color, Mapping) and str(color.get("type") or "") == "duotone":
        parts.append("vintage")
    genre = str(rationale.get("genre_detected") or "").strip()
    if genre:
        parts.append(genre.replace("_", " "))
    if len(parts) == (1 if text else 0):
        # Nessun look dichiarato e nessun prompt: il piano resta un verticale.
        parts.append(f"verticale {getattr(plan, 'aspect_ratio', '') or '9:16'}")
    return ", ".join(part for part in parts if part)


def font_energy(payload: Any, plan: Any) -> float:
    """0..1 dai numeri del piano: BPM, politica di taglio, densita' dei tagli.

    ``fontchooser.rank`` usa l'energia per chiedere un peso piu' o meno nero;
    una costante sarebbe una bugia. Il BPM viene dal payload, la densita' dai
    clip che il piano ha davvero, e la politica di taglio dice se il ritmo e'
    sincronizzato o narrativo.
    """
    rationale = _rationale(payload)
    pacing = rationale.get("pacing")
    pacing = pacing if isinstance(pacing, Mapping) else {}
    bpm = pacing.get("bpm_target")
    if isinstance(bpm, (int, float)) and not isinstance(bpm, bool) and bpm > 0:
        bpm_term = _clamp01((float(bpm) - 70.0) / 80.0)
    else:
        bpm_term = 0.5
    cut = str(pacing.get("cut_policy") or "").strip()
    cut_term = 1.0 if cut in ("beat_synced", "onset_synced") else 0.35
    duration = 0.0
    try:
        duration = float(getattr(plan, "duration_sec", 0.0) or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    cuts = sum(len(getattr(track, "clips", ()) or ()) for track in getattr(plan, "video_tracks", ()))
    density_term = _clamp01((cuts / duration) / 0.8) if duration > 0 and cuts else 0.35
    return round(
        _clamp01(0.5 * bpm_term + 0.25 * cut_term + 0.25 * density_term), 3
    )


def _choice_family(workdir: Path) -> str:
    """La famiglia scritta dalla vista, o ``""``: un file rotto non e' una scelta."""
    path = Path(workdir) / FONT_CHOICE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, Mapping):
        return ""
    return str(data.get("family") or "").strip()


def _candidate_map(proposal: Any) -> Dict[str, Mapping[str, Any]]:
    raw = proposal.get("candidates") if isinstance(proposal, Mapping) else None
    items = raw if isinstance(raw, list) else []
    out: Dict[str, Mapping[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        family = str(item.get("family") or "").strip()
        if family:
            out.setdefault(family, item)
    return out


def font_gate(
    workdir: Any,
    *,
    requirement: str,
    energy: float,
    allow_network: bool = False,
    wait: float = FONT_WAIT_DEFAULT,
    gate: bool = True,
    view_url: str = "",
    propose: Optional[Callable[..., Dict[str, Any]]] = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    notify: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Proponi i font, poi aspetta una scelta umana senza mai bloccarsi.

    Ordine dei casi, e sono tutti finiti:

    1. una ``font_choice.json`` gia' presente vince subito, nessuna attesa;
    2. senza scelta, se il cancello e' aperto (vista accesa, non
       ``--no-font-gate``) si scrive dove scegliere e si controlla il file ogni
       secondo per al massimo ``wait`` secondi;
    3. alla scelta si usa quella famiglia (``selected_by="user"``); allo scadere
       si usa ``proposal["chosen"]`` e lo si dichiara
       (``selected_by="auto_after_timeout"``);
    4. cancello chiuso: si usa subito ``chosen`` (``selected_by="auto"``).

    Se ``propose`` solleva, la sezione torna con ``family=""`` e un ``error``
    in italiano: il chiamante prosegue col font del preset invece di fermare il
    montaggio. ``propose``, ``clock`` e ``sleep`` sono iniettabili per provare il
    cancello senza rete e senza aspettare davvero.
    """
    root = Path(workdir)
    say = notify if notify is not None else _say
    section: Dict[str, Any] = {
        "scope": "captions",
        "family": "",
        "licence": "",
        "source": "",
        "downloaded": False,
        "selected_by": "auto",
        "candidates": [],
        "pool": "local",
        "waited_sec": 0.0,
        "asked": False,
        "answered_by": "auto",
        "by": "auto",
        "expired": False,
        "choice": "",
    }
    runner = propose
    if runner is None:
        from . import fontproposal  # noqa: PLC0415 - the network path stays lazy

        runner = fontproposal.propose
    try:
        proposal = runner(
            root,
            requirement=str(requirement or ""),
            energy=float(energy),
            allow_network=bool(allow_network),
        )
    except Exception as exc:  # noqa: BLE001 - un font mancante non ferma un render
        section["error"] = (
            f"proposta font non disponibile, resta il preset: {exc}"
        )
        say(f"Font: {section['error']}")
        return section
    if not isinstance(proposal, Mapping):
        section["error"] = (
            "proposta font non valida (non e' un oggetto JSON), resta il preset"
        )
        say(f"Font: {section['error']}")
        return section

    by_family = _candidate_map(proposal)
    section["candidates"] = [
        {"family": family, "score": item.get("score")}
        for family, item in by_family.items()
    ]
    section["pool"] = str(proposal.get("pool") or "local")
    # Quale parola del brief ha deciso la proposta: senza, "perche' proprio
    # questo font?" non ha risposta nel report.
    section["intents"] = list(proposal.get("intents") or [])
    section["signals"] = list(proposal.get("signals") or [])

    started = clock()
    family = _choice_family(root)
    selected_by = "user" if family else "auto"
    asked = False
    expired = False
    waited = 0.0
    if not family and gate and float(wait) > 0:
        asked = True
        if str(view_url or "").strip():
            say(f"Scegli il font nella vista: {view_url}")
        else:
            say(f"Scegli il font nella cartella: {root} (file {FONT_CHOICE_NAME})")
        _record_gate(
            root,
            "font",
            open=True,
            asked=True,
            wait_sec=float(wait),
            choice=family or None,
        )
        deadline = started + float(wait)
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                break
            sleep(min(1.0, remaining))
            family = _choice_family(root)
            if family:
                break
        waited = max(0.0, clock() - started)

    if family:
        selected_by = "user"
    if not family:
        chosen = str(proposal.get("chosen") or "").strip()
        if chosen and chosen in by_family:
            family = chosen
            selected_by = (
                "auto_after_timeout" if (gate and float(wait) > 0) else "auto"
            )
            expired = bool(gate and float(wait) > 0)
    section["waited_sec"] = round(waited, 3)
    answered_by = _gate_by(selected_by)
    if asked and not family:
        # La domanda e' stata fatta e nessuno ha risposto: anche senza un
        # ripiego valido il cancello e' scaduto, e il report non lo tace.
        answered_by = "timeout"
        expired = True
    section.update(
        {
            "asked": asked,
            "answered_by": answered_by,
            "by": answered_by,
            "expired": expired,
            "choice": family,
        }
    )
    if gate:
        _record_gate(
            root,
            "font",
            open=False,
            asked=asked,
            wait_sec=float(wait),
            choice=family,
            answered_by=answered_by,
            expired=expired,
        )

    item = by_family.get(family)
    if item is not None:
        section.update(
            {
                "family": family,
                "licence": str(item.get("licence") or ""),
                "source": str(item.get("source") or ""),
                "downloaded": bool(item.get("downloaded")),
                "file": str(item.get("file") or ""),
                "selected_by": selected_by,
            }
        )
    elif family:
        # Una scelta che la proposta non contiene non e' un Fontname valido.
        section["family"] = family
        section["error"] = (
            f"{family}: famiglia non presente fra i candidati proposti, "
            "il render resta sul preset"
        )
        selected_by = "user"

    if section["family"]:
        if selected_by == "user":
            say(f"Font: {section['family']} — scelta nella vista.")
        elif selected_by == "auto_after_timeout":
            say(
                f"Font: {section['family']} — scelto in automatico dopo "
                f"{float(wait):g} s senza scelta."
            )
        else:
            say(f"Font: {section['family']} — scelto in automatico.")
    elif not section.get("error"):
        reason = str(proposal.get("reason_if_none") or "").strip()
        section["error"] = reason or "nessun font proposto: resta il preset"
    if section.get("error") and not section["family"]:
        say(f"Font: {section['error']}")
    return section


def _run_font_gate(
    args: Any,
    workdir: Path,
    plan: Any,
    payload: Any,
    *,
    view_url: str = "",
    view_active: bool = False,
) -> Dict[str, Any]:
    """Legge i flag, traduce il piano in richiesta/energia, apre o chiude il cancello."""
    allow_network = getattr(args, "font_network", None)
    if allow_network is None:
        allow_network = bool(settings.get("font.network", True)) and (
            font_network_available()
        )
    explicit = getattr(args, "font_wait", None)
    try:
        configured = float(settings.get("font.wait_sec", 0.0) or 0.0)
    except (TypeError, ValueError):
        configured = 0.0
    if explicit is not None:
        # Un'attesa chiesta a mano vince sempre, anche senza nessuno davanti.
        wait = float(explicit)
        asked_wait = True
    elif configured > 0:
        wait = configured
        asked_wait = True
    elif bool(view_active):
        # Il default (180 s) vale solo mentre qualcuno guarda.
        wait = FONT_WAIT_DEFAULT
        asked_wait = False
    else:
        wait = 0.0
        asked_wait = False
    gate = (bool(view_active) or asked_wait) and not bool(
        getattr(args, "no_font_gate", False)
    )
    return font_gate(
        workdir,
        requirement=font_requirement(getattr(args, "prompt", ""), payload, plan),
        energy=font_energy(payload, plan),
        allow_network=bool(allow_network),
        wait=float(wait),
        gate=gate,
        view_url=str(view_url or ""),
        notify=_say,
    )


def _run_plan_gate(
    args: Any,
    workdir: Path,
    plan: Any,
    *,
    view_active: bool = False,
) -> Dict[str, Any]:
    """Stima il lavoro e, sopra il tetto, chiede full o fast.

    Il budget arriva dal flag ``--budget``; senza flag dalle settings
    (``budget.max_seconds``, 600 s). L'attesa e' ``--plan-wait``
    (``budget.plan_wait_sec``, 180 s) e non blocca mai: allo scadere si prende
    la versione rapida e la si dichiara. Il preset rapido deve arrivare al
    render, non solo al referto: per questo si scrive su ``args.preset``.
    """
    budget = getattr(args, "budget", None)
    if budget is None:
        budget = float(
            settings.get("budget.max_seconds", estimate.BUDGET_DEFAULT)
            or estimate.BUDGET_DEFAULT
        )
    explicit = getattr(args, "plan_wait", None)
    try:
        configured = float(settings.get("budget.plan_wait_sec", 0.0) or 0.0)
    except (TypeError, ValueError):
        configured = 0.0
    if explicit is not None:
        wait = float(explicit)
        gate = True
    elif configured > 0:
        wait = configured
        gate = True
    elif bool(view_active):
        wait = float(estimate.PLAN_WAIT_DEFAULT)
        gate = True
    else:
        wait = 0.0
        gate = False
    result = estimate.plan_gate(
        workdir,
        plan,
        budget=float(budget),
        wait=float(wait),
        gate=gate,
        preset=str(getattr(args, "preset", None) or settings.get("render.preset", "medium")),
        crf=int(getattr(args, "crf", None) or settings.get("render.crf", 18)),
        notify=_say,
    )
    section = result["plan_choice"]
    answered_by = _gate_by(section.get("selected_by"))
    if bool(section.get("asked")) and section.get("chosen") == "fast" and not gate:
        answered_by = "auto"
    section.update(
        {
            "answered_by": answered_by,
            "by": answered_by,
            "expired": bool(
                section.get("asked") and section.get("selected_by") == "timeout"
            ),
            "choice": section.get("chosen"),
        }
    )
    if result["plan_choice"].get("chosen") == "fast":
        setattr(args, "preset", estimate.FAST_PRESET)
        setattr(args, "plan_choice_preset", estimate.FAST_PRESET)
    return result


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> Path:
    """Un JSON leggibile per intero anche mentre un altro processo lo guarda."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)
    return path


def _plan_video_clips(plan: Any) -> List[Any]:
    clips: List[Any] = []
    for track in getattr(plan, "video_tracks", ()) or ():
        for clip in getattr(track, "clips", ()) or ():
            clips.append(clip)
    return sorted(
        clips,
        key=lambda clip: (
            float(getattr(clip, "dst_in", 0.0) or 0.0),
            int(getattr(clip, "layer", 0) or 0),
            str(getattr(clip, "clip_id", "")),
        ),
    )


def _frame_choices(plan: Any) -> List[Dict[str, Any]]:
    """Tre istanti del montaggio: 25%, 50% e 75% della timeline vera.

    Ogni istante di destinazione si riporta nel tempo sorgente del clip che lo
    copre: e' il fotogramma che si vedra' li', non il primo del materiale.
    """
    clips = _plan_video_clips(plan)
    duration = float(getattr(plan, "duration_sec", 0.0) or 0.0)
    if not clips or duration <= 0:
        return []
    choices: List[Dict[str, Any]] = []
    for fraction in KEYFRAME_PICKS:
        at_dst = duration * fraction
        clip = next(
            (
                item
                for item in clips
                if float(getattr(item, "dst_in", 0.0) or 0.0)
                <= at_dst
                < float(getattr(item, "dst_out", 0.0) or 0.0)
            ),
            clips[-1],
        )
        src_in = float(getattr(clip, "src_in", 0.0) or 0.0)
        src_out = float(getattr(clip, "src_out", src_in) or src_in)
        offset = max(0.0, at_dst - float(getattr(clip, "dst_in", 0.0) or 0.0))
        choices.append(
            {
                "clip": str(getattr(clip, "clip_id", "")),
                "source_id": str(getattr(clip, "source_id", "")),
                "index": clips.index(clip),
                "at": round(min(src_out, src_in + offset), 6),
            }
        )
    return choices


def _extract_keyframe(source: str, at: float, png: Path) -> Tuple[str, str]:
    """Un PNG a un istante del sorgente: ``(sha256, "")`` o ``("", motivo)``.

    Il temporaneo finisce in ``.tmp.png``, non in ``.png.tmp``: ffmpeg sceglie
    il formato dall'**ultima** estensione, e ``.tmp`` non e' un formato (senza
    questo, ogni estrazione falliva in silenzio). ``-f image2`` lo dice anche a
    chi legge il comando. Il motivo dell'errore torna al chiamante, cosi' il
    report non restituisce un'impronta vuota senza dire perche'.
    """
    png = Path(png)
    png.parent.mkdir(parents=True, exist_ok=True)
    temporary = png.with_name(f"{png.stem}.tmp{png.suffix}")
    try:
        proc = subprocess.run(
            [
                ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{float(at):.3f}", "-i", str(source),
                "-frames:v", "1", "-update", "1", "-f", "image2", str(temporary),
            ],
            capture_output=True,
        )
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        return "", f"ffmpeg non eseguibile: {exc}"
    stderr = (proc.stderr or b"").decode("utf-8", "replace").strip()
    try:
        written = temporary.is_file() and temporary.stat().st_size > 0
    except OSError:
        written = False
    if proc.returncode != 0 or not written:
        temporary.unlink(missing_ok=True)
        lines = [line.strip() for line in stderr.splitlines() if line.strip()]
        why = lines[-1] if lines else f"ffmpeg uscito con codice {proc.returncode}"
        return "", f"estrazione fallita a {float(at):.3f}s: {why}"
    try:
        temporary.replace(png)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        return "", f"PNG non scritto: {exc}"
    return hashlib.sha256(png.read_bytes()).hexdigest(), ""


def _timeline_fingerprint(plan: Any) -> str:
    """L'impronta della timeline che si sta per rendere, senza i percorsi."""
    clips = [
        {
            "clip_id": str(getattr(clip, "clip_id", "")),
            "source_id": str(getattr(clip, "source_id", "")),
            "src_in": float(getattr(clip, "src_in", 0.0) or 0.0),
            "src_out": float(getattr(clip, "src_out", 0.0) or 0.0),
            "dst_in": float(getattr(clip, "dst_in", 0.0) or 0.0),
            "dst_out": float(getattr(clip, "dst_out", 0.0) or 0.0),
            "layer": int(getattr(clip, "layer", 0) or 0),
        }
        for clip in _plan_video_clips(plan)
    ]
    payload = {
        "fps": float(getattr(plan, "fps", 0.0) or 0.0),
        "duration_sec": float(getattr(plan, "duration_sec", 0.0) or 0.0),
        "resolution": [int(value) for value in (getattr(plan, "resolution", ()) or ())],
        "clips": clips,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def propose_keyframes(workdir: Any, plan: Any) -> Dict[str, Any]:
    """Il piano dei fotogrammi: tre PNG dalla timeline e un digest che li lega.

    Il digest copre la timeline e i fotogrammi scelti (clip, istante, sha256 del
    PNG): se il montaggio cambia dopo l'approvazione, il record non combacia piu'
    e il cancello e' chiuso. Senza clip o senza durata non c'e' piano: ``{}``.
    """
    root = Path(workdir)
    choices = _frame_choices(plan)
    if not choices:
        return {}
    sources = {
        str(key): str(getattr(ref, "path", "") or "")
        for key, ref in (getattr(plan, "sources", {}) or {}).items()
    }
    frames: List[Dict[str, Any]] = []
    for position, choice in enumerate(choices):
        png = root / KEYFRAMES_DIR / f"kf{position}.png"
        source = sources.get(choice["source_id"], "")
        # Un PNG di un giro precedente non deve passare per questo piano.
        png.unlink(missing_ok=True)
        if source:
            sha, error = _extract_keyframe(source, choice["at"], png)
        else:
            sha, error = "", (
                f"sorgente non nota per {choice['source_id'] or '(senza id)'}"
            )
        frames.append(
            {
                "clip": choice["clip"],
                "index": choice["index"],
                "at": choice["at"],
                "sha256": sha,
                "path": str(png),
                "error": error,
            }
        )
    # Il digest copre solo i fotogrammi che un'immagine ce l'hanno davvero:
    # un frame vuoto non e' una prova da approvare. La timeline resta nel
    # digest, quindi due piani diversi non possono avere lo stesso digest.
    valid = [
        {
            "clip": item["clip"],
            "index": item["index"],
            "at": item["at"],
            "sha256": item["sha256"],
        }
        for item in frames
        if str(item.get("sha256") or "").strip()
    ]
    timeline_sha = _timeline_fingerprint(plan)
    digest = hashlib.sha256(
        json.dumps(
            {
                "schema": KEYFRAMES_PLAN_SCHEMA,
                "timeline": timeline_sha,
                "frames": valid,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema": KEYFRAMES_PLAN_SCHEMA,
        "timeline_digest": timeline_sha,
        "digest": digest,
        "count": len(frames),
        "with_images": len(valid),
        "frames": frames,
    }


def keyframe_gate(
    workdir: Any,
    *,
    plan: Any = None,
    wait: float = KEY_GATE_WAIT_DEFAULT,
    gate: bool = True,
    view_url: str = "",
    reason_when_closed: str = "",
    propose: Optional[Callable[[Path, Any], Mapping[str, Any]]] = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    notify: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Ferma il render sui fotogrammi chiave finche' qualcuno li approva.

    Ordine dei casi, tutti finiti:

    1. senza piano (``plan`` nullo e nessun ``propose``) il cancello non si apre
       e non aspetta: ``required=False`` e la ragione in italiano;
    2. un ``keyframes.json`` gia' coerente col piano apre subito
       (``approved=True``, ``selected_by="user"``), nessuna attesa;
    3. col cancello aperto si scrive ``<workdir>/keyframes/waiting.json`` e si
       guarda ``<workdir>/keyframes.json`` ogni secondo, al massimo ``wait``
       secondi; all'approvazione il file d'attesa sparisce;
    4. allo scadere il render prosegue e il report dichiara
       ``selected_by="timeout"``: un'attesa finita non e' un blocco;
    5. cancello chiuso (``--no-key-gate``, nessuna vista) nessuna attesa:
       ``selected_by="auto"``.

    ``propose``, ``clock`` e ``sleep`` sono iniettabili per provare il cancello
    senza render e senza aspettare davvero.
    """
    root = Path(workdir)
    say = notify if notify is not None else _say
    section: Dict[str, Any] = {
        "required": False,
        "approved": False,
        "waiting": False,
        "selected_by": "none",
        "count": 0,
        "digest": "",
        "waited_sec": 0.0,
        "frames": [],
        "reason": "",
        "asked": False,
        "answered_by": "auto",
        "by": "auto",
        "expired": False,
        "choice": "auto",
    }
    runner = propose if propose is not None else propose_keyframes
    proposal: Any = {}
    if plan is not None or propose is not None:
        try:
            proposal = runner(root, plan)
        except Exception as exc:  # noqa: BLE001 - un piano mancante non ferma il render
            section["error"] = f"piano dei fotogrammi non disponibile: {exc}"
    if not isinstance(proposal, Mapping):
        if hasattr(proposal, "as_dict"):
            proposal = proposal.as_dict()
        else:
            proposal = {}
    digest = str(proposal.get("digest") or "")
    raw_frames = [
        dict(item) for item in (proposal.get("frames") or []) if isinstance(item, Mapping)
    ]
    # Un fotogramma senza sha256 non ha un'immagine: non si approva il nulla.
    frames = [item for item in raw_frames if str(item.get("sha256") or "").strip()]
    count = len(frames)
    if count <= 0:
        # Nessun cancello senza niente da mostrare: la policy di oggi e' "tre
        # fotogrammi, render senza approvazione", e un'attesa sul vuoto e' 81 s
        # buttati. Il report tiene gli errori di ogni tentativo.
        reason = (
            "nessun fotogramma estraibile"
            if raw_frames
            else str(section.get("error") or "nessun piano di fotogrammi")
        )
        waiting_path = root / KEYFRAMES_DIR / KEYFRAMES_WAITING_NAME
        waiting_path.unlink(missing_ok=True)
        # Il pending resta come testimone del tentativo (count 0, nessun digest):
        # la vista legge il perche' invece di dire "nessun piano", e il POST di
        # approvazione lo rifiuta perche' non c'e' un'immagine da approvare.
        pending_path = root / KEYFRAMES_DIR / KEYFRAMES_PENDING_NAME
        if raw_frames:
            _write_json_atomic(
                pending_path,
                {
                    "schema": KEYFRAMES_PLAN_SCHEMA,
                    "digest": "",
                    "timeline_digest": str(proposal.get("timeline_digest") or ""),
                    "count": 0,
                    "frames": raw_frames,
                },
            )
        else:
            pending_path.unlink(missing_ok=True)
        section.update(
            {
                "required": False,
                "approved": False,
                "waiting": False,
                "selected_by": "auto",
                "count": 0,
                "digest": "",
                "frames": raw_frames,
                "reason": reason,
                "asked": False,
                "answered_by": "auto",
                "by": "auto",
                "expired": False,
                "choice": "auto",
            }
        )
        _record_gate(
            root,
            "keyframes",
            open=False,
            asked=False,
            wait_sec=0.0,
            choice="auto",
            answered_by="auto",
        )
        say(f"Fotogrammi: {reason}, il render prosegue.")
        return section
    if not digest:
        section["reason"] = str(section.get("error") or "nessun piano di fotogrammi")
        say(f"Fotogrammi: {section['reason']}")
        return section

    _write_json_atomic(
        root / KEYFRAMES_DIR / KEYFRAMES_PENDING_NAME,
        {
            "schema": KEYFRAMES_PLAN_SCHEMA,
            "digest": digest,
            "timeline_digest": str(proposal.get("timeline_digest") or ""),
            "count": count,
            "frames": frames,
        },
    )
    section.update({"count": count, "digest": digest, "frames": frames})

    record_path = root / keygate.RECORD_NAME
    waiting_path = root / KEYFRAMES_DIR / KEYFRAMES_WAITING_NAME

    def approved_now() -> bool:
        payload = keygate.read(record_path)
        return bool(
            payload
            and payload.get("approved") is True
            and str(payload.get("digest") or "") == digest
        )

    started = clock()
    approved = approved_now()
    waited = 0.0
    if approved:
        # Il caso di un giro ripreso: l'approvazione c'e' gia' e vale per questo piano.
        waiting_path.unlink(missing_ok=True)
        section.update(
            {
                "required": True,
                "approved": True,
                "selected_by": "user",
                "reason": "",
                "asked": False,
                "answered_by": "user",
                "by": "user",
                "expired": False,
                "choice": "approved",
            }
        )
        _record_gate(
            root,
            "keyframes",
            open=False,
            asked=False,
            wait_sec=float(wait),
            choice="approved",
            answered_by="user",
        )
        say("Fotogrammi: approvati, si va al render.")
    elif gate and float(wait) > 0:
        started_wait = time.time()
        _write_json_atomic(
            waiting_path,
            {
                "schema": KEYFRAMES_WAIT_SCHEMA,
                "since_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "wait_sec": round(float(wait), 3),
                "until_epoch": round(started_wait + float(wait), 3),
                "frames": frames,
            },
        )
        section.update({"required": True, "waiting": True, "asked": True})
        _record_gate(
            root,
            "keyframes",
            open=True,
            asked=True,
            wait_sec=float(wait),
        )
        if str(view_url or "").strip():
            say(f"Guarda e approva i fotogrammi nella vista: {view_url}")
        else:
            say(
                "Guarda e approva i fotogrammi: "
                f"{root} (file {keygate.RECORD_NAME})"
            )
        deadline = started + float(wait)
        # Un orologio fermo non deve diventare un blocco: il numero di giri e'
        # limitato dal tempo chiesto, non solo dal suo avanzare.
        attempts = int(float(wait)) + 2
        while attempts > 0:
            attempts -= 1
            remaining = deadline - clock()
            if remaining <= 0:
                break
            sleep(min(1.0, remaining))
            if approved_now():
                approved = True
                break
        waited = max(0.0, clock() - started)
        waiting_path.unlink(missing_ok=True)
        section["waiting"] = False
        if approved:
            section.update(
                {
                    "approved": True,
                    "selected_by": "user",
                    "reason": "",
                    "answered_by": "user",
                    "by": "user",
                    "expired": False,
                    "choice": "approved",
                }
            )
            say("Fotogrammi: approvati nella vista, si va al render.")
        else:
            section.update(
                {
                    "approved": False,
                    "selected_by": "timeout",
                    "reason": f"nessuna approvazione in {float(wait):g} s",
                    "answered_by": "timeout",
                    "by": "timeout",
                    "expired": True,
                    "choice": "timeout",
                }
            )
            say(
                f"Fotogrammi: nessuna approvazione in {float(wait):g} s — "
                "il render prosegue e il report lo dichiara."
            )
        _record_gate(
            root,
            "keyframes",
            open=False,
            asked=True,
            wait_sec=float(wait),
            choice=section["choice"],
            answered_by=section["answered_by"],
            expired=section["expired"],
        )
    else:
        # Nessuno aspetta: un ``waiting.json`` di un giro precedente direbbe il falso.
        waiting_path.unlink(missing_ok=True)
        section.update(
            {
                "approved": False,
                "selected_by": "auto",
                "reason": reason_when_closed
                or "cancello dei fotogrammi chiuso: nessuna attesa",
                "asked": False,
                "answered_by": "auto",
                "by": "auto",
                "expired": False,
                "choice": "auto",
            }
        )
        _record_gate(
            root,
            "keyframes",
            open=False,
            asked=False,
            wait_sec=0.0,
            choice="auto",
            answered_by="auto",
        )
        say(f"Fotogrammi: {section['reason']}")
    section["waited_sec"] = round(waited, 3)
    return section


def _run_key_gate(
    args: Any,
    workdir: Path,
    plan: Any,
    *,
    view_url: str = "",
    view_active: bool = False,
) -> Dict[str, Any]:
    """Legge i flag, decide se il cancello e' aperto, e restituisce la sezione."""
    explicit_wait = getattr(args, "key_wait", None)
    try:
        configured = float(settings.get("key.wait_sec", 0.0) or 0.0)
    except (TypeError, ValueError):
        configured = 0.0
    if explicit_wait is not None:
        wait = float(explicit_wait)
        asked_wait = True
    elif configured > 0:
        wait = configured
        asked_wait = True
    elif bool(view_active):
        wait = KEY_GATE_WAIT_DEFAULT
        asked_wait = False
    else:
        wait = 0.0
        asked_wait = False
    explicit = getattr(args, "key_gate", None)
    if explicit is None:
        gate = bool(view_active) or asked_wait
    else:
        gate = bool(explicit)
    if not gate:
        closed_reason = (
            "cancello dei fotogrammi spento (--no-key-gate)"
            if explicit is False
            else "nessuna vista: il cancello dei fotogrammi non aspetta nessuno"
        )
    elif wait <= 0:
        closed_reason = "nessuna vista: il cancello dei fotogrammi non aspetta nessuno"
        gate = False
    else:
        closed_reason = ""
    return keyframe_gate(
        workdir,
        plan=plan,
        wait=float(wait),
        gate=gate,
        view_url=str(view_url or ""),
        reason_when_closed=closed_reason,
        notify=_say,
    )


def _typography_font(
    requested: Any, caption_font: Mapping[str, Any], workdir: Path
) -> Dict[str, Any]:
    """The family the ASS can carry, and where it comes from.

    ``fontlib.Store`` and the bundled ``assets/fonts`` are the only two places a
    family can come from; the job's own ``fonts/`` directory (the font gate
    choice) is the third. A family the brief names but nobody has is never
    invented: the bundled face is used and ``fallback`` says so with the note
    that lands in the report.
    """
    from . import fontlib, fontproposal
    from . import fonts as fonts_mod

    family = str(requested or "").strip()
    chosen = str(caption_font.get("family") or "").strip()
    if not family:
        if chosen:
            return {
                "requested": "",
                "family": chosen,
                "source": "job",
                "fallback": False,
                "note": "",
            }
        return {
            "requested": "",
            "family": subs.DEFAULT_FONT,
            "source": "bundled",
            "fallback": False,
            "note": "",
        }
    if chosen and chosen.lower() == family.lower():
        return {
            "requested": family,
            "family": chosen,
            "source": "job",
            "fallback": False,
            "note": "",
        }
    found = []
    try:
        found = fontlib.Store(fontproposal._store_root()).find(family)
    except Exception:  # noqa: BLE001 - un indice illeggibile non e' un font
        found = []
    if found:
        from . import typography

        try:
            applied = typography.apply(
                fontlib.Store(fontproposal._store_root()), found[0], workdir
            )
        except Exception as exc:  # noqa: BLE001 - licenza/file: ripiego dichiarato
            fallback = chosen or subs.DEFAULT_FONT
            return {
                "requested": family,
                "family": fallback,
                "source": "job" if chosen else "bundled",
                "fallback": True,
                "note": (
                    f"font {family!r} e' nello store ma non entra nel lavoro "
                    f"({type(exc).__name__}: {exc}): uso {fallback}, ripiego dichiarato"
                ),
            }
        return {
            "requested": family,
            "family": str(applied.get("family") or found[0].family),
            "source": "store",
            "fallback": False,
            "note": "",
            "fonts_dir": str(applied.get("fonts_dir") or ""),
            "licence": str(applied.get("licence") or ""),
        }
    bundled = {
        str(item.get("family") or "").lower(): item
        for item in fonts_mod.discover()
    }
    if family.lower() in bundled:
        return {
            "requested": family,
            "family": str(bundled[family.lower()]["family"]),
            "source": "bundled",
            "fallback": False,
            "note": "",
        }
    fallback = chosen or subs.DEFAULT_FONT
    source = "job" if chosen else "bundled"
    return {
        "requested": family,
        "family": fallback,
        "source": source,
        "fallback": True,
        "note": (
            f"font {family!r} non e' in fontlib.Store ne' nel bundle: "
            f"uso {fallback} ({source}), ripiego dichiarato"
        ),
    }


def _hook_fallback_text(bridge_report: Any) -> str:
    """Le prime parole del parlato, solo quando il brief chiede un hook senza frase."""
    captions = getattr(bridge_report, "captions", ()) if bridge_report is not None else ()
    for item in captions or ():
        text = " ".join(str(item.get("text") or "").split())
        if text:
            return " ".join(text.split()[:6])
    return ""


def _merged_brief(
    prompt: Any, recipe_brief: Optional[Mapping[str, Any]] = None
) -> tuple[Any, Dict[str, str]]:
    """Il brief del prompt, con la ricetta a riempire solo i vuoti.

    Il prompt vince quando dichiara la stessa cosa: un ``font`` scritto nel
    prompt non viene sovrascritto, un ``hook at 2.5s`` resta 2.5. La ricetta
    aggiunge cio' che il prompt tace. ``sources`` dice, campo per campo, chi ha
    deciso (``prompt`` o ``recipe``) e finisce nel report: niente default
    travestiti da scelta.
    """
    brief = textbrief.parse_brief_text(prompt)
    layer = dict(recipe_brief or {})
    if not layer:
        return brief, {}

    from dataclasses import replace  # noqa: PLC0415 - solo per questo merge

    sources: Dict[str, str] = {}
    updates: Dict[str, Any] = {}

    font = str(layer.get("font") or "").strip()
    if brief.font:
        sources["font"] = "prompt"
    elif font:
        updates["font"] = font
        sources["font"] = "recipe"

    if brief.size is not None:
        sources["size"] = "prompt"
    elif layer.get("size") is not None:
        updates["size"] = int(layer["size"])
        sources["size"] = "recipe"

    # ``textbrief`` riempie ``hook_at`` col default 0.10 quando il prompt tace:
    # un prompt che dichiara esattamente 0.10 e' indistinguibile dal silenzio,
    # ma i due valori coincidono, quindi la scelta non cambia il pixel.
    if brief.hook_at != textbrief.DEFAULT_HOOK_AT:
        sources["hook_at"] = "prompt"
    elif layer.get("hook_at") is not None:
        updates["hook_at"] = max(0.0, float(layer["hook_at"]))
        sources["hook_at"] = "recipe"

    if layer.get("cta") is True and not brief.asks_cta:
        updates["asks_cta"] = True
        sources["cta"] = "recipe"
    elif brief.asks_cta:
        sources["cta"] = "prompt"

    # Una ricetta che chiede un hook (``hook_at`` scritto) lo chiede davvero:
    # senza frase resta un "missing" dichiarato, come per il prompt.
    if "hook_at" in layer and not brief.asks_hook:
        updates["asks_hook"] = True

    merged = replace(brief, **updates) if updates else brief
    if merged.asks_text and not merged.hook_text and not merged.cta_text:
        merged = replace(merged, reason="")
    return merged, sources


def _overlay_font(font: Mapping[str, Any]) -> Dict[str, Any]:
    """Il font dei titoli dichiara il suo mestiere, distinto da quello dei sottotitoli.

    Due font nello stesso report non sono un errore: il cancello veste le
    caption, il brief veste hook e CTA. ``scope`` e una nota breve lo dicono a
    chi legge, e le chiavi esistenti (``family``, ``note``, ``fonts_dir``)
    restano dove sono.
    """
    out = dict(font)
    out["scope"] = "overlays"
    scope_note = "font dei titoli (overlays): puo' differire dal font dei sottotitoli"
    existing = str(out.get("note") or "")
    out["note"] = f"{existing}; {scope_note}" if existing else scope_note
    return out


def _run_typography(
    workdir: Path,
    plan: Any,
    prompt: Any,
    caption_font: Mapping[str, Any],
    captions_ass: Optional[str],
    bridge_report: Any = None,
    recipe_brief: Optional[Mapping[str, Any]] = None,
) -> tuple[Dict[str, Any], Optional[str]]:
    """Piano -> ASS -> render: il ponte che mancava.

    Legge solo cio' che il brief dichiara (``hook:``/``cta:``, il secondo, la
    famiglia, il corpo, lo stile). ``recipe_brief`` e' il frammento di una
    ricetta: riempie i campi che il prompt tace, non li sovrascrive. Scrive
    ``hook.ass`` e ``subs.ass`` con i writer di :mod:`vx.subs`;
    ``overlays.ass``/``text.ass`` e' il livello unico che ``render_edl`` brucia,
    perche' il suo filtro ``subtitles=`` ne accetta uno solo. Senza testo nel
    brief non scrive niente e lo dichiara: il percorso delle caption esistenti
    resta esattamente quello di prima.
    """
    brief, sources = _merged_brief(prompt, recipe_brief)
    if not brief.asks_text:
        return (
            {
                "applied": False,
                "reason": brief.reason,
                "brief": brief.to_dict(),
                "sources": sources,
            },
            captions_ass,
        )

    width, height = int(plan.resolution[0]), int(plan.resolution[1])
    duration = max(0.2, float(plan.duration_sec))
    aspect = str(getattr(plan, "aspect_ratio", "9:16") or "9:16")
    font = _overlay_font(_typography_font(brief.font, caption_font, workdir))
    # Il fontsdir della prova deve essere lo stesso che brucera' il render: la
    # card di prova con un ripiego misurerebbe un altro carattere.
    probe_fonts = str(font.get("fonts_dir") or caption_font.get("fonts_dir") or "")
    text_work = workdir / "legibility"
    files: Dict[str, str] = {}
    texts: List[Dict[str, Any]] = []
    legibility: Optional[Dict[str, Any]] = None
    safe_zone: Optional[Dict[str, Any]] = None
    cta_legibility: Optional[Dict[str, Any]] = None
    cta_safe_zone: Optional[Dict[str, Any]] = None
    contrast_region: Optional[List[float]] = None
    contrast_at: Optional[float] = None

    hook_text = brief.hook_text
    if not hook_text and brief.asks_hook:
        hook_text = _hook_fallback_text(bridge_report)
    hook_path: Optional[Path] = None
    if hook_text:
        hook_at = max(0.0, min(float(brief.hook_at), duration - 0.2))
        hook_dur = max(0.2, min(float(brief.hook_dur), duration - hook_at))
        hook_style = (
            brief.style
            if brief.style and brief.style.lower() not in ("karaoke", "clean")
            else "hook"
        )
        safe_zone = subs.hook_placement(aspect, width, height)
        hook_bg = _text_background(
            plan,
            hook_at,
            out_w=width,
            out_h=height,
            dst=text_work / "hook_bg.png",
        )
        legibility = subs.choose_hook_legibility(
            hook_bg,
            hook_text,
            out_w=width,
            out_h=height,
            style=hook_style,
            font=font["family"],
            size=brief.size,
            margin_v=safe_zone["margin_v"],
            alignment=safe_zone["alignment"],
            fonts_dir=probe_fonts or None,
            workdir=text_work / "hook",
        )
        chosen_style = legibility.get("params") or {}
        hook_path = Path(
            subs.write_hook_ass(
                workdir / "hook.ass",
                hook_text,
                out_w=width,
                out_h=height,
                start_s=hook_at,
                duration_s=hook_dur,
                style=hook_style,
                font=font["family"],
                size=brief.size,
                margin_v=safe_zone["margin_v"],
                alignment=safe_zone["alignment"],
                outline=chosen_style.get("outline"),
                shadow=chosen_style.get("shadow"),
                border_style=chosen_style.get("border_style"),
                outline_colour=chosen_style.get("outline_colour"),
                primary=chosen_style.get("primary"),
            )
        )
        files["hook"] = str(hook_path)
        contrast_region = list(legibility.get("region") or [])
        contrast_at = round(min(duration - 0.05, hook_at + 0.05), 3)
        texts.append(
            {
                "kind": "hook",
                "text": " ".join(hook_text.split()).upper(),
                "at": round(hook_at, 3),
                "end": round(hook_at + hook_dur, 3),
                "file": "hook.ass",
                "placement": safe_zone.get("placement"),
                "legibility": legibility.get("chosen"),
            }
        )

    subs_path: Optional[Path] = None
    if brief.cta_text:
        cta_dur = max(0.2, min(float(brief.cta_dur), duration - 0.1))
        earliest = 0.0
        if hook_text:
            earliest = min(duration - 0.2, hook_at + min(hook_dur, duration - hook_at) + 0.05)
        default_at = max(earliest, duration - cta_dur - 0.1)
        requested_at = brief.cta_at if brief.cta_at is not None else default_at
        cta_at = max(0.0, min(float(requested_at), duration - 0.2))
        cta_dur = max(0.2, min(cta_dur, duration - cta_at))
        cta_safe_zone = subs.cta_placement(aspect, width, height)
        cta_bg = _text_background(
            plan,
            cta_at,
            out_w=width,
            out_h=height,
            dst=text_work / "cta_bg.png",
        )
        cta_legibility = subs.choose_hook_legibility(
            cta_bg,
            brief.cta_text,
            out_w=width,
            out_h=height,
            style="cta",
            font=font["family"],
            size=brief.size,
            margin_v=cta_safe_zone["margin_v"],
            alignment=cta_safe_zone["alignment"],
            fonts_dir=probe_fonts or None,
            workdir=text_work / "cta",
        )
        cta_style = cta_legibility.get("params") or {}
        subs_path = Path(
            subs.write_hook_ass(
                workdir / "subs.ass",
                brief.cta_text,
                out_w=width,
                out_h=height,
                start_s=cta_at,
                duration_s=cta_dur,
                style="cta",
                font=font["family"],
                size=brief.size,
                margin_v=cta_safe_zone["margin_v"],
                alignment=cta_safe_zone["alignment"],
                outline=cta_style.get("outline"),
                shadow=cta_style.get("shadow"),
                border_style=cta_style.get("border_style"),
                outline_colour=cta_style.get("outline_colour"),
                primary=cta_style.get("primary"),
            )
        )
        files["subs"] = str(subs_path)
        if contrast_region is None:
            contrast_region = list(cta_legibility.get("region") or [])
            contrast_at = round(min(duration - 0.05, cta_at + 0.05), 3)
        texts.append(
            {
                "kind": "cta",
                "text": " ".join(brief.cta_text.split()).upper(),
                "at": round(cta_at, 3),
                "end": round(cta_at + cta_dur, 3),
                "file": "subs.ass",
                "placement": cta_safe_zone.get("placement"),
                "legibility": cta_legibility.get("chosen"),
            }
        )

    if not texts:
        return (
            {
                "applied": False,
                "reason": (
                    "il brief nomina hook/CTA ma non da' la frase e non c'e' "
                    "parlato da cui prenderla: nessun ASS scritto"
                ),
                "brief": brief.to_dict(),
                "font": font,
                "sources": sources,
            },
            captions_ass,
        )

    missing: List[str] = []
    if brief.asks_hook and not hook_text:
        missing.append("hook: nessuna frase nel brief ne' nel parlato")
    if brief.asks_cta and not brief.cta_text:
        missing.append("cta: nessuna frase nel brief")

    layers = [str(path) for path in (hook_path, subs_path) if path is not None]
    if len(layers) == 1:
        render_layer = layers[0]
    else:
        render_layer = subs.merge_ass(layers, workdir / "overlays.ass")
    if captions_ass:
        # Le caption esistenti restano il loro file: si uniscono al livello che
        # il render brucia, non si riscrivono e non spariscono.
        render_layer = subs.merge_ass(
            [captions_ass, render_layer], workdir / "text.ass"
        )
    files["render"] = render_layer
    return (
        {
            "applied": True,
            "reason": "",
            "brief": brief.to_dict(),
            "font": font,
            "files": files,
            "texts": texts,
            "missing": missing,
            "sources": sources,
            "legibility": legibility,
            "safe_zone": safe_zone or cta_safe_zone,
            "cta_legibility": cta_legibility,
            "cta_safe_zone": cta_safe_zone,
            "contrast_region": contrast_region,
            "contrast_at": contrast_at,
            "render": {
                "filter": "subtitles",
                "note": (
                    "render_edl espone un solo subtitles=: hook, CTA e caption "
                    "viaggiano in un livello unico (overlays.ass/text.ass); "
                    "hook_path separato richiede il parametro in render_edl"
                ),
            },
            "captions": {
                "path": str(captions_ass) if captions_ass else None,
                "merged": bool(captions_ass),
            },
        },
        render_layer,
    )


def _caption_font(workdir: Path) -> Dict[str, str]:
    """Famiglia scelta e cartella font per l'ASS dell'auto; vuota = preset.

    ``cli._job_font`` e' l'unico posto che legge ``font_choice.json`` /
    ``font_proposal.json.chosen`` e copia il TTF in ``<workdir>/fonts``: qui si
    usa lo stesso risultato del percorso ``run``, cosi' ``Fontname`` e
    ``fontsdir`` non possono divergere. Quando la scelta c'e' ma il file non
    arriva nella cartella, la famiglia torna vuota e ``fallback_reason`` dice
    perche': il report non deve far credere che il pixel sia cambiato.
    """
    from . import cli as cli_mod  # noqa: PLC0415 -- cli importa auto a runtime

    # La scelta esplicita vince anche quando non e' applicabile: se il file non
    # c'e', si dichiara il ripiego invece di bruciare in silenzio il `chosen`
    # della proposta con un `Fontname` che il report chiama diverso.
    choice = cli_mod._maybe_json(Path(workdir) / FONT_CHOICE_NAME)
    explicit = (
        str(choice.get("family") or "").strip()
        if isinstance(choice, Mapping)
        else ""
    )
    try:
        picked = cli_mod._job_font(Path(workdir))
    except Exception as exc:  # noqa: BLE001 - un font non ferma il montaggio
        return {
            "family": "",
            "fonts_dir": "",
            "fallback_reason": f"font scelto non applicabile: {exc}",
        }
    family = str(picked.get("family") or "")
    fonts_dir = str(picked.get("fonts_dir") or "")
    note = str(picked.get("note") or "")
    if explicit and family != explicit:
        return {
            "family": "",
            "fonts_dir": "",
            "fallback_reason": (
                f"{explicit}: nessun file in {Path(workdir) / 'fonts'} "
                "(o famiglia non fra i candidati), il master resta sul preset"
            ),
        }
    if not family:
        return {"family": "", "fonts_dir": "", "fallback_reason": note}
    if not Path(fonts_dir).is_dir():
        return {
            "family": "",
            "fonts_dir": "",
            "fallback_reason": (
                f"{family}: cartella {fonts_dir} mancante, il master resta sul preset"
            ),
        }
    return {"family": family, "fonts_dir": fonts_dir, "fallback_reason": ""}


def _resource_report(clock: _Clock) -> Dict[str, Any]:
    """La sezione ``resources``: quanta macchina, con quale diritto, a che priorita'.

    ``ram_free_mb`` e' il minimo visto durante il lavoro, non quello di partenza:
    e' il numero che dice se la macchina stava per swappare. ``reasons`` ripete
    perche' i worker sono quelli e non altri - un numero senza motivo non e'
    verificabile.
    """
    budget = dict(clock.facts.get("resources") or {})
    samples = [
        float(value)
        for value in (clock.facts.get("ram_free_mb"), resources.ram_free_mb())
        if isinstance(value, (int, float))
    ]
    whisper = clock.facts.get("whisper")
    return {
        "cpu_logical": int(budget.get("cpu_logical") or resources.cpu_logical()),
        "workers": int(budget.get("workers") or 1),
        "mode": str(budget.get("mode") or "in fila"),
        "ram_free_mb": round(min(samples), 1) if samples else None,
        "gpu": clock.facts.get("gpu") or resources.gpu(),
        "priority": str(clock.facts.get("priority") or "normal"),
        "concurrent_jobs": int(
            clock.facts.get("concurrent_jobs_render")
            or budget.get("concurrent_jobs")
            or 0
        ),
        "ffmpeg_threads": {
            "analysis": int(
                clock.facts.get("ffmpeg_threads") or WORKER_FFMPEG_THREADS
            ),
            "render": "default (pin thread cambierebbe il bitstream)",
        },
        "whisper": whisper
        or {"used": False, "device": None, "reason": "nessuna trascrizione richiesta"},
        "limits": {
            "max_workers": resources.MAX_WORKERS,
            "cpu_headroom": resources.CPU_HEADROOM,
            "ram_per_worker_mb": resources.RAM_PER_WORKER_MB,
        },
        "reasons": list(budget.get("reasons") or []),
    }


# --------------------------------------------------------------------------- #
# recipe: il file dichiara, il montaggio esegue
# --------------------------------------------------------------------------- #


def _recipe_explicit_params(loaded: Mapping[str, Any]) -> set:
    """Quali ``params`` la catena ha davvero scritto, non i default ereditati.

    ``recipe.load`` riempie ogni parametro col default dichiarato in
    ``recipe.PARAMS``, quindi la sola mappa risolta non distingue "la ricetta
    chiede 1.0" da "nessuno l'ha chiesto". Le sorgenti invece lo dicono: si
    rileggono (sono i file gia' validati da ``load_safe``) e si tiene l'unione
    delle chiavi scritte.
    """
    explicit: set = set()
    for source in loaded.get("sources") or ():
        try:
            data = json.loads(Path(str(source)).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        params = data.get("params") if isinstance(data, dict) else None
        if isinstance(params, dict):
            explicit.update(str(key) for key in params)
    return explicit


def _merge_layers(base: Mapping[str, Any], extra: Mapping[str, Any]) -> Dict[str, Any]:
    """Deep merge, ``extra`` on top: the same rule ``vx.settings`` applies."""
    out: Dict[str, Any] = dict(base)
    for key, value in extra.items():
        current = out.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            out[key] = _merge_layers(current, value)
        else:
            out[key] = value
    return out


def _recipe_settings_file(recipe_obj: Mapping[str, Any], workdir: Path, args: Any) -> tuple[Path, str]:
    """Scrive il layer della ricetta; se c'e' ``--settings`` lo tiene sotto.

    ``write_layer`` scrive il layer puro (provenienza, ``recipe_settings.json``).
    Il file che il cascade legge e' quello combinato: ``--settings`` e' una
    scelta per-job piu' stretta di una ricetta, quindi vince lui; ``--override``
    resta il gradino piu' alto perche' ``settings.build`` lo riapplica dopo.
    """
    layer_path = recipe.write_layer(recipe_obj, Path(workdir) / RECIPE_SETTINGS_NAME)
    layer = json.loads(Path(layer_path).read_text(encoding="utf-8"))
    source = getattr(args, "settings", None)
    if not source:
        return layer_path, ""
    try:
        raw = json.loads(Path(str(source)).expanduser().read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return layer_path, (
            f"--settings {source} non leggibile ({type(exc).__name__}: {exc}): "
            "il cascade usa il solo layer della ricetta"
        )
    if not isinstance(raw, dict):
        return layer_path, (
            f"--settings {source} non e' un oggetto JSON: "
            "il cascade usa il solo layer della ricetta"
        )
    user = {k: v for k, v in raw.items() if k != "settings"} if "thresholds" in raw else raw
    combined = _merge_layers(layer, user)
    path = Path(workdir) / RECIPE_SETTINGS_COMBINED_NAME
    path.write_text(
        json.dumps(combined, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path, ""


def _apply_recipe_settings(
    recipe_obj: Mapping[str, Any], workdir: Path, args: Any
) -> Dict[str, Any]:
    """I settings della ricetta entrano nel cascade come farebbe ``--settings``.

    Il posto giusto sarebbe ``cli._apply_settings`` (gira prima di ogni comando,
    quando la ricetta e' gia' nota) ma quel file non e' modificabile da qui:
    quindi il layer si riapplica all'ingresso di ``_run_locked``, prima che i
    moduli leggano i valori. Cio' che manca e' dichiarato nel report: i valori
    di ``--settings``/``--override`` sono ricostruiti da ``args``, e un loro
    errore non ferma il lavoro (il cascade resta quello di prima).
    """
    from . import features

    section: Dict[str, Any] = {
        "applied": False,
        "path": None,
        "fingerprint": None,
        "precedence": "ricetta sopra il profilo di sito/config, sotto --settings e --override",
        "problems": [],
    }
    try:
        path, note = _recipe_settings_file(recipe_obj, workdir, args)
        if note:
            section["note"] = note
        effective = settings.build(
            settings_file=str(path),
            overrides=list(getattr(args, "override", None) or []),
        )
        features.validate_table(effective)
        features.apply_disables(effective, list(getattr(args, "no_feature", None) or []))
        applied = settings.apply(effective)
        section.update(
            {
                "applied": True,
                "path": str(path),
                "fingerprint": str(
                    applied.get("_fingerprint") or settings.fingerprint(effective)
                ),
                "effect_budget": settings.get("render.effect_budget"),
            }
        )
    except Exception as exc:  # noqa: BLE001 - un layer rotto non ferma il montaggio
        degrade.note("recipe.settings", exc)
        section["problems"].append(
            f"settings non applicati: {type(exc).__name__}: {exc}"
        )
    return section


def load_recipe(args: Any, workdir: Path) -> Dict[str, Any]:
    """La sezione ``report["recipe"]``: caricamento, settings e dichiarazioni.

    Un nome inesistente o un file rotto non fermano il lavoro: il report porta
    ``applied: False`` e i problemi, il degrado e' annotato, e la pipeline segue
    coi suoi default. Una ricetta valida porta le sorgenti risolte, i parametri
    (con la lista di quelli *scritti*, non ereditati) e i settings applicati.
    """
    requested = getattr(args, "recipe", None)
    section: Dict[str, Any] = {
        "requested": requested,
        "applied": False,
        "sources": [],
        "params": {},
        "settings": {},
        "brief": {},
        "explicit": [],
        "applied_params": [],
        "ignored_params": [],
    }
    if not requested:
        return section
    loaded, problems = recipe.load_safe(str(requested))
    if loaded is None:
        why = "; ".join(problems) or f"ricetta {requested!r} non caricata"
        degrade.note("recipe", why)
        section["problems"] = list(problems) or [why]
        return section

    explicit = _recipe_explicit_params(loaded)
    section.update(
        {
            "applied": True,
            "name": loaded.get("name"),
            "sources": [str(item) for item in loaded.get("sources") or ()],
            "params": dict(loaded.get("params") or {}),
            "settings": dict(loaded.get("settings") or {}),
            "brief": dict(loaded.get("brief") or {}),
            "explicit": sorted(explicit),
        }
    )
    section["settings_apply"] = _apply_recipe_settings(loaded, workdir, args)
    if section["settings_apply"].get("problems"):
        section["problems"] = list(section["settings_apply"]["problems"])
    return section


def _recipe_explicit(section: Mapping[str, Any]) -> set:
    return set(section.get("explicit") or ())


def _recipe_param(section: Mapping[str, Any], name: str, default: Any = None) -> Any:
    """Valore di un parametro **solo** se la ricetta l'ha scritto e e' caricata."""
    if not section.get("applied") or name not in _recipe_explicit(section):
        return default
    return (section.get("params") or {}).get(name, default)


def _declare_param(section: Dict[str, Any], name: str, where: str, detail: str = "") -> None:
    row: Dict[str, Any] = {"name": name, "where": where}
    if detail:
        row["detail"] = detail
    section.setdefault("applied_params", []).append(row)


def _ignore_param(section: Dict[str, Any], name: str, reason: str) -> None:
    section.setdefault("ignored_params", []).append({"name": name, "reason": reason})


def _recipe_beat_args(section: Mapping[str, Any]) -> tuple[Optional[str], Optional[float]]:
    """``(beat_grid, bpm)`` per ``decide.parse_and_decide``.

    La ricetta dichiara solo la griglia (``on``/``off``); il BPM viene dal
    prompt o dal pacing del payload, che ``decide`` sa gia' leggere. Passare
    ``bpm`` qui sarebbe inventare un numero che la ricetta non ha.
    """
    grid = _recipe_param(section, "beat_grid")
    if grid not in ("on", "off"):
        return None, None
    return str(grid), None


def _decide_payload(
    args: Any,
    assets: Sequence[Any],
    section: Mapping[str, Any],
    montage_mode: bool,
    music: Optional[Mapping[str, Any]] = None,
) -> tuple[Dict[str, Any], str]:
    """``parse_and_decide`` coi parametri della ricetta; degrada se serve un BPM.

    ``beat_grid: on`` senza BPM e' un ``VxError`` onesto di ``decide`` ("passa
    --bpm o scrivilo nel prompt"), ma qui non c'e' nessun flag da passare: il
    lavoro riparte senza griglia e il report dichiara il parametro ignorato,
    invece di morire su una ricetta che chiedeva piu' di quanto il prompt dia.

    Il BPM puo' venire da ``--bpm`` (dichiarato) o dalla traccia ``--music``
    (misurato). In entrambi i casi lo si passa come numero esplicito, cosi'
    ``decide`` accende la griglia; la provenienza vera si riscrive nel blocco
    dopo, perche' "flag" non distingue un numero scritto da uno misurato.
    """
    grid, bpm = _recipe_beat_args(section)
    source = ""
    music = music or {}
    if music.get("bpm") is not None:
        bpm = float(music["bpm"])
        source = str(music.get("bpm_source") or "")
    common = {
        "asset_pool": assets,
        "force_mode": "autonomous" if getattr(args, "autonomous", False) else None,
        "montage": bool(montage_mode),
    }
    try:
        payload = decide.parse_and_decide(args.prompt, beat_grid=grid, bpm=bpm, **common)
    except contract.VxError as exc:
        if grid == "on" and "BPM" in str(exc):
            degrade.note("recipe.beat_grid", exc)
            payload = decide.parse_and_decide(args.prompt, beat_grid=None, bpm=None, **common)
            payload["beat_grid"] = dict(payload.get("beat_grid") or {})
            payload["beat_grid"].update(
                {
                    "enabled": False,
                    "bpm": None,
                    "source": source or "unavailable",
                }
            )
            return payload, str(exc)
        raise
    block = payload.get("beat_grid")
    if isinstance(block, dict):
        if music.get("requested") and music.get("bpm_source") == "unavailable" and str(
            block.get("source") or ""
        ) not in ("prompt",):
            # La misura manca e nessuno ha dichiarato un tempo: il default che
            # l'autonomia riempie nel brief non e' una misura e non deve
            # accendere la griglia. Si spegne qui e lo si dichiara.
            block.update(
                {
                    "enabled": False,
                    "bpm": None,
                    "source": "unavailable",
                    "note": (
                        "BPM non misurabile dalla traccia: griglia spenta, "
                        "il montaggio segue il contenuto"
                    ),
                }
            )
            return payload, str(block["note"])
        if source and str(block.get("source") or "") not in ("prompt",):
            block["source"] = source
            block["bpm"] = round(float(bpm), 3)
            block["source_note"] = (
                "BPM misurato dalla traccia --music"
                if source == "music"
                else "BPM dichiarato con --bpm"
            )
    return payload, ""


def _beat_provenance(
    payload: Mapping[str, Any], music: Mapping[str, Any]
) -> Dict[str, Any]:
    """``bpm``, ``bpm_source`` e ``enabled`` come li ha davvero usati il piano.

    Il blocco del payload nasce da ``decide`` e parla di "flag"; qui si
    riporta la provenienza vera (traccia misurata, numero dichiarato, prompt) e,
    quando ``--music`` non e' misurabile e nessuno ha dichiarato un tempo, si
    dichiara ``unavailable`` con ``bpm: null`` e la griglia spenta: un'assenza
    scritta, non un silenzio.
    """
    block = payload.get("beat_grid")
    block = block if isinstance(block, Mapping) else {}
    enabled = bool(block.get("enabled"))
    bpm = block.get("bpm")
    source = str(block.get("source") or "")
    if music.get("requested"):
        requested = str(music.get("bpm_source") or "")
        if requested in ("music", "declared"):
            bpm = music.get("bpm")
            source = requested
            enabled = bool(bpm) and enabled
        elif enabled and source in ("prompt", "flag"):
            source = "declared" if source == "flag" else "prompt"
        else:
            bpm = None
            source = "unavailable"
            enabled = False
    try:
        value = float(bpm) if bpm is not None else None
    except (TypeError, ValueError):
        value = None
    if value is not None and value > 0:
        value = round(value, 2)
    else:
        value = None
        source = source or ("unavailable" if music.get("requested") else "none")
    return {"bpm": value, "bpm_source": source, "enabled": enabled}


def _declare_beat_grid(
    section: Dict[str, Any], payload: Mapping[str, Any], degraded_reason: str
) -> None:
    if "beat_grid" not in _recipe_explicit(section) or not section.get("applied"):
        return
    if degraded_reason:
        _ignore_param(section, "beat_grid", degraded_reason)
        return
    block = payload.get("beat_grid") if isinstance(payload, Mapping) else None
    block = block if isinstance(block, Mapping) else {}
    if block.get("mode") == "on" and block.get("enabled", True):
        _declare_param(
            section,
            "beat_grid",
            "decide.parse_and_decide",
            f"bpm={block.get('bpm')} source={block.get('source')}",
        )
        return
    _ignore_param(
        section,
        "beat_grid",
        "la griglia non e' entrata nel payload: "
        f"mode={block.get('mode')!r}, bpm={block.get('bpm')!r}, "
        f"source={block.get('source')!r}",
    )


def _recipe_loudness(
    section: Dict[str, Any], old_lufs: float, true_peak: float
) -> Dict[str, Any]:
    """Target LUFS effettivo: il profilo resta il pavimento, la ricetta lo sposta.

    Il true peak non si tocca: e' il ceiling che il limiter e il QC usano gia',
    e un target piu' forte non e' il permesso di sfondarlo.
    """
    row: Dict[str, Any] = {
        "old_lufs": float(old_lufs),
        "new_lufs": float(old_lufs),
        "true_peak_db": float(true_peak),
        "source": "profilo/default",
    }
    value = _recipe_param(section, "loudness")
    if value is None:
        return row
    new = float(value)
    row.update({"new_lufs": new, "source": "recipe"})
    _declare_param(
        section,
        "loudness",
        "render.render_edl + qc.audit_master",
        f"{float(old_lufs):g} -> {new:g} LUFS, true peak {float(true_peak):g} dBTP invariato",
    )
    return row


def _recipe_text_brief(section: Mapping[str, Any]) -> Dict[str, Any]:
    """Il frammento di brief tipografico che la ricetta dichiara davvero.

    Solo i parametri scritti: ``hook_at`` (default ``0.0``) non deve chiedere un
    hook che nessuno ha voluto.
    """
    layer: Dict[str, Any] = {}
    font = _recipe_param(section, "font")
    if isinstance(font, str) and font.strip():
        layer["font"] = font.strip()
    size = _recipe_param(section, "font_size")
    if isinstance(size, (int, float)) and not isinstance(size, bool):
        layer["size"] = int(size)
    if "hook_at" in _recipe_explicit(section) and section.get("applied"):
        try:
            layer["hook_at"] = float((section.get("params") or {}).get("hook_at", 0.0))
        except (TypeError, ValueError):
            pass
    if "cta" in _recipe_explicit(section) and section.get("applied"):
        layer["cta"] = bool((section.get("params") or {}).get("cta", False))
    return layer


def _declare_text_params(
    section: Dict[str, Any], typography: Mapping[str, Any], plan: Any
) -> None:
    """Dichiara i parametri di testo: applicati dove sono arrivati al pixel."""
    if not section.get("applied"):
        return
    explicit = _recipe_explicit(section)
    sources = typography.get("sources") or {}
    texts = {str(item.get("kind")) for item in typography.get("texts") or ()}
    missing = [str(item) for item in typography.get("missing") or ()]
    reason = str(typography.get("reason") or "tipografia non applicata")
    written = bool(typography.get("applied"))

    for name in ("font", "font_size"):
        if name not in explicit:
            continue
        key = "font" if name == "font" else "size"
        if not written:
            _ignore_param(section, name, reason)
        elif sources.get(key) == "prompt":
            _ignore_param(section, name, "il prompt dichiara gia' questo valore: vince lui")
        else:
            _declare_param(section, name, "typography (ASS)", f"fonte: {sources.get(key) or 'ricetta'}")

    if "hook_at" in explicit:
        detail = next((item for item in missing if item.startswith("hook")), "")
        if "hook" in texts:
            at = next(
                (item.get("at") for item in typography.get("texts") or () if item.get("kind") == "hook"),
                None,
            )
            _declare_param(section, "hook_at", "typography hook.ass", f"hook a {at}s")
        elif sources.get("hook_at") == "prompt":
            _ignore_param(section, "hook_at", "il prompt dichiara il suo secondo: vince lui")
        else:
            _ignore_param(section, "hook_at", detail or reason)

    if "cta" in explicit:
        wanted = bool((section.get("params") or {}).get("cta", False))
        detail = next((item for item in missing if item.startswith("cta")), "")
        if not wanted:
            _ignore_param(section, "cta", "cta: false nella ricetta: nessuna CTA scritta")
        elif "cta" in texts:
            at = next(
                (item.get("at") for item in typography.get("texts") or () if item.get("kind") == "cta"),
                None,
            )
            _declare_param(section, "cta", "typography subs.ass", f"CTA a {at}s")
        else:
            _ignore_param(
                section,
                "cta",
                detail
                or "nessuna frase CTA nel brief: la ricetta non inventa una frase",
            )

    if "shot_max" in explicit:
        _ignore_param(section, "shot_max", _shot_max_reason(section, plan))


def _shot_max_reason(section: Mapping[str, Any], plan: Any) -> str:
    """Perche' ``shot_max`` non si applica, con i numeri veri del piano."""
    try:
        limit = float((section.get("params") or {}).get("shot_max"))
    except (TypeError, ValueError):
        limit = 0.0
    durations: List[float] = []
    for track in getattr(plan, "video_tracks", ()) or ():
        for clip in getattr(track, "clips", ()) or ():
            try:
                durations.append(float(clip.dst_out) - float(clip.dst_in))
            except (TypeError, ValueError):
                continue
    if not durations:
        measured = "nessun segmento video nel piano"
    else:
        longest = max(durations)
        mean = sum(durations) / len(durations)
        measured = f"misurato: shot piu' lungo {longest:.2f}s, medio {mean:.2f}s"
        if limit and longest <= limit:
            measured += f" (sotto il tetto {limit:g}s richiesto)"
    return (
        "il piano EDL non espone un tetto di durata per segmento: i tagli "
        "nascono dalla griglia di battuta e spezzarli dopo il ponte audio "
        f"disallineerebbe le caption; {measured}"
    )


def policy_checks(
    report: Mapping[str, Any], policy: Optional[Mapping[str, Any]]
) -> Dict[str, Any]:
    """Le soglie della policy dei giudizi, quando esiste, contro il piano vero.

    La policy nasce dai voti (``vx.learning``) e ha quattro numeri. Finche'
    ``version`` e' 0 non esiste e qui non cambia niente: nessuna regressione su
    chi non ha mai votato. Da v1 in poi e' un contratto come gli altri — se il
    montaggio cade fuori dalle sue soglie, la consegna si ferma e il motivo ha
    misura e soglia. ``variety_min`` non produce un check: entra in
    ``report["min_variety"]``, che e' il numero che ``verify`` legge.
    """
    data = policy if isinstance(policy, Mapping) else {}
    thresholds = data.get("thresholds") if isinstance(data.get("thresholds"), Mapping) else {}
    version = int(data.get("version") or 0)
    measured = report.get("measured") if isinstance(report.get("measured"), Mapping) else {}
    effects_block = report.get("effects") if isinstance(report.get("effects"), Mapping) else {}
    shots = _as_seconds(measured.get("clips"))
    duration = _as_seconds(measured.get("duration_sec"))
    effects = _as_seconds(effects_block.get("count"))
    cut_rate = shots / duration if duration > 0 else None
    density = effects / shots if shots > 0 else None

    block: Dict[str, Any] = {
        "version": version,
        "provisional": bool(data.get("provisional")),
        "thresholds": dict(thresholds),
        "measured": {
            "cut_rate": None if cut_rate is None else round(cut_rate, 4),
            "effects_density": None if density is None else round(density, 4),
        },
        "checks": [],
        "note": "" if version else "nessuna policy scritta: le soglie non si applicano",
    }
    if not version:
        return block

    def add(identifier: str, ok: bool, measured_value: Any, expected: Any, why: str) -> None:
        block["checks"].append(
            {
                "id": identifier,
                "passed": bool(ok),
                "measured": {"value": measured_value},
                "expected": expected,
                "why": why,
            }
        )

    low = thresholds.get("cut_rate_min")
    high = thresholds.get("cut_rate_max")
    if cut_rate is not None and isinstance(low, (int, float)):
        add(
            "policy_cut_rate_min",
            cut_rate >= float(low),
            round(cut_rate, 4),
            {"cut_rate_min": low},
            f"tagli {cut_rate:.2f}/s >= {float(low):.2f}" if cut_rate >= float(low)
            else f"tagli {cut_rate:.2f}/s sotto il minimo {float(low):.2f}: montaggio troppo lento",
        )
    if cut_rate is not None and isinstance(high, (int, float)):
        add(
            "policy_cut_rate_max",
            cut_rate <= float(high),
            round(cut_rate, 4),
            {"cut_rate_max": high},
            f"tagli {cut_rate:.2f}/s <= {float(high):.2f}" if cut_rate <= float(high)
            else f"tagli {cut_rate:.2f}/s sopra il massimo {float(high):.2f}: montaggio frenetico",
        )
    cap = thresholds.get("effects_density_max")
    if density is not None and isinstance(cap, (int, float)):
        add(
            "policy_effects_density_max",
            density <= float(cap),
            round(density, 4),
            {"effects_density_max": cap},
            f"effetti {density:.2f}/inquadratura <= {float(cap):.2f}" if density <= float(cap)
            else f"effetti {density:.2f}/inquadratura sopra il tetto {float(cap):.2f}",
        )
    block["ok"] = all(check["passed"] for check in block["checks"])
    return block


def taste_law_checks(laws: Any) -> List[Dict[str, Any]]:
    """I check bloccanti delle due leggi del gusto (C8).

    ``laws`` e' il blocco ``report["laws"]``: ``law_a`` (prova e tetto dei
    trattamenti) e ``law_b`` (ripetizioni e durata minima). Ogni violazione
    diventa un check ``passed: False`` con la misura e la soglia, cosi'
    ``apply_verification`` ferma la consegna e ``vx reel`` la vede come uno
    stadio fallito. Una legge rispettata non produce nessun check.
    """
    data = laws if isinstance(laws, Mapping) else {}
    checks: List[Dict[str, Any]] = []
    for law in (data.get("law_a") or {}, data.get("law_b") or {}):
        if not isinstance(law, Mapping):
            continue
        for violation in law.get("violations") or ():
            if not isinstance(violation, Mapping):
                continue
            checks.append(
                {
                    "id": "taste_" + str(violation.get("code") or "law"),
                    "passed": False,
                    "measured": {
                        "code": violation.get("code"),
                        "value": violation.get("measured"),
                        "names": violation.get("names"),
                    },
                    "expected": {"threshold": violation.get("threshold")},
                    "why": str(violation.get("why") or ""),
                }
            )
    return checks


def _recipe_effects(
    section: Dict[str, Any],
    plan: Any,
    workdir: Path,
    *,
    evidence: Optional[Mapping[str, Mapping[str, Any]]] = None,
    frames: Optional[Mapping[str, str]] = None,
) -> tuple[Dict[str, Any], Any]:
    """Genera le spec ``vx.effects`` per clip e le attacca al piano.

    Zero e' un valore: nessuna spec, nessuna tabella sul piano, grafo identico.
    Un nome non valido e' un errore (:class:`vx.efx.EffectError`), non un
    effetto inventato: qui si degrada e si dichiara, senza scrivere nulla.

    Con ``evidence`` (C8, legge A) la generazione e' *strict*: ogni spec porta
    una misura fatta su quella clip, le spec senza prova cadono e si contano,
    e i trattamenti creativi distinti restano sotto il tetto del contratto.
    Senza ``evidence`` (chiamata storica, test) resta il comportamento vecchio.
    """
    per_shot = _recipe_param(section, "effects_per_shot")
    if per_shot is None:
        return (
            {
                "applied": False,
                "per_shot": None,
                "clips": 0,
                "specs": 0,
                "reason": "la ricetta non dichiara effects_per_shot",
            },
            plan,
        )
    try:
        density = float(per_shot)
    except (TypeError, ValueError):
        _ignore_param(section, "effects_per_shot", f"valore non numerico: {per_shot!r}")
        return (
            {"applied": False, "per_shot": per_shot, "clips": 0, "specs": 0,
             "reason": f"effects_per_shot non numerico: {per_shot!r}"},
            plan,
        )
    if density <= 0:
        return (
            {
                "applied": True,
                "per_shot": 0.0,
                "clips": 0,
                "specs": 0,
                "reason": "effects_per_shot 0: nessuna spec, il grafo resta quello di prima",
            },
            plan,
        )
    strict = evidence is not None
    try:
        result = efx.generate(
            plan,
            density,
            salt=str(section.get("name") or ""),
            evidence=evidence,
            strict=strict,
        )
    except Exception as exc:  # noqa: BLE001 - un effetto rotto non e' il montaggio
        degrade.note("recipe.effects", exc)
        _ignore_param(
            section,
            "effects_per_shot",
            f"generazione fallita: {type(exc).__name__}: {exc}",
        )
        return (
            {"applied": False, "per_shot": density, "clips": 0, "specs": 0,
             "reason": f"generazione fallita: {type(exc).__name__}: {exc}"},
            plan,
        )

    table = result.get("table") or {}
    if table:
        object.__setattr__(plan, "effects", table)
    summary = efx.summary(result)
    if strict and evidence is not None and frames:
        # La prova del bordo dipende dal fotogramma: se la clip non ne aveva uno
        # la spec "vignette" e' gia' caduta, e il report lo dice.
        summary["frames_measured"] = len(
            [clip_id for clip_id in (table or {}) if clip_id in frames]
        )
    path = Path(workdir) / "effects.json"
    try:
        path.write_text(
            json.dumps({"table": table, "summary": summary}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        degrade.note("effects.file", exc)
        path = None  # type: ignore[assignment]
    detail = (
        f"{summary['specs']} spec su {summary['clips']} clip, costo {summary['cost']:g}"
        f"/{summary['budget']:g}"
    )
    if strict:
        detail += (
            f"; {summary.get('dropped_no_evidence', 0)} scartate senza prova, "
            f"{summary.get('creative_distinct', 0)} trattamenti creativi distinti "
            f"su {summary.get('creative_max', 0)}"
        )
    _declare_param(
        section,
        "effects_per_shot",
        "plan.effects -> render.render_edl",
        detail,
    )
    return (
        {
            "applied": True,
            "per_shot": density,
            **summary,
            "path": str(path) if path else None,
        },
        plan,
    )


def collect_triage_questions(
    workdir: Any,
    *,
    view_active: bool,
    view_url: str = "",
    wait: float = TRIAGE_WAIT_DEFAULT,
    ask_anyway: bool = False,
    notify: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """Le tre domande del triage, nella vista; senza vista il default dichiarato.

    ``ask`` scrive una domanda e aspetta la risposta. Le tre domande sono
    indipendenti, quindi si aprono tutte insieme: chi guarda le vede nello
    stesso momento e l'attesa totale e' ``wait``, non ``3 x wait``. Ognuna ha
    la sua scadenza dentro :func:`vx.ask.ask`; allo scadere il valore e' il
    default e ``answered_by`` lo dice.

    Senza una vista attiva non si scrive niente e non si aspetta: si usano i
    tre default e ogni voce dichiara ``answered_by="default"``. Un guasto di
    ``ask`` non ferma il lavoro: quella voce torna al default con l'errore.
    """
    root = Path(workdir)
    say = notify if notify is not None else _say
    try:
        limit = max(0.0, float(TRIAGE_WAIT_DEFAULT if wait is None else wait))
    except (TypeError, ValueError):
        limit = TRIAGE_WAIT_DEFAULT

    def fallback(spec: Mapping[str, Any], error: str = "") -> Dict[str, Any]:
        entry: Dict[str, Any] = {
            "id": str(spec.get("id") or ""),
            "question": str(spec.get("question") or ""),
            "default": spec.get("default"),
            "value": spec.get("default"),
            "answered_by": "auto",
            "by": "auto",
            "answered": False,
            "asked": False,
            "timed_out": False,
            "expired": False,
            "waited_sec": 0.0,
            "choice": spec.get("default"),
        }
        if error:
            entry["error"] = error
            entry["degraded"] = True
        return entry

    # Senza vista non si aspetta nessuno. L'unica eccezione e' un'attesa
    # esplicita (``triage.wait_sec``), che vince sempre e allo scadere dichiara
    # il timeout invece di fingere che nessuno avesse chiesto.
    if limit <= 0 or (not view_active and not ask_anyway):
        return [fallback(spec) for spec in TRIAGE_QUESTIONS]

    if str(view_url or "").strip():
        say(f"Rispondi alle domande del triage nella vista: {view_url}")
    else:
        say(
            "Rispondi alle domande del triage: "
            f"{root} (cartella {ask.QUESTION_DIR})"
        )
    if view_active:
        _record_gate(
            root,
            "triage",
            open=True,
            asked=True,
            wait_sec=limit,
        )

    results: Dict[str, Dict[str, Any]] = {}

    def run(spec: Mapping[str, Any]) -> None:
        key = str(spec.get("id") or "")
        try:
            results[key] = ask.ask(
                root,
                key,
                spec.get("question"),
                spec.get("options"),
                default=spec.get("default"),
                context={"stage": "triage", "from": "auto"},
                timeout=limit,
            )
        except Exception as exc:  # noqa: BLE001 - una domanda rotta non ferma il lavoro
            results[key] = {
                "answered_by": "default",
                "value": spec.get("default"),
                "timed_out": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

    threads = [
        threading.Thread(target=run, args=(spec,), daemon=True)
        for spec in TRIAGE_QUESTIONS
    ]
    for thread in threads:
        thread.start()
    # ``ask`` ha gia' la sua scadenza; il margine copre solo la scrittura.
    for thread in threads:
        thread.join(limit + 5.0)

    entries: List[Dict[str, Any]] = []
    for spec in TRIAGE_QUESTIONS:
        key = str(spec.get("id") or "")
        result = results.get(key)
        if not isinstance(result, Mapping):
            entries.append(fallback(spec, "nessuna risposta entro la scadenza"))
            continue
        raw_by = str(result.get("answered_by") or "default")
        expired = bool(result.get("timed_out"))
        answered_by = "user" if raw_by == "user" else ("timeout" if expired else "auto")
        entry = {
            "id": key,
            "question": str(spec.get("question") or ""),
            "default": spec.get("default"),
            "value": result.get("value"),
            "answered_by": answered_by,
            "by": answered_by,
            "answered": raw_by == "user",
            "asked": True,
            "timed_out": expired,
            "expired": expired,
            "waited_sec": round(float(result.get("waited_sec") or 0.0), 3),
            "choice": result.get("value"),
        }
        if result.get("error"):
            entry["error"] = str(result.get("error"))
            entry["degraded"] = True
        entries.append(entry)
    for entry in entries:
        if entry["answered_by"] == "user":
            say(f"Triage: {entry['id']} = {entry['value']} (scelto nella vista).")
        elif entry["answered_by"] == "timeout":
            say(
                f"Triage: {entry['id']} = {entry['value']} "
                f"(nessuna risposta in {limit:g} s, default dichiarato)."
            )
        else:
            say(f"Triage: {entry['id']} = {entry['value']} (default dichiarato).")
    if view_active:
        triage_by = "auto"
        if any(item["answered_by"] == "user" for item in entries):
            triage_by = "user"
        elif any(item["expired"] for item in entries):
            triage_by = "timeout"
        _record_gate(
            root,
            "triage",
            open=False,
            asked=True,
            wait_sec=limit,
            choice={item["id"]: item["value"] for item in entries},
            answered_by=triage_by,
            expired=any(item["expired"] for item in entries),
        )
    return entries


def _run_locked(
    args: Any,
    workdir: Path,
    *,
    progress: Optional[Callable[[float, str], None]] = None,
    progress_file: Optional[str] = None,
    montage: bool = False,
    fill: str = "fit",
    view_url: str = "",
    view_active: bool = False,
) -> Dict[str, Any]:
    from . import events as events_mod
    from . import features as features_mod

    def watching() -> bool:
        """C'e' qualcuno *adesso*? Un client puo' aprire la vista a lavoro avviato."""
        return bool(view_active) or view_client_recent(workdir)

    log = events_mod.EventLog(workdir / "events.jsonl")
    flags = features_mod.effective()
    clock = _Clock()
    clock.facts["ram_free_mb"] = resources.ram_free_mb()
    clock.facts["gpu"] = resources.gpu()
    _emit(log, "run", "start", Path(args.out).name)

    # La ricetta si carica prima di tutto il resto: i suoi settings devono
    # essere gia' nel cascade quando i moduli leggono i valori, e i suoi
    # parametri decidono taglio, testo, loudness ed effetti. Se non c'e' o e'
    # rotta, il lavoro prosegue coi default e il report lo dichiara.
    with clock.time("recipe"):
        recipe_section = load_recipe(args, workdir)
    _emit(
        log,
        "recipe",
        "done" if recipe_section.get("applied") else "skip",
        str(recipe_section.get("name") or recipe_section.get("requested") or "nessuna"),
    )

    # Le domande del triage: prima andavano in chat, ora vanno nella vista.
    # Con una vista si aspettano le tre risposte (in parallelo, una sola
    # scadenza); senza vista si usano i default e il report lo dichiara.
    with clock.time("questions"):
        watched = watching()
        try:
            configured = float(settings.get("triage.wait_sec", 0.0) or 0.0)
        except (TypeError, ValueError):
            configured = 0.0
        # Un'attesa esplicita nelle settings vince; altrimenti il default vale
        # solo se c'e' qualcuno che puo' rispondere, e senza vista e' zero.
        if configured > 0:
            question_wait = configured
        elif watched:
            question_wait = TRIAGE_WAIT_DEFAULT
        else:
            question_wait = 0.0
        questions = collect_triage_questions(
            workdir,
            view_active=watched,
            view_url=str(view_url or ""),
            wait=question_wait,
            ask_anyway=configured > 0,
            notify=_say,
        )
    _emit(
        log,
        "questions",
        "done" if watched else "skip",
        ", ".join(
            f"{item['id']}={item['value']} ({item['answered_by']})"
            for item in questions
        ),
    )
    answers = {str(item.get("id")): item for item in questions}
    subtitles_answer = answers.get("subtitles") or {}
    if subtitles_answer.get("answered_by") == "user":
        # L'unica risposta che ha gia' un interruttore nella pipeline: la vista
        # ha scelto, non si finge che il default sia una scelta.
        args.subs = str(subtitles_answer.get("value") or "") != "nessuno"

    with clock.time("music"):
        # La traccia si legge prima del triage: il suo BPM decide la griglia, e
        # il file inesistente deve fermare il lavoro prima di scrivere un master.
        music_section = _music_plan(args)
    _emit(
        log,
        "music",
        "done" if music_section.get("requested") else "skip",
        (
            f"{music_section.get('path')} "
            f"bpm={music_section.get('bpm')} ({music_section.get('bpm_source')})"
            if music_section.get("requested")
            else "nessuna traccia --music"
        ),
    )
    if music_section.get("note"):
        _say(f"Musica: {music_section['note']}")

    with clock.time("triage"):
        assets = _discover(args, music_section)
        assets_by_id = {asset.asset_id: asset for asset in assets}
        fill_mode = _fill_mode(fill)
        montage_mode = bool(montage)

        try:
            payload, recipe_decide_note = _decide_payload(
                args, assets, recipe_section, montage_mode, music=music_section
            )
        except decide.AmbiguousContractError as exc:
            raise contract.VxError(f"prompt ambiguo in STRICT: {exc}") from exc

    block = payload.get("asset_triage")
    if not isinstance(block, Mapping) or not block:
        raise contract.VxError("payload senza asset_triage: triage non eseguito")
    selected = list(block.get("selected_assets") or [])
    rejected = list(block.get("rejected_assets") or [])
    # The canvas the triage actually agreed to: the adaptation is the payload's,
    # not a local note, because scene.resolution is what the EDL and the render
    # read. `order_source` says which rule put the assets in this order.
    order_source = "manifest" if getattr(args, "manifest", None) else "name"
    adapted = block.get("adapted")
    adapted = dict(adapted) if isinstance(adapted, Mapping) else None
    if adapted and adapted.get("resolution"):
        payload["scene"]["resolution"] = [int(value) for value in adapted["resolution"]]
    sharpness_low = float(contract.THRESHOLDS["triage_sharpness_low"])
    selected, opening = apply_opening(block, assets_by_id, threshold=sharpness_low)
    # Il monte a_roll si controlla **prima** del montaggio: se non copre la
    # durata chiesta e c'e' altro video, si promuove e lo si dice. Scoprirlo a
    # render finito e' troppo tardi per fare qualcosa.
    scene_requested = (
        payload.get("scene", {}).get("duration_sec")
        if isinstance(payload.get("scene"), Mapping)
        else None
    )
    selected, promotion = promote_a_roll(
        selected, assets_by_id, scene_requested, montage=montage_mode
    )
    if promotion["promoted"] and not montage_mode:
        # Gli a_roll non coprono la durata chiesta e c'e' altro video: si rifa' la
        # decisione in modalita' montage **prima** del piano, cosi' il reel usa
        # il materiale che c'e' invece di uscire corto. Il ruolo deciso prima non
        # bastava: il piano nasce dal payload, che era gia' stato costruito.
        payload, recipe_decide_note = _decide_payload(
            args, assets, recipe_section, True, music=music_section
        )
        block = payload.get("asset_triage")
        if isinstance(block, Mapping) and block:
            selected, opening = apply_opening(block, assets_by_id, threshold=sharpness_low)
            montage_mode = True
            promotion["redecided_with_montage"] = True
            # Il secondo payload puo' aver scartato o adattato altro: il report
            # deve leggere quello, non quello della prima decisione.
            rejected = list(block.get("rejected_assets") or [])
            adapted = block.get("adapted")
            adapted = dict(adapted) if isinstance(adapted, Mapping) else None
            if adapted and adapted.get("resolution"):
                payload["scene"]["resolution"] = [
                    int(value) for value in adapted["resolution"]
                ]
            _say(
                "Ruoli: il monte a_roll non copriva la durata chiesta, "
                f"decisione rifatta in modalita' montage (+{promotion['a_roll_sec_after'] - promotion['a_roll_sec_before']:.2f} s)"
            )
    for item in promotion["promoted"]:
        _say(
            f"Ruolo: {item['asset_id']} promosso ad a_roll "
            f"(+{float(item['seconds']):.2f} s)"
        )
    if adapted:
        message = str(adapted.get("warning") or "")
        if message:
            _say(message)
    if opening:
        _say(
            "Apertura: "
            + str(opening.get("reason") or "")
            + (" (sotto soglia)" if opening.get("quality") == "below_threshold" else "")
        )
    selected_assets = _selected_asset_infos(selected, assets_by_id)
    with clock.time("hash"):
        source_sha = _source_hashes(selected_assets)
    _declare_beat_grid(recipe_section, payload, recipe_decide_note)
    beat_provenance = _beat_provenance(payload, music_section)
    if music_section.get("requested"):
        music_section.update(
            {
                "bpm": beat_provenance["bpm"],
                "bpm_source": beat_provenance["bpm_source"],
                "grid_enabled": beat_provenance["enabled"],
            }
        )
        if beat_provenance["bpm_source"] == "unavailable":
            _say(
                "Musica: BPM non misurabile, griglia spenta; il montaggio segue "
                "il contenuto (dichiarato nel report)."
            )
        elif beat_provenance["bpm_source"] == "music":
            _say(
                f"Musica: {beat_provenance['bpm']:g} BPM misurati dalla traccia "
                f"({music_section.get('method') or 'misura'}), griglia accesa."
            )
    old_lufs, target_tp = loudness_target(payload)
    loudness_row = _recipe_loudness(recipe_section, old_lufs, target_tp)
    recipe_section["loudness"] = loudness_row
    target_lufs = float(loudness_row["new_lufs"])
    _notify(progress, 5.0, "triage", log=log)

    with clock.time("probe"):
        fps = _fps(args, selected, assets_by_id, rejected)
        timeline_est = _timeline_est(selected, assets_by_id, rejected)

    sources_map: Dict[str, str] = {}
    for entry in selected:
        asset_id = entry.get("asset_id")
        asset = assets_by_id.get(asset_id)
        if asset is None:
            raise contract.VxError(f"asset selezionato {asset_id!r} assente dal pool")
        sources_map[asset_id] = asset.path

    specs = _specs(selected, assets_by_id, fps, timeline_est)
    _log(f"[auto] staging {len(sources_map)} sorgenti a {fps:g} fps")
    with clock.time("ingest"):
        staged = _stage(sources_map, specs, workdir / "stage_00")
        with clock.time("probe"):
            source_refs, media_info_map = _source_refs(staged)
    _notify(progress, 20.0, "ingest", log=log)

    # Il punto di presa si sceglie per misura, non per caso: la clip si guarda
    # fotogramma per fotogramma (profilo di nitidezza), e il piano monta da
    # li'. Senza profilo non si inventa niente: si usa il comportamento di
    # sempre e il report dice ``not_measured``.
    with clock.time("in_point"):
        in_points = frame_choice(
            selected,
            assets_by_id,
            staged,
            black_luma=float(coherence_black_luma()),
            freeze_seconds=float(contract.THRESHOLDS["freeze_min"]),
        )
    for asset_id, row in in_points.items():
        if row.get("excluded"):
            _say(f"Clip esclusa: {asset_id} — {row.get('why')}")
        elif row.get("quality") == "measured" and row.get("moved"):
            _say(
                f"Punto di presa: {asset_id} da {float(row['src_in']):.2f}s "
                f"(nitidezza {float(row['sharpness']):.1f}) — {row.get('why')}"
            )
        elif row.get("quality") == "not_measured":
            _say(f"Punto di presa: {asset_id} non misurato (piano invariato)")

    with clock.time("plan"):
        # La composizione della scaletta: prima di montare, le misure che il
        # lavoro ha già preso (nitidezza dell'apertura e dei b-roll, corse utili
        # del probe, BPM della traccia) diventano tre proposte. Il piano si
        # costruisce due volte — senza e con la proposta — perché il numero di
        # inquadrature che il report dichiara deve essere **contato**, non
        # promesso. Il costo è dati puri, nessun render.
        from . import sections as sections_mod  # noqa: PLC0415

        facts = _composition_facts(
            selected,
            assets_by_id,
            in_points,
            opening,
            beat_provenance,
            scene_requested,
            fps,
            media_info_map,
            beat_block=payload.get("beat_grid") if isinstance(payload.get("beat_grid"), Mapping) else None,
            expectations=sections_mod.expectations(getattr(args, "prompt", "")),
        )
        composition = sections_mod.compose(facts)
        # Il punto di presa può spostarsi **per il gancio**, e solo allora: la
        # baseline resta quella misurata dal probe, così il report conta il
        # prima/dopo invece di raccontarlo.
        in_points_baseline = {key: dict(value) for key, value in in_points.items()}
        take = {}
        rules_block = composition.get("rules") if isinstance(composition.get("rules"), Mapping) else {}
        hook_rules = rules_block.get("hook") if isinstance(rules_block.get("hook"), Mapping) else {}
        if isinstance(hook_rules.get("take"), Mapping):
            take = hook_rules["take"]
        moved_asset = str((composition.get("proposed") or {}).get("hook_asset") or "")
        if take.get("moved") and moved_asset in in_points:
            row = in_points[moved_asset]
            row["src_in"] = float(take.get("to") or 0.0)
            row["in_point"] = float(take.get("to") or 0.0)
            row["moved"] = True
            row["why"] = (
                f"{str(row.get('why') or '').strip().rstrip(';')}; composizione: punto "
                f"di presa spostato a {float(take.get('to') or 0.0):.2f}s "
                f"(nitidezza {float(take.get('sharpness') or 0.0):.1f} ≥ soglia "
                f"preferita {float(take.get('preferred') or 0.0):.2f}) per un gancio "
                f"di {float(hook_rules.get('seconds') or 0.0):.2f}s"
            ).lstrip("; ")
        if composition.get("proposed"):
            payload["composition"] = composition
        composition_baseline = contract.payload_to_edl_plan(
            {key: value for key, value in payload.items() if key != "composition"},
            source_refs,
            media_info_map,
            fps=fps,
            quality=in_points_baseline,
        )
        plan = contract.payload_to_edl_plan(
            payload, source_refs, media_info_map, fps=fps, quality=in_points
        )
        errors = plan.validate()
        if errors:
            raise contract.VxError("EDL non valido: " + "; ".join(errors))
        composition_applied = sections_mod.measure_application(
            composition, composition_baseline, plan
        )
        take_applied = composition_applied.get("applied") or {}
        if (
            take_applied.get("hook_take_moved")
            and take_applied.get("hook_take_honored") is False
            and moved_asset in in_points_baseline
        ):
            # Il piano ha dovuto usare la corsa intera per arrivare alla durata
            # chiesta: il punto di presa spostato non è servito, quindi il
            # report torna a quello vero invece di dichiarare un numero che il
            # piano non usa.
            in_points[moved_asset].update(in_points_baseline[moved_asset])
            note = (
                "il piano usa la corsa intera per la durata chiesta: il punto di "
                "presa non serve al gancio"
            )
            rules_hook = (composition.get("rules") or {}).get("hook")
            if isinstance(rules_hook, dict) and isinstance(rules_hook.get("take"), dict):
                rules_hook["take"]["honored"] = False
                rules_hook["take"]["note"] = note
            take_applied["hook_take_note"] = note
            composition_applied["applied"] = take_applied
        _say(sections_mod.describe(composition_applied))

    # C8, legge B: prima del ponte si tolgono le inquadrature ripetute. Il
    # ponte costruisce caption, reframe e bus audio sul piano che riceve:
    # accorciare dopo disallineerebbe tutto. Il blocco ``variety`` va nel report.
    with clock.time("variety"):
        plan, variety_block = _shorten_variety(plan, workdir)
    _emit(
        log,
        "variety",
        "done" if variety_block.get("removed") else "skip",
        (
            f"{variety_block.get('removed')} ripetizioni tolte, "
            f"{variety_block.get('duration_before')}s -> "
            f"{variety_block.get('duration_after')}s"
            if variety_block.get("removed")
            else str(variety_block.get("note") or "materiale vario, piano intatto")
        ),
    )

    with clock.time("plan"):
        media_by_source = _media_by_source_key(plan, staged, media_info_map)
        staged_owner = {
            str(_staged_path(result)): asset_id
            for asset_id, result in staged.items()
        }
        source_owner = {
            str(key): staged_owner.get(str(getattr(ref, "path", "") or ""))
            or str(key)
            for key, ref in getattr(plan, "sources", {}).items()
        }
        segment_rows = segment_quality(plan, in_points, owner=source_owner)

    bridge_summary: Dict[str, Any] = {
        "enabled": False,
        "cuts": 0,
        "gained_clips": 0,
        "not_applied": 0,
        "captions": 0,
        "reframe": 0,
        "restore": 0,
        "subtitles": None,
        "report": None,
    }
    reframe_map: Dict[str, Any] = {}
    restore_map: Dict[str, Any] = {}
    captions_ass: Optional[str] = None
    captions_srt: Optional[str] = None
    bridge_gains_applied = False
    analyses: Dict[str, Any] = {}
    motion: Dict[str, Any] = {}
    bridge_report: Any = None
    if not bool(getattr(args, "no_bridge", False)):
        _notify(progress, 22.0, "bridge", log=log, status="start")
        # Un testo sopra il video non si scrive se nessuno l'ha chiesto: ma se la
        # richiesta lo dice a parole ("con sottotitoli"), quella e' una richiesta
        # e va onorata. Prima si guardava solo il flag, e il prompt veniva perso
        # in silenzio: il report diceva "no speech" mentre lo stadio non partiva.
        subs_enabled = subs_requested(args)
        plan, bridge_report, analyses, motion = _bridge_plan(
            plan,
            captions=subs_enabled,
            media=media_by_source,
            target_lufs=target_lufs,
            clock=clock,
            workdir=workdir,
        )
        reframe_map, restore_map = _render_effects(bridge_report)
        bridge_gains_applied = any("gain_db" in item for item in bridge_report.audio)
        bridged_errors = plan.validate()
        if bridged_errors:
            raise contract.VxError(
                "EDL dopo il ponte non valido: " + "; ".join(bridged_errors)
            )
        if bridge_report.captions:
            from . import bridge as bridge_mod

            # L'ASS nasce dopo il cancello dei font: la famiglia scelta deve
            # arrivare nel `Fontname`, non solo nel report. Il sidecar SRT non
            # ha font e si scrive subito.
            sidecar = bridge_mod.caption_srt(
                bridge_report.captions,
                workdir / "captions.srt",
            )
            captions_srt = str(sidecar) if sidecar is not None else None
        report_path = (workdir / "bridge_report.json").resolve()
        bridge_report.write(report_path)
        bridge_summary = {
            "enabled": True,
            "cuts": len(bridge_report.cuts),
            "gained_clips": len(bridge_report.audio),
            "not_applied": len(bridge_report.not_applied),
            "captions": len(bridge_report.captions),
            "reframe": sum(
                1 for item in bridge_report.reframe if item.get("keyframes")
            ),
            "restore": sum(
                1
                for item in bridge_report.restore
                if item.get("denoise") or item.get("stabilize")
            ),
            "subtitles": captions_ass,
            "subtitles_srt": captions_srt,
            "report": str(report_path),
        }
        _log(
            f"[auto] bridge: {len(bridge_report.cuts)} tagli, "
            f"{len(bridge_report.audio)} gain, "
            f"{len(bridge_report.captions)} caption, "
            f"{len(bridge_report.not_applied)} non applicati"
        )
    _emit(
        log,
        "bridge",
        "done" if bridge_summary["enabled"] else "skip",
        "eseguito" if bridge_summary["enabled"] else "--no-bridge",
    )

    with clock.time("plan"):
        (workdir / "plan.json").write_text(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # Il montaggio deve avere un senso per default: il referto si calcola qui,
    # prima del render, e finisce in ``report["coherence"]``. Le violazioni non
    # fermano il lavoro, ma non possono sparire nel silenzio.
    with clock.time("coherence"):
        coherence_section, coherence_frames = _coherence_section(
            args, workdir, plan, bridge_report
        )
    from . import coherence as coherence_mod  # noqa: PLC0415
    # La mappa clip_id -> PNG si costruisce sul piano che la coerenza ha
    # guardato: se il piano creativo cambia, i clip sopravvissuti portano la
    # prova del proprio fotogramma, non quella di un indice riciclato.
    evidence_frames = coherence_mod.frames_by_clip(plan, coherence_frames)
    _emit(
        log,
        "coherence",
        "done" if coherence_section.get("ok") else "warn",
        ", ".join(
            str(item.get("code")) for item in coherence_section.get("violations") or ()
        )
        or str(coherence_section.get("reason") or "ok"),
    )

    # Il piano esiste: prima di partire si dichiara quanto ci vorra'. Se la
    # stima supera il tetto (``--budget``/settings) si chiede full o fast;
    # sotto il tetto non si chiede niente e non si scrive nessun file.
    with clock.time("plan_choice"):
        plan_choice = _run_plan_gate(
            args, workdir, plan, view_active=watching()
        )
    plan = plan_choice["plan"]
    if plan_choice["plan_choice"].get("chosen") == "fast":
        # Il piano su disco deve essere quello che si rende, non quello di prima.
        try:
            (workdir / "plan.json").write_text(
                json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001 - il referto resta la fonte della scelta
            pass
    _emit(
        log,
        "plan_choice",
        "done" if plan_choice["plan_choice"].get("asked") else "skip",
        f"{plan_choice['plan_choice'].get('chosen')} "
        f"{plan_choice['estimate'].get('seconds')}s "
        f"({plan_choice['plan_choice'].get('selected_by')})",
    )

    # Punto 3: due piani leciti che competono su un punteggio unico, calcolato
    # dalle metriche gia' misurate (coerenza, prompt, costo, novita'). Niente
    # modello e niente rete: la scelta e' riproducibile e il registro la prova.
    # Con i sottotitoli la timeline e' anche il tempo delle caption, quindi il
    # montaggio non si riordina: un solo candidato, dichiarato.
    has_captions = bool(
        bridge_report is not None and getattr(bridge_report, "captions", None)
    )
    with clock.time("creative"):
        creative_section, plan = _run_creative(
            args,
            plan,
            coherence_section,
            plan_choice["estimate"],
            payload,
            captions=has_captions,
        )
    _emit(
        log,
        "creative",
        "done" if creative_section.get("chosen") else "skip",
        f"{creative_section.get('chosen')}: {creative_section.get('thesis')}",
    )
    if plan is not plan_choice["plan"]:
        # Il piano su disco deve essere quello che si rende, non quello di prima.
        try:
            (workdir / "plan.json").write_text(
                json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001 - il referto resta la fonte della scelta
            pass

    # Gli effetti della ricetta: dopo che il piano definitivo e' stato scelto
    # (creative puo' sostituirlo) e prima del render, che li legge da
    # ``plan.effects``. zero e' un valore: nessuna tabella, grafo identico.
    # C8, legge A: la generazione riceve le misure per clip (jitter, cast,
    # volti, bordo dal fotogramma); senza prova la spec non nasce.
    with clock.time("effects"):
        evidence = _effect_evidence(
            plan, motion, bridge_report, evidence_frames
        )
        effects_section, plan = _recipe_effects(
            recipe_section,
            plan,
            workdir,
            evidence=evidence,
            frames=evidence_frames,
        )
    recipe_section["effects"] = effects_section
    _emit(
        log,
        "effects",
        "done" if effects_section.get("applied") else "skip",
        (
            f"{effects_section.get('specs', 0)} spec su "
            f"{effects_section.get('clips', 0)} clip, "
            f"{effects_section.get('dropped_no_evidence', 0)} senza prova, "
            f"{effects_section.get('creative_distinct', 0)} creativi distinti"
            if effects_section.get("applied")
            else str(effects_section.get("reason") or "")
        ),
    )

    # Il piano esiste: ora si propongono i font e si aspetta la scelta, prima del
    # render. Il cancello e' aperto solo se c'e' una vista e non l'hanno spento:
    # in autonomia senza pannello si prende il ``chosen`` e lo si dichiara.
    with clock.time("font"):
        font_section = _run_font_gate(
            args,
            workdir,
            plan,
            payload,
            view_url=view_url,
            view_active=watching(),
        )
    _emit(
        log,
        "font",
        "done" if font_section.get("family") else "skip",
        str(font_section.get("family") or font_section.get("error") or ""),
    )

    # La scelta deve arrivare al pixel, non solo al report: l'ASS si scrive qui,
    # dopo il cancello, con il `Fontname` di `cli._job_font` e la sua cartella.
    caption_font = _caption_font(workdir)
    if bridge_report is not None and getattr(bridge_report, "captions", None):
        from . import bridge as bridge_mod

        written = bridge_mod.caption_ass(
            bridge_report.captions,
            workdir / "captions.ass",
            aspect=str(plan.aspect_ratio),
            out_w=int(plan.resolution[0]),
            out_h=int(plan.resolution[1]),
            font_family=caption_font["family"] or None,
        )
        captions_ass = str(written) if written is not None else None
        bridge_summary["subtitles"] = captions_ass
    if not caption_font["family"]:
        chosen = str(font_section.get("family") or "")
        if chosen:
            font_section["fallback_reason"] = caption_font.get(
                "fallback_reason"
            ) or (
                f"{chosen}: nessun file in {workdir / 'fonts'}, "
                "il master resta sul preset"
            )

    # Il ponte piano -> ASS -> render. Sta dopo il cancello dei font (la
    # famiglia scelta deve poter arrivare al `Fontname`) e prima del render:
    # senza testo nel brief non scrive nessun file e restituisce l'ASS delle
    # caption esistenti, cioe' il comportamento di prima.
    try:
        with clock.time("typography"):
            typography_section, typography_render = _run_typography(
                workdir,
                plan,
                getattr(args, "prompt", ""),
                caption_font,
                captions_ass,
                bridge_report,
                recipe_brief=_recipe_text_brief(recipe_section),
            )
    except Exception as exc:  # noqa: BLE001 - la scrittura del testo non e' il render
        degrade.note("typography", exc)
        typography_section = {
            "applied": False,
            "degraded": True,
            "reason": f"tipografia non applicata: {type(exc).__name__}: {exc}",
        }
        typography_render = captions_ass
    _emit(
        log,
        "typography",
        "done" if typography_section.get("applied") else "skip",
        ", ".join(
            f"{item['kind']}@{item['at']}s"
            for item in typography_section.get("texts") or ()
        )
        or str(typography_section.get("reason") or ""),
    )
    typography_fonts_dir = str(
        (typography_section.get("font") or {}).get("fonts_dir") or ""
    )
    _declare_text_params(recipe_section, typography_section, plan)

    exemptions = grammar.camera_exemptions(plan)
    try:
        grammar.assert_cinematic_rules(plan)
    except grammar.GrammarError as exc:
        # The CLI reports the violations; the exemptions travel with them so an
        # undeclared camera is never silently dropped from the failure report.
        exc.camera_exemptions = exemptions  # type: ignore[attr-defined]
        raise

    gate1 = gates.validate_gate1_durations(plan, media_by_source, fps=fps)

    # Il cancello dei fotogrammi: la timeline e' valida e il render sta per
    # partire. Con una vista si aspetta l'approvazione, al massimo `--key-wait`;
    # senza vista non si aspetta nessuno. Allo scadere si prosegue e lo si
    # dichiara nel report: un'attesa finita non e' un blocco.
    with clock.time("keyframes"):
        key_section = _run_key_gate(
            args, workdir, plan, view_url=view_url, view_active=watching()
        )
    _emit(
        log,
        "keyframes",
        "done" if key_section.get("approved") else "skip",
        str(key_section.get("selected_by") or key_section.get("reason") or ""),
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    staged_paths = {
        key: str(getattr(ref, "path", "") or "") for key, ref in plan.sources.items()
    }
    _log(f"[auto] render {plan.duration_sec:.3f}s -> {out}")
    render_estimate = estimate.begin(
        plan,
        workdir,
        crf=int(getattr(args, "crf", None) or settings.get("render.crf", 18)),
        preset=str(
            getattr(args, "preset", None) or settings.get("render.preset", "medium")
        ),
    )
    _notify(progress, 25.0, "render", log=log, status="start")
    ceiling_dbtp = qc.true_peak_ceiling(target_tp) if bridge_gains_applied else None
    render_cache: Dict[str, Any] = {}
    effects_report: Dict[str, Any] = {}
    if progress_file:
        # Un file lasciato da un render precedente direbbe il falso: si riparte da zero.
        Path(progress_file).unlink(missing_ok=True)
    with clock.time("render"):
        # Il render e' il processo lungo che tiene la macchina: i suoi ffmpeg
        # figli nascono sotto la norma, cosi' mentre encoding uno si puo' ancora
        # lavorare. La priorita' non entra nel bitstream: l'equivalenza resta.
        previous_priority = os.environ.get("VX_CHILD_PRIORITY")
        os.environ["VX_CHILD_PRIORITY"] = "below"
        clock.facts["priority"] = "below_normal"
        clock.facts["concurrent_jobs_render"] = resources.heavy_jobs()
        try:
            with resources.register_job("render", workdir):
                render.render_edl(
                    plan,
                    staged_paths,
                    out,
                    workdir=workdir / "render",
                    loudness=(target_lufs, target_tp),
                    crf=int(getattr(args, "crf", None) or settings.get("render.crf", 18)),
                    preset=str(
                        getattr(args, "preset", None)
                        or settings.get("render.preset", "medium")
                    ),
                    progress_file=progress_file,
                    fill=fill_mode,
                    subtitles=typography_render,
                    reframe=reframe_map,
                    restore=restore_map,
                    ceiling_dbtp=ceiling_dbtp,
                    fonts_dir=typography_fonts_dir
                    or caption_font["fonts_dir"]
                    or None,
                    cache_report=render_cache,
                    effects_report=effects_report,
                )
        finally:
            if previous_priority is None:
                os.environ.pop("VX_CHILD_PRIORITY", None)
            else:
                os.environ["VX_CHILD_PRIORITY"] = previous_priority
    estimate.finish(render_estimate)
    _notify(progress, 92.0, "render_done", log=log, detail="master scritto")

    # Il master nasce dal render, ma il contenitore puo' portare un padding audio
    # oltre l'ultimo frame. La timeline e' quella del piano: se il file la supera
    # di oltre mezzo frame si chiude la coda (stream copy) e si rimisura. Il
    # master resta sul disco in ogni caso; il report dichiara cosa e' successo.
    with clock.time("duration_conform"):
        duration_conform = conform_master_duration(out, plan.duration_sec, fps)
    if duration_conform.get("applied"):
        _log(
            "[auto] coda del master: "
            + str(duration_conform.get("why") or "chiusa sul frame")
        )
    _emit(
        log,
        "duration_conform",
        "done" if duration_conform.get("applied") else "skip",
        str(duration_conform.get("why") or ""),
    )

    with clock.time("qc"):
        qc_result = qc.audit_master(out, target_lufs=target_lufs, target_tp=target_tp)
    _notify(progress, 96.0, "qc", log=log)

    with clock.time("deliverables"):
        produced = deliverables.produce(
            plan,
            out,
            workdir / "out",
            srt_source=Path(captions_srt) if captions_srt else None,
            workdir=workdir,
            # Se i sottotitoli non erano richiesti, il report lo dice; altrimenti
            # "no speech" resterebbe una mezza verita'.
            subtitles_requested=subs_requested(args),
        )
    _notify(progress, 100.0, "deliverables", log=log)

    # La copertina nasce dall'apertura: il frame piu' nitido (col volto, se c'e')
    # dello shot che apre il reel, non il fotogramma a meta' secondo che
    # `deliverables` prende per default. Il gate di coerenza chiama "inutilizzabile"
    # un frame sotto la soglia di nitidezza: la copertina non puo' essere uno di
    # quelli, e quando lo sono tutti il report lo dichiara.
    thumbnail = thumbnail_frame(
        plan, assets_by_id, opening, threshold=sharpness_low
    )
    thumbnail_item = next(
        (item for item in produced if item.get("kind") == "thumbnail"), None
    )
    if thumbnail is not None and thumbnail_item is not None:
        if write_thumbnail(out, thumbnail["t"], thumbnail_item.get("path")):
            target = Path(str(thumbnail_item.get("path")))
            try:
                thumbnail_item["bytes"] = target.stat().st_size
                thumbnail_item["sha256"] = _sha256(target)
            except OSError:  # noqa: BLE001 - the file exists, the number is the loss
                pass
            thumbnail_item.update(
                {
                    "clip_id": thumbnail["clip_id"],
                    "asset_id": thumbnail["asset_id"],
                    "t": thumbnail["t"],
                    "t_src": thumbnail["t_src"],
                    "sharpness": thumbnail["sharpness"],
                    "first_frame_sharpness": thumbnail.get("first_frame_sharpness"),
                    "faces": thumbnail["faces"],
                    "face_preferred": thumbnail.get("face_preferred"),
                    "quality": thumbnail["quality"],
                }
            )
            _say(
                "Copertina: "
                f"{thumbnail['clip_id']} a {thumbnail['t']:.2f}s, "
                f"nitidezza {thumbnail['sharpness']:.1f}"
                + (
                    " (sotto soglia: la migliore disponibile)"
                    if thumbnail["quality"] == "below_threshold"
                    else ""
                )
            )
        else:
            thumbnail = None

    with clock.time("hash"):
        _assert_sources_unchanged(selected_assets, source_sha)

    a_roll_indices: Dict[str, int] = {}
    for entry in selected:
        if entry.get("role") != "a_roll":
            continue
        asset_id = entry.get("asset_id")
        if isinstance(asset_id, str) and asset_id not in a_roll_indices:
            a_roll_indices[asset_id] = len(a_roll_indices)

    # Il numero che il report dichiara come "materiale utile" e' il tetto vero:
    # la somma delle corse utilizzabili, non la somma dei target. Se il piano
    # non lo raggiunge, la nota dice quanta parte era davvero montabile.
    usable_sec = usable_material_sec(in_points, selected, assets_by_id)
    if usable_sec <= 0.0:
        usable_sec = timeline_est
    staged_owner = {
        str(_staged_path(result)): asset_id for asset_id, result in staged.items()
    }
    source_owner = {
        str(key): staged_owner.get(str(getattr(ref, "path", "") or "")) or str(key)
        for key, ref in getattr(plan, "sources", {}).items()
    }
    runs_used = plan_runs_used(plan, in_points, source_owner)

    scene_block = payload.get("scene") if isinstance(payload, dict) else None
    requested_sec = (
        scene_block.get("duration_sec") if isinstance(scene_block, dict) else None
    )
    shortfall = duration_shortfall(requested_sec, plan.duration_sec)
    # C8, legge B: un reel accorciato per togliere le ripetizioni non e' un
    # reel fuori durata. La durata *dichiarata* diventa quella vera; se pero'
    # scende sotto il minimo, la dichiarazione resta il minimo e ``reel`` firma
    # il render come fallito: sotto quella soglia non si consegna.
    declared_sec = requested_sec
    if variety_block.get("removed"):
        declared_sec = float(plan.duration_sec)
        if variety_block.get("below_minimum"):
            declared_sec = max(
                declared_sec,
                float(contract.THRESHOLDS["variety_min_duration_sec"]),
            )

    # Le cinque scelte del giro, con la prova che le sostiene. Il registro si
    # costruisce qui, quando tutte le sezioni hanno gia' i loro numeri. Un
    # registro che non si costruisce e' un accessorio che cede: il report lo
    # dichiara in ``degraded`` e la consegna prosegue senza il suo elenco.
    try:
        decision_log = build_decisions(
            font_section=font_section,
            key_section=key_section,
            plan_choice=plan_choice["plan_choice"],
            estimate_section=plan_choice["estimate"],
            coherence_section=coherence_section,
            bridge_summary=bridge_summary,
            plan=plan,
            payload=payload,
            requirement=font_requirement(getattr(args, "prompt", ""), payload, plan),
            energy=font_energy(payload, plan),
            creative_section=creative_section,
        )
    except Exception as exc:  # noqa: BLE001 - il registro non e' la consegna
        degrade.note("decisions", exc)
        decision_log = []

    # Il letto musicale: la sezione dichiara la traccia (percorso, durata, BPM
    # misurato o dichiarato) e ogni letto porta il suo percorso, cosi' un
    # ``music.path`` non va cercato in due posti diversi.
    music_beds = music_summary(plan, bridge_report)
    music_report = {
        key: value for key, value in music_section.items() if key != "asset"
    }
    if music_section.get("requested"):
        for bed in music_beds:
            bed["path"] = music_section.get("path")
            bed["duration"] = music_section.get("duration")
            bed["bpm"] = beat_provenance.get("bpm")
            bed["bpm_source"] = beat_provenance.get("bpm_source")
    beat_report = _beat_report(plan, beat_provenance, payload)

    # C8: le due leggi del gusto, misurate sul piano che si sta per rendere.
    # Legge A: ogni spec ha una prova e i nomi distinti restano sotto il tetto.
    # Legge B: le ripetizioni sono state tolte e il reel non e' sotto il minimo.
    law_a = efx.audit(
        getattr(plan, "effects", {}) or {},
        max_creative=contract.THRESHOLDS["max_creative_treatments"],
    )
    law_b = coherence_mod.audit_variety(variety_block)
    laws_block = {
        "ok": bool(law_a.get("ok")) and bool(law_b.get("ok")),
        "law_a": law_a,
        "law_b": law_b,
        "violations": list(law_a.get("violations") or ())
        + list(law_b.get("violations") or ()),
    }

    # I cancelli sanno quanto hanno aspettato una persona: quel tempo esce dal
    # tempo di macchina prima che il report lo dichiari. Serve a non chiamare
    # "lento" un lavoro che era solo fermo in attesa di una risposta.
    gates_block = gates_report(
        questions=questions,
        font_section=font_section,
        key_section=key_section,
        plan_section=plan_choice["plan_choice"],
    )
    clock.waited(gates_block.get("total_waited_sec"))

    # La policy dei giudizi: se qualcuno ha votato abbastanza da scriverla, le
    # sue soglie valgono come le altre. Senza policy (version 0) non cambia nulla.
    try:
        from . import learning as learning_mod  # noqa: PLC0415 - import a chiamata

        policy_block = learning_mod.policy_load()
    except Exception as exc:  # noqa: BLE001 - una policy rotta non ferma il montaggio
        degrade.note("policy", exc)
        policy_block = None

    # La composizione, misurata quando il piano è nato e **ricontrollata** sul
    # piano che si rende: `applied` dice cosa ha fatto la composizione (baseline
    # contro piano composto, prima di varietà e scelta creativa), `survival` dice
    # se il gancio è ancora in testa dopo quelle scelte.
    composition_survival = sections_mod.measure_survival(composition, plan)
    composition_report = dict(composition_applied)
    composition_report["applied"] = {
        **(composition_applied.get("applied") or {}),
        "survived": bool(composition_survival.get("preserved")),
        "shots_final": composition_survival.get("shots_final"),
    }
    composition_report["survival"] = composition_survival

    report: Dict[str, Any] = {
        "ok": True,
        "payload_version": str(payload.get("contract_version") or contract.VERSION),
        "edl_version": str(plan.contract_version),
        "fps": float(fps),
        "loudness_target": {"lufs": target_lufs, "true_peak_db": target_tp},
        "timeline_sec": float(plan.duration_sec),
        "bpm": beat_provenance.get("bpm"),
        "bpm_source": beat_provenance.get("bpm_source"),
        "beat": beat_report,
        "music_source": music_report,
        "audio_tracks": _audio_tracks_report(plan),
        "assets": {
            "total": len(assets),
            "selected": len(selected),
            "rejected": len(rejected),
        },
        "montage": {
            "mode": "montage" if montage_mode else "single",
            "a_roll_count": len(a_roll_indices),
            "roles": promotion,
        },
        "fill": _fill_declaration(plan, media_by_source, fill_mode),
        "recipe": recipe_section,
        "effects": {**effects_section, "render": effects_report},
        "font": font_section,
        "typography": typography_section,
        "text_required": bool(typography_section.get("texts")),
        "texts": [
            str(item.get("text") or "")
            for item in typography_section.get("texts") or ()
            if str(item.get("text") or "").strip()
        ],
        "contrast_region": typography_section.get("contrast_region"),
        "contrast_at": typography_section.get("contrast_at"),
        "keyframes_gate": key_section,
        "questions": questions,
        "gates": gates_block,
        "decisions": decision_log,
        "bridge": bridge_summary,
        "coherence": coherence_section,
        "variety": variety_block,
        "laws": laws_block,
        "estimate": plan_choice["estimate"],
        "plan_choice": plan_choice["plan_choice"],
        "creative": creative_section,
        "render_estimate": render_estimate,
        "audio": audio_summary(analyses, ceiling_dbtp),
        "music": music_beds,
        "selected": [
            {
                "asset_id": entry.get("asset_id"),
                "role": entry.get("role"),
                "type": entry.get("type"),
                "target_duration_sec": entry.get("target_duration_sec"),
                "treatment": entry.get("treatment"),
                "a_roll_index": a_roll_indices.get(entry.get("asset_id")),
            }
            for entry in selected
        ],
        "rejected": [
            {
                "asset_id": entry.get("asset_id"),
                "type": entry.get("type"),
                "rejection_reason": entry.get("rejection_reason"),
                "details": entry.get("details") or {},
            }
            for entry in rejected
        ],
        "not_evaluated": list(block.get("not_evaluated") or []),
        "order_source": order_source,
        "triage": {
            "adapted": adapted,
            "order_source": order_source,
            "opening": opening,
        },
        "opening": opening,
        "plan_notes": list(getattr(plan, "notes", ()) or ()),
        "composition": composition_report,
        "plan": plan_declaration(
            in_points,
            segments=segment_rows,
            plan=plan,
            material_sec=usable_sec,
            runs_used=runs_used,
        ),
        "camera_exemptions": exemptions,
        "gate1": gate1.to_dict(),
        "qc": qc_result,
        "duration_conform": duration_conform,
        "deliverables": produced,
        "master": str(out.resolve()),
        "workdir": str(workdir.resolve()),
        "events_path": str(log.path.resolve()),
        "features": flags,
        "source_sha": source_sha,
        "sources_unchanged": True,
        "resources": _resource_report(clock),
        "timings": clock.as_dict(),
    }
    if shortfall:
        report["duration_shortfall"] = shortfall
        report.setdefault("warnings", []).append(
            "duration_shortfall: chiesti {requested_sec:g} s, consegnati "
            "{delivered_sec:g} s".format(**shortfall)
        )
    if declared_sec is not None:
        try:
            report["duration_requested_sec"] = round(float(declared_sec), 6)
        except (TypeError, ValueError):
            pass
    timeline_block = timeline_declaration(
        requested_sec,
        plan.duration_sec,
        fps,
        material_sec=usable_sec,
    )
    if timeline_block is not None:
        # La coda della timeline: e' l'ultima battuta, quella che si accorcia o
        # si allunga per chiudere sul frame. Il report la dichiara sempre.
        report["timeline"] = timeline_block
        if not timeline_block["tail"].get("reachable", True):
            report.setdefault("warnings", []).append(
                "timeline: " + str(timeline_block["tail"]["note"])
            )
    if isinstance(opening, dict):
        # Se il triage non ha scelto (girato scuro), l'apertura è la prima a_roll:
        # il punto di presa che conta è il suo, misurato dal profilo per-frame.
        # Senza questo fallback il report diceva "non misurato" mentre il piano
        # aveva una misura e la composizione la usava.
        opening_asset_id = (opening.get("chosen") or {}).get("asset_id")
        if not opening_asset_id:
            first_roll = next(
                (
                    str(entry.get("asset_id") or "")
                    for entry in selected
                    if entry.get("role") == "a_roll" and entry.get("asset_id")
                ),
                "",
            )
            opening_asset_id = first_roll or None
        opening["in_point"] = opening_in_point(in_points, opening_asset_id)
        report["opening"] = opening
    excluded_plan = [
        item for item in report["plan"].get("excluded") or () if isinstance(item, Mapping)
    ]
    if excluded_plan:
        report.setdefault("warnings", []).append(
            "plan: {count} clip escluse perche' inutilizzabili ({names}): "
            "la timeline usa il resto".format(
                count=len(excluded_plan),
                names=", ".join(str(item.get("asset_id")) for item in excluded_plan),
            )
        )
    below_plan = list(report["plan"].get("below_preferred") or ())
    if below_plan:
        report.setdefault("warnings", []).append(
            "plan: {count} segmenti sotto la qualita' preferita (non sotto il "
            "pavimento): tenuti e dichiarati".format(count=len(below_plan))
        )
    # La quota si misura sul materiale che il piano **poteva** usare per la
    # durata chiesta, non su tutta la sorgente: con una richiesta da 12 s e 95 s
    # di girato, 12 su 95 non è scarto, è la richiesta. Il taglio dei silenzi
    # non entra qui: quello lo dichiara il ponte col suo riempimento.
    material_used = float(report["plan"].get("material_sec") or 0.0)
    timeline_now = float(report["plan"].get("timeline_sec") or 0.0)
    if declared_sec:
        try:
            material_used = min(material_used, float(declared_sec))
        except (TypeError, ValueError):
            pass
    retained = (timeline_now / material_used) if material_used > 0 else None
    retained_floor = 1.0 - float(IN_POINT_MAX_REMOVAL)
    if retained is not None and retained < retained_floor - 1e-9:
        report.setdefault("warnings", []).append(
            "plan: la timeline tiene il {pct:.0f}% del materiale che poteva usare "
            "({timeline:.2f}s su {material:.2f}s), sotto il minimo dichiarato "
            "{floor:.0%}: il resto e' scarto oltre la quota ammessa".format(
                pct=retained * 100.0,
                timeline=timeline_now,
                material=material_used,
                floor=retained_floor,
            )
        )
    if adapted and adapted.get("warning"):
        report.setdefault("warnings", []).append(str(adapted["warning"]))
    if opening and opening.get("quality") == "below_threshold":
        report.setdefault("warnings", []).append(
            "opening: ogni clip candidata e' sotto la soglia di nitidezza "
            f"{sharpness_low:.1f}; apre la migliore "
            f"({(opening.get('chosen') or {}).get('asset_id')}, "
            f"{(opening.get('chosen') or {}).get('sharpness')})"
        )
    if render_cache:
        report["stage_cache"] = render_cache
    if typography_section.get("applied") and typography_section.get("font", {}).get(
        "fallback"
    ):
        report.setdefault("warnings", []).append(
            "typography: " + str(typography_section["font"].get("note") or "font di ripiego")
        )
    violations = coherence_section.get("violations") or []
    if violations or coherence_section.get("error"):
        report.setdefault("warnings", []).append(
            "coherence: "
            + (
                ", ".join(str(item.get("code")) for item in violations)
                if violations
                else str(coherence_section.get("error"))
            )
        )
    for law_violation in laws_block.get("violations") or ():
        report.setdefault("warnings", []).append(
            "legge {}: {}".format(
                law_violation.get("code") or "?",
                law_violation.get("why") or "",
            )
        )
    # Il `weak_open` non e' un problema di nitidezza: il primo shot non porta un
    # hook. Dirlo qui evita che due violazioni diverse vengano lette come una.
    if opening and any(
        str(item.get("code")) == "weak_open" for item in violations
    ):
        report["opening"]["weak_open"] = {
            "code": "weak_open",
            "cause": (
                "il primo shot non porta un hook: nessun hook_overlay e nessun "
                "primo verso nei primi secondi"
            ),
            "sharpness_related": False,
            "opening_sharpness": (opening.get("chosen") or {}).get("sharpness"),
            "note": (
                "l'apertura e' scelta per nitidezza misurata; questa violazione "
                "riguarda il motivo dell'apertura, non la sua nitidezza"
            ),
        }
        report.setdefault("warnings", []).append(
            "opening: weak_open spiegato — manca l'hook, non la nitidezza"
        )
    # La guardia di consegna: l'audit di `vx.guard` sulla workdir, dopo le
    # consegne e prima del report finale. Il report provvisorio e' la prova che
    # il master e' tracciato (G2), quindi si scrive prima di interrogare
    # l'audit; quello finale lo riscrive con l'esito dentro.
    auto_report = workdir / "auto_report.json"
    # Finche' la guardia e la verifica non hanno parlato, l'esito e' "interrotto":
    # se il processo muore qui il report provvisorio non promette una consegna che
    # nessuno ha ancora deciso. La scrittura finale lo sostituisce.
    report["outcome"] = "incomplete"
    _write_json_atomic(auto_report, report)
    with clock.time("guard"):
        guard_section = run_guard(args, workdir)
    apply_guard(report, guard_section)
    # Le falsificazioni del registro, eseguite sul master consegnato: dopo il
    # QC e la guardia, prima che il report finale venga scritto. Un verdetto
    # negativo non ferma il processo — il master esiste — ma resta nel report
    # con la misura che lo sostiene.
    # Il numero che `verify.check_variety` legge: se una policy esiste, la sua
    # soglia vince sulla costante dichiarata. Va scritto **prima** del verdetto.
    if int((policy_block or {}).get("version") or 0) > 0:
        policy_variety = ((policy_block or {}).get("thresholds") or {}).get("variety_min")
        if isinstance(policy_variety, (int, float)):
            report["min_variety"] = float(policy_variety)
    from . import verify as verify_mod

    try:
        with clock.time("verify"):
            verification = verify_mod.verify_all(report, out)
    except Exception as exc:  # noqa: BLE001 - un verdetto rotto non e' la consegna
        degrade.note("verify", exc)
        verification = {
            "ok": False,
            "checks": [],
            "degraded": ["verify_all"],
            "error": f"{type(exc).__name__}: {exc}",
        }
        report["verification"] = verification
    if verification.get("degraded"):
        degrade.note("verify", why=f"check non eseguiti: {', '.join(verification['degraded'])}")
    if not verification.get("ok"):
        failed = ", ".join(
            str(item.get("id"))
            for item in verification.get("checks") or ()
            if not item.get("passed")
        )
        report.setdefault("warnings", []).append(
            "verification: "
            + (
                f"check falliti ({failed})"
                if failed
                else str(verification.get("error") or "verdetto assente")
            )
        )
    # C8: le due leggi del gusto sono dure. Una violazione non e' un avviso:
    # entra nella verifica come check bloccante, quindi ``apply_verification``
    # ferma la consegna con il motivo in chiaro in ``failures``.
    laws_block = report.get("laws") or {}
    law_checks = taste_law_checks(laws_block)
    if law_checks:
        verification = dict(verification)
        verification["checks"] = list(verification.get("checks") or ()) + law_checks
        verification["ok"] = False
        verification["taste_laws"] = {
            "ok": False,
            "violations": [check["id"] for check in law_checks],
        }
        report["verification"] = verification
    # Un titolo illeggibile non e' un dettaglio da mettere fra gli avvisi: e' la
    # ragione per cui il lavoro non si consegna. Il master resta sul disco (la
    # vista lo mostra come "NON RIUSCITO"), ma ``delivered`` dice la verita'.
    # La verifica si applica **dopo** `measured` e i check della policy: il
    # verdetto deve vedere i numeri del lavoro, non un blocco ancora vuoto.
    report["timings"] = clock.as_dict()
    _emit(
        log,
        "guard",
        "skip"
        if guard_section.get("skipped")
        else ("done" if guard_section.get("ok") else "fail"),
        str(
            guard_section.get("why")
            or (
                f"{len(guard_section.get('violations') or [])} violazioni, "
                "il lavoro non e' consegnabile"
            )
        ),
    )
    # I tempi misurati di questo giro sono la calibrazione del prossimo: la
    # riga si scrive qui, dopo il report, e un errore non ferma la consegna.
    try:
        estimate.record_timings(workdir, report)
    except Exception as exc:  # noqa: BLE001 - la contabilita' non e' il lavoro
        report["timing_history_error"] = f"{type(exc).__name__}: {exc}"
    timings = report["timings"]
    _log(
        "[auto] tempi: "
        + " ".join(f"{stage}={seconds:.1f}s" for stage, seconds in timings.items())
    )
    _emit(log, "timings", "done", json.dumps(timings, ensure_ascii=False))
    _emit(log, "run", "done", f"{len(produced)} consegne")
    # Un master che esiste non e' un lavoro consegnato: la riga lo dice anche
    # a chi legge solo i log, e la riscrittura porta la guardia nel report.
    if not report.get("delivered", True):
        _log("[auto] non consegnato: il video c'e', la consegna no")
    # Il registro delle sessioni: se il comando ne ha aperta una (il decoratore
    # di ``cli``) e non e' riuscita a scrivere, il report lo dichiara qui. Non
    # si riapre e non si duplica: la sessione e' del comando, non del render.
    opened = getattr(args, "_vx_session", None)
    raw_reason = getattr(opened, "last_error", "") if opened is not None else ""
    reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
    if reason:
        degrade.note("session", why=reason)
    # Un accessorio che ha ceduto non cambia l'esito: si dichiara una volta nel
    # report, con il motivo, prima che il report vada su disco.
    degrade.merge_into(report)
    if report.get("degraded"):
        _say("Accessori degradati: " + degrade.text(report) + " - il lavoro e' proseguito.")
    # I numeri che il contratto del lavoro firma, gia' misurati da questo giro:
    # un orchestratore (``vx reel``) li legge da qui invece di rimisurare un
    # master che il motore ha appena controllato.
    contrast_ratio = None
    for item in verification.get("checks") or ():
        if isinstance(item, Mapping) and str(item.get("id")) == "contrast":
            measured = item.get("measured")
            if isinstance(measured, Mapping) and isinstance(
                measured.get("ratio"), (int, float)
            ):
                contrast_ratio = float(measured["ratio"])
            break
    # C8: la firma ``deliver`` di ``vx reel`` e' il contrasto del master
    # consegnabile. Quando una legge del gusto ferma la consegna non esiste un
    # consegnabile da firmare, quindi il numero resta non firmato: il reel vede
    # il proprio stadio ``deliver`` fallire e rinomina in NON-PUBBLICABILE-*.
    if not laws_block.get("ok", True):
        contrast_ratio = None
    report["measured"] = {
        "duration_sec": round(float(plan.duration_sec), 6),
        "requested_duration_sec": (
            round(float(declared_sec), 6)
            if isinstance(declared_sec, (int, float))
            and not isinstance(declared_sec, bool)
            else None
        ),
        "lufs": (qc_result.get("measured") or {}).get("i"),
        "contrast": contrast_ratio,
        "clips": sum(
            len(getattr(track, "clips", ()) or ())
            for track in getattr(plan, "video_tracks", ()) or ()
        ),
        "sources_measured": len(analyses),
        "assets_used": len(selected),
        "gates_wait_sec": (report.get("gates") or {}).get("total_waited_sec"),
    }
    # La policy dei giudizi, adesso che le misure del lavoro esistono: le sue
    # soglie entrano nella verifica come le leggi del gusto, e una violazione
    # ferma la consegna con misura e soglia.
    # Il piano-scaletta: sezioni compilate dalle misure appena scritte. Non
    # decide niente e non spegne nessuna competenza: legge e dichiara.
    from . import sections as sections_mod  # noqa: PLC0415 - solo qui serve

    report["sections"] = sections_mod.compile_sections(report)
    # Le composizioni che meritano un motore grafico (split screen, confronto,
    # sottopancia): dichiarate, con il documento e la sua identità. Il pezzo
    # renderizzato arriverà dopo; intanto la vista e il report sanno cosa
    # aspettarsi invece di scoprirlo alla fine.
    from . import scenes as scenes_mod  # noqa: PLC0415 - solo qui serve

    report["scenes"] = scenes_mod.declare(getattr(args, "prompt", ""))
    # La scaletta guida: quello che la richiesta pretende deve esserci. Un hook
    # o una CTA chiesti a parole e non compilati sono un buco bloccante, con la
    # sezione e lo stato nel motivo — non un dettaglio da leggere a fine lavoro.
    wanted = sections_mod.expectations(getattr(args, "prompt", ""))
    audit = sections_mod.audit(report["sections"], wanted)
    report["sections"]["expected"] = wanted
    report["sections"]["gaps"] = audit["gaps"]
    # "ok" vuol dire che la scaletta regge: se un requisito e' scoperto, non e'
    # ok anche quando qualche sezione e' compilata.
    report["sections"]["ok"] = bool(report["sections"].get("ok")) and audit["ok"]
    if audit["checks"]:
        verification = dict(verification)
        verification["checks"] = list(verification.get("checks") or ()) + list(
            audit["checks"]
        )
        verification["ok"] = False
        report["verification"] = verification
    report["policy"] = policy_checks(report, policy_block)
    if report["policy"].get("checks") and not report["policy"].get("ok", True):
        verification = dict(verification)
        verification["checks"] = list(verification.get("checks") or ()) + list(
            report["policy"]["checks"]
        )
        verification["ok"] = False
        report["verification"] = verification
    apply_verification(report, verification)
    if not report.get("delivered", True):
        _log("[auto] " + str((report.get("failures") or [""])[-1]))
    # Gli intermedi della varieta' (i PNG campionati per il pHash) hanno finito
    # il loro lavoro quando il piano e' deciso: si tolgono **prima** di scrivere
    # il report, cosi' il file su disco e quello restituito sono identici.
    variety_cache = workdir / "cache" / "variety"
    if variety_cache.is_dir():
        try:
            frames = [path for path in variety_cache.rglob("*") if path.is_file()]
            freed = sum(path.stat().st_size for path in frames)
            shutil.rmtree(variety_cache, ignore_errors=True)
            report.setdefault("cleaned", {})["variety_frames"] = len(frames)
            report["cleaned"]["bytes"] = int(freed)
            _log(
                f"[auto] intermedi: {len(frames)} fotogrammi di varieta' rimossi "
                f"({freed / 1024:.0f} KB)"
            )
        except OSError as exc:  # noqa: BLE001 - la pulizia non e' la consegna
            degrade.note("cache", exc)
    # L'esito e' sempre valorizzato, e viene dai fatti: master + verifiche verdi
    # -> ``delivered``; consegna fermata -> ``failed``; lavoro interrotto prima
    # della decisione -> ``incomplete``. Nessun report esce senza questa riga.
    report["outcome"] = final_outcome(report)
    report["outcome_suggested"] = (
        "done" if report["outcome"] == "delivered" else "failed"
    )
    _write_json_atomic(auto_report, report)
    # Il giudizio umano viene **dopo** il report scritto: il segnale deve
    # leggere l'ultimo report, non uno provvisorio. Se il segnale non si scrive
    # il lavoro e' gia' salvo; il cedimento resta nel referto restituito.
    try:
        request = write_feedback_request(
            report, workdir, str(getattr(args, "prompt", "") or "")
        )
        if request.get("asked"):
            _log("[auto] giudizio: chiesto nella workdir, il voto e' facoltativo")
        else:
            _log(f"[auto] giudizio: segnale non scritto ({request.get('error') or 'niente da chiedere'})")
    except Exception as exc:  # noqa: BLE001 - il giudizio non e' la consegna
        degrade.note("feedback", exc)
        degrade.merge_into(report)
        _log(f"[auto] giudizio non registrato: {type(exc).__name__}: {exc}")
    log.close()
    return report


def run(
    args: Any,
    *,
    progress: Optional[Callable[[float, str], None]] = None,
    progress_file: Optional[str] = None,
    montage: Optional[bool] = None,
    fill: Optional[str] = None,
    view_url: str = "",
    view_active: Optional[bool] = None,
) -> Dict[str, Any]:
    """Execute the whole pipeline under the workdir lock and return the report.

    ``montage`` and ``fill`` default to ``None``: the flags on ``args`` win
    unless a caller passes an explicit value, so an existing caller that only
    has ``args`` keeps the single-a_roll and ``fit`` behaviour unchanged.
    ``view_active`` says whether a human can actually answer a gate here
    (``--watch`` or a client that read the state recently); when it is ``None``
    only an explicitly attached view or a fresh ``view_client.json`` counts, not
    a panel that merely writes HTML.
    """
    out = Path(args.out)
    assets_dir = Path(args.assets)
    global _NARRATING
    _NARRATING = progress is not None
    workdir = _resolve_workdir(args)
    montage_mode = (
        bool(getattr(args, "montage", False)) if montage is None else bool(montage)
    )
    fill_mode = (
        str(getattr(args, "fill", "fit") or "fit") if fill is None else str(fill)
    )

    if _inside(out, assets_dir):
        raise contract.VxError(
            "--out non puo' stare dentro --assets: la cartella sorgente non si scrive"
        )
    # Il brano si controlla prima di toccare la workdir: un file inesistente e'
    # un errore d'uso (exit 2), non un lavoro iniziato e poi fermato.
    music_raw = getattr(args, "music", None)
    if music_raw:
        music_path = Path(str(music_raw)).expanduser()
        if not music_path.is_file():
            raise contract.VxError(
                f"--music: file inesistente: {music_path} "
                "(il brano si legge soltanto: nessun master scritto)"
            )
    _declared_bpm(args)
    if _inside(workdir, assets_dir):
        raise contract.VxError(
            "--workdir non puo' stare dentro --assets: la cartella sorgente non si scrive"
        )

    workdir.mkdir(parents=True, exist_ok=True)
    if view_active is None:
        panel = getattr(progress, "panel", None) if progress is not None else None
        # Un pannello che scrive HTML non e' una persona: serve un URL
        # esplicitamente agganciato o un client che ha letto lo stato da poco.
        view_active = view_is_attached(workdir, panel=panel, view_url=view_url)
    beat = lock.acquire(
        workdir,
        stage="auto",
        heartbeat_seconds=float(settings.get("lock.heartbeat_seconds", 30.0)),
    )
    try:
        return _run_locked(
            args,
            workdir,
            progress=progress,
            progress_file=progress_file,
            montage=montage_mode,
            fill=fill_mode,
            view_url=view_url,
            view_active=bool(view_active),
        )
    except Exception as exc:  # noqa: BLE001 - re-raised unchanged, only logged
        from . import events as events_mod

        log = events_mod.EventLog(workdir / "events.jsonl")
        _emit(log, "run", "fail", f"{type(exc).__name__}: {exc}")
        log.close()
        raise
    finally:
        lock.release(beat)
