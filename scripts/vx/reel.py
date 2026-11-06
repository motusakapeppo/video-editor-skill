"""vx.reel — one verb, eight signatures, one truth about delivery.

``vx reel`` is an orchestrator, not a second engine: it drives ``vx.auto`` and
then signs the eight stages of the contract in ``<workdir>/.vx/run.jsonl`` with
the value the engine measured and the threshold it had to pass. A stage that
did not measure its number, or measured the wrong one, is a ``fail`` event in
Italian with the numbers in the reason — and the master is renamed
``NON-PUBBLICABILE-<name>.mp4`` so the file itself cannot be mistaken for a
deliverable.

``--resume`` trusts only the signatures already on disk: when a finished
``auto_report.json`` is there the missing stages are signed from its measured
values (no second render); otherwise the engine runs again for the part that
never produced a report. A corrupt log stops the resume with
``RUN_LOG_CORRUPT`` and the line number.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from . import auto, doctor, probe, runlog
from .contract import VxError

__all__ = ["STAGES", "run", "resolve_workdir", "measure_stages"]

#: The fixed contract, re-exported so callers name one table only.
STAGES: Tuple[str, ...] = runlog.STAGES

GATES_MAX_SEC = 5.0
LOUDNESS_TOLERANCE_LU = 1.5
CONTRAST_MIN = 4.5
#: ``motion.cuts_per_10s`` has no contract threshold of its own (the docs say so
#: explicitly), so ``reel`` declares the floor it will not go below: at least one
#: cut every ten seconds. A single static shot is not a reel.
MIN_CUT_DENSITY = 1.0


def resolve_workdir(args: Any) -> Path:
    explicit = getattr(args, "workdir", None)
    if explicit:
        return Path(explicit)
    out = Path(args.out)
    return out.parent / f"{out.stem}-reel"


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _fmt(value: float, digits: int = 3) -> str:
    return f"{float(value):.{digits}f}"


def _master_of(report: Mapping[str, Any]) -> Optional[Path]:
    raw = report.get("master") if isinstance(report, Mapping) else None
    if not raw:
        return None
    target = Path(str(raw))
    if target.is_file():
        return target
    # A previous ``reel`` run may already have renamed it.
    sibling = target.with_name(f"NON-PUBBLICABILE-{target.name}")
    return sibling if sibling.is_file() else None


def _measured(report: Mapping[str, Any], workdir: Path) -> Dict[str, Any]:
    """The engine's measured numbers, with a fallback for older reports."""
    block = report.get("measured")
    if isinstance(block, Mapping):
        return dict(block)
    gate = report.get("gates") if isinstance(report.get("gates"), Mapping) else {}
    qc = report.get("qc") if isinstance(report.get("qc"), Mapping) else {}
    verification = (
        report.get("verification")
        if isinstance(report.get("verification"), Mapping)
        else {}
    )
    contrast = None
    for item in verification.get("checks") or ():
        if isinstance(item, Mapping) and str(item.get("id")) == "contrast":
            measured = item.get("measured")
            if isinstance(measured, Mapping):
                contrast = _num(measured.get("ratio"))
            break
    clips = 0
    plan_path = workdir / "plan.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        for track in (plan.get("tracks") or {}).get("video") or ():
            clips += len(track.get("clips") or ())
    except (OSError, ValueError):
        pass
    return {
        "duration_sec": _num(report.get("timeline_sec")),
        "requested_duration_sec": _num(report.get("duration_requested_sec")),
        "lufs": _num((qc.get("measured") or {}).get("i")),
        "contrast": contrast,
        "clips": clips,
        "sources_measured": len(report.get("source_sha") or {}),
        "assets_used": _num((report.get("assets") or {}).get("selected")),
        "gates_wait_sec": _num(gate.get("total_waited_sec")),
    }


def _contrast_reason(value: Optional[float]) -> str:
    if value is None:
        return (
            "contrasto non misurato: nessun titolo leggibile sul fotogramma "
            f"campionato, serve {CONTRAST_MIN:.1f}"
        )
    if value < CONTRAST_MIN:
        return f"titolo illeggibile: contrasto {value:.2f}, serve {CONTRAST_MIN:.1f}"
    return f"contrasto {value:.2f} >= {CONTRAST_MIN:.1f}"


