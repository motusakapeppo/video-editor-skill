"""Live chunks: publish what the renderer has really encoded.

The chunk count is chosen for a piece every ~4 s (2..40, never more pieces than
layer-0 clips); each finished piece lands in ``<workdir>/live/chunk_NNN.mp4`` and
an atomically replaced ``manifest.json`` (``vx.live/1``) says which ones are
ready. ``vx serve`` exposes that manifest as ``live`` in ``/api/state`` and serves
the pieces with Range, from ``<workdir>/live`` only.

No network and no ffmpeg: the publisher, the manifest and the HTTP contract are
exercised with copies of a tiny local file.

python tests/test_live.py
"""

from __future__ import annotations

import hashlib
import http.client
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3_chunks, serve  # noqa: E402

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

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
