"""vx.motion_registry -- the catalogue of Motion Studio templates.

`motion.load_template` reads ONE directory and refuses it; `motion_lint.lint`
audits ONE directory. A caller that offers "the templates" needs a collection
that has already paid both gates, and it needs the collection to survive one
broken template instead of losing every good one.

Four decisions, each with its reason:

* **Lazy.** Importing this module touches no filesystem. The scan runs on the
  first public call, so an importer that only wants the error class, or a test
  that only checks importability, pays nothing.
* **Linted at load.** Every directory is passed to
  `motion_lint.lint(..., runtime_check=False)` before it is admitted. The static
  pass is the admission gate; the runtime clock probe needs a real browser and
  belongs to `capture`, not to a catalogue lookup, so it is never run here. A
  directory with findings is EXCLUDED from `names()`/`get()`/`describe()` and
  recorded with every finding under `blocked()`. Never silently listed, never
  fatal to its neighbours.
* **No second manifest parser.** `motion.load_template` already validates
  name/fps/duration/size/params and is the loader `capture()` uses; a private
  parser would drift from it. The linter allows exactly the top-level keys
  `name`, `fps`, `duration`, `size`, `params`, so `title` and `category` cannot
  live in `template.json` without being rejected. They are DERIVED from the
  declared name instead -- see `_derive_title` and `_derive_category`.
* **Rescan without a new process.** `add(root)` registers another templates
  root, `refresh()` rescans the roots already known; both replace the catalogue
  in place.

`validate_call` delegates to `motion.validate_params`, which already merges
defaults and raises `PARAM_UNKNOWN` / `PARAM_RANGE` / `PARAM_TYPE` naming the
parameter and its domain. Reusing it keeps one definition of "a valid call".

stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import motion, motion_lint
from .contract import VxError

__all__ = ["Registry", "RegistryError", "TemplateEntry"]

_NAME_PREFIX = "vx-"
_WORDS_RE = re.compile(r"[-_\s]+")


class RegistryError(VxError):
    """A declared catalogue failure: the name is missing or blocked."""

    def __init__(self, code: str, message: str, **payload: Any) -> None:
        self.code = code
        self.payload: Dict[str, Any] = {"code": code, "message": message, **payload}
        super().__init__(f"{code}: {message}")

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else self.payload["code"]


def _name_words(name: str) -> List[str]:
    stem = name[len(_NAME_PREFIX) :] if name.startswith(_NAME_PREFIX) else name
    return [word for word in _WORDS_RE.split(stem) if word]


def _derive_title(name: str) -> str:
    """`vx-title-lower-third` -> `Title Lower Third`; any other name, kept."""
    words = _name_words(name)
    if not words:
        return name
    return " ".join(word[:1].upper() + word[1:] for word in words)


def _derive_category(name: str) -> str:
    """The first segment after `vx-`: the catalogue groups by kind, not by file."""
    words = _name_words(name)
    return words[0] if words else "general"


@dataclass(frozen=True)
class TemplateEntry:
    """One admitted template: exactly what `describe()` promises, plus its dir."""

    name: str
    title: str
    category: str
    params: Dict[str, Dict[str, Any]]
    fps: float
    duration: float
    size: Tuple[int, int]
    frames: int
    directory: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "category": self.category,
            "params": {key: dict(value) for key, value in self.params.items()},
            "fps": self.fps,
            "duration": self.duration,
            "size": [int(self.size[0]), int(self.size[1])],
            "frames": self.frames,
        }


def _entry_from(meta: Dict[str, Any]) -> TemplateEntry:
    name = str(meta["name"])
    fps = float(meta["fps"])
    duration = float(meta["duration"])
    size = (int(meta["size"][0]), int(meta["size"][1]))
    return TemplateEntry(
        name=name,
        title=_derive_title(name),
        category=_derive_category(name),
        params={key: dict(value) for key, value in meta["params"].items()},
        fps=fps,
        duration=duration,
        size=size,
        frames=int(round(fps * duration)),
        directory=str(meta["dir"]),
    )


class Registry:
    """A lazy, linted catalogue of template directories.

    `Registry()` uses `motion.templates_root()`; `Registry(path)` uses `path`.
    Nothing is read until the first call on the instance.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self._roots: List[Path] = [
            Path(root) if root is not None else motion.templates_root()
        ]
        self._entries: Dict[str, TemplateEntry] = {}
        self._blocked: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    # -- roots -----------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._roots[0]

    def roots(self) -> Tuple[Path, ...]:
        return tuple(self._roots)

    def add(self, root: str | Path) -> "Registry":
        """Register another templates root and rescan; returns `self`."""
        given = Path(root)
        if given not in self._roots:
            self._roots.append(given)
        return self.refresh()

    def refresh(self) -> "Registry":
        """Rescan every known root in place; returns `self`."""
        self._scan()
        return self

    # -- loading ---------------------------------------------------------

    def _ensure(self) -> None:
        if not self._loaded:
            self._scan()

    @staticmethod
    def _children(root: Path) -> List[Path]:
        try:
            listing = sorted(root.iterdir(), key=lambda item: item.name)
        except OSError:
            return []
        # Only dot-directories are skipped: they are editor/OS debris, not
        # templates. Anything else is scanned and, if wrong, reported by
        # `blocked()` -- never dropped without a trace.
        return [
            item for item in listing if item.is_dir() and not item.name.startswith(".")
        ]

    @staticmethod
    def _lint(template_dir: Path) -> Dict[str, Any]:
        try:
            return motion_lint.lint(template_dir, runtime_check=False)
        except Exception as exc:  # noqa: BLE001 - one broken tree must not kill the rest
            return {
                "ok": False,
                "findings": [
                    {
                        "code": "LINT_ERROR",
                        "where": template_dir.name,
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                ],
                "stats": {"files": 0, "bytes": 0, "deps": 0},
            }

    def _record_blocked(
        self,
        blocked: Dict[str, Dict[str, Any]],
        child: Path,
        code: str,
        findings: List[Dict[str, Any]],
        stats: Dict[str, Any],
    ) -> None:
        key = child.name
        if key in blocked:
            key = str(child)
        blocked[key] = {
            "dir": str(child),
            "code": code,
            "findings": [dict(item) for item in findings],
            "stats": dict(stats),
        }

    def _scan(self) -> None:
        entries: Dict[str, TemplateEntry] = {}
        blocked: Dict[str, Dict[str, Any]] = {}
        for root in self._roots:
            for child in self._children(root):
                report = self._lint(child)
                if not report["ok"]:
                    self._record_blocked(
                        blocked,
                        child,
                        "MOTION_LINT_FAILED",
                        report["findings"],
                        report["stats"],
                    )
                    continue
                try:
                    entry = _entry_from(motion.load_template(child))
                except motion.MotionError as exc:
                    self._record_blocked(
                        blocked,
                        child,
                        exc.code,
                        [
                            {
                                "code": exc.code,
                                "where": "template.json",
                                "detail": exc.payload.get("message", str(exc)),
                            }
                        ],
                        report["stats"],
                    )
                    continue
                already = entries.get(entry.name)
                if already is not None:
                    self._record_blocked(
                        blocked,
                        child,
                        "TEMPLATE_DUPLICATE",
                        [
                            {
                                "code": "TEMPLATE_DUPLICATE",
                                "where": "template.json",
                                "detail": (
                                    f"declares name {entry.name!r}, already "
                                    f"registered by {already.directory}"
                                ),
                            }
                        ],
                        report["stats"],
                    )
                    continue
                entries[entry.name] = entry
        self._entries = entries
        self._blocked = blocked
        self._loaded = True

    # -- read surface ----------------------------------------------------

    def names(self) -> List[str]:
        self._ensure()
        return sorted(self._entries)

    def list(self) -> List[TemplateEntry]:
        self._ensure()
        return [self._entries[key] for key in sorted(self._entries)]

    def get(self, name: str) -> Optional[TemplateEntry]:
        self._ensure()
        return self._entries.get(str(name))

    def params(self, name: str) -> Dict[str, Dict[str, Any]]:
        return {key: dict(value) for key, value in self._require(name).params.items()}

    def describe(self) -> Dict[str, Dict[str, Any]]:
        self._ensure()
        return {key: self._entries[key].as_dict() for key in sorted(self._entries)}

    def blocked(self) -> Dict[str, Dict[str, Any]]:
        self._ensure()
        return {
            key: {
                "dir": item["dir"],
                "code": item["code"],
                "findings": [dict(finding) for finding in item["findings"]],
                "stats": dict(item["stats"]),
            }
            for key, item in sorted(self._blocked.items())
        }

    # -- calls -----------------------------------------------------------

    def _require(self, name: str) -> TemplateEntry:
        requested = str(name)
        entry = self.get(requested)
        if entry is not None:
            return entry
        blocked = self.blocked().get(requested)
        if blocked is not None:
            codes = ", ".join(sorted({item["code"] for item in blocked["findings"]}))
            raise RegistryError(
                "TEMPLATE_BLOCKED",
                f"template {requested!r} is blocked by the linter: {codes}",
                template=requested,
                findings=blocked["findings"],
            )
        raise RegistryError(
            "TEMPLATE_MISSING",
            f"no template named {requested!r}; known: {self.names()}",
            template=requested,
            known=self.names(),
        )

    def validate_call(
        self, name: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Merge defaults and validate; `motion.validate_params` owns the codes."""
        entry = self._require(name)
        return motion.validate_params(
            {"name": entry.name, "params": entry.params}, params
        )
