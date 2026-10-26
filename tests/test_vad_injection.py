"""`analyze_audio` must consume a supplied VAD exactly once, and only when it has speech.

The chunked engine computes VAD per chunk and hands the merged result over. Two
mistakes are easy and both are silent:

* running the full-file VAD again, doubling the cost;
* trusting a VAD that reports ``available=True`` but **zero** regions, which is
  what a music-only or silent clip produces. Its speech level is -240 dB, so the
  SNR becomes meaningless and the clip is labelled "noisy".

Run:
    python tests/test_vad_injection.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import audio, vad  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:62} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"SKIP {label:62} {reason}")


def _tone(path: Path) -> bool:
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
            "sine=frequency=440:sample_rate=48000:duration=3",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and path.is_file()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-vad-") as raw:
        wav = Path(raw) / "tone.wav"
        if not _tone(wav):
            skip("segnale di prova", "ffmpeg non disponibile")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 0

        fake = vad.VadResult(
            engine="silero-v5",
            available=True,
            regions=[{"start": 0.0, "end": 1.0}],
            speech_ratio=1.0 / 3.0,
            speech_dbfs=-18.0,
            noise_dbfs=-60.0,
            snr_db=42.0,
            windows=10,
            duration=3.0,
        )
        calls = {"n": 0}
        original_vad_pass = audio._vad_pass

        def must_not_run(*_a, **_k):
            calls["n"] += 1
            raise AssertionError("_vad_pass must not run when a VAD is supplied")

        audio._vad_pass = must_not_run
        try:
            got = audio.analyze_audio(str(wav), vad_result=fake)
        finally:
            audio._vad_pass = original_vad_pass
        check("VAD fornito: nessuna seconda passata", calls["n"] == 0, calls["n"])
        check(
            "VAD fornito: regioni usate",
            got.speech_regions == fake.regions,
            got.speech_regions,
        )
        check("VAD fornito: motore", got.vad_engine == "silero-v5", got.vad_engine)
        check("VAD fornito: snr_method", got.snr_method == "vad-masked", got.snr_method)
        check("VAD fornito: noise", got.noise_floor_dbfs == -60.0, got.noise_floor_dbfs)
        check(
            "VAD fornito: speech", got.speech_level_dbfs == -18.0, got.speech_level_dbfs
        )
        check("VAD fornito: snr", got.snr_db == 42.0, got.snr_db)
        gaps = [(gap["start"], gap["end"]) for gap in got.dead_air]
        check("VAD fornito: dead air dai gap", gaps == [(1.0, 3.0)], gaps)

        empty = vad.VadResult(
            engine="silero-v5", available=True, regions=[], duration=3.0
        )
        calls["n"] = 0
        audio._vad_pass = must_not_run
        try:
            got = audio.analyze_audio(str(wav), vad_result=empty)
        finally:
            audio._vad_pass = original_vad_pass
        check("VAD senza regioni: nessuna seconda passata", calls["n"] == 0, calls["n"])
        check("VAD senza regioni: p10-p90", got.snr_method == "p10-p90", got.snr_method)
        check(
            "VAD senza regioni: motore none", got.vad_engine == "none", got.vad_engine
        )
        check(
            "VAD senza regioni: snr non e' -218",
            abs(got.snr_db) < 100.0,
            got.snr_db,
        )

        unavailable = vad.VadResult(available=False, reason="modello assente")
        calls["n"] = 0
        audio._vad_pass = must_not_run
        try:
            got = audio.analyze_audio(str(wav), vad_result=unavailable)
        finally:
            audio._vad_pass = original_vad_pass
        check("VAD non disponibile: nessun retry", calls["n"] == 0, calls["n"])
        check(
            "VAD non disponibile: p10-p90", got.snr_method == "p10-p90", got.snr_method
        )

        calls["n"] = 0

        def returns_fake(*_a, **_k):
            calls["n"] += 1
            return fake

        audio._vad_pass = returns_fake
        try:
            got = audio.analyze_audio(str(wav))
        finally:
            audio._vad_pass = original_vad_pass
        check("nessun VAD fornito: lo calcola", calls["n"] == 1, calls["n"])
        check(
            "nessun VAD fornito: lo usa", got.snr_method == "vad-masked", got.snr_method
        )

        from vx import cli

        block = cli._chunked_report({"stats": {"chunks_total": 1}, "vad": empty})
        check(
            "report: snr null senza regioni",
            block["vad"]["snr_db"] is None,
            block["vad"]["snr_db"],
        )
        block = cli._chunked_report({"stats": {}, "vad": fake})
        check(
            "report: snr presente con regioni",
            block["vad"]["snr_db"] == 42.0,
            block["vad"]["snr_db"],
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
