"""vx.worker — the detached executor for exactly one job.

``python -m vx.worker <job_id> [root]`` runs one record from the job store and
nothing else: it claims the job, marks it ``running``, hands the stored params to
:func:`vx.api.run_auto`, and writes the terminal state back. The pipeline is
never reimplemented here; the worker is a thin, crash-safe shell around the
public API.

Two invariants shape the code:

* **stdout is not a channel.** The process is spawned detached with stdout and
  stderr on ``DEVNULL``, so every diagnostic goes to stderr and to the job
  record. Nothing is ever printed to stdout.
* **progress must never kill a render.** The observer clamps and enforces
  monotonicity in-process, swallows store errors, and the ffmpeg tail is a
  daemon thread that is always stopped and joined before the terminal write.

The ffmpeg tail is the only place that reads a file the pipeline writes: the
first render pass is given ``-progress <job dir>/ffprogress.log``, and the tail
maps ``out_time_us`` onto the 25..88 band so the bar moves during the longest
stage without ever overtaking the stage values auto emits afterwards.
"""

from __future__ import annotations

import json

import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Union

from . import api, contract, jobs

PROGRESS_FILE = "ffprogress.log"

# The tail owns the 25..88 band: auto emits 25 before the render and 92 after it.
TAIL_FLOOR = 25.0
TAIL_SPAN = 63.0
TAIL_CEILING = 88.0
TAIL_POLL_SEC = 0.1
JOIN_TIMEOUT_SEC = 2.0
ERROR_DETAIL_CHARS = 4000

# Exactly the fields of api.AutoRequest, in the order jobs.create_job validates.
_REQUEST_KEYS = (
    "prompt",
    "assets",
    "out",
    "workdir",
    "fps",
    "manifest",
    "autonomous",
    "crf",
    "preset",
)


def _log(message: str) -> None:
    """Diagnostics go to stderr: stdout belongs to nobody in a detached worker."""
    print(message, file=sys.stderr, flush=True)


def _workdir(params: Mapping[str, Any]) -> Path:
    """Mirror auto._resolve_workdir: explicit workdir, else ``<out stem>-auto``."""
    explicit = params.get("workdir")
    if explicit:
        return Path(str(explicit))
    out = Path(str(params.get("out") or ""))
    return out.parent / f"{out.stem}-auto"


