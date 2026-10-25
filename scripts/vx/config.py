"""vx.config — machine configuration, so the toolkit is portable and no personal path is baked in.

Resolution order, first hit wins:
  1. ``$VX_CONFIG`` (explicit path to a JSON file)
  2. ``<skill>/vx.config.json``   (gitignored, machine-local)
  3. ``%APPDATA%/vx/config.json`` (Windows) or ``~/.config/vx/config.json``
  4. environment variables
  5. built-in defaults

Recognised keys: work_root, venv, ffmpeg, ffprobe, whisper_cache, proxy_width, cpu_threads.
"""

from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

SKILL_ROOT = Path(__file__).resolve().parents[2]


def _candidates() -> list[Path]:
    out: list[Path] = []
    explicit = os.environ.get("VX_CONFIG")
    if explicit:
        out.append(Path(explicit))
    out.append(SKILL_ROOT / "vx.config.json")
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(Path(appdata) / "vx" / "config.json")
    out.append(Path.home() / ".config" / "vx" / "config.json")
    return out


@lru_cache(maxsize=1)
def load() -> Dict[str, Any]:
    """Merged configuration plus the file it came from (``_source``)."""
    merged: Dict[str, Any] = {}
    source = ""
    for candidate in _candidates():
        try:
            if candidate.is_file():
                data = json.loads(candidate.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict):
                    merged.update(data)
                    source = str(candidate)
                    break
        except (OSError, json.JSONDecodeError):
            continue

    env_map = {
        "work_root": "VX_WORK",
        "venv": "VX_VENV",
        "ffmpeg": "VX_FFMPEG",
        "ffprobe": "VX_FFPROBE",
        "whisper_cache": "VX_WHISPER_CACHE",
    }
    for key, env in env_map.items():
        value = os.environ.get(env)
        if value and not merged.get(key):
            merged[key] = value

    merged.setdefault("proxy_width", 640)
    merged.setdefault("cpu_threads", 0)
    merged["_source"] = source
    merged["_defaults_applied"] = not source
    return merged


def get(key: str, default: Any = None) -> Any:
    value = load().get(key)
    return default if value in (None, "") else value


def work_root() -> Path:
    configured = get("work_root")
    return Path(configured) if configured else Path.home() / "video_work"


def python_exe() -> str:
    return (
        get("venv")
        and str(Path(str(get("venv"))) / "Scripts" / "python.exe")
        or sys.executable
    )


def whisper_cache() -> Optional[str]:
    value = get("whisper_cache")
    return str(value) if value else None


def describe() -> Dict[str, Any]:
    data = load()
    return {
        "source": data.get("_source") or "defaults",
        "work_root": str(work_root()),
        "venv": str(get("venv") or ""),
        "ffmpeg": str(get("ffmpeg") or ""),
        "whisper_cache": whisper_cache() or "",
        "proxy_width": int(data.get("proxy_width") or 640),
    }
