"""F26-T08 — one real minute per profile, through the chunk path, with the
memory measured instead of hoped for.

D-12 (``PLAN/DISCREPANZE.md``) recorded the failure this task answers: dozens of
clips compiled into **one** ffmpeg graph pushed the process to 5.8 GB of RSS and
eleven minutes without writing a frame. So the render here never runs as one
graph. Every profile's minute is:

1. **document** — 60 s at the profile's own canvas and rate, with a cut count
   inside its declared ``cut_density`` band and at most three library looks in
   any ten seconds;
2. **chunks** — rendered by ``edl3_render.render(..., chunks=6, benchmark=True)``,
   which is the path F26-T06 built: six independent ffmpeg processes of 10 s
   each, recorded through ``vx.stagecache``;
3. **join** — the six pieces are joined by the concat demuxer with a *re-encode*
   (never ``-c copy``), and the audio bed is laid down in the same pass, so the
   master carries the audio :mod:`vx.profile_master` needs;
4. **measure** — ffprobe on the master for exact canvas, frame count and
   duration; ``vx.profile_master`` for the profile's loudness target, verified
   against a *fresh* measurement of its own output;
5. **deliver** — the profile's first declared mp4 target through
   :mod:`vx.deliver`, then codec and pixel format read back from the file;
6. **prove** — one frame extracted, its sha256 recorded beside the report;
7. **memory** — ffmpeg's own ``-benchmark`` ``maxrss`` for every chunk and for
   the join; the peak is a measurement, never an estimate.

A profile whose declared length window starts above 60 s (a 90 s trailer, a
4800 s film) is **not** stretched and **not** shortened: the document is the
minute this task asks for, and the report states the measured length next to the
declared window with ``inside_declared_window: false``.

The gate
--------
Everything that encodes sits behind ``VX_LONGRUN=1``. With the gate closed the
harness still builds every document, validates it, compiles all six 10 s pieces
of it and checks the clip count against the profile's density band — and then
says in capitals that nothing was rendered and that this is NOT a pass.

    python tests/test_profile_minute.py
    VX_LONGRUN=1 python tests/test_profile_minute.py

``VX_PROFILE_MINUTE_ONLY=reel,film`` renders a subset (debugging only: the run
is a full pass only when all nine profiles are measured).
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import (  # noqa: E402
    deliver,
    deliver_probe,
    edl3,
    edl3_render,
    edl3_slice,
    profile_master,
    profiles,
    qc,
    stagecache,
    structure,
)
from vx.ffmpeg import run_ff, run_fp  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0

#: The minute this task asks for, per profile.
SECONDS = 60.0
#: Ten-second pieces: the segment length F26-T06's chunk path was proven with.
CHUNKS = 6
WINDOW = SECONDS / CHUNKS
#: Library looks allowed to *start* inside any ten-second window (D031).
LOOKS_CAP = 3
#: The two library looks used, alternating per piece (two clips of each piece
#: carry one, so a sliding window spans at most three).
LOOK_EFFECTS = ("vignette", "sepia")
#: Source fixtures: long enough for the longest clip plus its read offset.
SOURCE_SECONDS = 14.0
SOURCE_OFFSETS = (0.0, 0.5, 1.0)
#: D-12 measured 5.8 GB in one graph. A chunk that needs more than 2 GB of RSS is
#: a failure of the chunking itself, so the ceiling is a gate and not a comment.
MEMORY_CEILING_MIB = 2048.0
#: Where the per-profile reports and proof frames land (beside each other).
REPORT_DIR = Path(os.environ.get("VX_PROFILE_REPORT_DIR") or r"D:\video_work\ulw_profiles")

LONGRUN = os.environ.get("VX_LONGRUN", "") == "1"
ONLY = tuple(
    item.strip() for item in os.environ.get("VX_PROFILE_MINUTE_ONLY", "").split(",") if item.strip()
)

#: The audio bed the chunk path cannot carry (``edl3_slice`` refuses an audio
#: bus, D-12's slicing rule) and that the join pass therefore lays down: a 440 Hz
#: tone with a slow envelope, so loudnorm measures a *varying* signal and the
#: two-pass result is a real measurement, not a constant.
AUDIO_BED = f"sine=frequency=440:sample_rate=48000:duration={SECONDS:g}"
AUDIO_ENVELOPE = "volume='0.2+0.12*sin(2*PI*t/4)':eval=frame"

MEASURED: Dict[str, Any] = {"profiles": {}}


# --------------------------------------------------------------------- harness


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:62} {reason}")


def num(value: float) -> str:
    """An ffmpeg-friendly number: no trailing zeros, no exponent."""
    return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"


def mib(kib: Optional[int]) -> str:
    return "n/a" if kib is None else f"{kib / 1024:.1f} MiB"


def _concat_path(path: Path) -> str:
    """A concat-demuxer path: POSIX separators, single quotes escaped."""
    return path.resolve().as_posix().replace("'", "'\\''")


# ------------------------------------------------------------------- documents


def clips_per_window(row: profiles.Profile) -> int:
    """Cuts per ten seconds this document uses for ``row``, an integer in band.

    Two constraints decide it, in this order: the cut density band (the number
    of layer-0 clips inside one ten-second piece *is* the density), and the
    profile's hook — a profile that hooks at 3 s cannot open with a 10 s shot, so
    the clip length ``10 / k`` must not exceed ``hook_seconds``. A profile whose
    band has no integer in it would be a broken row, and ``profiles.check``
    refuses that before this runs.
    """
    low = max(1, int(math.ceil(row.cut_density_min - 1e-9)))
    high = max(low, int(math.floor(row.cut_density_max + 1e-9)))
    chosen = min(max(int(round((row.cut_density_min + row.cut_density_max) / 2.0)), low), high)
    if row.hook_seconds > 0.0:
        need = int(math.ceil(WINDOW / row.hook_seconds - 1e-9))
        chosen = min(max(chosen, need, low), high)
    return chosen


def looks_at(index: int, per_window: int) -> List[str]:
    """The library looks clip ``index`` carries: two per ten-second piece."""
    local = index % per_window
    piece = index // per_window
    positions = {0} if per_window < 2 else {0, per_window // 2}
    if local not in positions:
        return []
    return [LOOK_EFFECTS[piece % len(LOOK_EFFECTS)]]


def build_document(row: profiles.Profile, sources: Sequence[str]) -> Dict[str, Any]:
    """The 60 s document for ``row``: uniform clips that tile the minute exactly.

    Uniform clip length is what makes a chunk boundary land on 10.000 s: six
    pieces of ``clips_per_window(row)`` clips are six ten-second windows, so the
    chunk path's refusal of a boundary that cuts a clip never triggers.
    """
    per_window = clips_per_window(row)
    total = CHUNKS * per_window
    clip_seconds = WINDOW / per_window
    clips: List[Dict[str, Any]] = []
    for index in range(total):
        piece = index // per_window
        # The read offset advances with the piece as well as with the clip, so a
        # piece is not a byte-copy of its neighbour: a slice that rendered the
        # wrong window would still show up in the artifact's sha256.
        offset = SOURCE_OFFSETS[(index + piece) % len(SOURCE_OFFSETS)]
        clips.append(
            {
                "id": f"{row.id}-{index:03d}",
                "source": sources[index % len(sources)],
                "src_in": offset,
                "src_out": offset + clip_seconds,
                # (i + 1) * clip_seconds is both this clip's dst_out and the next
                # one's dst_in, so the tiling is exact by construction.
                "dst_in": index * clip_seconds,
                "dst_out": (index + 1) * clip_seconds,
                "layer": 0,
                "effects": [{"kind": kind} for kind in looks_at(index, per_window)],
            }
        )
    return {
        "version": edl3.EDL3_VERSION,
        "fps": int(row.fps),
        "size": [row.width, row.height],
        "clips": clips,
        # No profile_ref on purpose: ``edl3_slice.plan_windows`` refuses a
        # declared profile on a piece ("its structure is measured against the
        # whole edit"), so the profile travels as the canvas/rate plus the
        # report, and the loudness law is applied by :mod:`vx.profile_master`.
    }


def timeline_of(document: Dict[str, Any]) -> edl3.Timeline3:
    return edl3.Timeline3.from_dict(document)


def cuts_of(timeline: edl3.Timeline3) -> int:
    """Boundaries where one layer-0 clip ends and the next starts at that instant."""
    spans = [
        (float(clip.dst_in), float(clip.dst_out))
        for clip in timeline.clips
        if clip.layer == 0
    ]
    return sum(
        1 for left, right in zip(spans, spans[1:]) if abs(left[1] - right[0]) <= 1e-6
    )


def look_windows(timeline: edl3.Timeline3) -> List[Tuple[float, int]]:
    """``(start, looks)`` for every clip that carries a library look."""
    rows: List[Tuple[float, int]] = []
    for clip in timeline.clips:
        if not clip.effects:
            continue
        rows.append((float(clip.dst_in), len(clip.effects)))
    return rows


def worst_look_window(rows: Sequence[Tuple[float, int]]) -> int:
    """The largest number of looks starting inside any ten-second window."""
    anchors = sorted(rows)
    worst = 0
    for position, (start, _count) in enumerate(anchors):
        total = sum(
            count
            for other, count in anchors[position:]
            if other - start <= WINDOW + 1e-9
        )
        worst = max(worst, total)
    return worst


def expected_structure_codes(row: profiles.Profile) -> set:
    """The ``check_structure`` codes a 60 s document of this profile must produce.

    Two are structural consequences of this task, not defects:

    * ``STRUCTURE_DURATION`` — the profile's declared window starts above 60 s
      (a 120 s videoclip, a 4800 s film). The minute is what was asked for, and
      the report carries both numbers;
    * ``STRUCTURE_INTERVIEW`` — a documentary's grammar wants dialogue material,
      and the only carriers of it are an audio bus or a nested sequence, both of
      which ``edl3_slice.plan_windows`` refuses to slice. The chunk path and that
      piece of the grammar are therefore mutually exclusive, and this says so
      instead of hiding it.

    A ``STRUCTURE_HOOK`` is *not* expected: ``clips_per_window`` keeps the clip
    length at or under the declared hook.
    """
    codes = set()
    if not (row.min_seconds - 1e-9 <= SECONDS <= row.max_seconds + 1e-9):
        codes.add("STRUCTURE_DURATION")
    if row.interview_driven:
        codes.add("STRUCTURE_INTERVIEW")
    return codes


def structure_codes(problems: Sequence[str]) -> set:
    return {str(item).split(":", 1)[0] for item in problems}


# -------------------------------------------------------------------- fixtures


def make_source(path: Path, row: profiles.Profile, spec: str) -> str:
    proc = run_ff(
        [
            "-f", "lavfi",
            "-i",
            f"{spec}=size={row.width}x{row.height}:rate={num(row.fps)}:duration={SOURCE_SECONDS:g}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-pix_fmt", "yuv420p", "-an", str(path),
        ],
        loglevel="error",
    )
    if not proc.ok or not path.exists():
        raise RuntimeError(f"fixture {path.name} failed: {proc.tail()}")
    return str(path)


def build_sources(work: Path, row: profiles.Profile) -> List[str]:
    work.mkdir(parents=True, exist_ok=True)
    return [
        make_source(work / f"src{index}.mp4", row, spec)
        for index, spec in enumerate(("testsrc2", "smptebars"))
    ]


def placeholder_source(work: Path) -> str:
    """One real, tiny file, so a document compiles before any minute is rendered.

    ``compile_timeline`` refuses a document whose source is not on disk, and the
    always-on section must compile the six pieces of every document without
    encoding a minute. This stands in for the picture; the rendered run rebuilds
    the same document against the profile's own canvas-sized sources.
    """
    work.mkdir(parents=True, exist_ok=True)
    path = work / "placeholder.mp4"
    if not path.exists():
        proc = run_ff(
            ["-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=0.2",
             "-frames:v", "1", "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", "-an", str(path)],
            loglevel="error",
        )
        if not proc.ok or not path.exists():
            raise RuntimeError(f"placeholder fixture failed: {proc.tail()}")
    return str(path)


# ------------------------------------------------------- gated render pipeline


def join_and_sonorise(
    records: Sequence[Dict[str, Any]], destination: Path, row: profiles.Profile
) -> Tuple[Dict[str, Any], float]:
    """Join the chunks with a re-encode and lay the audio bed down, in one pass.

    ``-c copy`` is deliberately absent: the pieces are decoded off the concat
    demuxer and re-encoded at the profile's own clock, so the join cannot inherit
    a chunk's last frame twice or drop one, and the audio exists as a real stream
    for :mod:`vx.profile_master` to normalise. ffmpeg's own ``-benchmark`` line
    is read here too, so the join's peak RSS is measured like every chunk's.
    """
    list_path = destination.parent / "concat.txt"
    list_path.write_text(
        "\n".join(f"file '{_concat_path(Path(record['artifact']))}'" for record in records)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    started = time.monotonic()
    proc = run_ff(
        [
            "-benchmark",
            "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-f", "lavfi", "-i", AUDIO_BED,
            "-vf", "setsar=1",
            "-af", AUDIO_ENVELOPE,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            # The rate is resampled by the encoder, never by an ``fps`` filter:
            # the filter drops the last frame of a concat stream (measured: 899
            # of 900 frames on three ten-second pieces), the encoder's own CFR
            # grid keeps all of them.
            "-r", num(row.fps), "-fps_mode", "cfr",
            "-t", num(SECONDS), "-shortest", "-movflags", "+faststart",
            str(destination),
        ],
        loglevel="info",
        step="render",
    )
    elapsed = time.monotonic() - started
    if not proc.ok or not destination.exists() or destination.stat().st_size == 0:
        raise RuntimeError(f"join failed: {proc.tail()}")
    return (
        {
            "seconds": round(elapsed, 3),
            "maxrss_kib": edl3_render._bench_maxrss(proc.stderr),
            "command": [str(item) for item in proc.args],
            "sha256": stagecache.file_sha256(destination),
        },
        elapsed,
    )


def delivery_target(row: profiles.Profile) -> str:
    """The profile's first declared mp4 target this build can actually encode.

    A target is only ever one the profile declares. The mp4 one is preferred
    because it is the distribution deliverable; a mezzanine (ProRes, DNxHR at
    60 s of 2K) would cost a gigabyte to prove a codec already covered by the
    encoder probe. The report names the target, so nothing is hidden.
    """
    declared = deliver.targets(row)
    available = [
        key
        for key in declared
        if deliver.encoder_available(deliver.spec(key).encoder)
        and deliver.encoder_available(deliver.spec(key).audio_encoder)
    ]
    in_mp4 = [key for key in available if deliver.spec(key).family == "mp4"]
    chosen = (in_mp4 or available or declared)[0]
    return chosen


def image_size(path: Path) -> Tuple[int, int]:
    """The pixel size of a still, from ffprobe."""
    probe = run_fp(
        ["-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)]
    )
    text = (probe.stdout or "").strip().splitlines()
    parts = text[-1].split(",") if probe.ok and text else []
    if len(parts) < 2:
        return (0, 0)
    return (int(parts[0]), int(parts[1]))


def extract_frame(source: Path, destination: Path, at: float) -> Dict[str, Any]:
    proc = run_ff(
        [
            "-ss", num(at), "-i", str(source), "-frames:v", "1",
            "-f", "image2", "-c:v", "png", str(destination),
        ],
        loglevel="error",
    )
    if not proc.ok or not destination.exists():
        raise RuntimeError(f"proof frame failed: {proc.tail()}")
    width, height = image_size(destination)
    return {
        "frame": str(destination),
        "at_seconds": float(at),
        "sha256": stagecache.file_sha256(destination),
        "bytes": destination.stat().st_size,
        "width": width,
        "height": height,
    }


def measure_profile(
    row: profiles.Profile, entry: Dict[str, Any], work: Path
) -> Dict[str, Any]:
    """Render, master, deliver and prove one profile's minute."""
    started = time.monotonic()
    validated: edl3.Timeline3 = entry["timeline"]
    root = work / row.id
    root.mkdir(parents=True, exist_ok=True)
    sources = build_sources(root / "sources", row)
    document = build_document(row, sources)
    timeline = timeline_of(document)
    check(
        f"{row.id} the rendered document is the one that was validated",
        len(timeline.clips) == len(validated.clips)
        and abs(timeline.duration - validated.duration) <= 1e-6,
        f"{len(timeline.clips)} clips, {timeline.duration:.3f}s",
    )

    pieces = root / "pieces.mp4"
    render_started = time.monotonic()
    report = edl3_render.render(
        timeline,
        pieces,
        workdir=root / "chunk_wd",
        audio="drop",
        chunks=CHUNKS,
        benchmark=True,
    )
    render_seconds = time.monotonic() - render_started

    records = report["chunks"]["records"]
    check(
        f"{row.id} renders all {CHUNKS} pieces through the chunk path",
        report["chunks"]["rendered"] == list(range(CHUNKS)),
        report["chunks"]["rendered"],
    )
    check(
        f"{row.id} chunked render wrote no single graph for the minute",
        report.get("chunked") is True and report["chunks"]["count"] == CHUNKS,
        f"{report['chunks']['count']} x {WINDOW:g}s",
    )
    per_piece: Dict[int, int] = {}
    for clip in report["clips"]:
        per_piece[int(clip["chunk"])] = per_piece.get(int(clip["chunk"]), 0) + 1
    check(
        f"{row.id} no graph ever holds more than {clips_per_window(row)} clip inputs",
        sorted(per_piece.values()) == [clips_per_window(row)] * CHUNKS,
        sorted(per_piece.values()),
    )
    check(
        f"{row.id} every piece artifact is on disk with its recorded sha256",
        all(
            Path(record["artifact"]).exists()
            and stagecache.file_sha256(Path(record["artifact"])) == record["sha256"]
            for record in records
        ),
        [record["sha256"][:8] for record in records],
    )

    master_path = root / "master.mp4"
    join, _join_seconds = join_and_sonorise(records, master_path, row)
    check(
        f"{row.id} the join re-encodes two inputs and never stream-copies",
        join["command"].count("-i") == 2
        and "copy" not in join["command"]
        and "libx264" in join["command"],
        f"{join['command'].count('-i')} inputs, -c copy absent: {'copy' not in join['command']}",
    )
    measured = deliver_probe.probe(master_path)
    chunk_kib = [record["maxrss_kib"] for record in records]
    peaks = [value for value in chunk_kib if value] + (
        [join["maxrss_kib"]] if join["maxrss_kib"] else []
    )
    peak = max(peaks) if peaks else None

    check(
        f"{row.id} master is exactly {row.width}x{row.height}",
        (measured["width"], measured["height"]) == (row.width, row.height),
        f"{measured['width']}x{measured['height']}",
    )
    frames_expected = int(round(SECONDS * float(row.fps)))
    check(
        f"{row.id} master counts {frames_expected} frames +-2",
        abs(measured["frames"] - frames_expected) <= 2,
        measured["frames"],
    )
    check(
        f"{row.id} master runs 60 s +-0.1",
        abs(measured["duration"] - SECONDS) <= 0.1,
        f"{measured['duration']:.3f}s",
    )
    check(
        f"{row.id} master declares the profile's rate",
        abs(measured["fps"] - float(row.fps)) <= 1e-3,
        measured["fps"],
    )
    check(
        f"{row.id} master carries the audio the loudness law needs",
        measured["audio_streams"] == 1,
        measured["audio_codec"],
    )
    check(
        f"{row.id} every chunk reported its own maxrss",
        all(value for value in chunk_kib) and len(chunk_kib) == CHUNKS,
        chunk_kib,
    )
    check(
        f"{row.id} peak RSS stayed under {MEMORY_CEILING_MIB:g} MiB",
        peak is not None and peak / 1024.0 < MEMORY_CEILING_MIB,
        f"peak {mib(peak)} (D-12 measured 5848 MiB)",
    )

    mastered = root / "mastered.mp4"
    loud_started = time.monotonic()
    loudness = profile_master.master(master_path, row, mastered, root / "loudness")
    loud_seconds = time.monotonic() - loud_started
    fresh = qc.lufs_of(mastered)
    delta = abs(float(fresh.get("input_i", 0.0)) - float(row.loudness_lufs))
    check(
        f"{row.id} loudness lands within 0.5 LU of {row.loudness_lufs:g}",
        delta <= 0.5,
        f"{float(fresh.get('input_i', 0.0)):.3f} LUFS (delta {delta:.3f})",
    )
    check(
        f"{row.id} reported loudness is the fresh measurement",
        abs(float(loudness["output_lufs"]) - float(fresh.get("input_i", 0.0))) <= 0.01,
        f"report={loudness['output_lufs']} measured={float(fresh.get('input_i', 0.0)):.3f}",
    )
    mastered_measured = deliver_probe.probe(mastered)
    check(
        f"{row.id} mastered file keeps every frame",
        mastered_measured["frames"] == measured["frames"],
        mastered_measured["frames"],
    )

    target = delivery_target(row)
    recipe = deliver.spec(target)
    deliver_started = time.monotonic()
    delivery = deliver.deliver(mastered, row, target, root / "delivery", root / "deliver_wd")
    deliver_seconds = time.monotonic() - deliver_started
    check(
        f"{row.id} delivers the declared target {target}",
        target in deliver.targets(row) and delivery["target"] == target,
        target,
    )
    check(
        f"{row.id} delivered codec is the target's {recipe.codec}",
        delivery["video_codec"] == recipe.codec,
        delivery["video_codec"],
    )
    check(
        f"{row.id} delivered pix_fmt is the target's {recipe.pix_fmt}",
        delivery["pix_fmt"] == recipe.pix_fmt,
        delivery["pix_fmt"],
    )
    check(
        f"{row.id} delivered file is the profile's canvas",
        (delivery["width"], delivery["height"]) == (row.width, row.height),
        f"{delivery['width']}x{delivery['height']}",
    )
    check(
        f"{row.id} delivered file keeps the minute's frames +-2",
        abs(delivery["frames"] - frames_expected) <= 2,
        delivery["frames"],
    )

    proof = extract_frame(Path(delivery["path"]), REPORT_DIR / f"{row.id}.png", SECONDS / 2.0)
    check(
        f"{row.id} proof frame was extracted and hashed",
        Path(proof["frame"]).exists() and len(str(proof["sha256"])) == 64,
        f"{proof['sha256'][:16]}… ({proof['bytes']} B)",
    )

    return {
        "profile": row.id,
        "label": row.label,
        "gated": True,
        "canvas": f"{row.width}x{row.height}",
        "fps": float(row.fps),
        "declared": {
            "min_seconds": row.min_seconds,
            "max_seconds": row.max_seconds,
            "cut_density_min": row.cut_density_min,
            "cut_density_max": row.cut_density_max,
            "loudness_lufs": row.loudness_lufs,
            "true_peak_db": row.true_peak_db,
            "deliverables": list(row.deliverables),
        },
        "document": document_summary(timeline),
        "length": {
            "declared_min_seconds": row.min_seconds,
            "declared_max_seconds": row.max_seconds,
            "measured_seconds": SECONDS,
            "inside_declared_window": row.min_seconds - 1e-9
            <= SECONDS
            <= row.max_seconds + 1e-9,
            "note": (
                "60 s is this task's minute; the profile's window starts at "
                f"{row.min_seconds:g} s and the render was not stretched to fit it"
                if SECONDS < row.min_seconds - 1e-9
                else "60 s sits inside the declared window"
            ),
        },
        "chunks": {
            "path": "vx.edl3_chunks via edl3_render.render(chunks=6)",
            "count": CHUNKS,
            "window_seconds": WINDOW,
            "rendered": report["chunks"]["rendered"],
            "reused": report["chunks"]["reused"],
            "sha256": [record["sha256"] for record in records],
            "copy_join_sha256": report["chunks"]["output_sha256"],
            "directory": report["chunks"]["directory"],
        },
        "memory": {
            "source": "ffmpeg -benchmark, bench: maxrss=... KiB (one per chunk + the join)",
            "per_chunk_kib": chunk_kib,
            "join_kib": join["maxrss_kib"],
            "peak_maxrss_kib": peak,
            "peak_maxrss_mib": None if peak is None else round(peak / 1024.0, 1),
            "ceiling_mib": MEMORY_CEILING_MIB,
            "d12_reference_mib": 5848.0,
        },
        "master": {
            "path": str(master_path),
            "bytes": master_path.stat().st_size,
            "render_seconds": round(render_seconds, 3),
            "join_seconds": join["seconds"],
            "join_sha256": join["sha256"],
            "measured": {
                "width": measured["width"],
                "height": measured["height"],
                "frames": measured["frames"],
                "duration": measured["duration"],
            },
        },
        "loudness": {
            "target_lufs": row.loudness_lufs,
            "measured_lufs": round(float(fresh.get("input_i", 0.0)), 3),
            "delta_lu": round(delta, 3),
            "measured_true_peak_db": round(float(fresh.get("input_tp", 0.0)), 3),
            "report_output_lufs": loudness["output_lufs"],
            "applied_gain_db": loudness["applied_gain_db"],
            "master_seconds": round(loud_seconds, 3),
        },
        "delivery": {
            "target": target,
            "path": delivery["path"],
            "bytes": delivery["bytes"],
            "video_codec": delivery["video_codec"],
            "pix_fmt": delivery["pix_fmt"],
            "container": delivery["container"],
            "width": delivery["width"],
            "height": delivery["height"],
            "frames": delivery["frames"],
            "duration": delivery["duration"],
            "loudness_lufs": delivery["loudness_lufs"],
            "deliver_seconds": round(deliver_seconds, 3),
        },
        "proof": proof,
        "structure": {"problems": list(structure.check_structure(timeline, row))},
        "wall_seconds": round(time.monotonic() - started, 3),
    }


