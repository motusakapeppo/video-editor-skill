"""vx.prove — the independent prover: it must be able to say no.

Why: a brief that grades itself is not evidence. This module re-reads the ledger
and re-measures the renderer without importing any decision logic from the
registry — only the vocabulary constants. If the brief claims an effect that was
not executed, or a confidence that contradicts its own inaction rule, the prover
fails it.

Wave 0 ships the ledger and the renderer delta. The surface/hook/docs gates land
in Wave 2.5; their absence here is deliberate, not an oversight.
"""

from __future__ import annotations

import hashlib
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .registry import ANIMATIONS, APPROVED_DELIVERY_SHA256, AXES

# The approved delivery is machine-local and never re-rendered. The environment
# variable exists so a different workstation can point at its own copy.
APPROVED_DELIVERY_PATH = Path(
    os.environ.get(
        "VX_APPROVED_DELIVERY",
        r"D:\VOX POPULI - ALBUM\SOCIAL\PUBBLICAZIONI\P_25-10-26\video\MOTUS_vox_FINALE_delivery.mp4",
    )
)

# The V2 grade the delivery was approved with. ``node=None`` means this node, not
# "no grade": a baseline of zero would make the 0.5x stop rule meaningless.
APPROVED_GRADE: Dict[str, Any] = {
    "type": "curve",
    "master": [[0.0, 0.0], [0.5, 0.50], [0.85, 0.88], [1.0, 1.0]],
    "channels": {
        "r": [[0, 0.0400], [0.25, 0.4401], [0.5, 0.8989], [0.75, 0.9419], [1, 1.0]],
        "g": [[0, 0.0120], [0.25, 0.1761], [0.5, 0.3416], [0.75, 0.7064], [1, 0.99]],
        "b": [[0, 0.0120], [0.25, 0.1320], [0.5, 0.2697], [0.75, 0.4710], [1, 0.9694]],
    },
    "desaturate": True,
    "grain": 4,
    "vignette": math.pi / 9.0,
}

_PROVENANCE_PREFIXES = ("corpus_hash:", "measured:", "declared:")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_ledger(brief: Dict[str, Any]) -> List[str]:
    """Re-read the ledger. Every returned string is a reason to reject the brief."""
    errors: List[str] = []
    if not isinstance(brief, dict):
        return ["brief non è un oggetto"]

    choices = brief.get("choices")
    if not isinstance(choices, dict):
        return ["choices mancante o non è un oggetto"]

    executed = brief.get("executed")
    if not isinstance(executed, list):
        errors.append("executed mancante o non è una lista")
        executed = []
    by_axis: Dict[str, Dict[str, Any]] = {}
    for item in executed:
        if isinstance(item, dict) and item.get("axis") in AXES:
            by_axis[item["axis"]] = item

    for axis in AXES:
        choice = choices.get(axis)
        if not isinstance(choice, dict):
            errors.append(f"{axis}: scelta mancante")
            continue

        for field in (
            "driver",
            "value",
            "provenance",
            "inaction",
            "confidence",
            "applied",
        ):
            if field not in choice:
                errors.append(f"{axis}: campo {field} mancante")

        provenance = choice.get("provenance")
        if not isinstance(provenance, str) or not provenance.startswith(
            _PROVENANCE_PREFIXES
        ):
            errors.append(f"{axis}: provenienza non ammessa: {provenance!r}")

        confidence = choice.get("confidence")
        applied = choice.get("applied")
        if not _is_number(confidence):
            errors.append(f"{axis}: confidence non numerica")
        elif not isinstance(applied, bool):
            errors.append(f"{axis}: applied non booleano")
        else:
            uncertain = 0.2 <= float(confidence) <= 0.8
            if uncertain and applied:
                errors.append(
                    f"{axis}: confidence {confidence} in [0.2, 0.8] ma applied true"
                )
            if not uncertain and not applied:
                errors.append(
                    f"{axis}: confidence {confidence} fuori [0.2, 0.8] ma applied false"
                )

        if axis == "animation" and choice.get("direction_basis") != "asserted":
            errors.append("animation: direction_basis deve essere 'asserted'")

        item = by_axis.get(axis)
        if item is None:
            errors.append(f"{axis}: executed mancante")
            continue
        if item.get("rc") != 0:
            errors.append(f"{axis}: executed.rc != 0")
        frames = item.get("frames")
        if not isinstance(frames, int) or isinstance(frames, bool) or frames <= 0:
            errors.append(f"{axis}: executed.frames <= 0")
        filter_text = item.get("filter")
        if not isinstance(filter_text, str) or not filter_text.strip():
            errors.append(f"{axis}: executed.filter vuoto")
        if applied is False and item.get("name") != "none":
            errors.append(f"{axis}: applied false ma executed.name != 'none'")

    if brief.get("skeleton") != "deferred":
        errors.append("skeleton deve essere 'deferred'")

    reasons = brief.get("reasons")
    if not isinstance(reasons, list) or len(reasons) != 6:
        errors.append("reasons deve avere 6 voci")

    if len(executed) != 6:
        errors.append("executed deve avere 6 voci")

    return errors


