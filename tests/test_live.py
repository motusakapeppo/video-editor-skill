"""Live chunks: publish what the renderer has really encoded.

The chunk count is chosen for a piece every ~4 s (2..40, never more pieces than
layer-0 clips); each finished piece lands in ``<workdir>/live/chunk_NNN.mp4`` and
an atomically replaced ``manifest.json`` (``vx.live/1``) says which ones are
ready. ``vx serve`` exposes that manifest as ``live`` in ``/api/state`` and serves
the pieces with Range, from ``<workdir>/live`` only.

No network. The publisher, the manifest and the HTTP contract are exercised with
copies of a tiny local file; one final section runs real ffmpeg to prove that a
document with an audio bus and no ``dst_in``/``dst_out`` renders through the
chunk path and that its pieces add up to the single-pass master.

python tests/test_live.py
"""

from __future__ import annotations

import array
import hashlib
import http.client
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, edl3_chunks, edl3_render, edl3_slice, ffmpeg, probe, serve  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(live: Path) -> dict:
    return json.loads((live / "manifest.json").read_text(encoding="utf-8"))


def _windows(count: int) -> list:
    return [
        {"index": index, "t0": index * 4.0, "t1": (index + 1) * 4.0, "clips": [index]}
        for index in range(count)
    ]


def _fake_render(timeline, output, **kwargs):
    """Drive ``render_chunked`` without ffmpeg: three dummy pieces are produced."""
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"master")
    root = Path(kwargs["workdir"])
    calls = []

    def windows(_timeline, _chunks):
        return _windows(3)

    def one(run, window, on_ready=None):
        artifact = run.chunk_dir / f"{int(window['index']):03d}" / "chunk.mp4"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(f"piece {window['index']}".encode("utf-8"))
        record = {
            "chunk": int(window["index"]),
            "t0": float(window["t0"]),
            "t1": float(window["t1"]),
            "state": "rendered",
            "artifact": str(artifact),
            "size": artifact.stat().st_size,
            "sha256": _digest(artifact),
            "maxrss_kib": None,
            "plan": {
                "clips": [],
                "backends": [],
                "chromium": {
                    "available": False,
                    "qualified": 0,
                    "redirected": 0,
                    "refused": 0,
                    "realized_by_generator": 0,
                },
                "warnings": [],
                "overlays": 0,
                "filter_script": "",
            },
        }
        calls.append(int(window["index"]))
        if on_ready is not None:
            on_ready(record)
        return record

    def join(run, paths):
        for path in paths:
            if not Path(path).is_file():
                raise AssertionError(f"missing chunk artifact {path}")
        return SimpleNamespace(args=["concat"], stderr="", tail=lambda _n=5: "")

    real = (edl3_chunks.plan_windows, edl3_chunks._render_one, edl3_chunks._join)
    edl3_chunks.plan_windows = windows
    edl3_chunks._render_one = one
    edl3_chunks._join = join
    try:
        report = edl3_chunks.render_chunked(
            timeline, destination, chunks=3, workdir=root, **{
                k: v for k, v in kwargs.items() if k != "workdir"
            }
        )
    finally:
        edl3_chunks.plan_windows, edl3_chunks._render_one, edl3_chunks._join = real
    return report, calls


def _video(path: Path, pattern: str, seconds: float = 1.0) -> None:
    subprocess.run(
        [
            ffmpeg.ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", f"{pattern}=s=160x120:r=12:d={seconds}", "-c:v", "libx264",
            "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p", "-an",
            "-y", str(path),
        ],
        check=True,
    )


def _dissolve(seconds: float = 0.3) -> "edl3.Transition":
    return edl3.Transition(kind="crossfade", duration=seconds, style="fade")


def _sources(raw: Path, count: int, seconds: float) -> list:
    patterns = ["testsrc2", "smptebars", "testsrc", "rgbtestsrc"]
    paths = []
    for index in range(count):
        path = raw / f"cf{index}.mp4"
        _video(path, patterns[index % len(patterns)], seconds)
        paths.append(path)
    return paths


def _join_spans(document) -> list:
    """Each dissolve as the time span it occupies in the master."""
    times = edl3_slice.placements(document)
    return [
        (times[position][0], times[position - 1][1])
        for position, _seconds in document.crossfade_joins()
    ]


