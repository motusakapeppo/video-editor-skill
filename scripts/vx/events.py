"""vx.events — append-only JSONL log of what the pipeline actually did.

One line per boundary, written and flushed the moment the boundary happens: a
crash or a kill leaves every event that already occurred on disk, because
nothing is held in a buffer waiting for a clean shutdown. The file is created
lazily on the first ``emit`` (a run that never starts writes nothing), and the
sequence continues from the last line already present, so re-opening the same
path never restarts numbering and never rewrites a line.

No telemetry, no network, no background thread, no clock other than the UTC
timestamp stamped on each record.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

EVENT_SCHEMA = "vx.event/1.0"

# The contract: every line carries exactly these keys, in this order.
FIELDS = ("seq", "ts", "stage", "code", "status", "ms", "detail")

# Statuses the pipeline uses; the writer does not enforce them so a caller can
# declare a new boundary without editing this module.
STATUSES = ("start", "done", "skip", "fail")


def _timestamp() -> str:
    """ISO8601 in UTC with millisecond precision and a trailing ``Z``."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def read(path: Union[str, Path]) -> List[Dict[str, Any]]:
    """Every well-formed record on disk, in write order.

    A crash can cut the final line in half; a truncated record is not evidence
    of anything, so it is dropped and the records before it are returned
    unchanged.
    """
    target = Path(path)
    if not target.is_file():
        return []
    records: List[Dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _last_seq(path: Path) -> int:
    sequence = 0
    for record in read(path):
        try:
            value = int(record.get("seq") or 0)
        except (TypeError, ValueError):
            continue
        sequence = max(sequence, value)
    return sequence


class EventLog:
    """Append-only writer for one run. ``emit`` is the only way in."""

    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)
        self._handle: Optional[Any] = None
        self._seq = 0

    def _open(self) -> Any:
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # A crash can leave a half-written last line without its newline:
            # close that broken record off so the next one starts on a fresh line
            # instead of being glued to the fragment and lost with it.
            torn = False
            if self.path.is_file() and self.path.stat().st_size:
                with self.path.open("rb") as raw:
                    raw.seek(-1, 2)
                    torn = raw.read(1) != b"\n"
            self._seq = _last_seq(self.path)
            self._handle = self.path.open("a", encoding="utf-8", newline="\n")
            if torn:
                self._handle.write("\n")
                self._handle.flush()
        return self._handle

    def emit(
        self,
        stage: str,
        code: str,
        status: str,
        *,
        ms: float = 0.0,
        detail: str = "",
    ) -> Dict[str, Any]:
        """Append one event and flush it: what happened survives the next crash."""
        handle = self._open()
        self._seq += 1
        record: Dict[str, Any] = {
            "seq": self._seq,
            "ts": _timestamp(),
            "stage": str(stage),
            "code": str(code),
            "status": str(status),
            "ms": float(ms),
            "detail": str(detail),
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        handle.write(line + "\n")
        handle.flush()
        return record

    def close(self) -> None:
        if self._handle is None:
            return
        handle, self._handle = self._handle, None
        try:
            handle.flush()
        finally:
            handle.close()

    def __enter__(self) -> "EventLog":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
