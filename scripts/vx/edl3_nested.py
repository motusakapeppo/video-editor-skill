"""vx.edl3_nested — nested sequences as virtual sources.

A timeline's clip can cite a ``sequence`` instead of a ``source``. That must not
become a special case sprinkled through the compiler: an inner timeline is
rendered once, exactly like a file would be, and its result joins the parent's
source table under a deterministic key. This module computes that execution
plan and nothing else — no ffmpeg, no files, no mutation of the document.

Plan shape
----------
``resolve(doc)`` returns::

    {
      "depth": 2,                              # nesting levels below ``doc``
      "order": ["inner", "outer"],             # render order, children first
      "renders": [                             # one entry per distinct sequence
        {"id": "inner", "timeline": Timeline3, "depth": 1,
         "virtual_source": "seq_inner_ab12..."},
        {"id": "outer", "timeline": Timeline3, "depth": 2,
         "virtual_source": "seq_outer_cd34..."},
      ],
      "sources": {"r1": "seq_outer_cd34...",   # root clip -> virtual source
                  "outer/o1": "seq_inner_ab12..."},
    }

Rules the plan enforces
-----------------------
* A sequence referenced twice, at any depth, is **rendered once**: the second
  reference reuses its virtual source. ``renders`` length is the number of
  distinct sequences that are actually reachable.
* Children render before parents, because a parent consumes the child's output
  as a source. ``order`` is that topological order.
* A reference cycle is refused with ``NESTING_CYCLE``; exceeding ``max_depth``
  is refused with ``NESTING_TOO_DEEP``. Both carry the path.
* ``sources`` keys are clip ids for the document passed in; clips inside a
  sequence are qualified as ``"<sequence id>/<clip id>"`` so two timelines that
  reuse a clip id never collide.

Declared numbers
----------------
=================  ======  ==============  ===================================
constant           value   range           meaning
=================  ======  ==============  ===================================
DEFAULT_MAX_DEPTH  3       [1, 16]         nesting levels allowed by default
MIN_DEPTH          1       [1, 16]         below one there is nothing to nest
MAX_DEPTH_CEILING  16      [1, 16]         refuse absurd limits loudly
MIN_START_DEPTH    0       [0, 16]         ``depth`` of the document itself
HASH_LENGTH        16      16 hex chars    digest characters in the source id
=================  ======  ==============  ===================================
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .edl3 import Clip3, NestedSequence, Timeline3

__all__ = [
    "NestingError",
    "DEFAULT_MAX_DEPTH",
    "MIN_DEPTH",
    "MAX_DEPTH_CEILING",
    "MIN_START_DEPTH",
    "HASH_LENGTH",
    "SOURCE_KEY_PATTERN",
    "resolve",
    "virtual_source_id",
    "canonical_timeline",
]

DEFAULT_MAX_DEPTH = 3
MIN_DEPTH = 1
MAX_DEPTH_CEILING = 16
MIN_START_DEPTH = 0
HASH_LENGTH = 16

#: A legal EDL source key: letters, digits, underscore, dot, hyphen.
SOURCE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class NestingError(Exception):
    """A nesting plan cannot be produced; ``code`` is stable and actionable."""

    def __init__(self, code: str, detail: str, where: str = "") -> None:
        self.code = code
        self.detail = detail
        self.where = where
        text = f"{code}: {detail}"
        if where:
            text = f"{text} ({where})"
        super().__init__(text)


# ------------------------------------------------------------- coercion


def _as_timeline(value: Any, where: str = "document") -> Timeline3:
    if isinstance(value, Timeline3):
        return value
    if isinstance(value, Mapping):
        return Timeline3.from_dict(value)
    raise NestingError(
        "NESTING_SHAPE", f"{where} is not a vx.edl3 timeline: {type(value).__name__}"
    )


def _as_sequence(value: Any, sequence_id: str) -> Timeline3:
    if isinstance(value, NestedSequence):
        return value.timeline
    if isinstance(value, Timeline3):
        return value
    if isinstance(value, Mapping):
        inner = value.get("timeline")
        if isinstance(inner, Mapping):
            return Timeline3.from_dict(inner)
    raise NestingError(
        "NESTING_SHAPE",
        f"sequence {sequence_id!r} does not carry a timeline",
        f"sequences.{sequence_id}",
    )


def _int(name: str, value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NestingError(
            "NESTING_DEPTH_INVALID", f"{name} must be an integer, got {value!r}"
        ) from exc
    if not math.isfinite(number) or number != int(number):
        raise NestingError(
            "NESTING_DEPTH_INVALID", f"{name} must be an integer, got {value!r}"
        )
    return int(number)


# ------------------------------------------------------- canonical + ids


def canonical_timeline(timeline: Any) -> str:
    """Canonical JSON of a timeline: sorted keys, no whitespace, ASCII only."""
    document = _as_timeline(timeline).to_dict()
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def virtual_source_id(sequence_id: Any, timeline: Any) -> str:
    """Deterministic, legal EDL source key for a rendered inner sequence.

    The digest covers the sequence id *and* the canonical document, so two
    different inner timelines that happen to share a name never share a source,
    and the same pair always yields the same key across runs and machines.
    """
    raw = str(sequence_id).strip()
    if not raw:
        raise NestingError("NESTING_SHAPE", "sequence id is empty")
    document = _as_timeline(timeline, f"sequence {raw!r}")
    body = f"{raw}\x00{canonical_timeline(document)}"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:HASH_LENGTH]
    safe = _UNSAFE.sub("_", raw).strip("._-") or "sequence"
    key = f"seq_{safe}_{digest}"
    if not SOURCE_KEY_PATTERN.match(key):
        # ``safe`` is already reduced to the legal alphabet and prefixed with a
        # letter, so this is unreachable; it is a guard, not a fallback.
        raise NestingError(
            "NESTING_SHAPE", f"derived source key {key!r} is not a legal EDL key"
        )
    return key


# ------------------------------------------------------------------ plan


def resolve(
    doc: Any, *, depth: Any = MIN_START_DEPTH, max_depth: Any = DEFAULT_MAX_DEPTH
) -> Dict[str, Any]:
    """Return the render plan for ``doc``: children first, each sequence once.

    ``depth`` is the nesting level at which ``doc`` itself already sits; it is
    only used to bound recursion and to report relative depth. ``max_depth`` is
    the number of nesting levels allowed below ``doc``.
    """
    root = _as_timeline(doc)
    start = _int("depth", depth)
    limit = _int("max_depth", max_depth)
    if start < MIN_START_DEPTH or start > MAX_DEPTH_CEILING:
        raise NestingError(
            "NESTING_DEPTH_INVALID",
            f"depth {start} outside [{MIN_START_DEPTH}, {MAX_DEPTH_CEILING}]",
        )
    if limit < MIN_DEPTH or limit > MAX_DEPTH_CEILING:
        raise NestingError(
            "NESTING_DEPTH_INVALID",
            f"max_depth {limit} outside [{MIN_DEPTH}, {MAX_DEPTH_CEILING}]",
        )
    if start > limit:
        raise NestingError(
            "NESTING_TOO_DEEP",
            f"document already sits at depth {start}, above max_depth {limit}",
        )

    renders: List[Dict[str, Any]] = []
    order: List[str] = []
    rendered: Dict[str, Dict[str, Any]] = {}
    sources: Dict[str, str] = {}
    deepest = start

    def source_key(owner: Optional[str], clip_id: str) -> str:
        return clip_id if owner is None else f"{owner}/{clip_id}"

    def walk(
        timeline: Timeline3, level: int, stack: Tuple[str, ...], owner: Optional[str]
    ) -> None:
        for index, clip in enumerate(timeline.clips):
            _refuse_clip_shape(clip, index, owner)
            if clip.sequence is None:
                continue
            sequence_id = str(clip.sequence)
            if sequence_id in stack:
                path = " -> ".join(list(stack) + [sequence_id])
                raise NestingError(
                    "NESTING_CYCLE",
                    f"sequence reference cycle: {path}",
                    f"{owner or 'document'}:clips[{index}].sequence",
                )
            if sequence_id not in timeline.sequences:
                raise NestingError(
                    "NESTING_UNKNOWN",
                    f"no nested sequence {sequence_id!r} on this timeline; "
                    f"declared: {sorted(timeline.sequences)}",
                    f"{owner or 'document'}:clips[{index}].sequence",
                )
            entry = rendered.get(sequence_id)
            if entry is None:
                entry = render_sequence(
                    sequence_id,
                    timeline.sequences[sequence_id],
                    level + 1,
                    stack + (sequence_id,),
                )
            sources[source_key(owner, clip.id)] = str(entry["virtual_source"])

    def render_sequence(
        sequence_id: str,
        raw_sequence: Any,
        level: int,
        stack: Tuple[str, ...],
    ) -> Dict[str, Any]:
        nonlocal deepest
        if level > limit:
            path = " -> ".join(["document"] + list(stack))
            raise NestingError(
                "NESTING_TOO_DEEP",
                f"sequence {sequence_id!r} sits at nesting level {level}, above "
                f"max_depth {limit} ({path}); flatten the document or raise the "
                "limit",
                f"sequences.{sequence_id}",
            )
        inner = _as_sequence(raw_sequence, sequence_id)
        walk(inner, level, stack, sequence_id)
        entry: Dict[str, Any] = {
            "id": sequence_id,
            "timeline": inner,
            "depth": level,
            "virtual_source": virtual_source_id(sequence_id, inner),
        }
        rendered[sequence_id] = entry
        renders.append(entry)
        order.append(sequence_id)
        deepest = max(deepest, level)
        return entry

    walk(root, start, (), None)

    return {
        "depth": deepest - start,
        "order": order,
        "renders": renders,
        "sources": sources,
    }


def _refuse_clip_shape(clip: Clip3, index: int, owner: Optional[str]) -> None:
    where = f"{owner or 'document'}:clips[{index}]"
    if clip.sequence is not None and clip.source:
        raise NestingError(
            "NESTING_SHAPE",
            f"clip {clip.id!r} declares both source and sequence; a clip is one "
            "or the other",
            where,
        )
    if clip.sequence is not None and not str(clip.sequence).strip():
        raise NestingError(
            "NESTING_SHAPE", f"clip {clip.id!r} has an empty sequence id", where
        )
