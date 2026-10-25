"""vx.settings — operational parameters outside the source, with an explicit cascade.

Precedence, lowest to highest:

    1. contract.DEFAULT_THRESHOLDS      (the floor: the skill works with zero config)
    2. <skill>/settings.json            (machine/site profile)
    3. vx.config.json -> "settings"     (same file as the paths, optional)
    4. --settings FILE                  (per-job profile)
    5. --override key.path=value        (per-job payload, highest)

Overrides are validated against a range table before they are applied, and the
effective set is fingerprinted so every report states exactly which values produced
the numbers inside it.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import config
from . import contract

SETTINGS_SCHEMA = "vx.settings/1.0"

_APPLIED: Dict[str, Any] = {}
_REFRESHERS: List[Callable[[Dict[str, Any]], None]] = []

RANGES: Dict[str, tuple[float, float]] = {
    "jitter_shaky": (0.01, 50.0),
    "stab_min_headroom_pct": (0.0, 100.0),
    "clip_high_pct": (0.0, 100.0),
    "clip_high_block_pct": (0.0, 100.0),
    "luma_under": (0.0, 255.0),
    "cast_rg": (0.0, 128.0),
    "cast_by": (0.0, 128.0),
    "freeze_min": (0.05, 3600.0),
    "scene_cut": (0.0, 1.0),
    "noise_snr_low": (-60.0, 120.0),
    "dead_air_ratio": (0.0, 1.0),
    "dead_air_max_cut": (0.0, 1.0),
    "silence_pad": (0.0, 5.0),
    "silence_min": (0.01, 600.0),
    "clip_pct": (0.0, 100.0),
    "lra_wide": (0.0, 60.0),
    "speech_wpm_fast": (10.0, 600.0),
    "sub_max_chars_916": (4.0, 120.0),
    "sub_max_chars_169": (4.0, 200.0),
    "sub_max_lines": (1.0, 6.0),
    "sub_max_cps": (1.0, 120.0),
    "hook_window": (0.1, 120.0),
    "max_creative_treatments": (0.0, 20.0),
    "cut_fade_ms": (0.0, 500.0),
    "vad_threshold": (0.05, 0.95),
    "vad_min_speech": (0.05, 60.0),
    "vad_min_silence": (0.05, 60.0),
    "music_confidence": (0.0, 1.0),
    "detector_min_confidence": (0.0, 1.0),
    "nvenc_cq": (0.0, 51.0),
}

DEFAULTS: Dict[str, Any] = {
    "thresholds": {},
    "audio": {
        "vad": "silero",
        "vad_threshold": 0.5,
        "vad_min_speech": 0.25,
        "vad_min_silence": 0.35,
        "music_detector": "auto",
        "music_confidence": 0.6,
        "detector_min_confidence": 0.6,
        "denoise_on_uncertain": False,
    },
    "accel": {
        "encoder": "auto",
        "nvence_cq": 21,
        "whisper_device": "auto",
        "probe_ttl_hours": 24,
        "verify_with_test_encode": True,
        "master_encoder": "libx264",
        "scratch_encoder": "auto",
    },
    "render": {
        "crf": 18,
        "preset": "medium",
        "audio_bitrate": "192k",
    },
    "lock": {
        "heartbeat_seconds": 30,
        "zombie_warn_seconds": 300,
    },
    "qc": {
        "duration_drift_ms": 40.0,
        "frame_count_delta": 1,
        "lufs_delta": 0.3,
        "true_peak_margin": 0.1,
        "av_start_ms": 20.0,
        "black_frames_edges": 2,
        "font_fallback_count": 0,
    },
}


def register_refresher(fn: Callable[[Dict[str, Any]], None]) -> None:
    """Modules that derive constants at import time register here to be re-derived."""
    if fn not in _REFRESHERS:
        _REFRESHERS.append(fn)


def _deep_merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        current = out.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            out[key] = _deep_merge(current, value)
        else:
            out[key] = value
    return out


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _file_layers(settings_file: Optional[str]) -> List[Dict[str, Any]]:
    layers: List[Dict[str, Any]] = []
    skill = Path(__file__).resolve().parents[2]
    for candidate in (
        skill / "settings.json",
        Path(config.load().get("_source") or ""),
    ):
        if candidate and candidate.is_file():
            data = _read_json(candidate)
            layers.append(data.get("settings") or {})
    if settings_file:
        data = _read_json(Path(settings_file))
        layers.append(
            {k: v for k, v in data.items() if k != "settings"}
            if "thresholds" in data
            else data
        )
    return [layer for layer in layers if layer]


def _coerce(current: Any, raw: str) -> Any:
    if isinstance(current, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        return float(raw) if isinstance(current, float) else int(float(raw))
    if isinstance(current, list):
        return [item.strip() for item in raw.split(",")]
    return raw


def _apply_override(
    target: Dict[str, Any], dotted: str, raw: str, defaults: Dict[str, Any]
) -> None:
    keys = dotted.split(".")
    node = target
    ref: Any = defaults
    for key in keys[:-1]:
        if not isinstance(node.get(key), dict):
            node[key] = {}
        node = node[key]
        ref = ref.get(key) if isinstance(ref, dict) else None
    leaf = keys[-1]
    existing = node.get(leaf, ref.get(leaf) if isinstance(ref, dict) else None)
    node[leaf] = _coerce(existing, raw)


def _validate_ranges(settings: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    merged_thresholds = settings.get("thresholds") or {}
    for key, value in merged_thresholds.items():
        bounds = RANGES.get(key)
        if bounds is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            problems.append(f"thresholds.{key} is not numeric: {value!r}")
            continue
        low, high = bounds
        if not (low <= number <= high):
            problems.append(f"thresholds.{key}={number} outside [{low}, {high}]")
    for key in ("vad_threshold", "music_confidence", "detector_min_confidence"):
        section = settings.get("audio") or {}
        if key in section:
            try:
                number = float(section[key])
            except (TypeError, ValueError):
                problems.append(f"audio.{key} is not numeric")
                continue
            low, high = RANGES[key]
            if not (low <= number <= high):
                problems.append(f"audio.{key}={number} outside [{low}, {high}]")
    return problems


def build(
    *, settings_file: Optional[str] = None, overrides: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Merge every layer into one effective settings tree (not yet applied)."""
    effective = copy.deepcopy(DEFAULTS)
    effective["thresholds"] = dict(contract.DEFAULT_THRESHOLDS)
    for layer in _file_layers(settings_file):
        effective = _deep_merge(effective, layer)
    for item in overrides or []:
        if "=" not in item:
            raise contract.VxError(f"--override expects key.path=value, got {item!r}")
        dotted, _, raw = item.partition("=")
        _apply_override(
            effective,
            dotted.strip(),
            raw,
            DEFAULTS | {"thresholds": contract.DEFAULT_THRESHOLDS},
        )
    effective["schema"] = SETTINGS_SCHEMA
    return effective


