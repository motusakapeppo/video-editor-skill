"""vx.doctor — environment scan and verdict (Fase 1).

Answers one question before any edit starts: can this machine run the whole
pipeline, and if not, exactly what is missing? The scan is read-only and never
installs anything.

Verdict law:
    ready     ffmpeg + libx264 + ass + cv2 + faster_whisper all present
    degraded  something optional is missing (listed in ``warnings``)
    broken    ffmpeg or libx264 missing — nothing can be encoded
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import ffmpeg as ff
from .contract import EXIT_MISSING_DEP, EXIT_OK, VERSION
from .faces import YUNET_MODEL
from .winpath import work_root

__all__ = ["scan", "to_text", "main"]

# The 25 capabilities the pipeline actually calls. Order is the report order.
REQUIRED_CAPS = (
    "vidstabdetect",
    "vidstabtransform",
    "silencedetect",
    "silenceremove",
    "loudnorm",
    "ebur128",
    "astats",
    "ametadata",
    "scdet",
    "freezedetect",
    "subtitles",
    "ass",
    "concat",
    "trim",
    "atrim",
    "acrossfade",
    "afade",
    "crop",
    "scale",
    "zoompan",
    "boxblur",
    "overlay",
    "afftdn",
    "libx264",
    "aac",
)

# Hard requirements for a "ready" verdict.
HARD_CAPS = ("libx264", "ass")
HARD_DEPS = ("cv2", "faster_whisper")

DEPS = (
    "cv2",
    "mediapipe",
    "moviepy",
    "librosa",
    "faster_whisper",
    "soundfile",
    "scipy",
    "pydub",
    "numpy",
    "yaml",
)

MAX_TEXT_LINES = 25


# --------------------------------------------------------------------------- #
# probes
# --------------------------------------------------------------------------- #


def _has_module(name: str) -> bool:
    """Import-free presence check: never executes the package."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _cuda_runtime() -> bool:
    """True when a CUDA runtime is reachable (env var or cudart DLL on PATH)."""
    if os.environ.get("CUDA_PATH"):
        return True
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            if any(Path(entry).glob("cudart64*.dll")):
                return True
        except OSError:
            continue
    return False


def _whisper_cache() -> str:
    from . import config

    configured = config.whisper_cache()
    if configured:
        return configured
    explicit = os.environ.get("WHISPER_CACHE")
    if explicit:
        return explicit
    return str(Path.home() / ".cache" / "whisper")


def _config_block() -> Dict[str, Any]:
    from . import config

    return config.describe()


def _ffmpeg_block() -> Dict[str, Any]:
    path: Optional[str] = None
    version: Optional[str] = None
    caps: Dict[str, Any] = {name: False for name in REQUIRED_CAPS}
    error = ""
    try:
        path = ff.ffmpeg()
        version = ff.version()
        live = ff.capabilities()
        for name in REQUIRED_CAPS:
            caps[name] = bool(live.get(name))
    except Exception as exc:  # MissingDependency or a broken build
        error = str(exc)
    block: Dict[str, Any] = {"path": path, "version": version, "capabilities": caps}
    if error:
        block["error"] = error
    return block


def _models_block() -> Dict[str, Any]:
    exists = YUNET_MODEL.is_file()
    size = YUNET_MODEL.stat().st_size if exists else 0
    return {"yunet": {"path": str(YUNET_MODEL), "exists": exists, "bytes": int(size)}}


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #


