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
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import ffmpeg as ff
from .contract import EXIT_MISSING_DEP, EXIT_OK, VERSION
from .faces import YUNET_MODEL
from .winpath import work_root

__all__ = ["scan", "to_text", "capabilities_report", "capabilities_text", "main"]

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


def _has_option(name: str) -> bool:
    """Il binario accetta ``-<name>``? ffmpeg 9 ha rimosso le opzioni deprecate.

    Non e' pedanteria: senza ``-filter_complex_script`` i grafi lunghi non si
    passano (su Windows il limite di riga comando e' 8191 caratteri) e ogni
    render EDL muore con "Unrecognized option". Meglio dirlo qui che a meta'
    lavoro.
    """
    try:
        proc = ff.run_ff(["-hide_banner", "-h", "full"], loglevel="error", step="probe")
    except Exception:  # noqa: BLE001 - un guasto non misurato non si accusa
        return True
    text = (proc.stdout or "") + (proc.stderr or "")
    return ("-" + name) in text


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
    if path:
        block["filter_complex_script"] = _has_option("filter_complex_script")
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
    elif ffmpeg_block.get("filter_complex_script") is False:
        # Senza quell'opzione ogni render EDL muore: non e' "degraded", e' rotto.
        missing.append("ffmpeg:filter_complex_script")
        verdict = "broken"
    elif missing:
        verdict = "degraded"
    else:
        verdict = "ready"

    # Required gaps are repeated in warnings so a degraded report is actionable.
    for item in missing:
        warnings.append(f"manca {item} (richiesto)")
    if ffmpeg_block.get("filter_complex_script") is False:
        warnings.append(
            "ffmpeg "
            f"{ffmpeg_block.get('version') or '?'}: '-filter_complex_script' non c'e' "
            "(rimosso in 9.0). Serve la serie 8.x: senza quell'opzione i grafi lunghi "
            "non si passano (8191 caratteri su Windows) e ogni render EDL fallisce."
        )

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
# capabilities: le stesse 25, con cosa si perde quando manca qualcosa
# --------------------------------------------------------------------------- #

#: Una frase per capability: cosa smette di funzionare senza di lei. Non e'
#: un'etichetta generica, e' la conseguenza concreta sulla pipeline.
CAPABILITY_EFFECTS: Dict[str, str] = {
    "vidstabdetect": "niente stabilizzazione: la ripresa a mano libera resta mossa",
    "vidstabtransform": "niente stabilizzazione: la ripresa a mano libera resta mossa",
    "silencedetect": "niente rilevamento dei silenzi: i tagli sui vuoti non si misurano",
    "silenceremove": "niente rimozione automatica dei silenzi",
    "loudnorm": "niente normalizzazione loudness EBU R128 in un passaggio",
    "ebur128": "niente misura loudness/true peak a norma",
    "astats": "niente statistiche audio: clipping, picchi e rumore restano non misurati",
    "ametadata": "niente lettura/scrittura dei metadati audio",
    "scdet": "niente rilevamento scene cut: i tagli si scelgono a mano",
    "freezedetect": "niente rilevamento dei fotogrammi congelati",
    "subtitles": "niente sottotitoli bruciati da file .srt/.ass",
    "ass": "niente sottotitoli ASS stilizzati (font, posizione, animazione)",
    "concat": "niente concatenazione dei segmenti senza ricodifica",
    "trim": "niente taglio video preciso",
    "atrim": "niente taglio audio preciso",
    "acrossfade": "niente dissolvenze audio fra i segmenti",
    "afade": "niente fade audio in entrata e in uscita",
    "crop": "niente rifilatura/reframe",
    "scale": "niente ridimensionamento: proxy e formati multipiattaforma si fermano",
    "zoompan": "niente Ken Burns ne' zoom animato",
    "boxblur": "niente sfocature di riempimento per i formati diversi",
    "overlay": "niente composizione di overlay, testi e watermark",
    "afftdn": "niente riduzione spettrale del rumore",
    "libx264": "niente codifica H.264: il render non parte",
    "aac": "niente audio AAC: l'export resta senza traccia audio",
}

#: Le stesse frasi per le dipendenze python, cosi' un verdetto degradato dice
#: sempre *cosa* si perde e non solo che manca un pacchetto.
DEP_EFFECTS: Dict[str, str] = {
    "cv2": "niente analisi video (jitter, esposizione, colore, freeze) ne' volti",
    "mediapipe": "niente pose/gesture da MediaPipe",
    "moviepy": "niente backend alternativo MoviePy per i tagli",
    "librosa": "niente analisi ritmica: il montaggio sul beat resta senza misure",
    "faster_whisper": "niente trascrizione: sottotitoli e tagli sul parlato non partono",
    "soundfile": "niente lettura/scrittura audio oltre il WAV",
    "scipy": "niente filtri scientifici (smoothing, correlazioni)",
    "pydub": "niente montaggio audio rapido di fallback",
    "numpy": "niente pipeline numerica: quasi tutto si ferma",
    "yaml": "niente ricette e profili in YAML",
}

#: Cio' che senza di lui non si codifica niente: la stessa legge del verdetto
#: ``broken`` di ``doctor``, non una lista nuova.
ENCODE_REQUIRED = ("ffmpeg", "libx264")

