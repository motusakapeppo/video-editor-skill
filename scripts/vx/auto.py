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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

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


def _notify(
    progress: Optional[Callable[[float, str], None]],
    percent: float,
    stage: str,
) -> None:
    """Call the observer, swallowing anything it raises: a broken observer must
    never break a render."""
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
        discarded = ", ".join(
            f"{entry.get('asset_id')}={entry.get('rejection_reason')}"
            for entry in rejected
        )
        detail = f"; scarti: {discarded}" if discarded else ""
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


def _bridge_plan(plan: Any, *, captions: bool = True) -> Any:
    """Run the bridge: analyses and transcripts per a_roll source, failures declared.

    A transcription is attempted only when ``captions`` is on. ``subs.transcribe``
    never raises, but it reports a skip as ``skipped_reason``; both that and any
    exception are handed to ``bridge.build`` as a value, which turns them into a
    ``transcript_failed`` declaration instead of a dead run.
    """
    from . import audio, bridge, subs

    analyses: Dict[str, Any] = {}
    transcripts: Dict[str, Any] = {}
    sources = _a_roll_sources(plan)
    for source_id in sources:
        ref = plan.sources.get(source_id)
        path = str(getattr(ref, "path", "") or "")
        try:
            analyses[source_id] = audio.analyze_audio(path)
        except Exception as exc:  # noqa: BLE001 - the bridge turns it into a declaration
            analyses[source_id] = exc
        if not captions:
            continue
        try:
            transcript = subs.transcribe(path)
            reason = str(getattr(transcript, "skipped_reason", "") or "")
            if reason:
                raise RuntimeError(reason)
            transcripts[source_id] = transcript
        except Exception as exc:  # noqa: BLE001 - the bridge declares it
            transcripts[source_id] = exc
    return bridge.build(
        plan,
        analyses,
        target_lufs=TARGET_LUFS,
        loudness_spans=audio.loudness_spans,
        transcripts=transcripts if captions else None,
    )


def _run_locked(
    args: Any,
    workdir: Path,
    *,
    progress: Optional[Callable[[float, str], None]] = None,
    progress_file: Optional[str] = None,
    montage: bool = False,
    fill: str = "fit",
) -> Dict[str, Any]:
    assets = _discover(args)
    assets_by_id = {asset.asset_id: asset for asset in assets}
    source_sha = _source_hashes(assets)
    fill_mode = _fill_mode(fill)
    montage_mode = bool(montage)

    try:
        payload = decide.parse_and_decide(
            args.prompt,
            asset_pool=assets,
            force_mode="autonomous" if getattr(args, "autonomous", False) else None,
            montage=montage_mode,
        )
    except decide.AmbiguousContractError as exc:
        raise contract.VxError(f"prompt ambiguo in STRICT: {exc}") from exc

    block = payload.get("asset_triage")
    if not isinstance(block, Mapping) or not block:
        raise contract.VxError("payload senza asset_triage: triage non eseguito")
    selected = list(block.get("selected_assets") or [])
    rejected = list(block.get("rejected_assets") or [])
    _notify(progress, 5.0, "triage")

    fps = _fps(args, selected, assets_by_id)
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
    staged = _stage(sources_map, specs, workdir / "stage_00")
    source_refs, media_info_map = _source_refs(staged)
    _notify(progress, 20.0, "ingest")

    plan = contract.payload_to_edl_plan(payload, source_refs, media_info_map, fps=fps)
    errors = plan.validate()
    if errors:
        raise contract.VxError("EDL non valido: " + "; ".join(errors))

    bridge_summary: Dict[str, Any] = {
        "enabled": False,
        "cuts": 0,
        "gained_clips": 0,
        "not_applied": 0,
        "captions": 0,
        "subtitles": None,
        "report": None,
    }
    captions_ass: Optional[str] = None
    if not bool(getattr(args, "no_bridge", False)):
        _notify(progress, 22.0, "bridge")
        subs_enabled = not bool(getattr(args, "no_subs", False))
        plan, bridge_report = _bridge_plan(plan, captions=subs_enabled)
        bridged_errors = plan.validate()
        if bridged_errors:
            raise contract.VxError(
                "EDL dopo il ponte non valido: " + "; ".join(bridged_errors)
            )
        if bridge_report.captions:
            from . import bridge as bridge_mod

            written = bridge_mod.caption_ass(
                bridge_report.captions,
                workdir / "captions.ass",
                aspect=str(plan.aspect_ratio),
                out_w=int(plan.resolution[0]),
                out_h=int(plan.resolution[1]),
            )
            captions_ass = str(written) if written is not None else None
        report_path = (workdir / "bridge_report.json").resolve()
        bridge_report.write(report_path)
        bridge_summary = {
            "enabled": True,
            "cuts": len(bridge_report.cuts),
            "gained_clips": len(bridge_report.audio),
            "not_applied": len(bridge_report.not_applied),
            "captions": len(bridge_report.captions),
            "subtitles": captions_ass,
            "report": str(report_path),
        }
        _log(
            f"[auto] bridge: {len(bridge_report.cuts)} tagli, "
            f"{len(bridge_report.audio)} gain, "
            f"{len(bridge_report.captions)} caption, "
            f"{len(bridge_report.not_applied)} non applicati"
        )

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
    _notify(progress, 25.0, "render")
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
        progress_file=progress_file,
        fill=fill_mode,
        subtitles=captions_ass,
    )
    _notify(progress, 92.0, "render_done")

    qc_result = qc.audit_master(out, target_lufs=TARGET_LUFS, target_tp=TARGET_TP)
    _notify(progress, 96.0, "qc")

    produced = deliverables.produce(plan, out, workdir / "out", workdir=workdir)
    _notify(progress, 100.0, "deliverables")

    _assert_sources_unchanged(assets, source_sha)

    a_roll_indices: Dict[str, int] = {}
    for entry in selected:
        if entry.get("role") != "a_roll":
            continue
        asset_id = entry.get("asset_id")
        if isinstance(asset_id, str) and asset_id not in a_roll_indices:
            a_roll_indices[asset_id] = len(a_roll_indices)

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
        "montage": {
            "mode": "montage" if montage_mode else "single",
            "a_roll_count": len(a_roll_indices),
        },
        "fill": _fill_declaration(plan, media_info_map, fill_mode),
        "bridge": bridge_summary,
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


def run(
    args: Any,
    *,
    progress: Optional[Callable[[float, str], None]] = None,
    progress_file: Optional[str] = None,
    montage: Optional[bool] = None,
    fill: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute the whole pipeline under the workdir lock and return the report.

    ``montage`` and ``fill`` default to ``None``: the flags on ``args`` win
    unless a caller passes an explicit value, so an existing caller that only
    has ``args`` keeps the single-a_roll and ``fit`` behaviour unchanged.
    """
    out = Path(args.out)
    assets_dir = Path(args.assets)
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
        )
    finally:
        lock.release(beat)
