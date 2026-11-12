"""E2E con **parole vere**: sottotitoli sulla coda e punteggio dei b-roll.

    python tests/test_speech_e2e.py

Il girato di prova (`stock_corpus_speech/speech_pauses_70s.mp4`) è parlato sintetizzato
con pause misurate: tre periodi, 2,5 s di silenzio tra loro, tre copie (69,9 s,
oltre la finestra di lettura piena di 60 s). Serve a provare due cose che i test
unitari non possono:

S1  il ponte riempie la coda col parlato che segue e la **trascrive a parte**:
    `tail_filled_with_speech`, `captions_tail_completed`, nessuna coda scoperta,
    e la trascrizione resta quella vera (whisper);
S2  le parole arrivano al **punteggio dei b-roll**: ogni b-roll del report porta
    uno `score` numerico, non `None`;
S3  il master dura quello che è stato chiesto.

Senza `faster-whisper` il test si dichiara e salta: la trascrizione non si finge.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

FIXTURE = ROOT / "tests" / "stock_corpus_speech" / "speech_pauses_70s.mp4"
PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def skip(label: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:60} {why}")


def ffmpeg() -> str:
    explicit = os.environ.get("VX_FFMPEG")
    if explicit and Path(explicit).is_file():
        return explicit
    return shutil.which("ffmpeg") or "ffmpeg"


def make_image(path: Path, pattern: str) -> None:
    subprocess.run(
        [
            ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"{pattern}=size=1080x1920:rate=1:duration=1",
            "-frames:v", "1", str(path),
        ],
        check=False,
        capture_output=True,
    )


def main() -> int:
    if not FIXTURE.is_file():
        # Il girato non è nel repo (i media generati non si versionano): lo
        # costruisce il generatore del corpus, se la voce di sistema c'è.
        sys.path.insert(0, str(ROOT / "tests" / "stock_corpus"))
        try:
            import make_speech  # noqa: PLC0415 - solo quando manca il file

            built = make_speech.build(FIXTURE)
        except Exception as exc:  # noqa: BLE001 - senza voce si dichiara
            built = False
            print(f"  [SKIP] generatore non eseguibile: {type(exc).__name__}: {exc}")
        if not built:
            skip("S1/S2/S3", f"girato non costruibile (serve SAPI e ffmpeg): {FIXTURE.name}")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 0
    try:
        import faster_whisper  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - senza modello non si finge
        skip("S1/S2/S3", f"faster-whisper assente: {exc}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    python = sys.executable
    with tempfile.TemporaryDirectory(prefix="vx-speech-") as raw:
        tmp = Path(raw)
        pool = tmp / "pool"
        pool.mkdir()
        shutil.copy2(FIXTURE, pool / FIXTURE.name)
        make_image(pool / "a_ponti.png", "testsrc2")
        make_image(pool / "b_citta.png", "smptebars")
        out = tmp / "master.mp4"
        work = tmp / "work"
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "scripts")
        env["PYTHONIOENCODING"] = "utf-8"
        env.setdefault("VX_NO_REVEAL", "1")
        run = subprocess.run(
            [
                python, "-m", "vx.cli", "auto",
                "--prompt", "un reel di 6 secondi, con sottotitoli",
                "--assets", str(pool),
                "--out", str(out),
                "--workdir", str(work),
                "--json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=900,
        )
        check("S0 l'auto gira e scrive il report", run.returncode == 0, run.returncode)
        try:
            report = json.loads(run.stdout)
        except Exception as exc:  # noqa: BLE001
            check("S0 stdout è il report", False, f"{type(exc).__name__}: {str(run.stderr)[-200:]}")
            report = {}

        bridge_path = work / "bridge_report.json"
        bridge = json.loads(bridge_path.read_text(encoding="utf-8")) if bridge_path.is_file() else {}
        rows = [row for row in (bridge.get("not_applied") or []) if isinstance(row, dict)]
        reasons = {str(row.get("reason")) for row in rows}

        check(
            "S1 il ponte ha riempito la coda col parlato che segue",
            "tail_filled_with_speech" in reasons,
            sorted(reasons & {"tail_filled_with_speech", "captions_tail_completed", "captions_tail_uncovered"}),
        )
        check(
            "S1 la coda è stata trascritta a parte e il ponte ricostruito",
            "captions_tail_completed" in reasons,
            [row for row in rows if row.get("reason") == "captions_tail_completed"],
        )
        check(
            "S1 nessuna coda resta senza sottotitoli",
            "captions_tail_uncovered" not in reasons,
            [row for row in rows if row.get("reason") == "captions_tail_uncovered"],
        )
        subtitles = (report.get("bridge") or {}).get("subtitles")
        srt = work / "captions.srt"
        check(
            "S1 il sidecar dei sottotitoli esiste ed è la trascrizione vera",
            bool(subtitles) and Path(str(subtitles)).is_file() and srt.is_file(),
            subtitles,
        )
        cues = 0
        if srt.is_file():
            cues = sum(1 for line in srt.read_text(encoding="utf-8", errors="replace").splitlines() if "-->" in line)
        check("S1 e ha delle battute", cues >= 3, cues)

        broll = [item for item in (bridge.get("broll") or []) if isinstance(item, dict)]
        scored = [item for item in broll if isinstance(item.get("score"), (int, float))]
        check(
            "S2 le parole vere arrivano al punteggio dei b-roll",
            bool(broll) and bool(scored),
            [(item.get("asset_id"), item.get("score")) for item in broll],
        )
        declared = {str(row.get("reason")) for row in rows}
        check(
            "S2 un b-roll senza parole è dichiarato, non inventato",
            len(scored) == len(broll) or "broll_score_unavailable" in declared,
            sorted(declared & {"broll_score_unavailable", "broll_shifted_to_speech"}),
        )

        info = None
        if out.is_file():
            from vx import probe  # noqa: PLC0415 - solo quando il master esiste

            info = probe.probe(str(out))
        check(
            "S3 il master dura quello che è stato chiesto",
            bool(info) and abs(float(info.duration) - 6.0) <= 0.2,
            info.duration if info else None,
        )
        check(
            "S3 ed è consegnato",
            report.get("delivered") is True and report.get("outcome") == "delivered",
            (report.get("outcome"), report.get("delivered")),
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
