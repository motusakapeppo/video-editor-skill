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

import json
import math
import os
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from . import ask, decisions, ffmpeg, stagecache
from .edl3 import Timeline3
from .edl3_render import AUDIO_BITRATE, AUDIO_RATE
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
REPORT_NAME = "report.json"

#: Where ``--live`` publishes the pieces a player can already watch.
LIVE_DIR = "live"
LIVE_MANIFEST = "manifest.json"
LIVE_SCHEMA = "vx.live/1"
LIVE_CHUNK_SECONDS = 4.0
LIVE_MIN_CHUNKS = 2
LIVE_MAX_CHUNKS = 40


def live_chunks(duration: float, *, clips: Optional[int] = None) -> int:
    """A piece every ~4 s of output, clamped to 2..40 and to the clip count.

    A chunk boundary is a clip boundary, so asking for more chunks than there are
    layer-0 clips can only be refused by :func:`edl3_slice.plan_windows`; with
    ``clips`` given the count is lowered to what the document can actually tile.
    """
    seconds = max(0.0, float(duration or 0.0))
    count = (
        int(math.ceil(seconds / LIVE_CHUNK_SECONDS))
        if seconds > 0.0
        else LIVE_MIN_CHUNKS
    )
    count = max(LIVE_MIN_CHUNKS, min(LIVE_MAX_CHUNKS, count))
    if clips is not None and int(clips) >= LIVE_MIN_CHUNKS:
        count = min(count, int(clips))
    return count


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


#: Il canale domande/decisioni del percorso a chunk. Una domanda sola, quella
#: che cambia il master (i sottotitoli); la scelta dei kind di transizione e'
#: una decisione presa dal documento, ma va scritta nel report con la prova.
SUBTITLE_ID = "subtitles"
SUBTITLE_QUESTION = "Vuoi i sottotitoli nel master?"
SUBTITLE_OPTIONS: List[Dict[str, str]] = [
    {"value": "no", "label": "No, senza sottotitoli"},
    {"value": "yes", "label": "Si', aggiungi i sottotitoli"},
]
SUBTITLE_DEFAULT = "no"


def _read_record(path: Path) -> Dict[str, Any]:
    """Un oggetto JSON, o ``{}``: troncato, assente e illeggibile sono uguali."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_record(path: Path, payload: Mapping[str, Any]) -> None:
    """Scrittura atomica, come ``ask``: la vista vede il file intero o niente."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _open_subtitles(root: Path, timeline: Timeline3, windows: List[Mapping[str, Any]]) -> None:
    """Apre la domanda sui sottotitoli nella workdir, senza fermare il render.

    ``ask.ask`` blocca; qui la domanda deve essere visibile *mentre* si lavora,
    quindi si scrive il file e si va avanti. Se una risposta c'e' gia' (un run
    precedente, o l'operatore ha risposto prima del render) non si riapre nulla.
    Fail-soft: una cartella non scrivibile lascia solo il default nel report.
    """
    if _read_record(ask.answer_path(root, SUBTITLE_ID)):
        return
    if ask.question_path(root, SUBTITLE_ID).is_file():
        return
    try:
        _write_record(
            ask.question_path(root, SUBTITLE_ID),
            {
                "id": SUBTITLE_ID,
                "question": SUBTITLE_QUESTION,
                "options": ask.normalize_options(SUBTITLE_OPTIONS),
                "default": SUBTITLE_DEFAULT,
                "created_utc": _utc_now(),
                "context": {
                    "job": "edl3 render",
                    "duration": round(float(timeline.duration), 3),
                    "chunks": len(windows),
                    "fps": float(timeline.fps),
                },
            },
        )
    except OSError:
        return


