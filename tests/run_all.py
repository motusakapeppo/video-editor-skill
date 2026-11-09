"""Cross-platform runner for the script-style suite.

Every ``tests/test_*.py`` is a standalone program: it prints PASS/FAIL lines,
a final ``N PASS / M FAIL / K SKIP`` summary, and exits non-zero on failure.
This runner discovers them, gives each one a clean environment, and returns a
single exit code that CI can trust.

Why a runner instead of a shell loop: the suite must run identically on
Windows, Linux and macOS. A PowerShell loop fails on macOS, a bash loop fails
on cmd, and neither forces UTF-8 on the child. Here the environment is set in
Python, the child's bytes are decoded with BOM detection, and the timeout is
enforced by the parent.

Why the default is parallel, and why a red gets one rerun alone:

Measured on this machine (16 cores): the whole suite takes ~1418 s with
``--jobs 1``, while 14 files finish in ~20 s with ``--jobs 6``. Serially the
wall clock, not the assertions, is the bottleneck: each file is an independent
program with its own clean environment, so overlapping them changes nothing
about what is proven. The default is therefore ``--jobs 0`` = automatic,
``max(2, min(6, (os.cpu_count() or 4) - 2))``: two cores stay free for the
parent and for the developer's own work, and beyond six the golden tests (which
compare rendered bytes) start to starve. ``--jobs 1`` remains the way to force
the serial run.

The rerun exists because a red under load is not automatically a defect: golden
tests compare bytes of rendered frames, and six children competing for the same
cores can make a frame late. After the parallel pass every FAIL is rerun once,
**alone**. Passing alone makes it FLAKY: visible, named in the summary, and it
does not touch the exit code. Failing alone too keeps it FAIL and the exit code
1. That distinguishes a saturated machine from a real regression without
silencing, skipping or shortening a single test.

Run:
    python tests/run_all.py
    python tests/run_all.py --list
    python tests/run_all.py --only ingest --only render --verbose
    python tests/run_all.py --jobs 1          # serial, e.g. to compare timings
    python tests/run_all.py --jobs 4 --timeout 1800
    python tests/run_all.py --heavy           # + the heavy category (quiet machine)

The heavy category (``HEAVY``) holds what cannot be measured next to five other
children: the timing-sensitive ``test_f17_crash``, the byte-exact motion golden/
slice/cache/fallback files and the temp-tree scan in ``test_temp_hygiene``. They
are not flaky, they are simply not proof under load, so without ``--heavy`` they
are skipped one named line at a time and the summary says how many were left out
and which command brings them back. ``--only`` narrows both lists: asking for a
heavy file without ``--heavy`` names the skip and says to add the flag, it never
silently reports "no such test".
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
SUMMARY = re.compile(
    r"(\d+)\s+PASS\s*/\s*(\d+)\s+FAIL(?:\s*/\s*(\d+)\s+SKIP)?", re.IGNORECASE
)
DEFAULT_TIMEOUT_SEC = 1800.0
#: Auto parallel width: ``max(2, min(MAX_AUTO_JOBS, cpu - RESERVED_CORES))``.
#: Measured 1418 s serial vs ~20 s for 14 files at 6 jobs; two cores are left to
#: the parent process and to whatever else the machine is doing, and past six the
#: byte-exact golden tests lose more than the extra workers gain.
MAX_AUTO_JOBS = 6
RESERVED_CORES = 2


def discover() -> List[Path]:
    """Every test module, in a stable order (name, then path)."""
    return sorted(
        (path for path in TESTS.glob("test_*.py") if path.is_file()),
        key=lambda path: path.name.lower(),
    )


#: The heavy category: tests whose proof needs a quiet machine, not a busy one.
#: ``test_f17_crash`` measures how fast this machine is and kills at fixed offsets
#: from that (after 175 other tests the offsets drift and the kills pile into one
#: phase); the motion golden/slice/cache/fallback files compare rendered bytes, so
#: six concurrent children starve them and they go red without a defect; temp
#: hygiene scans the whole temp tree, which is contended while other tests write
#: there. None of them is flaky in isolation: they are simply not proof of
#: anything while the other five children are running. Hence a category, skipped
#: loudly by default and run together with the rest under ``--heavy``.
HEAVY = (
    "test_f17_crash.py",
    "test_motion_golden.py",
    "test_motion_slice.py",
    "test_motion_cache.py",
    "test_motion_fallback.py",
    "test_scaletta_view.py",
    "test_temp_hygiene.py",
)


def _match(files: Sequence[Path], only: Optional[Sequence[str]]) -> List[Path]:
    """Apply the ``--only`` substrings (case-insensitive) to an already filtered list."""
    if not only:
        return list(files)
    needles = [needle.lower() for needle in only if needle]
    return [path for path in files if any(n in path.name.lower() for n in needles)]


def selected(only: Optional[Sequence[str]], *, heavy: bool = False) -> List[Path]:
    files = discover()
    if not heavy:
        files = [path for path in files if path.name not in HEAVY]
    return _match(files, only)


def heavy_skipped(only: Optional[Sequence[str]] = None) -> List[Path]:
    """The heavy files this run leaves out, honouring the same ``--only`` needles.

    Returned rather than merely counted so the runner can name each one: a skip
    nobody sees is a coverage gap wearing a green exit code.
    """
    return _match([path for path in discover() if path.name in HEAVY], only)


def auto_jobs() -> int:
    """Parallel width when ``--jobs 0``: two cores free, capped at six.

    Below two the parallel pass would be a serial pass wearing a costume, so the
    floor is 2; above six the golden tests that compare bytes begin to starve and
    the failures they produce say more about the machine than about the code.
    """
    return max(2, min(MAX_AUTO_JOBS, (os.cpu_count() or 4) - RESERVED_CORES))


def child_env() -> Dict[str, str]:
    """The child must import ``vx`` without an install and print UTF-8.

    PYTHONPATH uses ``os.pathsep`` (``;`` on Windows, ``:`` elsewhere) and is
    prepended, never replaced, so a caller's extra path survives.
    """
    env = os.environ.copy()
    scripts = str(ROOT / "scripts")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = scripts + (os.pathsep + existing if existing else "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.setdefault("PYTHONUNBUFFERED", "1")
    # Le prove non devono scrivere nel registro di sessione **dell'utente**:
    # ogni `auto` lanciato da un test lasciava una sessione nella vista vera, e
    # la vista mostra l'ultima (e' cosi' che in pagina e' comparso un lavoro di
    # prova chiamato "prova progresso"). Isolato per questa corsa, non globale.
    env.setdefault(
        "VX_SESSION_DIR",
        str(Path(tempfile.gettempdir()) / "vx-tests-sessions" / f"run-{os.getpid()}"),
    )
    # Nessuna prova deve aprire finestre sulla macchina dell'utente, Explorer
    # compreso: era il difetto del 2026-11-06 (una finestra su una cartella
    # fixture "vuota" a ogni giro). `serve.reveal_native` esce subito quando
    # questa variabile e' attiva.
    env["VX_NO_REVEAL"] = "1"
    return env


def _decode_output(raw: bytes) -> str:
    """Decode a child's bytes without assuming the platform's default codec.

    Windows defaults to cp1252; a test printing accented Italian would then
    either crash the child or produce mojibake. BOMs win when present.
    """
    if not raw:
        return ""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            return raw.decode("utf-16", "replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")


def _parse_summary(text: str) -> Dict[str, int]:
    found: Dict[str, int] = {}
    for match in SUMMARY.finditer(text):
        found = {
            "pass": int(match.group(1)),
            "fail": int(match.group(2)),
            "skip": int(match.group(3) or 0),
        }
    return found


def run_one(path: Path, timeout: float, verbose: bool) -> Dict[str, Any]:
    started = time.time()
    timed_out = False
    # Ogni test e' un processo di console: lanciato da un padre senza console
    # (agente, servizio, finestra staccata) se ne fa allocare una nuova, e sulla
    # macchina dell'utente compare una finestra nera per ogni file. Le prove non
    # devono farsi vedere.
    hidden = 0
    startupinfo = None
    if os.name == "nt":
        hidden = 0x08000000  # CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(ROOT),
            env=child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=(timeout if timeout and timeout > 0 else None),
            creationflags=hidden,
            startupinfo=startupinfo,
        )
        raw = proc.stdout or b""
        code = int(proc.returncode)
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or b""
        code = -1
        timed_out = True
    text = _decode_output(raw)
    return {
        "name": path.name,
        "path": path,
        "code": code,
        "timed_out": timed_out,
        "seconds": round(time.time() - started, 1),
        "text": text,
        "summary": _parse_summary(text),
        "verbose": verbose,
    }


def _tail(text: str, limit: int = 80) -> str:
    lines = text.rstrip().splitlines()
    return "\n".join(lines[-limit:])


def _label(result: Dict[str, Any]) -> str:
    if result.get("flaky"):
        return "FLAKY"
    if result["timed_out"]:
        return "TIMEOUT"
    if result["code"] != 0:
        return "FAIL"
    return "PASS"


def _line(result: Dict[str, Any], index: int, total: int) -> str:
    summary = result["summary"]
    detail = ""
    if summary:
        detail = " ({pass} pass / {fail} fail / {skip} skip)".format(**summary)
    return (
        f"[{index:>2}/{total}] {result['name']:<38} "
        f"{_label(result):<7} {result['seconds']:>6.1f}s{detail}"
    )


def _report(result: Dict[str, Any], index: int, total: int) -> None:
    print(_line(result, index, total), flush=True)
    if result["code"] != 0 or result["verbose"]:
        body = _tail(result["text"])
        if body:
            for line in body.splitlines():
                print(f"    {line}", flush=True)


def _retry_failures(
    results: List[Dict[str, Any]], timeout: float, verbose: bool
) -> None:
    """Rerun every red once, alone; passing alone makes it FLAKY, not a failure.

    Only the parallel pass needs this: ``--jobs 1`` already ran each file with the
    machine to itself, so a red there is a red. The lone run replaces the first
    one in ``results`` -- its summary is the honest one, and its flaky flag makes
    the difference visible in the per-file line and in the final tally.
    """
    retryable = [
        index
        for index, result in enumerate(results)
        if result["code"] != 0 or result["timed_out"]
    ]
    if not retryable:
        return
    print(
        f"\nrun_all: retrying {len(retryable)} failure(s) one at a time "
        "(a red under load is not yet a defect)",
        flush=True,
    )
    for index in retryable:
        again = run_one(results[index]["path"], timeout, verbose)
        again["flaky"] = again["code"] == 0 and not again["timed_out"]
        results[index] = again
        verdict = "FLAKY" if again["flaky"] else _label(again)
        print(
            f"[retry] {again['name']:<38} {verdict:<7}"
            f" {again['seconds']:>6.1f}s alone",
            flush=True,
        )
        if not again["flaky"]:
            body = _tail(again["text"])
            if body:
                for line in body.splitlines():
                    print(f"    {line}", flush=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_all.py", description="Run every script-style test module."
    )
    parser.add_argument("--only", action="append", metavar="SUBSTRING")
    parser.add_argument(
        "--list", action="store_true", help="print the discovered files"
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=0,
        help=(
            "parallel children; 0 (default) = automatic "
            f"max(2, min({MAX_AUTO_JOBS}, cpu-{RESERVED_CORES})) "
            f"= {auto_jobs()} here, 1 = serial"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SEC,
        help="per-file seconds; 0 disables the parent timeout",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="print every child's output"
    )
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--heavy",
        action="store_true",
        help="also run the heavy category (timing-sensitive, run on a quiet machine)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    files = selected(args.only, heavy=args.heavy)
    skipped_heavy = [] if args.heavy else heavy_skipped(args.only)
    if args.list:
        for path in files:
            print(path.name)
        for path in skipped_heavy:
            print(f"{path.name}  (heavy, needs --heavy)")
        return 0
    if not files:
        for path in skipped_heavy:
            print(f"[heavy] {path.name:<38} SKIP heavy: run with --heavy", flush=True)
        hint = " (it is heavy: add --heavy)" if skipped_heavy else ""
        print(f"no test matched {args.only!r} in {TESTS}{hint}")
        return 2

    jobs = args.jobs if args.jobs > 0 else auto_jobs()
    total = len(files)
    print(
        f"run_all: {total} files from {TESTS} "
        f"(python {sys.version.split()[0]}, jobs={jobs}"
        f"{', auto' if args.jobs <= 0 else ''})",
        flush=True,
    )

    for path in skipped_heavy:
        print(f"[heavy] {path.name:<38} SKIP heavy: run with --heavy", flush=True)

    results: List[Dict[str, Any]] = []
    started = time.time()

    if jobs <= 1:
        for index, path in enumerate(files, 1):
            result = run_one(path, args.timeout, args.verbose)
            results.append(result)
            _report(result, index, total)
            if result["code"] != 0 or result["timed_out"]:
                if args.fail_fast:
                    break
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {
                pool.submit(run_one, path, args.timeout, args.verbose): path
                for path in files
            }
            done = 0
            for future in as_completed(futures):
                done += 1
                result = future.result()
                results.append(result)
                _report(result, done, total)
                if result["code"] != 0 or result["timed_out"]:
                    if args.fail_fast:
                        for pending in futures:
                            pending.cancel()
                        break

    if jobs > 1 and not args.fail_fast:
        _retry_failures(results, args.timeout, args.verbose)

    failed_results = [
        result
        for result in results
        if not result.get("flaky") and (result["code"] != 0 or result["timed_out"])
    ]
    flaky_results = [result for result in results if result.get("flaky")]
    asserts = {"pass": 0, "fail": 0, "skip": 0}
    for result in results:
        for key in asserts:
            asserts[key] += int(result["summary"].get(key, 0))
    elapsed = round(time.time() - started, 1)
    timeouts = sum(1 for result in failed_results if result["timed_out"])
    print(
        f"\nrun_all: {len(results) - len(failed_results) - len(flaky_results)} ok, "
        f"{len(failed_results)} failed, {len(flaky_results)} flaky"
        f"{f', {timeouts} timed out' if timeouts else ''}"
        f" | asserts {asserts['pass']} pass / {asserts['fail']} fail / {asserts['skip']} skip"
        f" | {elapsed}s"
    )
    if failed_results:
        print("failed: " + ", ".join(result["name"] for result in failed_results))
    if flaky_results:
        print(
            f"flake: {len(flaky_results)} file: "
            + ", ".join(result["name"] for result in flaky_results)
            + " (passed alone; not a defect)"
        )
    if skipped_heavy:
        print(
            f"skipped: 1 category (heavy, {len(skipped_heavy)} files) "
            "| recover: python tests/run_all.py --heavy"
        )
    return 1 if failed_results else 0


if __name__ == "__main__":
    raise SystemExit(main())
