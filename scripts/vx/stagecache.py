"""vx.stagecache — content-addressed cache for finished render stages.

The speech engine already caches *analysis* chunks on the source hash, the
analysis hash and the engine version (see :mod:`vx.checkpoint`).  A render has
the same shape one level up: every stage writes one file, the file is a pure
function of the stage's inputs, its parameters and the code that produced it,
and re-running the stage is the expensive part.  This module owns that on-disk
contract.

* :func:`key` — sha256 of the canonical JSON of ``(stage, inputs, params,
  version)``.  Nothing else takes part in the identity: no timestamps, no
  paths, no run order.
* :func:`lookup` — the recorded entry, but only while the artifact still
  exists, its size still matches the size recorded when the stage wrote it, and
  its bytes still hash to the hash recorded at the same moment.  A deleted,
  truncated or overwritten file is a miss, never a false hit: stage artifacts
  live at fixed paths, so two different keys can write the same path, and the
  content hash is what keeps that aliasing honest.
* :func:`record` — atomic JSON write (temp file in the same directory, then
  ``os.replace``), carrying the artifact path, its size, its sha256, the stage
  metadata and the moment it was recorded.

Both halves are journal-aware when, and only when, ``<workdir>/.vx-writes.jsonl``
exists (see :mod:`vx.artifacts`).  ``record`` then brackets the artifact write
with a plan and a completion and places the bytes through
:func:`vx.artifacts.write_bytes_atomic`; ``lookup`` refuses a hit whose path
carries an unfinished intent, which is how a crash between "the bytes are on
disk" and "the entry is trustworthy" becomes a miss instead of a false hit.
Without a journal, neither function changes what it did before.

The dependency map itself lives with the stages (``render.STAGE_DEPENDS``);
:func:`dependents` turns that map into the transitive set of stages an input
change invalidates, so the invalidation rule is executable, not prose.

Reads never raise: a corrupt entry is a cache miss.  Writes never break a
render: an unwritable cache directory is reported through the return value and
ignored.  stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Set

from . import artifacts

STAGE_CACHE_SCHEMA = "vx.stagecache/1"
CACHE_DIR = "stage_cache"
ENTRY_SUFFIX = ".json"


def _canonical(value: Any) -> str:
    """Stable JSON used for hashing: sorted keys, no insignificant spaces."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def key(
    stage: str,
    inputs: Mapping[str, str],
    params: Mapping[str, Any],
    version: str,
) -> str:
    """The cache key of one stage run.

    ``inputs`` are the fingerprints the stage consumes: the source hash, the
    filtergraph text, a subtitle file hash, or the *key* of the upstream stage.
    Using the upstream key rather than its path is what makes a stage
    invalidate exactly its descendants: a stage that did not change keeps its
    key, so nothing downstream of it is recomputed.

    ``version`` is the code version of the stage; bumping it drops every cached
    artifact of that stage, which is the only invalidation that cannot be
    derived from data.
    """
    payload = {
        "schema": STAGE_CACHE_SCHEMA,
        "version": str(version),
        "stage": str(stage),
        "inputs": {str(name): str(value) for name, value in (inputs or {}).items()},
        "params": dict(params or {}),
    }
    return _sha256(_canonical(payload))


def entry_path(workdir: str | Path, digest: str) -> Path:
    """Where the entry for ``digest`` lives: ``<workdir>/stage_cache/<key>.json``."""
    return Path(workdir) / CACHE_DIR / f"{digest}{ENTRY_SUFFIX}"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


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