def document_summary(timeline: edl3.Timeline3) -> Dict[str, Any]:
    cuts = cuts_of(timeline)
    span = max((float(clip.dst_out) for clip in timeline.clips), default=0.0)
    looks = look_windows(timeline)
    return {
        "seconds": round(span, 6),
        "frames_expected": int(round(span * float(timeline.fps))),
        "clips": len(timeline.clips),
        "cuts": cuts,
        "clip_seconds": round(span / max(1, len(timeline.clips)), 6),
        "clips_per_10s": round(len(timeline.clips) * WINDOW / span, 3) if span else 0.0,
        "cut_density_per_10s": round(cuts * WINDOW / span, 3) if span else 0.0,
        "looks": sum(count for _start, count in looks),
        "looks_worst_window": worst_look_window(looks),
        "looks_cap": LOOKS_CAP,
    }


# ------------------------------------------------------------------- sections


def section_documents(
    rows: Sequence[profiles.Profile], probe_source: str
) -> Dict[str, Dict[str, Any]]:
    """Build and validate every document. No frame is encoded here."""
    print("\n[a] nine documents built to their own declared grammar (no frame encoded)")
    built: Dict[str, Dict[str, Any]] = {}
    print(
        f"    {'profile':13} {'window':14} {'seconds':8} {'clips':6} {'cuts/10s':9} "
        f"{'band':9} {'looks':6} {'k':3}"
    )
    for row in rows:
        document = build_document(row, [probe_source, probe_source])
        timeline = timeline_of(document)
        summary = document_summary(timeline)
        per_window = clips_per_window(row)
        built[row.id] = {"document": document, "timeline": timeline, "summary": summary}

        problems = timeline.validate()
        structure_problems = structure.check_structure(timeline, row)
        density = summary["cut_density_per_10s"]
        looks = summary["looks_worst_window"]
        band = f"{row.cut_density_min:g}-{row.cut_density_max:g}"
        print(
            f"    {row.id:13} {f'{row.min_seconds:g}-{row.max_seconds:g}s':<14}"
            f"{summary['seconds']:8.3f}{summary['clips']:7d}{density:10.3f}"
            f" {band:9}{looks:7d}{per_window:4d}"
        )

        check(
            f"{row.id} document is exactly 60 s",
            abs(summary["seconds"] - SECONDS) <= 1e-6,
            f"{summary['seconds']:.6f}s",
        )
        check(
            f"{row.id} document declares the profile's canvas and rate",
            (timeline.width, timeline.height) == (row.width, row.height)
            and abs(float(timeline.fps) - float(row.fps)) <= 1e-9,
            f"{timeline.width}x{timeline.height}@{float(row.fps):g}",
        )
        check(
            f"{row.id} clip count is {per_window} per 10 s x {CHUNKS} pieces",
            len(timeline.clips) == CHUNKS * per_window,
            f"{len(timeline.clips)} clips",
        )
        check(
            f"{row.id} cut density is inside {band} per 10 s",
            row.cut_density_min - 1e-9 <= density <= row.cut_density_max + 1e-9,
            f"{density:.3f} over {summary['cuts']} cuts",
        )
        check(
            f"{row.id} the other reading of density is inside the band too",
            row.cut_density_min - 1e-9
            <= summary["clips_per_10s"]
            <= row.cut_density_max + 1e-9,
            f"{summary['clips_per_10s']:.3f} clips per 10 s",
        )
        check(
            f"{row.id} looks stay at or under {LOOKS_CAP} per 10 s",
            0 < looks <= LOOKS_CAP,
            f"worst window holds {looks}",
        )
        check(
            f"{row.id} the effect-density rule accepts the document",
            edl3.check_effect_density(timeline) == [],
            edl3.check_effect_density(timeline)[:1],
        )
        check(
            f"{row.id} validates with no problem",
            problems == [],
            problems[:2],
        )
        check(
            f"{row.id} the only structure codes are the ones 60 s forces",
            structure_codes(structure_problems) == expected_structure_codes(row),
            sorted(structure_codes(structure_problems)),
        )
        check(
            f"{row.id} 60 s never exceeds the declared maximum",
            SECONDS <= row.max_seconds + 1e-9,
            "60s in "
            + f"{row.min_seconds:g}-{row.max_seconds:g}s"
            + (
                ""
                if row.min_seconds <= SECONDS
                else " (below the declared minimum: reported, not stretched)"
            ),
        )

        windows = edl3_slice.plan_windows(timeline, CHUNKS)
        check(
            f"{row.id} the chunk path splits it into {CHUNKS} ten-second pieces",
            len(windows) == CHUNKS
            and all(abs((window["t1"] - window["t0"]) - WINDOW) <= 1e-6 for window in windows),
            [round(window["t1"] - window["t0"], 3) for window in windows],
        )
        compiled = 0
        for window in windows:
            piece = edl3.Timeline3.from_dict(edl3_slice.document(timeline, window))
            plan = edl3_render.compile_timeline(piece, audio="drop")
            compiled += 1 if plan["duration"] > 0 else 0
        check(
            f"{row.id} all {CHUNKS} pieces compile as their own documents",
            compiled == CHUNKS,
            compiled,
        )
    return built


