"""vx.edl3_chunks — resumable chunked rendering of a ``vx.edl3`` document.

``edl3_render.render`` compiles the whole document into one filtergraph and runs
one ffmpeg process, so a crash at minute 98 of a two-hour render throws away
everything before it. This module is the resumable path. It is opt-in
(``render(..., chunks=N)``), which is why a document that does not ask for it
still renders through the single-pass graph, byte for byte.

Per chunk, and nothing else:

* the piece is planned and sliced by :mod:`vx.edl3_slice` (whole clips only, so
  no effect, keyframe or transition is re-timed);
* it is compiled and encoded by its own ffmpeg process into
  ``<workdir>/chunks/<index>/``;
* it is recorded through :mod:`vx.stagecache` — the existing content-addressed
  store, not a second checkpoint format — keyed on the sub-document hash, the
  content hash of every source that piece reads, the chunk geometry and the
  encode settings. The entry carries the artifact path, its size and its sha256,
  so a deleted, truncated or overwritten chunk is a miss and never a false hit;
  the chunk index and both hashes are also kept in its ``meta``;
* a re-run looks every chunk up first and re-encodes only the misses. Every chunk
  is still *compiled*, so a resumed run reports exactly what an uninterrupted run
  reports about each chunk;
* the pieces are joined by the concat demuxer in stream copy. The join is
  byte-deterministic — identical chunk bytes produce an identical file — which is
  what makes "resume equals uninterrupted" a measurement instead of a promise.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from . import ffmpeg, stagecache
from .edl3 import Timeline3
from .edl3_slice import canonical, document, plan_windows, sha256, source_hashes

#: Cache stage name. The key covers the (sub-document hash, source hashes, chunk
#: geometry, encode settings) tuple, so bumping this string is what invalidates
#: every recorded chunk when the emitted encoder flags change — the same contract
#: as ``render.STAGE_VERSION``.
CHUNK_STAGE = "edl3_render_chunk"
CHUNK_VERSION = "1"
CHUNK_DIR = "chunks"
CHUNK_PREFIX = "chunk_"
CONCAT_LIST = "concat.txt"


@dataclass(frozen=True)
class Run:
    """Everything one chunked render needs that is the same for every chunk."""

    timeline: Timeline3
    destination: Path
    root: Path
    chunk_dir: Path
    audio: str
    settings: Mapping[str, Any]
    timeout: Optional[int]
    benchmark: bool
    total: int
    #: Source content hashes, shared across chunks so a file is read once.
    memo: Dict[str, str]


def _concat_path(path: Path) -> str:
    """A concat-demuxer path: POSIX separators, single quotes escaped."""
    return path.resolve().as_posix().replace("'", "'\\''")


def _render_one(run: Run, window: Mapping[str, Any]) -> Dict[str, Any]:
    """Encode ``window`` unless its inputs still match, and return its record."""
    from . import edl3_render  # noqa: PLC0415 - render-only, avoids the cycle

    sub_document = document(run.timeline, window)
    sources = source_hashes(sub_document["clips"], run.memo)
    document_sha = sha256(canonical(sub_document))
    index = int(window["index"])
    digest = stagecache.key(
        CHUNK_STAGE,
        {
            "document": document_sha,
            "sources": sha256(canonical(sources)),
        },
        {
            "chunk": index,
            "chunks": run.total,
            "t0": float(window["t0"]),
            "t1": float(window["t1"]),
            "audio": run.audio,
            "fps": float(run.timeline.fps),
            "size": [run.timeline.width, run.timeline.height],
            "settings": dict(run.settings),
        },
        CHUNK_VERSION,
    )
    plan = edl3_render.compile_timeline(
        Timeline3.from_dict(sub_document), audio=run.audio, settings=run.settings
    )
    artifact = run.chunk_dir / f"{index:03d}" / f"{CHUNK_PREFIX}{index:03d}.mp4"
    record: Dict[str, Any] = {
        "chunk": index,
        "t0": float(window["t0"]),
        "t1": float(window["t1"]),
        "clips": len(window["clips"]),
        "key": digest,
        "document_sha256": document_sha,
        "source_sha256": sources,
    }
    hit = stagecache.lookup(run.root, digest)
    if hit is not None:
        record.update(
            {
                "state": "reused",
                "artifact": hit["path"],
                "size": hit["size"],
                "sha256": hit["sha256"],
                "recorded": True,
                "maxrss_kib": None,
                "plan": plan,
            }
        )
        return record
    artifact.parent.mkdir(parents=True, exist_ok=True)
    proc, _script = edl3_render._encode(
        plan, artifact, artifact.parent, run.timeout, benchmark=run.benchmark
    )
    checksum = stagecache.file_sha256(artifact)
    recorded = stagecache.record(
        run.root,
        digest,
        artifact,
        {
            "chunk": index,
            "chunks": run.total,
            "t0": float(window["t0"]),
            "t1": float(window["t1"]),
            "document_sha256": document_sha,
            "source_sha256": sources,
            "fps": float(run.timeline.fps),
            "size": [run.timeline.width, run.timeline.height],
            "audio": run.audio,
            "sha256": checksum,
        },
    )
    record.update(
        {
            "state": "rendered",
            "artifact": str(artifact),
            "size": artifact.stat().st_size,
            "sha256": checksum,
            "recorded": bool(recorded),
            "maxrss_kib": edl3_render._bench_maxrss(proc.stderr),
            "plan": plan,
            "command": proc.args,
        }
    )
    return record


def _join(run: Run, paths: List[Path]) -> Any:
    """Join the chunk artifacts with the concat demuxer; return its ``Proc``."""
    list_path = run.chunk_dir / CONCAT_LIST
    list_path.write_text(
        "\n".join(f"file '{_concat_path(path)}'" for path in paths) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    args = ["-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy"]
    if run.benchmark:
        args.insert(0, "-benchmark")
    proc = ffmpeg.run_ff(
        args + [str(run.destination)],
        loglevel="info" if run.benchmark else "error",
        timeout=run.timeout,
        step="render",
    )
    if not proc.ok:
        from .edl3_render import CompileError  # noqa: PLC0415 - failure path only

        raise CompileError("RENDER_FAILED", proc.tail(), str(run.destination))
    if not run.destination.exists() or run.destination.stat().st_size == 0:
        from .edl3_render import CompileError  # noqa: PLC0415 - failure path only

        raise CompileError(
            "RENDER_FAILED",
            "the concat demuxer reported success but wrote no output",
            str(run.destination),
        )
    return proc


def render_chunked(
    timeline: Timeline3,
    output: Any,
    *,
    workdir: Any,
    timeout: Optional[int] = None,
    audio: str = "refuse",
    settings: Optional[Mapping[str, Any]] = None,
    chunks: int = 2,
    benchmark: bool = False,
    jobs: int = 1,
) -> Dict[str, Any]:
    """Render ``timeline`` as ``chunks`` resumable pieces, then join them."""
    from . import edl3_render  # noqa: PLC0415 - render-only, avoids the cycle

    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    root = Path(workdir)
    chunk_dir = root / CHUNK_DIR
    chunk_dir.mkdir(parents=True, exist_ok=True)
    joins = timeline.crossfade_joins() if hasattr(timeline, "crossfade_joins") else []
    if joins:
        raise edl3_render.CompileError(
            "CHUNK_UNSUPPORTED",
            "the document declares a crossfade, and a chunk boundary would cut it in "
            "half: render it whole, or remove the dissolves",
            "chunks",
        )
    windows = plan_windows(timeline, chunks)
    run = Run(
        timeline=timeline,
        destination=destination,
        root=root,
        chunk_dir=chunk_dir,
        audio=audio,
        settings=settings or {},
        timeout=timeout,
        benchmark=bool(benchmark),
        total=len(windows),
        memo={},
    )

    # A chunk is an independent ffmpeg process writing into its own folder, and
    # the stage cache records each chunk atomically (temp file, then os.replace),
    # so the windows can overlap without touching each other. `map` keeps the
    # order of `windows`, which is what the join and the report depend on.
    workers = max(1, min(int(jobs), len(windows)))
    if workers == 1:
        records = [_render_one(run, window) for window in windows]
    else:
        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415 - only here

        with ThreadPoolExecutor(max_workers=workers) as pool:
            records = list(pool.map(lambda window: _render_one(run, window), windows))
    proc = _join(run, [Path(record["artifact"]) for record in records])

    clips: List[Dict[str, Any]] = []
    backends: List[Dict[str, Any]] = []
    warnings: List[str] = []
    scripts: List[str] = []
    commands: List[List[str]] = []
    chromium: Dict[str, Any] = {
        "available": bool(run.settings.get("chromium_available")),
        "qualified": 0,
        "redirected": 0,
        "refused": 0,
        "realized_by_generator": 0,
    }
    overlays = 0
    for record in records:
        plan = record["plan"]
        clips.extend({**row, "chunk": record["chunk"]} for row in plan["clips"])
        backends.extend({**row, "chunk": record["chunk"]} for row in plan["backends"])
        for name, value in plan["chromium"].items():
            if isinstance(value, int) and not isinstance(value, bool):
                chromium[name] = int(chromium.get(name, 0)) + value
        warnings.extend(
            f"chunk {record['chunk']:03d}: {text}" for text in plan["warnings"]
        )
        overlays += int(plan["overlays"])
        scripts.append(
            f"# chunk {record['chunk']:03d} {record['t0']:.3f}s"
            f"..{record['t1']:.3f}s\n{plan['filter_script']}"
        )
        if "command" in record:
            commands.append(record["command"])

    return {
        "version": timeline.version,
        "backend": edl3_render.choose_backend(timeline),
        "audio_policy": audio,
        "output": str(destination.resolve()),
        "output_size": destination.stat().st_size,
        "duration": timeline.duration,
        "fps": timeline.fps,
        "size": [timeline.width, timeline.height],
        "color": edl3_render.delivery_colour(timeline.profile_ref),
        "chunked": True,
        "chunks": {
            "stage": CHUNK_STAGE,
            "version": CHUNK_VERSION,
            "count": len(windows),
            "rendered": [r["chunk"] for r in records if r["state"] == "rendered"],
            "reused": [r["chunk"] for r in records if r["state"] == "reused"],
            "records": [{k: v for k, v in r.items() if k != "plan"} for r in records],
            "directory": str(chunk_dir.resolve()),
            "concat_list": str((chunk_dir / CONCAT_LIST).resolve()),
            "output_sha256": stagecache.file_sha256(destination),
        },
        "clips": clips,
        "backends": backends,
        "chromium": chromium,
        "overlays": overlays,
        "warnings": warnings,
        "filter_script": "\n".join(scripts),
        "filter_script_path": str(chunk_dir.resolve()),
        "command": proc.args,
        "chunk_commands": commands,
        "benchmark": {
            "chunks_kib": [r["maxrss_kib"] for r in records if r["maxrss_kib"]],
            "concat_kib": edl3_render._bench_maxrss(proc.stderr)
            if benchmark
            else None,
        },
        "stderr_tail": proc.tail(5),
    }