def _subtitle_choice(root: Path) -> tuple:
    """``(value, by)`` per la decisione: risposta dell'operatore o default."""
    record = _read_record(ask.answer_path(root, SUBTITLE_ID))
    value = str(record.get("value") or "")
    allowed = {item["value"] for item in SUBTITLE_OPTIONS}
    if value in allowed:
        return value, "user"
    return SUBTITLE_DEFAULT, "auto"


def _transition_kinds(timeline: Timeline3) -> Dict[str, int]:
    """Ogni kind dichiarato e quante volte compare nel documento."""
    found: Dict[str, int] = {}
    for clip in timeline.clips:
        for transition in (clip.transition_in, clip.transition_out):
            if transition is None:
                continue
            kind = str(transition.kind)
            if kind == "crossfade":
                kind = f"crossfade:{transition.style}"
            found[kind] = found.get(kind, 0) + 1
    return found


def _decisions_for(
    timeline: Timeline3,
    root: Path,
    windows: Sequence[Mapping[str, Any]],
    *,
    subtitle: str,
    by: str,
) -> List[Dict[str, Any]]:
    """Le due voci scritte nel report: sottotitoli e kind di transizione.

    ``decisions.record`` rifiuta una voce senza prova: l'evidenza porta i numeri
    veri (risposte, join, secondi) e ``falsified_if`` dice cosa la smentirebbe.
    """
    joins = (
        list(timeline.crossfade_joins())
        if hasattr(timeline, "crossfade_joins")
        else []
    )
    replayed = sum(float(window.get("overlap_in") or 0.0) for window in windows)
    base_count = sum(1 for clip in timeline.clips if int(clip.layer) == 0)
    answers = 1 if by == "user" else 0
    subtitle_evidence = [
        f"risposta dell'operatore nella vista: {subtitle!r}" if by == "user"
        else f"nessuna risposta: vale il default {SUBTITLE_DEFAULT!r}",
        f"{answers} risposta(e) su 1 domanda aperta in {root.name!r}",
    ]
    kinds = _transition_kinds(timeline)
    listed = ", ".join(f"{kind} x{count}" for kind, count in sorted(kinds.items()))
    transition_evidence = [
        f"{len(joins)} dissolvenza/e, {len(kinds)} kind dichiarati"
        + (f": {listed}" if listed else " (nessuna transizione)"),
        f"{base_count} clip di base, {len(windows)} chunk, "
        f"{replayed:.3f} s di dissolvenza ripetuti",
    ]
    entries: List[Dict[str, Any]] = []
    entries.append(
        decisions.record(
            id=SUBTITLE_ID,
            question=SUBTITLE_QUESTION,
            chosen=subtitle,
            evidence=subtitle_evidence,
            falsified_if="il master mostra sottotitoli non richiesti, o ne manca uno approvato",
            by=by,
            alternatives=[
                {
                    "option": "yes" if subtitle == "no" else "no",
                    "why_not": "la scelta scritta nella vista prevale sull'alternativa",
                }
            ],
        )
    )
    entries.append(
        decisions.record(
            id="transition_kinds",
            question="Quali kind di transizione rende il documento?",
            chosen=listed or "nessuna transizione",
            evidence=transition_evidence,
            falsified_if="un kind dichiarato non compare nel grafo, o ne compare uno non dichiarato",
            by="auto",
            alternatives=[
                {
                    "option": "nessuna transizione",
                    "why_not": (
                        f"{len(joins)} join dichiarati: toglierle cambierebbe la richiesta"
                        if joins
                        else "il documento non ne dichiara nessuna"
                    ),
                }
            ],
        )
    )
    return entries


