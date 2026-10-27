"""vx.models — approved model bytes, pinned by SHA-256.

The registry is declared data: no network lookup, no "download the latest" and
no silent fallback to a smaller model when the approved bytes are missing. A
model whose hash or size differs from the record is refused, because a swapped
weight file is indistinguishable from a corrupt one at inference time.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List

from .contract import VxError

CHUNK = 1024 * 1024

RECORDS: Dict[str, Dict[str, Any]] = {
    "silero_vad": {
        "path_hint": "assets/models/silero_vad.onnx",
        "sha256": "2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f",
        "size": 2327524,
        "source_url": "https://github.com/snakers4/silero-vad/releases/tag/v5.1.2",
        "licence": "MIT",
    },
    "face_detection_yunet_2023mar": {
        "path_hint": "assets/models/face_detection_yunet_2023mar.onnx",
        "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        "size": 232589,
        "source_url": (
            "https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet"
        ),
        "licence": "MIT",
    },
    "faster_whisper_small": {
        "path_hint": "assets/models/faster-whisper-small/model.bin",
        "sha256": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
        "size": 483546902,
        "source_url": (
            "https://huggingface.co/Systran/faster-whisper-small/tree/"
            "536b0662742c02347bc0e980a01041f333bce120"
        ),
        "licence": "MIT",
    },
}


def _refusal(message: str, payload: Dict[str, Any]) -> VxError:
    error = VxError(message)
    error.payload = dict(payload)
    return error


def record_for(name: str) -> Dict[str, Any]:
    record = RECORDS.get(name)
    if record is None:
        known = ", ".join(sorted(RECORDS)) or "none"
        raise _refusal(
            f"unknown model {name!r}; recorded models: {known}",
            {"model": name, "expected": None, "actual": None, "size": None},
        )
    return {"name": name, **record}


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve(root: str | Path, hint: str) -> Path:
    path = Path(hint).expanduser()
    return path if path.is_absolute() else Path(root) / path


def verify(name: str, path: str | Path) -> Dict[str, Any]:
    record = record_for(name)
    source = Path(path)
    expected = str(record["sha256"])
    if not source.is_file():
        raise _refusal(
            f"model {name!r}: no file at {source}",
            {"model": name, "expected": expected, "actual": None, "size": 0},
        )
    size = source.stat().st_size
    actual = _digest(source)
    if actual != expected or size != int(record["size"]):
        raise _refusal(
            f"model {name!r}: bytes are not the approved ones "
            f"(expected {expected}, actual {actual}, size {size})",
            {"model": name, "expected": expected, "actual": actual, "size": size},
        )
    return {
        "name": name,
        "path": str(source.resolve()),
        "sha256": actual,
        "size": size,
        "expected": expected,
        "matches": True,
        "source_url": record["source_url"],
        "licence": record["licence"],
    }


def probe_all(root: str | Path) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for name in sorted(RECORDS):
        record = RECORDS[name]
        expected = str(record["sha256"])
        source = _resolve(root, str(record["path_hint"]))
        if not source.is_file():
            entries.append(
                {
                    "name": name,
                    "present": False,
                    "matches": False,
                    "expected": expected,
                    "actual": None,
                }
            )
            continue
        actual = _digest(source)
        entries.append(
            {
                "name": name,
                "present": True,
                "matches": actual == expected
                and source.stat().st_size == int(record["size"]),
                "expected": expected,
                "actual": actual,
            }
        )
    return entries
