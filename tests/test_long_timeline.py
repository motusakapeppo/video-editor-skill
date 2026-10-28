"""F26-T06 — a two-hour timeline compiles, and a long render is measurable and
resumable.

Three measurements, all of them from a command that ran here:

1. *compile* — 1800 clips of 4 s on layer 0 (7200 s, two hours) are validated and
   compiled to one plan, and the memory it costs is read twice: ``tracemalloc``
   for the Python allocation peak, and the process's own ``PeakWorkingSetSize``
   (``psapi.GetProcessMemoryInfo`` on Windows, ``ru_maxrss`` elsewhere). No frame
   is rendered for this leg.
2. *checkpoint* — a short document is rendered through ``render(..., chunks=N)``,
   one chunk's record is deleted, and the re-run must re-encode exactly that chunk
   and produce the same overall sha256 as the uninterrupted run.
3. *long run* — the first 600 s of the two-hour timeline are rendered at 320x180,
   12 fps (7200 frames), and ffprobe counts the frames while ffmpeg's own
   ``-benchmark`` reports its peak RSS. **Gated**: it runs only with ``VX_LONGRUN=1``
   and the gate is printed both ways, so a skip can never be read as a pass.

    python tests/test_long_timeline.py
    VX_LONGRUN=1 python tests/test_long_timeline.py
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, edl3_render, edl3_slice, stagecache  # noqa: E402
from vx.ffmpeg import run_ff, run_fp  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FPS = 12
SIZE = (320, 180)
CLIP_SEC = 4.0
CLIPS = 1800
LONG_SEC = 600.0
LONG_CHUNKS = 20
LONGRUN = os.environ.get("VX_LONGRUN", "") == "1"
MEASURED: Dict[str, Any] = {}


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:62} {reason}")


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_working_set() -> Optional[int]:
    """This process's peak working set in bytes, or ``None`` if unreadable.

    Windows: ``psapi.GetProcessMemoryInfo`` -> ``PeakWorkingSetSize``, the number
    the kernel keeps for this process. Everywhere else: ``ru_maxrss``, which is
    already a peak.
    """
    if os.name != "nt":
        import resource  # noqa: PLC0415 - POSIX only

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    import ctypes.wintypes as wt  # noqa: PLC0415 - Windows only

    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wt.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wt.HANDLE,
        ctypes.POINTER(_ProcessMemoryCounters),
        wt.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wt.BOOL
    if not psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        return None
    return int(counters.PeakWorkingSetSize)


def mb(value: Optional[int]) -> str:
    return "n/a" if value is None else f"{value / 1024 / 1024:.1f} MB"


# ------------------------------------------------------------------ fixtures


def make_source(work: Path, name: str, spec: str) -> str:
    path = work / name
    proc = run_ff(
        ["-f", "lavfi", "-i", spec, "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "18", "-pix_fmt", "yuv420p", str(path)],
        loglevel="error",
    )
    if not proc.ok or not path.exists():
        raise RuntimeError(f"fixture {name} failed: {proc.tail()}")
    return str(path)


def build_source_set(work: Path) -> List[str]:
    work.mkdir(parents=True, exist_ok=True)
    specs = [
        "testsrc2=s=320x180:r=12:d=8",
        "smptebars=s=320x180:r=12:d=8",
        "rgbtestsrc=s=320x180:r=12:d=8",
    ]
    return [make_source(work, f"src{index}.mp4", spec) for index, spec in enumerate(specs)]


def build_clips(sources: List[str], count: int) -> List[Dict[str, Any]]:
    """``count`` four-second clips on layer 0, each reading a different window.

    The window offset is what makes every chunk's picture distinct, so a chunk's
    sha256 names that chunk and not the shared source.
    """
    clips: List[Dict[str, Any]] = []
    for index in range(count):
        start = (index % 8) * 0.5
        clip: Dict[str, Any] = {
            "id": f"c{index:05d}",
            "source": sources[index % len(sources)],
            "src_in": start,
            "src_out": start + CLIP_SEC,
            "dst_in": index * CLIP_SEC,
            "dst_out": (index + 1) * CLIP_SEC,
            "layer": 0,
            "effects": [],
        }
        if index % 12 == 0:
            clip["effects"].append({"kind": "vignette"})
        if index % 5 == 0:
            clip["effects"].append({"kind": "transform", "params": {"scale": 1.0}})
        clips.append(clip)
    return clips


def timeline_of(clips: List[Dict[str, Any]]) -> edl3.Timeline3:
    return edl3.Timeline3.from_dict(
        {"version": "vx.edl3/1.2", "fps": FPS, "size": list(SIZE), "clips": clips}
    )


def first_seconds(timeline: edl3.Timeline3, seconds: float) -> edl3.Timeline3:
    kept = [
        clip.to_dict()
        for clip in timeline.clips
        if float(clip.dst_out) <= seconds + 1e-9
    ]
    return timeline_of(kept)


def frame_count(path: Path) -> int:
    proc = run_fp(
        ["-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)]
    )
    text = (proc.stdout or "").strip().splitlines()
    return int(text[-1]) if proc.ok and text else -1


# ------------------------------------------------------------------ section 1


def section_compile(sources: List[str]) -> edl3.Timeline3:
    print("\n[1] two-hour compile (validate + compile_timeline, no render)")
    clips = build_clips(sources, CLIPS)
    document = {"version": "vx.edl3/1.2", "fps": FPS, "size": list(SIZE), "clips": clips}
    check("document declares 1800 clips", len(document["clips"]) == CLIPS, len(clips))

    before = peak_working_set()
    tracemalloc.start()
    started = time.monotonic()
    timeline = edl3.Timeline3.from_dict(document)
    problems = timeline.validate()
    plan = edl3_render.compile_timeline(timeline, audio="drop")
    elapsed = time.monotonic() - started
    _, python_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    after = peak_working_set()

    check("timeline duration is exactly two hours", abs(timeline.duration - 7200.0) < 1e-6,
          f"{timeline.duration}s")
    check("validation reports no problem", problems == [], problems[:2])
    check("compile returns a plan", isinstance(plan, dict))
    check("plan duration is two hours", abs(float(plan["duration"]) - 7200.0) < 1e-6)
    check("plan keeps the document's clock", float(plan["fps"]) == float(FPS))
    check("plan carries a row per clip", len(plan["clips"]) == CLIPS, len(plan["clips"]))
    check("every clip is one ffmpeg input", plan["input_args"].count("-i") == CLIPS,
          plan["input_args"].count("-i"))
    check("layer 0 concatenates all 1800 clips",
          f"concat=n={CLIPS}:v=1:a=0" in plan["filter_script"])
    check("150 library looks were routed (one per 12 clips)",
          sum(1 for row in plan["backends"] if row["effect"] == "vignette") == 150)
    check("compile finished well inside a minute", elapsed < 60.0, f"{elapsed:.3f}s")
    check("python allocation stayed under 256 MB", python_peak < 256 * 1024 * 1024,
          mb(python_peak))
    peak = after if after is not None else before
    check("process peak working set stayed under 1 GB",
          peak is None or peak < 1024 * 1024 * 1024, mb(peak))

    MEASURED.update(
        {
            "clips": len(plan["clips"]),
            "compile_seconds": round(elapsed, 3),
            "python_peak_bytes": python_peak,
            "process_peak_bytes": peak,
            "filter_script_chars": len(plan["filter_script"]),
        }
    )
    print(f"      clips={CLIPS} compile={elapsed:.3f}s python_peak={mb(python_peak)} "
          f"process_peak={mb(peak)}")
    return timeline


# ------------------------------------------------------------------ section 2


def section_checkpoint(work: Path, sources: List[str]) -> None:
    print("\n[2] checkpoint / resume (render, delete one record, re-run)")
    clips = build_clips(sources, 6)
    timeline = timeline_of(clips)
    workdir = work / "checkpoint"
    uninterrupted = work / "uninterrupted.mp4"
    resumed = work / "resumed.mp4"

    started = time.monotonic()
    first = edl3_render.render(
        timeline, uninterrupted, workdir=workdir, audio="drop", chunks=3
    )
    first_seconds_elapsed = time.monotonic() - started
    check("first run rendered all three chunks", first["chunks"]["rendered"] == [0, 1, 2],
          first["chunks"]["rendered"])
    check("first run reused nothing", first["chunks"]["reused"] == [])
    check("every chunk record names its index",
          [row["chunk"] for row in first["chunks"]["records"]] == [0, 1, 2])
    checksums = [row["sha256"] for row in first["chunks"]["records"]]
    check("the three chunks are three distinct pictures", len(set(checksums)) == 3,
          len(set(checksums)))
    artifacts = [Path(row["artifact"]) for row in first["chunks"]["records"]]
    check("each record's sha256 is the file's sha256",
          all(stagecache.file_sha256(path) == row["sha256"]
              for path, row in zip(artifacts, first["chunks"]["records"])))
    check("three cache entries exist",
          len(list((workdir / "stage_cache").glob("*.json"))) == 3)

    keys = [row["key"] for row in first["chunks"]["records"]]
    window = edl3_slice.plan_windows(timeline, 3)[1]
    document_sha = edl3_slice.sha256(
        edl3_slice.canonical(edl3_slice.document(timeline, window))
    )
    check("the record carries the chunk's input hash",
          first["chunks"]["records"][1]["document_sha256"] == document_sha,
          document_sha[:12])
    stored = json.loads((workdir / "stage_cache" / f"{keys[1]}.json").read_text("utf-8"))
    check("the record stores the chunk index in its meta", stored["meta"]["chunk"] == 1)
    check("the record stores the output sha256", stored["sha256"] == checksums[1])
    expected_sources = {
        sources[index % len(sources)] for index in window["clips"]
    }
    check("the record stores the source hashes",
          set(stored["meta"]["source_sha256"]) == expected_sources,
          sorted(stored["meta"]["source_sha256"]))

    (workdir / "stage_cache" / f"{keys[1]}.json").unlink()
    started = time.monotonic()
    second = edl3_render.render(
        timeline, resumed, workdir=workdir, audio="drop", chunks=3
    )
    resume_seconds = time.monotonic() - started
    check("the re-run re-encoded exactly the deleted chunk",
          second["chunks"]["rendered"] == [1], second["chunks"]["rendered"])
    check("the re-run reused the other two", second["chunks"]["reused"] == [0, 2],
          second["chunks"]["reused"])
    check("the re-run produced the same overall sha256",
          second["chunks"]["output_sha256"] == first["chunks"]["output_sha256"],
          second["chunks"]["output_sha256"][:16])
    check("the re-rendered chunk is byte-identical",
          second["chunks"]["records"][1]["sha256"] == checksums[1])
    check("the untouched chunks were not rewritten",
          second["chunks"]["records"][0]["sha256"] == checksums[0]
          and second["chunks"]["records"][2]["sha256"] == checksums[2])
    check("the record was restored", (workdir / "stage_cache" / f"{keys[1]}.json").exists())
    check("the resumed file has the same frame count",
          frame_count(resumed) == frame_count(uninterrupted) == 288,
          frame_count(resumed))

    single = edl3_render.render(timeline, work / "single.mp4",
                                workdir=work / "single_wd", audio="drop")
    check("chunked and single-pass agree on frames",
          frame_count(work / "single.mp4") == frame_count(uninterrupted), frame_count(work / "single.mp4"))
    check("the single-pass report gained a benchmark slot",
          "benchmark" in single and single["benchmark"]["encode_kib"] is None)
    check("resuming is faster than the uninterrupted run",
          resume_seconds < first_seconds_elapsed,
          f"resume={resume_seconds:.2f}s first={first_seconds_elapsed:.2f}s")

    other = build_clips(sources, 6)
    other[0]["effects"] = [{"kind": "sepia"}]
    outside = edl3_slice.sha256(
        edl3_slice.canonical(edl3_slice.document(timeline_of(other), window))
    )
    check("an edit outside the chunk leaves its input hash untouched",
          outside == document_sha)
    other[window["clips"][0]]["effects"] = [{"kind": "sepia"}]
    changed = edl3_slice.sha256(
        edl3_slice.canonical(edl3_slice.document(timeline_of(other), window))
    )
    check("an edit inside the chunk changes its input hash", changed != document_sha)

    try:
        edl3_slice.plan_windows(
            timeline_of(build_clips(sources, 2)), 4
        )
        check("more chunks than clips is refused", False)
    except edl3_render.CompileError as exc:
        check("more chunks than clips is refused", exc.code == "CHUNK_UNSUPPORTED", exc.code)

    audio_clip = build_clips(sources, 6)
    audio_doc = {
        "version": "vx.edl3/1.2",
        "fps": FPS,
        "size": list(SIZE),
        "clips": audio_clip,
        "audio": [{"role": "dialogue", "clips": [
            {"source": sources[0], "src_in": 0.0, "src_out": 1.0,
             "dst_in": 0.0, "dst_out": 1.0}]}],
    }
    try:
        edl3_slice.plan_windows(edl3.Timeline3.from_dict(audio_doc), 3)
        check("an audio bus is refused, never sliced", False)
    except edl3_render.CompileError as exc:
        check("an audio bus is refused, never sliced", exc.code == "CHUNK_UNSUPPORTED",
              exc.code)

    MEASURED.update(
        {
            "chunk_count": 3,
            "first_run_seconds": round(first_seconds_elapsed, 3),
            "resume_seconds": round(resume_seconds, 3),
            "resumed_chunk": second["chunks"]["rendered"],
            "sha_equal": second["chunks"]["output_sha256"] == first["chunks"]["output_sha256"],
            "chunk_sha256": [checksum[:16] for checksum in checksums],
        }
    )


# ------------------------------------------------------------------ section 3


def section_long(timeline: edl3.Timeline3, work: Path) -> None:
    print("\n[3] ten-minute render (320x180, 12 fps, first 600 s of the two-hour plan)")
    if not LONGRUN:
        print("  " + "=" * 74)
        skip(
            "LONGRUN heavy render",
            "gate VX_LONGRUN=1 is NOT set: nothing was rendered, nothing was measured",
        )
        print("  " + "!" * 74)
        print("  !! THIS IS A SKIP, NOT A PASS. Run with VX_LONGRUN=1 to render the")
        print("  !! 7200 real frames and read ffmpeg's own -benchmark line.")
        print("  " + "!" * 74)
        MEASURED["long"] = {"gated": "VX_LONGRUN not set", "skipped": True}
        return

    part = first_seconds(timeline, LONG_SEC)
    check("the long-run document is exactly 600 s", abs(part.duration - LONG_SEC) < 1e-6,
          part.duration)
    check("the long-run document is 150 clips", len(part.clips) == 150, len(part.clips))

    output = work / "ten_minutes.mp4"
    tracemalloc.start()
    started = time.monotonic()
    report = edl3_render.render(
        part,
        output,
        workdir=work / "long",
        audio="drop",
        chunks=LONG_CHUNKS,
        benchmark=True,
    )
    elapsed = time.monotonic() - started
    _, python_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    process_peak = peak_working_set()

    frames = frame_count(output)
    expected = round(LONG_SEC * FPS)
    check("ffprobe counts 7200 frames (±2)", abs(frames - expected) <= 2, frames)
    check("the render produced a non-empty file", report["output_size"] > 0,
          f"{report['output_size'] / 1024:.1f} KB")
    check("all 20 chunks were rendered", report["chunks"]["rendered"] == list(range(LONG_CHUNKS)),
          len(report["chunks"]["rendered"]))
    maxrss = report["benchmark"]["chunks_kib"]
    check("ffmpeg reported one maxrss per chunk", len(maxrss) == LONG_CHUNKS, len(maxrss))
    check("the encoder's peak RSS was parsed from its own bench line",
          bool(maxrss) and max(maxrss) > 0, f"max={max(maxrss) if maxrss else 0} KiB")

    victim = LONG_CHUNKS // 2
    victim_key = report["chunks"]["records"][victim]["key"]
    (work / "long" / "stage_cache" / f"{victim_key}.json").unlink()
    resume_started = time.monotonic()
    again = edl3_render.render(
        part, work / "ten_minutes_resumed.mp4", workdir=work / "long",
        audio="drop", chunks=LONG_CHUNKS, benchmark=True,
    )
    resume_seconds = time.monotonic() - resume_started
    check("the 600 s resume re-encodes exactly the deleted chunk",
          again["chunks"]["rendered"] == [victim], again["chunks"]["rendered"])
    check("the 600 s resume reuses the other 19",
          len(again["chunks"]["reused"]) == LONG_CHUNKS - 1, len(again["chunks"]["reused"]))
    check("the 600 s resume produces the same overall sha256",
          again["chunks"]["output_sha256"] == report["chunks"]["output_sha256"],
          again["chunks"]["output_sha256"][:16])
    check("the resumed 600 s file still counts 7200 frames",
          abs(frame_count(work / "ten_minutes_resumed.mp4") - expected) <= 2)

    MEASURED["long"] = {
        "frames": frames,
        "render_seconds": round(elapsed, 3),
        "output_bytes": report["output_size"],
        "chunks": report["chunks"]["count"],
        "maxrss_kib": max(maxrss) if maxrss else None,
        "maxrss_per_chunk_kib": maxrss,
        "concat_maxrss_kib": report["benchmark"]["concat_kib"],
        "python_peak_bytes": python_peak,
        "process_peak_bytes": process_peak,
        "output_sha256": report["chunks"]["output_sha256"],
        "resume_seconds": round(resume_seconds, 3),
        "resume_rendered": again["chunks"]["rendered"],
        "resume_sha_equal": again["chunks"]["output_sha256"]
        == report["chunks"]["output_sha256"],
    }
    print(f"      frames={frames} render={elapsed:.2f}s "
          f"bytes={report['output_size']} maxrss={max(maxrss) if maxrss else 'n/a'}KiB "
          f"python_peak={mb(python_peak)} process_peak={mb(process_peak)}")


# ---------------------------------------------------------------------- main


def main() -> int:
    print(f"LONGRUN GATE: VX_LONGRUN={'1 (enabled)' if LONGRUN else 'not set (heavy render skipped)'}")
    with tempfile.TemporaryDirectory(prefix="vx_long_timeline_") as tmp:
        work = Path(tmp)
        started = time.monotonic()
        try:
            sources = build_source_set(work / "sources")
            timeline = section_compile(sources)
            section_checkpoint(work, sources)
            section_long(timeline, work)
        except Exception:  # noqa: BLE001 - a crash is a failure, never a skip
            import traceback

            traceback.print_exc()
            print("\nharness crashed before the report was produced")
            return 1
        elapsed = time.monotonic() - started

    print("\n" + "=" * 78)
    print("F26-T06 MEASURED")
    print("=" * 78)
    for key, value in MEASURED.items():
        if key == "long":
            for name, number in value.items():
                print(f"  long.{name}: {number}")
            continue
        print(f"  {key}: {value}")
    print("-" * 78)
    gate = "ENABLED" if LONGRUN else "DISABLED (VX_LONGRUN not set) -> heavy render skipped"
    print(f"  LONGRUN gate: {gate}")
    print(f"  {PASS} passed, {FAIL} failed, {SKIP} skipped, {elapsed:.1f}s")
    if FAIL:
        print("FAIL")
    elif SKIP:
        print("NO FAILURE — but the skipped section measured nothing: NOT a full pass")
    else:
        print("OK")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
