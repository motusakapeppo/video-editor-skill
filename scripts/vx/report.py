"""vx.report — the single JSON artifact every stage writes into.

One report per source, sections merged as the pipeline advances. Writes are
atomic: a ``*.tmp`` sibling is written and fsynced, then ``os.replace`` swaps it
in. A crash mid-write leaves the previous valid file intact.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .contract import SCHEMA, VERSION, validate_report

__all__ = ["new_report", "merge", "write", "read", "validate"]

SOURCE_KEYS = (
    "path",
    "duration",
    "fps",
    "width",
    "height",
    "nb_frames",
    "video_codec",
    "audio_codec",
    "sample_rate",
    "channels",
    "pix_fmt",
    "rotation",
    "has_audio",
    "bitrate",
    "container",
    "size",
    "mtime",
)

SECTIONS = ("analysis", "plan", "render", "qc", "triage", "subs", "audio", "faces")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _deep_merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        current = out.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            out[key] = _deep_merge(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            out[key] = current + value
        else:
            out[key] = value
    return out


def new_report(media: dict, tool: dict | None = None) -> dict:
    """Start a report from a probe dict (``ProbeResult.to_dict()``)."""
    source: Dict[str, Any] = {}
    for key in SOURCE_KEYS:
        if key in media:
            source[key] = media[key]
    if "path" not in source:
        source["path"] = str(media.get("path") or "")
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "created": _now(),
        "updated": _now(),
        "source": source,
        "findings": [],
        "analysis": {},
        "plan": {},
        "render": {},
        "qc": {},
        "tool": dict(tool or {}),
    }


def merge(report: dict, section: str, data) -> dict:
    """Merge ``data`` into ``report[section]`` in place and return the report.

    dict + dict -> recursive merge; list + list -> concatenation; anything else
    replaces. ``findings`` is a top-level list and follows the same rule.
    """
    if not isinstance(report, dict):
        raise TypeError("report must be a dict")
    current = report.get(section)
    if isinstance(current, dict) and isinstance(data, dict):
        report[section] = _deep_merge(current, data)
    elif isinstance(current, list) and isinstance(data, list):
        report[section] = current + data
    else:
        report[section] = data
    report["updated"] = _now()
    return report


def write(report: dict, path) -> str:
    """Atomically write ``report`` to ``path``; return the path as a string."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n"

    handle = tempfile.NamedTemporaryFile(
        "w",
        dir=str(target.parent),
        prefix=target.name + ".",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
        newline="\n",
    )
    tmp = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return str(target)


def read(path) -> dict:
    """Read a report; raises ``ValueError`` when the JSON is not an object."""
    target = Path(path)
    with target.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"report is not a JSON object: {target}")
    return data


def validate(report: dict) -> list[str]:
    """Return a list of problems; empty means the report is well formed."""
    if not isinstance(report, dict):
        return ["report must be a dict"]
    errors = list(validate_report(report))
    if report.get("version") != VERSION:
        errors.append(f"version must be {VERSION}")
    if not isinstance(report.get("source"), dict):
        errors.append("source must be an object")
    for section in SECTIONS:
        if section in report and not isinstance(report[section], (dict, list)):
            errors.append(f"{section} must be an object or a list")
    qc = report.get("qc")
    if isinstance(qc, dict) and qc:
        if "pass" not in qc and "pass_" not in qc:
            errors.append("qc.pass flag missing")
        if not isinstance(qc.get("checks"), list):
            errors.append("qc.checks must be a list")
    return errors


# --------------------------------------------------------------------------- #
# smoke entry
# --------------------------------------------------------------------------- #


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Smoke entry: ``python -m vx.report <path>`` validates an existing report."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        demo = new_report(
            {
                "path": "input.mp4",
                "duration": 8.0,
                "fps": 30.0,
                "width": 1080,
                "height": 1920,
                "has_audio": True,
            },
            tool={"name": "vx", "version": VERSION},
        )
        merge(demo, "findings", [{"id": "F1", "kind": "jitter"}])
        merge(demo, "render", {"precision": "full", "frames": 120})
        print(json.dumps(demo, indent=2))
        print("validate:", validate(demo))
        return 0
    data = read(argv[0])
    problems = validate(data)
    print(json.dumps(problems, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
