"""Stable external surface for agents and the MCP server; argparse stays private."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union


@dataclass(frozen=True)
class AutoRequest:
    prompt: str
    assets: str
    out: str
    workdir: Optional[str] = None
    fps: Optional[float] = None
    manifest: Optional[str] = None
    autonomous: bool = False
    crf: Optional[int] = None
    preset: Optional[str] = None


def run_auto(
    request: AutoRequest,
    *,
    progress: Optional[Any] = None,
    progress_file: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the whole pipeline from a typed request and return its report."""
    from types import SimpleNamespace

    from . import auto

    namespace = SimpleNamespace(
        prompt=request.prompt,
        assets=request.assets,
        out=request.out,
        workdir=request.workdir,
        manifest=request.manifest,
        fps=request.fps,
        autonomous=request.autonomous,
        crf=request.crf,
        preset=request.preset,
    )
    # Il worker ha stdout muto per contratto: la narrazione, se resta accesa da
    # una chiamata precedente nello stesso processo, scriverebbe li'.
    previous = getattr(auto, "_NARRATING", False)
    reserved = getattr(auto, "_STDOUT_RESERVED", False)
    # "La vista parte da sola" vale anche per l'MCP: `vx_auto` non e' un
    # percorso di seconda classe. Un daemon che non parte non ferma il lavoro.
    auto.autostart_view(namespace)
    auto._NARRATING = False
    auto._STDOUT_RESERVED = True
    try:
        return auto.run(namespace, progress=progress, progress_file=progress_file)
    finally:
        auto._NARRATING = previous
        auto._STDOUT_RESERVED = reserved


def triage(
    directory: Union[str, Path],
    width: int,
    height: int,
    *,
    manifest: Optional[str] = None,
    require_anchor: bool = False,
) -> Dict[str, Any]:
    """Discover and triage the top level of ``directory`` without writing anything."""
    from . import asset_triage, contract

    try:
        target_w = int(width)
        target_h = int(height)
    except (TypeError, ValueError) as exc:
        raise contract.VxError(f"width/height devono essere interi: {exc}") from exc
    if target_w <= 0 or target_h <= 0:
        raise contract.VxError(
            f"width/height devono essere > 0, ricevuto {width!r}x{height!r}"
        )

    assets = asset_triage.discover_assets(directory, manifest=manifest)
    result = asset_triage.triage_assets(
        assets,
        (target_w, target_h),
        require_anchor=require_anchor,
    ).to_dict()
    result["total"] = len(assets)
    return result


def _load_document(source: Union[str, Path, Mapping[str, Any]]) -> Dict[str, Any]:
    if isinstance(source, Mapping):
        return dict(source)
    if isinstance(source, Path):
        return json.loads(source.read_text(encoding="utf-8"))
    if isinstance(source, str):
        try:
            return json.loads(source)
        except json.JSONDecodeError:
            return json.loads(Path(source).read_text(encoding="utf-8"))
    raise TypeError(f"EDL plan non supportato: {type(source).__name__}")


def validate_edl(plan: Union[str, Path, Mapping[str, Any]]) -> Dict[str, Any]:
    """Validate an EDL document and report the same shape as ``cmd_edl``."""
    from . import edl_plan, grammar

    document = _load_document(plan)
    try:
        parsed = edl_plan.EDLPlan.from_dict(document)
    except edl_plan.EDLValidationError as exc:
        return {
            "ok": False,
            "errors": list(exc.errors),
            "grammar": [],
            "camera_exemptions": [],
        }

    errors = parsed.validate()
    violations = grammar.validate_cinematic_rules(parsed)
    exemptions = grammar.camera_exemptions(parsed)
    if errors or violations:
        return {
            "ok": False,
            "errors": list(errors) + [item.details for item in violations],
            "grammar": [item.to_dict() for item in violations],
            "camera_exemptions": exemptions,
        }

    return {
        "ok": True,
        "errors": [],
        "grammar": [],
        "contract_version": parsed.contract_version,
        "sources": list(parsed.sources),
        "video_tracks": len(parsed.video_tracks),
        "audio_tracks": len(parsed.audio_tracks),
        "duration_sec": parsed.duration_sec,
        "resolution": list(parsed.resolution),
        "camera_exemptions": exemptions,
    }


def doctor() -> Dict[str, Any]:
    """Return the environment scan unchanged."""
    from . import doctor as doctor_mod

    return doctor_mod.scan()
