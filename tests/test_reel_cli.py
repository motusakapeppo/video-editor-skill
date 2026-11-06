"""vx reel — orchestrazione, otto firme, parita' con ``vx auto``.

    python tests/test_reel_cli.py

Fixture sintetica a 1080x1920 (la tela di consegna): niente rete, niente
whisper. Il test guida la CLI vera e legge il run log, non le promesse.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import reel, runlog  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FFMPEG = os.environ.get("VX_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("VX_FFPROBE") or shutil.which("ffprobe") or "ffprobe"
PROMPT = "un reel verticale di 3 secondi, hook: NON CI CREDERAI"


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:58} {reason}")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _env() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "scripts")
    env["PYTHONIOENCODING"] = "utf-8"
    env["VX_FFMPEG"] = FFMPEG
    env["VX_FFPROBE"] = FFPROBE
    return env


def _cli(args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "vx.cli", *args],
        cwd=str(ROOT),
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _make(path: Path, args: list) -> bool:
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args, str(path)],
        capture_output=True,
    )
    return proc.returncode == 0 and path.is_file() and path.stat().st_size > 0


def _pool(pool: Path) -> bool:
    return _make(
        pool / "clip.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1080x1920:rate=25:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=3",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
        ],
    )


def _common(assets: Path, out: Path, work: Path) -> list:
    return [
        "reel",
        "--prompt",
        PROMPT,
        "--assets",
        str(assets),
        "--out",
        str(out),
        "--workdir",
        str(work),
        "--montage",
        "--no-subs",
        "--no-font-network",
    ]


def main() -> int:
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="vx-reel-") as raw:
        tmp = Path(raw)
        pool = tmp / "pool"
        pool.mkdir()
        if not _pool(pool):
            skip("fixture", "ffmpeg non disponibile")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 0

        out = tmp / "master.mp4"
        work = tmp / "work"
        result = _cli([*_common(pool, out, work), "--json"])
        check("vx reel rc 0", result.returncode == 0, result.returncode)
        try:
            payload = json.loads(result.stdout)
        except ValueError as exc:
            payload = {}
            check("--json: stdout e' un documento solo", False, f"{exc}: {result.stdout[-120:]}")
        else:
            check("--json: stdout e' un documento solo", isinstance(payload, dict))
        check("run.jsonl esiste", runlog.path(work).is_file(), runlog.path(work))

        events = runlog.read(work)
        check("8 eventi, uno per stadio", len(events) == 8, len(events))
        check(
            "gli stadi sono quelli del contratto",
            [item["stage"] for item in events] == list(runlog.STAGES),
            [item["stage"] for item in events],
        )
        required = {"v", "seq", "at", "stage", "event", "contract", "value", "threshold", "ok", "sha"}
        check(
            "ogni riga porta schema, seq, valore, soglia, esito, sha",
            all(required <= set(item) for item in events),
        )
        measured = [
            item
            for item in events
            if item["event"] == "sign"
            and isinstance(item["value"], (int, float))
            and isinstance(item["threshold"], (int, float))
        ]
        check("almeno 3 firme con valore e soglia numerici", len(measured) >= 3, len(measured))
        check(
            "tutti gli 8 stadi sono firmati",
            runlog.signed(work) == set(runlog.STAGES),
            runlog.signed(work),
        )
        check(
            "report: delivered true e outcome delivered",
            payload.get("delivered") is True and payload.get("outcome") == "delivered",
            {k: payload.get(k) for k in ("delivered", "outcome")},
        )
        check("report.run.failures vuoto", payload.get("run", {}).get("failures") == [])
        check(
            "il master consegnato esiste",
            Path(str(payload.get("master") or "")).is_file(),
            payload.get("master"),
        )

        # -- parita' con vx auto ------------------------------------------ #
        auto_out = tmp / "auto.mp4"
        auto_work = tmp / "auto_work"
        auto_result = _cli(
            [
                "auto",
                "--prompt",
                PROMPT,
                "--assets",
                str(pool),
                "--out",
                str(auto_out),
                "--workdir",
                str(auto_work),
                "--montage",
                "--no-subs",
                "--no-font-network",
                "--json",
            ]
        )
        check("vx auto rc 0", auto_result.returncode == 0, auto_result.returncode)
        try:
            auto_payload = json.loads(auto_result.stdout)
        except ValueError:
            auto_payload = {}
        check(
            "stesso sha256 del master",
            out.is_file() and auto_out.is_file() and _sha(out) == _sha(auto_out),
            (_sha(out) if out.is_file() else "", _sha(auto_out) if auto_out.is_file() else ""),
        )
        mine = payload.get("measured") or {}
        theirs = auto_payload.get("measured") or {}
        keys = ("duration_sec", "lufs", "contrast", "clips")
        check(
            "stessi numeri chiave (durata, LUFS, contrasto, clip)",
            all(mine.get(key) == theirs.get(key) for key in keys),
            {key: (mine.get(key), theirs.get(key)) for key in keys},
        )

        # -- resume: tutti firmati, nessuna ripetizione -------------------- #
        before = len(runlog.read(work))
        resume = _cli([*_common(pool, out, work), "--resume", "--json"])
        check("--resume rc 0", resume.returncode == 0, resume.returncode)
        try:
            resumed = json.loads(resume.stdout)
        except ValueError:
            resumed = {}
        info = resumed.get("run", {}).get("resume", {})
        check(
            "resume salta tutti gli stadi gia' firmati",
            set(info.get("skipped") or []) == set(reel.STAGES),
            info.get("skipped"),
        )
        check(
            "resume non riscrive firme gia' presenti",
            len(runlog.read(work)) == before,
            (before, len(runlog.read(work))),
        )
        check("resume resta veloce (nessun render)", info.get("elapsed_sec", 999) < 15.0, info.get("elapsed_sec"))

        # -- coda tronca: il log parsa e la ripresa rifirma l'ultima ------- #
        data = runlog.path(work).read_bytes()
        runlog.path(work).write_bytes(data[:-18])
        parsed = len(runlog.read(work))
        check("coda tronca: il log parsa e scarta l'ultima riga", parsed == 7, parsed)
        again = _cli([*_common(pool, out, work), "--resume", "--json"])
        check("resume dopo coda tronca rc 0", again.returncode == 0, again.returncode)
        try:
            again_payload = json.loads(again.stdout)
        except ValueError:
            again_payload = {}
        skipped = set(again_payload.get("run", {}).get("resume", {}).get("skipped") or [])
        check("dopo la coda tronca salta i 7 stadi validi", len(skipped) == 7, skipped)
        check(
            "dopo la ripresa tutti gli 8 stadi sono firmati",
            runlog.signed(work) == set(reel.STAGES),
            runlog.signed(work),
        )
        check("verify: log valido e completo", runlog.verify(work)["ok"] is True, runlog.verify(work))

        # -- riga centrale corrotta: la ripresa si ferma ------------------- #
        lines = runlog.path(work).read_text(encoding="utf-8").splitlines()
        lines[1] = "{ riga rotta"
        runlog.path(work).write_text("\n".join(lines) + "\n", encoding="utf-8")
        broken = _cli([*_common(pool, out, work), "--resume", "--json"])
        check("log corrotto: resume non parte (rc != 0)", broken.returncode != 0, broken.returncode)
        check(
            "log corrotto: RUN_LOG_CORRUPT dichiarato",
            "RUN_LOG_CORRUPT" in (broken.stdout + broken.stderr),
            (broken.stdout + broken.stderr)[-160:],
        )

        # -- sotto soglia: la firma fallisce e il nome lo dice ------------- #
        forged = {
            "fps": 30.0,
            "loudness_target": {"lufs": -14.0},
            "timeline_sec": 6.0,
            "duration_requested_sec": 6.0,
            "measured": {
                "duration_sec": 6.0,
                "requested_duration_sec": 6.0,
                "lufs": -14.0,
                "contrast": 3.56,
                "clips": 4,
                "sources_measured": 3,
                "assets_used": 3,
                "gates_wait_sec": 0.0,
            },
            "master": str(tmp / "below.mp4"),
        }
        shutil.copy2(out, tmp / "below.mp4")
        stages = reel.measure_stages(forged, tmp)
        check(
            "contrasto 3.56 < 4.5: deliver non firma",
            stages["deliver"]["ok"] is False
            and "3.56" in stages["deliver"]["why"]
            and "4.5" in stages["deliver"]["why"],
            stages["deliver"]["why"],
        )
        renamed = reel._rename_for_failure(forged)
        check(
            "master sotto soglia: NON-PUBBLICABILE-<nome>.mp4",
            renamed is not None and renamed.name == "NON-PUBBLICABILE-below.mp4",
            renamed.name if renamed else None,
        )

        # -- senza titolo il contrasto non si applica, non e' un fallimento -- #
        # Difetto vero, trovato il 2026-11-06 su un reel reale: il prompt non
        # chiedeva testo, `measured.contrast` era null, e `vx reel` bollava il
        # lavoro NON-PUBBLICABILE mentre il QC diceva `applicable: false` e
        # passava. Stesso master, due verdetti opposti.
        untitled = dict(forged)
        untitled["text_required"] = False
        untitled["measured"] = dict(forged["measured"], contrast=None)
        untitled_stages = reel.measure_stages(untitled, tmp)
        check(
            "nessun titolo richiesto: deliver firma e lo dichiara",
            untitled_stages["deliver"]["ok"] is True
            and "non applicabile" in untitled_stages["deliver"]["why"]
            and untitled_stages["deliver"]["value"] == "n/a",
            untitled_stages["deliver"]["why"],
        )
        titled = dict(untitled, text_required=True)
        check(
            "titolo richiesto ma non misurato: deliver non firma",
            reel.measure_stages(titled, tmp)["deliver"]["ok"] is False,
            reel.measure_stages(titled, tmp)["deliver"]["why"],
        )

        # -- la durata e' quella del contenitore, come la misura il QC ------ #
        # Difetto vero, stesso giorno: `probe` legge la traccia video (9.937 s)
        # e il QC il contenitore (9.984 s). Costruisco un file in cui i due
        # numeri differono davvero: video da 4 s, audio da 6 s.
        divergent = tmp / "divergent.mp4"
        subprocess.run(
            [
                FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30:duration=4",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                str(divergent),
            ],
            check=False,
            capture_output=True,
        )
        stream = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=duration", "-of", "csv=p=0", str(divergent)],
            check=False, capture_output=True, text=True,
        )
        container = reel._container_duration(divergent)
        check(
            "il file di prova ha davvero due durate diverse",
            container is not None and container > 5.0,
            f"contenitore {container} · traccia {stream.stdout.strip()!r}",
        )
        mixed = dict(
            forged,
            fps=30.0,
            duration_requested_sec=6.0,
            measured={**forged["measured"], "duration_sec": 4.0,
                      "requested_duration_sec": 6.0},
            master=str(divergent),
        )
        mixed_render = reel.measure_stages(mixed, tmp)["render"]
        check(
            "render legge il contenitore: 6 s richiesti, 6 s firmati",
            mixed_render["ok"] is True
            and abs(float(mixed_render["value"]) - float(container)) < 1e-3,
            f"{mixed_render['value']} vs contenitore {container}",
        )

    elapsed = time.time() - started
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  ({elapsed:.1f}s)")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
