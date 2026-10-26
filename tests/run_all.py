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

Run:
    python tests/run_all.py
    python tests/run_all.py --list
    python tests/run_all.py --only ingest --only render --verbose
    python tests/run_all.py --jobs 4 --timeout 1800
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
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


def discover() -> List[Path]:
    """Every test module, in a stable order (name, then path)."""
    return sorted(
        (path for path in TESTS.glob("test_*.py") if path.is_file()),
        key=lambda path: path.name.lower(),
    )


def selected(only: Optional[Sequence[str]]) -> List[Path]:
    files = discover()
    if not only:
        return files
    needles = [needle.lower() for needle in only if needle]
    return [path for path in files if any(n in path.name.lower() for n in needles)]


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
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(ROOT),
            env=child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=(timeout if timeout and timeout > 0 else None),
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_all.py", description="Run every script-style test module."
    )
    parser.add_argument("--only", action="append", metavar="SUBSTRING")
    parser.add_argument(
        "--list", action="store_true", help="print the discovered files"
    )
    parser.add_argument(
        "--jobs", type=int, default=1, help="parallel children (default 1)"
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
    args = parser.parse_args(list(argv) if argv is not None else None)

    files = selected(args.only)
    if args.list:
        for path in files:
            print(path.name)
        return 0
    if not files:
        print(f"no test matched {args.only!r} in {TESTS}")
        return 2

    total = len(files)
    print(
        f"run_all: {total} files from {TESTS} "
        f"(python {sys.version.split()[0]}, jobs={max(1, args.jobs)})",
        flush=True,
    )

    results: List[Dict[str, Any]] = []
    failed = 0
    started = time.time()

    if args.jobs <= 1:
        for index, path in enumerate(files, 1):
            result = run_one(path, args.timeout, args.verbose)
            results.append(result)
            _report(result, index, total)
            if result["code"] != 0 or result["timed_out"]:
                failed += 1
                if args.fail_fast:
                    break
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
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
                    failed += 1
                    if args.fail_fast:
                        for pending in futures:
                            pending.cancel()
                        break

    asserts = {"pass": 0, "fail": 0, "skip": 0}
    for result in results:
        for key in asserts:
            asserts[key] += int(result["summary"].get(key, 0))
    elapsed = round(time.time() - started, 1)
    timeouts = sum(1 for result in results if result["timed_out"])
    print(
        f"\nrun_all: {len(results) - failed} ok, {failed} failed"
        f"{f', {timeouts} timed out' if timeouts else ''}"
        f" | asserts {asserts['pass']} pass / {asserts['fail']} fail / {asserts['skip']} skip"
        f" | {elapsed}s"
    )
    if failed:
        names = ", ".join(result["name"] for result in results if result["code"] != 0)
        print(f"failed: {names}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
