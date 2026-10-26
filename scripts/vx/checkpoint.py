"""vx.checkpoint — resumable, content-addressed chunk checkpoints.

The analysis engine splits a long clip into fixed-size chunks.  Each chunk is
expensive (ffmpeg decode + Whisper + Silero), so a crash at minute 42 of a
45-minute clip must not throw away the first 41 minutes.  This module owns the
on-disk contract:

* a directory name derived from the source hash and a hash of the analysis
  configuration, so a different source or a different model never reuses the
  wrong cache;
* a manifest with the chunk count and the indices that are fully readable;
* one JSON file per chunk, written atomically and checksummed.

Everything here is stdlib only.  Reads never raise: a corrupt file is simply a
cache miss.  Writes are atomic: a temp file in the same directory, then
``os.replace``; on failure the temp file is removed and the previous file is
left untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CHECKPOINT_SCHEMA = "vx.checkpoints/1"
ENGINE_VERSION = "1"

MANIFEST_NAME = "manifest.json"
CHUNK_PREFIX = "analysis_chunk_"
CHUNK_SUFFIX = ".json"

_HEADER_KEYS = (
    "schema",
    "engine_version",
    "identity",
    "index",
    "own_start",
    "own_end",
    "decode_start",
    "decode_end",
    "status",
    "payload_sha256",
)


def _canonical(value: Any) -> str:
    """Stable JSON used for hashing and for identity comparison."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def identity(
    source_sha256: str,
    analysis_sha256: str,
    *,
    chunk_sec: float,
    model: str,
    language: str,
    transcribe: bool,
    vad_config: Dict[str, Any],
    engine_version: str = ENGINE_VERSION,
) -> Dict[str, Any]:
    """The full cache key for one analysis run.

    ``source_sha256`` is the hash of the *whole* source file, supplied by the
    caller.  It is deliberately excluded from :func:`cfg_hash`: two different
    sources analysed with the same settings share a configuration hash, which
    is what lets :meth:`Store.find_previous` find a stale sibling.
    """
    return {
        "schema": CHECKPOINT_SCHEMA,
        "engine_version": str(engine_version),
        "source_sha256": str(source_sha256),
        "analysis_sha256": str(analysis_sha256),
        "chunk_sec": float(chunk_sec),
        "model": str(model),
        "language": str(language),
        "transcribe": bool(transcribe),
        "vad_config": dict(vad_config or {}),
    }


def cfg_hash(ident: Dict[str, Any]) -> str:
    """First 8 hex of the canonical identity, source hash excluded."""
    config = {k: v for k, v in ident.items() if k != "source_sha256"}
    return _sha256(_canonical(config))[:8]


def dir_name(ident: Dict[str, Any]) -> str:
    source = str(ident.get("source_sha256", ""))
    return f"{source[:16]}-{cfg_hash(ident)}"


def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON through a same-directory temp file, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