def fingerprint(settings: Dict[str, Any]) -> str:
    payload = {k: v for k, v in settings.items() if k != "schema"}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def apply(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Install the effective values: mutate contract.THRESHOLDS in place and refresh
    the few modules that derive constants at import time."""
    problems = _validate_ranges(settings)
    if problems:
        raise contract.VxError("invalid settings: " + "; ".join(problems))

    thresholds = settings.get("thresholds") or {}
    for key, value in thresholds.items():
        contract.THRESHOLDS[key] = value

    _APPLIED.clear()
    _APPLIED.update(settings)
    _APPLIED["_fingerprint"] = fingerprint(settings)
    for refresher in _REFRESHERS:
        refresher(settings)
    _refresh_modules()
    return _APPLIED


def _refresh_modules() -> None:
    """Modules that snapshot THRESHOLDS at import expose `refresh_from_settings`."""
    import importlib
    import sys

    for name in ("vx.analyze", "vx.qc"):
        module = sys.modules.get(name) or importlib.import_module(name)
        hook = getattr(module, "refresh_from_settings", None)
        if hook is not None:
            hook(_APPLIED)


def current() -> Dict[str, Any]:
    return _APPLIED or apply(build())


def get(dotted: str, default: Any = None) -> Any:
    node: Any = current()
    for key in dotted.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(key, default)
    return node


def describe() -> Dict[str, Any]:
    settings = current()
    return {
        "schema": settings.get("schema"),
        "fingerprint": settings.get("_fingerprint") or fingerprint(settings),
        "sources": [str(path) for path in _file_layers(None)],
        "thresholds": dict(contract.THRESHOLDS),
        "audio": settings.get("audio"),
        "accel": settings.get("accel"),
        "lock": settings.get("lock"),
    }