class LivePublisher:
    """Publish finished chunks as they land, with one atomically replaced manifest.

    The manifest is written before the first chunk (every planned piece is listed
    with ``ready: false``) and re-written after each piece is copied into the live
    directory. Both the copy and the manifest use a temp file plus ``os.replace``,
    so a reader sees either the old file or the new one, never a half-written one.
    """

    def __init__(
        self,
        directory: Any,
        windows: List[Mapping[str, Any]],
        *,
        t0: Optional[str] = None,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / LIVE_MANIFEST
        self.t0 = t0 or _utc_now()
        self._lock = threading.Lock()
        self._entries: List[Dict[str, Any]] = [
            {
                "index": int(window["index"]),
                "file": f"{CHUNK_PREFIX}{int(window['index']):03d}.mp4",
                "t_in": float(window["t0"]),
                "t_out": float(window["t1"]),
                "bytes": 0,
                "sha256": "",
                "ready": False,
            }
            for window in windows
        ]
        self._write()

    def _payload(self) -> Dict[str, Any]:
        return {
            "schema": LIVE_SCHEMA,
            "chunks": [dict(entry) for entry in self._entries],
            "total": len(self._entries),
            "ready": sum(1 for entry in self._entries if entry["ready"]),
            "t0": self.t0,
            "updated": _utc_now(),
        }

    def _write(self) -> None:
        payload = self._payload()
        temp = self.path.with_name(
            f"{self.path.name}.tmp{os.getpid()}.{threading.get_ident()}"
        )
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temp, self.path)

    def publish(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        """Copy one finished chunk in and flip its manifest entry to ready."""
        index = int(record["chunk"])
        source = Path(str(record["artifact"]))
        target = self.directory / f"{CHUNK_PREFIX}{index:03d}.mp4"
        temp = target.with_name(
            f"{target.name}.part{os.getpid()}.{threading.get_ident()}"
        )
        shutil.copyfile(source, temp)
        os.replace(temp, target)
        digest = stagecache.file_sha256(target)
        size = target.stat().st_size
        with self._lock:
            for entry in self._entries:
                if int(entry["index"]) != index:
                    continue
                entry.update(
                    {
                        "file": target.name,
                        "t_in": float(record["t0"]),
                        "t_out": float(record["t1"]),
                        "bytes": int(size),
                        "sha256": digest,
                        "ready": True,
                    }
                )
                break
            self._write()
            return dict(self._payload())

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            payload = self._payload()
        return {
            "schema": LIVE_SCHEMA,
            "directory": str(self.directory.resolve()),
            "manifest": str(self.path.resolve()),
            "total": payload["total"],
            "ready": payload["ready"],
            "t0": payload["t0"],
        }


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
    #: ``EFFECT_DENSITY`` budget forwarded to every chunk's validation; ``None``
    #: keeps the declared default (see :data:`vx.edl3.EFFECT_DENSITY_LIMIT`).
    effect_budget: Optional[int] = None


def _concat_path(path: Path) -> str:
    """A concat-demuxer path: POSIX separators, single quotes escaped."""
    return path.resolve().as_posix().replace("'", "'\\''")


def _render_one(
    run: Run,
    window: Mapping[str, Any],
    on_ready: Optional[Callable[[Mapping[str, Any]], None]] = None,
) -> Dict[str, Any]:
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
        Timeline3.from_dict(sub_document),
        audio=run.audio,
        settings=run.settings,
        effect_budget=run.effect_budget,
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
        if on_ready is not None:
            on_ready(record)
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
    if on_ready is not None:
        on_ready(record)
    return record


def _join(run: Run, paths: List[Path]) -> Any:
    """Join the chunk artifacts; return the ``Proc`` that wrote the master.

    Picture is concatenated by stream copy. Audio cannot be: an AAC chunk starts
    with its encoder's priming frame, and the concat demuxer keeps one per file,
    so a copied join is a whole frame longer than the master and drifts at every
    boundary. The join therefore concatenates the decoded chunk audio with the
    ``concat`` filter and encodes it once, which is what makes the pieces add up
    to the master sample for sample.
    """
    list_path = run.chunk_dir / CONCAT_LIST
    list_path.write_text(
        "\n".join(f"file '{_concat_path(path)}'" for path in paths) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    has_audio = _carries_audio(paths)
    video_path = run.chunk_dir / "join_video.mp4" if has_audio else run.destination
    args = ["-f", "concat", "-safe", "0", "-i", str(list_path)]
    if has_audio:
        args += ["-map", "0:v:0", "-c:v", "copy", "-an", str(video_path)]
    else:
        args += ["-c", "copy", str(video_path)]
    if run.benchmark:
        args.insert(0, "-benchmark")
    proc = ffmpeg.run_ff(
        args,
        loglevel="info" if run.benchmark else "error",
        timeout=run.timeout,
        step="render",
    )
    if not proc.ok:
        from .edl3_render import CompileError  # noqa: PLC0415 - failure path only

        raise CompileError("RENDER_FAILED", proc.tail(), str(run.destination))
    if not video_path.exists() or video_path.stat().st_size == 0:
        from .edl3_render import CompileError  # noqa: PLC0415 - failure path only

        raise CompileError(
            "RENDER_FAILED",
            "the concat demuxer reported success but wrote no output",
            str(run.destination),
        )
    if not has_audio:
        return proc

    inputs: List[str] = []
    for path in paths:
        inputs.extend(["-i", str(path)])
    durations = _audio_seconds(paths)
    filters: List[str] = []
    labels_list: List[str] = []
    for index, seconds in enumerate(durations):
        label = f"a{index}"
        if seconds > 0.0:
            # A decoded AAC chunk is padded up to its frame grid; trimming each
            # piece to the duration it declares puts the boundary back where the
            # master has it, instead of 5 ms later at every join.
            filters.append(
                f"[{index}:a:0]atrim=0:{seconds:.6f},"
                f"asetpts=PTS-STARTPTS[{label}]"
            )
        else:
            filters.append(f"[{index}:a:0]asetpts=PTS-STARTPTS[{label}]")
        labels_list.append(f"[{label}]")
    filters.append(
        "".join(labels_list) + f"concat=n={len(paths)}:v=0:a=1[aout]"
    )
    audio_args = (
        inputs
        + [
            "-i",
            str(video_path),
            "-filter_complex",
            ";".join(filters),
            "-map",
            f"{len(paths)}:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            AUDIO_BITRATE,
            "-ar",
            str(AUDIO_RATE),
        ]
    )
    if run.benchmark:
        audio_args.insert(0, "-benchmark")
    proc = ffmpeg.run_ff(
        audio_args + [str(run.destination)],
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
            "the audio join reported success but wrote no output",
            str(run.destination),
        )
    return proc


def _carries_audio(paths: List[Path]) -> bool:
    """True when the pieces carry an audio stream (all pieces share the policy)."""
    from . import probe as probe_module  # noqa: PLC0415 - join only

    if not paths:
        return False
    try:
        return bool(probe_module.probe(str(paths[0])).has_audio)
    except Exception:  # noqa: BLE001 - an unreadable piece falls back to copy
        return False


def _audio_seconds(paths: List[Path]) -> List[float]:
    """Declared duration of each piece, or zeros when it cannot be read."""
    from . import probe as probe_module  # noqa: PLC0415 - join only

    found: List[float] = []
    for path in paths:
        try:
            found.append(max(0.0, float(probe_module.probe(str(path)).duration)))
        except Exception:  # noqa: BLE001 - an unknown length is left untrimmed
            found.append(0.0)
    return found


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
    live: bool = False,
    effect_budget: Optional[int] = None,
) -> Dict[str, Any]:
    """Render ``timeline`` as ``chunks`` resumable pieces, then join them.

    ``live`` publishes every piece into ``<workdir>/live/`` the moment it is
    ready, plus an atomically replaced ``manifest.json`` (``vx.live/1``), so a
    player can watch what has actually been encoded while the render continues.
    Without it the module writes exactly the files it wrote before.

    Dissolves are a supported document feature, not a refusal: the boundaries
    are moved off every join (:func:`vx.edl3_slice.plan_windows`). When the
    document leaves no clean edge the split lands on a join and the overlap is
    replayed at that boundary: the manifest and the report declare which join
    and how many seconds, so a longer master is a stated cost, never a surprise.
    """
    from . import edl3_render  # noqa: PLC0415 - render-only, avoids the cycle

    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    root = Path(workdir)
    chunk_dir = root / CHUNK_DIR
    chunk_dir.mkdir(parents=True, exist_ok=True)
    requested = int(chunks)
    windows = plan_windows(timeline, chunks)
    replayed = [
        (int(window["index"]), float(window["overlap_in"]))
        for window in windows
        if float(window.get("overlap_in") or 0.0) > 0.0
    ]
    notes: List[str] = []
    if requested != len(windows):
        notes.append(
            f"dissolvenze: {len(windows)} chunk invece di {requested} per non "
            "tagliare a meta' un join"
        )
    for index, seconds in replayed:
        notes.append(
            f"dissolvenze: il confine del chunk {index:03d} cade su un join, "
            f"{seconds:.3f}s ripetuti ai bordi (dichiarato nel report)"
        )
    _open_subtitles(root, timeline, windows)
    subtitle, subtitle_by = _subtitle_choice(root)
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
        effect_budget=effect_budget,
    )
    publisher = LivePublisher(root / LIVE_DIR, windows) if live else None
    on_ready: Optional[Callable[[Mapping[str, Any]], None]] = (
        publisher.publish if publisher is not None else None
    )

    # A chunk is an independent ffmpeg process writing into its own folder, and
    # the stage cache records each chunk atomically (temp file, then os.replace),
    # so the windows can overlap without touching each other. `map` keeps the
    # order of `windows`, which is what the join and the report depend on.
    workers = max(1, min(int(jobs), len(windows)))
    if workers == 1:
        records = [_render_one(run, window, on_ready) for window in windows]
    else:
        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415 - only here

        with ThreadPoolExecutor(max_workers=workers) as pool:
            records = list(
                pool.map(lambda window: _render_one(run, window, on_ready), windows)
            )
    proc = _join(run, [Path(record["artifact"]) for record in records])

    clips: List[Dict[str, Any]] = []
    backends: List[Dict[str, Any]] = []
    warnings: List[str] = list(notes)
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
    joins = (
        list(timeline.crossfade_joins())
        if hasattr(timeline, "crossfade_joins")
        else []
    )
    strategy = "duplicated" if replayed else ("shifted" if joins else "none")
    decisions_log = _decisions_for(
        timeline, root, windows, subtitle=subtitle, by=subtitle_by
    )

    report = {
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
        #: La workdir vera del render: la vista deve leggere ``live/`` da qui,
        #: non da una cartella indovinata accanto al master.
        "workdir": str(root.resolve()),
        "subtitles": subtitle,
        "decisions": decisions_log,
        "questions": ask.pending(root),
        "chunks": {
            "stage": CHUNK_STAGE,
            "version": CHUNK_VERSION,
            "count": len(windows),
            "requested": requested,
            "workdir": str(root.resolve()),
            "transitions": {
                "strategy": strategy,
                "joins": [
                    {"position": int(position), "seconds": float(seconds)}
                    for position, seconds in joins
                ],
                "replayed": [
                    {"chunk": index, "seconds": seconds} for index, seconds in replayed
                ],
                "boundaries": [float(window["t0"]) for window in windows],
                "seconds_replayed": round(sum(seconds for _, seconds in replayed), 6),
            },
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
    if publisher is not None:
        report["live"] = publisher.summary()
    # Il report sul disco e' il canale della vista: senza, ``vx serve`` non ha
    # dove leggere le decisioni di questo render ne' la workdir vera del live.
    try:
        _write_record(root / REPORT_NAME, report)
    except (OSError, TypeError, ValueError):
        pass
    return report
