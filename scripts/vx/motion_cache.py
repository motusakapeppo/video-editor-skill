"""vx.motion_cache -- the frame cache for Motion Studio.

A capture renders one PNG per frame of virtual time.  Rendering the same
template with the same parameters twice is pure waste, so the frames are kept
-- but only where they belong: *inside the job work directory*, at
``<workdir>/.motion-cache/``, declared in that work directory's ``.vx-owned``
manifest.  There is no global cache directory: a relative path such as
``stage/motion_cache`` would not be ours, would not be quarantined on delete,
and two jobs rendering different templates would overwrite each other's frames.
When no work directory is given the cache is disabled and the capture says so.

The key is a sha256 over everything that can move a pixel:

* the *content* of the template -- sha256 of ``index.html`` and
  ``template.json`` -- never the template's name.  Editing the CSS of a
  template keeps its name and must invalidate every frame; hashing the name
  would keep serving the old picture, which is the bug this cache exists under.
* the resolved parameters, as canonical JSON.
* ``fps``, ``size`` and ``engine``.  Two engines are two different rasterisers,
  so a frame captured by Chrome is never served to Edge.
* a stage version, bumped when the capture harness itself changes the pixels.
* the frame time, rounded to 6 decimals so float noise cannot split one frame
  into two keys.

Frames are written atomically and journalled: :func:`vx.artifacts.plan` before
the bytes, :func:`vx.artifacts.write_bytes_atomic` for the bytes,
:func:`vx.artifacts.complete` after, so a crash leaves a detectable open intent
rather than a half-written frame that looks plausible.

Every look at the disk re-hashes the PNG.  A truncated screenshot, a file
replaced behind our back, a corrupt index, a corrupt entry: all of them are a
MISS, counted in ``damaged``, never fatal.  A PNG below ``MIN_FRAME_BYTES`` is
never stored, so a truncated screenshot cannot become a permanent hit.

stdlib only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import artifacts, vault
from .contract import VxError

SCHEMA = "vx.motion.cache/1"
STAGE_VERSION = "f3-t05.1"
CACHE_DIRNAME = ".motion-cache"
INDEX_NAME = "index.json"
FRAME_KIND = "motion-cache-frame"
INDEX_KIND = "motion-cache-index"
JOURNAL_KIND = "write-journal"

#: A PNG smaller than this is not a frame.  A 240x240 flat-colour capture is a
#: handful of kilobytes; a truncated screenshot is a few dozen bytes.  The floor
#: keeps the second one from becoming a permanent hit.
MIN_FRAME_BYTES = 256

_TEMPLATE_FILES = ("index.html", "template.json")
_SHA_HEX = frozenset("0123456789abcdef")


class CacheError(VxError):
    """A declared cache failure: the key could not be built or the state is gone."""


class CacheRefused(CacheError):
    """The bytes handed to :meth:`Cache.put` are below the frame floor."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical(value: Any) -> str:
    """Canonical JSON: sorted keys, no whitespace, no NaN, stable numbers."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CacheError(
            f"cannot canonicalise {type(value).__name__} for the cache key: {exc}"
        ) from exc


def template_digest(template_dir: str | Path) -> str:
    """sha256 over the template's *content*: both files, name-tagged, in order.

    The directory name is deliberately absent: renaming a template does not
    change a pixel, and editing it does.  The file names are mixed in so a
    swap between the two files cannot collide.
    """
    root = Path(template_dir)
    digest = hashlib.sha256()
    digest.update(b"vx.motion.template/1\0")
    for name in _TEMPLATE_FILES:
        digest.update(name.encode("utf-8") + b"\0")
        try:
            data = (root / name).read_bytes()
        except OSError as exc:
            raise CacheError(
                f"cannot read {name} of {root} for the cache key: {exc}"
            ) from exc
        digest.update(str(len(data)).encode("ascii") + b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def _clean_entry(value: Any) -> Optional[Dict[str, Any]]:
    """One index record, or ``None`` when it is not believable."""
    if not isinstance(value, dict):
        return None
    sha = value.get("sha256")
    size = value.get("size")
    created = value.get("created_at")
    if not isinstance(sha, str) or len(sha) != 64 or not set(sha.lower()) <= _SHA_HEX:
        return None
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return None
    if not isinstance(created, str) or not created:
        return None
    return {"sha256": sha.lower(), "size": int(size), "created_at": created}


class Cache:
    """Frame cache rooted at ``<workdir>/.motion-cache``.

    ``Cache(None)`` is a disabled cache: ``enabled`` is False, ``get`` is always
    a miss that writes nothing, and ``put`` refuses.  Constructing a disabled
    cache touches no filesystem.
    """

    def __init__(self, workdir: str | Path | None, *, enabled: bool = True) -> None:
        self._workdir = Path(workdir) if workdir is not None else None
        self.enabled = bool(enabled) and self._workdir is not None
        self._dir = (
            (self._workdir / CACHE_DIRNAME)
            if self.enabled and self._workdir is not None
            else None
        )
        self._hits = 0
        self._misses = 0
        self._writes = 0
        self._bytes = 0
        self._damaged = 0
        self._refused = 0
        self._registered = 0
        self._vault_errors: List[str] = []
        self._declared = False
        self._index: Optional[Dict[str, Dict[str, Any]]] = None

    # -- geometry ----------------------------------------------------------
    @property
    def directory(self) -> Optional[Path]:
        """The cache directory, or ``None`` when disabled.  Never created here."""
        return self._dir

    @property
    def index_path(self) -> Optional[Path]:
        return None if self._dir is None else self._dir / INDEX_NAME

    def _frame_path(self, key: str) -> Path:
        if self._dir is None:
            raise CacheError("cache is disabled: there is no frame directory")
        return self._dir / f"{key}.png"

    def _workdir_or_raise(self) -> Path:
        if self._workdir is None:
            raise CacheError("cache is disabled: there is no work directory")
        return self._workdir

    def _ensure_declared(self) -> None:
        """Put the ownership manifest in place before the first byte.

        ``vault.declare`` is idempotent and cheap; a failure here is recorded
        and re-attempted by :meth:`register`, never fatal for the capture.
        """
        if self._declared:
            return
        workdir = self._workdir_or_raise()
        try:
            vault.declare(workdir)
            self._declared = True
        except (VxError, OSError) as exc:
            self._vault_errors.append(f"declare: {exc}")

    # -- keys --------------------------------------------------------------
    def key(
        self,
        template_dir: str | Path,
        params: Dict[str, Any] | None,
        *,
        fps: float,
        size: Sequence[int],
        engine: str,
        t: float,
    ) -> str:
        """sha256 of the canonical payload; identical inputs, identical key."""
        dimensions = tuple(size)
        if len(dimensions) != 2:
            raise CacheError(f"size must be [width, height], got {size!r}")
        width, height = int(dimensions[0]), int(dimensions[1])
        payload = {
            "schema": SCHEMA,
            "stage": STAGE_VERSION,
            "template": template_digest(template_dir),
            "params": dict(params or {}),
            "fps": float(fps),
            "size": [width, height],
            "engine": str(engine),
            "t": round(float(t), 6),
        }
        return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()

    # -- index -------------------------------------------------------------
    def _load_index(self) -> Dict[str, Dict[str, Any]]:
        """The index, or an empty one; damage is counted, never raised."""
        if self._index is not None:
            return self._index
        entries: Dict[str, Dict[str, Any]] = {}
        target = self.index_path
        if target is None or not target.is_file():
            self._index = entries
            return entries
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._damaged += 1
            self._index = entries
            return entries
        if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
            self._damaged += 1
            self._index = entries
            return entries
        stored = raw.get("entries")
        if not isinstance(stored, dict):
            self._damaged += 1
            self._index = entries
            return entries
        for raw_key, raw_entry in stored.items():
            if not isinstance(raw_key, str) or len(raw_key) != 64:
                self._damaged += 1
                continue
            entry = _clean_entry(raw_entry)
            if entry is None:
                self._damaged += 1
                continue
            entries[raw_key.lower()] = entry
        self._index = entries
        return entries

    def _write_index(self, entries: Dict[str, Dict[str, Any]]) -> None:
        """Rewrite ``index.json`` atomically, inside the write journal."""
        workdir = self._workdir_or_raise()
        target = self.index_path
        if target is None:
            raise CacheError("cache is disabled: there is no index")
        payload = {"schema": SCHEMA, "stage": STAGE_VERSION, "entries": entries}
        data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        sha = hashlib.sha256(data).hexdigest()
        intent = artifacts.plan(workdir, target, expected_sha256=sha, kind=INDEX_KIND)
        artifacts.write_bytes_atomic(target, data, root=workdir)
        artifacts.complete(workdir, intent, target, sha256=sha)

    # -- reads -------------------------------------------------------------
    def read(self, key: str) -> Optional[bytes]:
        """The verified bytes of a cached frame, or ``None`` and a counted miss.

        Verification is a full re-hash: the file must exist, be at least the
        frame floor, match the recorded size and hash the way the index says.
        """
        if not self.enabled:
            return None
        entry = self._load_index().get(key)
        if entry is None:
            self._misses += 1
            return None
        try:
            data = self._frame_path(key).read_bytes()
        except OSError:
            self._damaged += 1
            self._misses += 1
            return None
        if len(data) < MIN_FRAME_BYTES or len(data) != entry["size"]:
            self._damaged += 1
            self._misses += 1
            return None
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            self._damaged += 1
            self._misses += 1
            return None
        self._hits += 1
        return data

    def get(self, key: str) -> Optional[Path]:
        """The cached frame path, only when :meth:`read` would return its bytes."""
        if self.read(key) is None:
            return None
        return self._frame_path(key)

    # -- writes ------------------------------------------------------------
    def put(self, key: str, png_bytes: bytes) -> Path:
        """Store one frame atomically and record it; returns its path.

        Raises :class:`CacheRefused` (and stores nothing) when the payload is
        below :data:`MIN_FRAME_BYTES`: a truncated screenshot must not become a
        permanent hit.
        """
        if not self.enabled:
            raise CacheError("cache is disabled: refusing to write a frame")
        data = bytes(png_bytes)
        if len(data) < MIN_FRAME_BYTES:
            self._refused += 1
            raise CacheRefused(
                f"frame of {len(data)} bytes is below the {MIN_FRAME_BYTES}-byte "
                "floor; not cached"
            )
        workdir = self._workdir_or_raise()
        target = self._frame_path(key)
        self._ensure_declared()
        sha = hashlib.sha256(data).hexdigest()
        intent = artifacts.plan(workdir, target, expected_sha256=sha, kind=FRAME_KIND)
        artifacts.write_bytes_atomic(target, data, root=workdir)
        artifacts.complete(workdir, intent, target, sha256=sha)
        entries = self._load_index()
        entries[key] = {
            "sha256": sha,
            "size": len(data),
            "created_at": _now_iso(),
        }
        try:
            self._write_index(entries)
        except (OSError, VxError):
            # The frame is on disk and will be a miss until the index catches up:
            # a declared degradation, not a failed render.
            self._index = None
            self._damaged += 1
        self._writes += 1
        self._bytes += len(data)
        return target

    # -- ownership ---------------------------------------------------------
    def register(self) -> Dict[str, Any]:
        """Record every cache file in the work directory's ``.vx-owned`` manifest."""
        if not self.enabled or self._workdir is None:
            return {"registered": 0, "errors": []}
        workdir = self._workdir
        self._ensure_declared()
        targets: List[Tuple[Path, str]] = []
        if self._dir is not None and self._dir.is_dir():
            for path in sorted(self._dir.rglob("*")):
                if path.is_file():
                    kind = INDEX_KIND if path.name == INDEX_NAME else FRAME_KIND
                    targets.append((path, kind))
        journal = artifacts.journal_path(workdir)
        if journal.is_file():
            targets.append((journal, JOURNAL_KIND))
        registered = 0
        errors: List[str] = []
        for path, kind in targets:
            try:
                vault.register(workdir, path, kind=kind)
                registered += 1
            except (VxError, OSError) as exc:
                errors.append(f"{path.name}: {exc}")
        self._registered = registered
        self._vault_errors = self._vault_errors + errors
        return {"registered": registered, "errors": list(errors)}

    # -- reporting ---------------------------------------------------------
    def stats(self) -> Dict[str, int]:
        self._load_index()
        return {
            "hits": self._hits,
            "misses": self._misses,
            "writes": self._writes,
            "bytes": self._bytes,
            "damaged": self._damaged,
            "refused": self._refused,
        }

    def report(self) -> Dict[str, Any]:
        """The ``cache`` block a capture returns; disabled says so explicitly."""
        counters = self.stats()
        if not self.enabled:
            return {
                "enabled": False,
                "status": "disabled",
                "dir": None,
                "registered": 0,
                "vault_errors": [],
                "frame_floor_bytes": MIN_FRAME_BYTES,
                "stage": STAGE_VERSION,
                **counters,
            }
        return {
            "enabled": True,
            "status": "active",
            "dir": None if self._dir is None else str(self._dir),
            "registered": self._registered,
            "vault_errors": list(self._vault_errors),
            "frame_floor_bytes": MIN_FRAME_BYTES,
            "stage": STAGE_VERSION,
            **counters,
        }