def scan() -> dict:
    """Collect the full environment picture plus a verdict."""
    ffmpeg_block = _ffmpeg_block()
    caps = ffmpeg_block["capabilities"]
    deps = {name: _has_module(name) for name in DEPS}
    models = _models_block()

    smi = shutil.which("nvidia-smi")
    gpu = {"nvidia_smi": smi, "cuda_runtime": _cuda_runtime()}

    venv = sys.prefix if sys.prefix != sys.base_prefix else None

    missing: List[str] = []
    warnings: List[str] = []

    if not ffmpeg_block["path"]:
        missing.append("ffmpeg")
    for cap in HARD_CAPS:
        if not caps.get(cap):
            missing.append(cap)
    for dep in HARD_DEPS:
        if not deps.get(dep):
            missing.append(dep)

    if not ffmpeg_block["path"] or not caps.get("libx264"):
        verdict = "broken"
    elif missing:
        verdict = "degraded"
    else:
        verdict = "ready"

    # Required gaps are repeated in warnings so a degraded report is actionable.
    for item in missing:
        warnings.append(f"manca {item} (richiesto)")

    for cap in REQUIRED_CAPS:
        if cap in HARD_CAPS or cap == "libx264":
            continue
        if not caps.get(cap):
            warnings.append(f"filtro ffmpeg assente: {cap}")

    for dep in DEPS:
        if dep in HARD_DEPS:
            continue
        if not deps.get(dep):
            warnings.append(f"pacchetto python assente: {dep}")

    if not models["yunet"]["exists"]:
        warnings.append(f"modello YuNet assente: {models['yunet']['path']}")
    if not gpu["nvidia_smi"]:
        warnings.append("nvidia-smi non trovato: nessuna GPU NVIDIA")
    if not gpu["cuda_runtime"]:
        warnings.append("CUDA runtime non rilevato: whisper girera' su CPU")
    if not Path(_whisper_cache()).is_dir():
        warnings.append(f"cache whisper vuota: {_whisper_cache()}")

    return {
        "ffmpeg": ffmpeg_block,
        "python": {
            "version": sys.version.split()[0],
            "executable": sys.executable,
            "venv": venv,
        },
        "config": _config_block(),
        "deps": deps,
        "models": models,
        "gpu": gpu,
        "whisper_cache": _whisper_cache(),
        "work_root": str(work_root()),
        "verdict": verdict,
        "missing": missing,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# human report
# --------------------------------------------------------------------------- #


def _ok(flag: bool) -> str:
    return "ok" if flag else "NO"


def to_text(report: dict) -> str:
    """Compact Italian table, hard-capped at 25 lines."""
    ffmpeg_block = report.get("ffmpeg") or {}
    caps = ffmpeg_block.get("capabilities") or {}
    deps = report.get("deps") or {}
    models = report.get("models") or {}
    yunet = models.get("yunet") or {}
    gpu = report.get("gpu") or {}
    python = report.get("python") or {}

    present = sum(1 for name in REQUIRED_CAPS if caps.get(name))
    lines: List[str] = [
        f"VX doctor {VERSION}",
        f"verdetto     {str(report.get('verdict', '?')).upper()}",
        f"ffmpeg       {ffmpeg_block.get('version') or 'ASSENTE'}",
        f"             {ffmpeg_block.get('path') or '-'}",
        f"python       {python.get('version') or '?'}  venv={python.get('venv') or 'no'}",
        f"capacita'    {present}/{len(REQUIRED_CAPS)}",
    ]

    absent = [name for name in REQUIRED_CAPS if not caps.get(name)]
    lines.append("             mancanti: " + (", ".join(absent) if absent else "-"))

    dep_line = "  ".join(f"{name}={_ok(bool(deps.get(name)))}" for name in DEPS)
    lines.append("deps         " + dep_line)

    lines.append(
        "modello      yunet "
        + (f"{yunet.get('bytes', 0)} B ok" if yunet.get("exists") else "ASSENTE")
    )
    lines.append(
        "gpu          nvidia-smi={}  cuda={}".format(
            _ok(bool(gpu.get("nvidia_smi"))), _ok(bool(gpu.get("cuda_runtime")))
        )
    )
    lines.append(f"whisper      {report.get('whisper_cache') or '-'}")
    lines.append(f"work         {report.get('work_root') or '-'}")
    config_block = report.get("config") or {}
    lines.append(f"config       {config_block.get('source') or 'defaults'}")

    missing = report.get("missing") or []
    warnings = report.get("warnings") or []
    lines.append(f"mancanti     {len(missing)}  avvisi {len(warnings)}")

    budget = MAX_TEXT_LINES - len(lines)
    if budget > 0 and warnings:
        shown = warnings[: max(0, budget - 1)]
        for item in shown:
            lines.append(f"  - {item}")
        rest = len(warnings) - len(shown)
        if rest > 0 and len(lines) < MAX_TEXT_LINES:
            lines.append(f"  ... altri {rest}")

    return "\n".join(lines[:MAX_TEXT_LINES])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vx.doctor",
        description="Scansione ambiente e verdetto (Fase 1).",
    )
    parser.add_argument("--json", action="store_true", help="stampa il JSON completo")
    ns = parser.parse_args(list(argv) if argv is not None else None)

    report = scan()
    if ns.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(to_text(report))

    return EXIT_OK if report["verdict"] in ("ready", "degraded") else EXIT_MISSING_DEP


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