CAPABILITIES_SCHEMA = "vx.capabilities/1"


def _ffprobe_block() -> Dict[str, Any]:
    """``path`` e ``version`` di ffprobe, o l'errore: il probe non si inventa."""
    block: Dict[str, Any] = {"path": None, "version": None}
    try:
        path = ff.ffprobe()
        proc = ff.run([path, "-version"], step="probe")
        first = proc.stdout.splitlines()[0] if proc.stdout else ""
        block["path"] = path
        block["version"] = (
            first.replace("ffprobe version ", "").split()[0] if first else "unknown"
        )
    except Exception as exc:  # noqa: BLE001 - manca il binario o e' rotto
        block["error"] = str(exc)
    return block


def capabilities_report() -> Dict[str, Any]:
    """Il referto delle capability: verdetto, binari, le 25 e cosa si perde.

    Il verdetto non mente: se una qualunque delle 25 manca, o l'ambiente non e'
    ``ready``, il verdetto scende a ``degraded``. Solo ffmpeg o libx264
    mancanti portano a ``broken`` e a exit 4, come in ``doctor``.
    """
    environment = scan()
    ffmpeg_block = environment.get("ffmpeg") or {}
    caps = dict(ffmpeg_block.get("capabilities") or {})
    present = [name for name in REQUIRED_CAPS if caps.get(name)]
    missing = [name for name in REQUIRED_CAPS if not caps.get(name)]
    deps = dict(environment.get("deps") or {})
    deps_missing = [name for name in DEPS if not deps.get(name)]

    required_missing: List[str] = []
    if not ffmpeg_block.get("path"):
        required_missing.append("ffmpeg")
    for cap in ENCODE_REQUIRED:
        if cap != "ffmpeg" and not caps.get(cap):
            required_missing.append(cap)

    lost = [
        {"capability": name, "what": CAPABILITY_EFFECTS.get(name, "funzione non disponibile")}
        for name in missing
    ]
    lost += [
        {"capability": name, "what": DEP_EFFECTS.get(name, "pacchetto python non disponibile")}
        for name in deps_missing
    ]

    if required_missing:
        verdict = "broken"
    elif missing or deps_missing or str(environment.get("verdict")) != "ready":
        verdict = "degraded"
    else:
        verdict = "ready"

    return {
        "schema": CAPABILITIES_SCHEMA,
        "verdict": verdict,
        "ok": not required_missing,
        "exit_code": EXIT_MISSING_DEP if required_missing else EXIT_OK,
        "ffmpeg": {
            "path": ffmpeg_block.get("path"),
            "version": ffmpeg_block.get("version"),
            **({"error": ffmpeg_block["error"]} if ffmpeg_block.get("error") else {}),
        },
        "ffprobe": _ffprobe_block(),
        "capabilities": {name: bool(caps.get(name)) for name in REQUIRED_CAPS},
        "present": len(present),
        "total": len(REQUIRED_CAPS),
        "missing": missing,
        "missing_required": required_missing,
        "deps": deps,
        "deps_missing": deps_missing,
        "lost": lost,
        "warnings": list(environment.get("warnings") or []),
    }


_VERDICT_IT: Dict[str, str] = {
    "ready": "pronto",
    "degraded": "degradato",
    "broken": "rotto",
}


def capabilities_text(report: Mapping[str, Any]) -> str:
    """La tabella per il terminale: verdetto, binari, 25 righe, cosa si perde."""
    verdict = str(report.get("verdict") or "?")
    ffmpeg_block = report.get("ffmpeg") or {}
    ffprobe_block = report.get("ffprobe") or {}
    caps = report.get("capabilities") or {}
    lost = report.get("lost") or []
    deps_missing = report.get("deps_missing") or []
    missing_required = report.get("missing_required") or []

    lines: List[str] = [
        f"VX capabilities {VERSION}",
        f"ambiente     {_VERDICT_IT.get(verdict, verdict).upper()} ({verdict})",
        f"ffmpeg       {ffmpeg_block.get('version') or 'ASSENTE'}"
        + (f"  {ffmpeg_block['path']}" if ffmpeg_block.get("path") else ""),
        f"ffprobe      {ffprobe_block.get('version') or 'ASSENTE'}"
        + (f"  {ffprobe_block['path']}" if ffprobe_block.get("path") else ""),
        f"capacita'    {int(report.get('present') or 0)}/{int(report.get('total') or 0)} presenti",
    ]

    for name in REQUIRED_CAPS:
        state = "presente" if caps.get(name) else "ASSENTE "
        lines.append(f"  {name:<18} {state}")

    if lost:
        lines.append("cosa si perde")
        for item in lost:
            lines.append(f"  - {item['capability']}: {item['what']}")
    else:
        lines.append("cosa si perde   niente: tutte le capability e le dipendenze rispondono")

    if missing_required:
        lines.append(
            "dipendenze obbligatorie mancanti: " + ", ".join(str(item) for item in missing_required)
        )
    if deps_missing and not missing_required:
        lines.append(
            "python mancante: " + ", ".join(str(item) for item in deps_missing)
        )
    return "\n".join(lines)


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