def _duration(workdir: Path) -> float:
    """``plan.json`` -> ``timeline.duration_sec``; 0.0 when absent or unusable."""
    try:
        data = json.loads((Path(workdir) / "plan.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0.0
    timeline = data.get("timeline") if isinstance(data, dict) else None
    if not isinstance(timeline, dict):
        return 0.0
    try:
        value = float(timeline.get("duration_sec") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0.0 else 0.0


class _Progress:
    """Clamped, monotonic progress writes plus the one ffmpeg tail thread."""

    def __init__(self, job_id: str, base: Path, workdir: Path) -> None:
        self._job_id = job_id
        self._base = base
        self._workdir = workdir
        self._lock = threading.Lock()
        self._last = 0.0
        self._stop = threading.Event()
        self._tail_lock = threading.Lock()
        self._tail: Optional[threading.Thread] = None
        self._closed = False

    # -- observer ------------------------------------------------------- #

    def observe(self, percent: float, stage: str) -> None:
        """The callback auto calls at 5/20/25/92/96/100."""
        self.write(percent, stage)
        if stage == "render":
            self._start_tail()

    def write(self, percent: float, stage: str) -> None:
        """Clamp to 0..100, drop anything lower than the last value, persist."""
        try:
            value = float(percent)
        except (TypeError, ValueError):
            return
        value = contract.clamp(value, 0.0, 100.0)
        with self._lock:
            if self._closed:
                return
            if value < self._last:
                return
            self._last = value
            try:
                jobs.update_job(
                    self._job_id,
                    {"progress_percent": value, "stage": stage},
                    root=self._base,
                )
            except (contract.VxError, OSError):
                # A reaped record or a transient write error must not stop the
                # render: the terminal write is the one that has to succeed.
                pass

    # -- tail lifecycle ------------------------------------------------- #

    def _start_tail(self) -> None:
        with self._tail_lock:
            if self._tail is not None or self._stop.is_set():
                return
            thread = threading.Thread(
                target=self._tail_loop,
                name=f"vx-progress-{self._job_id}",
                daemon=True,
            )
            self._tail = thread
            thread.start()

    def stop(self) -> None:
        """Signal the tail and join it; safe to call more than once."""
        with self._lock:
            self._closed = True
        self._stop.set()
        with self._tail_lock:
            thread = self._tail
        if thread is not None and thread.is_alive():
            thread.join(timeout=JOIN_TIMEOUT_SEC)

    # -- tail body ------------------------------------------------------ #

    def _tail_loop(self) -> None:
        path = self._base / self._job_id / PROGRESS_FILE
        duration = _duration(self._workdir)
        offset = 0
        pending = ""
        while not self._stop.is_set():
            if duration <= 0.0:
                # No timeline to map onto: never divide by zero, write nothing.
                self._stop.wait(TAIL_POLL_SEC)
                continue
            try:
                with path.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                    offset = handle.tell()
            except OSError:
                self._stop.wait(TAIL_POLL_SEC)
                continue
            if chunk:
                pending += chunk.decode("utf-8", "replace")
                lines = pending.split("\n")
                pending = lines.pop()  # keep the incomplete last line
                for line in lines:
                    self._consume(line, duration)
            self._stop.wait(TAIL_POLL_SEC)

    def _consume(self, line: str, duration: float) -> None:
        text = line.strip()
        if not text.startswith("out_time_us="):
            return
        try:
            micros = int(text.split("=", 1)[1])
        except ValueError:
            return
        seconds = micros / 1_000_000.0
        percent = TAIL_FLOOR + TAIL_SPAN * min(1.0, seconds / duration)
        # Cap before the helper: the tail must never reach the 92/96/100 stages.
        self.write(min(percent, TAIL_CEILING), "render")


def spawn(job_id: str, root: Optional[Union[str, Path]] = None) -> int:
    """Start one detached worker for ``job_id`` and return its pid.

    The child is ``python -m vx.worker <job_id> <base>`` with its cwd inside the
    job directory, so a relative path in the record cannot be re-interpreted
    against the caller's cwd. ``<repo>/scripts`` is prepended to ``PYTHONPATH``
    because the test environment has no install. Detachment is per-platform and
    mutually exclusive: ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` on
    Windows, ``start_new_session`` elsewhere. Never ``shell``, never
    ``preexec_fn``. A ``Popen`` failure propagates to the caller.
    """
    if not isinstance(job_id, str) or not jobs.JOB_ID_RE.fullmatch(job_id):
        raise contract.VxError(f"invalid job id: {job_id!r}")
    base = jobs.jobs_root(root)
    workdir = base / job_id
    workdir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    scripts = str(Path(__file__).resolve().parents[1])
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = scripts + (os.pathsep + existing if existing else "")

    flags: Dict[str, Any] = {}
    if os.name == "nt":
        flags["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        flags["start_new_session"] = True

    proc = subprocess.Popen(
        [sys.executable, "-m", "vx.worker", job_id, str(base)],
        cwd=str(workdir),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **flags,
    )
    return proc.pid


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        _log("usage: python -m vx.worker <job_id> [root]")
        return 2
    job_id = args[0]
    root = args[1] if len(args) > 1 else None

    try:
        base = jobs.jobs_root(root)
    except OSError as exc:
        _log(f"worker: jobs root unavailable: {exc}")
        return 2

    try:
        record = jobs.read_job(job_id, root=base, reap=False)
    except contract.VxError as exc:
        _log(f"worker: {exc}")
        return 2
    if record is None:
        _log(f"worker: unknown job {job_id}")
        return 2

    try:
        claimed = jobs.claim_job(job_id, root=base)
    except contract.VxError as exc:
        _log(f"worker: {exc}")
        return 2
    if not claimed:
        # Another worker owns this job: leave the record exactly as it is.
        return 0

    try:
        jobs.update_job(
            job_id,
            {
                "status": "running",
                "pid": os.getpid(),
                "stage": "running",
                "progress_percent": 2.0,
            },
            root=base,
        )
    except contract.VxError as exc:
        _log(f"worker: {exc}")
        return 2

    params = record.get("params")
    if not isinstance(params, dict):
        params = {}
    workdir = _workdir(params)
    progress = _Progress(job_id, base, workdir)

    failure: Optional[Exception] = None
    detail = ""
    report: Dict[str, Any] = {}
    try:
        try:
            request = api.AutoRequest(
                **{key: params[key] for key in _REQUEST_KEYS if key in params}
            )
            report = api.run_auto(
                request,
                progress=progress.observe,
                progress_file=str(base / job_id / PROGRESS_FILE),
            )
        except Exception as exc:  # noqa: BLE001
            failure = exc
            detail = traceback.format_exc()[-ERROR_DETAIL_CHARS:]
    finally:
        # Always stop and join, success or failure, before the terminal write.
        progress.stop()

    if failure is not None:
        message = f"{type(failure).__name__}: {failure}"
        try:
            jobs.update_job(
                job_id,
                {"status": "failed", "error": message, "error_detail": detail},
                root=base,
            )
        except contract.VxError as exc:
            _log(f"worker: cannot record failure for {job_id}: {exc}")
        _log(f"worker: job {job_id} failed: {message}")
        return 1

    if not isinstance(report, dict):
        report = {}
    result = {
        "master": str(Path(str(params.get("out") or "")).resolve()),
        "workdir": str(workdir.resolve()),
        "report": str((workdir / "auto_report.json").resolve()),
        "qc": report.get("qc"),
        "assets": report.get("assets"),
        "sources_unchanged": report.get("sources_unchanged"),
    }
    try:
        jobs.update_job(
            job_id,
            {
                "status": "completed",
                "progress_percent": 100.0,
                "stage": "done",
                "result": result,
            },
            root=base,
        )
    except contract.VxError as exc:
        _log(f"worker: cannot record completion for {job_id}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
