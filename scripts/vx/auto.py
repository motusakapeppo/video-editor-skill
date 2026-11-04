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


def _discover(args: Any) -> List[asset_triage.AssetInfo]:
    try:
        assets = asset_triage.discover_assets(
            args.assets, manifest=getattr(args, "manifest", None)
        )
    except ValueError as exc:
        raise contract.VxError(f"--assets: {exc}") from exc
    if not assets:
        raise contract.VxError(f"--assets: nessun asset riconosciuto in {args.assets}")
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
    reordered = section.pop("reordered")
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
            runs.append(
                {
                    "from": first,
                    "to": index - 1,
                    "start": float(samples[first]["t"]),
                    "end": float(samples[index - 1]["t"]) + float(step),
                    "seconds": (index - first) * float(step),
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
        at = 0.0
        while at < duration - 1e-6:
            capture.set(cv2.CAP_PROP_POS_MSEC, at * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                at += float(step)
                continue
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
                    "t": round(at, 4),
                    "sharpness": round(sharpness, 3),
                    "luma": round(luma, 5),
                    "diff": round(diff, 3),
                }
            )
            at += float(step)
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
    plan: Any, choice: Mapping[str, Dict[str, Any]], *, step: float = IN_POINT_STEP_SEC
) -> Dict[str, List[Dict[str, Any]]]:
    """Classifica il **punto di presa** di ogni segmento layer-0.

    ``below_preferred`` sono i segmenti che partono sotto la soglia preferita
    della propria clip ma sopra il pavimento: restano nel montaggio, dichiarati.
    ``unusable`` sono quelli che partono sotto il pavimento: non dovrebbero
    esistere, e se esistono il report lo dice invece di nasconderli.
    """
    out: Dict[str, List[Dict[str, Any]]] = {"below_preferred": [], "unusable": []}
    for track in getattr(plan, "video_tracks", ()) or ():
        for clip in getattr(track, "clips", ()) or ():
            if int(getattr(clip, "layer", 0) or 0) != 0:
                continue
            row = choice.get(str(getattr(clip, "source_id", ""))) or {}
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
                "asset_id": str(getattr(clip, "source_id", "") or ""),
                "src_in": round(at, 3),
                "sharpness": value,
                "threshold_preferred": preferred,
            }
            if value < floor:
                out["unusable"].append(record)
            elif value < preferred:
                out["below_preferred"].append(record)
    return out


