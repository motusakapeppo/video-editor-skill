"""Async MCP jobs: a submitted job outlives the server and a new session reads it.

The proof has three parts. Session 1 submits a job and returns before the render
can finish, so the master cannot exist yet. The detached worker keeps going after
the server exits on EOF. Session 2, a brand new process, reads the terminal
record from the same job store.

The child environment pins the work root inside the temporary directory twice:
``VX_WORK`` is the documented switch, and ``VX_CONFIG`` points at a config file
without a ``work_root`` because a machine-local ``vx.config.json`` would
otherwise win over the environment and send the job to the real work root.

python tests/test_mcp_async.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import config, jobs  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"

W, H = 1080, 1920
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SUBMIT_BUDGET_SEC = 15.0
POLL_TIMEOUT_SEC = 240.0
POLL_INTERVAL_SEC = 0.5


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


def _request(method: str, request_id: object = None, params: object = None) -> dict:
    message = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    return message


def _call(name: str, arguments: dict, request_id: object) -> dict:
    return _request("tools/call", request_id, {"name": name, "arguments": arguments})


def _result(response: object) -> dict:
    if not isinstance(response, dict):
        return {}
    value = response.get("result")
    return value if isinstance(value, dict) else {}


def _text(response: object) -> str:
    content = _result(response).get("content") or []
    if not content or not isinstance(content[0], dict):
        return ""
    return str(content[0].get("text", ""))


def _payload(response: object) -> dict:
    try:
        value = json.loads(_text(response))
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _child_env(tmp: Path) -> dict:
    """The environment every MCP session (and its detached worker) inherits."""
    env = os.environ.copy()
    scripts = str(ROOT / "scripts")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = scripts + (os.pathsep + existing if existing else "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["VX_FFMPEG"] = FFMPEG
    env["VX_WORK"] = str(tmp)
    # config.load() lets a config file win over the environment. A machine-local
    # vx.config.json would therefore ignore VX_WORK and use the real work root,
    # so the child gets a config without work_root and VX_WORK takes effect.
    config_path = tmp / "vx.config.json"
    config_path.write_text(json.dumps({"proxy_width": 640}), encoding="utf-8")
    env["VX_CONFIG"] = str(config_path)
    return env


def _session(env: dict, requests: list) -> tuple:
    payload = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in requests)
    started = time.time()
    proc = subprocess.run(
        [sys.executable, "-m", "vx.mcp_server"],
        cwd=str(ROOT),
        env=env,
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    return proc, time.time() - started


def _responses(proc: subprocess.CompletedProcess) -> tuple:
    raw_lines = [line for line in proc.stdout.splitlines() if line.strip()]
    parsed = []
    parsed_ok = True
    for line in raw_lines:
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            parsed_ok = False
    by_id = {}
    for response in parsed:
        if isinstance(response, dict) and "id" in response:
            by_id[response["id"]] = response
    return raw_lines, parsed, by_id, parsed_ok


def _poll(job_id: str, root: Path) -> tuple:
    samples = []
    started = time.time()
    last = None
    while time.time() - started < POLL_TIMEOUT_SEC:
        record = jobs.read_job(job_id, root=root)
        elapsed = round(time.time() - started, 2)
        if record is None:
            samples.append((elapsed, None, None, None))
        else:
            last = record
            samples.append(
                (
                    elapsed,
                    record.get("progress_percent"),
                    record.get("status"),
                    record.get("stage"),
                )
            )
            if record.get("status") in ("completed", "failed"):
                return record, samples, True
        time.sleep(POLL_INTERVAL_SEC)
    return last, samples, False


def main() -> int:
    started = time.time()
    with tempfile.TemporaryDirectory(
        prefix="vx-mcp-async-", ignore_cleanup_errors=True
    ) as raw:
        tmp = Path(raw)
        pool = tmp / "pool"
        pool.mkdir()
        clip = pool / "clip.mp4"
        master = tmp / "master.mp4"
        root = tmp / "jobs"

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
                "sine=frequency=330:duration=6",
                "-t",
                "6",
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

        before_sha = _sha256(clip)
        env = _child_env(tmp)

        # -- session 1: submit and return before the render can finish ----- #
        requests = [
            _request("initialize", 1, {"protocolVersion": "2025-06-18"}),
            _request("notifications/initialized"),
            _call(
                "vx_job_submit",
                {
                    "prompt": "un reel veloce",
                    "assets": str(pool.resolve()),
                    "out": str(master.resolve()),
                    "autonomous": True,
                },
                2,
            ),
        ]
        proc1, elapsed1 = _session(env, requests)
        raw1, parsed1, by_id1, parsed_ok1 = _responses(proc1)

        check("session 1 rc 0", proc1.returncode == 0, proc1.returncode)
        check(
            "session 1 stdout: ogni riga e' JSON",
            parsed_ok1 and len(raw1) == len(parsed1),
            (len(raw1), len(parsed1)),
        )
        check(
            "session 1 stdout: solo risposte JSON",
            len(parsed1) == 2,
            [item.get("id") for item in parsed1 if isinstance(item, dict)],
        )

        submit = _result(by_id1.get(2))
        check(
            "submit isError false",
            submit.get("isError") is False,
            submit.get("isError"),
        )
        payload = _payload(by_id1.get(2))
        job_id = payload.get("job_id")
        check(
            "submit job_id 32 hex",
            isinstance(job_id, str) and JOB_ID_RE.fullmatch(job_id) is not None,
            job_id,
        )
        check(
            "submit status queued",
            payload.get("status") == "queued",
            payload.get("status"),
        )
        pid = payload.get("pid")
        check(
            "submit pid intero > 0",
            isinstance(pid, int) and not isinstance(pid, bool) and pid > 0,
            pid,
        )
        state = payload.get("state")
        check(
            "submit state esiste",
            isinstance(state, str) and Path(state).is_file(),
            state,
        )
        check(
            "submit wall time < 15s",
            elapsed1 < SUBMIT_BUDGET_SEC,
            f"{elapsed1:.2f}s",
        )
        check(
            "master non esiste al ritorno di session 1",
            not master.exists(),
            master,
        )

        if not isinstance(job_id, str) or JOB_ID_RE.fullmatch(job_id) is None:
            print("\njob_id assente: impossibile proseguire")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 1

        # -- poll the detached worker -------------------------------------- #
        final, samples, terminal = _poll(job_id, root)
        print("\n  samples (t, progress, status, stage):")
        for sample in samples:
            print(f"    {sample}")

        final = final or {}
        failure_detail = ""
        if final.get("status") == "failed":
            failure_detail = (
                f"error={final.get('error')!r} "
                f"error_detail={str(final.get('error_detail'))[-400:]!r}"
            )
        check(
            "job terminale entro il timeout",
            terminal,
            failure_detail or final.get("status"),
        )
        check(
            "status completed",
            final.get("status") == "completed",
            failure_detail or final.get("status"),
        )
        check(
            "progress finale 100",
            final.get("progress_percent") == 100.0,
            final.get("progress_percent"),
        )
        check("stage done", final.get("stage") == "done", final.get("stage"))

        numeric = [
            float(sample[1])
            for sample in samples
            if isinstance(sample[1], (int, float)) and not isinstance(sample[1], bool)
        ]
        check(
            "almeno un campione intermedio 0 < p < 100",
            any(0.0 < value < 100.0 for value in numeric),
            numeric,
        )
        check(
            "campioni non decrescenti",
            all(later >= earlier for earlier, later in zip(numeric, numeric[1:])),
            numeric,
        )

        result = final.get("result") or {}
        check(
            "result.master == out risolto",
            result.get("master") == str(master.resolve()),
            (result.get("master"), str(master.resolve())),
        )
        check(
            "master esiste e non e' vuoto",
            master.is_file() and master.stat().st_size > 0,
            master.stat().st_size if master.is_file() else "assente",
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
            "result.report esiste",
            Path(str(result.get("report") or "")).is_file(),
            result.get("report"),
        )
        check("sha clip invariato", _sha256(clip) == before_sha, before_sha[:12])

        # -- session 2: a new process reads the durable record ------------- #
        # subprocess.run only returns once the child has exited, so a non-None
        # returncode is the proof that session 1's server is gone.
        check(
            "server di session 1 terminato",
            proc1.returncode is not None,
            proc1.returncode,
        )

        requests2 = [
            _request("initialize", 1, {"protocolVersion": "2025-06-18"}),
            _request("notifications/initialized"),
            _call("vx_job_status", {"job_id": job_id}, 2),
        ]
        proc2, elapsed2 = _session(env, requests2)
        raw2, parsed2, by_id2, parsed_ok2 = _responses(proc2)

        check("session 2 rc 0", proc2.returncode == 0, proc2.returncode)
        check(
            "session 2 stdout: ogni riga e' JSON",
            parsed_ok2 and len(raw2) == len(parsed2),
            (len(raw2), len(parsed2)),
        )
        status_result = _result(by_id2.get(2))
        check(
            "status isError false",
            status_result.get("isError") is False,
            status_result.get("isError"),
        )
        payload2 = _payload(by_id2.get(2))
        check(
            "session 2 status completed",
            payload2.get("status") == "completed",
            payload2.get("status"),
        )
        check(
            "session 2 progress 100",
            payload2.get("progress_percent") == 100.0,
            payload2.get("progress_percent"),
        )
        check(
            "session 2 result.master identico",
            (payload2.get("result") or {}).get("master") == result.get("master"),
            (payload2.get("result") or {}).get("master"),
        )
        check(
            "state sotto <tmp>/jobs",
            isinstance(state, str)
            and Path(state).resolve().is_relative_to(root.resolve()),
            state,
        )
        real_root = config.work_root()
        check(
            "job assente dal work root reale",
            not (real_root / "jobs" / job_id).exists(),
            real_root / "jobs" / job_id,
        )
        print(f"\n  session 2 wall time: {elapsed2:.2f}s")

        print("\n  job record finale:")
        print(json.dumps(final, indent=2, ensure_ascii=False, sort_keys=True))

    elapsed = time.time() - started
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  ({elapsed:.1f}s)")
    print(f"exit code: {0 if FAIL == 0 else 1}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
