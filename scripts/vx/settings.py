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
from . import features

SETTINGS_SCHEMA = "vx.settings/1.0"

# Hard ceilings for child processes, in seconds. A probe that cannot answer in
# two minutes or a render that cannot finish in an hour is a hang, not slowness:
# the caller kills it instead of waiting for a machine that may never reply.
# Values are clamped to PROCESS_TIMEOUT_RANGE where they are used, so a bad
# profile cannot disable the watchdog or make it expire instantly.
PROCESS_TIMEOUTS: Dict[str, int] = {
    "probe": 120,
    "render": 3600,
}
PROCESS_TIMEOUT_RANGE: tuple[int, int] = (1, 86400)

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
    "silence_dbfs": (-120.0, 0.0),
    "bridge_min_fragment_ms": (50.0, 5000.0),
    "bridge_gain_tp_ceiling_dbtp": (-6.0, 0.0),
    "font_advance_condensed_max": (0.1, 2.0),
    "font_advance_extended_min": (0.1, 2.0),
    "font_stem_light_max": (0.0, 0.6),
    "font_stem_regular_max": (0.0, 0.6),
    "font_stem_medium_max": (0.0, 0.6),
    "font_stem_bold_max": (0.0, 0.6),
    "limiter_tolerance_db": (0.0, 3.0),
    "limiter_encode_overshoot_db": (0.0, 3.0),
    "bridge_caption_min_confidence": (0.0, 1.0),
    "bridge_caption_gap_ms": (0.0, 5000.0),
    "bridge_caption_max_chars": (4.0, 200.0),
    # The renderer clamps MarginV to [24, out_h // 4]; the ratio cannot exceed
    # a quarter of the canvas without being clamped anyway.
    "bridge_caption_bottom_inset_ratio": (0.0, 0.25),
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
    "dialogue_fade_ms": (0.0, 500.0),
    "broll_fade_sec": (0.0, 10.0),
    "music_loop_crossfade_sec": (0.0, 3.0),
    # Normalised semantic score of a B-roll (coverage x word confidence).
    "broll_min_score": (0.0, 1.0),
    "vad_threshold": (0.05, 0.95),
    "vad_min_speech": (0.05, 60.0),
    "vad_min_silence": (0.05, 60.0),
    "music_confidence": (0.0, 1.0),
    "detector_min_confidence": (0.0, 1.0),
    "nvenc_cq": (0.0, 51.0),
    "triage_max_upscale": (1.0, 10.0),
    "triage_max_broll": (0.0, 500.0),
    "triage_max_music": (0.0, 100.0),
    "triage_min_duration_sec": (0.0, 3600.0),
    "triage_broll_sec": (0.1, 600.0),
    "triage_image_duration_sec": (0.1, 600.0),
    "triage_music_gain_db": (-60.0, 0.0),
    "triage_music_ratio_low": (0.0, 1.0),
    "triage_sharpness_low": (0.0, 1000.0),
    "triage_confidence_low": (0.0, 1.0),
    "triage_confidence_high": (0.0, 1.0),
    # Above 0.5 two unrelated dHashes are statistically indistinguishable from
    # copies, so the range stops there: a wider window would collapse real takes.
    "triage_phash_max_distance": (0.0, 0.5),
    "triage_phash_duration_tolerance_sec": (0.0, 10.0),
}

DEFAULTS: Dict[str, Any] = {
    "thresholds": {},
    "analysis": {
        "chunk_sec": 300.0,
    },
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
    "font": {
        # Rete attiva di default quando il catalogo risponde; --no-font-network
        # la spegne per job. ``wait_sec`` e' quanto si aspetta una scelta umana.
        "network": True,
        "wait_sec": 180.0,
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
    # Declared flags only (vx.features is the registry): the table exists so
    # `--override features.<name>=on` has a bool to coerce, and so an unknown
    # name is a usage error instead of a silently inert key.
    "features": features.defaults(),
}


def register_refresher(fn: Callable[[Dict[str, Any]], None]) -> None:
    """Modules that derive constants at import time register here to be re-derived."""
    if fn not in _REFRESHERS:
        _REFRESHERS.append(fn)


def clamp_timeout(value: int) -> int:
    low, high = PROCESS_TIMEOUT_RANGE
    return max(low, min(high, int(value)))


def timeout_for(kind: str) -> int:
    return clamp_timeout(PROCESS_TIMEOUTS.get(kind, PROCESS_TIMEOUTS["render"]))


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
    """Install the effective values as a new live snapshot; the public table is read-only."""
    problems = _validate_ranges(settings)
    if problems:
        raise contract.VxError("invalid settings: " + "; ".join(problems))

    merged = dict(contract.DEFAULT_THRESHOLDS)
    merged.update(settings.get("thresholds") or {})
    contract._set_thresholds(merged)

    _APPLIED.clear()
    _APPLIED.update(settings)
    _APPLIED["_fingerprint"] = fingerprint(settings)
    for refresher in _REFRESHERS:
        refresher(settings)
    _refresh_modules()
    return _APPLIED


def reset() -> Dict[str, Any]:
    """Restore the frozen default snapshot."""
    contract._set_thresholds(dict(contract.DEFAULT_THRESHOLDS))
    _APPLIED.clear()
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
        "features": features.effective(settings),
    }