def plan_declaration(
    choice: Mapping[str, Dict[str, Any]],
    *,
    segments: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    plan: Any = None,
    material_sec: Optional[float] = None,
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
    if plan is not None:
        duration = float(getattr(plan, "duration_sec", 0.0) or 0.0)
        declaration["timeline_sec"] = round(duration, 3)
        if material_sec is not None and float(material_sec) > 0:
            declaration["material_sec"] = round(float(material_sec), 3)
            declaration["duration_retained"] = round(
                duration / float(material_sec), 4
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


def apply_verification(
    report: Dict[str, Any], verification: Mapping[str, Any]
) -> Dict[str, Any]:
    """Scrive il verdetto e toglie la consegna se il titolo e' illeggibile.

    ``delivered`` risponde alla domanda "questo lavoro si consegna?". Un
    contrasto WCAG sotto 4.5 e' la prova che il titolo non si legge: il master
    resta sul disco e la vista lo mostra come NON RIUSCITO, ma nessun report lo
    dichiara consegnato. Il motivo viaggia in chiaro in ``failures``.
    """
    report["verification"] = dict(verification) if isinstance(verification, Mapping) else {}
    contrast = next(
        (
            item
            for item in report["verification"].get("checks") or ()
            if isinstance(item, Mapping) and str(item.get("id")) == "contrast"
        ),
        None,
    )
    if contrast is None or contrast.get("passed"):
        return report
    report["delivered"] = False
    report["outcome_suggested"] = "failed"
    report["outcome"] = "failed"
    measured = contrast.get("measured")
    ratio = measured.get("ratio") if isinstance(measured, Mapping) else None
    reason = (
        "verification: contrasto del titolo "
        + (f"{float(ratio):.2f}" if isinstance(ratio, (int, float)) else "sotto soglia")
        + " < 4.5: titolo illeggibile, non consegnato"
    )
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


def _caption_window(plan: Any, source_id: str) -> Optional[float]:
    """How far into `source_id` the plan actually reads.

    Captions only ever anchor to a clip's own window, so a source used for the
    first eight seconds does not need the other 171 transcribed. The bound is
    the largest ``src_out`` the plan cites for that source; ``None`` means the
    plan does not use it at all and the caller keeps today's full read.
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
    return new_plan, report, analyses


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


def _coherence_section(
    args: Any,
    workdir: Path,
    plan: Any,
    bridge_report: Any,
) -> Dict[str, Any]:
    """Il referto di coerenza, calcolato prima del render.

    Senza flag il lavoro prosegue anche con violazioni: il report e la
    narrazione devono dire quali sono, non bloccare in silenzio ne' fingere un
    successo pulito. ``--free``/``--no-coherence`` spegne i controlli e lo
    dichiara (``disabled``), perche' un ``ok`` senza controllo non e' un ``ok``.
    """
    from . import coherence

    if bool(getattr(args, "free", False) or getattr(args, "no_coherence", False)):
        section = coherence.disabled("--free/--no-coherence")
        _say(f"Coerenza: {section['reason']}")
        return section
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
        }
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
    return section


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
        "family": "",
        "licence": "",
        "source": "",
        "downloaded": False,
        "selected_by": "auto",
        "candidates": [],
        "pool": "local",
        "waited_sec": 0.0,
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

    started = clock()
    family = _choice_family(root)
    selected_by = "user" if family else "auto"
    waited = 0.0
    if not family and gate and float(wait) > 0:
        if str(view_url or "").strip():
            say(f"Scegli il font nella vista: {view_url}")
        else:
            say(f"Scegli il font nella cartella: {root} (file {FONT_CHOICE_NAME})")
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
    section["waited_sec"] = round(waited, 3)

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
    wait = getattr(args, "font_wait", None)
    if wait is None:
        wait = float(settings.get("font.wait_sec", FONT_WAIT_DEFAULT))
    gate = bool(view_active) and not bool(getattr(args, "no_font_gate", False))
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
    wait = getattr(args, "plan_wait", None)
    if wait is None:
        wait = float(
            settings.get("budget.plan_wait_sec", estimate.PLAN_WAIT_DEFAULT)
            or estimate.PLAN_WAIT_DEFAULT
        )
    result = estimate.plan_gate(
        workdir,
        plan,
        budget=float(budget),
        wait=float(wait),
        preset=str(getattr(args, "preset", None) or settings.get("render.preset", "medium")),
        crf=int(getattr(args, "crf", None) or settings.get("render.crf", 18)),
        notify=_say,
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


def _extract_keyframe(source: str, at: float, png: Path) -> str:
    """Un PNG a un istante del sorgente, e il suo sha256; ``""`` se non riesce.

    Un fotogramma che non si estrae non ferma il montaggio: il piano resta
    valido con l'impronta vuota, e il report non finge un pixel che non c'e'.
    """
    png = Path(png)
    png.parent.mkdir(parents=True, exist_ok=True)
    temporary = png.with_name(png.name + ".tmp")
    try:
        proc = subprocess.run(
            [
                ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{float(at):.3f}", "-i", str(source),
                "-frames:v", "1", "-update", "1", str(temporary),
            ],
            capture_output=True,
        )
    except OSError:
        temporary.unlink(missing_ok=True)
        return ""
    if proc.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        return ""
    temporary.replace(png)
    return hashlib.sha256(png.read_bytes()).hexdigest()


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
        sha = _extract_keyframe(source, choice["at"], png) if source else ""
        frames.append(
            {
                "clip": choice["clip"],
                "index": choice["index"],
                "at": choice["at"],
                "sha256": sha,
                "path": str(png),
            }
        )
    timeline_sha = _timeline_fingerprint(plan)
    fingerprint = [
        {
            "clip": item["clip"],
            "index": item["index"],
            "at": item["at"],
            "sha256": item["sha256"],
        }
        for item in frames
    ]
    digest = hashlib.sha256(
        json.dumps(
            {
                "schema": KEYFRAMES_PLAN_SCHEMA,
                "timeline": timeline_sha,
                "frames": fingerprint,
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
    count = int(proposal.get("count") or 0)
    if not digest or count <= 0:
        section["reason"] = str(
            section.get("error") or "nessun piano di fotogrammi"
        )
        say(f"Fotogrammi: {section['reason']}")
        return section

    frames = [dict(item) for item in (proposal.get("frames") or []) if isinstance(item, Mapping)]
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
            {"required": True, "approved": True, "selected_by": "user", "reason": ""}
        )
        say("Fotogrammi: approvati, si va al render.")
    elif gate and float(wait) > 0:
        _write_json_atomic(
            waiting_path,
            {
                "schema": KEYFRAMES_WAIT_SCHEMA,
                "since_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "frames": frames,
            },
        )
        section.update({"required": True, "waiting": True})
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
                {"approved": True, "selected_by": "user", "reason": ""}
            )
            say("Fotogrammi: approvati nella vista, si va al render.")
        else:
            section.update(
                {
                    "approved": False,
                    "selected_by": "timeout",
                    "reason": f"nessuna approvazione in {float(wait):g} s",
                }
            )
            say(
                f"Fotogrammi: nessuna approvazione in {float(wait):g} s — "
                "il render prosegue e il report lo dichiara."
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
            }
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
    wait = getattr(args, "key_wait", None)
    if wait is None:
        wait = float(settings.get("key.wait_sec", KEY_GATE_WAIT_DEFAULT))
    explicit = getattr(args, "key_gate", None)
    gate = bool(view_active) if explicit is None else bool(explicit)
    if not gate:
        closed_reason = (
            "cancello dei fotogrammi spento (--no-key-gate)"
            if explicit is False
            else "nessuna vista: il cancello dei fotogrammi non aspetta nessuno"
        )
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
    font = _typography_font(brief.font, caption_font, workdir)
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
) -> tuple[Dict[str, Any], str]:
    """``parse_and_decide`` coi parametri della ricetta; degrada se serve un BPM.

    ``beat_grid: on`` senza BPM e' un ``VxError`` onesto di ``decide`` ("passa
    --bpm o scrivilo nel prompt"), ma qui non c'e' nessun flag da passare: il
    lavoro riparte senza griglia e il report dichiara il parametro ignorato,
    invece di morire su una ricetta che chiedeva piu' di quanto il prompt dia.
    """
    grid, bpm = _recipe_beat_args(section)
    common = {
        "asset_pool": assets,
        "force_mode": "autonomous" if getattr(args, "autonomous", False) else None,
        "montage": bool(montage_mode),
    }
    try:
        payload = decide.parse_and_decide(args.prompt, beat_grid=grid, bpm=bpm, **common)
        return payload, ""
    except contract.VxError as exc:
        if grid == "on" and "BPM" in str(exc):
            degrade.note("recipe.beat_grid", exc)
            payload = decide.parse_and_decide(args.prompt, beat_grid=None, bpm=None, **common)
            return payload, str(exc)
        raise


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


def _recipe_effects(
    section: Dict[str, Any], plan: Any, workdir: Path
) -> tuple[Dict[str, Any], Any]:
    """Genera le spec ``vx.effects`` per clip e le attacca al piano.

    Zero e' un valore: nessuna spec, nessuna tabella sul piano, grafo identico.
    Un nome non valido e' un errore (:class:`vx.efx.EffectError`), non un
    effetto inventato: qui si degrada e si dichiara, senza scrivere nulla.
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
    try:
        result = efx.generate(
            plan, density, salt=str(section.get("name") or "")
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
    _declare_param(
        section,
        "effects_per_shot",
        "plan.effects -> render.render_edl",
        f"{summary['specs']} spec su {summary['clips']} clip, costo {summary['cost']:g}"
        f"/{summary['budget']:g}",
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
            "answered_by": "default",
            "answered": False,
            "timed_out": False,
        }
        if error:
            entry["error"] = error
            entry["degraded"] = True
        return entry

    if not view_active or limit <= 0:
        return [fallback(spec) for spec in TRIAGE_QUESTIONS]

    if str(view_url or "").strip():
        say(f"Rispondi alle domande del triage nella vista: {view_url}")
    else:
        say(
            "Rispondi alle domande del triage: "
            f"{root} (cartella {ask.QUESTION_DIR})"
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
        entry = {
            "id": key,
            "question": str(spec.get("question") or ""),
            "default": spec.get("default"),
            "value": result.get("value"),
            "answered_by": str(result.get("answered_by") or "default"),
            "answered": str(result.get("answered_by") or "") == "user",
            "timed_out": bool(result.get("timed_out")),
        }
        if result.get("error"):
            entry["error"] = str(result.get("error"))
            entry["degraded"] = True
        entries.append(entry)
    for entry in entries:
        if entry["answered_by"] == "user":
            say(f"Triage: {entry['id']} = {entry['value']} (scelto nella vista).")
        elif entry["answered_by"] == "default":
            say(f"Triage: {entry['id']} = {entry['value']} (default dichiarato).")
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
        try:
            question_wait = float(
                settings.get("triage.wait_sec", TRIAGE_WAIT_DEFAULT)
            )
        except (TypeError, ValueError):
            question_wait = TRIAGE_WAIT_DEFAULT
        questions = collect_triage_questions(
            workdir,
            view_active=bool(view_active),
            view_url=str(view_url or ""),
            wait=question_wait,
            notify=_say,
        )
    _emit(
        log,
        "questions",
        "done" if view_active else "skip",
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

    with clock.time("triage"):
        assets = _discover(args)
        assets_by_id = {asset.asset_id: asset for asset in assets}
        fill_mode = _fill_mode(fill)
        montage_mode = bool(montage)

        try:
            payload, recipe_decide_note = _decide_payload(
                args, assets, recipe_section, montage_mode
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
        plan = contract.payload_to_edl_plan(
            payload, source_refs, media_info_map, fps=fps, quality=in_points
        )
        errors = plan.validate()
        if errors:
            raise contract.VxError("EDL non valido: " + "; ".join(errors))
        media_by_source = _media_by_source_key(plan, staged, media_info_map)
        segment_rows = segment_quality(plan, in_points)

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
    bridge_report: Any = None
    if not bool(getattr(args, "no_bridge", False)):
        _notify(progress, 22.0, "bridge", log=log, status="start")
        # Un testo sopra il video non si scrive se nessuno l'ha chiesto.
        subs_enabled = bool(getattr(args, "subs", False))
        plan, bridge_report, analyses = _bridge_plan(
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
        coherence_section = _coherence_section(args, workdir, plan, bridge_report)
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
        plan_choice = _run_plan_gate(args, workdir, plan)
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
    with clock.time("effects"):
        effects_section, plan = _recipe_effects(recipe_section, plan, workdir)
    recipe_section["effects"] = effects_section
    _emit(
        log,
        "effects",
        "done" if effects_section.get("applied") else "skip",
        (
            f"{effects_section.get('specs', 0)} spec su "
            f"{effects_section.get('clips', 0)} clip"
            if effects_section.get("applied")
            else str(effects_section.get("reason") or "")
        ),
    )

    # Il piano esiste: ora si propongono i font e si aspetta la scelta, prima del
    # render. Il cancello e' aperto solo se c'e' una vista e non l'hanno spento:
    # in autonomia senza pannello si prende il ``chosen`` e lo si dichiara.
    with clock.time("font"):
        font_section = _run_font_gate(
            args, workdir, plan, payload, view_url=view_url, view_active=view_active
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
            args, workdir, plan, view_url=view_url, view_active=view_active
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

    scene_block = payload.get("scene") if isinstance(payload, dict) else None
    requested_sec = (
        scene_block.get("duration_sec") if isinstance(scene_block, dict) else None
    )
    shortfall = duration_shortfall(requested_sec, plan.duration_sec)

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

    report: Dict[str, Any] = {
        "ok": True,
        "payload_version": str(payload.get("contract_version") or contract.VERSION),
        "edl_version": str(plan.contract_version),
        "fps": float(fps),
        "loudness_target": {"lufs": target_lufs, "true_peak_db": target_tp},
        "timeline_sec": float(plan.duration_sec),
        "assets": {
            "total": len(assets),
            "selected": len(selected),
            "rejected": len(rejected),
        },
        "montage": {
            "mode": "montage" if montage_mode else "single",
            "a_roll_count": len(a_roll_indices),
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
        "decisions": decision_log,
        "bridge": bridge_summary,
        "coherence": coherence_section,
        "estimate": plan_choice["estimate"],
        "plan_choice": plan_choice["plan_choice"],
        "creative": creative_section,
        "render_estimate": render_estimate,
        "audio": audio_summary(analyses, ceiling_dbtp),
        "music": music_summary(plan, bridge_report),
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
        "plan": plan_declaration(
            in_points,
            segments=segment_rows,
            plan=plan,
            material_sec=timeline_est,
        ),
        "camera_exemptions": exemptions,
        "gate1": gate1.to_dict(),
        "qc": qc_result,
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
    if isinstance(opening, dict):
        opening["in_point"] = opening_in_point(
            in_points, (opening.get("chosen") or {}).get("asset_id")
        )
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
    retained = report["plan"].get("duration_retained")
    if isinstance(retained, (int, float)) and float(retained) < 0.85:
        report.setdefault("warnings", []).append(
            "plan: la timeline tiene il {pct:.0f}% del materiale ottenibile "
            "({timeline:.2f}s su {material:.2f}s): il resto e' davvero "
            "inutilizzabile".format(
                pct=float(retained) * 100.0,
                timeline=float(report["plan"].get("timeline_sec") or 0.0),
                material=float(report["plan"].get("material_sec") or 0.0),
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
    _write_json_atomic(auto_report, report)
    with clock.time("guard"):
        guard_section = run_guard(args, workdir)
    apply_guard(report, guard_section)
    # Le falsificazioni del registro, eseguite sul master consegnato: dopo il
    # QC e la guardia, prima che il report finale venga scritto. Un verdetto
    # negativo non ferma il processo — il master esiste — ma resta nel report
    # con la misura che lo sostiene.
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
    # Un titolo illeggibile non e' un dettaglio da mettere fra gli avvisi: e' la
    # ragione per cui il lavoro non si consegna. Il master resta sul disco (la
    # vista lo mostra come "NON RIUSCITO"), ma ``delivered`` dice la verita'.
    apply_verification(report, verification)
    if not report.get("delivered", True):
        _log("[auto] " + str((report.get("failures") or [""])[-1]))
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
    ``view_active`` says whether a human can actually see this job (panel or
    ``--watch``); when it is ``None`` a narrator carrying a panel counts as one.
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
    if _inside(workdir, assets_dir):
        raise contract.VxError(
            "--workdir non puo' stare dentro --assets: la cartella sorgente non si scrive"
        )

    workdir.mkdir(parents=True, exist_ok=True)
    if view_active is None:
        panel = getattr(progress, "panel", None) if progress is not None else None
        view_active = bool(view_url) or panel is not None
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
