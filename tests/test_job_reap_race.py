"""Il falso "worker died": un lavoro finito non si dichiara morto.

    python tests/test_job_reap_race.py

Difetto visto sotto carico il 2026-11-06 nella suite: `test_mcp_async` falliva con
`error="worker died"` e progress 22. La causa e' una corsa in `jobs.read_job`:
leggeva lo stato `running`, il worker nel frattempo scriveva `completed` e usciva,
poi il reaper scriveva `failed` sulla copia vecchia, cancellando l'esito vero.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import jobs  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def write_record(root: Path, job_id: str, record: dict) -> None:
    base = jobs.jobs_root(root)
    folder = base / job_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / jobs.JOB_FILE).write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )


def test_completed_just_before_reap_wins() -> None:
    print("\n1. il worker chiude fra la lettura e il controllo del pid")
    with tempfile.TemporaryDirectory(prefix="vx-reap-") as tmp:
        root = Path(tmp)
        job_id = "a" * 32
        write_record(
            root,
            job_id,
            {"job_id": job_id, "status": "running", "pid": 424242, "progress_percent": 22.0},
        )
        original = jobs.is_alive

        def racy(pid):
            # Il worker finisce **proprio ora**: scrive completed e muore.
            write_record(
                root,
                job_id,
                {
                    "job_id": job_id,
                    "status": "completed",
                    "pid": pid,
                    "progress_percent": 100.0,
                    "result": {"ok": True},
                },
            )
            return False

        jobs.is_alive = racy  # type: ignore[assignment]
        try:
            record = jobs.read_job(job_id, root=root)
        finally:
            jobs.is_alive = original  # type: ignore[assignment]
        check(
            "lo stato resta completed",
            record is not None and record.get("status") == "completed",
            (record or {}).get("status"),
        )
        check(
            "nessun 'worker died' inventato",
            record is not None and not record.get("error"),
            (record or {}).get("error"),
        )
        check(
            "il progresso finale resta 100",
            record is not None and record.get("progress_percent") == 100.0,
            (record or {}).get("progress_percent"),
        )


def test_real_death_still_reaped() -> None:
    print("\n2. un worker davvero morto viene marcato failed")
    with tempfile.TemporaryDirectory(prefix="vx-reap-") as tmp:
        root = Path(tmp)
        job_id = "b" * 32
        write_record(
            root,
            job_id,
            {"job_id": job_id, "status": "running", "pid": 424242, "progress_percent": 22.0},
        )
        original = jobs.is_alive
        jobs.is_alive = lambda pid: False  # type: ignore[assignment]
        try:
            record = jobs.read_job(job_id, root=root)
        finally:
            jobs.is_alive = original  # type: ignore[assignment]
        check("lo stato diventa failed", (record or {}).get("status") == "failed", (record or {}).get("status"))
        check("con il motivo in chiaro", (record or {}).get("error") == "worker died", (record or {}).get("error"))


def test_update_does_not_rewind_a_finished_job() -> None:
    print("\n3. un aggiornamento non riporta indietro un lavoro finito")
    with tempfile.TemporaryDirectory(prefix="vx-reap-") as tmp:
        root = Path(tmp)
        job_id = "c" * 32
        write_record(
            root,
            job_id,
            {"job_id": job_id, "status": "completed", "progress_percent": 100.0},
        )
        stale = {"job_id": job_id, "status": "running", "progress_percent": 22.0}
        raised = None
        try:
            jobs._apply_update(jobs.jobs_root(root), job_id, stale, {"progress_percent": 40.0})
        except Exception as exc:  # noqa: BLE001 - la transizione illegale e' il punto
            raised = exc
        after = jobs.read_job(job_id, root=root, reap=False)
        check(
            "lo stato resta completed",
            (after or {}).get("status") == "completed",
            (after or {}).get("status"),
        )
        check(
            "e il progresso non torna indietro",
            (after or {}).get("progress_percent") == 100.0,
            (after or {}).get("progress_percent"),
        )


def main() -> int:
    test_completed_just_before_reap_wins()
    test_real_death_still_reaped()
    test_update_does_not_rewind_a_finished_job()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