def _inside(moment: float, spans: list) -> bool:
    return any(start - 1e-9 < moment < end + 1e-9 for start, end in spans)


def _audio(path: Path, seconds: float) -> None:
    subprocess.run(
        [
            ffmpeg.ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
            "-c:a", "aac", "-b:a", "128k", "-y", str(path),
        ],
        check=True,
    )


def _pcm(path: Path) -> "array.array":
    target = path.with_suffix(".f32")
    subprocess.run(
        [
            ffmpeg.ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-map", "0:a:0", "-f", "f32le", "-ac", "1", "-ar", "48000", "-y",
            str(target),
        ],
        check=True,
    )
    return array.array("f", target.read_bytes())


def live_audio_case(raw: Path) -> None:
    """``--live`` accepts an audio bus and clips without dst_in/dst_out."""
    print("\n-- live: audio bus e clip senza dst, la somma resta il master")
    try:
        ffmpeg.ffmpeg()
    except Exception as exc:  # noqa: BLE001 - the rest of the file needs no binary
        print(f"    [SKIP] no ffmpeg: {type(exc).__name__}")
        return
    work = raw / "audio_job"
    work.mkdir()
    patterns = ["testsrc2", "smptebars", "testsrc", "rgbtestsrc"]
    sources = []
    for index, pattern in enumerate(patterns):
        path = work / f"v{index}.mp4"
        _video(path, pattern)
        sources.append(path)
    bed = work / "bed.m4a"
    _audio(bed, 4.0)

    document = edl3.Timeline3(
        fps=12,
        size=(160, 120),
        clips=[
            edl3.Clip3(id=f"c{index}", source=str(source), src_in=0.0, src_out=1.0)
            for index, source in enumerate(sources)
        ],
        audio=[
            edl3.AudioTrack3(
                role="dialogue",
                clips=[
                    edl3.AudioClip3(
                        source=str(bed), src_in=0.0, src_out=4.0,
                        dst_in=0.0, dst_out=4.0,
                    )
                ],
            )
        ],
    )
    check(
        "i tempi si derivano dal cursore, senza dst_in/dst_out",
        all(clip.dst_in is None for clip in document.clips),
        [clip.timeline_duration for clip in document.clips],
    )
    windows = edl3_chunks.plan_windows(document, 2)
    check(
        "i due chunk coprono 0..2 e 2..4 derivando la concatenazione",
        [(window["t0"], window["t1"]) for window in windows] == [(0.0, 2.0), (2.0, 4.0)],
        [(window["t0"], window["t1"]) for window in windows],
    )
    pieces = [
        edl3_chunks.document(document, window).get("audio") or [] for window in windows
    ]
    check(
        "ogni chunk porta la sua fetta di bus audio",
        all(len(track) == 1 and track[0]["clips"] for track in pieces)
        and all(
            abs(
                (float(track[0]["clips"][0]["src_out"])
                 - float(track[0]["clips"][0]["src_in"])) - 2.0
            )
            < 1e-9
            for track in pieces
        ),
        [track[0]["clips"][0]["src_in"] for track in pieces if track],
    )

    master = work / "master.mp4"
    edl3_render.render(document, master, workdir=work / "master_wd", audio="mix")
    joined = work / "live_master.mp4"
    report = edl3_chunks.render_chunked(
        document,
        joined,
        workdir=work / "chunk_wd",
        audio="mix",
        chunks=2,
        live=True,
    )
    check(
        "il render a chunk con audio e live completa i due pezzi",
        report["chunks"]["count"] == 2
        and report.get("live", {}).get("ready") == 2,
        report.get("live", {}).get("ready"),
    )
    check(
        "ogni chunk compila il bus audio ([aout])",
        report["filter_script"].count("[aout]") == 2,
        report["filter_script"].count("[aout]"),
    )
    check(
        "master e somma dei chunk hanno la stessa durata",
        abs(probe.probe(str(master)).duration - probe.probe(str(joined)).duration) <= 0.05,
        f"{probe.probe(str(master)).duration:.3f} vs {probe.probe(str(joined)).duration:.3f}",
    )
    expected = _pcm(master)
    actual = _pcm(joined)
    count = min(len(expected), len(actual))
    error = (
        sum(abs(expected[i] - actual[i]) for i in range(count)) / count
        if count
        else 1.0
    )
    check(
        "la somma dei campioni audio e' il master (errore medio < 0.005)",
        count > 0 and abs(len(expected) - len(actual)) <= 2048 and error < 0.005,
        f"{len(actual)} vs {len(expected)} campioni, errore medio {error:.6f}",
    )
    check(
        "l'audio non e' stato rifiutato ne' scartato",
        report["audio_policy"] == "mix" and joined.stat().st_size > 0,
        report["audio_policy"],
    )