def section_render(
    rows: Sequence[profiles.Profile], built: Dict[str, Dict[str, Any]], work: Path
) -> None:
    """Render, master, deliver and prove every profile. Gate: VX_LONGRUN=1."""
    print("\n[b] one rendered minute per profile, through the chunk path")
    if not LONGRUN:
        print("  " + "=" * 74)
        skip(
            "LONGRUN heavy render",
            "gate VX_LONGRUN=1 is NOT set: NOTHING WAS RENDERED, NOTHING WAS MEASURED",
        )
        print("  " + "!" * 74)
        print("  !! THIS IS A SKIP, NOT A PASS.")
        print("  !! NO PROFILE WAS RENDERED, NO MASTER, NO DELIVERY AND NO FRAME WAS")
        print("  !! PRODUCED: the nine reports under")
        print(f"  !! {REPORT_DIR}")
        print("  !! were NOT written by this run. Run with VX_LONGRUN=1 for the real")
        print("  !! minutes, the ffprobe numbers and ffmpeg's own maxrss lines.")
        print("  " + "!" * 74)
        MEASURED["render"] = {"gated": "VX_LONGRUN not set", "skipped": True}
        return

    for row in rows:
        print(f"\n  {row.id}: {row.label} — 60 s of {row.width}x{row.height}@{row.fps:g}")
        result = measure_profile(row, built[row.id], work)
        result["structure"]["expected_codes"] = sorted(expected_structure_codes(row))
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORT_DIR / f"{row.id}.json"
        path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        check(
            f"{row.id} JSON report was written",
            path.is_file() and json.loads(path.read_text("utf-8"))["profile"] == row.id,
            path.name,
        )
        check(
            f"{row.id} report carries every column of the summary table",
            all(
                key in result
                for key in ("canvas", "fps", "document", "loudness", "delivery", "proof", "memory")
            ),
            sorted(result)[:6],
        )
        MEASURED["profiles"][row.id] = {
            "canvas": result["canvas"],
            "fps": result["fps"],
            "frames": result["master"]["measured"]["frames"],
            "seconds": result["master"]["measured"]["duration"],
            "delta_lu": result["loudness"]["delta_lu"],
            "delivery": f"{result['delivery']['target']} {result['delivery']['video_codec']}/{result['delivery']['pix_fmt']}",
            "frame_sha256": result["proof"]["sha256"],
            "peak_maxrss_mib": result["memory"]["peak_maxrss_mib"],
            "wall_seconds": result["wall_seconds"],
        }