def measure_stages(
    report: Mapping[str, Any],
    workdir: Path,
    *,
    doctor_report: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """One event per stage, with the measured value and the threshold.

    The value is what the engine measured, never "done": a report without the
    number produces ``ok: false`` and a reason naming the missing measurement.
    """
    data = _measured(report, workdir)
    master = _master_of(report)
    duration = _num((data or {}).get("duration_sec"))
    if master is not None:
        try:
            duration = float(probe.probe(master).duration)
        except Exception:  # noqa: BLE001 - a missing measure is a failed stage
            pass
    requested = _num(data.get("requested_duration_sec"))
    fps = _num(report.get("fps")) or 30.0
    frame = 1.0 / fps if fps > 0 else 0.0
    target_lufs = _num((report.get("loudness_target") or {}).get("lufs"))
    lufs = _num(data.get("lufs"))
    contrast = _num(data.get("contrast"))
    clips = int(data.get("clips") or 0)
    sources = int(data.get("sources_measured") or 0)
    assets_used = int(data.get("assets_used") or 0)
    gates_wait = _num(data.get("gates_wait_sec"))

    events: Dict[str, Dict[str, Any]] = {}

    verdict = str((doctor_report or {}).get("verdict") or "")
    doctor_ok = bool(verdict) and verdict != "broken"
    events["doctor"] = {
        "contract": "verdetto dell'ambiente non 'broken'",
        "value": verdict or "n/d",
        "threshold": None,
        "ok": doctor_ok,
        "why": (
            f"verdetto: {verdict or 'non misurato'}"
            + (
                ""
                if doctor_ok
                else ": ffmpeg/libx264 non utilizzabile, nessun lavoro puo' partire"
            )
        ),
    }

    events["ingest"] = {
        "contract": "asset usati >= 1",
        "value": assets_used,
        "threshold": None,
        "ok": assets_used >= 1,
        "why": (
            f"{assets_used} asset usati nella timeline"
            if assets_used >= 1
            else "nessun asset usato: la timeline non esiste"
        ),
    }

    events["analyze"] = {
        "contract": "sorgenti misurate >= 1",
        "value": sources,
        "threshold": None,
        "ok": sources >= 1,
        "why": (
            f"{sources} sorgenti misurate"
            if sources >= 1
            else "nessuna sorgente misurata: analisi assente"
        ),
    }

    density = (clips / duration * 10.0) if duration and duration > 0 else 0.0
    decide_ok = duration is not None and duration > 0 and density >= MIN_CUT_DENSITY
    events["decide"] = {
        "contract": f"densita' di taglio >= {MIN_CUT_DENSITY:.1f} tagli/10s",
        "value": clips,
        "threshold": MIN_CUT_DENSITY,
        "ok": decide_ok,
        "why": (
            f"{clips} clip, densita' {density:.2f} tagli/10s "
            f">= {MIN_CUT_DENSITY:.1f}"
            if decide_ok
            else (
                f"{clips} clip, densita' {density:.2f} tagli/10s "
                f"< {MIN_CUT_DENSITY:.1f}: montaggio troppo lento"
                if duration is not None and duration > 0
                else f"{clips} clip ma durata 0: densita' non misurabile"
            )
        ),
    }

    gates_ok = gates_wait is not None and gates_wait <= GATES_MAX_SEC
    events["gates"] = {
        "contract": f"attesa totale <= {GATES_MAX_SEC:.1f} s",
        "value": gates_wait if gates_wait is not None else "n/d",
        "threshold": GATES_MAX_SEC,
        "ok": gates_ok,
        "why": (
            f"{gates_wait:.1f} s di attesa totale (limite {GATES_MAX_SEC:.1f} s)"
            if gates_ok
            else (
                f"attesa {gates_wait:.1f} s > {GATES_MAX_SEC:.1f} s: cancelli "
                "aperti senza che il lavoro fosse autonomo"
                if gates_wait is not None
                else "attesa non misurata: il report non dichiara i cancelli"
            )
        ),
    }

    if duration is None:
        render_ok = False
        render_why = "durata master non misurata: il master non esiste o non si apre"
        render_value: Any = "n/d"
    elif requested is None:
        render_ok = duration > 0
        render_why = f"durata master {_fmt(duration)} s; durata non richiesta, nessuna soglia"
        render_value = round(duration, 6)
    else:
        render_ok = abs(duration - requested) <= frame + 1e-6
        render_value = round(duration, 6)
        render_why = (
            f"durata master {_fmt(duration)} s, richiesti {_fmt(requested)} s "
            f"(+/- 1 frame = {_fmt(frame)} s)"
            + ("" if render_ok else ": fuori tolleranza")
        )
    events["render"] = {
        "contract": "durata master = richiesta +/- 1 frame",
        "value": render_value,
        "threshold": round(requested, 6) if requested is not None else None,
        "ok": render_ok,
        "why": render_why,
    }

    qc_ok = (
        lufs is not None
        and target_lufs is not None
        and abs(lufs - target_lufs) <= LOUDNESS_TOLERANCE_LU
    )
    events["qc"] = {
        "contract": f"LUFS = target +/- {LOUDNESS_TOLERANCE_LU:.1f}",
        "value": round(lufs, 3) if lufs is not None else "n/d",
        "threshold": round(target_lufs, 3) if target_lufs is not None else None,
        "ok": qc_ok,
        "why": (
            f"{lufs:.2f} LUFS, target {target_lufs:.2f} +/- "
            f"{LOUDNESS_TOLERANCE_LU:.1f} LU"
            if qc_ok
            else (
                f"loudness {lufs:.2f} LUFS fuori target {target_lufs:.2f} +/- "
                f"{LOUDNESS_TOLERANCE_LU:.1f} LU"
                if lufs is not None and target_lufs is not None
                else "LUFS non misurati sul master"
            )
        ),
    }

    deliver_ok = contrast is not None and contrast >= CONTRAST_MIN
    events["deliver"] = {
        "contract": f"contrasto >= {CONTRAST_MIN:.1f}",
        "value": round(contrast, 3) if contrast is not None else "n/d",
        "threshold": CONTRAST_MIN,
        "ok": deliver_ok,
        "why": _contrast_reason(contrast),
    }
    return events


def _load_report(workdir: Path) -> Optional[Dict[str, Any]]:
    target = workdir / "auto_report.json"
    try:
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _report_is_finished(report: Optional[Mapping[str, Any]]) -> bool:
    """A report is reusable for resume only after the engine decided the outcome."""
    if not isinstance(report, Mapping):
        return False
    if report.get("outcome") not in ("delivered", "failed"):
        return False
    if not isinstance(report.get("verification"), Mapping):
        return False
    return _master_of(report) is not None


def _auto_args(args: Any, workdir: Path) -> Any:
    """The same ``args`` surface ``vx auto`` reads, with the reel defaults."""
    values = dict(vars(args))
    values.update(
        {
            "workdir": str(workdir),
            "subs": bool(getattr(args, "subs", False)),
            "no_subs": bool(getattr(args, "no_subs", False)),
            "montage": bool(getattr(args, "montage", False)),
            "fill": str(getattr(args, "fill", "fit") or "fit"),
            "fps": getattr(args, "fps", None),
            "manifest": getattr(args, "manifest", None),
            "music": getattr(args, "music", None),
            "recipe": getattr(args, "recipe", None),
            "autonomous": bool(getattr(args, "autonomous", False)),
            "no_bridge": bool(getattr(args, "no_bridge", False)),
            "free": bool(getattr(args, "free", False)),
            "no_coherence": bool(
                getattr(args, "free", False) or getattr(args, "no_coherence", False)
            ),
            "no_guard": bool(getattr(args, "no_guard", False)),
            "crf": getattr(args, "crf", None),
            "preset": getattr(args, "preset", None),
            "no_feature": list(getattr(args, "no_feature", None) or []),
            "font_network": getattr(args, "font_network", None),
            "font_wait": getattr(args, "font_wait", None),
            "no_font_gate": bool(getattr(args, "no_font_gate", False)),
            "key_gate": getattr(args, "key_gate", None),
            "no_key_gate": bool(getattr(args, "no_key_gate", False)),
            "key_wait": getattr(args, "key_wait", None),
            "budget": getattr(args, "budget", None),
            "plan_wait": getattr(args, "plan_wait", None),
            "panel": getattr(args, "panel", None),
            "no_panel": True,
            "watch": False,
            "live": False,
            "json": bool(getattr(args, "json", False)),
            "_vx_session": None,
        }
    )
    from . import settings

    if values["font_network"] is None:
        values["font_network"] = bool(settings.get("font.network", True)) and (
            auto.font_network_available()
        )
    if values["budget"] is None:
        values["budget"] = float(
            settings.get("budget.max_seconds", auto.estimate.BUDGET_DEFAULT)
            or auto.estimate.BUDGET_DEFAULT
        )
    return _Namespace(values)


class _Namespace:
    def __init__(self, values: Mapping[str, Any]) -> None:
        self.__dict__.update(values)

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"ReelArgs({sorted(self.__dict__)})"


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _sign_stage(workdir: Path, stage: str, event: Mapping[str, Any]) -> Dict[str, Any]:
    return runlog.append(
        workdir,
        stage=stage,
        event="sign" if event.get("ok") else "fail",
        contract=str(event.get("contract") or ""),
        value=event.get("value"),
        threshold=event.get("threshold"),
        ok=bool(event.get("ok")),
        why=str(event.get("why") or ""),
    )


def _rename_for_failure(report: Dict[str, Any]) -> Optional[Path]:
    master = _master_of(report)
    if master is None:
        return None
    if master.name.startswith("NON-PUBBLICABILE-"):
        return master
    target = master.with_name(f"NON-PUBBLICABILE-{master.name}")
    try:
        os.replace(master, target)
    except OSError:
        return None
    report["master_original"] = str(master.resolve())
    report["master"] = str(target.resolve())
    report["deliverable"] = str(target.resolve())
    return target


def run(args: Any) -> Dict[str, Any]:
    """Run (or resume) the job, sign the eight stages, decide delivery."""
    started = time.time()
    workdir = resolve_workdir(args)
    workdir.mkdir(parents=True, exist_ok=True)
    resume = bool(getattr(args, "resume", False))
    signed_before: Set[str] = set()
    if resume:
        try:
            runlog.read(workdir)
        except runlog.RunLogCorrupt as exc:
            raise VxError(str(exc)) from exc
        signed_before = runlog.signed(workdir)

    doctor_report: Optional[Mapping[str, Any]] = None
    report: Optional[Dict[str, Any]] = None

    if resume:
        candidate = _load_report(workdir)
        if _report_is_finished(candidate):
            report = dict(candidate)
        if "doctor" not in signed_before:
            doctor_report = doctor.scan()
    else:
        doctor_report = doctor.scan()

    # Doctor is signed before the engine: it either works or the job cannot start.
    if "doctor" not in signed_before:
        events = measure_stages(
            report or {}, workdir, doctor_report=doctor_report
        )
        _sign_stage(workdir, "doctor", events["doctor"])

    auto_args = _auto_args(args, workdir)
    if report is None:
        try:
            report = auto.run(
                auto_args,
                progress=None,
                progress_file=str(auto.progress_path(auto_args)),
                view_url="",
                view_active=False,
            )
        except BaseException:
            # Whatever the engine raises, the log must not pretend later stages
            # happened: sign the ones that never produced a value as failures.
            try:
                present = runlog.signed(workdir)
            except runlog.RunLogCorrupt:
                present = set()
            if "doctor" not in present:
                present.add("doctor")
            for stage in STAGES:
                if stage in present:
                    continue
                runlog.append(
                    workdir,
                    stage=stage,
                    event="fail",
                    contract="stadio non misurato",
                    value="n/d",
                    threshold=None,
                    ok=False,
                    why=f"lavoro interrotto prima di misurare {stage}",
                )
            raise

    if not isinstance(report, dict):
        report = dict(report)

    if doctor_report is None and "doctor" not in signed_before:
        doctor_report = doctor.scan()
    events = measure_stages(report, workdir, doctor_report=doctor_report)

    signed_now: List[str] = []
    failures: List[str] = []
    skipped: List[str] = sorted(signed_before) if resume else []
    completed: Set[str] = set(signed_before)
    if "doctor" not in completed:
        # Signed above, before the engine started.
        completed.add("doctor")
    for stage in STAGES:
        if stage in completed:
            continue
        event = events[stage]
        _sign_stage(workdir, stage, event)
        if event.get("ok"):
            signed_now.append(stage)
            completed.add(stage)
        else:
            failures.append(f"{stage}: {event.get('why')}")

    final_stages = completed
    delivered = all(stage in final_stages for stage in STAGES) and not failures
    report["delivered"] = delivered
    report["outcome"] = "delivered" if delivered else "failed"
    report["outcome_suggested"] = "done" if delivered else "failed"

    renamed: Optional[Path] = None
    if not delivered:
        renamed = _rename_for_failure(report)
        if renamed is None:
            failures.append("master non rinominabile: il file consegnato non esiste")
    else:
        master = _master_of(report)
        if master is not None:
            report["deliverable"] = str(master.resolve())

    if failures:
        existing = [str(item) for item in report.get("failures") or ()]
        for reason in failures:
            if reason not in existing:
                existing.append(reason)
        report["failures"] = existing

    log_path = runlog.path(workdir)
    try:
        log_records = runlog.read(workdir)
    except runlog.RunLogCorrupt:
        log_records = []
    report["run"] = {
        "schema": runlog.SCHEMA_VERSION,
        "log": str(log_path.resolve()),
        "events": len(log_records),
        "signed": [stage for stage in STAGES if stage in final_stages],
        "missing": [stage for stage in STAGES if stage not in final_stages],
        "failures": list(failures),
        "engine_failures": [
            str(item) for item in report.get("failures") or () if str(item) not in failures
        ],
        "resume": {
            "enabled": resume,
            "skipped": skipped,
            "signed_now": signed_now,
            "skipped_seconds": round(
                sum(
                    float((report.get("timings") or {}).get(stage) or 0.0)
                    for stage in skipped
                ),
                3,
            ),
            "elapsed_sec": round(time.time() - started, 3),
        },
    }
    report["reel"] = {
        "outcome": report["outcome"],
        "delivered": delivered,
        "renamed": str(renamed.resolve()) if renamed is not None else None,
        "log": str(log_path.resolve()),
    }

    _write_report(workdir / "reel_report.json", report)
    # The page and ``vx serve`` read ``auto_report.json``: the outcome there must
    # be the one the log supports, not the engine's provisional one.
    _write_report(workdir / "auto_report.json", report)
    return report
