"""vx.winpath — the Windows quoting layer. One implementation, used everywhere.

Verified on ffmpeg 8.1 / Windows:
    subtitles='D\\:/work/clip a.ass'   -> WORKS
    subtitles=D\\:/work/clip a.ass     -> FAILS (colon eaten as option separator)
    spaces inside the single quotes    -> fine
    backslash-escaped spaces           -> fails
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

from .contract import VxError

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def ass_filter_path(path: str | Path) -> str:
    """Return the value to place inside `subtitles=` / `ass=` (already quoted)."""
    p = Path(path).resolve().as_posix()  # D:/work/clip.ass
    escaped = p.replace(":", "\\:")  # D\\:/work/clip.ass
    return f"'{escaped}'"


def filter_value(path: str | Path) -> str:
    """Unquoted escaped form, for callers that add their own quotes."""
    return Path(path).resolve().as_posix().replace(":", "\\:")


def escape_expr(expr: str) -> str:
    """Escape commas for a filtergraph option value (crop expressions)."""
    return expr.replace(",", "\\,")


def safe_stem(name: str, limit: int = 48) -> str:
    stem = Path(name).stem
    cleaned = _UNSAFE.sub("-", stem).strip("-")
    return (cleaned or "clip")[:limit]


def source_fingerprint(path: str | Path) -> dict:
    p = Path(path)
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    stat = p.stat()
    return {
        "path": str(p.resolve()),
        "sha256": h.hexdigest(),
        "size": stat.st_size,
        "mtime": round(stat.st_mtime, 3),
    }


def same_fingerprint(a: dict, b: dict) -> bool:
    return (
        a.get("sha256") == b.get("sha256")
        and a.get("size") == b.get("size")
        and a.get("mtime") == b.get("mtime")
    )


def work_root() -> Path:
    from . import config

    root = config.work_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def make_workdir(
    source: str | Path, *, root: str | Path | None = None, subdirs: tuple[str, ...] = ()
) -> Path:
    """<work_root>/<stem>-<sha8>, taken by liveness (see `vx.lock`), never by age."""
    from . import lock as lock_mod
    from . import settings

    fp = source_fingerprint(source)
    base = Path(root) if root else work_root()
    target = base / f"{safe_stem(Path(source).name)}-{fp['sha256'][:8]}"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("proxy", "analysis", "subs", "stage", "out", "qc", *subdirs):
        (target / name).mkdir(exist_ok=True)
    beat = lock_mod.acquire(
        target,
        stage="ingest",
        heartbeat_seconds=float(settings.get("lock.heartbeat_seconds", 30)),
    )
    _BEATS[str(target)] = beat
    (target / "source.json").write_text(
        __import__("json").dumps(fp, indent=2), encoding="utf-8"
    )
    return target


_BEATS: dict = {}


def heartbeat(workdir: str | Path, stage: str) -> None:
    """Announce progress so a long render is visibly alive, not mistaken for a crash."""
    beat = _BEATS.get(str(Path(workdir)))
    if beat is not None:
        beat.bump(stage)


def clear_lock(workdir: str | Path) -> None:
    from . import lock as lock_mod

    key = str(Path(workdir))
    beat = _BEATS.pop(key, None)
    if beat is not None:
        lock_mod.release(beat)
        return
    info = lock_mod.read(workdir)
    if info is not None and not lock_mod.state(workdir)["stale"]:
        return
    Path(workdir, lock_mod.LOCK_NAME).unlink(missing_ok=True)
