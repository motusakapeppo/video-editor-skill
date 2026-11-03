"""F17-B/T10: crash the real ``vx auto`` at a stage boundary, then resume it.

What was wrong with the timed version
-------------------------------------
The old file drew 50 kill instants and fired ``taskkill /F /T`` at them.  A
``render`` stage lasts 125-188 ms on the reference machine and a tree kill takes
125-171 ms to actually land, so a sample aimed at ``render`` landed there by
luck: the same matrix produced ``render=8/8`` on one run and ``3/8`` on the
next, and the file went red while every workdir it produced was healthy.  The
property being proven was *coverage*, and coverage was the one thing left to
chance.  No amount of calibration fixes that: a kill slower than the stage it
aims at cannot be aimed with a clock.

What replaces it
----------------
The child crashes itself, at the boundary asked for, with no signal and no
timer.  ``VX_CRASH_AFTER`` (documented in ``vx.auto``) is ``<stage>[:<n>]``:

    VX_CRASH_AFTER=qc          -> die right after the ``qc`` boundary
    VX_CRASH_AFTER=render:2    -> die after the 2nd boundary named ``render``

``_emit`` writes the boundary to ``events.jsonl``, flushes it, and then calls
``os._exit(70)``: no ``finally``, no lock release, no journal close.  That is a
real crash.  Coverage is now a fact, not a sample: **exit code 70** says the
child reached the boundary, and the last record on disk says which one it was.
There is nothing to calibrate and nothing to retry.

One stage can own two boundaries - ``bridge`` is written as ``start`` before the
measurements and as ``done`` after them, ``render`` as ``start`` before the
encode and ``render_done`` after the master - which is exactly what the ``:n``
suffix is for.  A crash "at a stage" therefore names the boundary that follows
the work, not the one that announces it.

The cases
---------
One real run per case, then the real resume on the same workdir:

``ingest``      after staging: ``stage_00/`` exists, no ``plan.json`` yet.
``analysis``    after the analysis pass: the analysis stage has no boundary of
                its own (it is a nested measurement inside the bridge), so the
                case runs with the bridge on and dies at ``bridge:2`` - the
                second boundary with that name, the one written after the
                analysis and after ``bridge_report.json`` (``bridge:1`` is the
                ``start`` line, written before the measurements begin).
``render``      after ``render_done``: a whole pre-master exists, QC has not run.
``qc``          after QC: the master is measured, the deliverables are not copied.
``delivery``    after ``deliverables``: the master is in ``out/``, no report yet.

Each case asserts, in this order:

``crashed``           exit code is 70 - the boundary was reached, deterministically.
``boundary``          the last journal record is the requested boundary (so the
                      coverage claim is checked, not assumed).
``state``             the files the crash should and should not have left are
                      exactly those on disk.
``no_stray_files``    every file is inside the allowlist in ``tests/_crash.py``,
                      before and after the resume.
``resume_completes``  re-running the same argv exits 0 and delivers a whole MP4.
``progress_forward``  the furthest boundary on disk never goes backwards.
``journal_readable``  ``vx.events.read`` parses every ``*.jsonl``; no torn tail
                      survives the resume.
``sweep_removes_leftovers``  ``vx.artifacts.classify`` leaves no open intent in
                      front of a file that exists.
``sources_sha_unchanged``  the asset pool is untouched.

The switch is inert when unset
------------------------------
Proven twice, without trusting the code by reading it:

* in-process, ``_crash_point`` consumes nothing and never reaches ``os._exit``
  while the variable is absent (the per-stage counter stays empty), and with
  ``render:2`` it exits exactly on the second ``render`` boundary;
* end-to-end, two uninterrupted runs - one without the variable, one with it set
  to a name no boundary has - are compared as *graphs*: same exit code, same
  ordered ``(stage, status)`` sequence in ``events.jsonl``, same report keys.
  A branch that changed the pipeline would move that sequence.

The optional ``taskkill`` sample
--------------------------------
``VX_F17_TASKKILL=1`` adds one timed tree-kill sample as a *smoke* test of the
external-kill path.  It prints its observation and its invariants, it is never
counted as coverage, and it cannot fail the file on timing: that is the whole
lesson of the version this replaces.

Run:  python tests/test_f17_crash.py
      VX_F17_TASKKILL=1 python tests/test_f17_crash.py     (+ one timed sample)
      VX_REGEN_F17=1 python tests/test_f17_crash.py        (refresh the fixture)

The per-run matrix is written to the run's temp directory, so a suite run leaves
the tree clean.  The committed fixture ``tests/fixtures/f17b/crash_matrix.json``
is refreshed **only** when ``VX_REGEN_F17=1``: a crash matrix is evidence of one
run on one machine, not a golden value.  The old ``kill_matrix.json`` stays as
the record of the timed method it describes.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Tuple
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
for _entry in (str(TESTS), str(ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import _crash  # noqa: E402

SEED = int(os.environ.get("VX_F17_SEED", "1717"))
MATRIX_PATH = ROOT / "tests" / "fixtures" / "f17b" / "crash_matrix.json"
MATRIX_SCHEMA = "vx.f17b.crash_matrix/1"
MATRIX_NOTE = (
    "per-run matrix; the committed fixture is refreshed only when "
    "VX_REGEN_F17=1, because a crash matrix is evidence of one run on one "
    "machine, not a golden value"
)
#: The optional timed sample is a smoke test of the external kill, never coverage.
TASKKILL = os.environ.get("VX_F17_TASKKILL") == "1"

PASS = 0
FAIL = 0
SKIP = 0


class Case(NamedTuple):
    """One deterministic crash: where to die, and what the workdir must show."""

    name: str
    boundary: str
    bridge: bool
    present: Tuple[str, ...]
    absent: Tuple[str, ...]


#: The stages the file proves.  ``render`` crashes after ``render_done`` because
#: the ``render`` boundary itself is written *before* the encode starts: dying
#: there would prove the pre-render state, not a written master.
CASES: Tuple[Case, ...] = (
    Case(
        name="ingest",
        boundary="ingest",
        bridge=False,
        present=("stage_00", "events.jsonl"),
        absent=("plan.json", "auto_report.json", "out/master.mp4"),
    ),
    Case(
        name="analysis",
        boundary="bridge:2",
        bridge=True,
        present=("stage_00", "bridge_report.json"),
        absent=("plan.json", "auto_report.json", "out/master.mp4"),
    ),
    Case(
        name="render",
        boundary="render_done",
        bridge=False,
        present=("render/final_pre_loudness.mp4",),
        absent=("auto_report.json", "out/master.mp4"),
    ),
    Case(
        name="qc",
        boundary="qc",
        bridge=False,
        present=("render/final_pre_loudness.mp4",),
        absent=("auto_report.json", "out/master.mp4"),
    ),
    Case(
        name="delivery",
        boundary="deliverables",
        bridge=False,
        present=("out/master.mp4",),
        absent=("auto_report.json",),
    ),
)


def check(label: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label:56} {detail}".rstrip())
    else:
        FAIL += 1
        print(f"  [FAIL] {label:56} {detail}".rstrip())
    return ok


def note(label: str, detail: str = "") -> None:
    print(f"  [NOTE] {label:56} {detail}".rstrip())


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:56} {reason}")


def _write_matrix(payload: Dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


# --------------------------------------------------------------------------- #
# the switch itself
# --------------------------------------------------------------------------- #
def switch_checks() -> List[Dict[str, Any]]:
    """The ``VX_CRASH_AFTER`` contract, in this process, with ``os._exit`` faked.

    No pipeline, no child: this is the part that has to be true for the rest of
    the file to mean anything.  The fake exit is recorded and the real one is
    never reached - if the switch were wrong the test would die here, loudly,
    instead of silently proving nothing.
    """
    print("\nswitch (in-process)")
    import vx.auto as auto

    original = os.environ.pop(auto.CRASH_AFTER_ENV, None)
    results: List[Dict[str, Any]] = []
    try:
        auto._CRASH_SEEN.clear()
        for stage in _crash.BOUNDARY_ORDER:
            auto._crash_point(stage)
        inert = not auto._CRASH_SEEN
        check(
            "no variable: nothing consumed, no exit",
            inert,
            f"counters={dict(auto._CRASH_SEEN)}",
        )
        results.append({"check": "inert_without_variable", "ok": bool(inert)})

        calls: List[int] = []
        os.environ[auto.CRASH_AFTER_ENV] = "render:2"
        auto._CRASH_SEEN.clear()
        with mock.patch.object(
            auto.os, "_exit", side_effect=lambda code: calls.append(int(code))
        ):
            auto._emit(None, "render", "start")
            first = list(calls)
            auto._emit(None, "render_done", "done")
            other_name = list(calls)
            auto._emit(None, "render", "done")
            second = list(calls)
        os.environ.pop(auto.CRASH_AFTER_ENV, None)

        check(
            "render:2 skips the first boundary",
            not first and not other_name,
            f"after_1st={first} after_other_stage={other_name}",
        )
        check(
            "render:2 exits 70 on the second",
            second == [auto.CRASH_EXIT_CODE],
            f"calls={second} exit_code={auto.CRASH_EXIT_CODE}",
        )
        results.append(
            {"check": "second_occurrence", "ok": second == [auto.CRASH_EXIT_CODE]}
        )
        results.append({"check": "first_occurrence_inert", "ok": not first})
    finally:
        os.environ.pop(auto.CRASH_AFTER_ENV, None)
        if original is not None:
            os.environ[auto.CRASH_AFTER_ENV] = original
        auto._CRASH_SEEN.clear()
    return results


# --------------------------------------------------------------------------- #
# the graph diff: no variable == today's pipeline
# --------------------------------------------------------------------------- #
def graph_checks(base: Path) -> List[Dict[str, Any]]:
    """Two clean runs must be the same graph: the switch may not add a node."""
    print("\nbehaviour without the variable (real runs)")
    try:
        quiet = _crash.measure_run(base, label="graph_quiet")
        noisy = _crash.measure_run(base, label="graph_again")
    except RuntimeError as exc:
        check("unset: run completes (exit 0, whole master)", False, str(exc))
        return [{"check": "unset_runs_clean", "ok": False, "error": str(exc)}]
    # ``measure_run`` spawns through ``spawn_auto`` with no crash switch; the
    # sentinel run walks the branch (the variable is set) but names a boundary
    # the pipeline never writes, so it must behave exactly like the unset run.
    sentinel_workdir = base / "graph_sentinel" / "work"
    sentinel_pool = _crash.clone_pool(base / "graph_sentinel" / "pool")
    proc, _out, _argv = _crash.spawn_auto(
        sentinel_pool, sentinel_workdir, crash_after="__no_such_boundary__"
    )
    armed_code = proc.wait(timeout=180.0)
    try:
        if proc.stdout is not None:
            proc.stdout.close()
    except OSError:
        pass

    def _graph(workdir: Path) -> List[Tuple[int, str, str]]:
        marks = _crash.event_boundaries(workdir)
        return [(s["seq"], s["stage"], s["status"]) for s in marks]

    quiet_graph = _graph(Path(quiet["workdir"]))
    noisy_graph = _graph(Path(noisy["workdir"]))
    armed_graph = _graph(sentinel_workdir)
    quiet_keys = sorted(_report_keys(Path(quiet["workdir"])))
    noisy_keys = sorted(_report_keys(Path(noisy["workdir"])))

    check(
        "unset: run completes (exit 0, whole master)",
        quiet["exit_code"] == 0 and quiet["delivered_complete"],
        f"exit={quiet['exit_code']} complete={quiet['delivered_complete']}",
    )
    check(
        "unset vs armed sentinel: same boundary graph",
        quiet_graph == armed_graph,
        f"quiet={len(quiet_graph)} armed={len(armed_graph)} nodes",
    )
    check(
        "two unset runs: same boundary graph",
        quiet_graph == noisy_graph,
        f"{len(quiet_graph)} vs {len(noisy_graph)} nodes",
    )
    check(
        "two unset runs: same report keys",
        quiet_keys == noisy_keys,
        f"{len(quiet_keys)} keys",
    )
    check(
        "armed sentinel: exits 0, never fires",
        armed_code == 0,
        f"exit={armed_code}",
    )
    return [
        {
            "check": "unset_runs_clean",
            "ok": bool(quiet["exit_code"] == 0 and quiet["delivered_complete"]),
        },
        {
            "check": "graph_identical_to_armed_sentinel",
            "ok": bool(quiet_graph == armed_graph),
            "nodes": len(quiet_graph),
        },
        {
            "check": "graph_identical_between_runs",
            "ok": bool(quiet_graph == noisy_graph),
        },
        {"check": "report_keys_identical", "ok": bool(quiet_keys == noisy_keys)},
        {"check": "sentinel_never_fires", "ok": armed_code == 0},
    ]


def _report_keys(workdir: Path) -> List[str]:
    path = workdir / "auto_report.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return sorted(payload) if isinstance(payload, dict) else []


# --------------------------------------------------------------------------- #
# the crash cases
# --------------------------------------------------------------------------- #
def run_case(case: Case, index: int, base: Path) -> Dict[str, Any]:
    """Crash at ``case.boundary``, check the site, resume, check the result."""
    print(f"\n{index}. {case.name} (VX_CRASH_AFTER={case.boundary})")
    stage = case.boundary.partition(":")[0]
    record = _crash.run_crash(
        SEED, index, base, boundary=case.boundary, bridge=case.bridge
    )
    workdir = Path(record["workdir"])

    crashed = check(
        "crashed at the requested boundary",
        record["crashed"],
        f"exit={record['exit_code']} expected={_crash.CRASH_EXIT} "
        f"({record['elapsed_ms']} ms)",
    )
    hit = check(
        "journal ends on the requested boundary",
        record["last_boundary"] == stage,
        f"last={record['last_boundary']!r} wanted={stage!r}",
    )
    check(
        "progress reached the boundary",
        record["progress_rank"] >= _crash.boundary_rank(stage),
        f"rank={record['progress_rank']} wanted>={_crash.boundary_rank(stage)}",
    )
    check(
        "no report before the resume",
        not (workdir / "auto_report.json").is_file(),
        f"phase={record['phase']}",
    )
    missing = [rel for rel in case.present if not (workdir / rel).exists()]
    leaked = [rel for rel in case.absent if (workdir / rel).exists()]
    state = check(
        "crash state is the declared one",
        not missing and not leaked,
        f"missing={missing or 'none'} reached_early={leaked or 'none'}",
    )

    report = _crash.invariants(
        record["pool"],
        workdir,
        baseline=record["baseline"],
        bridge=case.bridge,
    )
    detail = report["detail"]
    check(
        "no stray file (allowlist, before + after)",
        report["no_stray_files"],
        f"before={detail['strays_before'] or 'none'} "
        f"after={detail['strays_after'] or 'none'}",
    )
    check(
        "journal readable",
        report["journal_readable"],
        f"files={len(detail['jsonl_after'])} torn_after={detail['torn_tails_after']}",
    )
    check(
        "sources untouched",
        report["sources_sha_unchanged"],
        f"{len(record['baseline'])} files",
    )
    check(
        "sweep leaves no open intent",
        report["sweep_removes_leftovers"],
        f"journals={len(detail['journals'])} orphans={detail['orphans_after']}",
    )
    resumed = check(
        "resume exits 0 with a whole master",
        report["resume_completes"],
        f"resume_exit={detail['resume_exit']} "
        f"bytes={detail['master_bytes_after_resume']} "
        f"complete={detail['master_complete_after_resume']}",
    )
    check(
        "progress did not go backwards",
        report["progress_forward"],
        f"{detail['progress_before']} -> {detail['progress_after']}",
    )
    if detail["resume_exit"] != 0:
        note("resume stderr tail", str(detail.get("resume_stderr_tail") or ""))

    return {
        "case": case.name,
        "boundary": case.boundary,
        "bridge": case.bridge,
        "crash_exit": record["exit_code"],
        "crashed": record["crashed"],
        "last_boundary": record["last_boundary"],
        "phase": record["phase"],
        "progress_before": detail["progress_before"],
        "progress_after": detail["progress_after"],
        "elapsed_ms": record["elapsed_ms"],
        "coverage": bool(crashed and hit),
        "state_ok": bool(state),
        "resume_exit": detail["resume_exit"],
        "resume_completes": resumed,
        "strays_before": detail["strays_before"],
        "strays_after": detail["strays_after"],
        "invariants": {name: bool(report[name]) for name in _crash.INVARIANT_KEYS},
    }


# --------------------------------------------------------------------------- #
# optional: the external kill, as a smoke sample only
# --------------------------------------------------------------------------- #
def taskkill_smoke(base: Path) -> Dict[str, Any]:
    """One timed tree kill: proves the external path still runs, counts nothing."""
    print("\nsmoke (not counted): one taskkill sample")
    record = _crash.run_kill(
        SEED,
        0,
        base,
        target_phase="render",
        anchor_phase="render",
        sleep_ms=0.0,
        watch_timeout=30.0,
    )
    report = _crash.invariants(
        record["pool"], record["workdir"], baseline=record["baseline"]
    )
    note(
        "timed kill",
        f"exit={record['exit_code']} phase={record['phase']} "
        f"reached={record['trigger']['reached']}",
    )
    note(
        "invariants",
        " ".join(
            f"{name}={'ok' if report[name] else 'BROKEN'}"
            for name in _crash.INVARIANT_KEYS
        ),
    )
    shutil.rmtree(Path(record["workdir"]).parent, ignore_errors=True)
    return {
        "exit_code": record["exit_code"],
        "phase": record["phase"],
        "coverage": False,
        "invariants": {name: bool(report[name]) for name in _crash.INVARIANT_KEYS},
    }


def main() -> int:
    global PASS, FAIL, SKIP
    print(
        f"seed={SEED} cases={len(CASES)} "
        f"boundaries={','.join(case.boundary for case in CASES)} "
        f"taskkill_smoke={'on' if TASKKILL else 'off (VX_F17_TASKKILL=1 to add)'}"
    )
    if not _crash.FFMPEG:
        print(
            "skip: VX_FFMPEG is not set; without an ffmpeg binary the corpus "
            "cannot be built and there is nothing to crash"
        )
        skip("whole file", "VX_FFMPEG missing")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    started = time.time()
    with tempfile.TemporaryDirectory(prefix="vx-f17b-") as raw:
        base = Path(raw)

        switch = switch_checks()
        graph = graph_checks(base)

        cases = [run_case(case, index + 1, base) for index, case in enumerate(CASES)]
        smoke = taskkill_smoke(base) if TASKKILL else None

        covered = [item["case"] for item in cases if item["coverage"]]
        uncovered = [item["case"] for item in cases if not item["coverage"]]
        if uncovered:
            FAIL += 1
            print(f"\nCOVERAGE HOLE: no deterministic crash at {', '.join(uncovered)}")
        if len(covered) != len(CASES):
            print(f"covered {len(covered)}/{len(CASES)}: {' '.join(covered)}")

        payload = {
            "schema": MATRIX_SCHEMA,
            "seed": SEED,
            "note": MATRIX_NOTE,
            "switch": {"env": _crash.CRASH_ENV, "exit_code": _crash.CRASH_EXIT},
            "summary": {
                "cases": [case.name for case in CASES],
                "covered": covered,
                "uncovered": uncovered,
                "failures": FAIL,
                "taskkill_smoke": TASKKILL,
            },
            "switch_checks": switch,
            "graph_checks": graph,
            "cases": cases,
            "smoke": smoke,
        }
        run_matrix = _write_matrix(payload, base / "crash_matrix.json")
        print(f"\nmatrix (this run): {run_matrix}")
        if os.environ.get("VX_REGEN_F17") == "1":
            refreshed = _write_matrix(payload, MATRIX_PATH)
            try:
                shown = refreshed.relative_to(ROOT).as_posix()
            except ValueError:
                shown = str(refreshed)
            print(f"committed fixture REFRESHED (VX_REGEN_F17=1): {shown}")
        else:
            print(
                "committed fixture untouched: set VX_REGEN_F17=1 to refresh "
                "tests/fixtures/f17b/crash_matrix.json"
            )

    elapsed = time.time() - started
    print("\ncoverage per stage (deterministic, exit 70):")
    for item in cases:
        print(
            f"  {item['case']:<10} boundary={item['boundary']:<12} "
            f"exit={item['crash_exit']:<3} last={item['last_boundary']:<12} "
            f"resume_exit={item['resume_exit']} "
            f"progress={item['progress_before']}->{item['progress_after']} "
            f"{'covered' if item['coverage'] else 'NOT COVERED'}"
        )
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  ({elapsed:.1f}s)")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
