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
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping

from . import (
    asset_triage,
    contract,
    decide,
    deliverables,
    edl_plan,
    gates,
    grammar,
    ingest,
    lock,
    probe,
    qc,
    render,
    settings,
)

# The delivery spec the renderer is asked to hit and the QC gate re-measures.
TARGET_LUFS = -14.0
TARGET_TP = -1.0


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


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


def _resolve_workdir(args: Any) -> Path:
    explicit = getattr(args, "workdir", None)
    if explicit:
        return Path(explicit)
    out = Path(args.out)
    return out.parent / f"{out.stem}-auto"


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


def _fps(
    args: Any,
    selected: List[Mapping[str, Any]],
    assets_by_id: Mapping[str, asset_triage.AssetInfo],
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

    if any(entry.get("type") == "image" for entry in selected):
        raise contract.VxError(
            "serve --fps: il pool contiene immagini e nessun video da cui dedurre "
            "il frame rate"
        )
    raise contract.VxError(
        "serve --fps: nessun video selezionato da cui dedurre il frame rate"
    )


def _timeline_est(
    selected: List[Mapping[str, Any]],
    assets_by_id: Mapping[str, asset_triage.AssetInfo],
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
        raise contract.VxError(
            "timeline stimata non positiva: nessun a_roll utilizzabile"
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


def _source_hashes(assets: List[asset_triage.AssetInfo]) -> Dict[str, str]:
    return {asset.asset_id: _sha256(Path(asset.path)) for asset in assets}


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


def _run_locked(args: Any, workdir: Path) -> Dict[str, Any]:
    assets = _discover(args)
    assets_by_id = {asset.asset_id: asset for asset in assets}
    source_sha = _source_hashes(assets)

    try:
        payload = decide.parse_and_decide(
            args.prompt,
            asset_pool=assets,
            force_mode="autonomous" if getattr(args, "autonomous", False) else None,
        )
    except decide.AmbiguousContractError as exc:
        raise contract.VxError(f"prompt ambiguo in STRICT: {exc}") from exc

    block = payload.get("asset_triage")
    if not isinstance(block, Mapping) or not block:
        raise contract.VxError("payload senza asset_triage: triage non eseguito")
    selected = list(block.get("selected_assets") or [])
    rejected = list(block.get("rejected_assets") or [])

    fps = _fps(args, selected, assets_by_id)
    timeline_est = _timeline_est(selected, assets_by_id)

    sources_map: Dict[str, str] = {}
    for entry in selected:
        asset_id = entry.get("asset_id")
        asset = assets_by_id.get(asset_id)
        if asset is None:
            raise contract.VxError(f"asset selezionato {asset_id!r} assente dal pool")
        sources_map[asset_id] = asset.path

    specs = _specs(selected, assets_by_id, fps, timeline_est)
    _log(f"[auto] staging {len(sources_map)} sorgenti a {fps:g} fps")
    staged = _stage(sources_map, specs, workdir / "stage_00")
    source_refs, media_info_map = _source_refs(staged)

    plan = contract.payload_to_edl_plan(payload, source_refs, media_info_map, fps=fps)
    errors = plan.validate()
    if errors:
        raise contract.VxError("EDL non valido: " + "; ".join(errors))
    (workdir / "plan.json").write_text(
        json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    exemptions = grammar.camera_exemptions(plan)
    try:
        grammar.assert_cinematic_rules(plan)
    except grammar.GrammarError as exc:
        # The CLI reports the violations; the exemptions travel with them so an
        # undeclared camera is never silently dropped from the failure report.
        exc.camera_exemptions = exemptions  # type: ignore[attr-defined]
        raise

    gate1 = gates.validate_gate1_durations(plan, media_info_map, fps=fps)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    staged_paths = {
        asset_id: str(_staged_path(result)) for asset_id, result in staged.items()
    }
    _log(f"[auto] render {plan.duration_sec:.3f}s -> {out}")
    render.render_edl(
        plan,
        staged_paths,
        out,
        workdir=workdir / "render",
        loudness=(TARGET_LUFS, TARGET_TP),
        crf=int(getattr(args, "crf", None) or settings.get("render.crf", 18)),
        preset=str(
            getattr(args, "preset", None) or settings.get("render.preset", "medium")
        ),
    )

    qc_result = qc.audit_master(out, target_lufs=TARGET_LUFS, target_tp=TARGET_TP)

    produced = deliverables.produce(plan, out, workdir / "out", workdir=workdir)

    _assert_sources_unchanged(assets, source_sha)

    report: Dict[str, Any] = {
        "ok": True,
        "payload_version": str(payload.get("contract_version") or contract.VERSION),
        "edl_version": str(plan.contract_version),
        "fps": float(fps),
        "timeline_sec": float(plan.duration_sec),
        "assets": {
            "total": len(assets),
            "selected": len(selected),
            "rejected": len(rejected),
        },
        "selected": [
            {
                "asset_id": entry.get("asset_id"),
                "role": entry.get("role"),
                "type": entry.get("type"),
                "target_duration_sec": entry.get("target_duration_sec"),
                "treatment": entry.get("treatment"),
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
        "plan_notes": list(getattr(plan, "notes", ()) or ()),
        "camera_exemptions": exemptions,
        "gate1": gate1.to_dict(),
        "qc": qc_result,
        "deliverables": produced,
        "master": str(out.resolve()),
        "workdir": str(workdir.resolve()),
        "source_sha": source_sha,
        "sources_unchanged": True,
    }
    (workdir / "auto_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def run(args: Any) -> Dict[str, Any]:
    """Execute the whole pipeline under the workdir lock and return the report."""
    out = Path(args.out)
    assets_dir = Path(args.assets)
    workdir = _resolve_workdir(args)

    if _inside(out, assets_dir):
        raise contract.VxError(
            "--out non puo' stare dentro --assets: la cartella sorgente non si scrive"
        )
    if _inside(workdir, assets_dir):
        raise contract.VxError(
            "--workdir non puo' stare dentro --assets: la cartella sorgente non si scrive"
        )

    workdir.mkdir(parents=True, exist_ok=True)
    beat = lock.acquire(
        workdir,
        stage="auto",
        heartbeat_seconds=float(settings.get("lock.heartbeat_seconds", 30.0)),
    )
    try:
        return _run_locked(args, workdir)
    finally:
        lock.release(beat)
