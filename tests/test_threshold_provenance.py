"""The T09 gate: no phase may close while corpus thresholds are provisional.

python tests/test_threshold_provenance.py

Checks:
(a) the ledger's F0-T09 status is parsed, not assumed;
(b) while T09 is open, registry.provenance() marks every threshold provisional;
(c) no phase line may be closed "sull'esame" while T09 is open (D010);
(d) the sample annotation corpus recomputes p33/p67 deterministically and matches
    registry.annotated_percentiles();
(e) the shipped module has 20 clip ids and zero data rows, and an empty corpus
    cannot be written as corpus_annotations.json.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from vx import registry  # noqa: E402
import import_annotations as importer  # noqa: E402

LEDGER = ROOT / "PLAN" / "MASTER_LEDGER.md"
FIXTURES = ROOT / "tests" / "fixtures"
SAMPLE = FIXTURES / "corpus_annotations.sample.json"
REAL = FIXTURES / "corpus_annotations.json"
_env_module = os.environ.get("VX_T09_MODULE")
MODULE = Path(_env_module) if _env_module else importer.DEFAULT_MODULE

T09_ROW = re.compile(r"^\|\s*F0-T09\s*\|")
CLOSED_ON_EXAM = re.compile(
    r"\b(DONE|CHIUS[AOEI]|CLOSED|COMPLETAT[AOI])\b[^|]{0,40}?sull['\u2019]esame",
    re.IGNORECASE,
)
NEGATED = re.compile(r"\b(non|nessun\w*|finch\w*|se|mentre|prima)\b", re.IGNORECASE)

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def skip(label: str, detail: str = "") -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:56} {detail}")


def read_t09() -> Tuple[Optional[int], str]:
    if not LEDGER.is_file():
        return None, ""
    for number, line in enumerate(LEDGER.read_text(encoding="utf-8").splitlines(), 1):
        if T09_ROW.match(line.strip()):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            return number, cells[-1] if cells else ""
    return None, ""


def numpy_percentile(values: List[float], p: float) -> Optional[float]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return round(ordered[0], 4)
    position = (len(ordered) - 1) * p / 100.0
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return round(ordered[low], 4)
    return round(ordered[low] + (position - low) * (ordered[high] - ordered[low]), 4)


def recompute(
    clips: Dict[str, Any], axis: str
) -> Tuple[Optional[float], Optional[float], int]:
    ranks = registry.ANNOTATION_RANKS[axis]
    values: List[float] = []
    for clip in clips.values():
        if axis == "hook":
            token = clip.get("hook")
        else:
            axes = clip.get("axes") or {}
            token = axes.get(axis)
        rank = ranks.get(token) if isinstance(token, str) else None
        if rank is not None:
            values.append(float(rank))
    return (
        numpy_percentile(values, 33.0),
        numpy_percentile(values, 67.0),
        len(values),
    )


def check_ledger_and_gate() -> None:
    print("(a) ledger F0-T09")
    row, status = read_t09()
    check("F0-T09 row found in PLAN/MASTER_LEDGER.md", row is not None, f"row {row}")
    check("status is not empty", bool(status), status)
    check(
        "registry status equals ledger status",
        registry.T09_STATUS == status,
        f"{registry.T09_STATUS!r} == {status!r}",
    )
    done = status.upper().startswith("DONE")
    check("registry T09_DONE equals ledger", registry.T09_DONE == done, str(done))
    check(
        "CORPUS_PROVISIONAL is the negation of T09_DONE",
        registry.CORPUS_PROVISIONAL == (not done),
        str(registry.CORPUS_PROVISIONAL),
    )

    print("(b) provisional gate")
    view = registry.provenance()
    states = {axis: entry["state"] for axis, entry in view["thresholds"].items()}
    check(
        "provenance covers every threshold axis",
        set(states) == set(registry.THRESHOLDS),
        str(sorted(states)),
    )
    if done:
        check(
            "T09 DONE: no corpus threshold is provisional",
            all(state != "provisional" for state in states.values()),
            str(states),
        )
    else:
        check(
            "T09 open: every corpus threshold is provisional",
            all(state == "provisional" for state in states.values()),
            str(states),
        )
        check(
            "T09 open: corpus_provisional is true",
            view["corpus_provisional"] is True,
            str(view["corpus_provisional"]),
        )

    print("(c) no phase closed 'sull'esame' while T09 is open")
    if done:
        skip("phase closure check", "T09 is DONE")
    else:
        violations = []
        for number, line in enumerate(
            LEDGER.read_text(encoding="utf-8").splitlines(), 1
        ):
            stripped = line.strip()
            if stripped.startswith("|"):
                cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            elif stripped.startswith("#"):
                cells = [stripped]
            else:
                continue
            for cell in cells:
                if CLOSED_ON_EXAM.search(cell) and not NEGATED.search(cell):
                    violations.append((number, cell))
        check(
            "no ledger line closes a phase on the exam while T09 is open",
            not violations,
            str(violations[:2]),
        )


def check_sample_recomputation() -> None:
    print("(d) sample corpus recomputation")
    if not SAMPLE.is_file():
        check("sample fixture exists", False, str(SAMPLE))
        return
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    check("sample fixture is labelled sample: true", data.get("sample") is True)
    check("sample fixture is not the real corpus file", SAMPLE.name != REAL.name)
    clips = data.get("clips")
    check(
        "sample has three invented clips",
        isinstance(clips, dict) and len(clips) == 3,
        str(len(clips) if isinstance(clips, dict) else clips),
    )

    exposed = registry.annotated_percentiles(clips)
    again = registry.annotated_percentiles(clips)
    check("annotated_percentiles is deterministic", exposed == again, str(exposed))
    check(
        "annotated_percentiles is documented",
        bool(registry.annotated_percentiles.__doc__)
        and "severity" in registry.annotated_percentiles.__doc__.lower(),
        "docstring names the severity ranks",
    )

    matched = True
    detail = []
    for axis in registry.ANNOTATION_LEVELS:
        expected_p33, expected_p67, expected_n = recompute(clips, axis)
        entry = exposed.get(axis) or {}
        ok = (
            entry.get("p33") == expected_p33
            and entry.get("p67") == expected_p67
            and entry.get("n") == expected_n
        )
        matched = matched and ok
        detail.append(f"{axis}:{entry.get('p33')}/{entry.get('p67')}")
    check(
        "registry recomputation matches independent recomputation",
        matched,
        " ".join(detail),
    )
    check(
        "pinned linear percentiles (shake 0.66/1.34, noise 1.68)",
        exposed["shake"]["p33"] == 0.66
        and exposed["shake"]["p67"] == 1.34
        and exposed["noise"]["p67"] == 1.68,
        f"{exposed['shake']} {exposed['noise']}",
    )

    if REAL.is_file():
        real = json.loads(REAL.read_text(encoding="utf-8"))
        check(
            "real corpus is not sample data",
            real.get("sample") is not True and bool(real.get("clips")),
        )
    else:
        skip("real corpus absent", "corpus_annotations.json not present")

    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary) / "corpus_annotations.json"
        refused = False
        try:
            importer.write_corpus(data, target)
        except importer.AnnotationError:
            refused = True
        check("sample cannot be written as corpus_annotations.json", refused)


def check_shipped_module() -> None:
    print("(e) shipped module is empty")
    if MODULE is None or not MODULE.is_file():
        skip("shipped module", f"{MODULE} not found")
        return
    identifiers = importer.list_clip_ids(MODULE)
    expected = [f"clip_{number:02d}" for number in range(1, 21)]
    check("module has 20 clip ids", identifiers == expected, str(len(identifiers)))
    check("ESEMPIO row is ignored", "esempio" not in identifiers)
    data = importer.import_module(MODULE)
    check("module has zero data rows", data["clips"] == {}, str(data["clips"]))
    check("module has zero skipped rows", data["skipped"] == [], str(data["skipped"]))
    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary) / "corpus_annotations.json"
        refused = False
        try:
            importer.write_corpus(data, target)
        except importer.AnnotationError:
            refused = True
        check("empty module cannot be written as the corpus", refused)
    print(f"    reader: {importer.reader_name()}")


def main() -> int:
    print(f"reader: {importer.reader_name()}")
    check_ledger_and_gate()
    check_sample_recomputation()
    check_shipped_module()
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
