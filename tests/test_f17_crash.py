"""F17-B: 50 real process kills against the real ``vx auto`` pipeline.

Each kill is a ``taskkill /F /T`` (POSIX: ``killpg(SIGKILL)``) against a child
running ``vx.cli auto`` on a tiny corpus, at a delay drawn from one seeded
``random.Random``.  Nothing here simulates a crash, exits in-process or stubs
the pipeline: if the child finished before the kill landed, the kill FAILS.

For every kill three things are asserted before the invariants are considered:

* the kill really happened - the child's exit code is non-zero, or the killer
  itself reported success (``taskkill`` rc 0 / the process group existed);
* the run had not finished - no ``auto_report.json`` and no exit 0.  This is the
  only sound "kill landed too late" signal: the ``--out`` master is ffmpeg's
  loudness artifact, written in place and complete ~1.1 s before the run ends
  (measured; see ``tests/_crash.py``), so its existence is not completion;
* no delivery was produced - ``out/master.mp4`` inside the workdir is absent or
  not a whole MP4, i.e. the master the caller would receive was not produced.
  An incomplete ``--out`` file is recorded as ``master-file=<n>B`` in the note.

Then five invariants (four on the kill state, one after a resume run on the same
workdir):

``sources_sha_unchanged``
    every file of the asset pool has the same sha256 it had before the spawn.
``journal_readable``
    ``vx.events.read`` reads every ``*.jsonl`` under the workdir without
    raising and returns only objects; after the resume the last line of each
    parses.
``no_stray_files``
    every file under the workdir is inside the allowlist declared in
    ``tests/_crash.py``, before and after the resume.
``resume_completes``
    re-running ``vx auto`` on the crashed workdir exits 0 and the delivered
    master is a whole MP4.
``sweep_removes_leftovers``
    ``vx.artifacts.classify`` runs on every journal root, reports its three
    declared keys, and leaves no open intent pointing at a file that exists.

Fixture written: ``tests/fixtures/f17b/kill_matrix.json`` - index, delay, exit
code, invariant verdicts and the pass flag, no wall-clock values, so two runs
with the same seed produce the same file.

Run:  python tests/test_f17_crash.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
for _entry in (str(TESTS), str(ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import _crash  # noqa: E402

KILLS = 50
CEILING_SECONDS = 600
MATRIX_PATH = ROOT / "tests" / "fixtures" / "f17b" / "kill_matrix.json"
MATRIX_SCHEMA = "vx.f17b.kill_matrix/1"
SEED = int(os.environ.get("VX_F17_SEED", "1717"))

PASS = 0
FAIL = 0
SKIP = 0


def _skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:64} {reason}")


def _write_matrix(seed: int, kills: list) -> Path:
    payload = {"schema": MATRIX_SCHEMA, "seed": seed, "kills": kills}
    MATRIX_PATH.parent.mkdir(parents=True, exist_ok=True)
    MATRIX_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return MATRIX_PATH


def main() -> int:
    global PASS, FAIL, SKIP
    print(f"seed={SEED} kills={KILLS} ceiling={CEILING_SECONDS}s")
    if not _crash.FFMPEG:
        print(
            "skip: VX_FFMPEG is not set; without an ffmpeg binary the corpus "
            "cannot be built and there is nothing to kill"
        )
        _skip("whole file", "VX_FFMPEG missing")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    started = time.time()
    delays = _crash.delay_plan(SEED, KILLS)
    print(
        "delay plan: stratified over "
        f"{_crash.DELAY_MIN_MS}-{_crash.DELAY_MAX_MS} ms "
        f"(first {delays[0]} ms, last {delays[-1]} ms, "
        f"mean {sum(delays) / len(delays):.0f} ms)"
    )
    print("delays: " + " ".join(str(value) for value in delays))

    kills: list = []
    failures: list = []
    resume_note = ""

    with tempfile.TemporaryDirectory(prefix="vx-f17b-") as raw:
        base = Path(raw)
        for index, delay in enumerate(delays):
            if time.time() - started > CEILING_SECONDS:
                for rest in range(index, KILLS):
                    _skip(f"#{rest:02d}", f"ceiling {CEILING_SECONDS}s reached")
                break

            record = _crash.run_kill(SEED, index, base, delay_ms=delay)
            report = _crash.invariants(
                record["pool"], record["workdir"], baseline=record["baseline"]
            )
            broken = _crash.failing_keys(report)
            if not record["killed"]:
                broken.append("process_survived")
            if record["finished"]:
                broken.append("run_finished_before_kill")
            if record["delivered_complete"]:
                broken.append("master_delivered_before_kill")
            passed = not broken
            kills.append(
                {
                    "index": index,
                    "delay_ms": record["delay_ms"],
                    "exit_code": record["exit_code"],
                    "invariants": {
                        name: bool(report[name]) for name in _crash.INVARIANT_KEYS
                    },
                    "passed": passed,
                }
            )
            note = ""
            if record["master_present"] and not record["delivered"]:
                note = f" master-file={record['master_bytes']}B"
            print(
                f"#{index:02d} exit={record['exit_code']:<3} "
                f"invariants={'ok' if not broken else 'FAIL'}"
                + (f" broken={','.join(broken)}" if broken else "")
                + note
            )
            if passed:
                PASS += 1
                shutil.rmtree(record["workdir"].parent, ignore_errors=True)
            else:
                FAIL += 1
                failures.append(
                    {
                        "index": index,
                        "delay_ms": record["delay_ms"],
                        "phase": record["phase"],
                        "exit_code": record["exit_code"],
                        "finished": record["finished"],
                        "broken": broken,
                        "detail": report["detail"],
                        "workdir": str(record["workdir"]),
                    }
                )
            if index == KILLS - 1:
                detail = report["detail"]
                resume_note = (
                    f"resume exit={detail['resume_exit']} "
                    f"master={detail['master_after_resume']} "
                    f"complete={detail['master_complete_after_resume']} "
                    f"bytes={detail['master_bytes_after_resume']} "
                    f"(after kill #{index:02d} at {record['delay_ms']} ms, "
                    f"phase {record['phase']})"
                )

    matrix = _write_matrix(SEED, kills)
    try:
        shown = matrix.relative_to(ROOT).as_posix()
    except ValueError:
        shown = str(matrix)
    print(f"\nfixture: {shown}")
    if resume_note:
        print(f"resume proof (last kill): {resume_note}")
    for failure in failures:
        print(f"\nfailed kill #{failure['index']:02d} ({failure['phase']}):")
        print(f"  delay_ms={failure['delay_ms']} exit={failure['exit_code']}")
        print(f"  broken={failure['broken']}")
        print(f"  detail={json.dumps(failure['detail'], sort_keys=True)}")

    elapsed = time.time() - started
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  ({elapsed:.1f}s)")
    if FAIL:
        return 1
    if SKIP:
        print("INCOMPLETE: kills were skipped, the matrix is not a proof")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
