"""`vx ingest` pubblica il suo progresso: la vista non resta a zero.

Il difetto vero: mentre l'ingest lavora, `vx serve` lo mostrava come lavoro
**dedotto** (`progress_source: "derived"`, percent 0, fasi lette dalle cartelle)
perche' il `progress.json` lo scriveva solo `vx auto`. Qui si prova il contrario:

I1 - `progress.json` e `progress.html` esistono nella workdir;
I2 - durante l'ingest il file c'e' gia' e la percentuale si muove (non 0, non
     100, con una fase in corso): la vista non aspetta la fine;
I3 - le fasi sono quelle vere dell'ingest, non quelle dedotte dalle cartelle;
I4 - alla fine percent 100, `current` nullo, nota di conclusione, `master` vuoto
     (l'ingest non produce un master);
I5 - `vx.serve.state()` su quella cartella dice `progress_source == "json"`.

Il clip e' sintetico (3 s, 640x360, senza audio): l'ingest completo dura ~50 s
soprattutto per il caricamento del modello dei volti, ed e' la fixture piu'
piccola che attraversa tutte le fasi.

Run:  python tests/test_ingest_progress.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import serve  # noqa: E402

PYTHON = sys.executable
FFMPEG = os.environ.get("VX_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"

#: Le fasi che l'ingest attraversa davvero, nell'ordine del codice.
INGEST_PHASES = [
    "ingest",
    "proxy",
    "analysis",
    "speech",
    "audio",
    "faces",
    "thumbs",
    "triage",
]

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:62} {reason}")


def read_json(path: Path) -> Dict[str, Any]:
    """Un JSON che puo' essere sostituito mentre lo si legge: vuoto, non un crash."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def make_clip(path: Path) -> None:
    proc = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=25:duration=3",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not path.is_file():
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-400:])


def run_ingest(clip: Path, work: Path, tmp: Path) -> tuple:
    """L'ingest vero, con la workdir osservata mentre gira."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "scripts")
    env["PYTHONIOENCODING"] = "utf-8"
    env["VX_FFMPEG"] = FFMPEG
    out_path = tmp / "stdout.txt"
    err_path = tmp / "stderr.txt"
    with out_path.open("wb") as out, err_path.open("wb") as err:
        proc = subprocess.Popen(
            [
                PYTHON,
                "-m",
                "vx.cli",
                "ingest",
                "--input",
                str(clip),
                "--workdir",
                str(work),
                "--json",
                "--no-speech",
                "--autonomous",
            ],
            cwd=str(ROOT),
            env=env,
            stdout=out,
            stderr=err,
        )
        panel = work / "progress.json"
        seen: List[Optional[float]] = []
        # Si legge mentre lavora: ogni campione con percentuale in movimento e
        # una fase in corso e' la prova che la vista non resta ferma a zero.
        while proc.poll() is None:
            data = read_json(panel)
            if data:
                percent = data.get("percent")
                if isinstance(percent, (int, float)) and data.get("current"):
                    seen.append(float(percent))
            time.sleep(0.2)
        return proc.returncode, out_path.read_text("utf-8", "replace"), seen


def main() -> int:
    if not (Path(FFMPEG).is_file() or shutil.which(FFMPEG)):
        skip("vx ingest progress", f"ffmpeg non disponibile: {FFMPEG}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    with tempfile.TemporaryDirectory(prefix="vx-ingest-progress-") as raw:
        tmp = Path(raw)
        clip = tmp / "clip.mp4"
        try:
            make_clip(clip)
        except RuntimeError as exc:
            skip("vx ingest progress", f"clip sintetico non creato: {exc}")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 0

        work = tmp / "work"
        code, stdout, seen = run_ingest(clip, work, tmp)
        panel = work / "progress.json"
        page = work / "progress.html"
        data = read_json(panel)

        check("I1 exit 0", code == 0, code)
        check("I1 progress.json esiste", panel.is_file(), panel.name)
        check("I1 progress.html esiste", page.is_file(), page.name)
        check(
            "I2 durante l'ingest la percentuale si e' mossa",
            bool(seen) and any(0.0 < value < 100.0 for value in seen),
            f"campioni={len(seen)} max={max(seen) if seen else None}",
        )

        keys = [item.get("key") for item in data.get("phases") or [] if isinstance(item, dict)]
        check(
            "I3 le fasi sono quelle vere dell'ingest",
            keys[: len(INGEST_PHASES)] == INGEST_PHASES and len(keys) >= len(INGEST_PHASES),
            keys,
        )
        check(
            "I3 le fasi hanno la riga in italiano",
            all(str(item.get("label") or "").strip() for item in data.get("phases") or []),
            len(data.get("phases") or []),
        )

        note = str(data.get("note") or "")
        check("I4 percent 100 alla fine", data.get("percent") == 100.0, data.get("percent"))
        check("I4 current nullo alla fine", data.get("current") is None, data.get("current"))
        check("I4 nota di conclusione in italiano", "concluso" in note.lower(), note)
        check("I4 master vuoto (l'ingest non consegna)", data.get("master") == "", repr(data.get("master")))
        check(
            "I4 tutte le fasi segnate come fatte",
            [key for key in INGEST_PHASES if key not in (data.get("done") or [])] == [],
            data.get("done"),
        )

        state = serve.state(work)
        check(
            'I5 vx serve legge "json", non "derived"',
            state.get("progress_source") == "json",
            state.get("progress_source"),
        )
        check(
            "I5 la vista riporta la stessa conclusione",
            state.get("percent") == 100.0 and state.get("current") is None,
            {k: state.get(k) for k in ("percent", "current")},
        )
        if code != 0:
            print(stdout[-800:])

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