def reproduce_sha256(path: str | Path) -> bool:
    """True only when the file's SHA-256 equals the approved delivery hash."""
    source = Path(path)
    if not source.is_file():
        return False
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper() == APPROVED_DELIVERY_SHA256.upper()


def _luma_percentiles(image: Any) -> Dict[str, float]:
    import numpy as np

    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    luma = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    return {
        "p01": float(np.percentile(luma, 1)),
        "p50": float(np.percentile(luma, 50)),
        "p99": float(np.percentile(luma, 99)),
    }


def renderer_delta(video: str | Path, node: Optional[Any] = None) -> float:
    """Max |ffmpeg - pillow| on p01/p50/p99 over 12 evenly spaced frames.

    ``node=None`` means the approved V2 grade, not "no grade": the baseline has to
    be a real renderer discrepancy or the 0.5x stop rule has nothing to compare
    against. A dict is parsed with :func:`vx.grade.parse`; a ``GradeNode`` is used
    as-is. The Pillow side reuses :func:`vx.grade.apply_pillow` on the same decoded
    frame, so the only difference measured is the renderer, not the decoder.

    Raises ``RuntimeError`` when no frame can be measured. It never returns a
    fabricated number.
    """
    from PIL import Image

    from . import ffmpeg as ff
    from . import grade as grade_mod
    from .probe import probe

    if node is None:
        grade_node = grade_mod.parse(APPROVED_GRADE)
    elif isinstance(node, dict):
        grade_node = grade_mod.parse(node)
    else:
        grade_node = node

    info = probe(str(video))
    duration = float(info.duration)
    if duration <= 0.0:
        raise RuntimeError(f"durata non misurabile: {video}")

    work = Path(tempfile.mkdtemp(prefix="vxdelta-"))
    worst = 0.0
    samples = 0
    for index in range(12):
        at = duration * (index + 0.5) / 12.0
        source = work / f"frame_{index:02d}.png"
        proc = subprocess.run(
            [
                ff.ffmpeg(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{at:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                str(source),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0 or not source.is_file():
            continue

        via_ffmpeg = work / f"frame_{index:02d}_ff.png"
        proc = subprocess.run(
            [
                ff.ffmpeg(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-vf",
                grade_mod.ffmpeg_filter(grade_node),
                "-frames:v",
                "1",
                str(via_ffmpeg),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0 or not via_ffmpeg.is_file():
            continue

        ff_stats = _luma_percentiles(Image.open(via_ffmpeg))
        pillow_stats = _luma_percentiles(
            grade_mod.apply_pillow(Image.open(source), grade_node)
        )
        for key in ("p01", "p50", "p99"):
            worst = max(worst, abs(ff_stats[key] - pillow_stats[key]))
        samples += 1

    if samples == 0:
        raise RuntimeError(f"nessun frame misurabile in {video}")
    return round(worst, 6)


def _selftest() -> int:
    import copy

    from . import brief as brief_mod

    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:44} {detail}")

    valid = brief_mod._valid_brief()
    errors = check_ledger(valid)
    check("brief con tutti gli assi applicati passa", errors == [], str(errors))

    false_applied = copy.deepcopy(valid)
    false_applied["choices"]["palette"]["applied"] = False
    false_applied["choices"]["palette"]["inaction"] = True
    false_applied["choices"]["palette"]["confidence"] = 0.5
    errors = check_ledger(false_applied)
    check(
        "applied false con effetto eseguito fallisce",
        errors != [],
        str(errors[:1]),
    )

    uncertain = copy.deepcopy(valid)
    uncertain["choices"]["grade"]["confidence"] = 0.5
    uncertain["choices"]["grade"]["applied"] = True
    errors = check_ledger(uncertain)
    check(
        "confidence 0.5 con applied true fallisce",
        errors != [],
        str(errors[:1]),
    )

    if APPROVED_DELIVERY_PATH.is_file():
        check(
            "reproduce_sha256 sul delivery approvato",
            reproduce_sha256(APPROVED_DELIVERY_PATH),
            str(APPROVED_DELIVERY_PATH),
        )
    else:
        print(f"  [SKIP] delivery approvato non presente: {APPROVED_DELIVERY_PATH}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as handle:
        handle.write(b"not the approved delivery")
        temp_path = Path(handle.name)
    try:
        check(
            "reproduce_sha256 su un file estraneo",
            reproduce_sha256(temp_path) is False,
            str(temp_path),
        )
    finally:
        temp_path.unlink(missing_ok=True)

    print(f"{'OK' if failures == 0 else 'FALLITO'} — {failures} fallimenti")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="vx.prove selftest")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    parser.print_help()
    raise SystemExit(2)