def crossfade_live_case(raw: Path) -> None:
    """D10: ``--live`` with dissolves publishes chunks between the joins.

    Six 1.5 s clips, a dissolve on the first three joins. The even split would
    fall on a join, so the boundary is moved: no chunk edge is inside a
    dissolve and the joined master keeps the document's duration.
    """
    print("\n-- D10: --live con dissolvenze, i confini si spostano")
    try:
        ffmpeg.ffmpeg()
    except Exception as exc:  # noqa: BLE001 - the geometry cases still run
        print(f"    [SKIP] no ffmpeg: {type(exc).__name__}")
        return
    work = raw / "dissolve_job"
    work.mkdir()
    sources = _sources(work, 6, 1.5)
    document = edl3.Timeline3(
        fps=12,
        size=(160, 120),
        clips=[
            edl3.Clip3(
                id=f"c{index}",
                source=str(source),
                src_in=0.0,
                src_out=1.5,
                transition_in=_dissolve() if index in (1, 2, 3) else None,
            )
            for index, source in enumerate(sources)
        ],
    )
    check(
        "D10 il documento dichiara 3 dissolvenze sequenziali",
        len(document.crossfade_joins()) == 3
        and document.validate() == [],
        document.crossfade_joins(),
    )
    spans = _join_spans(document)
    windows = edl3_chunks.plan_windows(document, 2)
    boundaries = [float(window["t0"]) for window in windows[1:]]
    check(
        "D10 il confine e' stato spostato fuori da ogni dissolvenza",
        len(windows) == 2
        and all(not _inside(moment, spans) for moment in boundaries)
        and all(float(window["overlap_in"]) == 0.0 for window in windows),
        [(round(start, 3), round(end, 3)) for start, end in spans] + boundaries,
    )
    check(
        "D10 ogni dissolvenza sta intera in un solo chunk",
        all(
            sum(
                1
                for window in windows
                if start >= float(window["t0"]) - 1e-9
                and end <= float(window["t1"]) + 1e-9
            )
            == 1
            for start, end in spans
        ),
        [(round(start, 3), round(end, 3)) for start, end in spans],
    )

    master = work / "master.mp4"
    report = edl3_chunks.render_chunked(
        document, master, workdir=work / "wd", chunks=2, live=True
    )
    manifest = _manifest(work / "wd" / "live")
    published = [float(item["t_in"]) for item in manifest["chunks"][1:]]
    transitions = report["chunks"]["transitions"]
    check(
        "D10 --live pubblica i due chunk",
        report["live"]["ready"] == 2
        and manifest["ready"] == 2
        and all(item["ready"] for item in manifest["chunks"]),
        report["live"],
    )
    check(
        "D10 i confini pubblicati non tagliano un join",
        transitions["strategy"] == "shifted"
        and not transitions["replayed"]
        and all(not _inside(moment, spans) for moment in published),
        transitions,
    )
    check(
        "D10 il master unito dura come il documento",
        abs(probe.probe(str(master)).duration - document.duration) <= 0.12,
        f"{probe.probe(str(master)).duration:.3f} vs {document.duration:.3f}",
    )
    check(
        "D10 il report dichiara la workdir vera del render",
        report.get("workdir") == str((work / "wd").resolve())
        and report["chunks"].get("workdir") == str((work / "wd").resolve()),
        report.get("workdir"),
    )

    # Every layer-0 edge is a dissolve: no clean boundary exists, so the split
    # lands on a join and the overlap is replayed. That is allowed only because
    # it is declared.
    dense = edl3.Timeline3(
        fps=12,
        size=(160, 120),
        clips=[
            edl3.Clip3(
                id=f"d{index}",
                source=str(sources[index]),
                src_in=0.0,
                src_out=1.5,
                transition_in=_dissolve() if index else None,
            )
            for index in range(4)
        ],
    )
    fallback = edl3_chunks.plan_windows(dense, 2)
    check(
        "D10 senza bordi puliti il confine cade sul join, dichiarato",
        any(float(window["overlap_in"]) > 0.0 for window in fallback),
        [(window["index"], window["overlap_in"]) for window in fallback],
    )
    dense_master = work / "dense.mp4"
    dense_report = edl3_chunks.render_chunked(
        dense, dense_master, workdir=work / "dense_wd", chunks=2, live=True
    )
    check(
        "D10 la dissolvenza ripetuta e' scritta nel report e nei warning",
        dense_report["chunks"]["transitions"]["strategy"] == "duplicated"
        and dense_report["chunks"]["transitions"]["seconds_replayed"] > 0.0
        and any("ripetut" in text for text in dense_report["warnings"])
        and dense_report["live"]["ready"] == 2,
        dense_report["chunks"]["transitions"],
    )
    check(
        "D10 il master dichiara i secondi ripetuti, non li nasconde",
        abs(
            probe.probe(str(dense_master)).duration
            - (dense.duration + dense_report["chunks"]["transitions"]["seconds_replayed"])
        )
        <= 0.12,
        f"{probe.probe(str(dense_master)).duration:.3f} vs {dense.duration:.3f}",
    )

    # Due clip, una dissolvenza, un overlay: ripetere l'overlap sposterebbe i
    # keyframe dell'overlay, quindi si rifiuta — dicendo quale join e come.
    overlay_doc = edl3.Timeline3(
        fps=12,
        size=(160, 120),
        clips=[
            edl3.Clip3(id="a", source=str(sources[0]), src_in=0.0, src_out=1.0),
            edl3.Clip3(
                id="b", source=str(sources[1]), src_in=0.0, src_out=1.0,
                transition_in=_dissolve(),
            ),
            edl3.Clip3(
                id="overlay", source=str(sources[2]), src_in=0.0, src_out=0.5,
                dst_in=0.0, dst_out=0.5, layer=1,
            ),
        ],
    )
    refusal = ""
    try:
        edl3_chunks.plan_windows(overlay_doc, 2)
    except edl3_render.CompileError as exc:
        refusal = f"{exc} {getattr(exc, 'detail', '')}"
    check(
        "D10 se il confine non si puo' spostare, l'errore nomina il join",
        "'a'" in refusal
        and "'b'" in refusal
        and "Move the join" in refusal
        and "dissolve" in refusal,
        refusal,
    )
    return dense_report


