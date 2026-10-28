"""F16-T01 gate - the measurement bench reports measured numbers, not guesses.

The bench is exercised the way a user runs it: ``python -m vx.bench`` in a child
process, so the exit code asserted here is the real one.

Two documents are used: a valid one rendered twice (two rows, same sha256, every
numeric field a real number, declared provenance for frames and memory) and a
broken one (a path that does not exist) which must exit 2 and land in the report
as ``"ok": false`` with a captured error and stderr tail.

No duration, byte count or memory figure is pinned: those are measurements, and
pinning them would certify this machine instead of the bench.

    python tests/test_bench.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import ffmpeg as ff  # noqa: E402

CEILING_SECONDS = 600.0
FPS = 12
WIDTH, HEIGHT = 320, 568

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    suffix = f"  {detail}" if detail else ""
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:<60}{suffix}")


def build_base(path: Path) -> Path:
    """One short real clip: the bench must measure an ffmpeg render, not a stub."""
    proc = ff.run_ff(
        [
            "-f", "lavfi", "-i", f"testsrc2=s={WIDTH}x{HEIGHT}:r={FPS}:d=1",
            "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-g", "1", str(path),
        ],
        loglevel="error",
    )
    if not proc.ok or not path.exists():
        raise RuntimeError(f"base clip failed: {proc.tail()}")
    return path


def write_doc(path: Path, base: Path) -> Path:
    doc = {
        "version": "vx.edl3/1.1",
        "fps": FPS,
        "size": [WIDTH, HEIGHT],
        "clips": [{"id": "c1", "source": str(base), "src_in": 0.0,
                   "src_out": 1.0, "dst_in": 0.0, "dst_out": 1.0, "layer": 0}],
    }
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def run_bench(doc: Path, out: Path, repeats: int) -> Tuple[int, float, str]:
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "vx.bench", "--doc", str(doc), "--out", str(out),
         "--repeats", str(repeats)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, time.perf_counter() - started, (proc.stderr or "").strip()


def numeric_fields(report: Mapping[str, Any]) -> List[Tuple[str, Any]]:
    """Every declared numeric field, flattened, so each one is asserted alone."""
    found: List[Tuple[str, Any]] = [
        ("wall_seconds", report["wall_seconds"]),
        ("seconds_total", report["seconds_total"]),
        ("repeats", report["repeats"]),
    ]
    for row in report["repeats_data"]:
        tag = f"repeat[{row['index']}]"
        found += [(f"{tag}.index", row["index"]),
                  (f"{tag}.seconds", row["seconds"]),
                  (f"{tag}.bytes", row["bytes"]),
                  (f"{tag}.frames", row["frames"])]
    return found


def check_report(report: Mapping[str, Any], table: Path) -> None:
    check("ok is true", report["ok"] is True)
    check("repeats echoed", report["repeats"] == 2)
    check("two rows for two repeats", len(report["repeats_data"]) == 2)
    rows = report["repeats_data"]
    digests = [row["sha256"] for row in rows]
    check("both repeats share one sha256", len(set(digests)) == 1, str(digests))
    check("sha256 is 64 lowercase hex chars",
          all(isinstance(d, str) and re.fullmatch(r"[0-9a-f]{64}", d) for d in digests))
    check("fresh output file per repeat", len({row["out"] for row in rows}) == 2)
    check("each row measured a positive frame count",
          all(isinstance(row["frames"], int) and row["frames"] > 0 for row in rows),
          str([row["frames"] for row in rows]))
    check("each row measured a positive byte count",
          all(isinstance(row["bytes"], int) and row["bytes"] > 0 for row in rows),
          str([row["bytes"] for row in rows]))
    for name, value in numeric_fields(report):
        check(f"{name} is a real number",
              isinstance(value, (int, float)) and not isinstance(value, bool),
              repr(value))
    check("wall_seconds is measured and under the ceiling",
          0.0 < float(report["wall_seconds"]) < CEILING_SECONDS,
          f"{float(report['wall_seconds']):.3f}s")
    check("every repeat took positive time",
          all(float(row["seconds"]) > 0.0 for row in rows))
    check("peak_rss is an int or null, never a string",
          report["peak_rss_bytes"] is None or isinstance(report["peak_rss_bytes"], int),
          repr(report["peak_rss_bytes"]))
    check("peak_rss provenance is declared",
          report["peak_rss_source"] in {"psutil", "unavailable"},
          str(report["peak_rss_source"]))
    check("frames provenance is declared",
          report["frames_source"] in {"ffprobe", "report", "unavailable"},
          str(report["frames_source"]))
    check("the exact ffmpeg command line was captured",
          isinstance(report["command"], list) and len(report["command"]) > 1
          and all(isinstance(part, str) for part in report["command"]))
    check("ffmpeg version is the real first line",
          str(report["environment"]["ffmpeg_version"]).startswith("ffmpeg version"),
          report["environment"]["ffmpeg_version"])
    check("python version and platform are recorded",
          isinstance(report["environment"]["python_version"], str)
          and isinstance(report["environment"]["platform"], str))
    check("timestamp is a UTC ISO instant",
          report["timestamp_utc"].endswith("Z")
          and datetime.strptime(
              report["timestamp_utc"], "%Y-%m-%dT%H:%M:%SZ") is not None)
    check("temp workdir was cleaned up (no --keep)",
          not Path(report["workdir"]).exists(), str(report["workdir"]))
    check("Markdown table exists", table.exists())
    if table.exists():
        body = table.read_text(encoding="utf-8")
        data_rows = re.findall(r"^\|\s*\d+\s*\|", body, flags=re.MULTILINE)
        check("Markdown has one row per repeat", len(data_rows) == 2,
              f"{len(data_rows)} row(s)")
        check("Markdown carries the measured sha256",
              all(row["sha256"] in body for row in rows))


def check_failure(work: Path) -> None:
    broken_out = work / "report" / "broken.json"
    code, _, _ = run_bench(work / "does_not_exist.json", broken_out, 1)
    check("broken doc exits 2", code == 2, f"rc={code}")
    check("broken doc still writes a report", broken_out.exists())
    check("broken report has a Markdown table", broken_out.with_suffix(".md").exists())
    if not broken_out.exists():
        return
    failure = json.loads(broken_out.read_text(encoding="utf-8"))
    check("broken report has ok=false", failure["ok"] is False)
    check("broken report names its error code",
          isinstance(failure["error"], Mapping)
          and bool(str(failure["error"]["code"])), str(failure["error"]["code"]))
    check("broken report captured a stderr tail",
          isinstance(failure["error"]["stderr_tail"], list)
          and len(failure["error"]["stderr_tail"]) > 0,
          f"{len(failure['error']['stderr_tail'])} line(s)")
    check("broken report has no measured rows", failure["repeats_data"] == [])


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx_bench_gate_") as tmp:
        work = Path(tmp)
        doc = write_doc(work / "doc.json", build_base(work / "base.mp4"))
        out = work / "report" / "bench.json"
        code, elapsed, stderr = run_bench(doc, out, 2)
        check("exit code is 0 on a real render", code == 0, f"rc={code} {stderr}")
        check("JSON report was written", out.exists())
        report: Optional[Mapping[str, Any]] = (
            json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
        )
        check("report parses as a JSON object", isinstance(report, Mapping))
        if report is not None:
            check_report(report, out.with_suffix(".md"))
        check_failure(work)
        print(f"\nbench gate: {elapsed:.1f}s wall")
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
