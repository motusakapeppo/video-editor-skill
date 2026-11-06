"""Crash harness for F17-B: a real ``vx auto`` run that dies at a stage edge.

Two ways to make the child die, and only one of them is evidence of coverage:

* **deterministic (default)** - ``VX_CRASH_AFTER=<stage>[:<n>]`` (see
  ``vx.auto``) makes the child call ``os._exit(70)`` right after the n-th
  boundary named ``<stage>`` is flushed to ``events.jsonl``. Exit code 70 *is*
  the proof that the requested boundary was reached, the last line of the
  journal says where, and no wall clock is involved anywhere.
* **timed (optional smoke)** - ``run_kill`` waits a seeded delay (or a phase
  marker), then kills the tree with ``taskkill /F /T`` (POSIX:
  ``killpg(SIGKILL)``). A ``render`` stage lasts ~125-188 ms on the reference
  box and a tree kill takes ~125-171 ms to land, so whether a timed kill hits
  a short stage is chance: useful as a smoke sample, useless as coverage.

Either way the parent never imports the pipeline: it builds a tiny corpus,
spawns ``vx.cli auto``, and then checks what the dead run left on disk against
the invariants a crash-safe pipeline owes its caller, and resumes the same
workdir to completion.

Why the invocation differs from ``tests/test_cli_auto.py``
----------------------------------------------------------
``test_cli_auto.py`` builds its pool at the delivery canvas (1080x1920) because
the triage gate rejects any source that would need more than 1.5x upscale.
That is the right fixture for an e2e proof and the wrong one here: the same
pool takes ~11 s per run, and a matrix of crash-plus-resume samples would blow
the ceiling.  This harness keeps the 9:16 shape, shrinks the frame to 320x568
(a 3.4x upscale from the canvas) and raises ``triage_max_upscale`` to 4 so the
gate still decides on a real rule instead of being bypassed by a fixture that
already matches the canvas.  ``--no-bridge`` removes the analysis pass before
assembly (``bridge=True`` keeps it, for the case that must reach the
``bridge`` boundary); ``--no-subs`` keeps ASR out.  Every other stage -
triage, ingest, staging, plan, render, loudness, stage cache, master write, QC,
deliverables, report - still runs.  One run is ~3.4 s on the reference machine.

The write-ahead journal is opt-in by presence (``vx.artifacts``: "Callers that
want the safety net call this once at the start of a run, or write the same
file themselves"), and ``vx.cli auto`` never calls ``enable()``.  The harness
therefore creates ``<workdir>/render/.vx-writes.jsonl`` before spawning - the
documented switch - so the killed run really journals its stage artifacts and
really goes through ``write_bytes_atomic``.

When is a run "finished"?
-------------------------
The delivery path is *not* that signal, and this is measured, not assumed:
polling a clean run every 20 ms gives

    .lock                    0.24 s
    events.jsonl             0.24 s
    stage_00/                1.27 s
    plan.json, edl_graph     1.60 s
    render/final_pre_*.mp4   1.72 s
    --out master             2.23 s   (0 B, then the whole file by ~2.30 s)
    workdir/out/master.mp4   2.98 s
    auto_report.json         3.38 s
    exit 0                   3.40 s

The master named by ``--out`` is the loudness stage's artifact: ffmpeg writes it
in place, complete, about a second *before* the run is done.  A kill at 2.3 s
therefore leaves a byte-complete master behind while the run was still in
flight, so "master exists" would reject every kill in the delivery window - the
most interesting crash site - and would call a truncated master a finished run.
``finished`` is the run reaching its end (exit 0, or ``auto_report.json`` on
disk, written last and atomically).  ``master_present`` and ``delivered`` keep
the literal facts for the record.

Declared workdir layout
-----------------------
The allowlist below is the layout a run actually produces, read off disk:

    <workdir>/.vx/lock                       run lease (stale after a kill)
    <workdir>/events.jsonl                   vx.events append log
    <workdir>/plan.json                      the payload plan
    <workdir>/auto_report.json               the final report
    <workdir>/bridge_report.json             bridge analysis (not with --no-bridge)
    <workdir>/feedback_request.json          the optional vote request written at the end
    <workdir>/gate_wait.json                the gates' state (waited, by, expired)
    <workdir>/font_proposal.json             the proposed font (chosen by a human)
    <workdir>/timing_history.jsonl           measured timings for the next estimate
    <workdir>/render_history.jsonl           one line per render
    <workdir>/stage_cache_report.json        cache statistics
    <workdir>/render/.vx-writes.jsonl        the write-ahead journal
    <workdir>/render/04_loudness.txt         loudnorm measurement
    <workdir>/render/edl_graph.txt           filtergraph text
    <workdir>/render/edl_render.json         render metadata
    <workdir>/render/render_effects.json     the effects block the graph was built from
    <workdir>/render/ffmpeg-progress.txt     render progress counter
    <workdir>/render/final_pre_loudness.mp4  pre-master render
    <workdir>/render/stage_cache/<key>.json  content-addressed cache entries
    <workdir>/out/{master.mp4,thumbnail.jpg,contact_sheet.jpg,subtitles.srt}
    <workdir>/coherence/shot_NNN.png         frames sampled for the coherence pass
    <workdir>/fonts/<...>                    the chosen font and its specimens
    <workdir>/keyframes/{pending,waiting}.json  the framing gate
    <workdir>/stage_NN/<...>                 staged sources
    <workdir>/{checkpoints,cache,quarantine}/<...>   optional caches / sweeps

Two entries are deliberately loose.  A file ending in ``.tmp`` or ``.partial``
is a temp file a SIGKILL can legitimately orphan mid-``os.replace``; the
allowlist names it so it is not reported as a stray, while the sweep invariant
judges the journal, which is the only thing that can say whether such a file
still stands in for a half-finished write.  ``stage_NN`` is matched by prefix
because the stage index depends on the pool, not on the harness.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"
for _entry in (str(SCRIPTS), str(TESTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from vx import artifacts, events  # noqa: E402

FFMPEG = os.environ.get("VX_FFMPEG") or ""
MASTER_NAME = "master.mp4"

#: The switch ``vx.auto`` reads at every stage boundary: ``<stage>[:<n>]``.
#: A child started with it dies with ``os._exit(70)`` right after that boundary
#: is flushed to ``events.jsonl``, so the exit code *is* the proof of coverage.
CRASH_ENV = "VX_CRASH_AFTER"
CRASH_EXIT = 70

# -- fixture shape ---------------------------------------------------------- #
CLIP_W, CLIP_H = 320, 568
CLIP_SEC = 1.5
CLIP_FPS = 12
MUSIC_SEC = 1

# -- delay plan ------------------------------------------------------------- #
DELAY_MIN_MS = 50
DELAY_MAX_MS = 2500

# -- CLI invocation --------------------------------------------------------- #
PROMPT = "un reel"
OVERRIDES = (
    "thresholds.triage_max_upscale=4",
    "features.stage_cache=on",
)

# -- declared allowlist (see module docstring) ------------------------------ #
ALLOWED_TOP_FILES = {
    ".lock",
    ".vx-writes.jsonl",
    "events.jsonl",
    "plan.json",
    "auto_report.json",
    "bridge_report.json",
    "render_history.jsonl",
    "stage_cache_report.json",
    "feedback_request.json",
    "font_proposal.json",
    "gate_wait.json",
    "timing_history.jsonl",
}
ALLOWED_TOP_DIRS = {
    "out",
    "render",
    "quarantine",
    ".quarantine",
    ".vx",
    "checkpoints",
    "cache",
    "coherence",
    "fonts",
    "keyframes",
}
ALLOWED_TOP_DIR_PREFIXES = ("stage_",)
ALLOWED_RENDER_FILES = {
    ".vx-writes.jsonl",
    "04_loudness.txt",
    "edl_graph.txt",
    "edl_render.json",
    "render_effects.json",
    "final_pre_loudness.mp4",
    "stage_cache_report.json",
    "ffmpeg-progress.txt",
}
ALLOWED_RENDER_CACHE_SUFFIXES = (".json",)
ALLOWED_OUT_FILES = {"master.mp4", "thumbnail.jpg", "contact_sheet.jpg", "subtitles.srt"}
ALLOWED_DEBRIS_SUFFIXES = (".tmp", ".partial")

INVARIANT_KEYS = (
    "sources_sha_unchanged",
    "journal_readable",
    "no_stray_files",
    "resume_completes",
    "sweep_removes_leftovers",
    "progress_forward",
)


# --------------------------------------------------------------------------- #
# fixture
# --------------------------------------------------------------------------- #
def _make(path: Path, args: Sequence[str]) -> bool:
    """One ffmpeg call that must produce a non-empty file."""
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args, str(path)],
        capture_output=True,
    )
    return proc.returncode == 0 and path.is_file() and path.stat().st_size > 0


def build_pool(root: str | Path) -> bool:
    """Write the tiny corpus: two clips plus one music bed.  False on failure."""
    pool = Path(root)
    pool.mkdir(parents=True, exist_ok=True)
    for name, freq in (("a.mp4", 440), ("b.mp4", 660)):
        ok = _make(
            pool / name,
            [
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={CLIP_W}x{CLIP_H}:rate={CLIP_FPS}:duration={CLIP_SEC}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={freq}:sample_rate=48000:duration={CLIP_SEC}",
                "-shortest",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
            ],
        )
        if not ok:
            return False
    return _make(
        pool / "music.m4a",
        ["-f", "lavfi", "-i", f"sine=frequency=440:duration={MUSIC_SEC}", "-c:a", "aac"],
    )


_TEMPLATE: Dict[str, Path] = {}


def template_pool() -> Path:
    """The corpus is encoded once with ffmpeg, then copied per kill.

    Fifty fresh encodes would add ~30 s of the 600 s ceiling for identical
    bytes; the copy is byte-for-byte the same corpus, so the source-hash
    invariant keeps its meaning.
    """
    cached = _TEMPLATE.get("path")
    if cached is not None and cached.is_dir():
        return cached
    holder = Path(tempfile.mkdtemp(prefix="vx-f17b-pool-"))
    atexit.register(shutil.rmtree, holder, True)
    pool = holder / "pool"
    if not build_pool(pool):
        raise RuntimeError(f"ffmpeg could not build the corpus in {pool}")
    _TEMPLATE["path"] = pool
    return pool


def clone_pool(root: str | Path) -> Path:
    """A fresh, writable copy of the corpus for one kill."""
    target = Path(root)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(template_pool(), target)
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def sha_map(root: str | Path) -> Dict[str, str]:
    """Relative path -> sha256 for every file under ``root``."""
    base = Path(root)
    return {
        path.relative_to(base).as_posix(): _sha256(path)
        for path in sorted(base.rglob("*"))
        if path.is_file()
    }


# --------------------------------------------------------------------------- #
# delays
# --------------------------------------------------------------------------- #
def delay_plan(
    seed: int,
    count: int,
    *,
    low_ms: Optional[int] = None,
    high_ms: Optional[int] = None,
) -> List[int]:
    """``count`` delays in ``[low, high]`` from one seed.

    Stratified, not plain uniform: the range is cut into ``count`` equal bands
    and each band contributes one draw, so the kills really cover the whole
    pipeline instead of clustering the way a uniform sample of 50 does.  The
    jitter inside a band keeps the sequence seed-dependent.

    ``low_ms``/``high_ms`` are the optional window and default to the fixed
    ``DELAY_MIN_MS``-``DELAY_MAX_MS`` range, so the historical plan is
    unchanged.  The crash matrix passes a window derived from a *measured* run
    (see ``measure_run``): a pipeline that got faster needs the kills earlier,
    and a kill scheduled past the end of the run proves nothing.  The floor
    ``DELAY_MIN_MS`` always holds - a delay below it kills during interpreter
    boot, before any stage has written anything.
    """
    low_bound = DELAY_MIN_MS if low_ms is None else max(DELAY_MIN_MS, int(low_ms))
    high_bound = DELAY_MAX_MS if high_ms is None else int(high_ms)
    if high_bound <= low_bound:
        high_bound = low_bound + 1
    if count <= 0:
        return []
    rng = random.Random(seed)
    span = (high_bound - low_bound) / float(count)
    delays: List[int] = []
    for index in range(count):
        low = low_bound + index * span
        delays.append(int(round(low + rng.uniform(0.0, span))))
    return [min(high_bound, max(low_bound, value)) for value in delays]


# --------------------------------------------------------------------------- #
# reading the crash site
# --------------------------------------------------------------------------- #
PHASE_ORDER = (
    "boot",
    "events",
    "lock",
    "staging",
    "planning",
    "render",
    "loudness",
    "delivery",
    "finished",
)


def phase_rank(phase: str) -> int:
    """Where ``phase`` sits in the pipeline; unknown labels rank as ``boot``."""
    try:
        return PHASE_ORDER.index(phase)
    except ValueError:
        return 0


def phase_guess(workdir: str | Path) -> str:
    """Which stage the killed run had reached, read off the workdir.

    Ordered from the end of the pipeline backwards: the first marker that
    exists is the furthest stage the run reached before it died.
    """
    base = Path(workdir)
    markers = (
        ("finished", ("auto_report.json",)),
        ("delivery", ("out/master.mp4",)),
        ("loudness", ("render/final_pre_loudness.mp4", "render/04_loudness.txt")),
        ("render", ("render/edl_graph.txt", "render/edl_render.json")),
        ("planning", ("plan.json",)),
        ("staging", ("stage_00",)),
        ("lock", (".vx/lock", ".lock")),
        ("events", ("events.jsonl",)),
    )
    for label, relatives in markers:
        for relative in relatives:
            if (base / Path(*relative.split("/"))).exists():
                return label
    return "boot"


#: The stage boundaries ``vx.auto`` writes through ``_emit``, in pipeline order.
#: ``run`` appears twice (start and end) and is ranked at the start.
BOUNDARY_ORDER = (
    "run",
    "triage",
    "ingest",
    "bridge",
    "coherence",
    "plan_choice",
    "font",
    "keyframes",
    "render",
    "render_done",
    "qc",
    "deliverables",
    "guard",
    "timings",
)


def boundary_rank(stage: str) -> int:
    """Where a boundary sits in the pipeline; unknown labels rank at the front."""
    try:
        return BOUNDARY_ORDER.index(stage)
    except ValueError:
        return 0


def event_boundaries(workdir: str | Path) -> List[Dict[str, Any]]:
    """Every stage boundary on disk, in write order, as ``stage``/``status``.

    The journal is the only record of *which* boundary a crashed run reached:
    the exit code says the crash was asked for, the last event here says where
    it happened.  Read through ``vx.events`` so a torn final line is dropped
    instead of raising.
    """
    records = events.read(Path(workdir) / "events.jsonl")
    out: List[Dict[str, Any]] = []
    for record in records:
        stage = str(record.get("stage") or "")
        if stage not in BOUNDARY_ORDER:
            continue
        out.append(
            {
                "seq": int(record.get("seq") or 0),
                "stage": stage,
                "status": str(record.get("status") or ""),
            }
        )
    return out


def progress_rank(workdir: str | Path) -> int:
    """The furthest boundary reached, as an index into ``BOUNDARY_ORDER``.

    Monotone by construction: a resume that starts over writes its own early
    boundaries but cannot lower the maximum already on disk, so a *decrease*
    after a resume would mean the journal was rewritten or truncated.
    """
    marks = event_boundaries(workdir)
    return max((boundary_rank(item["stage"]) for item in marks), default=-1)


def wait_for_phase(
    workdir: str | Path,
    target: str,
    deadline_s: float,
    *,
    poll_s: float = 0.005,
    proc: Optional[subprocess.Popen] = None,
) -> Dict[str, Any]:
    """Block until ``phase_guess`` reaches ``target`` or anything after it.

    Phase-triggered kills need the moment a marker appears, and the only honest
    clock for that is the workdir itself: ``phase_guess`` is the same label the
    report prints, so the trigger and the verdict agree by construction.  A
    target that never appears is not an error - the caller kills anyway and the
    sample is classified by what it actually left behind - but the wait must
    stop when the child dies or the deadline passes, or a run that failed at
    startup would block for the whole budget.

    Returns ``observed`` (the label at the moment the wait stopped),
    ``reached_ms`` (wall time since this call started), ``reached`` (whether the
    target or a later phase was seen) and ``stopped`` (``reached``,
    ``deadline`` or ``exited``).
    """
    wanted = phase_rank(target)
    started = time.monotonic()
    phase = phase_guess(workdir)
    while True:
        elapsed = time.monotonic() - started
        if phase_rank(phase) >= wanted:
            return {
                "observed": phase,
                "reached_ms": int(round(elapsed * 1000.0)),
                "reached": True,
                "stopped": "reached",
            }
        if proc is not None and proc.poll() is not None:
            return {
                "observed": phase,
                "reached_ms": int(round(elapsed * 1000.0)),
                "reached": False,
                "stopped": "exited",
            }
        if elapsed >= deadline_s:
            return {
                "observed": phase,
                "reached_ms": int(round(elapsed * 1000.0)),
                "reached": False,
                "stopped": "deadline",
            }
        time.sleep(poll_s)
        phase = phase_guess(workdir)


def mp4_complete(path: str | Path) -> bool:
    """Whether ``path`` is a whole MP4: its top-level boxes tile the file.

    Used only to keep the literal "a master was produced" fact honest.  A file
    a SIGKILL cut in half has a final box that runs past EOF and no ``moov``;
    ffmpeg's default layout (no faststart) puts ``moov`` last, so both tests
    point the same way.  Never raises.
    """
    target = Path(path)
    try:
        size = target.stat().st_size
        handle = target.open("rb")
    except OSError:
        return False
    with handle:
        offset = 0
        kinds: List[str] = []
        while offset < size:
            if offset + 8 > size:
                return False
            handle.seek(offset)
            header = handle.read(8)
            if len(header) != 8:
                return False
            length = int.from_bytes(header[:4], "big")
            kind = header[4:8].decode("latin-1", "replace")
            head = 8
            if length == 1:
                extended = handle.read(8)
                if len(extended) != 8:
                    return False
                length = int.from_bytes(extended, "big")
                head = 16
            elif length == 0:
                length = size - offset
            if length < head or offset + length > size:
                return False
            kinds.append(kind)
            offset += length
        return offset == size and "moov" in kinds


# --------------------------------------------------------------------------- #
# child process
# --------------------------------------------------------------------------- #
def _env(crash_after: Optional[str] = None) -> Dict[str, str]:
    """Child environment; the crash switch is never inherited from the shell.

    ``VX_CRASH_AFTER`` is removed unconditionally and re-added only for the
    child that must die: a developer who exported it to reproduce a case by
    hand must not turn every resume into a crash.
    """
    env = os.environ.copy()
    env.pop(CRASH_ENV, None)
    env["PYTHONPATH"] = str(SCRIPTS)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if FFMPEG:
        env["VX_FFMPEG"] = FFMPEG
    if crash_after:
        env[CRASH_ENV] = str(crash_after)
    return env


def auto_argv(
    pool: str | Path, workdir: str | Path, *, bridge: bool = False
) -> List[str]:
    """The exact child command line; the resume run uses the same list.

    ``bridge=False`` keeps ``--no-bridge`` (the cheap fixture every kill case
    uses). ``bridge=True`` runs the analysis pass, which is the only way to
    reach the ``bridge`` boundary and the ``analysis`` stage behind it.
    """
    out = Path(workdir).parent / MASTER_NAME
    argv = [
        sys.executable,
        "-m",
        "vx.cli",
        "auto",
        "--prompt",
        PROMPT,
        "--assets",
        str(pool),
        "--out",
        str(out),
        "--workdir",
        str(workdir),
        "--no-subs",
        "--autonomous",
    ]
    if not bridge:
        argv.append("--no-bridge")
    for override in OVERRIDES:
        argv.extend(["--override", override])
    argv.append("--json")
    return argv


def master_path(workdir: str | Path) -> Path:
    return Path(workdir).parent / MASTER_NAME


def delivered_path(workdir: str | Path) -> Path:
    """The delivered master inside the workdir (written after loudness + QC)."""
    return Path(workdir) / "out" / MASTER_NAME


def spawn_auto(
    pool: str | Path,
    workdir: str | Path,
    *,
    crash_after: Optional[str] = None,
    bridge: bool = False,
):
    """Spawn the real CLI in its own session; return ``(proc, out, argv)``.

    ``start_new_session`` is what makes the kill a tree kill: on POSIX the
    child gets its own process group, on Windows ``taskkill /T`` walks the
    child tree.  ``stdout=PIPE`` is drained by a thread: the run prints a JSON
    report, and a full pipe buffer would otherwise block the child and make a
    deadlock look like a slow run.

    ``crash_after`` is the only thing that reaches the child through
    ``VX_CRASH_AFTER``; the child then dies by itself at that boundary, which
    is what makes the recovery cases deterministic instead of timed.
    """
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    (work / "render").mkdir(exist_ok=True)
    # Documented opt-in: the journal is on when the file exists.
    artifacts.enable(work / "render")
    argv = auto_argv(pool, work, bridge=bridge)
    proc = subprocess.Popen(
        argv,
        cwd=str(ROOT),
        env=_env(crash_after),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    drained: List[bytes] = []

    def _drain() -> None:
        try:
            for block in iter(proc.stdout.read, b""):
                drained.append(block)
        except (OSError, ValueError):
            pass

    threading.Thread(target=_drain, daemon=True).start()
    proc.vx_output = drained  # type: ignore[attr-defined]
    proc.vx_kill_ok = False  # type: ignore[attr-defined]
    return proc, master_path(work), argv


def kill_tree(proc: subprocess.Popen) -> int:
    """Kill the whole tree from outside and return the child's exit code.

    ``proc.vx_kill_ok`` is set when the killer itself reported success, so a
    caller can tell "the kill landed" from "the child had already exited".
    """
    if proc.poll() is None:
        if os.name == "nt":
            done = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
            )
            proc.vx_kill_ok = done.returncode == 0  # type: ignore[attr-defined]
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.vx_kill_ok = True  # type: ignore[attr-defined]
            except OSError:
                proc.kill()
    try:
        code = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = proc.wait(timeout=30)
    try:
        if proc.stdout is not None:
            proc.stdout.close()
    except OSError:
        pass
    return code


def anchor_for(timeline: Sequence[Dict[str, Any]], fire_at_ms: float):
    """Which phase event to wait for, and how long after it to fire.

    A kill is not instantaneous: on Windows ``taskkill /F /T`` takes a few
    hundred milliseconds to walk the tree and reap it, measured by
    ``measure_kill_latency``.  So to die inside a phase at
    ``start + jitter`` the trigger must fire at ``start + jitter - latency``.
    When that instant is before the phase's own marker - which is always the
    case for a phase shorter than the latency - the wait has to start at an
    earlier phase boundary, and the sleep is measured from *that* marker.

    Returns ``(anchor_phase, sleep_ms)``.  Both come from the calibration
    timeline, so the offset between the anchor and the target is the measured
    one; the anchor itself is detected live in the run being killed.
    """
    chosen = None
    for entry in timeline:
        if entry["start_ms"] <= fire_at_ms:
            if chosen is None or entry["start_ms"] >= chosen["start_ms"]:
                chosen = entry
    if chosen is None:
        return timeline[0]["phase"] if timeline else "boot", 0.0
    return chosen["phase"], max(0.0, fire_at_ms - float(chosen["start_ms"]))


def measure_kill_latency(
    *,
    repeats: int = 3,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """How long ``kill_tree`` takes to actually end a process on this machine.

    A phase-triggered kill is only honest if the process dies *inside* the
    phase the trigger saw.  The trigger is synchronous but the kill is not:
    ``taskkill /F /T`` costs the time to start it plus the time to walk the
    tree, and the child keeps running (and writing) for all of it.  Measured on
    the reference Windows box: ~350 ms, against a ``render`` phase of ~90 ms -
    which is exactly why a marker-triggered kill can never land there and the
    schedule has to fire early.

    Each repeat is a real ``kill_tree`` of a real child started the same way a
    run is (own session, so the tree kill is the tree kill).  The median is
    returned with every sample and the child's identity, so the number can be
    audited.
    """
    samples: List[float] = []
    for _ in range(max(1, repeats)):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=str(ROOT),
            env=_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(0.2)
        began = time.monotonic()
        try:
            kill_tree(child)
        except Exception:  # noqa: BLE001 - a probe that fails must not kill the run
            child.kill()
        samples.append((time.monotonic() - began) * 1000.0)
    ordered = sorted(samples)
    median = ordered[len(ordered) // 2]
    return {
        "latency_ms": int(round(median)),
        "samples_ms": [int(round(value)) for value in samples],
        "timeout": timeout,
    }


def run_kill(
    seed: int,
    index: int,
    base_dir: Optional[str | Path] = None,
    *,
    delay_ms: Optional[int] = None,
    target_phase: Optional[str] = None,
    anchor_phase: Optional[str] = None,
    sleep_ms: float = 0.0,
    jitter_ms: float = 0.0,
    watch_timeout: float = 60.0,
    poll_s: float = 0.005,
) -> Dict[str, Any]:
    """Spawn, kill, and report what the kill left behind.

    Two triggers, one record:

    * ``target_phase`` set - wait for ``anchor_phase`` (default: the target) to
      appear in the workdir with ``wait_for_phase``, sleep ``sleep_ms`` and
      kill.  ``target_phase`` is where the kill is *meant* to die, ``anchor``
      is the boundary that makes it possible when the kill costs more than the
      phase lasts (see ``anchor_for``); ``jitter_ms`` is the offset inside the
      target that the schedule was built for.
    * ``target_phase`` unset - the historical timed kill after ``delay_ms``.

    ``watch_timeout`` bounds the wait: a target that never appears (a run that
    died at startup) must not eat the whole budget.  The phase recorded in the
    result is always ``phase_guess`` of the workdir *after* the kill, which is
    what actually landed, not what was aimed at.
    """
    if delay_ms is None and target_phase is None:
        delay_ms = delay_plan(seed, index + 1)[index]
    if base_dir is None:
        base_dir = tempfile.mkdtemp(prefix="vx-f17b-run-")
        atexit.register(shutil.rmtree, str(base_dir), True)
    run_dir = Path(base_dir) / f"kill_{index:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    pool = clone_pool(run_dir / "pool")
    workdir = run_dir / "work"
    baseline = sha_map(pool)

    proc, out, _argv = spawn_auto(pool, workdir)
    trigger: Dict[str, Any] = {
        "target_phase": target_phase,
        "anchor_phase": anchor_phase or target_phase,
        "jitter_ms": int(round(float(jitter_ms))),
        "scheduled_sleep_ms": int(round(float(sleep_ms))),
        "observed": None,
        "reached_ms": None,
        "stopped": None,
        "reached": None,
    }
    if target_phase is None:
        delay_ms = int(delay_ms or 0)
        time.sleep(max(0.0, float(delay_ms) / 1000.0))
    else:
        watch = wait_for_phase(
            workdir,
            trigger["anchor_phase"],
            watch_timeout,
            poll_s=poll_s,
            proc=proc,
        )
        trigger.update(watch)
        time.sleep(max(0.0, float(sleep_ms) / 1000.0))
        delay_ms = int(trigger["reached_ms"]) + int(round(float(sleep_ms)))
    exit_code = kill_tree(proc)
    killed = exit_code != 0 or bool(getattr(proc, "vx_kill_ok", False))
    report_path = Path(workdir) / "auto_report.json"
    finished = exit_code == 0 or report_path.is_file()
    delivered = delivered_path(workdir)
    return {
        "seed": seed,
        "index": index,
        "delay_ms": int(delay_ms),
        "trigger": trigger,
        "exit_code": int(exit_code),
        "killed": killed,
        "finished": finished,
        "master_present": out.exists(),
        "master_bytes": out.stat().st_size if out.is_file() else 0,
        "master_complete": mp4_complete(out),
        "delivered": delivered.is_file(),
        "delivered_complete": mp4_complete(delivered),
        "phase": phase_guess(workdir),
        "pool": pool,
        "workdir": workdir,
        "baseline": baseline,
        "master": out,
    }


def run_crash(
    seed: int,
    index: int,
    base_dir: Optional[str | Path] = None,
    *,
    boundary: str,
    bridge: bool = False,
    timeout: float = 180.0,
) -> Dict[str, Any]:
    """Spawn the CLI, let it crash itself at ``boundary``, report the site.

    No clock, no trigger, no ``taskkill``: the child reads ``VX_CRASH_AFTER``
    and calls ``os._exit(70)`` at the requested boundary, so an exit code of
    70 is proof that the boundary was reached.  Anything else
    (``0`` = the run finished before that boundary, another code = a real
    failure) is a coverage miss the caller must report, not a flake to retry.

    ``timeout`` only bounds a hang; a normal run is seconds, and the crash is
    instant because it happens inside the child.

    Returns the same shape ``run_kill`` returns, plus ``boundary`` (what was
    asked), ``crashed`` (the child exited 70), ``last_boundary`` (the last
    stage boundary in the journal) and ``boundaries`` (the whole sequence).
    """
    if base_dir is None:
        base_dir = tempfile.mkdtemp(prefix="vx-f17b-run-")
        atexit.register(shutil.rmtree, str(base_dir), True)
    run_dir = Path(base_dir) / f"crash_{index:02d}_{boundary.replace(':', '-')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    pool = clone_pool(run_dir / "pool")
    workdir = run_dir / "work"
    baseline = sha_map(pool)

    started = time.monotonic()
    proc, out, _argv = spawn_auto(
        pool, workdir, crash_after=boundary, bridge=bridge
    )
    try:
        exit_code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        exit_code = -1
    try:
        if proc.stdout is not None:
            proc.stdout.close()
    except OSError:
        pass
    elapsed_ms = int(round((time.monotonic() - started) * 1000.0))

    delivered = delivered_path(workdir)
    marks = event_boundaries(workdir)
    return {
        "seed": seed,
        "index": index,
        "boundary": boundary,
        "bridge": bridge,
        "exit_code": int(exit_code),
        "crashed": int(exit_code) == CRASH_EXIT,
        "elapsed_ms": elapsed_ms,
        "boundaries": marks,
        "last_boundary": marks[-1]["stage"] if marks else "",
        "progress_rank": progress_rank(workdir),
        "finished": exit_code == 0 or (Path(workdir) / "auto_report.json").is_file(),
        "master_present": out.exists(),
        "master_bytes": out.stat().st_size if out.is_file() else 0,
        "master_complete": mp4_complete(out),
        "delivered": delivered.is_file(),
        "delivered_complete": mp4_complete(delivered),
        "phase": phase_guess(workdir),
        "pool": pool,
        "workdir": workdir,
        "baseline": baseline,
        "master": out,
    }


def measure_run(
    base_dir: Optional[str | Path] = None,
    *,
    timeout: float = 180.0,
    label: str = "calibration",
    bridge: bool = False,
) -> Dict[str, Any]:
    """Time ONE real, uninterrupted ``vx auto`` run from spawn to exit.

    This is the calibration the kill window is derived from, and the distinction
    is the whole point of the function: a delay of 60 s measures *the delay*,
    not the run, and calibrating on it produced a 60 s "duration" once already.
    Here the child is spawned by ``spawn_auto`` - same corpus, same argv, same
    journal switch as every kill - and simply allowed to finish, and the clock
    runs from ``Popen`` to ``wait``.

    Returns the wall time in ``seconds`` (float, ``time.monotonic``) plus the
    facts that say the baseline is trustworthy: the exit code, whether the
    delivered master is a whole MP4, and the stdout tail.  It also returns the
    phase timeline it observed while waiting (``timeline``: ``phase``,
    ``start_ms``, ``duration_ms``) and the per-phase totals
    (``phase_windows_ms``): a kill scheduled by wall clock says nothing about
    *which* stage it hit, and the per-phase quota needs the measured boundaries
    rather than a fraction of the total.  Raises ``RuntimeError`` when the run
    does not finish inside ``timeout`` or does not exit 0 with a complete
    delivered master - a broken baseline cannot calibrate anything, and silently
    returning its wall time would hide that.
    """
    if base_dir is None:
        base_dir = tempfile.mkdtemp(prefix="vx-f17b-cal-")
        atexit.register(shutil.rmtree, str(base_dir), True)
    run_dir = Path(base_dir) / label
    run_dir.mkdir(parents=True, exist_ok=True)
    pool = clone_pool(run_dir / "pool")
    workdir = run_dir / "work"

    started = time.monotonic()
    proc, out, _argv = spawn_auto(pool, workdir, bridge=bridge)
    timeline: List[Dict[str, Any]] = []
    current: Optional[str] = None
    entered = 0.0
    exit_code: Optional[int] = None
    while True:
        elapsed = time.monotonic() - started
        phase = phase_guess(workdir)
        if phase != current:
            if current is not None:
                timeline[-1]["duration_ms"] = int(round((elapsed - entered) * 1000.0))
            timeline.append(
                {
                    "phase": phase,
                    "start_ms": int(round(elapsed * 1000.0)),
                    "duration_ms": 0,
                }
            )
            current = phase
            entered = elapsed
        exit_code = proc.poll()
        if exit_code is not None:
            break
        if elapsed > timeout:
            kill_tree(proc)
            raise RuntimeError(
                f"calibration run did not finish within {timeout:.0f} s"
            )
        time.sleep(0.005)
    seconds = time.monotonic() - started
    if timeline:
        timeline[-1]["duration_ms"] = int(round((seconds - entered) * 1000.0))
    windows: Dict[str, int] = {}
    for entry in timeline:
        windows[entry["phase"]] = windows.get(entry["phase"], 0) + int(
            entry["duration_ms"]
        )
    try:
        if proc.stdout is not None:
            proc.stdout.close()
    except OSError:
        pass

    delivered = delivered_path(workdir)
    delivered_complete = delivered.is_file() and mp4_complete(delivered)
    tail = b"".join(list(getattr(proc, "vx_output", [])))[-400:]
    record = {
        "seconds": seconds,
        "duration_ms": int(round(seconds * 1000.0)),
        "exit_code": int(exit_code),
        "master_present": out.exists(),
        "delivered": delivered.is_file(),
        "delivered_complete": bool(delivered_complete),
        "phase": phase_guess(workdir),
        "timeline": timeline,
        "phase_windows_ms": windows,
        "pool": pool,
        "workdir": workdir,
        "stdout_tail": tail.decode("utf-8", "replace"),
    }
    if exit_code != 0 or not delivered_complete:
        raise RuntimeError(
            "calibration run is not a clean baseline: "
            f"exit={exit_code} delivered={delivered.is_file()} "
            f"delivered_complete={delivered_complete} "
            f"tail={record['stdout_tail']!r}"
        )
    return record


# --------------------------------------------------------------------------- #
# invariants
# --------------------------------------------------------------------------- #
def _jsonl_state(workdir: Path):
    """``(readable, torn_tails, names)`` for every jsonl under ``workdir``.

    ``readable`` is ``vx.events.read`` on each file: it must not raise and every
    record it returns must be an object.  A torn final line is *counted*, not
    treated as a read failure - closing off a half-written record is exactly
    what ``vx.events`` is built to do - and the count is what tells the caller
    whether the run died mid-append.
    """
    readable = True
    torn = 0
    names: List[str] = []
    for path in sorted(workdir.rglob("*.jsonl")):
        names.append(path.relative_to(workdir).as_posix())
        try:
            records = events.read(path)
        except Exception:  # noqa: BLE001 - a reader that raises is a failure
            readable = False
            continue
        if any(not isinstance(record, dict) for record in records):
            readable = False
        try:
            raw = path.read_bytes()
        except OSError:
            readable = False
            continue
        if not raw:
            continue
        tail = raw.rstrip(b"\r\n").rsplit(b"\n", 1)[-1].strip()
        if not tail:
            continue
        try:
            json.loads(tail.decode("utf-8", "replace"))
        except ValueError:
            torn += 1
    return readable, torn, names


def _allowed(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return False
    name = parts[-1]
    if name.endswith(ALLOWED_DEBRIS_SUFFIXES):
        return True
    if len(parts) == 1:
        return name in ALLOWED_TOP_FILES
    head = parts[0]
    if head == "render":
        if len(parts) == 2:
            return name in ALLOWED_RENDER_FILES
        if len(parts) == 3 and parts[1] == "stage_cache":
            return name.endswith(ALLOWED_RENDER_CACHE_SUFFIXES)
        return False
    if head == "out":
        return len(parts) == 2 and name in ALLOWED_OUT_FILES
    if head in ALLOWED_TOP_DIRS or head.startswith(ALLOWED_TOP_DIR_PREFIXES):
        return True
    return False


def _strays(workdir: Path) -> List[str]:
    """Files under ``workdir`` that the declared allowlist does not cover."""
    return [
        path.relative_to(workdir).as_posix()
        for path in sorted(workdir.rglob("*"))
        if path.is_file() and not _allowed(Path(path.relative_to(workdir).as_posix()))
    ]


def _debris(workdir: Path) -> List[str]:
    """Temp files a crash may leave behind (allowed, but worth reporting)."""
    return [
        path.relative_to(workdir).as_posix()
        for path in sorted(workdir.rglob("*"))
        if path.is_file() and path.name.endswith(ALLOWED_DEBRIS_SUFFIXES)
    ]


def _classify_all(workdir: Path):
    """Run ``artifacts.classify`` on every journal root under ``workdir``.

    ``orphans`` is the number of open intents whose target still exists on
    disk: the sweep's job, and the one thing that means a crashed write was
    never resolved.
    """
    ok = True
    orphans = 0
    missing: List[str] = []
    journals: List[str] = []
    for journal in sorted(workdir.rglob(artifacts.JOURNAL_NAME)):
        journals.append(journal.relative_to(workdir).as_posix())
        try:
            report = artifacts.classify(journal.parent)
        except Exception:  # noqa: BLE001 - classify must never raise
            ok = False
            continue
        if set(report) != {"completed", "pending", "orphan_files"}:
            ok = False
            continue
        for raw in report["orphan_files"]:
            orphans += 1
            if not Path(str(raw)).is_file():
                missing.append(str(raw))
    return ok, orphans, missing, journals


def _resume(pool: Path, workdir: Path, timeout: float, *, bridge: bool = False):
    argv = auto_argv(pool, workdir, bridge=bridge)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(ROOT),
            env=_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, "resume timed out"
    return proc.returncode, (proc.stderr or "")[-400:]


def invariants(
    pool: str | Path,
    workdir: str | Path,
    *,
    baseline: Optional[Dict[str, str]] = None,
    timeout: float = 180.0,
    bridge: bool = False,
) -> Dict[str, Any]:
    """The invariants a crash-safe auto run owes, plus diagnostics.

    Order matters: the kill-state checks are taken before the resume, because
    the resume is allowed to heal what the crash damaged, and the post-resume
    checks are taken after, because a resume that leaves debris behind is a
    failure.  ``bridge`` must match the crashed run's argv, or the resume would
    be a different pipeline than the one that crashed.
    """
    pool = Path(pool)
    work = Path(workdir)
    delivered = delivered_path(work)
    progress_before = progress_rank(work)

    sources_ok = True
    if baseline is not None:
        sources_ok = sha_map(pool) == baseline

    readable_before, torn_before, jsonl_before = _jsonl_state(work)
    strays_before = _strays(work)

    resume_code, resume_tail = _resume(pool, work, timeout, bridge=bridge)
    progress_after = progress_rank(work)

    readable_after, torn_after, jsonl_after = _jsonl_state(work)
    strays_after = _strays(work)
    debris_after = _debris(work)
    classify_ok, orphans_after, orphan_missing, journals = _classify_all(work)
    resume_completes = bool(
        resume_code == 0 and mp4_complete(delivered) and delivered.stat().st_size > 0
    )

    return {
        "sources_sha_unchanged": bool(sources_ok),
        "journal_readable": bool(readable_before and readable_after and torn_after == 0),
        "no_stray_files": bool(not strays_before and not strays_after),
        "resume_completes": resume_completes,
        "sweep_removes_leftovers": bool(classify_ok and orphans_after == 0),
        # The journal is append-only, so the furthest boundary can only grow:
        # a lower rank after the resume means the crash site was rewritten.
        "progress_forward": bool(progress_after >= progress_before),
        # -- diagnostics ---------------------------------------------------- #
        "detail": {
            "progress_before": progress_before,
            "progress_after": progress_after,
            "strays_before": strays_before,
            "strays_after": strays_after,
            "jsonl_before": jsonl_before,
            "jsonl_after": jsonl_after,
            "torn_tails_before": torn_before,
            "torn_tails_after": torn_after,
            "journals": journals,
            "orphans_after": orphans_after,
            "orphan_missing": orphan_missing,
            "debris_after": debris_after,
            "resume_exit": resume_code,
            "resume_stderr_tail": resume_tail,
            "master_after_resume": delivered.is_file(),
            "master_bytes_after_resume": (
                delivered.stat().st_size if delivered.is_file() else 0
            ),
            "master_complete_after_resume": mp4_complete(delivered),
        },
    }


def failing_keys(report: Dict[str, Any]) -> List[str]:
    """The invariant names that did not hold."""
    return [name for name in INVARIANT_KEYS if not report.get(name)]