def lookup(
    workdir: str | Path,
    digest: str,
    *,
    pending: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """The recorded entry for ``digest``, or ``None`` for any kind of miss.

    A hit must satisfy all four: the entry parses and belongs to this key and
    schema, the artifact is still a file, its size equals the size recorded
    when the stage finished, and its bytes hash to the recorded hash.  The size
    check rejects the cheap cases without reading; the hash check is what makes
    a shared artifact path safe.  Nothing here raises.

    One extra miss: when the workdir carries a write-ahead journal and the
    artifact's path has an intent with no completion, a crash may have left a
    half-written file whose bytes still look plausible, so the entry is not
    trusted.  The miss is still a miss — nothing is deleted — and the caller may
    pass a dict as ``pending`` to receive ``pending_write: True`` plus the
    artifact meta and the intent id, ready to log.  Without a journal this
    parameter is never touched.
    """
    data = _read_json(entry_path(workdir, digest))
    if data is None:
        return None
    if data.get("schema") != STAGE_CACHE_SCHEMA:
        return None
    if str(data.get("key") or "") != str(digest):
        return None
    artifact = data.get("artifact")
    if not isinstance(artifact, str) or not artifact:
        return None
    path = Path(artifact)
    size = data.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        return None
    expected = data.get("sha256")
    if not isinstance(expected, str) or not expected:
        return None
    unfinished = artifacts.pending_for(workdir, path)
    if unfinished is not None:
        if pending is not None:
            meta = data.get("meta")
            pending.clear()
            pending.update(
                {
                    "key": str(digest),
                    "path": str(path),
                    "pending_write": True,
                    "intent_id": unfinished.get("id"),
                    "expected_sha256": unfinished.get("expected_sha256"),
                    "meta": {
                        **(meta if isinstance(meta, dict) else {}),
                        "pending_write": True,
                    },
                }
            )
        return None
    try:
        if not path.is_file():
            return None
        actual = path.stat().st_size
    except OSError:
        return None
    if actual != size:
        return None
    if file_sha256(path) != expected:
        return None
    meta = data.get("meta")
    return {
        "key": str(digest),
        "path": str(path),
        "size": actual,
        "sha256": expected,
        "meta": meta if isinstance(meta, dict) else {},
        "recorded": data.get("recorded"),
    }


def record(
    workdir: str | Path,
    digest: str,
    path: str | Path,
    meta: Optional[Mapping[str, Any]] = None,
) -> Optional[Path]:
    """Record ``path`` as the artifact of ``digest``; return the entry path.

    ``None`` means "nothing was recorded" — the artifact does not exist, or the
    cache directory is not writable.  Neither is fatal: the stage already ran,
    the next run simply recomputes it.

    When ``workdir`` carries a write-ahead journal and the artifact lives under
    it, the artifact's bytes are re-placed through
    :func:`vx.artifacts.write_bytes_atomic` between a plan and a completion.
    That is the only write this module owns, and it is journaled *before* it
    starts: a crash between the two records leaves an intent with no completion,
    which :func:`lookup` turns into a miss on the next run.  No journal, no
    difference from the previous behaviour.
    """
    artifact = Path(path)
    try:
        if not artifact.is_file():
            return None
        size = artifact.stat().st_size
    except OSError:
        return None
    checksum = file_sha256(artifact)
    if not checksum:
        return None
    workroot = Path(workdir)
    if artifacts.journal_path(workroot).is_file() and artifacts.inside(
        workroot, artifact
    ):
        intent = artifacts.plan(
            workroot,
            artifact,
            expected_sha256=checksum,
            kind="stage_artifact",
        )
        try:
            payload = artifact.read_bytes()
            artifacts.write_bytes_atomic(artifact, payload, root=workroot)
        except (OSError, ValueError):
            return None
        artifacts.complete(workroot, intent, artifact, sha256=checksum)
    target = entry_path(workdir, digest)
    payload = {
        "schema": STAGE_CACHE_SCHEMA,
        "key": str(digest),
        "artifact": str(artifact),
        "size": int(size),
        "sha256": checksum,
        "meta": dict(meta or {}),
        "recorded": _now(),
    }
    try:
        _atomic_write(target, payload)
    except OSError:
        return None
    return target


def file_sha256(path: str | Path | None) -> str:
    """sha256 of a file's bytes, or ``""`` when it cannot be read.

    Used for inputs whose *path* is stable but whose *bytes* are not — a
    subtitle file rewritten in place must invalidate the graph that renders it.
    """
    if path is None:
        return ""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()


def dependents(
    depends: Mapping[str, Iterable[str]], changed: Iterable[str]
) -> Set[str]:
    """The stages a change to ``changed`` invalidates, transitively.

    ``changed`` may name an input slot (``"subtitles"``) or an upstream stage
    (``"01_trim"``).  A stage depends on another stage by naming its key, which
    is why a stage reached through an unchanged parent is not invalidated.
    """
    hit: Set[str] = set()
    wanted = set(changed)
    for _ in range(len(depends) + 1):
        grown = {
            stage
            for stage, deps in depends.items()
            if stage not in hit
            and (any(dep in wanted for dep in deps) or any(dep in hit for dep in deps))
        }
        if not grown:
            break
        hit |= grown
    return hit
