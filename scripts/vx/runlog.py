"""vx.runlog — the job's state contract, one event per line, never rewritten.

``<workdir>/.vx/run.jsonl`` is the only answer to "is this job done?". A stage
is done when its ``sign`` event is on disk with the value it measured and the
threshold it had to pass; "the master exists" is not a signature.

Every line carries its own value, threshold, verdict and a sha256 chained to
the previous line: a line that was edited, reordered or truncated is visible,
not silently trusted. Writes go through ``tmp -> fsync -> os.replace`` so a
reader never sees a half line; the tolerant read still drops a truncated
*last* line (a crash between the bytes and the newline) and refuses with
``RUN_LOG_CORRUPT`` + the line number when the damage is in the middle.

    python - <<'PY'
    from vx import runlog
    runlog.append(workdir, stage="doctor", event="sign",
                  contract="verdict != broken", value="ready",
                  threshold=None, ok=True, why="ambiente pronto")
    print(runlog.verify(workdir))
    PY
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

__all__ = [
    "SCHEMA_VERSION",
    "STAGES",
    "EVENTS",
    "RunLogCorrupt",
    "path",
    "append",
    "read",
    "signed",
    "verify",
]

SCHEMA_VERSION = 1

#: The fixed stages of the contract: a job is delivered only when all are signed.
STAGES: tuple[str, ...] = (
    "doctor",
    "ingest",
    "analyze",
    "decide",
    "gates",
    "render",
    "qc",
    "deliver",
)

EVENTS = frozenset({"start", "sign", "skip", "fail"})

_DIR_NAME = ".vx"
_FILE_NAME = "run.jsonl"


class RunLogCorrupt(Exception):
    """A line in the middle of the log cannot be trusted.

    ``line`` is 1-based, so the message names exactly what to look at.
    """

    def __init__(self, line: int, why: str) -> None:
        self.line = int(line)
        self.why = str(why)
        super().__init__(f"RUN_LOG_CORRUPT: riga {self.line}: {self.why}")


def path(workdir: Any) -> Path:
    """The log path for ``workdir``: ``<workdir>/.vx/run.jsonl``."""
    return Path(workdir) / _DIR_NAME / _FILE_NAME


def _canonical(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(previous_sha: str, payload: Dict[str, Any]) -> str:
    return hashlib.sha256((previous_sha + _canonical(payload)).encode("utf-8")).hexdigest()


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _payload(record: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in record.items() if key != "sha"}


def _validate_shape(record: Any, line: int) -> Dict[str, Any]:
    if not isinstance(record, dict):
        raise RunLogCorrupt(line, "la riga non e' un oggetto JSON")
    if record.get("v") != SCHEMA_VERSION:
        raise RunLogCorrupt(
            line, f"schemaVersion inattesa: {record.get('v')!r} (attesa {SCHEMA_VERSION})"
        )
    stage = record.get("stage")
    if stage not in STAGES:
        raise RunLogCorrupt(line, f"stadio sconosciuto: {stage!r}")
    if record.get("event") not in EVENTS:
        raise RunLogCorrupt(line, f"evento sconosciuto: {record.get('event')!r}")
    if not isinstance(record.get("seq"), int):
        raise RunLogCorrupt(line, "seq mancante o non intero")
    if not isinstance(record.get("sha"), str) or not record["sha"]:
        raise RunLogCorrupt(line, "sha mancante")
    return record


def _read_raw(workdir: Any) -> bytes:
    target = path(workdir)
    try:
        return target.read_bytes()
    except FileNotFoundError:
        return b""
    except OSError as exc:  # noqa: BLE001 - unreadable is as bad as corrupt
        raise RunLogCorrupt(1, f"log illeggibile: {exc}") from exc


def _parse(data: bytes) -> tuple[List[Dict[str, Any]], bytes]:
    """Parse every trustworthy line; return the records and their exact bytes.

    The bytes returned stop at the last accepted line, so a truncated tail can
    be dropped before the next append without rewriting a single old byte.
    """
    if not data:
        return [], b""
    chunks = data.split(b"\n")
    if chunks and chunks[-1] == b"":
        chunks = chunks[:-1]
    records: List[Dict[str, Any]] = []
    accepted: List[bytes] = []
    previous_sha = ""
    for index, chunk in enumerate(chunks, start=1):
        is_last = index == len(chunks)
        try:
            record = json.loads(chunk.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            if is_last:
                # Crash between the bytes and the newline: the event never
                # happened from the reader's point of view, so it is dropped.
                break
            raise RunLogCorrupt(index, f"riga non JSON ({type(exc).__name__})") from exc
        record = _validate_shape(record, index)
        if record["seq"] != index:
            raise RunLogCorrupt(
                index, f"seq {record['seq']} al posto di {index}: riga fuori ordine"
            )
        expected = _digest(previous_sha, _payload(record))
        if record["sha"] != expected:
            raise RunLogCorrupt(index, "checksum non valido (catena di sha rotta)")
        previous_sha = record["sha"]
        records.append(record)
        accepted.append(chunk)
    raw = b"".join(chunk + b"\n" for chunk in accepted)
    return records, raw


def read(workdir: Any) -> List[Dict[str, Any]]:
    """Every valid event, oldest first.

    A truncated **last** line is discarded (the crash lost the event); a broken
    line anywhere else raises :class:`RunLogCorrupt` with its line number.
    """
    records, _ = _parse(_read_raw(workdir))
    return records


def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp.{os.getpid()}")
    try:
        with open(temp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def append(workdir: Any, **event: Any) -> Dict[str, Any]:
    """Append one event and return the line as written.

    Required keys: ``stage``, ``event``, ``contract``, ``value``, ``ok``. A
    missing ``threshold`` is ``None``; a missing ``why`` is the empty string.
    The line is never spliced into the old file: the old bytes plus the new
    line go to a temp file and ``os.replace`` publishes them whole.
    """
    stage = str(event.get("stage") or "")
    kind = str(event.get("event") or "")
    if stage not in STAGES:
        raise ValueError(f"stadio sconosciuto: {stage!r} (attesi {', '.join(STAGES)})")
    if kind not in EVENTS:
        raise ValueError(f"evento sconosciuto: {kind!r} (attesi {', '.join(sorted(EVENTS))})")

    target = path(workdir)
    data = _read_raw(workdir)
    records, raw = _parse(data)
    seq = int(records[-1]["seq"]) + 1 if records else 1
    previous_sha = str(records[-1]["sha"]) if records else ""

    payload: Dict[str, Any] = {
        "v": SCHEMA_VERSION,
        "seq": seq,
        "at": str(event.get("at") or _now()),
        "stage": stage,
        "event": kind,
        "contract": str(event.get("contract") or ""),
        "value": event.get("value"),
        "threshold": event.get("threshold"),
        "ok": bool(event.get("ok")),
        "why": str(event.get("why") or ""),
    }
    payload["sha"] = _digest(previous_sha, payload)
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    _atomic_write(target, raw + line.encode("utf-8"))
    return payload


def _last_events(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """The latest event per stage: a later ``fail`` unsigns an earlier ``sign``."""
    last: Dict[str, Dict[str, Any]] = {}
    for record in records:
        last[str(record["stage"])] = record
    return last


def signed(workdir: Any) -> Set[str]:
    """The stages whose **latest** event is a ``sign`` with ``ok: true``."""
    return {
        stage
        for stage, record in _last_events(read(workdir)).items()
        if record.get("event") == "sign" and record.get("ok") is True
    }


def verify(workdir: Any) -> Dict[str, Any]:
    """The contract's verdict: ``{ok, stages, missing, corrupt}``.

    ``corrupt`` is ``None`` or the declared reason (never a silent ``ok``).
    """
    try:
        records = read(workdir)
    except RunLogCorrupt as exc:
        return {
            "ok": False,
            "stages": [],
            "missing": list(STAGES),
            "corrupt": str(exc),
        }
    signed_stages = {
        stage
        for stage, record in _last_events(records).items()
        if record.get("event") == "sign" and record.get("ok") is True
    }
    stages = sorted(signed_stages, key=STAGES.index)
    missing = [stage for stage in STAGES if stage not in stages]
    return {
        "ok": not missing,
        "stages": stages,
        "missing": missing,
        "corrupt": None,
    }
