"""F17-B/T10: 50 real process kills against the real ``vx auto`` pipeline.

Each kill is a ``taskkill /F /T`` (POSIX: ``killpg(SIGKILL)``) against a child
running ``vx.cli auto`` on a tiny corpus.  Nothing here simulates a crash, exits
in-process or stubs the pipeline.

Kills are aimed at phases, not at wall-clock instants
-----------------------------------------------------
A delay drawn from the whole run lands wherever the pipeline happens to be, and
where that is depends on machine load: the same matrix produced
``lock=48 staging=2`` on one run and ``lock=20 loudness=20`` on another, with
``render`` - where a render can be left half-written - hit zero times.  Coverage
is the property being proven, so the trigger is the phase itself.

Calibration first: one real uninterrupted run (spawned through ``spawn_auto``,
allowed to finish) is timed *and* its phase timeline is recorded by polling
``phase_guess`` - the same label the report prints - so the boundary of every
phase is the measured one, not a fraction of the total.

Then the kill itself is calibrated, because a kill is not instantaneous.
``measure_kill_latency`` measures how long ``kill_tree`` takes to actually end a
process on this machine (measured on the reference Windows box: ~350 ms for
``taskkill /F /T``, against a ~90 ms ``render`` phase).  A kill that fires at
the moment a marker appears therefore dies several hundred milliseconds later,
which is fine for a two-second phase and useless for a ninety-millisecond one.
So each sample is scheduled on the timeline::

    die_at  = start(target) + jitter              # inside the phase
    fire_at = die_at - kill_latency               # when the trigger must fire

and the trigger waits for the latest phase marker at or before ``fire_at``
(``_crash.anchor_for``), then sleeps the remainder.  For a long phase that
anchor is the phase itself: the run is watched, the marker appears, the kill
fires inside the phase.  For a short phase it is the preceding marker, and the
sleep is the measured offset - the only way an event-triggered kill can land in
a phase shorter than the hand that delivers it.

Per-phase quota
---------------
``render >= 8``, ``loudness >= 6``, ``staging >= 6`` are the declared minimums;
the 30 remainder kills are split ``lock 24`` / ``boot 6``.  A phase that is
short enough to throw kills into its neighbours is retried on fresh passes over
the corpus - every miss is credited to the phase it actually landed in, so no
pass is wasted - and if a minimum is still unmet when the 50 samples are spent
the test FAILS with ``the matrix under-covered phase X``.

Every sample gets one of three honest verdicts
----------------------------------------------
``passed``
    the kill landed while the run was in flight - no ``auto_report.json``, no
    exit 0, no complete delivered master - and all invariants hold.
``inconclusive``
    the run had already finished (``run_finished_before_kill``), or had already
    delivered a whole master (``master_delivered_before_kill``), or the killer
    did not land (``kill_did_not_land``), or the target phase was never reached
    and the kill fell back before it (``phase_not_reached``).  The reason is
    recorded; an inconclusive sample is never a pass and never a failure.
``failed``
    an invariant broke.  Timing does not excuse an invariant, so this verdict
    wins over ``inconclusive``.

The test passes only when there are **no failures**, the per-phase minimums are
met, and at least ``MIN_CONCLUSIVE`` (40) samples are conclusive.

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

Fixture policy
--------------
The per-run matrix (``vx.f17b.kill_matrix/3``: calibration run, measured kill
latency, per-phase quota and coverage, and every sample with its target, anchor,
observed phase, trigger delay, verdict, reasons and invariant verdicts) is
written to the run's temp directory, so a suite run leaves the tree clean.  The
committed fixture ``tests/fixtures/f17b/kill_matrix.json`` is refreshed **only**
when ``VX_REGEN_F17=1`` is set: a kill matrix is evidence of one run on one
machine, not a golden value.

Run:  python tests/test_f17_crash.py
      VX_REGEN_F17=1 python tests/test_f17_crash.py     (refresh the fixture)
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
MIN_CONCLUSIVE = 40

#: Declared minimums: the phases the matrix must prove it can hit.
PHASE_MINIMUMS = {"render": 8, "loudness": 6, "staging": 6}
#: The remainder of the 50, split between the two phases that are cheap to hit.
PHASE_REMAINDER = {"lock": 24, "boot": 6}
PHASE_QUOTAS = {**PHASE_MINIMUMS, **PHASE_REMAINDER}

#: The kill must be comfortably inside the phase, not on its last millisecond.
KILL_MARGIN_MS = 25
#: Closed-loop correction of the schedule: how much one missed phase moves it.
CORRECTION_STEP_MS = 40.0
CORRECTION_LIMIT_MS = 240.0
POLL_SECONDS = 0.005

MATRIX_PATH = ROOT / "tests" / "fixtures" / "f17b" / "kill_matrix.json"
MATRIX_SCHEMA = "vx.f17b.kill_matrix/3"
SEED = int(os.environ.get("VX_F17_SEED", "1717"))
MATRIX_NOTE = (
    "per-run matrix; the committed fixture is refreshed only when "
    "VX_REGEN_F17=1, because a kill matrix is evidence of one run on one "
    "machine, not a golden value"
)

PASS = 0
FAIL = 0
SKIP = 0


def _skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:64} {reason}")


def _write_matrix(payload: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def _verdict(record: dict, broken: list, target_phase: str) -> tuple[str, list, bool]:
    """``(verdict, reasons, conclusive)`` for one sample.

    An invariant break is always ``failed``, even when the kill arrived late:
    the invariants are about the artifacts, not about the timing.  Everything
    that proves nothing is ``inconclusive`` with its reason; only a kill that
    landed during the run and left nothing broken is ``passed``.
    """
    if broken:
        return "failed", [], not record["finished"]
    if record["finished"]:
        return "inconclusive", ["run_finished_before_kill"], False
    if record["delivered_complete"]:
        return "inconclusive", ["master_delivered_before_kill"], False
    if target_phase != "boot" and record["phase"] == "boot":
        return "inconclusive", ["phase_not_reached"], False
    if not record["killed"]:
        return "inconclusive", ["kill_did_not_land"], False
    return "passed", [], True


def _schedule(
    phase: str,
    timeline: list,
    windows: dict,
    latency_ms: int,
    attempt: int,
    quota: int,
    correction_ms: float = 0.0,
) -> dict:
    """Where inside the phase to die, and which marker to fire from.

    A phase longer than the kill latency needs no prediction: wait for its own
    marker, sleep a jitter, fire, and the process dies ``latency + jitter``
    later, still inside the phase.  The jitter bands split the remaining room
    (``window - margin - latency``) so a phase's attempts are distinct points
    and the death never crosses the boundary.

    A phase shorter than the kill latency cannot be hit that way at all - the
    death would always be after it - so the trigger moves to an earlier phase
    boundary and the sleep becomes the calibrated offset minus the latency
    (``_crash.anchor_for``).  That path carries the timing error of the
    estimate, so ``correction_ms`` shifts it by what the previous attempt on
    this phase actually hit: early means fire later, late means fire earlier.
    """
    starts = {entry["phase"]: entry["start_ms"] for entry in timeline}
    window = max(1.0, float(windows.get(phase, 1)))
    bands = max(1, quota)
    band = (attempt % bands + 0.5) / bands
    room = window - KILL_MARGIN_MS - float(latency_ms)
    if room > 0:
        jitter = min(room, max(0.0, band * room))
        fire_at = float(starts.get(phase, 0)) + jitter
        return {
            "mode": "marker",
            "jitter_ms": jitter,
            "correction_ms": 0.0,
            "die_at_ms": fire_at + float(latency_ms),
            "fire_at_ms": fire_at,
            "anchor": phase,
            "sleep_ms": jitter,
        }
    usable = max(0.0, window - KILL_MARGIN_MS)
    jitter = min(usable, max(0.0, (0.25 + 0.5 * band) * usable + correction_ms))
    die_at = float(starts.get(phase, 0)) + jitter
    fire_at = die_at - float(latency_ms)
    anchor, sleep_ms = _crash.anchor_for(timeline, fire_at)
    return {
        "mode": "compensated",
        "jitter_ms": jitter,
        "correction_ms": correction_ms,
        "die_at_ms": die_at,
        "fire_at_ms": fire_at,
        "anchor": anchor,
        "sleep_ms": sleep_ms,
    }


def _next_target(counts: dict, windows: dict) -> str | None:
    """Fill the declared minimums first, then split the remainder.

    Among the open minimums the shortest measured window wins: ``render`` is
    both the hardest to hit and the reason this half of the task exists, so it
    is attempted while the budget is still intact.  A miss is credited to the
    phase it landed in, so an early attempt on a hard phase is not wasted.
    """
    unmet = [name for name, need in PHASE_MINIMUMS.items() if counts[name] < need]
    if unmet:
        return min(unmet, key=lambda name: (windows.get(name, 0), counts[name], name))
    open_remainder = [
        name for name, need in PHASE_REMAINDER.items() if counts[name] < need
    ]
    if not open_remainder:
        return None
    return min(
        open_remainder,
        key=lambda name: (
            name != "boot",
            counts[name] - PHASE_REMAINDER[name],
            name,
        ),
    )


def main() -> int:
    global PASS, FAIL, SKIP
    print(
        f"seed={SEED} kills={KILLS} ceiling={CEILING_SECONDS}s "
        f"min_conclusive={MIN_CONCLUSIVE} trigger=phase "
        f"minimums={','.join(f'{k}>={v}' for k, v in PHASE_MINIMUMS.items())} "
        f"remainder={','.join(f'{k}={v}' for k, v in PHASE_REMAINDER.items())}"
    )
    if not _crash.FFMPEG:
        print(
            "skip: VX_FFMPEG is not set; without an ffmpeg binary the corpus "
            "cannot be built and there is nothing to kill"
        )
        _skip("whole file", "VX_FFMPEG missing")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    started = time.time()

    with tempfile.TemporaryDirectory(prefix="vx-f17b-") as raw:
        base = Path(raw)

        # -- calibrate: one real run, timed *and* mapped by phase ------------ #
        print("calibration: spawning one uninterrupted run ...", flush=True)
        try:
            calib = _crash.measure_run(base, label="calibration")
        except RuntimeError as exc:
            print(f"calibration FAILED: {exc}")
            print(f"\n{PASS} PASS / {FAIL + 1} FAIL / {SKIP} SKIP")
            return 1
        duration_ms = int(calib["duration_ms"])
        timeline = calib["timeline"]
        windows = {
            name: int(value) for name, value in calib["phase_windows_ms"].items()
        }
        print(
            f"calibration: run took {calib['seconds']:.2f} s "
            f"(exit {calib['exit_code']}, delivered complete="
            f"{calib['delivered_complete']})"
        )
        print("phase boundaries (measured):")
        for entry in timeline:
            print(
                f"  {entry['start_ms']:6d} ms  {entry['phase']:<10} "
                f"{entry['duration_ms']:6d} ms"
            )
        latency = _crash.measure_kill_latency()
        latency_ms = int(latency["latency_ms"])
        print(
            f"kill latency (measured): {latency_ms} ms "
            f"samples={latency['samples_ms']} "
            f"(the child keeps running this long after the trigger fires)"
        )
        for name in PHASE_QUOTAS:
            if windows.get(name, 0) <= 0:
                print(
                    f"  note: phase {name} is absent from the calibration "
                    "timeline; it cannot be scheduled"
                )
        watch_timeout = max(10.0, float(calib["seconds"]) * 3.0)

        kills: list = []
        failures: list = []
        counts = {name: 0 for name in PHASE_QUOTAS}
        attempts = {name: 0 for name in PHASE_QUOTAS}
        corrections = {name: 0.0 for name in PHASE_QUOTAS}
        uncredited = 0
        last_note = ""

        index = 0
        while index < KILLS:
            if time.time() - started > CEILING_SECONDS:
                for rest in range(index, KILLS):
                    _skip(f"#{rest:02d}", f"ceiling {CEILING_SECONDS}s reached")
                break
            target = _next_target(counts, windows)
            if target is None:
                break
            plan = _schedule(
                target,
                timeline,
                windows,
                latency_ms,
                attempts[target],
                max(PHASE_QUOTAS[target], 1),
                corrections[target],
            )
            attempts[target] += 1

            record = _crash.run_kill(
                SEED,
                index,
                base,
                target_phase=target,
                anchor_phase=plan["anchor"],
                sleep_ms=plan["sleep_ms"],
                jitter_ms=plan["jitter_ms"],
                watch_timeout=watch_timeout,
                poll_s=POLL_SECONDS,
            )
            report = _crash.invariants(
                record["pool"], record["workdir"], baseline=record["baseline"]
            )
            broken = _crash.failing_keys(report)
            verdict, reasons, conclusive = _verdict(record, broken, target)
            observed = record["phase"]
            credited = False
            if verdict in ("passed", "failed") and observed in counts:
                counts[observed] += 1
                credited = True
            elif verdict != "inconclusive":
                uncredited += 1
            if verdict == "passed" and plan["mode"] == "compensated":
                if _crash.phase_rank(observed) < _crash.phase_rank(target):
                    corrections[target] += CORRECTION_STEP_MS
                elif _crash.phase_rank(observed) > _crash.phase_rank(target):
                    corrections[target] -= CORRECTION_STEP_MS
                corrections[target] = max(
                    -CORRECTION_LIMIT_MS, min(CORRECTION_LIMIT_MS, corrections[target])
                )

            kills.append(
                {
                    "index": index,
                    "target_phase": target,
                    "anchor_phase": plan["anchor"],
                    "mode": plan["mode"],
                    "phase": observed,
                    "delay_ms": record["delay_ms"],
                    "jitter_ms": int(round(plan["jitter_ms"])),
                    "planned_die_at_ms": int(round(plan["die_at_ms"])),
                    "fire_at_ms": int(round(plan["fire_at_ms"])),
                    "scheduled_sleep_ms": int(round(plan["sleep_ms"])),
                    "trigger": record["trigger"],
                    "exit_code": record["exit_code"],
                    "landed": verdict != "inconclusive",
                    "conclusive": conclusive,
                    "verdict": verdict,
                    "reasons": reasons,
                    "credited": credited,
                    "invariants": {
                        name: bool(report[name]) for name in _crash.INVARIANT_KEYS
                    },
                }
            )

            why = f" reason={','.join(reasons)}" if reasons else ""
            broke = f" broken={','.join(broken)}" if broken else ""
            print(
                f"#{index:02d} target={target:<8} anchor={plan['anchor']:<8} "
                f"{plan['mode'][:4]} jitter={int(round(plan['jitter_ms'])):<4} "
                f"sleep={int(round(plan['sleep_ms'])):<5} "
                f"phase={observed:<10} delay={record['delay_ms']:<5} "
                f"exit={record['exit_code']:<3} {verdict}{why}{broke}"
            )

            if verdict == "failed":
                FAIL += 1
                failures.append(
                    {
                        "index": index,
                        "target_phase": target,
                        "anchor_phase": plan["anchor"],
                        "phase": observed,
                        "delay_ms": record["delay_ms"],
                        "exit_code": record["exit_code"],
                        "finished": record["finished"],
                        "delivered_complete": record["delivered_complete"],
                        "broken": broken,
                        "detail": report["detail"],
                        "workdir": str(record["workdir"]),
                    }
                )
            else:
                if verdict == "passed":
                    PASS += 1
                shutil.rmtree(record["workdir"].parent, ignore_errors=True)

            detail = report["detail"]
            last_note = (
                f"resume exit={detail['resume_exit']} "
                f"master={detail['master_after_resume']} "
                f"complete={detail['master_complete_after_resume']} "
                f"bytes={detail['master_bytes_after_resume']} "
                f"(after kill #{index:02d} targeted at {target}, landed in "
                f"{observed} at {record['delay_ms']} ms)"
            )
            index += 1

        conclusive = sum(1 for kill in kills if kill["conclusive"])
        inconclusive = len(kills) - conclusive
        failed = sum(1 for kill in kills if kill["verdict"] == "failed")
        distribution: dict = {}
        for kill in kills:
            if kill["verdict"] != "inconclusive":
                distribution[kill["phase"]] = distribution.get(kill["phase"], 0) + 1
        under = {
            name: (counts[name], need)
            for name, need in PHASE_MINIMUMS.items()
            if counts[name] < need
        }

        summary = {
            "measured_run_ms": duration_ms,
            "measured_run_seconds": round(float(calib["seconds"]), 3),
            "kill_latency_ms": latency_ms,
            "kill_latency_samples_ms": latency["samples_ms"],
            "trigger": "phase",
            "minimums": PHASE_MINIMUMS,
            "remainder": PHASE_REMAINDER,
            "coverage": counts,
            "uncredited": uncredited,
            "scheduled": len(kills),
            "conclusive": conclusive,
            "inconclusive": inconclusive,
            "failed": failed,
            "skipped": SKIP,
            "min_conclusive": MIN_CONCLUSIVE,
            "phases": dict(sorted(distribution.items())),
            "under_covered": {name: list(pair) for name, pair in under.items()},
            "calibration_timeline": timeline,
        }
        payload = {
            "schema": MATRIX_SCHEMA,
            "seed": SEED,
            "note": MATRIX_NOTE,
            "summary": summary,
            "kills": kills,
        }
        run_matrix = _write_matrix(payload, base / "kill_matrix.json")
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
                "tests/fixtures/f17b/kill_matrix.json"
            )
        if last_note:
            print(f"resume proof (last kill): {last_note}")
        for failure in failures:
            print(
                f"\nfailed kill #{failure['index']:02d} "
                f"(target {failure['target_phase']}, landed "
                f"{failure['phase']}):"
            )
            print(f"  delay_ms={failure['delay_ms']} exit={failure['exit_code']}")
            print(f"  broken={failure['broken']}")
            print(f"  detail={json.dumps(failure['detail'], sort_keys=True)}")

    elapsed = time.time() - started
    coverage = " ".join(
        f"{name}={counts[name]}/{need}" for name, need in PHASE_QUOTAS.items()
    )
    landed_phases = (
        " ".join(f"{name}={count}" for name, count in sorted(distribution.items()))
        or "none"
    )
    print(f"\nmeasured run: {duration_ms} ms ({float(calib['seconds']):.2f} s)")
    print(f"kill latency: {latency_ms} ms")
    print(f"quota coverage: {coverage}")
    print(f"phases (landed kills): {landed_phases}")
    print(
        f"conclusive={conclusive} inconclusive={inconclusive} "
        f"failed={failed} uncredited={uncredited} skipped={SKIP}"
    )
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  ({elapsed:.1f}s)")

    problems = 0
    if FAIL:
        problems += 1
    for name, (got, need) in under.items():
        print(f"the matrix under-covered phase {name}: {got} kill(s), need {need}")
        problems += 1
    if conclusive < MIN_CONCLUSIVE:
        print(
            f"INSUFFICIENT: only {conclusive} conclusive kills, "
            f"need {MIN_CONCLUSIVE}; the matrix measured too little to prove "
            "anything"
        )
        problems += 1
    if problems:
        return 1
    if SKIP:
        print("INCOMPLETE: kills were skipped, the matrix is not a proof")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
