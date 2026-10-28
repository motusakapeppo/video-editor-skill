"""Crash harness for F17-B: a real ``vx auto`` run killed from the outside.

The child is the real CLI.  The parent never imports the pipeline: it builds a
tiny corpus, spawns ``vx.cli auto`` in its own session, waits a seeded delay,
and kills the whole process tree with ``taskkill /F /T`` (POSIX:
``killpg(SIGKILL)``).  What the killed run left on disk is then checked against
the invariants a crash-safe pipeline owes its caller, and the same workdir is
resumed to completion.

Why the invocation differs from ``tests/test_cli_auto.py``
-----------------------------------------------------------
``test_cli_auto.py`` builds its pool at the delivery canvas (1080x1920) because
the triage gate rejects any source that would need more than 1.5x upscale.
That is the right fixture for an e2e proof and the wrong one here: the same
pool takes ~11 s per run, and 50 kills plus 50 resumes would blow the 600 s
ceiling.  This harness keeps the 9:16 shape, shrinks the frame to 320x568 (a
3.4x upscale from the canvas) and raises ``triage_max_upscale`` to 4 so the
gate still decides on a real rule instead of being bypassed by a fixture that
already matches the canvas.  ``--no-bridge`` removes the analysis pass before
assembly; every other stage - triage, ingest, staging, plan, render, loudness,
stage cache, master write, QC, deliverables, report - still runs.  One run is
~3.4 s on the reference machine.

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

    <workdir>/.lock                          run lock (stale after a kill)
    <workdir>/events.jsonl                   vx.events append log
    <workdir>/plan.json                      the payload plan
    <workdir>/auto_report.json               the final report
    <workdir>/bridge_report.json             bridge analysis (not with --no-bridge)
    <workdir>/render_history.jsonl           one line per render
    <workdir>/stage_cache_report.json        cache statistics
    <workdir>/render/.vx-writes.jsonl        the write-ahead journal
    <workdir>/render/04_loudness.txt         loudnorm measurement
    <workdir>/render/edl_graph.txt           filtergraph text
    <workdir>/render/edl_render.json         render metadata
    <workdir>/render/final_pre_loudness.mp4  pre-master render
    <workdir>/render/stage_cache/<key>.json  content-addressed cache entries
    <workdir>/out/{master.mp4,thumbnail.jpg,contact_sheet.jpg,subtitles.srt}
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
}
ALLOWED_TOP_DIRS = {"out", "render", "quarantine", ".quarantine", "checkpoints", "cache"}
ALLOWED_TOP_DIR_PREFIXES = ("stage_",)
ALLOWED_RENDER_FILES = {
    ".vx-writes.jsonl",
    "04_loudness.txt",
    "edl_graph.txt",
    "edl_render.json",
    "final_pre_loudness.mp4",
    "stage_cache_report.json",
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
def delay_plan(seed: int, count: int) -> List[int]:
    """``count`` delays in ``[DELAY_MIN_MS, DELAY_MAX_MS]`` from one seed.

    Stratified, not plain uniform: the range is cut into ``count`` equal bands
    and each band contributes one draw, so the kills really cover the whole
    pipeline instead of clustering the way a uniform sample of 50 does.  The
    jitter inside a band keeps the sequence seed-dependent.
    """
    rng = random.Random(seed)
    span = (DELAY_MAX_MS - DELAY_MIN_MS) / float(count)
    delays: List[int] = []
    for index in range(count):
        low = DELAY_MIN_MS + index * span
        delays.append(int(round(low + rng.uniform(0.0, span))))
    return [min(DELAY_MAX_MS, max(DELAY_MIN_MS, value)) for value in delays]


# --------------------------------------------------------------------------- #
# reading the crash site
# --------------------------------------------------------------------------- #
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
        ("lock", (".lock",)),
        ("events", ("events.jsonl",)),
    )
    for label, relatives in markers:
        for relative in relatives:
            if (base / Path(*relative.split("/"))).exists():
                return label
    return "boot"


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
def _env() -> Dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SCRIPTS)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if FFMPEG:
        env["VX_FFMPEG"] = FFMPEG
    return env


def auto_argv(pool: str | Path, workdir: str | Path) -> List[str]:
    """The exact child command line; the resume run uses the same list."""
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
        "--no-bridge",
    ]
    for override in OVERRIDES:
        argv.extend(["--override", override])
    argv.append("--json")
    return argv


def master_path(workdir: str | Path) -> Path:
    return Path(workdir).parent / MASTER_NAME


def delivered_path(workdir: str | Path) -> Path:
    """The delivered master inside the workdir (written after loudness + QC)."""
    return Path(workdir) / "out" / MASTER_NAME


def spawn_auto(pool: str | Path, workdir: str | Path):
    """Spawn the real CLI in its own session; return ``(proc, out, argv)``.

    ``start_new_session`` is what makes the kill a tree kill: on POSIX the
    child gets its own process group, on Windows ``taskkill /T`` walks the
    child tree.  ``stdout=PIPE`` is drained by a thread: the run prints a JSON
    report, and a full pipe buffer would otherwise block the child and make a
    deadlock look like a slow run.
    """
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    (work / "render").mkdir(exist_ok=True)
    # Documented opt-in: the journal is on when the file exists.
    artifacts.enable(work / "render")
    argv = auto_argv(pool, work)
    proc = subprocess.Popen(
        argv,
        cwd=str(ROOT),
        env=_env(),
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


def run_kill(
    seed: int,
    index: int,
    base_dir: Optional[str | Path] = None,
    *,
    delay_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Spawn, kill after the delay, and report what the kill left behind."""
    if delay_ms is None:
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
    time.sleep(max(0.0, float(delay_ms) / 1000.0))
    exit_code = kill_tree(proc)
    killed = exit_code != 0 or bool(getattr(proc, "vx_kill_ok", False))
    report_path = Path(workdir) / "auto_report.json"
    finished = exit_code == 0 or report_path.is_file()
    delivered = delivered_path(workdir)
    return {
        "seed": seed,
        "index": index,
        "delay_ms": int(delay_ms),
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


def _resume(pool: Path, workdir: Path, timeout: float):
    argv = auto_argv(pool, workdir)
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
) -> Dict[str, Any]:
    """The five invariants a crash-safe auto run owes, plus diagnostics.

    Order matters: the kill-state checks are taken before the resume, because
    the resume is allowed to heal what the crash damaged, and the post-resume
    checks are taken after, because a resume that leaves debris behind is a
    failure.
    """
    pool = Path(pool)
    work = Path(workdir)
    delivered = delivered_path(work)

    sources_ok = True
    if baseline is not None:
        sources_ok = sha_map(pool) == baseline

    readable_before, torn_before, jsonl_before = _jsonl_state(work)
    strays_before = _strays(work)

    resume_code, resume_tail = _resume(pool, work, timeout)

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
        # -- diagnostics ---------------------------------------------------- #
        "detail": {
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
