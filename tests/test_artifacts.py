"""Write-ahead journal for artifacts: a crash mid-write leaves a story.

Acceptance for F17-T04:
    (a) plan -> complete -> pending is a round trip on one append-only journal;
    (b) a process that dies after planning, with a half-written file, is named
        in ``classify()["orphan_files"]`` and the stage-cache lookup is a miss;
    (c) a byte-identical file whose intent never completed is *still* a miss —
        the journal, not the bytes, decides;
    (d) a completed artifact is a hit again;
    (e) a corrupt journal line is skipped and the rest of the journal reads;
    (f) an atomic write leaves no temp file on success and no destination on
        failure;
    (g) a path outside the declared root is refused before a byte is written;
    (h) no journal means no plan calls and an unchanged lookup.

No ffmpeg, no network: the whole file is the journal and the cache entry.

python tests/test_artifacts.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import artifacts, stagecache  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(stage: str = "03_graph") -> str:
    return stagecache.key(stage, {"graph": "[0:v]null[vout]"}, {}, "1")


def _journal_lines(root: Path) -> list:
    raw = artifacts.journal_path(root).read_text(encoding="utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def test_round_trip(root: Path) -> None:
    journal = artifacts.journal_path(root)
    check("journal: absent to begin with", journal.exists() is False)
    check(
        "classify: a missing journal is an empty story",
        artifacts.classify(root)
        == {"completed": [], "pending": [], "orphan_files": []},
        artifacts.classify(root),
    )

    artifact = root / "round.bin"
    intent = artifacts.plan(root, artifact, expected_sha256="a" * 64, kind="artifact")
    check(
        "plan: returns a monotonic string id",
        isinstance(intent, str) and intent.isdigit(),
        intent,
    )
    check("plan: creates the journal", journal.is_file())
    second = artifacts.plan(root, root / "other.bin")
    check("plan: ids increase", int(second) > int(intent), (intent, second))

    open_intents = artifacts.pending(root)
    check(
        "pending: the uncompleted intents, in order",
        [record["id"] for record in open_intents] == [intent, second],
        [record["id"] for record in open_intents],
    )
    check(
        "pending: the plan carries path, kind and expected hash",
        open_intents[0]["path"] == str(artifact)
        and open_intents[0]["kind"] == "artifact"
        and open_intents[0]["expected_sha256"] == "a" * 64,
        open_intents[0],
    )

    done = artifacts.complete(root, intent, artifact, sha256="a" * 64)
    check(
        "complete: returns the completion record",
        isinstance(done, dict)
        and done["event"] == "complete"
        and done["id"] == intent
        and done["sha256"] == "a" * 64,
        done,
    )
    check(
        "pending: a completed intent leaves the list",
        [record["id"] for record in artifacts.pending(root)] == [second],
        [record["id"] for record in artifacts.pending(root)],
    )

    report = artifacts.classify(root)
    check(
        "classify: the three declared keys",
        set(report) == {"completed", "pending", "orphan_files"},
        sorted(report),
    )
    check(
        "classify: the completion is listed",
        [record["id"] for record in report["completed"]] == [intent],
        report["completed"],
    )
    check(
        "classify: the open intent is listed",
        [record["id"] for record in report["pending"]] == [second],
        report["pending"],
    )
    check(
        "classify: a pending path that does not exist is not an orphan",
        report["orphan_files"] == [],
        report["orphan_files"],
    )


def test_crash_mid_write(root: Path) -> None:
    work = root / "work"
    work.mkdir()
    artifact = work / "stage" / "03_video.mp4"
    artifact.parent.mkdir(parents=True)
    good = b"a finished stage artifact\n" * 512
    artifacts.write_bytes_atomic(artifact, good, root=work)
    digest = _digest()

    check("crash: no journal yet", artifacts.journal_path(work).exists() is False)
    entry = stagecache.record(work, digest, artifact, {"stage": "03_graph"})
    check("crash: the artifact was recorded", entry is not None and entry.is_file())
    first = stagecache.lookup(work, digest)
    check(
        "crash: a completed artifact is a hit",
        first is not None and first["size"] == len(good),
        first,
    )

    # A later run announces the same path, then the process dies while the
    # bytes are half on disk.
    fresh = b"the bytes of the next attempt\n" * 512
    intent = artifacts.plan(
        work, artifact, expected_sha256=_sha(fresh), kind="stage_artifact"
    )
    artifact.write_bytes(fresh[:37])
    report = artifacts.classify(work)
    check(
        "crash: the half-written file is an orphan",
        report["orphan_files"] == [str(artifact)],
        report["orphan_files"],
    )
    check(
        "crash: the intent is still pending",
        [record["id"] for record in report["pending"]] == [intent],
        report["pending"],
    )

    sink: dict = {}
    found = stagecache.lookup(work, digest, pending=sink)
    check("crash: lookup is a miss", found is None, found)
    check(
        "crash: the miss declares pending_write in its meta",
        sink.get("pending_write") is True
        and sink.get("meta", {}).get("pending_write") is True
        and sink.get("intent_id") == intent,
        sink,
    )
    check(
        "crash: lookup deleted nothing",
        artifact.is_file() and entry is not None and entry.is_file(),
    )

    # The strong case: the bytes are back to the recorded content, but the
    # journal still says a write may be in flight.  Only the journal can tell,
    # and it must win over a hash that happens to match.
    artifact.write_bytes(good)
    sink = {}
    found = stagecache.lookup(work, digest, pending=sink)
    check(
        "crash: byte-identical but unfinished is still a miss",
        found is None and sink.get("pending_write") is True,
        sink,
    )

    artifacts.complete(work, intent, artifact, sha256=_sha(good))
    hit = stagecache.lookup(work, digest)
    check(
        "crash: after the completion the artifact is a hit again",
        hit is not None and hit["sha256"] == _sha(good),
        hit,
    )
    check(
        "crash: classify no longer reports an orphan",
        artifacts.classify(work)["orphan_files"] == [],
        artifacts.classify(work)["orphan_files"],
    )


def test_corrupt_journal(root: Path) -> None:
    journal = artifacts.enable(root)
    first = artifacts.plan(root, root / "one.bin")
    with journal.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("{not json at all\n")
        handle.write(json.dumps(["not", "an", "object"]) + "\n")
        handle.write(
            json.dumps({"schema": "other/9", "event": "plan", "id": "x"}) + "\n"
        )
        handle.write("\n")
    second = artifacts.plan(root, root / "two.bin")
    artifacts.complete(root, first, root / "one.bin", sha256="0" * 64)

    open_intents = artifacts.pending(root)
    check(
        "corrupt: the damaged lines are skipped and the rest reads",
        [record["id"] for record in open_intents] == [second],
        open_intents,
    )
    report = artifacts.classify(root)
    check(
        "corrupt: the completion before the junk still reads",
        [record["id"] for record in report["completed"]] == [first],
        report["completed"],
    )
    check(
        "corrupt: a corrupt journal is not an orphan source",
        report["orphan_files"] == [],
        report["orphan_files"],
    )


def test_atomic_write(root: Path) -> None:
    target = root / "nested" / "artifact.bin"
    data = b"complete bytes"
    returned = artifacts.write_bytes_atomic(target, data, root=root)
    check(
        "atomic: success writes the whole file",
        returned == target and target.read_bytes() == data,
        target,
    )
    check(
        "atomic: success leaves no temp file",
        sorted(item.name for item in target.parent.iterdir()) == [target.name],
        sorted(item.name for item in target.parent.iterdir()),
    )

    broken = root / "nested" / "broken.bin"
    raised = False
    try:
        with mock.patch("os.replace", side_effect=OSError("simulated crash")):
            artifacts.write_bytes_atomic(broken, b"never lands", root=root)
    except OSError:
        raised = True
    check("atomic: a failing replace raises", raised is True)
    check("atomic: failure leaves no destination", broken.exists() is False)
    check(
        "atomic: failure leaves no temp file",
        [item.name for item in broken.parent.glob("*.tmp")] == [],
        [item.name for item in broken.parent.glob("*.tmp")],
    )

    outside = root.parent / f"vx-artifacts-outside-{os.getpid()}.bin"
    refused = False
    try:
        artifacts.write_bytes_atomic(outside, b"nope", root=root)
    except ValueError:
        refused = True
    check("atomic: a path outside root is refused", refused is True)
    check("atomic: the refused path was never created", outside.exists() is False)

    refused_root = False
    try:
        artifacts.write_bytes_atomic(root, b"nope", root=root)
    except ValueError:
        refused_root = True
    check("atomic: the root itself is not a destination", refused_root is True)


def test_absent_journal_unchanged(root: Path) -> None:
    work = root / "plain"
    work.mkdir()
    artifact = work / "out.bin"
    artifact.write_bytes(b"payload")
    digest = _digest()

    calls: list = []
    real_plan = artifacts.plan
    real_complete = artifacts.complete

    def spy_plan(*args, **kwargs):
        calls.append(("plan", args))
        return real_plan(*args, **kwargs)

    def spy_complete(*args, **kwargs):
        calls.append(("complete", args))
        return real_complete(*args, **kwargs)

    artifacts.plan = spy_plan
    artifacts.complete = spy_complete
    try:
        entry = stagecache.record(work, digest, artifact, {"stage": "03_graph"})
        found = stagecache.lookup(work, digest)
    finally:
        artifacts.plan = real_plan
        artifacts.complete = real_complete

    check("no journal: record still writes the entry", entry is not None)
    check("no journal: zero plan and zero complete calls", calls == [], calls)
    check(
        "no journal: record creates no journal",
        artifacts.journal_path(work).exists() is False,
    )
    check(
        "no journal: lookup is unchanged, a plain hit",
        found is not None and found["path"] == str(artifact),
        found,
    )


def test_record_with_journal(root: Path) -> None:
    work = root / "journalled"
    work.mkdir()
    artifacts.enable(work)
    artifact = work / "stage" / "03_video.mp4"
    artifact.parent.mkdir(parents=True)
    data = b"rendered bytes\n" * 300
    artifact.write_bytes(data)
    digest = _digest()

    entry = stagecache.record(work, digest, artifact, {"stage": "03_graph"})
    check("journal on: record writes the entry", entry is not None)
    check("journal on: the artifact is untouched", artifact.read_bytes() == data)
    check(
        "journal on: no temp file survives the atomic rewrite",
        [item.name for item in artifact.parent.glob("*.tmp")] == [],
    )
    lines = _journal_lines(work)
    check(
        "journal on: one plan then one completion",
        [line["event"] for line in lines] == ["plan", "complete"],
        [line["event"] for line in lines],
    )
    check(
        "journal on: the plan names the stage artifact",
        lines[0]["path"] == str(artifact) and lines[0]["kind"] == "stage_artifact",
        lines[0],
    )
    check(
        "journal on: the completion records the file hash",
        lines[1]["sha256"] == _sha(data),
        lines[1],
    )
    report = artifacts.classify(work)
    check(
        "journal on: nothing is left pending",
        report["pending"] == [] and report["orphan_files"] == [],
        report,
    )
    check("journal on: the entry is a hit", stagecache.lookup(work, digest) is not None)


def main() -> int:
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="vx-artifacts-") as raw:
        root = Path(raw)
        test_round_trip(root / "round")
        test_crash_mid_write(root)
        test_corrupt_journal(root / "corrupt")
        test_atomic_write(root / "atomic")
        test_absent_journal_unchanged(root)
        test_record_with_journal(root)
    print(f"\n{PASS} PASS / {FAIL} FAIL  ({time.time() - started:.1f}s)")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