def sequential_chunks_case(raw: Path) -> None:
    """D13: a sequential document without dst is chunked, times derived."""
    print("\n-- D13: documento sequenziale senza dst")
    document = edl3.Timeline3(
        fps=12,
        size=(160, 120),
        clips=[
            edl3.Clip3(id=f"s{index}", source=f"missing{index}.mp4",
                       src_in=0.0, src_out=1.0)
            for index in range(3)
        ],
    )
    times = edl3_slice.placements(document)
    check(
        "D13 i tempi vengono dal cursore, non da dst_in/dst_out",
        [times[index] for index in range(3)] == [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)],
        times,
    )
    windows = edl3_chunks.plan_windows(document, 3)
    check(
        "D13 i chunk si derivano senza chiedere placement all'autore",
        [(window["t0"], window["t1"]) for window in windows]
        == [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)],
        [(window["t0"], window["t1"]) for window in windows],
    )
    piece = edl3_slice.document(document, windows[1])
    check(
        "D13 il pezzo resta sequenziale: il dst lo deriva il compilatore",
        [clip["id"] for clip in piece["clips"]] == ["s1"]
        and piece["clips"][0]["dst_in"] is None
        and piece["clips"][0]["dst_out"] is None,
        piece["clips"],
    )

    try:
        ffmpeg.ffmpeg()
    except Exception as exc:  # noqa: BLE001 - geometry already proved above
        print(f"    [SKIP] no ffmpeg: {type(exc).__name__}")
        return
    work = raw / "sequential_job"
    work.mkdir()
    sources = _sources(work, 3, 1.0)
    real = edl3.Timeline3(
        fps=12,
        size=(160, 120),
        clips=[
            edl3.Clip3(id=f"r{index}", source=str(source),
                       src_in=0.0, src_out=1.0)
            for index, source in enumerate(sources)
        ],
    )
    report = edl3_chunks.render_chunked(
        real, work / "out.mp4", workdir=work / "wd", chunks=3
    )
    check(
        "D13 i chunk sono prodotti davvero, senza dst dichiarati",
        report["chunks"]["count"] == 3
        and len(report["chunks"]["records"]) == 3
        and all(
            Path(str(record["artifact"])).is_file()
            for record in report["chunks"]["records"]
        ),
        report["chunks"]["count"],
    )


