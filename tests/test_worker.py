"""Detached worker: one job, one claim, one report; stdout stays empty.

The pool is built at the delivery canvas (1080x1920) for the same reason as
``test_cli_auto``: the triage gate rejects anything that would need more than
1.5x upscale, so a small fixture would prove the gate instead of the worker.

python tests/test_worker.py
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import jobs  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"

W, H = 1080, 1920


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:56} {reason}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _make(path: Path, args: list) -> bool:
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args, str(path)],
        capture_output=True,
    )
    return proc.returncode == 0 and path.is_file() and path.stat().st_size > 0


def _worker(job_id: str, root: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "scripts")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["VX_FFMPEG"] = FFMPEG
    return subprocess.run(
        [sys.executable, "-m", "vx.worker", job_id, str(root)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def _params(pool: Path, out: Path) -> dict:
    return {
        "prompt": "un reel veloce",
        "assets": str(pool),
        "out": str(out),
        "autonomous": True,
    }


def main() -> int:
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="vx-worker-") as raw:
        tmp = Path(raw)
        pool = tmp / "pool"
        pool.mkdir()
        clip = pool / "clip.mp4"
        ok = _make(
            clip,
            [
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={W}x{H}:rate=25",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=330:duration=2",
                "-t",
                "2",
                "-pix_fmt",
                "yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-c:a",
                "aac",
            ],
        )
        if not ok:
            skip("fixture", "ffmpeg non disponibile")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 0

        before = {p.name: _sha256(p) for p in pool.iterdir() if p.is_file()}

        root = tmp / "jobs"
        master = tmp / "master.mp4"
        rec = jobs.create_job(_params(pool, master), root=root)
        job_id = rec["job_id"]

        # -- success ------------------------------------------------------ #
        proc = _worker(job_id, root)
        check("worker rc 0", proc.returncode == 0, proc.stderr[-400:])
        check("worker stdout vuoto", proc.stdout.strip() == "", repr(proc.stdout[:120]))

        done = jobs.read_job(job_id, root=root) or {}
        check("status completed", done.get("status") == "completed", done.get("status"))
        check(
            "progress 100",
            done.get("progress_percent") == 100.0,
            done.get("progress_percent"),
        )
        check("stage done", done.get("stage") == "done", done.get("stage"))

        result = done.get("result") or {}
        master_path = Path(str(result.get("master") or ""))
        check(
            "result.master esiste e non e' vuoto",
            master_path.is_file() and master_path.stat().st_size > 0,
            result.get("master"),
        )
        check(
            "result.master == out",
            master_path.resolve() == master.resolve(),
            (result.get("master"), str(master)),
        )
        check(
            "result.qc.ok",
            (result.get("qc") or {}).get("ok") is True,
            (result.get("qc") or {}).get("failures"),
        )
        check(
            "result.sources_unchanged",
            result.get("sources_unchanged") is True,
            result.get("sources_unchanged"),
        )
        check(
            "result.assets e' un dict",
            isinstance(result.get("assets"), dict),
            type(result.get("assets")).__name__,
        )
        check(
            "result.report esiste",
            Path(str(result.get("report") or "")).is_file(),
            result.get("report"),
        )
        check(
            "result.workdir assoluto",
            Path(str(result.get("workdir") or "")).is_absolute(),
            result.get("workdir"),
        )
        check(
            "ffprogress.log esiste",
            (root / job_id / "ffprogress.log").is_file(),
            root / job_id / "ffprogress.log",
        )

        # -- second run: the claim file wins ------------------------------ #
        again = _worker(job_id, root)
        check("seconda corsa rc 0", again.returncode == 0, again.stderr[-200:])
        check(
            "seconda corsa stdout vuoto",
            again.stdout.strip() == "",
            repr(again.stdout[:120]),
        )
        still = jobs.read_job(job_id, root=root) or {}
        check(
            "record ancora completed",
            still.get("status") == "completed",
            still.get("status"),
        )
        check(
            "stesso master",
            (still.get("result") or {}).get("master") == result.get("master"),
            (still.get("result") or {}).get("master"),
        )

        after = {p.name: _sha256(p) for p in pool.iterdir() if p.is_file()}
        check("sha pool invariato", before == after, sorted(before))

        # -- failure: the pool disappears after the job exists ------------ #
        fail_out = tmp / "fail.mp4"
        fail_rec = jobs.create_job(_params(pool, fail_out), root=root)
        fail_id = fail_rec["job_id"]
        shutil.rmtree(pool)

        failed = _worker(fail_id, root)
        check("failure rc 1", failed.returncode == 1, failed.returncode)
        check(
            "failure stdout vuoto",
            failed.stdout.strip() == "",
            repr(failed.stdout[:120]),
        )
        bad = jobs.read_job(fail_id, root=root) or {}
        check("status failed", bad.get("status") == "failed", bad.get("status"))
        check("error non vuoto", bool(bad.get("error")), bad.get("error"))
        check("result None", bad.get("result") is None, bad.get("result"))
        check(
            "error_detail presente",
            bool(bad.get("error_detail")),
            str(bad.get("error_detail"))[-120:],
        )

    elapsed = time.time() - started
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  ({elapsed:.1f}s)")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
