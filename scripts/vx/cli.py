"""vx.cli — the execution protocol (Fase 7).

Pipeline law:
    ingest  ->  gate (exit 3 = ask the human)  ->  plan  ->  subs  ->  proxy render
            ->  full render  ->  qc  ->  deliverables

Every command is non-interactive. Triage communicates through the exit code and a
JSON/markdown briefing on stdout; the agent driving this toolkit shows it and waits.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .contract import (
    EXIT_MISSING_DEP,
    EXIT_NEEDS_INPUT,
    EXIT_OK,
    EXIT_QC_FAIL,
    EXIT_RENDER_FAIL,
    EXIT_USAGE,
    SCHEMA,
    SAMPLE_FPS_FACE,
    SAMPLE_FPS_VIDEO,
    VERSION,
    MissingDependency,
    QCFailure,
    RenderFailure,
    TriageRequired,
    VxError,
)
from . import ffmpeg as ff
from . import lock as lock_mod
from . import winpath


# --------------------------------------------------------------------------- #
# io helpers
# --------------------------------------------------------------------------- #


#: Quando il narratore parla, le righe tecniche di `auto` tacciono: una voce
#: sola, altrimenti la narrazione si spezza in mezzo.
_NARRATING = False


def _log(msg: str) -> None:
    if _NARRATING and msg.startswith("[auto]"):
        return
    print(msg, file=sys.stderr, flush=True)


#: ``--json`` means the document is the only thing on stdout: progress lines are
#: not a document and must not corrupt it.
_JSON_MODE = False


def _say(msg: str) -> None:
    """Racconta un passo su stdout, mai sotto ``--json``.

    ``vx ingest`` usava ``stderr`` anche per le righe normali: con
    ``2>&1 | Select-Object`` in PowerShell ogni riga diventava un ErrorRecord e
    un comando **riuscito** chiudeva con 1. Il progresso e' output del comando,
    quindi va su stdout; lo ``stderr`` resta per gli errori veri.
    """
    if _JSON_MODE:
        return
    print(msg, flush=True)


def _out(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


# --------------------------------------------------------------------------- #
# session registry helpers
# --------------------------------------------------------------------------- #


def _session_argv() -> List[str]:
    """L'argv che la sessione ricorda: corto, e gia' tutto stringa."""
    return [str(item) for item in sys.argv[1:]][:40]


def _session_of(args: Any):
    """La sessione aperta dal decoratore, senza farne un obbligo."""
    return getattr(args, "_vx_session", None)


def _session_command(kind: str):
    """Apre la sessione prima del comando e la chiude su ogni uscita.

    La chiusura sta qui e non nei singoli ``return``: un comando che esce 3 per
    il triage, un'eccezione o un Ctrl+C lasciano comunque ``end`` nel registro.
    """

    def decorate(func):
        @functools.wraps(func)
        def wrapped(args):
            from . import session as session_mod

            session = session_mod.begin(
                kind, _session_argv(), os.getcwd()
            )
            setattr(args, "_vx_session", session)
            try:
                result = func(args)
            except BaseException as exc:  # noqa: BLE001 - re-raised unchanged
                session.error(f"{type(exc).__name__}: {exc}")
                session.end("failed")
                raise
            if result in (EXIT_OK, None):
                session.end("done")
            elif int(result) == EXIT_NEEDS_INPUT:
                session.end("incomplete")
            else:
                session.end("failed")
            return result

        return wrapped

    return decorate


def _session_artifact(session: Any, kind: str, path: Any) -> None:
    if session is None or path is None:
        return
    session.artifact(kind, str(path))


def _session_auto(session: Any, report: Any, panel: Any = None) -> None:
    """Racconta un giro di ``auto``: journal, decisioni, cancelli, consegne."""
    if session is None or not isinstance(report, dict):
        return
    workdir = str(report.get("workdir") or "")
    if workdir:
        session.set_job(workdir)
    for item in report.get("decisions") or []:
        if isinstance(item, dict):
            session.decision(
                str(item.get("id") or ""),
                item.get("chosen") or "",
                by=str(item.get("by") or ""),
                confidence=str(item.get("confidence") or ""),
            )
    font = report.get("font") if isinstance(report.get("font"), dict) else {}
    if font.get("candidates"):
        session.gate("font", open=True, detail="proposta pronta, scelta in attesa")
    session.gate(
        "font",
        open=False,
        chosen=str(font.get("chosen") or ""),
        by=str(font.get("selected_by") or ""),
    )
    keys = (
        report.get("keyframes_gate")
        if isinstance(report.get("keyframes_gate"), dict)
        else {}
    )
    if keys.get("required"):
        session.gate("keyframes", open=True, detail="fotogrammi da approvare")
    session.gate(
        "keyframes",
        open=False,
        approved=bool(keys.get("approved")),
        by=str(keys.get("selected_by") or ""),
    )
    plan = (
        report.get("plan_choice")
        if isinstance(report.get("plan_choice"), dict)
        else {}
    )
    if plan.get("asked"):
        session.gate("plan", open=True, detail="scelta full/fast in attesa")
    session.gate(
        "plan",
        open=False,
        chosen=str(plan.get("chosen") or ""),
        by=str(plan.get("selected_by") or ""),
    )
    _session_artifact(session, "master", report.get("master"))
    if workdir:
        _session_artifact(session, "report", Path(workdir) / "auto_report.json")
    seen = {
        str(row.get("stage") or "")
        for row in session.events
        if str(row.get("type") or "") == "stage"
    }
    timings = report.get("timings") if isinstance(report.get("timings"), dict) else {}
    for name, seconds in timings.items():
        if name == "total" or name in seen:
            continue
        try:
            detail = f"{float(seconds):.1f}s"
        except (TypeError, ValueError):
            detail = ""
        session.stage(str(name), None, detail)
    for item in report.get("deliverables") or []:
        if isinstance(item, dict):
            _session_artifact(
                session, str(item.get("kind") or "deliverable"), item.get("path")
            )
    for row in getattr(panel, "journal", []) or []:
        if isinstance(row, dict):
            session.journal(row.get("line"), phase=str(row.get("phase") or ""))
    if workdir:
        _session_live(session, workdir)


def _session_live(session: Any, workdir: Any) -> None:
    """Pubblica nel registro i pezzi del manifest live, dovunque stia ``live/``.

    La vista non deve leggere una cartella fissa: gli eventi portano il percorso
    vero del manifest e dei singoli chunk, e lo snapshot tiene ``ready/total``.
    """
    if session is None:
        return
    try:
        folder = Path(workdir) / "live"
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001 - nessun manifest live non e' un errore
        return
    if not isinstance(manifest, dict):
        return
    raw = manifest.get("chunks")
    chunks = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    for item in chunks:
        try:
            number = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        path = folder / f"chunk_{number:03d}.mp4"
        session.artifact(
            "chunk",
            str(path),
            bytes=(path.stat().st_size if path.is_file() else None),
            index=number,
            t_in=item.get("t_in"),
            t_out=item.get("t_out"),
            ready=bool(item.get("ready")),
        )
    ready = sum(1 for item in chunks if item.get("ready"))
    try:
        total = int(manifest.get("total") or len(chunks))
    except (TypeError, ValueError):
        total = len(chunks)
    session.live(ready, total, str(folder), chunks=chunks)


def _load_json(path: str | Path) -> Dict[str, Any]:
    try:
        raw = Path(path).read_bytes()
        text = (
            raw.decode("utf-16")
            if raw[:2] in (b"\xff\xfe", b"\xfe\xff")
            else raw.decode("utf-8-sig")
        )
    except (OSError, UnicodeDecodeError) as exc:
        raise VxError(f"{path}: unreadable JSON ({exc})") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VxError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise VxError(f"{path}: expected a JSON object")
    return data


def _load_brief(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    brief = _load_json(path)
    brief["_path"] = str(Path(path).resolve())
    return brief


def _load_plan(path: str | Path):
    from .contract import EditPlan

    data = _load_json(path)
    known = set(EditPlan.__dataclass_fields__)
    return EditPlan(**{k: v for k, v in data.items() if k in known})


def _visual_from_brief(brief: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The renderer's ``visual`` dict from a ``vx.visual/1.0`` brief.

    ``build_visual_brief`` stores the full decision objects under ``_visual``;
    the renderer only needs the per-segment chains, so the single decisions are
    repeated for every segment (``i % len`` with one entry).
    """
    vis = brief.get("_visual") or {}
    if not isinstance(vis, dict) or not vis:
        return None
    return {
        "grade_nodes": [vis.get("grade_node") or {"type": "none"}],
        "animations": [vis.get("animation") or {"name": "none"}],
    }


def _corpus_hash() -> str:
    """The corpus hash the thresholds carry, or a clear failure."""
    from .registry import THRESHOLDS

    for entry in THRESHOLDS.values():
        provenance = str(entry.get("provenance") or "")
        if provenance.startswith("corpus_hash:"):
            return provenance.split(":", 1)[1]
    raise VxError(
        "soglie non misurate: serve tests/fixtures/corpus_stats.json per il corpus_hash"
    )


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cut_density_choice(measurements: Dict[str, Any]) -> Dict[str, Any]:
    """The cut-density choice from ``onsets_per_s`` and the corpus thresholds.

    Bands from ``registry.THRESHOLDS["cut_density"]``: under p33 -> 3.0 s,
    between p33 and p67 -> 2.0 s, over p67 -> 1.2 s. The confidence mirrors
    ``animplan``: 0.5 on the cut point, rising on a tenth of the band width.
    """
    from .registry import THRESHOLDS

    entry = THRESHOLDS.get("cut_density") or {}
    p33, p67 = entry.get("p33"), entry.get("p67")
    provenance = entry.get("provenance") or "unmeasured"
    onsets = measurements.get("onsets_per_s")
    if onsets is None or p33 is None or p67 is None:
        return {
            "value": 2.0,
            "driver_value": None,
            "threshold": {"p33": p33, "p67": p67},
            "provenance": provenance,
            "inaction_reason": (
                "onsets_per_s non misurato: densita' di default 2.0 s, inazione."
            ),
            "confidence": 0.5,
            "applied": False,
        }
    onsets = float(onsets)
    p33, p67 = float(p33), float(p67)
    if onsets < p33:
        value, distance = 3.0, p33 - onsets
    elif onsets <= p67:
        value, distance = 2.0, min(onsets - p33, p67 - onsets)
    else:
        value, distance = 1.2, onsets - p67
    spread = p67 - p33
    span = 0.1 * spread if spread > 0.0 else 0.0
    confidence = 0.5 + 0.5 * min(1.0, distance / span) if span > 0.0 else 1.0
    uncertain = 0.2 <= confidence <= 0.8
    if uncertain:
        reason = (
            f"confidenza {confidence:.3f} in [0.2, 0.8]: inazione, densita' di "
            "default 2.0 s."
        )
        value = 2.0
    elif onsets < p33:
        reason = f"onsets_per_s {onsets:.4f} < p33 {p33:.4f}: taglio largo 3.0 s."
    elif onsets <= p67:
        reason = (
            f"p33 {p33:.4f} <= onsets_per_s {onsets:.4f} <= p67 {p67:.4f}: "
            "taglio 2.0 s."
        )
    else:
        reason = f"onsets_per_s {onsets:.4f} > p67 {p67:.4f}: taglio stretto 1.2 s."
    return {
        "value": value,
        "driver_value": onsets,
        "threshold": {"p33": p33, "p67": p67},
        "provenance": provenance,
        "inaction_reason": reason,
        "confidence": round(float(confidence), 4),
        "applied": not uncertain,
    }


def _typography_choice(measurements: Dict[str, Any]) -> Dict[str, Any]:
    """The typography choice from the ink coverage measured on a render.

    Under 0.45% the body grows by 8 px, over 0.94% it shrinks by 8 px, in the
    band it stays. An unmeasured ink (source clips) is inaction at 0.5: nothing
    is known, nothing is changed.
    """
    ink = measurements.get("ink_coverage_pct")
    if ink is None:
        return {
            "value": 0,
            "driver_value": None,
            "threshold": {"low": 0.45, "high": 0.94},
            "provenance": "measured:delivery",
            "inaction_reason": (
                "ink_coverage_pct non misurata: corpo invariato, inazione."
            ),
            "confidence": 0.5,
            "applied": False,
        }
    ink = float(ink)
    if ink < 0.45:
        value, distance = 8, 0.45 - ink
        reason = f"ink {ink:.4f}% < 0.45%: corpo +8 px."
    elif ink > 0.94:
        value, distance = -8, ink - 0.94
        reason = f"ink {ink:.4f}% > 0.94%: corpo -8 px."
    else:
        value, distance = 0, min(ink - 0.45, 0.94 - ink)
        reason = f"ink {ink:.4f}% nella banda 0.45-0.94%: corpo invariato."
    confidence = min(1.0, distance / 0.12)
    uncertain = 0.2 <= confidence <= 0.8
    if uncertain:
        reason = (
            f"confidenza {confidence:.3f} in [0.2, 0.8]: inazione, corpo invariato."
        )
        value = 0
    return {
        "value": value,
        "driver_value": ink,
        "threshold": {"low": 0.45, "high": 0.94},
        "provenance": "measured:delivery",
        "inaction_reason": reason,
        "confidence": round(float(confidence), 4),
        "applied": not uncertain,
    }


