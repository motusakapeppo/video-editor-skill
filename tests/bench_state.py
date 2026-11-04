"""Benchmark sintetico di ``serve.state``: 40 lavori finti, nessun ffmpeg.

Non e' un test della suite: e' lo strumento che misura il costo di una richiesta
``/api/state`` (la pagina polla ogni secondo). Genera N workdir verosimili in una
cartella temporanea, poi misura:

* lo stato a **freddo** (prima chiamata, cache vuota);
* lo stato a **caldo** (20 chiamate ripetute);
* la dimensione del JSON completo e della sola lista ``jobs``.

Uso::

    python tests/bench_state.py            # N=40
    python tests/bench_state.py --jobs 40 --repeat 20

Stampa millisecondi per richiesta e KB.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import serve  # noqa: E402

KB = 1024.0


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _clip(index: int, source: Path) -> dict:
    start = round(index * 3.5, 3)
    return {
        "src_in": start + 0.25,
        "src_out": start + 3.25,
        "dst_in": start,
        "dst_out": start + 3.0,
        "layer": 0,
        "source": str(source),
        "effects": [
            {"kind": "zoom", "params": {"amount": 0.05}},
            {"kind": "fade_in", "params": {"duration": 0.3}},
        ],
    }


def build_fixture(base: Path, count: int = 40) -> Path:
    """``count`` workdir pubblicate sotto ``base``, con tutti i file che la vista legge."""
    root = base / "lavori"
    root.mkdir(parents=True, exist_ok=True)
    old = time.time() - 3600.0
    for number in range(count):
        job = root / f"lavoro_{number:02d}"
        job.mkdir(parents=True, exist_ok=True)
        (job / "stage_00").mkdir(exist_ok=True)
        (job / "proxy").mkdir(exist_ok=True)
        (job / "out").mkdir(exist_ok=True)
        source = job / "source.mp4"
        source.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096)
        master = job / "out" / "master.mp4"
        master.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 196608)

        clips = [_clip(index, source) for index in range(20)]
        plan = {
            "schema": "vx.edl/2.2",
            "timeline": {"duration_sec": 70.0},
            "sources": {"source.mp4": {"path": str(source)}},
            "tracks": {"video": [{"layer": 0, "clips": clips}]},
            "treatments": [{"name": "grain", "enabled": True, "params": {"amount": 0.2}}],
        }
        _write_json(job / "plan.json", plan)
        _write_json(job / "source.json", {"path": str(source), "bytes": source.stat().st_size})

        frames = []
        for index in range(6):
            frame = job / "keyframes" / f"{index:03d}.png"
            frames.append({"name": frame.name, "t": round(index * 4.0, 3)})
            frame.parent.mkdir(parents=True, exist_ok=True)
            frame.write_bytes(b"\x89PNG\r\n\x1a\n" + b"k" * 2048)
        _write_json(job / "keyframes" / "pending.json", {"digest": f"d{number}", "frames": frames})
        _write_json(job / "keyframes" / "waiting.json", {"at": number})

        chunks = []
        live = job / "live"
        live.mkdir(exist_ok=True)
        for index in range(6):
            (live / f"chunk_{index:03d}.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"c" * 1024)
            chunks.append({"index": index, "t_in": index * 10.0, "t_out": (index + 1) * 10.0, "ready": True})
        _write_json(live / "manifest.json", {"schema": "vx.live/1", "total": len(chunks), "chunks": chunks})

        analysis = job / "analysis"
        analysis.mkdir(exist_ok=True)
        for index in range(6):
            (analysis / f"thumb_00_{index * 4.0:06.3f}.jpg").write_bytes(b"\xff\xd8" + b"t" * 1024)

        decisions = [
            {
                "id": f"dec_{index}",
                "kind": "style",
                "value": "warm" if index % 2 else "cold",
                "because": "coerenza col brief e con il materiale girato",
                "t": index,
            }
            for index in range(10)
        ]
        report = {
            "schema": "vx.report/1",
            "job": job.name,
            "estimate": {"seconds": 420.0, "confidence": 0.8},
            "plan_choice": {"choice": "full", "because": "materiale lungo"},
            "decisions": decisions,
            "guard": {"ok": True, "violations": [], "warnings": []},
            "shots": len(clips),
        }
        _write_json(job / "report.json", report)
        _write_json(job / "fonts" / "font_proposal.json", {
            "schema": "vx.fontproposal/1",
            "requirement": "elegante",
            "pool": "local",
            "candidates": [{"family": "Inter", "score": 0.8, "sample_png": ""}],
        })
        progress = {
            "schema": "vx.progress/1",
            "prompt": f"reel di prova numero {number}",
            "percent": float((number * 7) % 100),
            "current": None if number == count - 1 else "render",
            "clips": len(clips),
            "elapsed_sec": 120 + number,
            "note": "",
            "estimate": {"seconds": 420.0},
            "plan_choice": {"choice": "full"},
            "master": str(master),
            "phases": [
                {"key": "stage_00", "label": "Materiale importato"},
                {"key": "render", "label": "Renderizzo"},
            ],
            "journal": [
                {"t": index, "stage": "render", "label": f"pezzo {index} reso", "percent": index}
                for index in range(20)
            ],
        }
        panel = job / "progress.json"
        _write_json(panel, progress)
        # Un lavoro solo e' attivo: gli altri sono chiusi da un'ora.
        stamp = time.time() if number == count - 1 else old
        for target in (panel, job / "report.json", job / "plan.json", job / "out" / "master.mp4"):
            try:
                os.utime(target, (stamp, stamp))
            except OSError:
                pass
    return root


def _reset_cache() -> None:
    """Svuota la cache dello stato, se il modulo ne ha una (dopo l'ottimizzazione)."""
    clear = getattr(serve, "_cache_clear", None)
    if callable(clear):
        clear()


def _time_call(root: Path) -> tuple:
    begin = time.perf_counter()
    payload = serve.state(root)
    return (time.perf_counter() - begin) * 1000.0, payload


def main() -> int:
    parser = argparse.ArgumentParser(description="benchmark di vx.serve.state")
    parser.add_argument("--jobs", type=int, default=40)
    parser.add_argument("--repeat", type=int, default=20)
    args = parser.parse_args()

    base = Path(tempfile.mkdtemp(prefix="vx-bench-state-"))
    try:
        root = build_fixture(base, args.jobs)
        assert len(serve.discover(root)) == args.jobs, len(serve.discover(root))

        _reset_cache()
        cold_ms, payload = _time_call(root)

        warm: list = []
        for _ in range(args.repeat):
            ms, _ = _time_call(root)
            warm.append(ms)

        # Steady state vero: la pagina polla ogni secondo, la cache scade. Si
        # misura qualche richiesta dopo la scadenza, senza cache addosso.
        ttl = float(getattr(serve, "CACHE_TTL_SEC", 1.0))
        expired: list = []
        for _ in range(3):
            time.sleep(ttl + 0.05)
            ms, _ = _time_call(root)
            expired.append(ms)

        full_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        jobs_bytes = len(json.dumps(payload.get("jobs") or [], ensure_ascii=False).encode("utf-8"))

        print(f"lavori                  {args.jobs}")
        print(f"freddo                  {cold_ms:8.2f} ms")
        print(f"caldo medio             {statistics.mean(warm):8.2f} ms")
        print(f"caldo mediana           {statistics.median(warm):8.2f} ms")
        print(f"caldo massimo           {max(warm):8.2f} ms")
        print(f"dopo scadenza (media)   {statistics.mean(expired):8.2f} ms")
        print(f"JSON completo           {full_bytes / KB:8.2f} KB")
        print(f"JSON jobs[]             {jobs_bytes / KB:8.2f} KB")
        print(f"voci jobs[]             {len(payload.get('jobs') or [])}")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