class Store:
    """One checkpoint directory for one identity."""

    def __init__(self, root: str | Path, ident: Dict[str, Any]) -> None:
        self.root = Path(root)
        self.ident = dict(ident)
        self.path = self.root / dir_name(self.ident)

    # -- paths ----------------------------------------------------------- #

    @property
    def manifest_path(self) -> Path:
        return self.path / MANIFEST_NAME

    def chunk_path(self, index: int) -> Path:
        return self.path / f"{CHUNK_PREFIX}{int(index):02d}{CHUNK_SUFFIX}"

    # -- manifest -------------------------------------------------------- #

    def load_manifest(self) -> Optional[Dict[str, Any]]:
        """Return the manifest only when it belongs to this exact identity."""
        data = _read_json(self.manifest_path)
        if data is None:
            return None
        if data.get("schema") != CHECKPOINT_SCHEMA:
            return None
        if str(data.get("engine_version")) != str(self.ident.get("engine_version")):
            return None
        if _canonical(data.get("identity")) != _canonical(self.ident):
            return None
        total = data.get("total_chunks")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            return None
        try:
            chunk_sec = float(data.get("chunk_sec"))
        except (TypeError, ValueError):
            return None
        if chunk_sec <= 0.0:
            return None
        completed = data.get("completed")
        if not isinstance(completed, list):
            return None
        for index in completed:
            if not isinstance(index, int) or isinstance(index, bool):
                return None
            if index < 0 or index >= total:
                return None
        return data

    def write_manifest(self, total_chunks: int, chunk_sec: float) -> Dict[str, Any]:
        """Write the manifest, deriving ``completed`` from readable chunks.

        A chunk is listed as completed only after :meth:`load_chunk` has read
        it back successfully, so a half-written payload can never be marked
        done.
        """
        total = int(total_chunks)
        previous = self.load_manifest()
        created = (previous or {}).get("created") or _now()
        completed: List[int] = [
            index for index in range(total) if self.load_chunk(index) is not None
        ]
        manifest = {
            "schema": CHECKPOINT_SCHEMA,
            "engine_version": str(self.ident.get("engine_version")),
            "identity": dict(self.ident),
            "total_chunks": total,
            "chunk_sec": float(chunk_sec),
            "completed": completed,
            "created": created,
            "updated": _now(),
        }
        _atomic_write(self.manifest_path, manifest)
        return manifest

    # -- chunks ---------------------------------------------------------- #

    def load_chunk(self, index: int) -> Optional[Dict[str, Any]]:
        """Return the payload body, or ``None`` for any kind of cache miss."""
        data = _read_json(self.chunk_path(index))
        if data is None:
            return None
        if data.get("schema") != CHECKPOINT_SCHEMA:
            return None
        if str(data.get("engine_version")) != str(self.ident.get("engine_version")):
            return None
        if _canonical(data.get("identity")) != _canonical(self.ident):
            return None
        if data.get("index") != int(index):
            return None
        body = {k: v for k, v in data.items() if k not in _HEADER_KEYS}
        expected = data.get("payload_sha256")
        if not isinstance(expected, str) or _sha256(_canonical(body)) != expected:
            return None
        return body

    def write_chunk(self, index: int, payload: Dict[str, Any]) -> Path:
        """Atomically write one chunk payload and return its path."""
        body = dict(payload)
        own_start, own_end, decode_start, decode_end = self._geometry(index, body)
        body.pop("chunk", None)
        status = (
            "error"
            if "error" in body and not ("transcript" in body or "vad" in body)
            else "ok"
        )
        header = {
            "schema": CHECKPOINT_SCHEMA,
            "engine_version": str(self.ident.get("engine_version")),
            "identity": dict(self.ident),
            "index": int(index),
            "own_start": float(own_start),
            "own_end": float(own_end),
            "decode_start": float(decode_start),
            "decode_end": float(decode_end),
            "status": status,
            "payload_sha256": _sha256(_canonical(body)),
        }
        target = self.chunk_path(index)
        _atomic_write(target, {**header, **body})
        return target

    def _geometry(self, index: int, body: Dict[str, Any]) -> tuple:
        chunk = body.get("chunk")
        if chunk is not None:
            if isinstance(chunk, dict):
                return (
                    chunk.get("own_start", 0.0),
                    chunk.get("own_end", 0.0),
                    chunk.get("decode_start", 0.0),
                    chunk.get("decode_end", 0.0),
                )
            return (
                getattr(chunk, "own_start", 0.0),
                getattr(chunk, "own_end", 0.0),
                getattr(chunk, "decode_start", 0.0),
                getattr(chunk, "decode_end", 0.0),
            )
        if "own_start" in body:
            return (
                body.get("own_start", 0.0),
                body.get("own_end", 0.0),
                body.get("decode_start", 0.0),
                body.get("decode_end", 0.0),
            )
        try:
            chunk_sec = float(self.ident.get("chunk_sec") or 0.0)
        except (TypeError, ValueError):
            chunk_sec = 0.0
        start = float(index) * chunk_sec
        end = float(index + 1) * chunk_sec
        return start, end, start, end

    # -- discovery / invalidation --------------------------------------- #

    def find_previous(self) -> Optional[str]:
        """A sibling directory with the same config but a different source."""
        try:
            children = list(self.root.iterdir())
        except OSError:
            return None
        wanted = cfg_hash(self.ident)
        source = str(self.ident.get("source_sha256", ""))
        best: Optional[str] = None
        best_updated = ""
        for child in children:
            try:
                if not child.is_dir() or child.resolve() == self.path.resolve():
                    continue
            except OSError:
                continue
            data = _read_json(child / MANIFEST_NAME)
            if data is None:
                continue
            if data.get("schema") != CHECKPOINT_SCHEMA:
                continue
            other = data.get("identity")
            if not isinstance(other, dict):
                continue
            if cfg_hash(other) != wanted:
                continue
            if str(other.get("source_sha256", "")) == source:
                continue
            updated = str(data.get("updated", ""))
            if best is None or updated > best_updated:
                best = str(child)
                best_updated = updated
        return best

    def invalidate(self) -> None:
        """Remove the manifest and every chunk.  Never raises."""
        try:
            self.manifest_path.unlink()
        except OSError:
            pass
        try:
            for path in self.path.glob(f"{CHUNK_PREFIX}*{CHUNK_SUFFIX}"):
                try:
                    path.unlink()
                except OSError:
                    pass
        except OSError:
            pass