def _median(values: List[float]) -> Optional[float]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _aggregate_composition(plans: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One composition decision from the whole-clip ``screen.plan`` samples.

    Framing is a project-level decision: the offset is the median of the
    sampled offsets that actually found a face, and the crop box is the
    placement whose offset is closest to that median. At least two of the five
    samples must carry a face; otherwise the clip is centred. The threshold and
    the uncertainty band are the ones ``screen`` declares: this function
    aggregates, it does not invent a second rule.
    """
    from . import screen as screen_mod

    offsets = [
        plan["composition"]["value"]
        for plan in plans
        if plan.get("face_found")
        and isinstance(plan.get("composition"), dict)
        and plan["composition"].get("value") is not None
    ]
    samples = {
        "n": len(plans),
        "with_face": sum(1 for plan in plans if plan.get("face_found")),
        "median": None,
        "rule": "declared:at_least_2_of_5",
    }

    if len(offsets) < 2:
        return {
            "name": "centre",
            "crop": {},
            "driver": screen_mod.DRIVER,
            "value": None,
            "provenance": screen_mod.PROVENANCE,
            "inaction": True,
            "inaction_reason": (
                "meno di due volti nella clip: inquadratura al centro, nessuna scelta."
            ),
            "confidence": screen_mod.NO_FACE_CONFIDENCE,
            "applied": False,
            "samples": samples,
        }

    median_x = _median([float(item["x"]) for item in offsets])
    median_y = _median([float(item["y"]) for item in offsets])
    samples["median"] = {"x": round(median_x, 4), "y": round(median_y, 4)}
    magnitude = max(abs(median_x), abs(median_y))
    confidence = min(1.0, max(0.0, magnitude / screen_mod.OFFSET_THRESHOLD))
    if screen_mod.UNCERTAIN_LOW <= confidence <= screen_mod.UNCERTAIN_HIGH:
        name, applied = "centre", False
        reason = (
            f"offset mediano {magnitude:.4f} vicino alla soglia "
            f"{screen_mod.OFFSET_THRESHOLD}: inazione, inquadratura al centro."
        )
    elif magnitude >= screen_mod.OFFSET_THRESHOLD:
        name, applied = "offset", True
        reason = (
            f"offset mediano {magnitude:.4f} oltre la soglia "
            f"{screen_mod.OFFSET_THRESHOLD}: il crop segue il soggetto."
        )
    else:
        name, applied = "centre", True
        reason = (
            f"offset mediano {magnitude:.4f} sotto la soglia "
            f"{screen_mod.OFFSET_THRESHOLD}: il soggetto e' al centro."
        )

    closest = min(
        (
            plan
            for plan in plans
            if plan.get("face_found")
            and isinstance(plan.get("composition"), dict)
            and plan["composition"].get("value") is not None
        ),
        key=lambda plan: (
            abs(float(plan["composition"]["value"]["x"]) - median_x)
            + abs(float(plan["composition"]["value"]["y"]) - median_y)
        ),
    )
    crop = dict((closest.get("composition") or {}).get("crop") or {})
    return {
        "name": name,
        "crop": crop,
        "driver": screen_mod.DRIVER,
        "value": {"x": round(median_x, 4), "y": round(median_y, 4)},
        "provenance": screen_mod.PROVENANCE,
        "inaction": not applied,
        "inaction_reason": reason,
        "confidence": round(float(confidence), 4),
        "applied": applied,
        "samples": samples,
    }


def _downgrade_no_filter(
    choice: Dict[str, Any],
    reason: str,
    *,
    value: Any = None,
    note: Optional[str] = None,
    reason_field: str = "inaction_reason",
) -> None:
    """Record an inaction when the decision has no executable filter."""
    choice["applied"] = False
    choice["confidence"] = 0.5
    choice[reason_field] = reason
    if reason_field != "inaction":
        choice["inaction"] = True
    if value is not None:
        choice["value"] = value
    if note is not None:
        choice["note"] = note


def build_visual_brief(
    source: str | Path,
    measurements: Dict[str, Any],
    *,
    delivery: str | Path | None = None,
    project: str | None = None,
) -> Dict[str, Any]:
    """Build a ``vx.visual/1.0`` brief from the measured clip.

    The six choices come from the decision modules (``palette``, ``gradeplan``,
    ``animplan``, ``screen`` over five whole-clip timestamps) plus the
    cut-density and typography rules. Two normalisations happen here, by
    contract:

    * P3 — ``analyze.measure_color`` returns the cast in pixel units; any cast
      whose absolute value exceeds 1.0 is divided by 255 before it reaches
      ``gradeplan``, and ``cast_normalised`` is recorded on the brief.
    * P4 — ``gradeplan``/``animplan`` return ``inaction`` as a sentence; the
      brief stores the boolean (``not applied``) and the sentence goes to
      ``inaction_reason``.

    A third normalisation is the wave's invariant: an applied choice must point
    at a filter that actually reaches the render. Palette and typography have no
    filter in this wave, grade and animation must produce a non-empty chain, and
    composition must be an offset crop that differs from the centred default.
    Anything else is downgraded to inaction, with the original decision kept in
    ``note``.

    ``executed`` is left empty: the caller fills it from the real render. The
    full decision objects are kept under ``_visual`` so the renderer can splice
    the chains without re-deciding.
    """
    from . import animplan, brief as brief_mod, gradeplan
    from . import palette as palette_mod
    from . import probe as probe_mod
    from . import screen as screen_mod
    from .registry import THRESHOLDS

    src = Path(source)
    if not src.is_file():
        raise VxError(f"sorgente mancante: {src}")
    m = dict(measurements or {})

    cast_normalised = False
    for key in ("cast_rg", "cast_by"):
        value = m.get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and abs(float(value)) > 1.0
        ):
            m[key] = float(value) / 255.0
            cast_normalised = True

    duration = m.get("duration_s")
    width = m.get("width")
    height = m.get("height")
    fps = m.get("fps")
    if duration is None or not width or not height or fps is None:
        info = probe_mod.probe(str(src))
        duration = float(info.duration) if duration is None else float(duration)
        width = int(info.width) if not width else int(width)
        height = int(info.height) if not height else int(height)
        fps = float(info.fps) if fps is None else float(fps)

    pal = palette_mod.decide(m)
    grade = gradeplan.decide(m, pal)
    ratio = m.get("transient_ratio")
    anim = animplan.decide(
        None if ratio is None else float(ratio), m.get("onsets_per_s")
    )
    stamps = [float(duration) * (index + 0.5) / 5.0 for index in range(5)]
    comp = _aggregate_composition(
        screen_mod.plan(str(src), stamps, int(width), int(height))
    )
    cut = _cut_density_choice(m)
    typo = _typography_choice(m)

    if pal["applied"]:
        original_family = pal["family"]
        pal["family"] = "neutral"
        pal["value"] = "neutral"
        pal["note"] = (
            f"{pal.get('note', '')}; famiglia originale {original_family}"
        ).strip("; ")
        _downgrade_no_filter(
            pal,
            "decisione presa ma nessun filtro eseguibile in questa ondata: "
            "inazione registrata, famiglia neutra",
            value="neutral",
        )

    if grade["applied"] and not gradeplan.chain_for_shot(grade["node"]):
        original_node = (grade.get("node") or {}).get("type")
        grade["node"] = {"type": "none"}
        grade["value"] = "none"
        grade["note"] = f"nodo originale {original_node}"
        _downgrade_no_filter(
            grade,
            "decisione presa ma nessun filtro eseguibile in questa ondata: "
            "inazione registrata, nessun nodo cromatico",
            value="none",
            reason_field="inaction",
        )

    shot = {
        "duration": float(duration),
        "width": int(width),
        "height": int(height),
        "fps": float(fps),
    }
    if anim["applied"] and not animplan.chain_for_shot(anim, shot):
        original_anim = anim["name"]
        anim["name"] = "none"
        anim["count"] = 0
        anim["value"] = "none"
        anim["note"] = f"animazione originale {original_anim}"
        _downgrade_no_filter(
            anim,
            "decisione presa ma nessun filtro eseguibile in questa ondata: "
            "inazione registrata, nessuna animazione",
            value="none",
            reason_field="inaction",
        )

    crop = comp.get("crop") or {}
    crop_w = int(crop.get("crop_w") or 0)
    crop_h = int(crop.get("crop_h") or 0)
    centre_x = (int(width) - crop_w) // 2 if crop_w else None
    centre_y = (int(height) - crop_h) // 2 if crop_h else None
    crop_differs = bool(
        crop_w
        and crop_h
        and (
            int(crop.get("x", centre_x)) != centre_x
            or int(crop.get("y", centre_y)) != centre_y
        )
    )
    if comp["applied"] and not (comp["name"] == "offset" and crop_differs):
        original_comp = comp["name"]
        comp["value"] = "centre"
        comp["note"] = f"inquadratura originale {original_comp}"
        _downgrade_no_filter(
            comp,
            "decisione presa ma nessun filtro eseguibile in questa ondata: "
            "inazione registrata, inquadratura al centro",
            value="centre",
        )

    if typo["applied"]:
        original_typo = typo["value"]
        typo["note"] = f"valore originale {original_typo}"
        _downgrade_no_filter(
            typo,
            "decisione presa ma nessun filtro eseguibile in questa ondata: "
            "inazione registrata, corpo invariato",
            value=0,
        )

    anim_th = THRESHOLDS.get("animation") or {}
    brief = brief_mod.new_brief(project or src.stem, _sha256_file(src), _corpus_hash())
    brief["measurements"] = {
        "transient_ratio": m.get("transient_ratio"),
        "transient_p33": anim_th.get("p33"),
        "transient_p67": anim_th.get("p67"),
        "hue_mass_top3": m.get("hue_mass_top3") or [],
        "ink_coverage_pct": m.get("ink_coverage_pct"),
        "subject_offset": m.get("subject_offset"),
        "onsets_per_s": m.get("onsets_per_s"),
        "first_speech_onset_s": m.get("first_speech_onset_s"),
        "first3s_motion": m.get("first3s_motion"),
        "face_present": bool(m.get("face_present")),
        "luma_p50": m.get("luma_p50"),
        "contrast": m.get("contrast"),
    }
    brief["cast_normalised"] = cast_normalised
    if delivery is not None:
        brief["delivery"] = str(delivery)
    brief["_visual"] = {
        "grade_node": grade["node"],
        "animation": anim,
        "palette": pal,
        "composition": comp,
    }

    choices = brief["choices"]
    choices["palette"].update(
        driver=pal["driver"],
        value=pal["family"],
        provenance=pal["provenance"],
        inaction=bool(pal["inaction"]),
        inaction_reason=pal["inaction_reason"],
        confidence=pal["confidence"],
        applied=pal["applied"],
    )
    choices["grade"].update(
        driver=grade["driver"],
        value=grade["node"]["type"],
        provenance=grade["provenance"],
        inaction=not grade["applied"],
        inaction_reason=grade["inaction"],
        confidence=grade["confidence"],
        applied=grade["applied"],
    )
    choices["animation"].update(
        driver=anim["driver"],
        value=anim["name"],
        name=anim["name"],
        count=anim["count"],
        direction_basis="asserted",
        provenance=anim["provenance"],
        inaction=not anim["applied"],
        inaction_reason=anim["inaction"],
        confidence=anim["confidence"],
        applied=anim["applied"],
    )
    choices["cut_density"].update(
        driver="onsets_per_s",
        value=cut["value"],
        provenance=cut["provenance"],
        inaction=not cut["applied"],
        inaction_reason=cut["inaction_reason"],
        confidence=cut["confidence"],
        applied=cut["applied"],
    )
    choices["composition"].update(
        driver=comp["driver"],
        value=comp["value"] if comp["value"] is not None else "centre",
        provenance=comp["provenance"],
        inaction=bool(comp["inaction"]),
        inaction_reason=comp["inaction_reason"],
        confidence=comp["confidence"],
        applied=comp["applied"],
    )
    choices["typography"].update(
        driver="ink_coverage_pct",
        value=typo["value"],
        provenance=typo["provenance"],
        inaction=not typo["applied"],
        inaction_reason=typo["inaction_reason"],
        confidence=typo["confidence"],
        applied=typo["applied"],
    )

    brief_mod.add_reason(
        brief,
        "palette",
        pal["driver"],
        pal["value"],
        {
            "p33": (THRESHOLDS.get("palette") or {}).get("p33"),
            "p67": (THRESHOLDS.get("palette") or {}).get("p67"),
        },
        pal["provenance"],
        pal["inaction_reason"],
    )
    brief_mod.add_reason(
        brief,
        "grade",
        grade["driver"],
        grade["value"],
        {
            "p33": (THRESHOLDS.get("grade") or {}).get("p33"),
            "p67": (THRESHOLDS.get("grade") or {}).get("p67"),
        },
        grade["provenance"],
        grade["inaction"],
    )
    brief_mod.add_reason(
        brief,
        "animation",
        anim["driver"],
        anim["value"],
        {"p33": anim_th.get("p33"), "p67": anim_th.get("p67")},
        anim["provenance"],
        anim["inaction"],
        direction_basis="asserted",
    )
    brief_mod.add_reason(
        brief,
        "cut_density",
        "onsets_per_s",
        cut["driver_value"],
        cut["threshold"],
        cut["provenance"],
        cut["inaction_reason"],
    )
    brief_mod.add_reason(
        brief,
        "composition",
        comp["driver"],
        comp["value"],
        screen_mod.OFFSET_THRESHOLD,
        comp["provenance"],
        comp["inaction_reason"],
    )
    brief_mod.add_reason(
        brief,
        "typography",
        "ink_coverage_pct",
        typo["driver_value"],
        typo["threshold"],
        typo["provenance"],
        typo["inaction_reason"],
    )
    return brief


def cmd_visual(args: argparse.Namespace) -> int:
    measurements = _load_json(args.measurements)
    brief = build_visual_brief(
        args.source, measurements, delivery=args.delivery, project=args.project
    )
    if args.out:
        out = Path(args.out)
    else:
        src = Path(args.source)
        out = src.parent / f"{src.stem}.visual.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    from . import brief as brief_mod

    out.write_text(brief_mod.to_json(brief), encoding="utf-8")
    print(str(out))
    return EXIT_OK


def _enabled(plan, name: str) -> bool:
    for item in plan.treatments or []:
        if item.get("name") == name:
            return bool(item.get("enabled"))
    return False


def _params(plan, name: str) -> Dict[str, Any]:
    for item in plan.treatments or []:
        if item.get("name") == name:
            return item.get("params") or {}
    return {}


def _tool_block() -> Dict[str, Any]:
    return {
        "name": "video-editor",
        "version": VERSION,
        "ffmpeg": ff.version(),
        "python": sys.version.split()[0],
        "schema": SCHEMA,
    }


# --------------------------------------------------------------------------- #
# commands — inspection
# --------------------------------------------------------------------------- #


@_session_command("doctor")
def cmd_doctor(args: argparse.Namespace) -> int:
    from . import doctor

    session = _session_of(args)
    if session is not None:
        session.stage("scan", 0.0, "Scansione dell'ambiente")
    rep = doctor.scan()
    if session is not None:
        session.stage(
            "scan",
            100.0,
            f"Verdetto: {rep.get('verdict') or 'n/d'}",
        )
    if args.json:
        _out(rep)
    else:
        print(doctor.to_text(rep))
    return EXIT_OK if rep.get("verdict") != "broken" else EXIT_MISSING_DEP


def cmd_capabilities(args: argparse.Namespace) -> int:
    """Le 25 capability di ffmpeg, il verdetto dell'ambiente e cosa si perde.

    Esce 4 quando manca una dipendenza obbligatoria (ffmpeg o libx264: la
    stessa legge che rende ``doctor`` "broken"), 0 altrimenti. Non installa e
    non modifica niente: legge e riferisce.
    """
    from . import doctor

    report = doctor.capabilities_report()
    if getattr(args, "json", False):
        _out(report)
    else:
        print(doctor.capabilities_text(report))
    return int(report.get("exit_code") or EXIT_OK)


def cmd_probe(args: argparse.Namespace) -> int:
    from . import probe as probe_mod
    from . import report as report_io

    result = probe_mod.probe(args.input, count_frames=args.count_frames).to_dict()
    if args.report:
        rep = report_io.merge(report_io.read(args.report), "source", result)
        report_io.write(rep, args.report)
    _out(result)
    return EXIT_OK


def cmd_proxy(args: argparse.Namespace) -> int:
    from . import proxy as proxy_mod

    src = Path(args.input)
    work = Path(args.workdir) if args.workdir else winpath.make_workdir(str(src))
    dst = Path(args.out) if args.out else work / "proxy" / "proxy.mp4"
    _log(f"[proxy] {src.name} -> {dst} @ {args.width}px")
    result = proxy_mod.make_proxy(str(src), dst, width=args.width, workdir=work)
    if not args.no_previews:
        thumbs = proxy_mod.thumbs(str(src), work / "analysis", count=args.thumbs)
        result["thumbs"] = thumbs
        result["contact_sheet"] = proxy_mod.contact_sheet(
            thumbs, work / "analysis" / "contact.jpg"
        )
    _out(result)
    return EXIT_OK


def cmd_analyze(args: argparse.Namespace) -> int:
    from . import analyze, audio, faces
    from . import probe as probe_mod
    from . import report as report_io

    src = Path(args.input)
    probe_data = probe_mod.probe(str(src)).to_dict()
    _log("[1/3] video: jitter, esposizione, colore, freeze, tagli, ritmo")
    video = analyze.analyze_video(
        str(src), sample_fps=args.sample_fps, proxy_path=args.proxy or None
    )
    _log("[2/3] audio: noise floor, dead air, loudness, clipping")
    aud = audio.analyze_audio(str(src))
    _log("[3/3] volti e speaker")
    face_src = args.proxy or str(src)
    fac = faces.detect_faces_tracked(face_src, sample_fps=SAMPLE_FPS_FACE)
    if args.proxy:
        proxy_probe = probe_mod.probe(face_src).to_dict()
        fac = faces.rescale_tracks(
            fac,
            int(proxy_probe.get("width") or 0),
            int(proxy_probe.get("height") or 0),
            int(probe_data.get("width") or 0),
            int(probe_data.get("height") or 0),
        )

    payload = {
        "video": video.to_dict(),
        "audio": aud.to_dict(),
        "faces": fac.to_dict(),
    }
    if args.speech:
        payload["speech"], _ = _transcribe(str(src), args.model, args.lang)

    if args.report:
        rep = report_io.read(args.report)
        report_io.merge(rep, "analysis", payload)
        report_io.write(rep, args.report)
        _log(f"[ok] report aggiornato: {args.report}")
    _out({"source": probe_data, "analysis": payload})
    return EXIT_OK


def _speech_block(transcript, model: str = "") -> Dict[str, Any]:
    """Report-safe speech stats: counts and reasons, never the word list."""
    from . import subs

    if transcript is None:
        return {"skipped_reason": "no transcript"}
    stats = subs.transcript_stats(transcript)
    return {
        "language": transcript.language or "",
        "word_count": int(stats.get("word_count") or 0),
        "wpm": float(stats.get("wpm") or 0.0),
        "fillers": int(stats.get("fillers") or 0),
        "model": transcript.model or model,
        "skipped_reason": transcript.skipped_reason or "",
    }


def _vad_config() -> Dict[str, Any]:
    from . import settings

    cfg = settings.current().get("audio") or {}
    return {
        "threshold": float(cfg.get("vad_threshold", 0.5)),
        "min_speech": float(cfg.get("vad_min_speech", 0.25)),
        "min_silence": float(cfg.get("vad_min_silence", 0.35)),
    }


def _analysis_chunk_sec(args=None) -> float:
    value = getattr(args, "chunk_sec", None) if args is not None else None
    if value is not None:
        try:
            return max(1.0, float(value))
        except (TypeError, ValueError):
            pass
    from . import settings

    try:
        return float(settings.get("analysis.chunk_sec", 300.0))
    except (TypeError, ValueError):
        return 300.0


def _chunked_report(state: Dict[str, Any]) -> Dict[str, Any]:
    stats = dict(state.get("stats") or {})
    vad = state.get("vad")
    regions = len(getattr(vad, "regions", []) or [])
    return {
        "stats": stats,
        "vad": {
            "engine": str(getattr(vad, "engine", "none") or "none"),
            "available": bool(getattr(vad, "available", False)),
            "speech_ratio": float(getattr(vad, "speech_ratio", 0.0) or 0.0),
            "snr_db": (float(getattr(vad, "snr_db", 0.0) or 0.0) if regions else None),
            "regions": regions,
            "reason": str(getattr(vad, "reason", "") or ""),
        },
    }


def _analyze_speech(
    src: str,
    *,
    workdir: Path,
    source_sha256: str,
    analysis_sha256: str,
    media: Dict[str, Any],
    model: str,
    language: Optional[str],
    transcribe: bool,
    chunk_sec: Optional[float] = None,
) -> tuple[Dict[str, Any], Any, Optional[Dict[str, Any]]]:
    """Speech stats, transcript and chunked state; short clips keep the old path."""
    from . import analyze

    if not media.get("has_audio"):
        return {"skipped_reason": "no audio"}, None, None
    duration = float(media.get("duration") or 0.0)
    window = float(chunk_sec) if chunk_sec is not None else _analysis_chunk_sec()
    if not analyze.should_chunk(duration, True, chunk_sec=window):
        if not transcribe:
            return {"skipped_reason": "disabled"}, None, None
        block, transcript = _transcribe(src, model, language)
        return block, transcript, None

    state = analyze.analyze_chunked(
        src,
        workdir=workdir,
        source_sha256=source_sha256,
        analysis_sha256=analysis_sha256,
        chunk_sec=window,
        model=model,
        language=language,
        vad_config=_vad_config(),
        transcribe=transcribe,
    )
    stats = state.get("stats") or {}
    _log(
        "[chunk] {total} blocchi: {cached} da cache, {computed} calcolati, "
        "{failed} falliti".format(
            total=stats.get("chunks_total", 0),
            cached=stats.get("chunks_cached", 0),
            computed=stats.get("chunks_computed", 0),
            failed=stats.get("chunk_failures", 0),
        )
    )
    if not transcribe:
        return {"skipped_reason": "disabled"}, None, state
    transcript = state.get("transcript")
    return _speech_block(transcript, model), transcript, state


def _transcribe(
    src: str, model: str = "tiny", lang: Optional[str] = None
) -> tuple[Dict[str, Any], Any]:
    """Speech stats for the report plus the transcript object, so `run` never transcribes twice.

    A failed transcription is data too, never a fatal error.
    """
    try:
        from . import subs

        tr = subs.transcribe(src, model=model, language=lang)
        return _speech_block(tr, model), tr
    except Exception as exc:  # noqa: BLE001 - a missing model must not kill the run
        _log(f"[warn] trascrizione non disponibile: {exc}")
        return {"skipped_reason": str(exc)}, None


# --------------------------------------------------------------------------- #
# commands — subtitles
# --------------------------------------------------------------------------- #


def _layout_zone(out_w: int, out_h: int, overlay_paths: Optional[Sequence[str]]):
    """Misura l'ingombro grafico, oppure None se nessun overlay e' dichiarato."""
    from . import layout as layout_mod  # noqa: PLC0415

    overlays = {Path(p).stem: p for p in (overlay_paths or [])}
    if not overlays:
        return None
    return layout_mod.build(int(out_w), int(out_h), overlays)


def _layout_gate(zone, ass_path: str) -> Dict[str, Any]:
    """Cancello 4 sul box dichiarato. Solleva LayoutFailure (uscita 6) se collide."""
    from . import gates  # noqa: PLC0415
    from . import subs  # noqa: PLC0415

    declared = subs.declared_box(ass_path)
    box = zone.text_box(
        lines=declared["lines"],
        font_px=declared["font_px"],
        line_px=declared["line_px"],
        outline_px=declared["outline_px"],
    )
    return gates.layout(zone, box).to_dict()


def _maybe_json(path: Path) -> Dict[str, Any]:
    """A JSON object, or ``{}``: a missing or broken file is not an error here."""
    try:
        return _load_json(path)
    except VxError:
        return {}


def _font_choice(work: Path) -> Dict[str, Any]:
    """La famiglia scelta per il lavoro, dalla UI o dal ``chosen`` della proposta.

    La scelta esplicita (``font_choice.json``) vince. Senza di lei vale il
    ``chosen`` di ``font_proposal.json``, ma solo se e' davvero un candidato:
    un nome che la proposta non contiene non diventa un ``Fontname``. Nessuna
    delle due: ``{}`` e il render resta sul preset, come prima.
    """
    choice = _maybe_json(Path(work) / "font_choice.json")
    proposal = _maybe_json(Path(work) / "font_proposal.json")
    raw = proposal.get("candidates")
    by_family = {
        str(item.get("family") or ""): item
        for item in (raw if isinstance(raw, list) else [])
        if isinstance(item, dict)
    }
    family = str(choice.get("family") or "").strip()
    if family not in by_family:
        proposed = str(proposal.get("chosen") or "").strip()
        family = proposed if proposed in by_family else ""
    if not family:
        return {}
    return {"family": family, "record": by_family[family]}


def _job_font(work: Path) -> Dict[str, Any]:
    """Porta il TTF scelto in ``<workdir>/fonts`` e dice dove puntare libass.

    Ritorna ``{"family", "fonts_dir", "note"}``: famiglia vuota significa che
    si resta sul preset, e la nota dice perche' (file mancante o copia fallita)
    invece di lasciar credere che il font scelto sia stato usato.
    """
    picked = _font_choice(work)
    if not picked:
        return {"family": "", "fonts_dir": "", "note": ""}
    family = picked["family"]
    source = Path(str(picked["record"].get("file") or ""))
    if not source.is_file():
        return {
            "family": "",
            "fonts_dir": "",
            "note": f"{family}: file {source} mancante, il render resta sul preset",
        }
    from . import fontlib  # noqa: PLC0415 -- only the font path needs it

    try:
        record = fontlib.FontRecord(
            family=family,
            licence=str(picked["record"].get("licence") or "unknown"),
            file=str(source),
            sha256="",
        )
        fonts_dir = fontlib.job_fonts_dir(Path(work), [record])
    except Exception as exc:  # noqa: BLE001 - a copy that fails is a declared fallback
        return {
            "family": "",
            "fonts_dir": "",
            "note": f"{family}: copia in <workdir>/fonts non riuscita ({exc}), "
                    "il render resta sul preset",
        }
    return {"family": family, "fonts_dir": str(fonts_dir), "note": ""}


def _use_job_fonts(fonts_dir: str) -> None:
    """Fa puntare libass alla cartella font del lavoro, non solo al bundle.

    ``render`` e ``qc`` costruiscono ``subtitles=...:fontsdir=`` chiamando
    ``vx.fonts.fontsdir_arg()``; quella cartella contiene gia' il bundle piu' il
    font scelto (``fontlib.job_fonts_dir``), quindi la stessa chiamata basta a
    far comparire nel pixel la famiglia scelta invece del sostituto del sistema.
    """
    if not str(fonts_dir or "").strip():
        return
    from . import fonts as fonts_mod  # noqa: PLC0415

    fonts_mod.fontsdir_arg = lambda: winpath.ass_filter_path(fonts_dir)


def _write_job_ass(
    cues,
    work: Path,
    dst: Path,
    *,
    aspect: str,
    platform: str,
    style: str,
    out_w: int,
    out_h: int,
    zone=None,
    font: Optional[Dict[str, Any]] = None,
) -> str:
    """Scrive l'ASS con la famiglia scelta; senza scelta, il preset di sempre."""
    from . import subs  # noqa: PLC0415

    resolved = _job_font(work) if font is None else font
    return subs.write_ass(
        cues,
        dst,
        aspect=aspect,
        platform=platform,
        style=style,
        out_w=out_w,
        out_h=out_h,
        bottom_limit_px=zone.text_limit_y if zone else None,
        margin_lr=zone.margin_lr if zone else None,
        font_family=resolved.get("family") or None,
    )


def cmd_subs(args: argparse.Namespace) -> int:
    from . import subs

    if args.style == "none":
        _out({"skipped": "style none"})
        return EXIT_OK

    aspect = args.aspect
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = winpath.safe_stem(Path(args.input).name)

    _log(f"[subs] whisper model={args.model} lang={args.lang or 'auto'}")
    transcript = subs.transcribe(str(args.input), model=args.model, language=args.lang)
    if transcript.skipped_reason:
        _log(f"[warn] {transcript.skipped_reason}")

    cues = subs.build_cues(transcript, style=args.style, aspect=aspect)

    zone = _layout_zone(args.width, args.height, getattr(args, "overlay", None))
    ass = subs.write_ass(
        cues,
        out_dir / f"{stem}.ass",
        aspect=aspect,
        platform=args.platform,
        style=args.style,
        out_w=args.width,
        out_h=args.height,
        bottom_limit_px=zone.text_limit_y if zone else None,
        margin_lr=zone.margin_lr if zone else None,
    )
    gate4 = _layout_gate(zone, ass) if zone else None
    srt = subs.write_srt(cues, out_dir / f"{stem}.srt")
    fonts = subs.verify_fonts(ass)

    _out(
        {
            "ass": ass,
            "srt": srt,
            "cues": len(cues),
            "words": len(transcript.words),
            "wpm": subs.transcript_stats(transcript).get("wpm", 0.0),
            "language": transcript.language,
            "style": args.style,
            "fonts": fonts,
            "burn_filter": subs.burn_filter(ass),
            "layout": zone.to_dict() if zone else None,
            "gate4": gate4,
        }
    )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# commands — triage / plan
# --------------------------------------------------------------------------- #


def cmd_triage(args: argparse.Namespace) -> int:
    from . import plan as plan_mod
    from . import report as report_io
    from . import triage

    rep = report_io.read(args.report)
    rep.setdefault("_path", str(Path(args.report).resolve()))
    brief = _load_brief(args.brief)
    req = plan_mod.needs_triage(brief, rep)
    if req is None:
        print("Brief completo: nessun triage necessario.")
        return EXIT_OK
    if args.json:
        _out(req)
    else:
        print(triage.to_markdown(req))
    return EXIT_NEEDS_INPUT


def cmd_plan(args: argparse.Namespace) -> int:
    from . import plan as plan_mod
    from . import report as report_io
    from . import triage

    rep = report_io.read(args.report)
    rep.setdefault("_path", str(Path(args.report).resolve()))
    brief = _load_brief(args.brief)
    answers = _load_json(args.answers) if args.answers else None
    if answers and args.answers:
        brief["_answers_path"] = str(Path(args.answers).resolve())

    req = plan_mod.needs_triage(brief, rep)
    if req is not None and answers is None:
        if args.json:
            _out(req)
        else:
            print(triage.to_markdown(req))
        return EXIT_NEEDS_INPUT

    brief.setdefault("source", (rep.get("source") or {}).get("path"))
    built = plan_mod.build_plan(rep, brief, answers)
    errors = plan_mod.validate(built, rep)
    if errors:
        for err in errors:
            _log(f"[plan] {err}")
        return EXIT_USAGE

    if args.out:
        report_io.write(built.to_dict(), args.out)
        _log(f"[ok] plan scritto: {args.out}")
    _out(built.to_dict())
    return EXIT_OK


def cmd_deliverables(args: argparse.Namespace) -> int:
    from . import plan as plan_mod

    built = _load_plan(args.plan)
    items = plan_mod.deliverables(built)
    if args.out:
        Path(args.out).write_text(
            json.dumps({"deliverables": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    _out({"deliverables": items})
    return EXIT_OK


# --------------------------------------------------------------------------- #
# commands — render / qc
# --------------------------------------------------------------------------- #


def _norm_source_path(value: Any) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(value)))
    except (OSError, ValueError):
        return str(value)


def _same_number(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) <= 1e-3
    except (TypeError, ValueError):
        return a == b


def _resolve_report(
    args: argparse.Namespace, plan_path: Path
) -> Optional[Dict[str, Any]]:
    """Explicit ``--report`` -> sibling ``report.json`` -> None."""
    explicit = getattr(args, "report", None)
    if explicit:
        path: Optional[Path] = Path(explicit)
    else:
        sibling = plan_path.parent / "report.json"
        path = sibling if sibling.is_file() else None
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VxError(f"report illeggibile: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise VxError(f"report non e' un oggetto JSON: {path}")
    return data


def _verify_source_identity(report: Dict[str, Any], media: Dict[str, Any]) -> None:
    """The report must describe the same source the renderer is about to read."""
    source = report.get("source")
    if not isinstance(source, dict):
        raise VxError("report.source mancante: impossibile provare la sorgente")
    if _norm_source_path(source.get("path")) != _norm_source_path(media.get("path")):
        raise VxError("report.source.path non combacia con il media analizzato")
    for key in ("size", "mtime", "duration", "width", "height"):
        if key in source and key in media and not _same_number(source[key], media[key]):
            raise VxError(f"report.source.{key} non combacia con il media analizzato")


def cmd_render(args: argparse.Namespace) -> int:
    from . import gates
    from . import probe as probe_mod
    from . import render

    built = _load_plan(args.plan)
    source = args.input or (built.brief or {}).get("source")
    if not source:
        raise VxError("serve --input: il piano non contiene la sorgente")
    media = probe_mod.probe(source).to_dict()

    report = _resolve_report(args, Path(args.plan))
    if report is None:
        enabled = [t for t in (built.treatments or []) if t.get("enabled")]
        if enabled:
            _log(
                "[render] piano con trattamenti attivi ma nessun report: "
                "l'evidenza non e' verificabile"
            )
            return EXIT_USAGE
    else:
        _verify_source_identity(report, media)

    if report is not None:
        declared = (built.brief or {}).get("autonomous_findings") or []
        if declared:
            report.setdefault("findings", [])
            seen = {f.get("id") for f in report["findings"] if isinstance(f, dict)}
            report["findings"].extend(f for f in declared if f.get("id") not in seen)

    visual = (
        _visual_from_brief(_load_json(args.visual_brief))
        if getattr(args, "visual_brief", None)
        else None
    )
    work = Path(args.workdir) if args.workdir else winpath.work_root() / "manual"
    _log(f"[render] precision={args.precision}")
    gates.pre_render(built, media, findings=(report or {}).get("findings"))
    result = render.render(
        built,
        media,
        precision=args.precision,
        workdir=work,
        subs_path=args.subs or None,
        crop_plan=(built.render or {}).get("crop"),
        visual=visual,
    )
    _out({k: v for k, v in result.items() if k not in ("graph", "script")})
    return EXIT_OK


def cmd_qc(args: argparse.Namespace) -> int:
    from . import probe as probe_mod
    from . import qc

    built = _load_plan(args.plan)
    source = args.input or (built.brief or {}).get("source")
    if not source:
        raise VxError("serve --input: il piano non contiene la sorgente")
    media = probe_mod.probe(source).to_dict()
    executed = None
    sidecar = Path(args.plan).parent / "render.json"
    if sidecar.is_file():
        executed = (_load_json(sidecar) or {}).get("target")
    result = qc.check_render(
        built,
        media,
        args.render,
        subs_path=args.subs or None,
        executed_target=executed,
    )
    if args.out:
        Path(args.out).write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    _out(result.to_dict())
    return EXIT_OK if result.pass_ else EXIT_QC_FAIL


# --------------------------------------------------------------------------- #
# commands — the protocol
# --------------------------------------------------------------------------- #


#: Le fasi che ``vx ingest`` attraversa davvero, nell'ordine del codice:
#: chiave (la stessa che finisce in ``progress.json``), quota dell'arco e la
#: riga in italiano che la vista mostra. L'arco lo decide il chiamante — da
#: solo 0-100, dentro ``vx run`` 0-30 — cosi' la percentuale e' aggiornata ai
#: confini di stadio senza inventare un secondo formato.
_INGEST_STAGES: tuple = (
    ("ingest", 0.07, "Importo il materiale: probe, normalizzazione, staging."),
    ("proxy", 0.22, "Faccio il proxy a bassa risoluzione."),
    ("analysis", 0.44, "Misuro il video: esposizione, colore, movimento, tagli."),
    ("speech", 0.62, "Ascolto il parlato e misuro i silenzi."),
    ("audio", 0.74, "Misuro l'audio: rumore, loudness, clipping."),
    ("faces", 0.86, "Cerco volti e speaker."),
    ("thumbs", 0.93, "Estraggo le anteprime e il contact sheet."),
    ("triage", 1.0, "Guardo cosa manca e preparo le domande."),
)

_INGEST_PHASES = tuple((key, label) for key, _, label in _INGEST_STAGES)

#: ``vx run`` attraversa l'ingest e poi tutto il resto: il pannello elenca le
#: fasi in anticipo, cosi' la vista sa dove siamo e quanto manca.
_RUN_PHASES = _INGEST_PHASES + (
    ("plan", "Scelgo i tagli e le scelte del piano."),
    ("subs", "Scrivo sottotitoli, hook e inquadratura."),
    ("render", "Costruisco la timeline e renderizzo. E' la parte lunga."),
    ("render_done", "Il master e' scritto."),
    ("qc", "Controllo il risultato: durata, loudness, picco, audio."),
    ("deliverables", "Preparo le consegne."),
)


#: Il pannello dell'ultimo comando ancora in corso. Se il processo esce per un
#: errore o per Ctrl+C, ``main`` lo chiude dicendo la verita' invece di lasciare
#: l'ultima percentuale a far credere che il lavoro sia finito.
_ACTIVE_PROGRESS: Optional["_Progress"] = None


class _Progress:
    """Il pannello dei comandi che non passano da ``auto``.

    ``auto`` racconta con ``narrate.Narrator``; ``ingest`` e ``run`` pubblicano
    lo **stesso** ``progress.json``/``progress.html`` con lo stesso
    ``panel.Panel``, quindi ``vx serve`` legge una fonte sola
    (``progress_source: "json"``) invece di dedurre le fasi dalle cartelle.
    Un pannello che non si scrive non ferma mai il lavoro: si perde la vista,
    non l'ingest.
    """

    def __init__(
        self, work: Path, phases: Sequence[tuple], *, prompt: str = "", clips: int = 1
    ) -> None:
        from . import panel as panel_mod

        global _ACTIVE_PROGRESS
        self.labels = {str(key): str(label) for key, label in phases}
        self.started = time.monotonic()
        self.panel = panel_mod.Panel(
            Path(work) / "progress.html",
            prompt=str(prompt),
            clips=int(clips),
            phases=list(phases),
        )
        self.panel.write()
        _ACTIVE_PROGRESS = self

    def __call__(self, percent: float, stage: str, *, note: str = "") -> None:
        key = str(stage)
        try:
            line = note or self.labels.get(key, key)
            self.panel.note = line
            self.panel.record(
                float(percent), key, elapsed=time.monotonic() - self.started
            )
            # La riga che la vista mostra finisce anche nel journal: si racconta
            # cosa succede, non solo a che punto siamo.
            self.panel.say(line, phase=key)
        except Exception:  # noqa: BLE001 - the panel is a viewer, not a gate
            pass

    def finish(self, note: str, *, master: str = "") -> None:
        global _ACTIVE_PROGRESS
        try:
            self.panel.say(note, phase="done")
            self.panel.finish(
                note, elapsed=time.monotonic() - self.started, master=master
            )
        except Exception:  # noqa: BLE001
            pass
        finally:
            _ACTIVE_PROGRESS = None

    def abort(self, note: str, *, claim_master_missing: bool = False) -> None:
        """Il lavoro si e' fermato a meta': la percentuale resta, la nota dice cosa.

        Un comando che esce per un errore non ha un master: il pannello non lo
        dichiara concluso, non registra il file e racconta in italiano dove si
        e' fermato. Solo chi sa che il master non e' stato scritto lo dice
        (``claim_master_missing``): altrove la nota non afferma cio' che non sa.
        """
        global _ACTIVE_PROGRESS
        _ACTIVE_PROGRESS = None
        text = str(note or "fermato prima della fine")
        if claim_master_missing:
            text = f"{text}: il master non esiste"
        try:
            self.panel.say(text, phase="stop")
            self.panel.stop(text, elapsed=time.monotonic() - self.started)
        except Exception:  # noqa: BLE001
            pass

    def stop(self, note: str) -> None:
        """Il lavoro aspetta l'umano: la percentuale resta, la nota lo dice."""
        try:
            self.panel.note = note
            self.panel.say(note, phase="wait")
            self.panel.write()
        except Exception:  # noqa: BLE001
            pass


def _stop_active(note: str, *, claim_master_missing: bool = False) -> None:
    """Chiude il pannello del comando attivo, se c'e', dicendo come e' andata."""
    progress = _ACTIVE_PROGRESS
    if progress is not None:
        progress.abort(note, claim_master_missing=claim_master_missing)


def _publish_progress(
    work: Path, phases: Sequence[tuple], *, prompt: str = "", clips: int = 1
) -> Optional[_Progress]:
    try:
        return _Progress(work, phases, prompt=prompt, clips=clips)
    except Exception:  # noqa: BLE001 - no panel is better than no ingest
        return None


def _stage(
    progress: Optional[_Progress], percent: float, key: str, *, note: str = ""
) -> None:
    if progress is None:
        return
    progress(float(percent), key, note=note)


def _ingest(
    src: Path,
    args: argparse.Namespace,
    brief_override: Optional[Dict[str, Any]] = None,
    *,
    progress: Optional[_Progress] = None,
    arc: Sequence[float] = (0.0, 100.0),
    phases: Optional[Sequence[tuple]] = None,
    prompt: str = "",
    session: Any = None,
) -> Dict[str, Any]:
    """Fase 1 + Fase 4: proxy, pre-analysis, findings. Never mutates the source."""
    from . import analyze, audio, faces
    from . import ingest as ingest_mod
    from . import plan as plan_mod
    from . import probe as probe_mod
    from . import proxy as proxy_mod
    from . import report as report_io
    from . import settings
    from . import triage

    fingerprint = winpath.source_fingerprint(str(src))
    if args.workdir:
        work = Path(args.workdir)
        work.mkdir(parents=True, exist_ok=True)
    else:
        stale = (
            winpath.work_root()
            / f"{winpath.safe_stem(src.name)}-{fingerprint['sha256'][:8]}"
        )
        winpath.clear_lock(stale)
        work = winpath.make_workdir(str(src))

    # Il pannello nasce prima del probe: la vista c'e' da subito e `vx serve`
    # legge `progress.json` invece di dedurre le fasi dalle cartelle. Dentro
    # `run` il pannello e' gia' quello del lavoro e lo chiude `cmd_run`.
    owns_panel = progress is None and phases is None
    if progress is None:
        progress = _publish_progress(
            work,
            phases or _INGEST_PHASES,
            prompt=prompt or f"Ingest — {src.name}",
        )
    lo, hi = float(arc[0]), float(arc[1])
    if session is not None:
        session.set_job(work)

    def stage(key: str) -> None:
        for name, share, label in _INGEST_STAGES:
            if name == key:
                percent = lo + (hi - lo) * share
                _stage(progress, percent, key, note=label)
                if session is not None:
                    session.stage(key, round(percent, 1), label)
                return

    probe_data = probe_mod.probe(str(src)).to_dict()
    raw_stream = probe_mod.raw_video_stream(str(src))
    normalization = ingest_mod.normalize_source(str(src), raw_stream, work / "stage_00")
    analysis_src = Path(normalization.staged_path)
    analysis_probe = (
        probe_mod.probe(str(analysis_src)).to_dict()
        if normalization.is_staged
        else probe_data
    )
    if normalization.is_staged:
        _say(f"[0/6] ingest: {', '.join(normalization.filters_applied)}")
    stage("ingest")

    _say("[1/6] proxy a bassa risoluzione")
    proxy_info = proxy_mod.make_proxy(
        str(analysis_src), work / "proxy" / "proxy.mp4", width=args.width
    )
    proxy_path = proxy_info["path"]
    stage("proxy")

    _say("[2/7] pre-analisi video (proxy)")
    video = analyze.analyze_video(
        str(analysis_src), sample_fps=args.sample_fps, proxy_path=proxy_path
    )
    stage("analysis")
    analysis_sha = (
        winpath.source_fingerprint(str(analysis_src))["sha256"]
        if normalization.is_staged
        else fingerprint["sha256"]
    )
    _say("[3/7] parlato")
    speech, transcript, chunked = _analyze_speech(
        str(analysis_src),
        workdir=work,
        source_sha256=fingerprint["sha256"],
        analysis_sha256=analysis_sha,
        media=analysis_probe,
        model=args.model,
        language=args.lang,
        transcribe=not getattr(args, "no_speech", False),
        chunk_sec=_analysis_chunk_sec(args),
    )
    stage("speech")
    _say("[4/7] pre-analisi audio")
    aud = audio.analyze_audio(str(analysis_src), vad_result=(chunked or {}).get("vad"))
    stage("audio")
    _say("[5/7] volti e speaker")
    fac = faces.detect_faces_tracked(proxy_path, sample_fps=SAMPLE_FPS_FACE)
    # Detection ran on the proxy; every consumer below reads `analysis_probe`
    # dimensions (crop, report, plan). Convert once, here.
    fac = faces.rescale_tracks(
        fac,
        int(proxy_info.get("width") or 0),
        int(proxy_info.get("height") or 0),
        int(analysis_probe.get("width") or 0),
        int(analysis_probe.get("height") or 0),
    )
    stage("faces")
    _say("[6/7] anteprime")
    thumbs = proxy_mod.thumbs(str(src), work / "analysis", count=args.thumbs)
    sheet = proxy_mod.contact_sheet(thumbs, work / "analysis" / "contact.jpg")
    stage("thumbs")

    rep = report_io.new_report(probe_data, tool=_tool_block())
    report_path = work / "report.json"
    rep["source"]["sha256"] = fingerprint["sha256"]
    rep["source"]["analysis_duration"] = analysis_probe.get("duration")
    rep["source"]["staged"] = (
        normalization.staged_path if normalization.is_staged else None
    )
    rep["source"]["normalized"] = normalization.to_dict()
    rep["workdir"] = str(work)
    rep["settings"] = settings.describe()
    rep["_path"] = str(report_path)
    rep["artifacts"] = {
        "proxy": proxy_path,
        "thumbs": thumbs,
        "contact_sheet": sheet,
        "ass": None,
        "srt": None,
        "render": None,
        "report": str(report_path),
        "qc": None,
    }
    analysis_block = {
        "video": video.to_dict(),
        "audio": aud.to_dict(),
        "faces": fac.to_dict(),
        "speech": speech,
    }
    if chunked is not None:
        analysis_block["chunked"] = _chunked_report(chunked)
    report_io.merge(rep, "analysis", analysis_block)
    report_io.merge(rep, "findings", plan_mod.detect_findings(rep))

    brief = brief_override if brief_override is not None else _load_brief(args.brief)
    autonomous = bool(getattr(args, "autonomous", False))
    _say("[7/7] triage")
    request = plan_mod.needs_triage(brief, rep, autonomous=autonomous)
    if autonomous:
        rep["triage"] = {
            "status": "autonomous",
            "questions": [],
            "defaults": plan_mod.autonomous_defaults(rep, brief),
        }
    else:
        rep["triage"] = request
    report_io.write(rep, report_path)
    if request is not None:
        report_io.write(request, work / "triage.json")
        (work / "triage.md").write_text(triage.to_markdown(request), encoding="utf-8")
    if session is not None:
        session.artifact("report", str(report_path))
        if sheet is not None:
            session.artifact("contact_sheet", str(sheet))
        if proxy_path:
            session.artifact("proxy", str(proxy_path))
        session.gate("triage", open=request is not None, detail="" if request is None else "servono risposte")
    winpath.clear_lock(work)

    stage("triage")
    if owns_panel and progress is not None:
        if request is not None:
            note = "Ingest concluso: report pronto, servono alcune risposte."
        elif autonomous:
            note = "Ingest concluso: report pronto, default registrati."
        else:
            note = "Ingest concluso: report pronto, si passa al piano."
        progress.finish(note, master="")

    return {
        "workdir": work,
        "report": rep,
        "report_path": report_path,
        "proxy": proxy_info,
        "triage": request,
        "fingerprint": fingerprint,
        "probe": analysis_probe,
        "analysis_source": str(analysis_src),
        "analysis_sha256": analysis_sha,
        "normalization": normalization.to_dict(),
        "thumbs": thumbs,
        "contact_sheet": sheet,
        "transcript": transcript,
        "chunked": chunked,
        "progress": progress,
    }


@_session_command("ingest")
def cmd_ingest(args: argparse.Namespace) -> int:
    from . import triage

    src = Path(args.input)
    state = _ingest(src, args, session=_session_of(args))

    request = state["triage"]
    recorded = (state["report"] or {}).get("triage")
    autonomous = bool(getattr(args, "autonomous", False))
    if args.json:
        _out(
            {
                "workdir": str(state["workdir"]),
                "report": str(state["report_path"]),
                "proxy": state["proxy"],
                "triage": recorded,
                "verdict": (
                    "autonomous"
                    if autonomous
                    else ("needs_user_input" if request else "ready_for_plan")
                ),
                "resume_command": (request or {}).get("resume_command"),
            }
        )
    else:
        if request is not None:
            print(triage.to_markdown(request))
            print(f"\nReport: {state['report_path']}")
            print(f"Anteprime: {state['contact_sheet']}")
        elif autonomous:
            print("Modalita' autonoma: nessuna domanda, default registrati nel report.")
            print(f"Report: {state['report_path']}")
        else:
            print("Brief completo: pronto per il piano.")
            print(f"Report: {state['report_path']}")
    return EXIT_NEEDS_INPUT if request is not None else EXIT_OK


@_session_command("run")
def cmd_run(args: argparse.Namespace) -> int:
    """Fase 7 end-to-end: ingest -> plan -> subs -> proxy -> full -> qc -> deliverables."""
    from . import autoframe, render, subs
    from . import gates
    from . import plan as plan_mod
    from . import qc, triage
    from .contract import FaceTracks
    from . import report as report_io

    session = _session_of(args)
    src = Path(args.input)
    answers = _load_json(args.answers) if args.answers else None

    payload = None
    brief_override: Optional[Dict[str, Any]] = None
    if getattr(args, "payload", None):
        payload = _load_json(args.payload)
    elif getattr(args, "prompt", None):
        from . import decide as decide_mod

        try:
            payload = decide_mod.parse_and_decide(args.prompt)
        except decide_mod.AmbiguousContractError as exc:
            _log(f"[run] prompt ambiguo in STRICT: {exc}")
            return EXIT_NEEDS_INPUT
    if payload is not None:
        if not isinstance(payload, dict) or "scene" not in payload:
            _log("[run] payload non valido: manca scene")
            return EXIT_USAGE
        brief_override = {
            "format": payload["scene"]["aspect_ratio"],
            "hook": "none",
            "subtitles": "none",
            "source": str(src),
        }
        args.no_speech = True
        answers = {}

    state = _ingest(
        src,
        args,
        brief_override=brief_override,
        arc=(0.0, 30.0),
        phases=_RUN_PHASES,
        prompt=f"Montaggio — {src.name}",
        session=session,
    )
    progress: Optional[_Progress] = state.get("progress")

    rep = state["report"]
    work: Path = state["workdir"]
    media = state["probe"]
    brief = brief_override if brief_override is not None else _load_brief(args.brief)
    if answers is None and getattr(args, "autonomous", False):
        answers = {}
    if answers is None:
        request = state["triage"] or plan_mod.needs_triage(brief, rep)
        if request is not None:
            if progress is not None:
                progress.stop("Mi serve una risposta: guarda le domande del triage.")
            if args.json:
                _out(request)
            else:
                print(triage.to_markdown(request))
            return EXIT_NEEDS_INPUT
    else:
        brief["_answers_path"] = (
            None if not args.answers else str(Path(args.answers).resolve())
        )

    brief = plan_mod.apply_answers(brief, answers or {})
    brief.setdefault("source", (rep.get("source") or {}).get("path"))
    visual = (
        _visual_from_brief(_load_json(args.visual_brief))
        if getattr(args, "visual_brief", None)
        else None
    )

    if (
        str(brief.get("subtitles") or "auto") != "none"
        and state.get("transcript") is None
    ):
        _log("[run] trascrizione (serve al piano e ai sottotitoli)")
        speech, transcript, chunked = _analyze_speech(
            state.get("analysis_source", str(src)),
            workdir=work,
            source_sha256=state["fingerprint"]["sha256"],
            analysis_sha256=state.get("analysis_sha256")
            or state["fingerprint"]["sha256"],
            media=state["probe"],
            model=args.model,
            language=args.lang,
            transcribe=True,
            chunk_sec=_analysis_chunk_sec(args),
        )
        report_io.merge(rep, "analysis", {"speech": speech})
        if chunked is not None:
            report_io.merge(rep, "analysis", {"chunked": _chunked_report(chunked)})
        state["transcript"] = transcript

    _log("[run] piano")
    if payload is not None:
        from . import contract as contract_mod

        built = contract_mod.payload_to_edit_plan(payload, state["probe"])
        bridge = (built.brief or {}).get("bridge") or {}
        _log(f"[bridge] eseguiti: {bridge.get('executed')}")
        if bridge.get("unsupported"):
            _log(f"[bridge] non eseguiti: {bridge.get('unsupported')}")
    else:
        built = plan_mod.build_plan(rep, brief, answers or {})
    declared = (built.brief or {}).get("autonomous_findings") or []
    if declared:
        rep.setdefault("findings", [])
        seen = {f.get("id") for f in rep["findings"] if isinstance(f, dict)}
        rep["findings"].extend(f for f in declared if f.get("id") not in seen)
    errors = plan_mod.validate(built, rep)
    if errors:
        for err in errors:
            _log(f"[plan] {err}")
        return EXIT_USAGE
    _stage(progress, 35.0, "plan")
    # ---- subtitles
    subs_path: Optional[str] = None
    hook_path: Optional[str] = None
    stem = winpath.safe_stem(src.name)
    if _enabled(built, "subtitles"):
        _log("[run] sottotitoli dinamici")
        params = _params(built, "subtitles")
        transcript = state.get("transcript") or subs.transcribe(
            str(src), model=args.model, language=args.lang
        )
        quality = subs.transcript_quality(getattr(transcript, "words", []) or [])
        if not quality["ok"]:
            _log(f"[run] sottotitoli non incisi: {quality['reason']}")
            for t in built.treatments or []:
                if t.get("name") == "subtitles":
                    t["enabled"] = False
                    t.setdefault("reason", {})["explain"] = (
                        f"non incisi: {quality['reason']}"
                    )
            rep.setdefault("skipped", []).append({"treatment": "subtitles", **quality})
        else:
            cues = subs.build_cues(
                transcript,
                style=str(params.get("style") or "karaoke"),
                aspect=str(built.output.get("aspect") or "9:16"),
                max_chars=params.get("max_chars"),
                max_lines=params.get("max_lines"),
                max_cps=params.get("max_cps"),
            )
            zone = _layout_zone(
                int(built.output.get("width") or 1080),
                int(built.output.get("height") or 1920),
                getattr(args, "overlay", None),
            )
            font = _job_font(work)
            if font["family"]:
                _use_job_fonts(font["fonts_dir"])
                _log(f"[run] font scelto: {font['family']} ({font['fonts_dir']})")
            elif font["note"]:
                _log(f"[run] font: {font['note']}")
                rep.setdefault("skipped", []).append(
                    {"treatment": "font", "reason": font["note"]}
                )
            ass = _write_job_ass(
                cues,
                work,
                work / "subs" / f"{stem}.ass",
                aspect=str(built.output.get("aspect") or "9:16"),
                platform=str(built.output.get("platform") or "tiktok"),
                style=str(params.get("style") or "karaoke"),
                out_w=int(built.output.get("width") or 1080),
                out_h=int(built.output.get("height") or 1920),
                zone=zone,
                font=font,
            )
            if zone is not None:
                rep["artifacts"]["gate4"] = _layout_gate(zone, ass)
                rep["artifacts"]["layout"] = zone.to_dict()
            srt = subs.write_srt(cues, work / "subs" / f"{stem}.srt")
            fonts = subs.verify_fonts(ass, fontsdir=font["fonts_dir"] or None)
            if not fonts.get("ok", True):
                raise QCFailure(
                    {
                        "step": "fonts",
                        "failures": [
                            f"font sostituito da libass: {fonts.get('fonts_used')}"
                        ],
                        "metrics": fonts,
                    }
                )
            subs_path = ass
            rep["artifacts"]["ass"] = ass
            rep["artifacts"]["srt"] = srt
            rep["artifacts"]["fonts"] = fonts

    # ---- hook overlay
    hook_treatment = next(
        (t for t in built.treatments or [] if t.get("name") == "hook_overlay"), None
    )
    if hook_treatment is not None:
        text = (brief.get("hook_text") or "").strip()
        if not text:
            words = getattr(state.get("transcript"), "words", []) or []
            text = " ".join(
                str(w.get("word") or "").strip()
                for w in words[:8]
                if str(w.get("word") or "").strip()
            ).strip()
        if not text:
            hook_treatment["enabled"] = False
            hook_treatment.setdefault("reason", {})["explain"] = (
                "nessun testo disponibile: overlay non inciso"
            )
            rep.setdefault("skipped", []).append(
                {
                    "treatment": "hook_overlay",
                    "reason": "nessun testo disponibile: overlay non inciso",
                }
            )
            _log("[run] hook overlay non inciso: nessun testo disponibile")
        elif _enabled(built, "hook_overlay"):
            _log("[run] hook overlay")
            params = _params(built, "hook_overlay")
            hook_path = str(
                subs.write_hook_ass(
                    work / "subs" / f"{stem}.hook.ass",
                    text,
                    out_w=int(built.output.get("width") or 1080),
                    out_h=int(built.output.get("height") or 1920),
                    start_s=float(params.get("at") or 0.0),
                    duration_s=float(params.get("duration") or 3.0),
                )
            )
            rep["artifacts"]["hook"] = hook_path

    # ---- crop plan
    crop: Optional[Dict[str, Any]] = None
    if _enabled(built, "autoframe"):
        _log("[run] auto-framing")
        faces_data = rep["analysis"].get("faces") or {}
        known = set(FaceTracks.__dataclass_fields__)
        tracks = FaceTracks(**{k: v for k, v in faces_data.items() if k in known})
        params = _params(built, "autoframe")
        crop_plan = autoframe.build_crop_plan(
            tracks,
            int(media.get("width") or 0),
            int(media.get("height") or 0),
            aspect=str(built.output.get("aspect") or "9:16"),
            eye_line=float(params.get("eye_line") or 0.38),
            scene_cuts=(rep["analysis"].get("video") or {}).get("scene_cuts"),
            precision="full",
        )
        crop = crop_plan.to_dict()
        built.render["crop"] = crop

    # ---- preview on the proxy, then full-res delivery
    gates.pre_render(built, media, findings=rep.get("findings"))
    report_io.write(built.to_dict(), work / "plan.json")
    if session is not None:
        session.stage("plan", 40.0, "Piano scritto")
        _session_artifact(session, "plan", work / "plan.json")
    _stage(progress, 40.0, "subs")
    winpath.heartbeat(work, "render_preview")
    _stage(progress, 45.0, "render")
    _log("[run] render anteprima (proxy)")
    preview = render.render(
        built,
        media,
        precision="proxy",
        workdir=work / "stage",
        subs_path=subs_path,
        crop_plan=crop,
        visual=visual,
        hook_path=hook_path,
    )
    _log(f"[run] anteprima: {preview['path']} ({preview['frames']} frame)")
    _stage(
        progress,
        55.0,
        "render",
        note="Anteprima pronta: renderizzo il master a piena risoluzione.",
    )
    if session is not None:
        session.stage("render", 55.0, "Anteprima pronta, master a piena risoluzione")
        _session_artifact(session, "preview", preview.get("path"))

    winpath.heartbeat(work, "render_full")
    _log("[run] render finale (full-res)")
    final_plan = render.swap_proxy_to_full(built, media)
    final = render.render(
        final_plan,
        media,
        precision="full",
        workdir=work / "stage",
        subs_path=subs_path,
        crop_plan=crop,
        visual=visual,
        hook_path=hook_path,
    )
    _stage(progress, 85.0, "render_done")
    if session is not None:
        session.stage("render_done", 85.0, "Master scritto")
        _session_artifact(session, "render", final.get("path"))

    winpath.heartbeat(work, "qc")
    _log("[run] controllo qualita")
    report_io.write(
        {
            "target": final.get("target"),
            "loudness": final.get("loudness"),
            "stages": final.get("stages", []),
        },
        work / "render.json",
    )
    result = qc.check_render(
        final_plan,
        media,
        final["path"],
        subs_path=subs_path,
        source_fp=state["fingerprint"],
        executed_target=final.get("target"),
    )
    report_io.write(result.to_dict(), work / "qc.json")
    planned_total = (
        sum(float(c["out"]) - float(c["in"]) for c in (final_plan.cuts or [])) or None
    )
    gates.post_render(
        final_plan, media, final["path"], workdir=work, planned_total=planned_total
    )
    _stage(progress, 92.0, "qc")
    if session is not None:
        session.stage("qc", 92.0, "Controllo qualita' eseguito")
        _session_artifact(session, "qc", work / "qc.json")

    rep["plan"] = final_plan.to_dict()
    rep["qc"] = result.to_dict()
    rep["artifacts"]["render"] = final["path"]
    rep["artifacts"]["preview"] = preview["path"]
    rep["artifacts"]["qc"] = str(work / "qc.json")
    from . import deliverables

    out_dir = work / "out"
    srt_source = Path(rep["artifacts"]["srt"]) if rep["artifacts"].get("srt") else None
    produced = deliverables.produce(
        final_plan,
        Path(final["path"]),
        out_dir,
        srt_source=srt_source,
        workdir=work,
    )
    report_io.write({"deliverables": produced}, out_dir / "deliverables.json")
    rep["deliverables"] = produced
    report_io.write(rep, work / "report.json")
    _stage(progress, 96.0, "deliverables")
    if session is not None:
        session.stage("deliverables", 96.0, "Consegne pronte")
        _session_artifact(session, "deliverables", out_dir / "deliverables.json")
        _session_artifact(session, "report", work / "report.json")

    if not args.keep_proxy:
        Path(state["proxy"]["path"]).unlink(missing_ok=True)
        rep["artifacts"]["proxy"] = None
        report_io.write(rep, work / "report.json")

    master_item = next(
        (item for item in produced if str(item.get("kind")) == "master"), None
    )
    master_path = str((master_item or {}).get("path") or final["path"])
    if session is not None:
        session.set_job(work)
        _session_artifact(session, "master", master_path)
    if progress is not None:
        progress.finish(
            "Fatto: il master e' scritto, il controllo qualita passa."
            if result.pass_
            else "Fatto: il master e' scritto, il controllo qualita non passa: "
            "vedi qc.json.",
            master=master_path,
        )

    summary = {
        "output": final["path"],
        "preview": preview["path"],
        "frames": final["frames"],
        "expected_frames": final["expected_frames"],
        "loudness": final["loudness"],
        "qc_pass": result.pass_,
        "qc_failures": result.failures,
        "subs": subs_path,
        "report": str(work / "report.json"),
        "plan": str(work / "plan.json"),
        "deliverables": rep["deliverables"],
    }
    if args.json:
        _out(summary)
    else:
        print(f"Output: {final['path']}")
        print(f"Frame: {final['frames']} (attesi {final['expected_frames']})")
        print(f"QC: {'PASS' if result.pass_ else 'FAIL ' + ', '.join(result.failures)}")
        print(f"Report: {work / 'report.json'}")
    return EXIT_OK if result.pass_ else EXIT_QC_FAIL


# --------------------------------------------------------------------------- #
# commands — recipes
# --------------------------------------------------------------------------- #


def _recipe_explicit_params(loaded: Dict[str, Any]) -> set:
    """Which ``params`` keys the chain actually wrote, not the inherited defaults."""
    explicit = set()
    for source in loaded.get("sources") or []:
        try:
            data = json.loads(Path(str(source)).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        params = data.get("params") if isinstance(data, dict) else None
        if isinstance(params, dict):
            explicit.update(str(key) for key in params)
    return explicit


def _looks_like_recipe_file(value: str) -> bool:
    path = Path(value).expanduser()
    if path.is_file():
        return True
    return path.suffix.lower() == ".json" or "/" in value or "\\" in value


def _recipe_file_problems(path: Path) -> List[str]:
    from . import recipe

    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return [f"cannot read {path}: {exc}"]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [f"{path}: not valid JSON ({exc})"]
    return recipe.validate(data)


def _recipe_name_problems(name: str) -> List[str]:
    from . import recipe

    _loaded, problems = recipe.load_safe(name)
    return list(problems)


def _print_recipe_check(label: str, problems: List[str]) -> bool:
    if not problems:
        print(f"OK    {label}")
        return True
    print(f"FAIL  {label}")
    for problem in problems:
        print(f"      {problem}")
    return False


def cmd_recipe_list(args: argparse.Namespace) -> int:
    from . import recipe

    rows = recipe.list_all()
    if args.json:
        _out(rows)
        return EXIT_OK

    print(f"{'NAME':<22} {'ORIGIN':<18} {'DESCRIPTION'}")
    for row in rows:
        origin = str(row.get("source") or "")
        if row.get("shadowed"):
            origin += " shadowed"
        description = str(row.get("description") or "")
        if not row.get("valid"):
            problems = row.get("problems") or ["invalid recipe"]
            line = f"INVALIDA: {problems[0]}"
            description = f"{line} — {description}" if description else line
        print(f"{str(row.get('name') or ''):<22} {origin:<18} {description}")
    return EXIT_OK


def cmd_recipe_show(args: argparse.Namespace) -> int:
    from . import recipe

    loaded, problems = recipe.load_safe(args.name)
    if loaded is None:
        raise VxError(problems[0] if problems else f"recipe '{args.name}' not found")

    explicit = _recipe_explicit_params(loaded)
    defaults = sorted(str(key) for key in loaded["params"] if key not in explicit)

    if args.json:
        payload = dict(loaded)
        payload["defaults"] = defaults
        _out(payload)
        return EXIT_OK

    print(f"nome:        {loaded['name']}")
    print(f"descrizione: {loaded['description']}")
    print("file:")
    for source in loaded["sources"]:
        print(f"  {source}")
    for section in ("settings", "brief"):
        print(f"{section}:")
        text = json.dumps(loaded[section], ensure_ascii=False, indent=2)
        for line in text.splitlines():
            print(f"  {line}")
    print("params:")
    for key in sorted(loaded["params"]):
        suffix = "  (default)" if key in defaults else ""
        value = json.dumps(loaded["params"][key], ensure_ascii=False)
        print(f"  {key} = {value}{suffix}")
    return EXIT_OK


def cmd_recipe_validate(args: argparse.Namespace) -> int:
    from . import recipe

    targets = list(args.targets or [])
    ok = True
    if not targets:
        for row in recipe.list_all():
            name = str(row.get("name") or "")
            ok = _print_recipe_check(name, _recipe_name_problems(name)) and ok
        return EXIT_OK if ok else EXIT_USAGE

    for target in targets:
        value = str(target)
        if _looks_like_recipe_file(value):
            label = str(Path(value).expanduser())
            problems = _recipe_file_problems(Path(value).expanduser())
        else:
            label = value
            problems = _recipe_name_problems(value)
        ok = _print_recipe_check(label, problems) and ok
    return EXIT_OK if ok else EXIT_USAGE


def cmd_recipe_init(args: argparse.Namespace) -> int:
    from . import recipe

    name = str(args.name or "").strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise VxError(f"invalid recipe name {name!r}: use a file stem, not a path")

    base = str(args.extends).strip() if args.extends else None
    inherited: Optional[Dict[str, Any]] = None
    if base:
        inherited, problems = recipe.load_safe(base)
        if inherited is None:
            raise VxError(
                f"base recipe '{base}' is not usable: "
                + "; ".join(problems or ["unknown error"])
            )

    # Two explicit values are enough to start editing; everything else comes from
    # the declared defaults. When extending, keep the inherited value so ``init``
    # cannot silently change what the base recipe already decided.
    params: Dict[str, Any] = {}
    for key in ("shot_max", "loudness"):
        if inherited is not None:
            params[key] = inherited["params"][key]
        else:
            params[key] = recipe.PARAMS[key]["default"]

    doc: Dict[str, Any] = {
        "schema": recipe.SCHEMA,
        "name": name,
        "description": (
            f"Ricetta {name}: estende {base}, modifica i parametri qui sotto."
            if base
            else f"Ricetta {name}: base minima, modifica i parametri qui sotto."
        ),
    }
    if base:
        doc["extends"] = base
    doc["params"] = params

    problems = recipe.validate(doc)
    if problems:
        raise VxError("invalid recipe: " + "; ".join(problems))

    target = (
        Path(args.out).expanduser()
        if args.out
        else recipe.paths()["user"] / f"{name}.json"
    )
    if target.exists() and not args.force:
        raise VxError(
            f"recipe already exists: {target}; use --force to overwrite"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(str(target))
    return EXIT_OK


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def cmd_edl(args: argparse.Namespace) -> int:
    from . import gates, grammar, ingest, render
    from . import probe as probe_mod
    from .edl_plan import EDLPlan, EDLValidationError

    try:
        plan = EDLPlan.from_dict(_load_json(Path(args.plan)))
    except EDLValidationError as exc:
        _out({"ok": False, "errors": exc.errors})
        return 2

    violations = grammar.validate_cinematic_rules(plan)
    exemptions = grammar.camera_exemptions(plan)
    if violations:
        _out(
            {
                "ok": False,
                "errors": [item.details for item in violations],
                "grammar": [item.to_dict() for item in violations],
                "camera_exemptions": exemptions,
            }
        )
        return 2

    if args.edl_cmd == "validate":
        _out(
            {
                "ok": True,
                "contract_version": plan.contract_version,
                "sources": list(plan.sources),
                "video_tracks": len(plan.video_tracks),
                "audio_tracks": len(plan.audio_tracks),
                "duration_sec": plan.duration_sec,
                "resolution": list(plan.resolution),
                "camera_exemptions": exemptions,
            }
        )
        return EXIT_OK

    provided: Dict[str, str] = {}
    for item in args.source or []:
        source_id, separator, path = str(item).partition("=")
        if not separator or not source_id or not path:
            _out({"ok": False, "error": f"--source must be ID=PATH, got {item!r}"})
            return 2
        provided[source_id] = path
    declared = set(plan.sources)
    missing = sorted(declared - set(provided))
    if missing:
        _out({"ok": False, "error": "missing sources: " + ", ".join(missing)})
        return 2
    unknown = sorted(set(provided) - declared)
    if unknown:
        _out({"ok": False, "error": "unknown sources: " + ", ".join(unknown)})
        return 2

    work = Path(args.workdir) if args.workdir else Path.cwd() / "edl_work"
    work.mkdir(parents=True, exist_ok=True)
    staged = ingest.ingest_sources(provided, work / "stage_00")
    staged_paths = {
        source_id: result.staged_path for source_id, result in staged.items()
    }
    media_info = {
        source_id: probe_mod.probe_asset(path).to_dict()
        for source_id, path in staged_paths.items()
    }
    gates.validate_gate1_durations(plan, media_info)
    audio_source_ids = {
        clip.source_id for track in plan.audio_tracks for clip in track.clips
    }
    has_audio = any(
        media_info.get(source_id, {}).get("has_audio") for source_id in audio_source_ids
    )
    if plan.audio_tracks and not has_audio:
        _out(
            {
                "ok": False,
                "error": "plan declares audio but a staged source has no audio",
            }
        )
        return 2

    output = Path(args.out)
    render.render_edl(
        plan,
        staged_paths,
        output,
        workdir=work,
        crf=args.crf,
        preset=args.preset,
    )
    _out(
        {
            "ok": True,
            "output": str(output),
            "sources": staged_paths,
            "stage": {
                source_id: result.to_dict() for source_id, result in staged.items()
            },
        }
    )
    return EXIT_OK


@_session_command("edl3")
def cmd_edl3_validate(args: argparse.Namespace) -> int:
    """Valida un documento ``vx.edl3`` senza renderizzare: exit 0 se pulito."""
    from . import edl3

    path = Path(args.file)
    try:
        raw = _load_json(path)
    except VxError as exc:
        if args.json:
            _out({"ok": False, "document": str(path), "error": str(exc), "problems": []})
        else:
            _log(f"[error] {exc}")
        return EXIT_USAGE
    try:
        timeline = edl3.Timeline3.from_dict(raw)
    except edl3.EDLError as exc:
        problems = [{"code": exc.code, "where": exc.where, "detail": exc.detail}]
        return _report_edl3_validate(args, path, problems, raw.get("version"))
    problems = timeline.validate()
    return _report_edl3_validate(args, path, problems, timeline.version)


def _report_edl3_validate(
    args: argparse.Namespace, path: Path, problems: list, version: Any
) -> int:
    """Pubblica l'esito di ``edl3 validate`` e traduce i problemi in exit 2."""
    declared = str(version or "")
    if args.json:
        _out(
            {
                "ok": not problems,
                "document": str(path),
                "schema": declared,
                "accepted": declared in edl3_versions(),
                "problems": problems,
                "count": len(problems),
            }
        )
    elif problems:
        for problem in problems:
            where = problem.get("where") or ""
            print(f"{problem['code']} {where}: {problem['detail']}")
        print(f"{len(problems)} problema/i in {path}")
    else:
        print(f"ok: {path} — {declared}, nessun problema")
    return EXIT_OK if not problems else EXIT_USAGE


#: Le chiavi dei due schemi, per riconoscere il documento senza guardare il
#: nome del file: ``plan.json`` di ``auto`` non dichiara ``schema``, dichiara
#: ``contract_version``/``timeline``/``tracks``.
def _edl3_declared_version(raw: Mapping[str, Any]) -> str:
    """La versione EDL3 dichiarata dal documento, o ``""`` se non lo e'."""
    for key in ("version", "schema"):
        value = raw.get(key)
        if isinstance(value, str) and value.startswith("vx.edl3/"):
            return value
    return ""


def _edl2_declared_schema(raw: Mapping[str, Any]) -> str:
    """Lo schema 2.2 dichiarato, o ``""`` se il documento non e' un piano 2.2."""
    from .edl_plan import EDL_SCHEMA

    schema = raw.get("schema")
    if isinstance(schema, str) and schema.startswith("vx.edl/"):
        return schema
    contract = raw.get("contract_version")
    if isinstance(contract, str):
        parts = contract.split(".")
        if len(parts) >= 2 and parts[0].isdigit():
            return f"vx.edl/{parts[0]}.{parts[1]}"
    if isinstance(raw.get("timeline"), Mapping) and isinstance(
        raw.get("tracks"), Mapping
    ):
        # The auto `plan.json`: 2.2 shape without the `schema` label.
        return EDL_SCHEMA
    return ""


def _declared_duration(raw: Mapping[str, Any]) -> Any:
    """La ``timeline.duration_sec`` dichiarata, o ``None`` se manca."""
    timeline = raw.get("timeline")
    if not isinstance(timeline, Mapping):
        return None
    value = timeline.get("duration_sec")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _report_edl3_migrate(args: argparse.Namespace, payload: Dict[str, Any]) -> int:
    """Pubblica il report di ``edl3 migrate``: exit 2 quando non scrive nulla."""
    ok = bool(payload.get("ok"))
    if args.json:
        _out(payload)
        return EXIT_OK if ok else EXIT_USAGE

    schema = payload.get("schema") or {}
    if ok:
        duration = payload.get("duration_sec") or {}
        seconds = float(duration.get("to") or 0.0)
        print(
            f"ok: {payload.get('clips', 0)} clip, {payload.get('fps')} fps, "
            f"{seconds:.3f}s - {schema.get('from')} -> {schema.get('to')} -> "
            f"{payload.get('out')}"
        )
    else:
        error = payload.get("error") or {}
        line = f"{error.get('code')}: {error.get('detail')}"
        where = error.get("where")
        if where:
            line = f"{line} ({where})"
        print(line)
        for item in payload.get("loss") or []:
            print(f"loss {item['code']} {item['where']}: {item['detail']}")
        print(f"migrazione rifiutata: {payload.get('plan')}")
    for note in payload.get("notes") or []:
        print(f"nota: {note}")
    return EXIT_OK if ok else EXIT_USAGE


def _migrate_already_edl3(
    args: argparse.Namespace,
    raw: Mapping[str, Any],
    plan_path: Path,
    out_path: Path,
    version: str,
) -> int:
    """Copia un documento EDL3 invariato e lo dice: la migrazione e' idempotente."""
    from . import edl3

    payload: Dict[str, Any] = {
        "ok": False,
        "plan": str(plan_path),
        "out": None,
        "schema": {"from": version, "to": version},
        "clips": 0,
        "fps": None,
        "duration_sec": {"from": None, "to": None},
        "loss": [],
        "notes": [],
        "changed": False,
        "already": version,
    }
    try:
        timeline = edl3.Timeline3.from_dict(dict(raw))
        problems = timeline.validate()
    except edl3.EDLError as exc:
        problems = [{"code": exc.code, "where": exc.where, "detail": exc.detail}]
        timeline = None
    if problems or timeline is None:
        payload["problems"] = problems
        payload["error"] = {
            "code": (problems[0]["code"] if problems else "EDL3_INVALID"),
            "detail": "; ".join(
                f"{item['code']} at {item['where']}: {item['detail']}"
                for item in problems
            ),
            "where": str(plan_path),
        }
        return _report_edl3_migrate(args, payload)

    if out_path.resolve() != plan_path.resolve():
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(plan_path, out_path)
    seconds = round(float(timeline.duration), 6)
    payload.update(
        {
            "ok": True,
            "out": str(out_path),
            "clips": len(timeline.clips),
            "fps": timeline.fps,
            "duration_sec": {"from": seconds, "to": seconds},
            "notes": [
                f"gia' {version}: copiato invariato, nessuna modifica"
            ],
        }
    )
    return _report_edl3_migrate(args, payload)


@_session_command("edl3")
def cmd_edl3_migrate(args: argparse.Namespace) -> int:
    """Migra un piano ``vx.edl/2.2`` (anche il ``plan.json`` di ``auto``) in EDL3."""
    from . import edl3, edl3_migrate

    plan_path = Path(args.plan)
    out_path = Path(args.out)
    payload: Dict[str, Any] = {
        "ok": False,
        "plan": str(plan_path),
        "out": None,
        "schema": {"from": None, "to": None},
        "clips": 0,
        "fps": None,
        "duration_sec": {"from": None, "to": None},
        "loss": [],
        "notes": [],
        "changed": False,
        "already": None,
    }

    try:
        raw = _load_json(plan_path)
    except VxError as exc:
        payload["error"] = {
            "code": "PLAN_UNREADABLE",
            "detail": str(exc),
            "where": str(plan_path),
        }
        return _report_edl3_migrate(args, payload)

    version = _edl3_declared_version(raw)
    if version:
        return _migrate_already_edl3(args, raw, plan_path, out_path, version)

    source_schema = _edl2_declared_schema(raw)
    if not source_schema:
        payload["error"] = {
            "code": "PLAN_SCHEMA",
            "detail": (
                f"{plan_path} is neither a vx.edl/2.2 plan nor a "
                f"{edl3.EDL3_VERSION} document (keys: {sorted(raw)[:8]})"
            ),
            "where": str(plan_path),
        }
        return _report_edl3_migrate(args, payload)

    payload["schema"] = {"from": source_schema, "to": edl3.EDL3_VERSION}
    payload["duration_sec"] = {"from": _declared_duration(raw), "to": None}

    try:
        document = edl3_migrate.migrate(raw)
    except edl3_migrate.MigrationError as exc:
        payload["error"] = {
            "code": exc.code,
            "detail": exc.detail,
            "where": exc.where or str(plan_path),
        }
        return _report_edl3_migrate(args, payload)

    try:
        losses = edl3_migrate.audit(raw)
        declared = edl3_migrate.notes(raw)
    except edl3_migrate.MigrationError as exc:
        payload["error"] = {
            "code": exc.code,
            "detail": exc.detail,
            "where": exc.where or str(plan_path),
        }
        return _report_edl3_migrate(args, payload)

    payload["notes"] = declared
    if losses:
        payload["loss"] = losses
        payload["error"] = {
            "code": "MIGRATION_LOSS",
            "detail": (
                f"{len(losses)} parte/i del piano 2.2 non hanno un campo in "
                f"{edl3.EDL3_VERSION}: nessun documento scritto"
            ),
            "where": str(plan_path),
        }
        return _report_edl3_migrate(args, payload)

    timeline = edl3.Timeline3.from_dict(document)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    payload.update(
        {
            "ok": True,
            "out": str(out_path),
            "changed": True,
            "clips": len(document["clips"]),
            "fps": document["fps"],
            "duration_sec": {
                "from": payload["duration_sec"]["from"],
                "to": round(float(timeline.duration), 6),
            },
        }
    )
    return _report_edl3_migrate(args, payload)


def edl3_versions() -> tuple:
    """Le versioni accettate dal contratto, lette dalla costante unica."""
    from . import edl3

    return edl3.EDL3_VERSIONS


#: Le fasi vere di un render EDL3: quelle che il pannello pubblica e che
#: ``vx serve`` legge da ``progress.json``.
_EDL3_PHASES: tuple = (
    ("compile", "Compilo il documento EDL3."),
    ("render", "Renderizzo la timeline."),
    ("master", "Il master e' scritto."),
)


def _edl3_panel(work: Path, name: str, clips: int):
    """Il pannello del lavoro EDL3, o ``None`` se la vista non si scrive."""
    from . import panel as panel_mod

    try:
        panel = panel_mod.Panel(
            Path(work) / "progress.html",
            prompt=f"EDL3 - {name}",
            clips=int(clips),
            phases=_EDL3_PHASES,
        )
        panel.write()
        return panel
    except Exception:  # noqa: BLE001 - la vista non e' un cancello
        return None


@_session_command("edl3")
def cmd_edl3(args: argparse.Namespace) -> int:
    """Renderizza un documento EDL3 (``EDL3_VERSION``); con ``--live`` pubblica i pezzi."""
    from . import edl3, edl3_render, report as report_io

    session = _session_of(args)
    try:
        timeline = edl3.Timeline3.from_dict(_load_json(args.plan))
    except edl3.EDLError as exc:
        if session is not None:
            session.error(str(exc))
        _out({"ok": False, "error": str(exc)})
        return EXIT_USAGE

    output = Path(args.out)
    work = Path(args.workdir) if args.workdir else output.parent / "work"
    from . import settings as settings_module  # noqa: PLC0415 - only this command

    budget = getattr(args, "effect_budget", None)
    if budget is None:
        budget = settings_module.get(
            "render.effect_budget", edl3.EFFECT_DENSITY_LIMIT
        )
    try:
        budget = int(budget)
    except (TypeError, ValueError):
        budget = edl3.EFFECT_DENSITY_LIMIT
    if budget < 0:
        message = (
            f"--effect-budget must be >= 0 (0 disables the cap), got {budget}"
        )
        if session is not None:
            session.error(message)
        _out({"ok": False, "error": message})
        return EXIT_USAGE
    if session is not None:
        session.set_job(work)
        session.stage("render", 0.0, f"Compilo {Path(args.plan).name}")

    # D17: il job esiste per `vx serve` dall'inizio, non solo quando il render
    # finisce. Lo stesso `panel.Panel` degli altri comandi scrive
    # progress.html/progress.json; il report minimo arriva a lavoro concluso.
    work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    panel = _edl3_panel(work, Path(args.plan).name, len(timeline.clips))
    if panel is not None:
        panel.record(5.0, "compile", elapsed=0.0)
        panel.record(10.0, "render", elapsed=time.monotonic() - started)
    if getattr(args, "watch", False) and panel is not None:
        from . import watch as watch_mod

        url = watch_mod.running(work) or watch_mod.spawn(work)
        if url:
            panel.watch_url = url
            panel.write()
            if not args.json:
                print(f"Lo guardi qui: {url}", flush=True)
        elif not args.json:
            print("La vista non si e' aperta; il lavoro prosegue.", flush=True)

    try:
        report = edl3_render.render(
            timeline,
            output,
            workdir=work,
            audio=args.audio,
            chunks=args.chunks,
            jobs=args.jobs,
            live=bool(args.live),
            effect_budget=int(budget),
        )
    except BaseException as exc:  # noqa: BLE001 - the panel must hear the truth
        if panel is not None:
            panel.stop(f"fermato prima della fine: {type(exc).__name__}: {exc}")
        raise

    master = Path(report.get("output") or output)
    elapsed = time.monotonic() - started
    if panel is not None:
        panel.record(95.0, "master", elapsed=elapsed)
        panel.finish("Render completato", elapsed=elapsed, master=str(master))
    report_io.write(
        {
            "schema": SCHEMA,
            "version": VERSION,
            "tool": _tool_block(),
            "workdir": str(work),
            "master": str(master),
            "timeline_sec": round(float(timeline.duration), 3),
            "edl3": {
                "schema": timeline.version,
                "declared": edl3.EDL3_VERSION,
                "document": str(Path(args.plan).resolve()),
                "clips": len(timeline.clips),
            },
            "render": report,
        },
        work / "report.json",
    )
    if session is not None:
        session.stage("render", 100.0, "Render completato")
        _session_artifact(session, "master", report.get("output") or output)
        if report.get("live"):
            _session_live(session, work)
    if args.json:
        _out(report)
    else:
        print(f"Output: {report['output']}")
        if report.get("live"):
            live = report["live"]
            print(f"Live: {live['ready']}/{live['total']} in {live['directory']}")
    return EXIT_OK


def _auto_panel_dir(args: argparse.Namespace, workdir: Path) -> Path:
    """Dove vive il pannello di ``auto``: ``--panel`` vince sulla workdir risolta.

    Il default e' la **workdir** (accanto a ``plan.json``/``report.json``), non
    la cartella del master: ``vx serve <workdir>`` deve trovare lo stesso
    ``progress.json`` che il lavoro scrive.
    """
    target = getattr(args, "panel", None)
    return Path(str(target)) if target else Path(workdir)


@_session_command("reel")
def cmd_reel(args: argparse.Namespace) -> int:
    """Prompt + cartella -> auto + run log firmato -> consegna o rinomina."""
    from . import reel

    workdir = reel.resolve_workdir(args)
    # Prima di toccare pannello, sessione o run log: se un altro processo tiene
    # il lease della cartella si esce 2 e non si scrive niente.
    lock_mod.assert_free(workdir)
    session = _session_of(args)
    if session is not None:
        try:
            session.set_job(workdir)
        except Exception:  # noqa: BLE001 - il registro non deve fermare il giro
            pass

    report = reel.run(args)
    if getattr(args, "json", False):
        _out(report)
        return EXIT_OK

    run = report.get("run") or {}
    _say(
        "[reel] "
        + ("consegnato" if report.get("delivered") else "NON consegnato")
        + f": {report.get('master')}"
    )
    _say(
        "  stadi firmati: "
        + (", ".join(run.get("signed") or []) or "nessuno")
        + (
            f" | mancanti: {', '.join(run.get('missing') or [])}"
            if run.get("missing")
            else ""
        )
    )
    if run.get("resume", {}).get("enabled"):
        _say(
            "  ripresa: salta "
            + (", ".join(run["resume"].get("skipped") or []) or "nessuno stadio")
            + f" ({run['resume'].get('elapsed_sec')} s)"
        )
    for reason in run.get("failures") or ():
        _say(f"  motivo: {reason}")
    return EXIT_OK


@_session_command("auto")
def cmd_auto(args: argparse.Namespace) -> int:
    """Prompt + folder -> triage -> EDL -> render -> QC -> deliverables -> report."""
    from . import auto
    from . import settings
    from .grammar import GrammarError

    workdir = auto._resolve_workdir(args)
    # Stesso cancello di `reel`: il secondo scrittore esce 2 prima di scrivere
    # progress.html, sessione o qualunque stadio.
    lock_mod.assert_free(workdir)
    session = _session_of(args)
    if session is not None:
        try:
            session.set_job(workdir)
        except Exception:  # noqa: BLE001 - il registro non deve fermare il giro
            pass
        session.stage("triage", 0.0, "Avvio del lavoro")

    # La narrazione e' per chi guarda, il JSON per chi legge: non convivono.
    narrator = None
    panel_dir = None
    panel = None
    if not getattr(args, "json", False):
        from . import narrate

        global _NARRATING
        _NARRATING = True
        target = (
            None
            if getattr(args, "no_panel", False)
            else str(_auto_panel_dir(args, workdir))
        )
        if target:
            from . import panel as panel_mod

            panel_dir = Path(target)
            panel = panel_mod.Panel(
                panel_dir / "progress.html",
                prompt=str(getattr(args, "prompt", "")),
                clips=_media_count(getattr(args, "assets", "")),
                phases=list(narrate.PHASES.items()),
            )
            # Se il master e' fuori dalla workdir, accanto al master resta una
            # copia del solo HTML (comodita'): il json che la vista legge e' uno
            # solo, quello nella workdir.
            out = Path(str(getattr(args, "out", "") or "out.mp4"))
            extra = out.parent / f"{out.stem}-auto" / "progress.html"
            if extra.resolve() != Path(panel.path).resolve():
                panel.copy = extra
            if getattr(args, "watch", False):
                # Il daemon e' uno solo: se e' acceso lo si usa, altrimenti si
                # avvia. Un secondo server per lo stesso lavoro sarebbe una
                # seconda verita' da tenere allineata.
                url = _watch_url()
                if url:
                    panel.watch_url = url
                    print(f"Lo guardi qui: {url}", flush=True)
                else:
                    print("La vista non si e' aperta; il lavoro prosegue.", flush=True)
            panel.write()
        narrator = narrate.Narrator(panel=panel)
        count = _media_count(getattr(args, "assets", ""))
        narrator.header(str(getattr(args, "prompt", "")), count)

    # Il cancello dei font: default dalla configurazione, poi i flag vincono.
    if getattr(args, "font_network", None) is None:
        args.font_network = bool(settings.get("font.network", True)) and (
            auto.font_network_available()
        )
    # ``font_wait``/``plan_wait`` restano ``None`` finche' l'utente non li
    # passa: solo cosi' ``auto`` distingue un'attesa esplicita dal default, e
    # puo' non aspettare nessuno quando non c'e' una vista agganciata.
    if getattr(args, "budget", None) is None:
        args.budget = float(
            settings.get("budget.max_seconds", auto.estimate.BUDGET_DEFAULT)
            or auto.estimate.BUDGET_DEFAULT
        )

    progress_target = narrator
    if session is not None and narrator is not None:

        def progress_target(percent, stage, _narrator=narrator, _session=session):
            _session.stage(str(stage), float(percent))
            _narrator(percent, stage)

    view_url = str(getattr(panel, "watch_url", "") or "") if panel else ""
    # "C'e' qualcuno" non e' "il pannello esiste": serve un URL agganciato con
    # ``--watch`` oppure un client che ha letto lo stato da poco. Senza,
    # i cancelli usano la proposta e lo dichiarano (``by: auto``) subito.
    view_active = auto.view_is_attached(workdir, panel=panel, view_url=view_url)
    try:
        report = auto.run(
            args,
            progress=progress_target,
            progress_file=str(auto.progress_path(args)),
            view_url=view_url,
            view_active=view_active,
        )
    except GrammarError as exc:
        _out(
            {
                "ok": False,
                "errors": [item.details for item in exc.violations],
                "grammar": [item.to_dict() for item in exc.violations],
                "camera_exemptions": getattr(exc, "camera_exemptions", []),
            }
        )
        return EXIT_USAGE

    _session_auto(session, report, panel)

    if getattr(args, "json", False):
        _out(report)
    elif narrator is not None:
        narrator.summary(report)
        # La riga della guardia e' l'ultima parola: un master che esiste non e'
        # un lavoro consegnabile, e chi guarda lo deve sapere senza aprire JSON.
        verdict = auto.guard_line(report.get("guard"))
        if verdict:
            narrator._write(verdict)
    else:
        _log(
            f"[auto] ok: {report['master']} "
            f"({report['timeline_sec']:.3f}s, {report['assets']['selected']} selezionati)"
        )
    return EXIT_OK


_MEDIA_SUFFIXES = frozenset(
    {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".jpg", ".jpeg", ".png", ".webp"}
)


def _media_count(directory: str) -> int:
    """How many media files sit at the top of the folder: cheap, no probing."""
    try:
        root = Path(directory)
    except TypeError:
        return 0
    if not root.is_dir():
        return 0
    return sum(1 for item in root.iterdir() if item.is_file() and item.suffix.lower() in _MEDIA_SUFFIXES)


# --------------------------------------------------------------------------- #
# il daemon della vista: `vx up` / `vx down`
# --------------------------------------------------------------------------- #

#: Il pid file vive nella radice delle sessioni, non nel lavoro: un daemon serve
#: tutte le sessioni (l'ultima, o `?session=ID`) e deve sopravvivere al singolo
#: comando che l'ha avviato.
UP_NAME = "up.json"
UP_PORT = 8100
UP_PORT_TRIES = 20
UP_START_SEC = 30.0


def _up_file() -> Path:
    from . import session as session_mod

    return session_mod.default_root() / UP_NAME


def _read_up() -> Dict[str, Any]:
    """Il contenuto di ``up.json``, senza fidarsi della forma."""
    try:
        data = json.loads(_up_file().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_up(path: Path, port: int, url: str) -> None:
    """Il pid file lo scrive il daemon stesso: il pid e' il suo, non quello del padre."""
    from datetime import datetime, timezone

    payload = {
        "pid": os.getpid(),
        "port": int(port),
        "url": str(url),
        "started_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _pid_alive(pid: Any) -> bool:
    """Vivo? Su Windows ``os.kill(pid, 0)`` non e' un probe valido: si usa OpenProcess."""
    try:
        number = int(pid)
    except (TypeError, ValueError):
        return False
    if number <= 0:
        return False
    if os.name == "nt":
        return _win_pid_alive(number)
    try:
        os.kill(number, 0)
    except PermissionError:
        # Esiste ma non e' nostro: vivo, e comunque mai da terminare.
        return True
    except OSError:
        return False
    return True


def _win_pid_alive(pid: int) -> bool:
    """Vivo senza segnali: OpenProcess + GetExitCodeProcess (STILL_ACTIVE)."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return int(code.value) == 259
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - se non si puo' sapere, non si tocca nulla
        return False


def _answers(port: Any, timeout: float = 0.4) -> bool:
    """La porta risponde con lo stato della vista? (loopback, nessuna rete esterna)."""
    try:
        number = int(port)
    except (TypeError, ValueError):
        return False
    if number <= 0:
        return False
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{number}/api/state", timeout=timeout
        ) as response:
            return 200 <= int(getattr(response, "status", 200)) < 300
    except Exception:  # noqa: BLE001 - una porta chiusa e' la risposta normale
        return False


def _url_of(port: Any) -> str:
    return f"http://127.0.0.1:{int(port)}/"


def _daemon(wait: float = 0.0) -> Dict[str, Any]:
    """Il daemon **vivo**: pid vivo *e* porta che risponde, altrimenti ``{}``.

    Il pid da solo non basta: un numero di processo puo' essere riciclato dal
    sistema, e `vx down` non deve mai fermare un processo che non e' il nostro.
    ``wait`` concede qualche istante a un daemon che sta ancora accendendo:
    senza, un secondo `vx up` lo scambierebbe per morto e ne avvierebbe un altro.
    """
    record = _read_up()
    try:
        pid = int(record.get("pid") or 0)
        port = int(record.get("port") or 0)
    except (TypeError, ValueError):
        return {}
    if not _pid_alive(pid) or not port:
        return {}
    deadline = time.monotonic() + max(0.0, float(wait))
    while not _answers(port, 0.5):
        if time.monotonic() >= deadline:
            return {}
        time.sleep(0.15)
    return {**record, "pid": pid, "port": port, "url": _url_of(port)}


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", int(port)))
        except OSError:
            return False
    return True


def _choose_port(preferred: Optional[int]) -> int:
    """La porta chiesta se e' libera, altrimenti la prima libera dopo; 0 = il sistema.

    Una porta *chiesta* (un riavvio vuole tenersi la sua) si aspetta: il
    processo appena fermato la lascia per qualche istante, e prendere subito la
    successiva cambierebbe l'URL di una vista che l'utente ha gia' aperta. Solo
    se resta occupata dopo l'attesa si scende alla prima libera piu' avanti.
    """
    asked = preferred not in (None, 0)
    first = UP_PORT if not asked else int(preferred)
    if asked:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if _port_free(first):
                return first
            time.sleep(0.2)
    for offset in range(UP_PORT_TRIES):
        candidate = first + offset
        if candidate > 65535:
            break
        if _port_free(candidate):
            return candidate
    return 0


def _console_free_python() -> str:
    """L'interprete da usare per un processo di sfondo, senza finestra.

    Su Windows ``python.exe`` di un virtualenv e' un **lanciatore**: esegue il
    python vero come figlio. Il lanciatore eredita ``CREATE_NO_WINDOW``, ma il
    figlio no, e un eseguibile di console senza console ne riceve una nuova:
    ecco la finestra nera con il percorso del python che compariva a ogni
    ``vx up``. ``pythonw.exe`` non ha sottosistema console e non ne crea mai.
    """
    exe = Path(sys.executable)
    if os.name == "nt":
        windowless = exe.with_name("pythonw.exe")
        if windowless.is_file():
            return str(windowless)
    return str(exe)


def _daemon_command(extra: Sequence[str]) -> List[str]:
    """Come rilanciare questo stesso vx, senza dipendere dal PATH dell'utente.

    ``python vx.py`` (o ``python -m vx.cli`` se il pacchetto e' installato)
    invece del launcher ``vx.exe``: un processo solo, quindi il pid scritto in
    ``up.json`` e' davvero quello del server.
    """
    python = _console_free_python()
    script = Path(__file__).resolve().parents[1] / "vx.py"
    if script.is_file():
        return [python, str(script), *extra]
    return [python, "-m", "vx.cli", *extra]


def _spawn_daemon(port: int) -> Optional[subprocess.Popen]:
    target = _up_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    command = _daemon_command(
        ["serve", "--session", "--port", str(port), "--up", str(target)]
    )
    base = 0
    startupinfo = None
    if os.name == "nt":
        # Staccato dalla console del padre e senza finestra: sopravvive alla fine.
        base = 0x00000008 | 0x08000000
        # Seconda cintura: anche se un interprete decidesse di allocare una
        # console, la finestra nasce nascosta e non ruba il fuoco.
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
    options = dict(
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=os.name != "nt",
        startupinfo=startupinfo,
    )
    if os.name == "nt":
        # CREATE_BREAKAWAY_FROM_JOB: se chi lancia vive dentro un job "kill on
        # close" (terminali, CI, agenti), il daemon ne esce invece di morire con
        # lui. Se il job non lo permette si riprova senza: meglio un daemon che
        # dura quanto il terminale che nessuna vista.
        try:
            return subprocess.Popen(  # noqa: S603 - comando costruito qui
                command, creationflags=base | 0x01000000, **options
            )
        except OSError:
            pass
    try:
        return subprocess.Popen(command, creationflags=base, **options)  # noqa: S603
    except OSError:
        return None


def _code_files() -> List[Path]:
    """I file che il daemon ha in memoria: ``serve.py`` e gli asset della UI."""
    from . import serve as serve_mod

    entry = Path(serve_mod.__file__).resolve()
    files = [entry]
    try:
        files.extend(sorted((entry.parent / "ui").glob("*")))
    except OSError:
        pass
    return files


def _stale_code(record: Mapping[str, Any]) -> Optional[tuple]:
    """``(file, mtime)`` del codice piu' nuovo del daemon, o ``None``.

    Il confronto e' con ``started_utc``: un file toccato **dopo** l'avvio e'
    codice che il processo in memoria non ha mai letto. Solo i file della
    skill, mai l'orologio del sistema: un orologio sfasato non deve far
    riavviare un daemon sano.
    """
    raw = str(record.get("started_utc") or "")
    if not raw:
        return None
    from datetime import datetime, timezone

    try:
        started = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    start = started.timestamp()
    # ``started_utc`` e' troncato al secondo: senza un secondo di tolleranza un
    # file scritto poco prima dell'avvio sembrerebbe "piu' nuovo" del daemon e
    # ogni `vx up` riavvierebbe un processo sano.
    floor = start + 1.0
    newest: Optional[tuple] = None
    for path in _code_files():
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > floor and (newest is None or mtime > newest[1]):
            newest = (path, mtime)
    return newest


def _start_up(preferred: Optional[int] = None) -> Dict[str, Any]:
    """Riusa il daemon vivo, altrimenti lo avvia e aspetta che risponda.

    Prima di riusarlo si guarda il codice: se ``serve.py`` o un file di ``ui/``
    e' piu' nuovo di ``started_utc``, il daemon sta servendo la versione di
    prima. Si ferma **solo il proprio pid** e si riavvia; se non si riesce, lo
    si dice invece di far finta che la vista sia aggiornata.
    """
    running = _daemon(wait=3.0)
    was_running = bool(running)
    if running:
        stale = _stale_code(running)
        if stale is None:
            return {**running, "reused": True}
        from datetime import datetime, timezone

        path, mtime = stale
        stamp = datetime.fromtimestamp(mtime, timezone.utc).replace(microsecond=0)
        _log(
            f"[up] {path.name} e' piu' nuovo del daemon "
            f"(avviato {running.get('started_utc')}, file {stamp.isoformat()}): "
            f"riavvio il pid {running.get('pid')}"
        )
        if _stop_pid(int(running.get("pid") or 0)):
            try:
                _up_file().unlink(missing_ok=True)
            except OSError:
                pass
        else:
            running["stale_code"] = path.name
            running["restart_failed"] = (
                "non riesco a fermare il daemon: sta servendo codice vecchio"
            )
            _log(f"[up] {running['restart_failed']} (pid {running.get('pid')})")
            return {**running, "reused": True}
    else:
        try:
            _up_file().unlink(missing_ok=True)
        except OSError:
            pass
    if was_running and preferred is None:
        # Un riavvio non deve cambiare l'indirizzo: la scheda aperta punta a
        # quella porta, e spostarla su 8101 vuol dire una vista che sparisce.
        preferred = running.get("port")
    child = _spawn_daemon(_choose_port(preferred))
    if child is None:
        return {}
    deadline = time.monotonic() + UP_START_SEC
    while time.monotonic() < deadline:
        record = _read_up()
        try:
            pid = int(record.get("pid") or 0)
            port = int(record.get("port") or 0)
        except (TypeError, ValueError):
            pid, port = 0, 0
        # Il file e' stato rimosso prima dell'avvio: il primo record vivo e
        # che risponde e' il nostro. Il pid non si confronta con ``child.pid``
        # perche' l'interprete di un venv e' un rinvio a un secondo processo.
        if pid and port and _pid_alive(pid) and _answers(port, 0.5):
            if was_running:
                _log(
                    f"[up] daemon riavviato su codice nuovo: pid {pid}, "
                    f"started_utc {record.get('started_utc')}"
                )
            return {**record, "pid": pid, "port": port, "url": _url_of(port),
                    "reused": False}
        if child.poll() is not None:
            return {}
        time.sleep(0.1)
    try:
        child.terminate()
    except OSError:
        pass
    return {}


def _watch_url() -> str:
    """L'URL da dare a ``--watch``: il daemon vivo, o uno appena avviato."""
    return str(_start_up(None).get("url") or "")


def _stop_pid(pid: int) -> bool:
    """Ferma **solo** il pid dato, aspettando che sparisca. True se e' andato."""
    try:
        os.kill(int(pid), signal.SIGTERM)
    except (OSError, SystemError):
        return not _pid_alive(pid)
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                # Senza questo, `taskkill` e' un eseguibile di console: da un
                # processo senza console se ne fa allocare una -> finestra nera
                # che appare e sparisce a ogni `vx down` / riavvio del daemon.
                creationflags=0x08000000,
            )
        except OSError:
            pass
        time.sleep(0.2)
    return not _pid_alive(pid)


def cmd_up(args: argparse.Namespace) -> int:
    """Avvia (o riusa) il daemon della vista di sessione e stampa l'URL."""
    record = _start_up(getattr(args, "port", None))
    if not record:
        _log("[up] il daemon non e' partito: la vista resta chiusa")
        return EXIT_USAGE
    # Il browser si apre **solo** quando il daemon nasce adesso: su un daemon
    # gia' acceso la vista e' gia' davanti all'utente, e ogni `--open`
    # lascerebbe una scheda identica in piu' (succedeva a ogni /reel).
    if getattr(args, "open", False) and not record.get("reused"):
        try:
            import webbrowser

            webbrowser.open(str(record.get("url") or ""))
        except Exception:  # noqa: BLE001 - aprire il browser e' un di piu'
            pass
    # Una riga sola su stdout: due `vx up` di fila devono stampare lo stesso URL.
    print(str(record.get("url") or ""), flush=True)
    return EXIT_OK


def cmd_down(args: argparse.Namespace) -> int:
    """Ferma il daemon di ``vx up``: solo il suo pid, mai processi altrui."""
    record = _read_up()
    path = _up_file()
    try:
        pid = int(record.get("pid") or 0)
        port = int(record.get("port") or 0)
    except (TypeError, ValueError):
        pid, port = 0, 0
    # Si termina solo un pid vivo **e** che risponde su quella porta: se la
    # porta tace il numero potrebbe essere di un altro processo (pid riciclato).
    ours = bool(pid) and _pid_alive(pid) and bool(port) and _answers(port, 0.5)
    stopped = _stop_pid(pid) if ours else False
    if ours and stopped:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and _answers(port, 0.2):
            time.sleep(0.05)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    if ours:
        print(f"daemon fermato (pid {pid}, porta {port})", flush=True)
    elif pid:
        print(f"nessun daemon vivo (pid {pid}): pid file rimosso", flush=True)
    else:
        print("nessun daemon acceso", flush=True)
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    """Local UI: the live panel plus the keyframe approval, on 127.0.0.1."""
    from . import serve

    sessions = bool(getattr(args, "session", False)) or not getattr(args, "dir", None)
    root = Path(args.dir) if getattr(args, "dir", None) else None
    server = serve.Server(root, port=int(args.port), session=sessions).start()
    record: Optional[Path] = None
    raw = getattr(args, "up", None)
    if raw:
        record = Path(str(raw))
        try:
            _write_up(record, server.port, server.url)
        except OSError as exc:
            _log(f"[serve] pid file non scritto ({type(exc).__name__}): {exc}")
            record = None
    _log(f"vx serve: {server.url}  (Ctrl+C per fermare)")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        server.stop()
        if record is not None:
            try:
                current = json.loads(record.read_text(encoding="utf-8-sig"))
                if int(current.get("pid") or 0) == os.getpid():
                    record.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
        return EXIT_OK


def cmd_mcp(args: argparse.Namespace) -> int:
    """Run the stdio MCP server (debug convenience; no flags, the transport is the interface)."""
    from . import mcp_server

    return mcp_server.main([])


def cmd_stack(args: argparse.Namespace) -> int:
    """Due flussi video affiancati: il caso che al browser non conviene."""
    from . import stack as stack_mod

    info = stack_mod.stack(
        args.left,
        args.right,
        args.out,
        width=int(getattr(args, "width", 1080)),
        height=int(getattr(args, "height", 1920)),
        duration=getattr(args, "duration", None),
    )
    if getattr(args, "json", False):
        _out(info)
        return EXIT_OK
    print(f"Affiancati: {info['path']}")
    print(
        f"  {info['width']}x{info['height']} · {info['duration_sec']}s · "
        f"{info['engine']} ({info['filter']})"
    )
    print(f"  audio: {info['audio']}")
    return EXIT_OK


def cmd_scene(args: argparse.Namespace) -> int:
    """Rende una scena con Chromium e pubblica i fotogrammi mentre la costruisce."""
    from . import scenes as scenes_mod

    wanted = scenes_mod.wants(getattr(args, "request", ""))
    if wanted is None:
        raise contract.VxError(
            "scene: la richiesta non nomina una composizione nota "
            "(split screen, confronto prima/dopo, sottopancia)"
        )
    info = scenes_mod.render_frames(
        Path(str(args.workdir)),
        {
            **wanted,
            "name": str(getattr(args, "name", "") or ""),
            "role": str(getattr(args, "role", "") or ""),
            "left": str(getattr(args, "left", "PRIMA") or "PRIMA"),
            "right": str(getattr(args, "right", "DOPO") or "DOPO"),
        },
        frames=int(getattr(args, "frames", 12)),
        width=int(getattr(args, "width", 540)),
        height=int(getattr(args, "height", 960)),
    )
    if getattr(args, "json", False):
        _out(info)
        return EXIT_OK if info.get("engine") == "chromium" else 5
    print(f"Scena: {wanted['id']} ({info.get('engine')})")
    print(f"  fotogrammi: {info.get('frames')} su {info.get('declared_frames')}")
    print(f"  documento : {str(info.get('document_sha256'))[:16]}")
    if info.get("reason"):
        print(f"  motivo    : {info['reason']}")
    elif info.get("live"):
        print(f"  live      : {info['live']}")
    return EXIT_OK if info.get("engine") == "chromium" else 5


def cmd_session(args: argparse.Namespace) -> int:
    """Pota le sessioni vecchie: un comando, non una cartella cancellata a mano."""
    from . import session as session_mod

    data = session_mod.prune_sessions(
        root=getattr(args, "root", None),
        days=float(getattr(args, "days", 7.0)),
        dry_run=bool(getattr(args, "dry_run", False)),
        force=bool(getattr(args, "force", False)),
    )
    if getattr(args, "json", False):
        _out(data)
        return EXIT_OK
    megabytes = 1024 * 1024
    print(f"Sessioni: {data['root']} (piu' vecchie di {data['days']:g} giorni)")
    if data["dry_run"]:
        print(
            f"  da rimuovere: {data['removed_count']} | "
            f"tenute: {data['kept_count']} | non sessioni: {data['foreign_count']}"
        )
        for name in data["removed"][:20]:
            print(f"    {name}")
        if data["removed_count"] > 20:
            print(f"    ... e altre {data['removed_count'] - 20}")
        print("  --dry-run: non ho cancellato niente")
        return EXIT_OK
    print(
        f"  rimosse: {data['removed_count']} | tenute: {data['kept_count']} | "
        f"non sessioni: {data['foreign_count']} | "
        f"liberati {data['freed_bytes'] / megabytes:.1f} MB"
    )
    return EXIT_OK


def cmd_guard(args: argparse.Namespace) -> int:
    """Audit a work folder: was the job really done through vx? Exit 2 if not."""
    from . import guard

    report = guard.audit(args.dir)
    if args.json:
        _out(report)
    else:
        print(guard.to_text(report))
    return int(report["exit_code"])


def cmd_fonts(args: argparse.Namespace) -> int:
    """Audit font licences: cache, ``fetched.json`` and the matrix must agree."""
    from . import fontstore

    action = getattr(args, "fonts_cmd", "")
    if action != "audit":
        print("uso: vx fonts audit [--root DIR] [--matrix FILE] [--json]")
        return 2
    report = fontstore.audit(root=getattr(args, "root", None), matrix_path=getattr(args, "matrix", None))
    if args.json:
        _out(report)
    else:
        print(
            f"font in cache: {report['cache_families']} | "
            f"righe matrice: {report['matrix_rows']} | "
            f"righe fetched: {report['fetched_rows']}"
        )
        print(f"matrice: {report['matrix']}")
        if report["ok"]:
            print("coerente: nessuna incoerenza")
        else:
            for problem in report["problems"]:
                print(f"  - {problem}")
            print(f"{len(report['problems'])} incoerenze")
    return int(report["exit_code"])



def cmd_feedback(args: argparse.Namespace) -> int:
    """Il registro dei giudizi: ``add`` scrive una riga, ``report`` la legge.

    ``add`` prende il contesto dal report del lavoro (o da
    ``feedback_request.json`` se ``auto`` l'ha gia' scritto): chi vota non deve
    ricompilare esito, equilibrio, stima e consuntivo a mano. ``report`` mette
    gli aggregati in italiano e, sotto la soglia dichiarata, dice che la
    correlazione non e' conclusiva invece di lasciarla sembrare una prova.
    """
    from . import feedback

    action = getattr(args, "feedback_cmd", "")
    if action == "add":
        workdir = Path(str(getattr(args, "workdir", "") or ""))
        try:
            rating = feedback.validate_rating(getattr(args, "rating", None))
        except ValueError as exc:
            _log(f"[feedback] {exc}")
            return EXIT_USAGE
        request = feedback.request_context(workdir)
        if not request["context"]:
            _log(f"[feedback] nessun contesto in {workdir}: il voto si registra lo stesso")
        record = feedback.append(
            feedback.make_record(
                job_id=request["job_id"],
                prompt=request["prompt"],
                rating=rating,
                note=str(getattr(args, "note", "") or ""),
                context=request["context"],
            ),
            root=getattr(args, "root", None),
        )
        if getattr(args, "json", False):
            _out(record)
        else:
            print(
                f"Giudizio registrato: {record['rating']}/5 per "
                f"{record['job_id'] or 'il lavoro'} "
                f"(registro {feedback.register_path(getattr(args, 'root', None))})"
            )
        return EXIT_OK
    if action == "report":
        data = feedback.report(root=getattr(args, "root", None))
        if getattr(args, "json", False):
            _out(data)
        else:
            print(feedback.report_text(data))
        return EXIT_OK
    _log("uso: vx feedback add --rating N [--note ...] --workdir DIR | vx feedback report [--json]")
    return EXIT_USAGE


def cmd_learn(args: argparse.Namespace) -> int:
    """L'archivio leggero: quanto pesa, cosa si pota, che soglie sono in vigore.

    ``status`` legge il disco e non tocca niente; ``prune`` comprime i mesi
    vecchi e toglie i corpus piu' vecchi, mai quelli che una policy cita;
    ``policy --show`` stampa le soglie versionate con il motivo dell'ultimo
    cambio. Sempre in italiano, sempre con i MB veri.
    """
    from . import learning

    action = getattr(args, "learn_cmd", "")
    root = getattr(args, "root", None)
    budget = getattr(args, "budget_mb", None) or learning.DEFAULT_BUDGET_MB
    if action == "status":
        data = learning.status(root=root, budget_mb=budget)
        if getattr(args, "json", False):
            _out(data)
            return EXIT_OK
        megabytes = 1024 * 1024
        by_area = data["by_area"]
        print(f"Apprendimento: {data['root']}")
        for area in ("events", "archive", "corpus", "policy"):
            print(f"  {area:<8} {by_area.get(area, 0) / megabytes:7.2f} MB")
        if by_area.get("other"):
            print(f"  {'altro':<8} {by_area['other'] / megabytes:7.2f} MB")
        print(
            f"  {'totale':<8} {data['bytes'] / megabytes:7.2f} MB"
            f" / {data['budget_mb']:.1f} MB"
        )
        oldest = (data["oldest"] or "nessuno")[:10]
        print(
            f"eventi {data['events_count']} | corpus {data['corpus_count']} | "
            f"archivi {data['archive_count']} | piu' vecchio {oldest}"
        )
        # Il registro dei giudizi e' un'altra cosa dall'archivio che si pota:
        # senza questa riga "eventi 0" sembrava "nessuno ha mai votato", e per
        # due giorni si e' creduto che le valutazioni non venissero registrate.
        from . import feedback as feedback_mod  # noqa: PLC0415 - solo su richiesta

        verdicts = feedback_mod.report()
        count = int(verdicts.get("count") or 0)
        if count:
            mean = verdicts.get("mean")
            spread = "  ".join(
                f"{value}:{verdicts['distribution'].get(str(value), 0)}"
                for value in feedback_mod.RATINGS
            )
            print(
                f"giudizi umani {count} | media "
                f"{float(mean):.2f}/5 | {spread}"
                if mean is not None
                else f"giudizi umani {count} | media non calcolabile"
            )
            print(f"  registro: {verdicts.get('root')}")
            if verdicts.get("error"):
                print(f"  registro illeggibile: {verdicts['error']}")
            correlation = verdicts.get("correlation") or {}
            if count < int(correlation.get("min_n") or 0):
                print(
                    f"  sotto {correlation.get('min_n')} valutazioni: la correlazione "
                    "non e' conclusiva, la policy resta v0"
                )
        else:
            print(f"giudizi umani 0 | registro: {verdicts.get('root')}")
        if data["will_prune_next"]:
            print(
                f"prossima potatura: {len(data['will_prune_next'])} elementi, "
                f"{data['next_prune_bytes'] / megabytes:.2f} MB"
                f" (a partire da {data['will_prune_next'][0]})"
            )
        elif data["over_budget"]:
            print("sopra budget ma nulla da potare: restano il lavoro piu' recente e i corpus citati")
        else:
            print("sotto budget: niente da potare")
        return EXIT_OK
    if action == "prune":
        result = learning.prune(root=root, budget_mb=budget)
        if getattr(args, "json", False):
            _out(result)
            return EXIT_OK
        megabytes = 1024 * 1024
        print(
            f"potatura: {result['bytes_before'] / megabytes:.2f} MB -> "
            f"{result['bytes_after'] / megabytes:.2f} MB "
            f"(liberati {result['freed_bytes'] / megabytes:.2f} MB)"
        )
        print(
            f"  mesi compressi: {len(result['archived'])} | "
            f"elementi rimossi: {len(result['removed'])} | "
            f"corpus citati tenuti: {len(result['kept_cited'])}"
        )
        if result["over_budget"]:
            print("  ancora sopra budget: resta solo il recente e cio' che e' citato")
        return EXIT_OK
    if action == "policy":
        data = learning.policy_load(root=root)
        if getattr(args, "json", False):
            _out(data)
            return EXIT_OK
        print(f"policy v{int(data.get('version') or 0)} ({data.get('updated_utc') or 'mai salvata'})")
        if data.get("provisional"):
            print("  provvisoria: nessuna calibrazione definitiva")
        from . import feedback as feedback_mod  # noqa: PLC0415 - solo su richiesta

        verdicts = feedback_mod.report()
        count = int(verdicts.get("count") or 0)
        if count:
            print(
                f"  giudizi nel registro: {count} (media "
                f"{float(verdicts['mean']):.2f}/5)"
                if verdicts.get("mean") is not None
                else f"  giudizi nel registro: {count}"
            )
        thresholds = data.get("thresholds") or {}
        for key in sorted(thresholds):
            print(f"  {key:<22} {thresholds[key]}")
        if data.get("reason"):
            print(f"motivo: {data['reason']}")
        if data.get("feedback_ids"):
            print(f"feedback citati: {len(data['feedback_ids'])}")
        if data.get("rule"):
            print(f"regola: {data['rule']}")
        return EXIT_OK
    if action == "update":
        result = learning.learn_update(
            root=root,
            feedback_root=getattr(args, "feedback_root", None),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
        if getattr(args, "json", False):
            _out(result)
            return EXIT_OK
        if int(result.get("count") or 0) == 0:
            print(result["reason"])
            print(f"  registro: {result['register']}")
            print(f"  policy: v{int(result.get('version') or 0)} invariata")
            return EXIT_OK
        state = "provvisoria" if result["provisional"] else "calibrata"
        if result["written"]:
            print(f"policy v{result['version']} scritta ({state})")
        elif result["unchanged"]:
            print(result["reason"])
            return EXIT_OK
        else:
            print(f"proposta per la v{result['version']} ({state}), non scritta: --dry-run")
        print(f"motivo: {result['reason']}")
        for key in sorted(result["thresholds"]):
            print(f"  {key:<22} {result['thresholds'][key]}")
        if result["feedback_ids"]:
            print(f"giudizi citati: {len(result['feedback_ids'])}")
        for move in result["moves"]:
            print(f"  mossa: {move['threshold']} {move['from']} -> {move['to']}")
        return EXIT_OK
    _log(
        "uso: vx learn status | prune | policy --show | update [--dry-run] "
        "[--root DIR] [--feedback-root DIR] [--budget-mb N] [--json]"
    )
    return EXIT_USAGE


def _apply_settings(args: argparse.Namespace) -> None:
    """Load the cascade before any analysis: defaults -> config -> --settings -> --override."""
    from . import features, settings

    effective = settings.build(
        settings_file=getattr(args, "settings", None),
        overrides=getattr(args, "override", None) or [],
    )
    features.validate_table(effective)
    features.apply_disables(effective, getattr(args, "no_feature", None) or [])
    settings.apply(effective)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="vx.py",
        description="video-editor skill toolkit — senior editor pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version=f"vx {VERSION}")
    sub = ap.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "--settings",
            default=None,
            metavar="FILE",
            help="profilo JSON per-job (sovrascrive il config)",
        )
        p.add_argument(
            "--override",
            action="append",
            default=[],
            metavar="CHIAVE=VALORE",
            help="override puntuale, es. thresholds.jitter_shaky=0.5 (ripetibile)",
        )
        return p

    p = add("doctor", "scansione ambiente e verdetto (Fase 1)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = add("capabilities", "le 25 capability di ffmpeg, cosa manca e cosa si perde")
    p.add_argument("--json", action="store_true", help="referto macchina")
    p.set_defaults(func=cmd_capabilities)

    p = add("probe", "sonda tecnica del file (read-only)")
    p.add_argument("--input", required=True)
    p.add_argument("--report", default=None)
    p.add_argument("--count-frames", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_probe)

    p = add("proxy", "proxy a bassa risoluzione + anteprime (Fase 1)")
    p.add_argument("--input", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--workdir", default=None)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--thumbs", type=int, default=8)
    p.add_argument("--no-previews", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_proxy)

    p = add("analyze", "pre-analisi silenziosa video+audio+volti (Fase 4)")
    p.add_argument("--input", required=True)
    p.add_argument("--report", default=None)
    p.add_argument("--proxy", default=None)
    p.add_argument("--sample-fps", type=float, default=SAMPLE_FPS_VIDEO)
    p.add_argument(
        "--speech",
        action="store_true",
        help="accetta per compatibilita: il parlato e attivo di default",
    )
    p.add_argument("--model", default="tiny")
    p.add_argument("--lang", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_analyze)

    p = add("subs", "trascrizione + sottotitoli dinamici ASS/SRT")
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--style", default="karaoke", choices=["karaoke", "clean", "none"])
    p.add_argument("--lang", default=None)
    p.add_argument("--model", default="small")
    p.add_argument("--aspect", default="9:16")
    p.add_argument("--platform", default="tiktok")
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument(
        "--overlay",
        action="append",
        default=None,
        metavar="PNG",
        help="asset grafico (con alpha) che occupa il quadrante inferiore; "
        "ripetibile. Attiva la misura delle safe area e il cancello 4",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_subs)

    p = add("triage", "briefing interattivo (Fase 5) — esce 3 se serve l'utente")
    p.add_argument("--report", required=True)
    p.add_argument("--brief", default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--markdown", action="store_true")
    p.set_defaults(func=cmd_triage)

    p = add("plan", "matrice decisionale -> EditPlan (Fase 5/6)")
    p.add_argument("--report", required=True)
    p.add_argument("--brief", default=None)
    p.add_argument("--answers", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = add("render", "render da piano (proxy o full)")
    p.add_argument("--plan", required=True)
    p.add_argument("--input", default=None)
    p.add_argument("--precision", default="full", choices=["proxy", "full"])
    p.add_argument("--workdir", default=None)
    p.add_argument("--subs", default=None)
    p.add_argument(
        "--report",
        default=None,
        help="report.json con findings/source: default il sibling del piano",
    )
    p.add_argument(
        "--visual-brief",
        default=None,
        help="brief vx.visual/1.0: inserisce le catene grade+animazione nel grafo",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_render)

    p = add("qc", "controllo qualita sul file renderizzato")
    p.add_argument("--plan", required=True)
    p.add_argument("--render", required=True)
    p.add_argument("--input", default=None)
    p.add_argument("--subs", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_qc)

    p = add("ingest", "protocollo: probe+proxy+pre-analisi+triage (Fase 4/5)")
    p.add_argument("--input", required=True)
    p.add_argument("--brief", default=None)
    p.add_argument("--workdir", default=None)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--thumbs", type=int, default=8)
    p.add_argument("--sample-fps", type=float, default=SAMPLE_FPS_VIDEO)
    p.add_argument(
        "--chunk-sec",
        type=float,
        default=None,
        help="finestra di analisi a chunk in secondi (default: settings)",
    )
    p.add_argument(
        "--autonomous",
        action="store_true",
        help="non chiede mai: registra i default nel report e prosegue",
    )
    p.add_argument(
        "--speech",
        action="store_true",
        help="accetta per compatibilita: il parlato e attivo di default",
    )
    p.add_argument("--model", default="tiny")
    p.add_argument("--lang", default=None)
    p.add_argument(
        "--no-speech",
        action="store_true",
        help="salta la trascrizione durante l'ingest",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ingest)

    p = add("run", "pipeline completa: ingest->plan->subs->render->qc (Fase 7)")
    p.add_argument("--input", required=True)
    p.add_argument("--brief", default=None)
    p.add_argument("--payload", default=None)
    p.add_argument("--prompt", default=None)
    p.add_argument("--answers", default=None)
    p.add_argument("--workdir", default=None)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--thumbs", type=int, default=8)
    p.add_argument("--sample-fps", type=float, default=SAMPLE_FPS_VIDEO)
    p.add_argument(
        "--chunk-sec",
        type=float,
        default=None,
        help="finestra di analisi a chunk in secondi (default: settings)",
    )
    p.add_argument(
        "--autonomous",
        action="store_true",
        help="non chiede mai: usa i default registrati e prosegue",
    )
    p.add_argument(
        "--speech",
        action="store_true",
        help="accetta per compatibilita: il parlato e attivo di default",
    )
    p.add_argument("--model", default="small")
    p.add_argument("--lang", default=None)
    p.add_argument(
        "--no-speech",
        action="store_true",
        help="salta la trascrizione (nessun sottotitolo)",
    )
    p.add_argument("--keep-proxy", action="store_true")
    p.add_argument(
        "--no-feature",
        action="append",
        default=[],
        metavar="NAME",
        help="disattiva una feature dichiarata in vx.features (ripetibile)",
    )
    p.add_argument(
        "--visual-brief",
        default=None,
        help="brief vx.visual/1.0: inserisce le catene grade+animazione nel grafo",
    )
    p.add_argument(
        "--overlay",
        action="append",
        default=None,
        metavar="PNG",
        help="asset grafico (con alpha) che occupa il quadrante inferiore; "
        "ripetibile. Attiva la misura delle safe area e il cancello 4",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_run)

    p = add("deliverables", "matrice di consegna del piano")
    p.add_argument("--plan", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_deliverables)

    p = add("visual", "brief vx.visual/1.0 dalle misure del clip (W2.5)")
    p.add_argument("--source", required=True, help="sorgente del clip")
    p.add_argument("--measurements", required=True, help="JSON con le misure del clip")
    p.add_argument("--delivery", default=None, help="delivery di riferimento (audit)")
    p.add_argument("--out", default=None, help="percorso del brief JSON")
    p.add_argument("--project", default=None, help="project_id del brief")
    p.set_defaults(func=cmd_visual)

    p = add("recipe", "ricette vx.recipe/1: elenca, mostra, valida, crea")
    recipe_sub = p.add_subparsers(dest="recipe_cmd", required=True)
    r = recipe_sub.add_parser("list", help="elenco breve: nome, origine, descrizione")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_recipe_list)
    s = recipe_sub.add_parser("show", help="una ricetta risolta, con i default in chiaro")
    s.add_argument("name", help="nome della ricetta")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_recipe_show)
    v = recipe_sub.add_parser(
        "validate", help="valida tutte le ricette scoperte, o un nome/percorso"
    )
    v.add_argument(
        "targets",
        nargs="*",
        metavar="NAME|FILE",
        help="nomi o file; senza argomenti valida tutte quelle scoperte",
    )
    v.set_defaults(func=cmd_recipe_validate)
    i = recipe_sub.add_parser("init", help="crea una ricetta utente minima e valida")
    i.add_argument("name", help="nome della ricetta (file stem)")
    i.add_argument("--extends", default=None, metavar="BASE", help="ricetta da estendere")
    i.add_argument(
        "--out", default=None, metavar="PATH", help="percorso del file (default: cartella utente)"
    )
    i.add_argument(
        "--force",
        action="store_true",
        help="sovrascrive il file se esiste gia'",
    )
    i.set_defaults(func=cmd_recipe_init)

    p = add(
        "auto",
        "pipeline completa da prompt+cartella: triage->EDL->render->QC->consegna",
    )
    p.add_argument("--prompt", required=True, help="richiesta in linguaggio naturale")
    p.add_argument(
        "--assets", required=True, help="cartella degli asset (sola lettura)"
    )
    p.add_argument("--out", required=True, help="percorso del master")
    p.add_argument(
        "--workdir",
        default=None,
        help="default: <out parent>/<out stem>-auto",
    )
    p.add_argument(
        "--fps",
        type=float,
        default=None,
        help="frame rate della timeline; default: dedotto dal primo video selezionato",
    )
    p.add_argument("--manifest", default=None, help="manifest JSON con ruoli e ancore")
    p.add_argument(
        "--music",
        default=None,
        metavar="FILE",
        help="traccia musicale da leggere (sola lettura): il BPM si misura dalla "
        "traccia, il file diventa il letto musicale sotto il parlato",
    )
    p.add_argument(
        "--bpm",
        type=float,
        default=None,
        metavar="N",
        help="BPM dichiarato: vince sulla traccia; senza --music accende la "
        "griglia anche senza un numero nel prompt",
    )
    p.add_argument(
        "--autonomous",
        action="store_true",
        help="forza la modalita autonoma: nessuna domanda, i default sono registrati",
    )
    p.add_argument(
        "--montage",
        action="store_true",
        help="montaggio sequenziale: ogni video che supera i cancelli diventa a_roll, "
        "in ordine di scoperta; default: un solo a_roll",
    )
    p.add_argument(
        "--fill",
        choices=("crop", "fit", "blur"),
        default="fit",
        help="riempimento della tela: crop (copri e taglia), fit (bande, default), "
        "blur (sfondo sfocato, primo piano integro)",
    )
    p.add_argument(
        "--no-bridge",
        action="store_true",
        help="salta il ponte analisi->assemblaggio: nessun taglio sul silenzio, "
        "nessun gain misurato sul segmento usato",
    )
    p.add_argument(
        "--free",
        action="store_true",
        help="esonera il montaggio dai controlli di coerenza (varieta', "
        "ripetizioni, frame inutilizzabili, apertura, ragione dei tagli); "
        "il report lo dichiara spento, non lo finge verde",
    )
    p.add_argument(
        "--no-coherence",
        action="store_true",
        help="alias di --free: nessun controllo di coerenza, e il report lo dice",
    )
    p.add_argument(
        "--panel",
        metavar="DIR",
        default=None,
        help="dove scrivere progress.html (default: la workdir del lavoro); "
        "la pagina si aggiorna da sola ogni secondo",
    )
    p.add_argument(
        "--no-panel",
        action="store_true",
        help="non scrivere il pannello: solo le righe di testo",
    )
    p.add_argument(
        "--watch",
        action="store_true",
        help="apre la vista del lavoro e stampa l'indirizzo PRIMA del render: "
        "pannello, fotogrammi, approvazione e player del risultato",
    )
    p.add_argument(
        "--font-network",
        dest="font_network",
        action="store_true",
        default=None,
        help="scarica dal catalogo i font proposti (default: rete attiva se "
        "disponibile); un prelievo fallito resta dichiarato, non ferma il job",
    )
    p.add_argument(
        "--no-font-network",
        dest="font_network",
        action="store_false",
        help="solo pool locale: nessun socket per la proposta dei font",
    )
    p.add_argument(
        "--font-wait",
        type=float,
        default=None,
        metavar="SECONDS",
        help="quanto si aspetta una scelta umana nella vista (default: 180); "
        "allo scadere si usa il font proposto e lo si dichiara",
    )
    p.add_argument(
        "--no-font-gate",
        action="store_true",
        help="nessuna attesa: si prende subito il font proposto dalla classifica",
    )
    p.add_argument(
        "--key-gate",
        dest="key_gate",
        action="store_true",
        default=None,
        help="apre il cancello dei fotogrammi chiave anche senza vista "
        "(default: aperto quando c'e' una vista)",
    )
    p.add_argument(
        "--no-key-gate",
        dest="key_gate",
        action="store_false",
        help="nessuna attesa sui fotogrammi: il render parte subito e lo dichiara",
    )
    p.add_argument(
        "--key-wait",
        type=float,
        default=None,
        metavar="SECONDS",
        help="quanto si aspetta l'approvazione dei fotogrammi chiave (default: 180); "
        "allo scadere il render prosegue e il report lo dichiara",
    )
    p.add_argument(
        "--budget",
        type=float,
        default=None,
        metavar="SECONDS",
        help="tetto dichiarato per il lavoro (default: settings budget.max_seconds, "
        "600); la stima si fa prima di partire e, se supera il tetto, si chiede "
        "full o fast",
    )
    p.add_argument(
        "--plan-wait",
        type=float,
        default=None,
        metavar="SECONDS",
        help="quanto si aspetta la scelta full/fast (default: 180); allo scadere "
        "si parte con la versione rapida e il report lo dichiara",
    )
    p.add_argument(
        "--subs",
        action="store_true",
        help=(
            "brucia i sottotitoli dalla trascrizione; senza questo flag non si "
            "scrive nessun testo sopra il video"
        ),
    )
    p.add_argument(
        "--no-subs",
        action="store_true",
        help="compatibilita': i sottotitoli sono gia' spenti di default",
    )
    p.add_argument(
        "--no-feature",
        action="append",
        default=[],
        metavar="NAME",
        help="disattiva una feature dichiarata in vx.features (ripetibile)",
    )
    p.add_argument(
        "--no-guard",
        action="store_true",
        help="salta l'audit di consegna (`vx guard`) sulla workdir: il report lo "
        "dichiara saltato e il lavoro si considera consegnato",
    )
    p.add_argument(
        "--recipe",
        default=None,
        metavar="NAME",
        help="ricetta vx.recipe/1 da applicare (il flag popola args.recipe)",
    )
    p.add_argument("--crf", type=int, default=None)
    p.add_argument("--preset", default=None)
    p.add_argument(
        "--live",
        action="store_true",
        help="registra nella sessione i pezzi live pubblicati dal motore di "
        "render, se il lavoro ne produce (manifest vx.live/1)",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_auto)

    p = add(
        "reel",
        "orchestratore con contratto: 8 stadi firmati, master o NON-PUBBLICABILE",
    )
    p.add_argument("--prompt", required=True, help="richiesta in linguaggio naturale")
    p.add_argument("--assets", required=True, help="cartella degli asset (sola lettura)")
    p.add_argument("--out", required=True, help="percorso del master")
    p.add_argument(
        "--workdir",
        default=None,
        help="default: <out parent>/<out stem>-reel",
    )
    p.add_argument("--music", default=None, metavar="FILE", help="traccia musicale (sola lettura)")
    p.add_argument("--recipe", default=None, metavar="NAME", help="ricetta vx.recipe/1")
    p.add_argument(
        "--resume",
        action="store_true",
        help="riprende dal run.jsonl: salta gli stadi gia' firmati, rifa' i mancanti",
    )
    p.add_argument("--fps", type=float, default=None)
    p.add_argument("--manifest", default=None)
    p.add_argument("--autonomous", action="store_true")
    p.add_argument("--montage", action="store_true")
    p.add_argument("--fill", choices=("crop", "fit", "blur"), default="fit")
    p.add_argument("--no-bridge", action="store_true")
    p.add_argument("--free", action="store_true")
    p.add_argument("--no-coherence", action="store_true")
    p.add_argument("--subs", action="store_true")
    p.add_argument("--no-subs", action="store_true")
    p.add_argument("--no-guard", action="store_true")
    p.add_argument("--font-network", dest="font_network", action="store_true", default=None)
    p.add_argument("--no-font-network", dest="font_network", action="store_false")
    p.add_argument("--font-wait", type=float, default=None)
    p.add_argument("--no-font-gate", action="store_true")
    p.add_argument("--key-gate", dest="key_gate", action="store_true", default=None)
    p.add_argument("--no-key-gate", dest="key_gate", action="store_false")
    p.add_argument("--key-wait", type=float, default=None)
    p.add_argument("--budget", type=float, default=None)
    p.add_argument("--plan-wait", type=float, default=None)
    p.add_argument("--crf", type=int, default=None)
    p.add_argument("--preset", default=None)
    p.add_argument("--no-feature", action="append", default=[], metavar="NAME")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_reel)

    from . import edl3 as edl3_mod  # noqa: PLC0415 - one version, one spelling

    version = edl3_mod.EDL3_VERSION
    p = add(
        "edl3",
        f"EDL 3: valida o renderizza un documento {version} (--live pubblica i pezzi)",
    )
    edl3_sub = p.add_subparsers(dest="edl3_cmd", required=True)
    e3v = edl3_sub.add_parser(
        "validate", help=f"valida un documento {version} senza renderizzare"
    )
    e3v.add_argument("file", help=f"documento {version} (JSON)")
    e3v.add_argument("--json", action="store_true")
    e3v.set_defaults(func=cmd_edl3_validate)
    e3m = edl3_sub.add_parser(
        "migrate",
        help=f"migra un piano vx.edl/2.2 (anche il plan.json di auto) in {version}",
    )
    e3m.add_argument(
        "--plan",
        required=True,
        help="piano vx.edl/2.2 (o il plan.json scritto da `vx auto`)",
    )
    e3m.add_argument("--out", required=True, help=f"documento {version} da scrivere")
    e3m.add_argument("--json", action="store_true")
    e3m.set_defaults(func=cmd_edl3_migrate)
    e3 = edl3_sub.add_parser("render", help="renderizza il documento in --out")
    e3.add_argument("--plan", required=True, help=f"documento {version} (JSON)")
    e3.add_argument("--out", required=True, help="file master da scrivere")
    e3.add_argument("--workdir", default=None, help="cartella di lavoro (chunk, live)")
    e3.add_argument(
        "--audio",
        default="refuse",
        choices=["refuse", "drop", "mix"],
        help="politica audio del compilatore (default: refuse)",
    )
    e3.add_argument(
        "--chunks",
        type=int,
        default=None,
        help="blocchi resumable; con --live il default e' un pezzo ogni ~4 s",
    )
    e3.add_argument("--jobs", type=int, default=1, help="blocchi in parallelo")
    e3.add_argument(
        "--effect-budget",
        type=int,
        default=None,
        help="quanti look della libreria possono iniziare in una finestra di 10 s "
        "(default: render.effect_budget nei settings, 3; 0 toglie il tetto). "
        "Alzalo per un montaggio con molte transizioni",
    )
    e3.add_argument(
        "--live",
        action="store_true",
        help="pubblica ogni blocco appena e' pronto in <workdir>/live/ "
        "(chunk_NNN.mp4 + manifest.json), cosi' il player mostra il video vero",
    )
    e3.add_argument(
        "--watch",
        action="store_true",
        help="apre la vista del lavoro e stampa l'indirizzo PRIMA del render: "
        "vale anche per EDL3, che pubblica progress.json nella workdir",
    )
    e3.add_argument("--json", action="store_true")
    e3.set_defaults(func=cmd_edl3)

    p = add("up", "daemon della vista di sessione: avvia (o riusa) e stampa l'URL")
    p.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"porta preferita (default {UP_PORT}, altrimenti la prima libera)",
    )
    p.add_argument("--open", action="store_true", help="apre la vista nel browser")
    p.set_defaults(func=cmd_up)

    p = add("down", "ferma il daemon di `vx up` (solo il suo pid)")
    p.set_defaults(func=cmd_down)

    p = add("serve", "UI locale: guarda il lavoro e approva i fotogrammi")
    p.add_argument(
        "dir",
        nargs="?",
        default=None,
        help="cartella del lavoro (dove sta progress.json); senza cartella "
        "si serve l'ultima sessione",
    )
    p.add_argument(
        "--session",
        action="store_true",
        help="serve il registro di sessione invece di una cartella di lavoro "
        "(l'ultima, oppure ?session=ID)",
    )
    p.add_argument("--port", type=int, default=0, help="0 = porta libera scelta dal sistema")
    p.add_argument(
        "--up",
        default=None,
        metavar="FILE",
        help="scrive qui il pid file del daemon (usato da `vx up`)",
    )
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser(
        "guard",
        help="audit di una cartella: il lavoro e' passato davvero da vx? (exit 2 se no)",
        description=(
            "Audit read-only di una cartella di lavoro. Sei voci: firma vx, master "
            "fuori dalla pipeline, UI artigianale, pipeline artigianale, progresso "
            "pubblicato, sorgente non modificato. Exit 0 conforme, 2 con violazioni."
        ),
    )
    p.add_argument("dir", help="cartella del lavoro da controllare")
    p.add_argument("--json", action="store_true", help="referto macchina")
    p.set_defaults(func=cmd_guard)

    p = add("edl", "EDL v2.0 multi-sorgente: validate o render")
    edl_sub = p.add_subparsers(dest="edl_cmd", required=True)
    v = edl_sub.add_parser("validate", help="valida un EDL v2.0")
    v.add_argument("--plan", required=True)
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_edl)
    r = edl_sub.add_parser("render", help="staging di N sorgenti, Gate 1 e render")
    r.add_argument("--plan", required=True)
    r.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="ID=PATH",
        help="sorgente dichiarata dall'EDL, ripetibile",
    )
    r.add_argument("--workdir", default=None)
    r.add_argument("--out", required=True)
    r.add_argument("--crf", type=int, default=17)
    r.add_argument("--preset", default="fast")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_edl)

    p = sub.add_parser("mcp", help="server MCP su stdio (JSON-RPC per agenti)")
    p.set_defaults(func=cmd_mcp)

    p = add(
        "stack",
        "due video affiancati (split screen, confronto): lo fa ffmpeg, non il browser",
    )
    p.add_argument("--left", required=True, help="video di sinistra")
    p.add_argument("--right", required=True, help="video di destra")
    p.add_argument("--out", required=True, help="file di uscita")
    p.add_argument("--duration", type=float, default=None, help="durata (default: la piu' corta)")
    p.add_argument("--width", type=int, default=1080, help="larghezza totale (pari)")
    p.add_argument("--height", type=int, default=1920, help="altezza (default 1920)")
    p.add_argument("--json", action="store_true", help="referto macchina")
    p.set_defaults(func=cmd_stack)

    p = add(
        "scene",
        "una composizione complessa (split screen, confronto, sottopancia) resa da Chromium",
    )
    p.add_argument("--request", required=True, help="la richiesta: decide quale scena serve")
    p.add_argument("--workdir", required=True, help="dove pubblicare i fotogrammi (live/chromium)")
    p.add_argument("--frames", type=int, default=12, help="quanti fotogrammi costruire (default 12)")
    p.add_argument("--width", type=int, default=540, help="larghezza di rendering (default 540)")
    p.add_argument("--height", type=int, default=960, help="altezza di rendering (default 960)")
    p.add_argument("--name", default="", help="nome per il sottopancia")
    p.add_argument("--role", default="", help="ruolo per il sottopancia")
    p.add_argument("--left", default="PRIMA", help="etichetta sinistra (confronto)")
    p.add_argument("--right", default="DOPO", help="etichetta destra (confronto)")
    p.add_argument("--json", action="store_true", help="referto macchina")
    p.set_defaults(func=cmd_scene)

    p = add(
        "session",
        "le sessioni della vista: elencale e pota quelle vecchie (le prove ne lasciano molte)",
    )
    session_sub = p.add_subparsers(dest="session_cmd", required=True)
    a = session_sub.add_parser(
        "prune",
        help="rimuove le sessioni piu' vecchie di N giorni (default 7); senza --force non tocca la radice vera",
    )
    a.add_argument("--root", default=None, help="cartella delle sessioni (default: la radice vera)")
    a.add_argument("--days", type=float, default=7.0, help="eta' minima in giorni (default 7)")
    a.add_argument("--dry-run", action="store_true", help="elenca e basta, non cancella")
    a.add_argument(
        "--force",
        action="store_true",
        help="necessario per la radice vera delle sessioni (~/.vx/session)",
    )
    a.add_argument("--json", action="store_true", help="referto macchina")
    a.set_defaults(func=cmd_session)

    p = add("fonts", "licenze dei font: audit cache -> fetched.json -> matrice")
    fonts_sub = p.add_subparsers(dest="fonts_cmd", required=True)
    a = fonts_sub.add_parser(
        "audit",
        help="font in cache senza riga, righe senza file, licenze unknown (exit 2 se incoerente)",
    )
    a.add_argument("--root", default=None, help="radice del font store (default: assets/fonts)")
    a.add_argument("--matrix", default=None, help="matrice da controllare (default: LICENSES_MATRIX.md)")
    a.add_argument("--json", action="store_true", help="referto macchina")
    a.set_defaults(func=cmd_fonts)

    p = add(
        "feedback",
        "il giudizio umano sul lavoro: registralo e leggilo (il codice non cambia)",
    )
    feedback_sub = p.add_subparsers(dest="feedback_cmd", required=True)
    a = feedback_sub.add_parser(
        "add",
        help="registra un voto 1..5 sul lavoro, col contesto del suo report",
    )
    a.add_argument("--rating", type=int, required=True, help="voto da 1 (da buttare) a 5 (pronto)")
    a.add_argument("--note", default="", help="cosa ha funzionato o no, in una riga")
    a.add_argument(
        "--workdir",
        required=True,
        help="cartella del lavoro: il contesto arriva da auto_report.json o feedback_request.json",
    )
    a.add_argument(
        "--root",
        default=None,
        help="registro dei giudizi (default: ~/.vx, o VX_FEEDBACK_ROOT)",
    )
    a.add_argument("--json", action="store_true", help="record scritto, forma macchina")
    a.set_defaults(func=cmd_feedback)
    r = feedback_sub.add_parser(
        "report",
        help="aggregati del registro e correlazione equilibrio/voto, con la cautela",
    )
    r.add_argument(
        "--root",
        default=None,
        help="registro dei giudizi (default: ~/.vx, o VX_FEEDBACK_ROOT)",
    )
    r.add_argument("--json", action="store_true", help="referto macchina")
    r.set_defaults(func=cmd_feedback)

    p = add(
        "learn",
        "l'archivio leggero dei feedback: stato, potatura, policy versionata",
    )
    learn_sub = p.add_subparsers(dest="learn_cmd", required=True)
    s = learn_sub.add_parser("status", help="peso reale per area, conteggi e prossima potatura")
    s.add_argument("--root", default=None, help="archivio (default: ~/.vx/learning, o VX_LEARNING_DIR)")
    s.add_argument("--budget-mb", type=float, default=None, help="budget in MB (default: 20)")
    s.add_argument("--json", action="store_true", help="referto macchina")
    s.set_defaults(func=cmd_learn)
    d = learn_sub.add_parser("prune", help="comprime i mesi vecchi e pota i corpus piu' vecchi")
    d.add_argument("--root", default=None, help="archivio (default: ~/.vx/learning, o VX_LEARNING_DIR)")
    d.add_argument("--budget-mb", type=float, default=None, help="budget in MB (default: 20)")
    d.add_argument("--json", action="store_true", help="referto macchina")
    d.set_defaults(func=cmd_learn)
    y = learn_sub.add_parser("policy", help="mostra la policy corrente e la sua storia")
    y.add_argument("--show", action="store_true", help="stampa la policy (e' il default)")
    y.add_argument("--root", default=None, help="archivio (default: ~/.vx/learning, o VX_LEARNING_DIR)")
    y.add_argument("--json", action="store_true", help="referto macchina")
    y.set_defaults(func=cmd_learn)
    u = learn_sub.add_parser(
        "update", help="impara dai giudizi: soglie proposte, policy scritta o invariata"
    )
    u.add_argument("--root", default=None, help="archivio (default: ~/.vx/learning, o VX_LEARNING_DIR)")
    u.add_argument(
        "--feedback-root",
        default=None,
        help="registro dei giudizi (default: accanto all'archivio, o VX_FEEDBACK_ROOT)",
    )
    u.add_argument("--dry-run", action="store_true", help="mostra le soglie proposte senza scrivere")
    u.add_argument("--json", action="store_true", help="referto macchina")
    u.set_defaults(func=cmd_learn)

    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    # The C++ side of OpenCV writes its own WARN lines on stderr even when the
    # work is fine; merged into a pipeline those lines look like a failure, so a
    # successful ingest could be reported with exit 1. vx's own errors are
    # Python exceptions, not OpenCV chatter: keep only ERROR from the library.
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - older/captured streams
            pass

    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    global _ACTIVE_PROGRESS, _JSON_MODE
    _ACTIVE_PROGRESS = None
    _JSON_MODE = bool(getattr(args, "json", False))
    try:
        _apply_settings(args)
        return int(args.func(args))
    except TriageRequired as exc:
        _stop_active("fermato prima della fine: serve una risposta del triage")
        _out(exc.request)
        return EXIT_NEEDS_INPUT
    except MissingDependency as exc:
        _stop_active(f"fermato: manca una dipendenza ({exc})")
        _log(f"[missing] {exc}")
        return EXIT_MISSING_DEP
    except QCFailure as exc:
        _stop_active("errore: il controllo qualita non passa")
        _out(exc.result)
        return int(getattr(exc, "exit_code", EXIT_QC_FAIL))
    except RenderFailure as exc:
        _stop_active(f"errore: il render non e' riuscito ({exc})")
        _log(f"[render] {exc}")
        return EXIT_RENDER_FAIL
    except VxError as exc:
        _stop_active(f"errore: {exc}")
        _log(f"[error] {exc}")
        return EXIT_USAGE
    except KeyboardInterrupt:
        _stop_active(
            "fermato prima della fine: il lavoro e' stato interrotto",
            claim_master_missing=True,
        )
        _log("[aborted]")
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 - no raw traceback may escape the CLI
        _stop_active(f"errore interno: {type(exc).__name__}: {exc}")
        _log(f"[internal] {type(exc).__name__}: {exc}")
        _log(traceback.format_exc())
        return EXIT_RENDER_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