def workdir_live_case(raw: Path) -> Path:
    """D12: the view reads the manifest from the report's real workdir."""
    print("\n-- D12: la vista legge il live dal workdir del report")
    job = raw / "d12_job"
    (job / "out").mkdir(parents=True)
    (job / "out" / "master.mp4").write_bytes(
        b"\x00\x00\x00\x18ftypmp42" + b"m" * 512
    )
    render_dir = raw / "d12_render"
    live = render_dir / "live"
    live.mkdir(parents=True)
    payload = {
        "schema": "vx.live/1",
        "chunks": [
            {"index": 0, "file": "chunk_000.mp4", "t_in": 0.0, "t_out": 2.0,
             "bytes": 11, "sha256": "-", "ready": True},
            {"index": 1, "file": "chunk_001.mp4", "t_in": 2.0, "t_out": 4.0,
             "bytes": 0, "sha256": "", "ready": False},
        ],
        "total": 2,
        "ready": 1,
        "t0": "2026-11-03T10:00:00+00:00",
        "updated": "2026-11-03T10:00:04+00:00",
    }
    (live / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    (live / "chunk_000.mp4").write_bytes(b"chunk-bytes")
    (job / "report.json").write_text(
        json.dumps({"workdir": str(render_dir), "chunks": {"workdir": str(render_dir)}}),
        encoding="utf-8",
    )
    state = serve.live_state(job)
    check(
        "D12 il manifest viene dal workdir dichiarato, non da accanto al master",
        state.get("ready") == 1
        and state.get("workdir") == str(render_dir.resolve())
        and state.get("directory") == str(live.resolve()),
        {key: state.get(key) for key in ("ready", "workdir", "directory")},
    )
    check(
        "D12 la differenza col lavoro e' mostrata, non ignorata",
        state.get("declared") is True and state.get("mismatch") is True,
        {key: state.get(key) for key in ("declared", "mismatch", "job_dir")},
    )
    check(
        "D12 la vista sul workdir vero vede i chunk",
        serve.state(render_dir)["live"]["ready"] == 1,
        serve.state(render_dir).get("live"),
    )
    server = serve.Server(job).start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
        connection.request("GET", "/live/chunk_000.mp4")
        response = connection.getresponse()
        body = response.read()
        check(
            "D12 il chunk si serve dal workdir del report",
            response.status == 200 and body == b"chunk-bytes",
            f"{response.status} {body!r}",
        )
        connection.request("GET", "/api/state")
        data = json.loads(connection.getresponse().read().decode("utf-8"))
        check(
            "D12 /api/state espone la workdir del manifest",
            data["live"]["workdir"] == str(render_dir.resolve()),
            data["live"].get("workdir"),
        )
    finally:
        server.stop()
    return render_dir


def questions_and_decisions_case(raw: Path, render_dir: Path) -> None:
    """D25: the edl3 render writes the subtitle question and its decisions."""
    print("\n-- D25: domande e decisioni del render edl3 nella vista")
    if not (render_dir / "report.json").is_file():
        print("    [SKIP] nessun render reale: il caso D10 e' saltato")
        return
    report = json.loads((render_dir / "report.json").read_text(encoding="utf-8"))
    check(
        "D25 il report scrive le due decisioni con la prova",
        [item["id"] for item in report.get("decisions") or []]
        == ["subtitles", "transition_kinds"]
        and all(item.get("evidence") for item in report["decisions"])
        and all(item.get("falsified_if") for item in report["decisions"]),
        [(item["id"], item["chosen"]) for item in report.get("decisions") or []],
    )
    check(
        "D25 la domanda sui sottotitoli e' aperta nella workdir del render",
        [item["id"] for item in report.get("questions") or []] == ["subtitles"],
        report.get("questions"),
    )
    state = serve.state(render_dir)
    check(
        "D25 la vista mostra le decisioni invece di 'nessuna'",
        [item["id"] for item in state.get("decisions") or []]
        == ["subtitles", "transition_kinds"],
        state.get("decisions"),
    )
    check(
        "D25 la vista mostra la domanda da rispondere",
        [item["id"] for item in state.get("questions") or []] == ["subtitles"],
        state.get("questions"),
    )
    server = serve.Server(render_dir).start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
        connection.request("POST", "/api/answer?job=0&id=subtitles&value=yes")
        answer = json.loads(connection.getresponse().read().decode("utf-8"))
        question = render_dir / "questions" / "subtitles.json"
        replied = render_dir / "questions" / "subtitles.answer.json"
        check(
            "D25 la risposta arriva nella workdir dove la domanda e' scritta",
            answer.get("ok") is True
            and replied.is_file()
            and not question.exists(),
            answer,
        )
    finally:
        server.stop()
    check(
        "D25 la risposta scritta diventa la scelta letta dal render",
        edl3_chunks._subtitle_choice(render_dir) == ("yes", "user"),
        edl3_chunks._subtitle_choice(render_dir),
    )


def main() -> int:
    print("-- chunk geometry")
    check("20 s -> 5 pieces of ~4 s", edl3_chunks.live_chunks(20.0) == 5,
          edl3_chunks.live_chunks(20.0))
    check("20 s on 4 clips -> at most 4 pieces", edl3_chunks.live_chunks(20.0, clips=4) == 4,
          edl3_chunks.live_chunks(20.0, clips=4))
    check("a 1 s document still asks for 2 pieces", edl3_chunks.live_chunks(1.0) == 2,
          edl3_chunks.live_chunks(1.0))
    check("the upper bound is 40 pieces", edl3_chunks.live_chunks(1000.0) == 40,
          edl3_chunks.live_chunks(1000.0))

    with tempfile.TemporaryDirectory(prefix="vx-live-") as raw:
        work = Path(raw) / "work"
        live = work / "live"
        publisher = edl3_chunks.LivePublisher(live, _windows(3))
        initial = _manifest(live)
        check("the manifest is written before the first piece",
              initial.get("schema") == "vx.live/1" and initial.get("total") == 3,
              initial.get("schema"))
        check("every planned piece is listed but not ready",
              initial.get("ready") == 0 and all(
                  item["ready"] is False for item in initial["chunks"]),
              initial.get("ready"))

        source = Path(raw) / "piece_000.mp4"
        source.write_bytes(b"0123456789" * 100)
        publisher.publish({"chunk": 0, "artifact": str(source), "t0": 0.0, "t1": 4.0})
        after = _manifest(live)
        first = after["chunks"][0]
        target = live / "chunk_000.mp4"
        check("the piece is copied into <workdir>/live",
              target.is_file() and target.read_bytes() == source.read_bytes(),
              target.name)
        check("the manifest is ready: 1 of 3",
              after.get("ready") == 1 and after.get("total") == 3, after.get("ready"))
        check("the entry carries bytes and sha256",
              first["ready"] is True
              and first["bytes"] == target.stat().st_size
              and first["sha256"] == _digest(target),
              f"{first['bytes']} bytes")
        check("the other pieces stay ready:false",
              after["chunks"][1]["ready"] is False
              and after["chunks"][2]["ready"] is False,
              [item["ready"] for item in after["chunks"]])
        check("no temp file is left next to the manifest",
              not [item.name for item in live.iterdir() if ".tmp" in item.name],
              sorted(item.name for item in live.iterdir()))
        check("the manifest is valid JSON after every replace",
              isinstance(_manifest(live).get("updated"), str),
              _manifest(live).get("updated"))

        source2 = Path(raw) / "piece_002.mp4"
        source2.write_bytes(b"abcdefgh" * 50)
        publisher.publish({"chunk": 2, "artifact": str(source2), "t0": 8.0, "t1": 12.0})
        out_of_order = _manifest(live)
        check("pieces may finish out of order and the manifest keeps document order",
              out_of_order["ready"] == 2
              and [item["index"] for item in out_of_order["chunks"]] == [0, 1, 2],
              [item["index"] for item in out_of_order["chunks"]])

        print("-- render without --live writes nothing extra")
        timeline = SimpleNamespace(
            version="vx.edl3/1", fps=25, width=320, height=180,
            duration=12.0, clips=[], profile_ref=None,
        )
        render_work = Path(raw) / "render_work"
        render_work.mkdir()
        before = set(path.name for path in render_work.iterdir())
        report_off, _calls = _fake_render(
            timeline, Path(raw) / "off.mp4", workdir=render_work, live=False)
        check("live:false creates no live directory",
              not (render_work / "live").is_dir() and "live" not in before,
              sorted(before))
        check("live:false adds no live key to the report", "live" not in report_off,
              list(report_off))

        report_on, calls = _fake_render(
            timeline, Path(raw) / "on.mp4", workdir=render_work, live=True)
        check("live:true publishes all three pieces", (render_work / "live" / "chunk_002.mp4").is_file(),
              calls)
        check("the live report says 3 of 3",
              report_on.get("live", {}).get("ready") == 3
              and report_on["live"].get("total") == 3,
              report_on.get("live", {}).get("ready"))

        print("-- serve: state and HTTP contract")
        # A separate job folder is a real vx job: plan.json + progress.json.
        job = Path(raw) / "job"
        (job / "live").mkdir(parents=True)
        (job / "plan.json").write_text("{}", encoding="utf-8")
        (job / "progress.json").write_text("{}", encoding="utf-8")
        empty = serve.live_state(job)
        check("a job without live reports empty fields",
              empty == {"ready": 0, "total": 0, "chunks": []}, empty)

        payload = {
            "schema": "vx.live/1",
            "chunks": [
                {"index": 0, "file": "chunk_000.mp4", "t_in": 0.0, "t_out": 4.0,
                 "bytes": 12, "sha256": "-", "ready": True},
                {"index": 1, "file": "chunk_001.mp4", "t_in": 4.0, "t_out": 8.0,
                 "bytes": 0, "sha256": "", "ready": False},
            ],
            "total": 2,
            "ready": 1,
            "t0": "2026-11-03T10:00:00+00:00",
            "updated": "2026-11-03T10:00:04+00:00",
        }
        (job / "live" / "manifest.json").write_text(
            json.dumps(payload), encoding="utf-8")
        (job / "live" / "chunk_000.mp4").write_bytes(b"0123456789ab")
        (job / "secret.mp4").write_bytes(b"do not serve me")

        state = serve.state(Path(raw))
        live = state.get("live", {})
        check("state exposes ready/total from the manifest",
              live.get("ready") == 1 and live.get("total") == 2, live.get("ready"))
        check("state publishes chunk urls under /live/",
              [item.get("url") for item in live.get("chunks", [])]
              == ["/live/chunk_000.mp4", "/live/chunk_001.mp4"],
              [item.get("url") for item in live.get("chunks", [])])
        check("an unfinished chunk is still listed as ready:false",
              live.get("chunks", [{}])[1].get("ready") is False,
              live.get("chunks", [{}])[1].get("ready"))

        server = serve.Server(Path(raw)).start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.port, timeout=10)

            def get(path: str, headers=None):
                connection.request("GET", path, headers=headers or {})
                response = connection.getresponse()
                body = response.read()
                return response, body

            response, body = get("/live/chunk_000.mp4")
            check("GET /live/chunk_000.mp4 serves the piece whole",
                  response.status == 200 and body == b"0123456789ab"
                  and response.getheader("Accept-Ranges") == "bytes",
                  f"{response.status} {len(body)}")

            response, body = get(
                "/live/chunk_000.mp4", {"Range": "bytes=2-5"})
            check("a Range request returns 206 and the right slice",
                  response.status == 206 and body == b"2345"
                  and response.getheader("Content-Range") == "bytes 2-5/12",
                  f"{response.status} {body!r}")

            response, body = get("/live/chunk_001.mp4")
            check("a chunk that is not ready is a 404", response.status == 404,
                  response.status)

            response, _ = get("/live/../secret.mp4")
            check("a traversal out of live/ is refused", response.status == 404,
                  response.status)

            response, _ = get("/live/chunk_000.mp4%2F..%2Fsecret.mp4")
            check("an encoded separator is refused", response.status == 404,
                  response.status)

            response, _ = get("/live/other.mp4")
            check("only chunk_NNN.mp4 names are served", response.status == 404,
                  response.status)
        finally:
            server.stop()

        crossfade_live_case(Path(raw))
        sequential_chunks_case(Path(raw))
        workdir_live_case(Path(raw))
        questions_and_decisions_case(
            Path(raw), Path(raw) / "dissolve_job" / "dense_wd"
        )
        live_audio_case(Path(raw))

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
