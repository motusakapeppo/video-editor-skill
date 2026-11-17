"""Il render EDL3 racconta dove e' arrivato, mentre ci arriva.

Il caso che ha motivato questo file: un post verticale con 1272 fotogrammi di
overlay disegnati da Chromium. La vista pubblicava tre fasi (compile, render,
master) e restava al 10% per dieci minuti: chi guardava non poteva distinguere
un render che lavora da uno appeso.

S1 - i confini veri arrivano in ordine e la percentuale non torna indietro.
S2 - con un grafo unico, ffmpeg scrive il suo `-progress` dove la vista lo legge
     (`<workdir>/render/ffmpeg-progress.txt`): la testina segue l'encode vero.
S3 - il percorso a blocchi annuncia ogni pezzo: (1/N) ... (N/N).
S4 - le fasi elencate sono quelle che accadranno: niente "generator" senza
     generator, niente "chunk" senza blocchi.
S5 - una vista che esplode non ferma il render: il callback e' un osservatore.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import cli, edl3, edl3_render  # noqa: E402
from vx.ffmpeg import run_ff  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def source(path: Path, seconds: float = 1.0, size: str = "320x568", rate: int = 12) -> Path:
    proc = run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s={size}:r={rate}:d={seconds}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "1",
            str(path),
        ],
        loglevel="error",
    )
    if not proc.ok or not path.exists():
        raise RuntimeError(f"fixture {path.name}: {proc.tail()}")
    return path


def document(clip: Path, seconds: float, *, layer: int = 0, generator: bool = False) -> dict:
    row = {
        "id": f"c{layer}",
        "src_in": 0.0,
        "src_out": seconds,
        "dst_in": 0.0,
        "dst_out": seconds,
        "layer": layer,
    }
    if generator:
        row["generator"] = {"template": "vx-title-kicker", "params": {}}
    else:
        row["source"] = str(clip)
    return {
        "version": edl3.EDL3_VERSION,
        "fps": 12,
        "size": [320, 568],
        "fill": "crop",
        "clips": [row],
        "audio": [],
    }


def stages(rows) -> list:
    return [row[1] for row in rows]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-progress-") as raw:
        work = Path(raw)
        clip = source(work / "src.mp4")

        # -- S1/S2: un grafo unico ------------------------------------------- #
        seen = []
        timeline = edl3.Timeline3.from_dict(document(clip, 0.6))
        report = edl3_render.render(
            timeline,
            work / "out" / "single.mp4",
            workdir=work / "graph",
            audio="refuse",
            settings={"chromium_available": False},
            progress=lambda percent, stage, note="": seen.append((percent, stage, note)),
        )
        check("S1 il render annuncia almeno tre confini", len(seen) >= 3, len(seen))
        percents = [row[0] for row in seen]
        check(
            "S1 la percentuale non torna mai indietro",
            all(b >= a for a, b in zip(percents, percents[1:])),
            percents,
        )
        check("S1 la prima cosa annunciata e' 'nested'", stages(seen)[0] == "nested", stages(seen))
        check("S1 l'ultima cosa annunciata e' 'encode'", stages(seen)[-1] == "encode", stages(seen))
        check("S1 il render si ferma al 95%, il master lo chiude il comando", percents[-1] == 95.0, percents[-1])

        progress_file = work / "graph" / "render" / "ffmpeg-progress.txt"
        check(
            "S2 ffmpeg ha scritto il suo -progress dove la vista lo legge",
            progress_file.is_file() and "out_time_us=" in progress_file.read_text(encoding="utf-8", errors="replace"),
            str(progress_file.relative_to(work)),
        )
        check(
            "S2 il comando dichiara il file: la testina non e' inventata",
            any("ffmpeg-progress.txt" in str(part) for part in report["command"]),
            [part for part in report["command"] if "progress" in str(part)][:2],
        )

        # -- S5: un osservatore rotto non ferma il render ------------------- #
        def broken(percent, stage, note=""):
            raise RuntimeError("la vista non risponde")

        try:
            edl3_render.render(
                edl3.Timeline3.from_dict(document(clip, 0.6)),
                work / "out" / "resilient.mp4",
                workdir=work / "resilient",
                audio="refuse",
                settings={"chromium_available": False},
                progress=broken,
            )
            survived = (work / "out" / "resilient.mp4").is_file()
        except Exception as exc:  # noqa: BLE001 - a leak here is the failure
            survived = False
            print(f"      (il render e' morto: {type(exc).__name__}: {exc})")
        check("S5 un callback che esplode non ferma il render", survived)

        # -- S3: il percorso a blocchi annuncia ogni pezzo ------------------ #
        first = document(clip, 0.5)["clips"][0]
        second = {**first, "id": "c0b", "src_in": 0.5, "src_out": 1.0, "dst_in": 0.5, "dst_out": 1.0}
        two = {**document(clip, 0.5), "clips": [first, second]}
        pieces = []
        edl3_render.render(
            edl3.Timeline3.from_dict(two),
            work / "out" / "chunked.mp4",
            workdir=work / "chunked",
            audio="refuse",
            settings={"chromium_available": False},
            chunks=2,
            progress=lambda percent, stage, note="": pieces.append((percent, stage, note)),
        )
        chunk_rows = [(percent, stage) for percent, stage, _ in pieces if stage == "chunk"]
        finished_pieces = [row for row in chunk_rows if row[0] > 3.0]
        check(
            "S3 il percorso a blocchi annuncia ogni pezzo, non solo l'apertura",
            len(finished_pieces) == 2,
            chunk_rows,
        )
        check(
            "S3 l'ultimo pezzo annunciato e' N/N",
            bool(finished_pieces) and finished_pieces[-1][0] == 92.0,
            finished_pieces[-1] if finished_pieces else "",
        )

        # -- S4: le fasi elencate sono quelle che accadranno ---------------- #
        plain = argparse.Namespace(chunks=None, live=False)
        chunked = argparse.Namespace(chunks=4, live=False)
        base_clip = document(clip, 0.6)["clips"][0]
        overlay = {
            "id": "g",
            "generator": {"template": "vx-title-kicker", "params": {}},
            "src_in": 0.0,
            "src_out": 0.6,
            "dst_in": 0.0,
            "dst_out": 0.6,
            "layer": 1,
        }
        with_generator = edl3.Timeline3.from_dict(
            {**document(clip, 0.6), "clips": [base_clip, overlay]}
        )
        keys_plain = [key for key, _ in cli._edl3_phases(edl3.Timeline3.from_dict(document(clip, 0.6)), plain)]
        keys_chunk = [key for key, _ in cli._edl3_phases(edl3.Timeline3.from_dict(document(clip, 1.0)), chunked)]
        keys_gen = [key for key, _ in cli._edl3_phases(with_generator, plain)]
        check("S4 senza generator non si elenca 'generator'", "generator" not in keys_plain, keys_plain)
        check("S4 un grafo unico elenca 'encode'", "encode" in keys_plain and "chunk" not in keys_plain, keys_plain)
        check("S4 i blocchi elencano 'chunk'", "chunk" in keys_chunk and "encode" not in keys_chunk, keys_chunk)
        check("S4 un generator elenca la sua fase", "generator" in keys_gen, keys_gen)
        check("S4 la fase finale e' sempre 'master'", keys_plain[-1] == "master", keys_plain[-1])

        # -- coerenza del pannello vero ------------------------------------- #
        board = cli._edl3_panel(work / "panel", "plan.json", 2, cli._edl3_phases(edl3.Timeline3.from_dict(document(clip, 0.6)), plain))
        board.record(54.0, "encode", elapsed=3.0)
        data = json.loads((work / "panel" / "progress.json").read_text(encoding="utf-8"))
        check("S4 il pannello pubblica la fase corrente", data.get("current") == "encode", data.get("current"))
        check(
            "S4 le fasi nel json sono quelle scelte, non un elenco fisso",
            [row["key"] for row in data.get("phases", [])][-1] == "master",
            [row["key"] for row in data.get("phases", [])],
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
