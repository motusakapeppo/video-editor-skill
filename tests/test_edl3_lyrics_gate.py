"""Il cancello del testo dentro `vx edl3 render`.

Il caso: sette versioni, e ogni giro rifiutato per una parola sbagliata o un
conteggio di colpi. Sono cose che si leggono in un JSON prima di pagare il
render. `--lyrics` le mette davanti al render, e la verifica del master (durata,
loop) la mette dopo.

S1 - una spec buona: il render parte, il report porta i conteggi pre e post.
S2 - una spec sbagliata: exit 5 e **nessun master** (il render non e' cominciato).
S3 - un master che non dura quello che la spec dichiara: exit 5 e
     `deliverable: false`, anche se il file esiste.
S4 - senza `--lyrics` il comportamento e' quello di sempre: nessun blocco,
     nessun blocco `lyrics` nel report.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402
from vx.ffmpeg import run_ff  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def source(path: Path) -> Path:
    proc = run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=0x203040:s=320x568:r=12:d=1",
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
        raise RuntimeError(f"fixture: {proc.tail()}")
    return path


def plan(clip: Path) -> dict:
    return {
        "version": edl3.EDL3_VERSION,
        "fps": 12,
        "size": [320, 568],
        "fill": "crop",
        "clips": [
            {
                "id": "a",
                "source": str(clip),
                "src_in": 0.0,
                "src_out": 0.6,
                "dst_in": 0.0,
                "dst_out": 0.6,
                "layer": 0,
            }
        ],
        "audio": [],
    }


def spec(duration: float = 0.6) -> dict:
    return {
        "fps": 12,
        "duration": duration,
        "frames": int(round(duration * 12)),
        "events": [
            {
                "t0": 0.0,
                "t1": duration,
                "style": "hero",
                "size": 80,
                "y": 100,
                "lines": [{"text": "ok"}],
            }
        ],
    }


def run(work: Path, extra: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "vx.py"),
            "edl3",
            "render",
            "--plan",
            str(work / "plan.json"),
            "--out",
            str(work / "out" / "master.mp4"),
            "--workdir",
            str(work / "work"),
            "--audio",
            "refuse",
            "--json",
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
        env={**os.environ, "VX_NO_VIEW": "1"},
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-gate-") as raw:
        work = Path(raw)
        clip = source(work / "src.mp4")

        # -- S1: una spec buona ---------------------------------------------- #
        case = work / "good"
        case.mkdir()
        (case / "plan.json").write_text(json.dumps(plan(clip)), encoding="utf-8")
        (case / "spec.json").write_text(json.dumps(spec()), encoding="utf-8")
        (case / "text.txt").write_text("ok", encoding="utf-8")
        done = run(case, ["--lyrics", str(case / "spec.json"), "--lyrics-text", str(case / "text.txt")])
        report = json.loads((case / "work" / "report.json").read_text(encoding="utf-8")) if (case / "work" / "report.json").is_file() else {}
        block = (report.get("render") or {}).get("lyrics") or {}
        check("S1 il render parte e finisce", done.returncode == 0, done.returncode)
        check("S1 il master esiste", (case / "out" / "master.mp4").is_file())
        check("S1 il report porta i controlli pre", bool(block.get("pre")), block.get("pre"))
        check(
            "S1 il report porta i controlli del master",
            bool(block.get("post")) and block.get("failed") == [],
            block.get("post"),
        )
        check("S1 il master resta consegnabile", report.get("render", {}).get("deliverable") is True, report.get("render", {}).get("deliverable"))

        # -- S2: la spec sbagliata ferma il render --------------------------- #
        case = work / "bad-spec"
        case.mkdir()
        (case / "plan.json").write_text(json.dumps(plan(clip)), encoding="utf-8")
        (case / "spec.json").write_text(json.dumps(spec()), encoding="utf-8")
        (case / "text.txt").write_text("diverso", encoding="utf-8")
        done = run(case, ["--lyrics", str(case / "spec.json"), "--lyrics-text", str(case / "text.txt")])
        payload = json.loads(done.stdout) if done.stdout.strip() else {}
        check("S2 esce 5", done.returncode == 5, done.returncode)
        check(
            "S2 nessun master: il render non e' cominciato",
            not (case / "out" / "master.mp4").is_file(),
        )
        check(
            "S2 il referto dice quale controllo",
            payload.get("stage") == "lyrics" and "L10" in json.dumps(payload),
            payload.get("stage"),
        )

        # -- S3: il master non dura quello che la spec dichiara -------------- #
        case = work / "bad-master"
        case.mkdir()
        (case / "plan.json").write_text(json.dumps(plan(clip)), encoding="utf-8")
        (case / "spec.json").write_text(json.dumps(spec(3.0)), encoding="utf-8")
        done = run(case, ["--lyrics", str(case / "spec.json")])
        report = json.loads((case / "work" / "report.json").read_text(encoding="utf-8")) if (case / "work" / "report.json").is_file() else {}
        render = report.get("render") or {}
        check("S3 esce 5 dopo il render", done.returncode == 5, done.returncode)
        check("S3 il master esiste lo stesso (si guarda)", (case / "out" / "master.mp4").is_file())
        check("S3 il master non e' consegnabile", render.get("deliverable") is False, render.get("deliverable"))
        check(
            "S3 il motivo e' nel report",
            any("lyrics" in str(item) for item in render.get("warnings") or []),
            render.get("warnings"),
        )

        # -- S4: senza --lyrics, niente cambia ------------------------------- #
        case = work / "plain"
        case.mkdir()
        (case / "plan.json").write_text(json.dumps(plan(clip)), encoding="utf-8")
        done = run(case, [])
        report = json.loads((case / "work" / "report.json").read_text(encoding="utf-8"))
        check("S4 senza --lyrics esce 0", done.returncode == 0, done.returncode)
        check("S4 il report non finge un controllo mai fatto", "lyrics" not in (report.get("render") or {}))

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
