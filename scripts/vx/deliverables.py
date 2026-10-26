"""vx.deliverables — the producer behind the delivery manifest.

The plan declares four intents and this module executes them:

    master.mp4        -> out/master.mp4        (copy of the rendered file)
    subtitles.srt     -> out/subtitles.srt     (only when speech exists)
    thumbnail.jpg     -> out/thumbnail.jpg     (one frame from the hook)
    contact_sheet.jpg -> out/contact_sheet.jpg (4x4 grid, one frame every 2s)

Multi-aspect exports are not produced; they are not declared.

Import-light on purpose: only the ffmpeg helper is imported, so this module
can be used by the CLI without dragging in plan/render/qc and creating cycles.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import ffmpeg as ff

__all__ = ["planned", "produce"]

# The four intents, in delivery order. Paths are relative to the output dir.
_INTENTS = (
    ("master.mp4", "master"),
    ("subtitles.srt", "subtitles"),
    ("thumbnail.jpg", "thumbnail"),
    ("contact_sheet.jpg", "contact_sheet"),
)

_TILE_COLS = 4
_TILE_ROWS = 4
_TILE_WIDTH = 320
_TILE_PADDING = 4
_TILE_MARGIN = 4


# --------------------------------------------------------------------------- #
# plan access (EditPlan or plain dict)
# --------------------------------------------------------------------------- #


def _get(plan: Any, key: str, default: Any = None) -> Any:
    if isinstance(plan, dict):
        value = plan.get(key)
    else:
        value = getattr(plan, key, None)
    return default if value is None else value


def _has_speech(plan: Any) -> bool:
    """Speech is declared by an enabled subtitles treatment or an explicit flag."""
    brief = _get(plan, "brief", {}) or {}
    if brief.get("subtitles") == "none":
        return False
    treatments = _get(plan, "treatments", []) or []
    if isinstance(treatments, list):
        for treatment in treatments:
            if (
                isinstance(treatment, dict)
                and treatment.get("enabled")
                and treatment.get("name") == "subtitles"
            ):
                return True
    for key in ("speech", "has_speech"):
        if brief.get(key):
            return True
    output = _get(plan, "output", {}) or {}
    if output.get("speech") or output.get("has_speech"):
        return True
    return False


def planned(plan: Any) -> List[Dict[str, Any]]:
    """The four declared intents, with the same names/paths as the plan."""
    speech = _has_speech(plan)
    items: List[Dict[str, Any]] = []
    for name, kind in _INTENTS:
        items.append(
            {
                "name": name,
                "path": f"out/{name}",
                "kind": kind,
                "available": True if kind != "subtitles" else speech,
            }
        )
    return items


# --------------------------------------------------------------------------- #
# filesystem / probing helpers
# --------------------------------------------------------------------------- #


def _safe_out(out_dir: Path, name: str) -> Path:
    """Resolve a target and refuse anything that escapes out_dir."""
    root = out_dir.resolve()
    target = (root / name).resolve()
    if target.parent != root:
        raise ValueError(f"refusing to write outside {root}: {target}")
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe(path: Path) -> Dict[str, Any]:
    """Minimal ffprobe: width, height, duration. No vx.probe import."""
    proc = ff.run_fp(["-show_streams", "-show_format", "-of", "json", str(path)])
    if not proc.ok:
        raise RuntimeError(f"ffprobe failed on {path.name}: {proc.tail(5)}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError(f"no video stream in {path.name}")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    duration = 0.0
    try:
        duration = float(
            video.get("duration") or (data.get("format") or {}).get("duration") or 0.0
        )
    except (TypeError, ValueError):
        duration = 0.0
    return {"width": width, "height": height, "duration": round(duration, 6)}


def _run_ff(args: List[str], target: Path) -> None:
    """One ffmpeg invocation; fail loudly and never leave a zero-byte file."""
    proc = ff.run_ff(args)
    if not proc.ok:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"ffmpeg failed ({proc.rc}) for {target.name}: {proc.tail(20)}"
        )
    if not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg produced no data for {target.name}")


def _item(intent: Dict[str, Any], target: Path, info: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": intent["name"],
        "path": str(target.resolve()),
        "kind": intent["kind"],
        "available": True,
        "bytes": target.stat().st_size,
        "sha256": _sha256(target),
        "width": int(info.get("width") or 0),
        "height": int(info.get("height") or 0),
        "duration": round(float(info.get("duration") or 0.0), 6),
    }


def _thumbnail_time(plan: Any, duration: float) -> float:
    output = _get(plan, "output", {}) or {}
    render = _get(plan, "render", {}) or {}
    value = (
        output.get("thumbnail_at")
        or render.get("hook_time")
        or min(0.5, duration / 2.0)
    )
    try:
        t = float(value)
    except (TypeError, ValueError):
        t = min(0.5, duration / 2.0)
    if duration > 0:
        t = max(0.0, min(t, max(0.0, duration - 0.05)))
    return t


# --------------------------------------------------------------------------- #
# producer
# --------------------------------------------------------------------------- #


def produce(
    plan: Any,
    master: Path,
    out_dir: Path,
    *,
    srt_source: Optional[Path] = None,
    workdir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Execute every available intent and return the produced manifest.

    ``master`` is the already-rendered file: it is copied, never re-encoded.
    ``srt_source`` is copied only when it exists and is non-empty; otherwise
    the subtitles item is returned with ``available: false`` and no file is
    created. ``workdir`` is accepted for callers that keep scratch space; this
    producer needs none.
    """
    master = Path(master)
    out_dir = Path(out_dir)
    if not master.exists():
        raise FileNotFoundError(f"master not found: {master}")
    if master.stat().st_size == 0:
        raise RuntimeError(f"master is empty: {master}")

    out_dir.mkdir(parents=True, exist_ok=True)
    if workdir is not None:
        Path(workdir).mkdir(parents=True, exist_ok=True)

    master_info = _probe(master)
    duration = float(master_info.get("duration") or 0.0)
    produced: List[Dict[str, Any]] = []

    for intent in planned(plan):
        kind = intent["kind"]
        target = _safe_out(out_dir, intent["name"])

        if kind == "master":
            shutil.copy2(master, target)
            if target.stat().st_size == 0:
                target.unlink(missing_ok=True)
                raise RuntimeError(f"copied master is empty: {target}")
            produced.append(_item(intent, target, master_info))
            continue

        if kind == "subtitles":
            source = Path(srt_source) if srt_source is not None else None
            if source is not None and source.exists() and source.stat().st_size > 0:
                shutil.copy2(source, target)
                produced.append(
                    _item(
                        intent,
                        target,
                        {"width": 0, "height": 0, "duration": 0.0},
                    )
                )
            else:
                produced.append(
                    {
                        "name": intent["name"],
                        "path": str(target.resolve()),
                        "kind": intent["kind"],
                        "available": False,
                        "reason": "no speech",
                    }
                )
            continue

        if kind == "thumbnail":
            t = _thumbnail_time(plan, duration)
            _run_ff(
                [
                    "-ss",
                    f"{t:.3f}",
                    "-i",
                    str(master),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    "-update",
                    "1",
                    str(target),
                ],
                target,
            )
            produced.append(_item(intent, target, _probe(target)))
            continue

        if kind == "contact_sheet":
            fps = "1/2" if duration >= 32.0 else "1"
            vf = (
                f"fps={fps},scale={_TILE_WIDTH}:-2,"
                f"tile={_TILE_COLS}x{_TILE_ROWS}:"
                f"padding={_TILE_PADDING}:margin={_TILE_MARGIN}"
            )
            _run_ff(
                [
                    "-i",
                    str(master),
                    "-vf",
                    vf,
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    "-update",
                    "1",
                    str(target),
                ],
                target,
            )
            produced.append(_item(intent, target, _probe(target)))
            continue

        raise ValueError(f"unknown deliverable kind: {kind}")

    return produced
