"""File-based job store: atomic records, guarded ids, monotonic progress, reaping.

Every case owns its own temporary root, so the real work root is never touched.

python tests/test_jobs.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import contract, jobs  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def _params(tmp: Path) -> dict:
    assets = tmp / "assets"
    assets.mkdir(exist_ok=True)
    return {"prompt": "cut this", "assets": str(assets), "out": str(tmp / "out")}


def _raises(fn) -> bool:
    try:
        fn()
    except contract.VxError:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def main() -> int:
    # -- create ---------------------------------------------------------- #
    with tempfile.TemporaryDirectory(prefix="vx-jobs-") as raw:
        tmp = Path(raw)
        root = tmp / "jobs"
        rec = jobs.create_job(_params(tmp), root=root)
        check("create status queued", rec["status"] == "queued", rec["status"])
        check(
            "create progress 0",
            rec["progress_percent"] == 0.0,
            rec["progress_percent"],
        )
        check(
            "create id 32 hex",
            jobs.JOB_ID_RE.fullmatch(rec["job_id"]) is not None,
            rec["job_id"],
        )
        check(
            "create params echoed",
            rec["params"]["prompt"] == "cut this",
            rec["params"],
        )
        check(
            "job.json exists",
            (root / rec["job_id"] / "job.json").is_file(),
        )
        leftovers = list(root.rglob("*.tmp"))
        check("no .tmp left", leftovers == [], leftovers)

    # -- validation ------------------------------------------------------ #
    with tempfile.TemporaryDirectory(prefix="vx-jobs-") as raw:
        tmp = Path(raw)
        root = tmp / "jobs"
        base = _params(tmp)

        missing = dict(base)
        missing.pop("prompt")
        check(
            "missing prompt -> VxError",
            _raises(lambda: jobs.create_job(missing, root=root)),
        )

        empty = dict(base)
        empty["out"] = ""
        check(
            "empty out -> VxError",
            _raises(lambda: jobs.create_job(empty, root=root)),
        )

        unknown = dict(base)
        unknown["bogus"] = 1
        check(
            "unknown key -> VxError",
            _raises(lambda: jobs.create_job(unknown, root=root)),
        )

        ghost = dict(base)
        ghost["assets"] = str(tmp / "nope")
        check(
            "missing assets dir -> VxError",
            _raises(lambda: jobs.create_job(ghost, root=root)),
        )

    # -- read unknown ---------------------------------------------------- #
    with tempfile.TemporaryDirectory(prefix="vx-jobs-") as raw:
        tmp = Path(raw)
        root = tmp / "jobs"
        check(
            "read unknown -> None",
            jobs.read_job("0" * 32, root=root) is None,
        )

    # -- transitions ----------------------------------------------------- #
    with tempfile.TemporaryDirectory(prefix="vx-jobs-") as raw:
        tmp = Path(raw)
        root = tmp / "jobs"
        rec = jobs.create_job(_params(tmp), root=root)
        jid = rec["job_id"]
        jobs.update_job(jid, {"status": "running"}, root=root)
        jobs.update_job(jid, {"status": "completed"}, root=root)
        check(
            "queued->running->completed",
            jobs.read_job(jid, root=root)["status"] == "completed",
        )
        check(
            "completed->running raises",
            _raises(lambda: jobs.update_job(jid, {"status": "running"}, root=root)),
        )

        rec2 = jobs.create_job(_params(tmp), root=root)
        jid2 = rec2["job_id"]
        jobs.update_job(jid2, {"status": "running"}, root=root)
        check(
            "running->queued raises",
            _raises(lambda: jobs.update_job(jid2, {"status": "queued"}, root=root)),
        )

    # -- progress monotonic ---------------------------------------------- #
    with tempfile.TemporaryDirectory(prefix="vx-jobs-") as raw:
        tmp = Path(raw)
        root = tmp / "jobs"
        rec = jobs.create_job(_params(tmp), root=root)
        jid = rec["job_id"]
        jobs.update_job(jid, {"progress_percent": 50}, root=root)
        jobs.update_job(jid, {"progress_percent": 30}, root=root)
        check(
            "progress never decreases",
            jobs.read_job(jid, root=root)["progress_percent"] == 50.0,
            jobs.read_job(jid, root=root)["progress_percent"],
        )
        jobs.update_job(jid, {"progress_percent": 120}, root=root)
        check(
            "progress clamps high",
            jobs.read_job(jid, root=root)["progress_percent"] == 100.0,
            jobs.read_job(jid, root=root)["progress_percent"],
        )
        jobs.update_job(jid, {"progress_percent": -5}, root=root)
        check(
            "progress clamps low keeps previous",
            jobs.read_job(jid, root=root)["progress_percent"] == 100.0,
            jobs.read_job(jid, root=root)["progress_percent"],
        )

    # -- path traversal guard -------------------------------------------- #
    with tempfile.TemporaryDirectory(prefix="vx-jobs-") as raw:
        tmp = Path(raw)
        root = tmp / "jobs"
        for bad in ("../evil", "A" * 32, "0" * 31):
            check(
                f"read {bad!r} -> VxError",
                _raises(lambda bad=bad: jobs.read_job(bad, root=root)),
            )
            check(
                f"update {bad!r} -> VxError",
                _raises(lambda bad=bad: jobs.update_job(bad, {}, root=root)),
            )
            check(
                f"claim {bad!r} -> VxError",
                _raises(lambda bad=bad: jobs.claim_job(bad, root=root)),
            )

    # -- claim ----------------------------------------------------------- #
    with tempfile.TemporaryDirectory(prefix="vx-jobs-") as raw:
        tmp = Path(raw)
        root = tmp / "jobs"
        rec = jobs.create_job(_params(tmp), root=root)
        jid = rec["job_id"]
        check("claim first True", jobs.claim_job(jid, root=root) is True)
        check("claim second False", jobs.claim_job(jid, root=root) is False)

    # -- root isolation -------------------------------------------------- #
    with tempfile.TemporaryDirectory(prefix="vx-jobs-") as raw:
        tmp = Path(raw)
        root_a = tmp / "a"
        root_b = tmp / "b"
        rec = jobs.create_job(_params(tmp), root=root_a)
        jid = rec["job_id"]
        check("root A sees job", jobs.read_job(jid, root=root_a) is not None)
        check("root B does not", jobs.read_job(jid, root=root_b) is None)
        check("list A has 1", len(jobs.list_jobs(root=root_a)) == 1)
        check("list B empty", jobs.list_jobs(root=root_b) == [])

    # -- reaping --------------------------------------------------------- #
    with tempfile.TemporaryDirectory(prefix="vx-jobs-") as raw:
        tmp = Path(raw)
        root = tmp / "jobs"
        rec = jobs.create_job(_params(tmp), root=root)
        jid = rec["job_id"]
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        jobs.update_job(jid, {"status": "running", "pid": proc.pid}, root=root)
        reaped = jobs.read_job(jid, root=root)
        check("reap marks failed", reaped["status"] == "failed", reaped["status"])
        check(
            "reap error worker died",
            reaped["error"] == "worker died",
            reaped["error"],
        )

        rec2 = jobs.create_job(_params(tmp), root=root)
        fresh = jobs.read_job(rec2["job_id"], root=root, reap=False)
        check(
            "reap=False leaves queued",
            fresh["status"] == "queued",
            fresh["status"],
        )

    # -- liveness -------------------------------------------------------- #
    check("is_alive(None) False", jobs.is_alive(None) is False)
    check("is_alive(self) True", jobs.is_alive(os.getpid()) is True)

    # -- timestamps ------------------------------------------------------ #
    with tempfile.TemporaryDirectory(prefix="vx-jobs-") as raw:
        tmp = Path(raw)
        root = tmp / "jobs"
        rec = jobs.create_job(_params(tmp), root=root)
        jid = rec["job_id"]
        check(
            "updated_at >= created_at",
            rec["updated_at"] >= rec["created_at"],
            (rec["created_at"], rec["updated_at"]),
        )
        check("started_at None initially", rec["started_at"] is None)
        jobs.update_job(jid, {"status": "running"}, root=root)
        after = jobs.read_job(jid, root=root)
        check(
            "started_at set on running",
            after["started_at"] is not None,
            after["started_at"],
        )
        check(
            "started_at >= created_at",
            after["started_at"] >= after["created_at"],
        )

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        root = tmp / "jobs"
        rec = jobs.create_job(_params(tmp), root=root)
        jid = rec["job_id"]
        handle = (root / jid / "job.json").open("r", encoding="utf-8")

        def _close_later() -> None:
            time.sleep(0.3)
            handle.close()

        closer = threading.Thread(target=_close_later)
        closer.start()
        try:
            jobs.update_job(jid, {"status": "running"}, root=root)
            survived = True
            detail: object = ""
        except Exception as exc:  # noqa: BLE001 - the test records the failure
            survived = False
            detail = f"{type(exc).__name__}: {exc}"
        finally:
            closer.join()
            if not handle.closed:
                handle.close()
        check("scrittura robusta a un lettore aperto", survived, detail)
        check(
            "stato corretto dopo il retry",
            jobs.read_job(jid, root=root)["status"] == "running",
        )

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        root = tmp / "jobs"
        rec = jobs.create_job(_params(tmp), root=root)
        jid = rec["job_id"]
        check("claim preso per il job stantio", jobs.claim_job(jid, root=root) is True)
        path = root / jid / "job.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["updated_at"] = time.time() - (jobs.QUEUED_STALE_SEC + 1)
        path.write_text(json.dumps(data), encoding="utf-8")
        stale = jobs.read_job(jid, root=root)
        check(
            "queued stantio con claim -> failed",
            stale["status"] == "failed" and stale["error"] == "worker never started",
            (stale.get("status"), stale.get("error")),
        )

        rec2 = jobs.create_job(_params(tmp), root=root)
        path2 = root / rec2["job_id"] / "job.json"
        data2 = json.loads(path2.read_text(encoding="utf-8"))
        data2["updated_at"] = time.time() - (jobs.QUEUED_STALE_SEC + 1)
        path2.write_text(json.dumps(data2), encoding="utf-8")
        stale2 = jobs.read_job(rec2["job_id"], root=root)
        check(
            "queued stantio senza claim -> failed",
            stale2["status"] == "failed" and stale2["error"] == "worker never started",
            (stale2.get("status"), stale2.get("error")),
        )

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        root = tmp / "jobs"
        for label, key, value in (
            ("fps stringa", "fps", "25"),
            ("crf stringa", "crf", "17"),
            ("autonomous non booleano", "autonomous", "yes"),
        ):
            params = _params(tmp)
            params[key] = value
            try:
                jobs.create_job(params, root=root)
            except contract.VxError:
                rejected = True
            else:
                rejected = False
            check(f"parametro tipizzato: {label}", rejected, value)

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