def section_table(rows: Sequence[profiles.Profile]) -> None:
    """The summary table, from measurements when the gate was open."""
    print("\n[c] summary")
    header = (
        f"  {'profile':13} {'canvas':11} {'fps':5} {'frames':7} {'seconds':8} "
        f"{'dLU':6} {'delivery':24} {'frame sha256':18} {'peak maxrss':11}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    rendered = MEASURED.get("profiles") or {}
    ok_rows = 0
    for row in rows:
        got = rendered.get(row.id)
        if not got:
            print(
                f"  {row.id:13} {row.width}x{row.height:<6} {row.fps:<5g} "
                f"{'--':7} {'--':8} {'--':6} {'NOT RENDERED':24} {'--':18} {'--':11}"
            )
            continue
        ok_rows += 1
        print(
            f"  {row.id:13} {got['canvas']:11} {got['fps']:<5g} {got['frames']:<7d} "
            f"{got['seconds']:<8.3f} {got['delta_lu']:<6.3f} {got['delivery']:24} "
            f"{got['frame_sha256'][:16]:18} {str(got['peak_maxrss_mib']) + ' MiB':11}"
        )
    check(
        "the summary table carries every profile the run measured",
        ok_rows == len(rows) if LONGRUN else ok_rows == 0,
        f"{ok_rows} of {len(rows)} profile(s) measured",
    )
    if not LONGRUN:
        print("\n  " + "!" * 74)
        print("  !! NOTHING WAS RENDERED. THIS RUN IS NOT A PASS: it validated nine")
        print("  !! documents and measured no frame, no loudness, no delivery, no RSS.")
        print("  " + "!" * 74)


# ---------------------------------------------------------------------- main


def main() -> int:
    print(
        "LONGRUN GATE: VX_LONGRUN="
        + ("1 (enabled: the real minutes are rendered and measured)" if LONGRUN else "not set (nothing is rendered)")
    )
    print(f"REPORT DIR: {REPORT_DIR}")
    rows = [profiles.resolve(name) for name in profiles.names()]
    if ONLY:
        rows = [row for row in rows if row.id in ONLY]
        print(f"ONLY: {', '.join(row.id for row in rows)} (a subset is not a full pass)")

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vx_profile_minute_") as tmp:
        work = Path(tmp)
        try:
            built = section_documents(rows, placeholder_source(work / "probe"))
            section_render(rows, built, work)
            section_table(rows)
        except Exception:  # noqa: BLE001 - a crash is a failure, never a skip
            traceback.print_exc()
            print("\nharness crashed before every profile was measured")
            return 1
        total = time.monotonic() - started

    print("\n" + "=" * 78)
    print("F26-T08 MEASURED")
    print("=" * 78)
    profiles_seen = MEASURED.get("profiles") or {}
    peaks = [
        item["peak_maxrss_mib"]
        for item in profiles_seen.values()
        if item.get("peak_maxrss_mib") is not None
    ]
    print(f"  profiles measured: {len(profiles_seen)} of {len(rows)}")
    print(f"  total wall time  : {total:.1f}s")
    print(f"  peak maxrss      : {max(peaks) if peaks else 'n/a (nothing rendered)'} MiB")
    print(f"  D-12 reference   : 5848.0 MiB in one graph, no frame in 11 minutes")
    print(f"  reports          : {REPORT_DIR}")
    print("-" * 78)
    gate = "ENABLED" if LONGRUN else "DISABLED (VX_LONGRUN not set) -> nothing was rendered"
    print(f"  LONGRUN gate: {gate}")
    print(f"  {PASS} passed, {FAIL} failed, {SKIP} skipped, {total:.1f}s")
    if FAIL:
        print("FAIL")
    elif SKIP:
        print("NO FAILURE — but the render measured nothing: NOT a full pass")
    else:
        print("OK")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
