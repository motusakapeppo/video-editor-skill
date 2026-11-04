"""vx auto --music: il BPM viene dalla traccia, il brano e' il letto musicale.

python tests/test_auto_music.py

Nessun render: si misura una traccia vera (click generato qui, 120 BPM), si
guarda il payload che ``decide`` riceve e il piano EDL che ne esce. La traccia
si legge soltanto: lo sha prima e dopo lo prova. Il caso "musica non
misurabile" usa il silenzio, che e' l'unico ingresso in cui la griglia non ha
ne' tempo ne' battute: il report deve dirlo, non inventare un numero.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, bridge, contract  # noqa: E402
from vx.asset_triage import AssetInfo  # noqa: E402
from vx.edl_plan import SourceRef  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:66} {detail}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_wav(path: Path, seconds: float, sample, sr: int = 48000) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        frames = bytearray()
        for index in range(int(sr * seconds)):
            value = max(-1.0, min(1.0, float(sample(index / sr))))
            packed = struct.pack("<h", int(value * 32767))
            frames += packed + packed
        handle.writeframes(bytes(frames))
    return path


def _click_120(path: Path, seconds: float = 8.0) -> Path:
    """Un click ogni 0.5 s: 120 BPM, misurabile senza dipendere dal render."""

    def sample(t: float) -> float:
        phase = t % 0.5
        if phase >= 0.03:
            return 0.0
        return 0.7 * math.sin(2 * math.pi * 900 * t) * math.exp(-phase * 80)

    return _write_wav(path, seconds, sample)


def _silence(path: Path, seconds: float = 8.0) -> Path:
    return _write_wav(path, seconds, lambda _t: 0.0)


def _music_args(path: Path, bpm: float | None = None) -> SimpleNamespace:
    return SimpleNamespace(music=str(path), bpm=bpm)


def _cli(args: list) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "scripts")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "vx.cli", *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_measure(work: Path) -> tuple[Path, dict]:
    print("\n-- 1. BPM misurato dalla traccia")
    click = _click_120(work / "click120.wav")
    before = _sha256(click)
    plan = auto._music_plan(_music_args(click))
    after = _sha256(click)
    measured = plan.get("bpm") or 0.0
    check(
        "bpm dalla traccia (click a 120: misura tra 110 e 125)",
        110.0 <= float(measured) <= 125.0,
        measured,
    )
    check(
        "provenienza bpm_source == 'music'",
        plan.get("bpm_source") == "music" and plan.get("measured") is True,
        (plan.get("bpm_source"), plan.get("measured")),
    )
    check(
        "metodo e battute dichiarati",
        bool(plan.get("method")) and int(plan.get("beats") or 0) >= 8,
        (plan.get("method"), plan.get("beats")),
    )
    check(
        "music.path e music.duration nel report",
        Path(str(plan.get("path"))).resolve() == click.resolve()
        and abs(float(plan.get("duration") or 0.0) - 8.0) <= 0.25,
        (plan.get("path"), plan.get("duration")),
    )
    check(
        "traccia non modificata (sha256 prima == dopo)",
        before == after and plan.get("read_only") is True,
        before[:12],
    )
    return click, plan


def test_declared(work: Path, click: Path) -> None:
    print("\n-- 2. --bpm vince sulla traccia")
    plan = auto._music_plan(_music_args(click, bpm=90))
    check(
        "bpm dichiarato 90, bpm_source 'declared'",
        float(plan.get("bpm") or 0.0) == 90.0
        and plan.get("bpm_source") == "declared",
        (plan.get("bpm"), plan.get("bpm_source")),
    )
    check(
        "la misura resta dichiarata come confronto",
        110.0 <= float(plan.get("measured_bpm") or 0.0) <= 125.0,
        plan.get("measured_bpm"),
    )


def test_unavailable(work: Path) -> dict:
    print("\n-- 3. BPM non misurabile: griglia spenta e dichiarata")
    silence = _silence(work / "silence.wav")
    plan = auto._music_plan(_music_args(silence))
    check(
        "bpm null e bpm_source 'unavailable'",
        plan.get("bpm") is None and plan.get("bpm_source") == "unavailable",
        (plan.get("bpm"), plan.get("bpm_source")),
    )
    check(
        "la nota dice che la griglia e' spenta",
        "griglia spenta" in str(plan.get("note") or ""),
        plan.get("note"),
    )
    provenance = auto._beat_provenance(
        {"beat_grid": {"enabled": False, "bpm": 0.0, "source": "none"}}, plan
    )
    check(
        "_beat_provenance: bpm None, enabled False, source unavailable",
        provenance == {"bpm": None, "bpm_source": "unavailable", "enabled": False},
        provenance,
    )
    return plan


def test_decide(work: Path, measured_plan: dict, unavailable_plan: dict) -> None:
    print("\n-- 4. il payload riceve il BPM e la provenienza")
    recipe = {
        "applied": True,
        "explicit": ["beat_grid"],
        "params": {"beat_grid": "on"},
    }
    args = SimpleNamespace(prompt="reel verticale, ritmo serrato", autonomous=False)
    payload, note = auto._decide_payload(args, [], recipe, True, music=measured_plan)
    block = payload.get("beat_grid") or {}
    check(
        "griglia accesa col BPM della traccia",
        block.get("enabled") is True
        and block.get("source") == "music"
        and abs(float(block.get("bpm") or 0.0) - float(measured_plan["bpm"])) < 0.01,
        block,
    )
    check("nessun degrado dichiarato quando la misura c'e'", note == "", note)

    payload_off, note_off = auto._decide_payload(
        args, [], recipe, True, music=unavailable_plan
    )
    block_off = payload_off.get("beat_grid") or {}
    check(
        "non misurabile: griglia spenta e provenienza 'unavailable'",
        block_off.get("enabled") is False
        and block_off.get("source") == "unavailable"
        and block_off.get("bpm") is None,
        block_off,
    )
    check(
        "il degrado della ricetta e' dichiarato, non silenzioso",
        "BPM" in note_off,
        note_off,
    )


def test_discover(work: Path) -> None:
    print("\n-- 5. --music e' il letto: il doppione del manifest non entra")
    pool = work / "pool"
    pool.mkdir()
    bed = _click_120(pool / "audio_bed.wav")
    manifest = pool / "manifest.json"
    manifest.write_text(
        json.dumps({"assets": [{"path": "audio_bed.wav", "role": "music"}]}),
        encoding="utf-8",
    )
    music = {
        "requested": True,
        "path": str(bed),
        "asset": auto._music_asset(bed),
    }
    assets = auto._discover(
        SimpleNamespace(assets=str(pool), manifest=str(manifest)), music
    )
    music_assets = [asset for asset in assets if asset.kind == "audio"]
    check(
        "un solo letto musicale, con ruolo music",
        len(music_assets) == 1 and music_assets[0].role == "music",
        [(asset.asset_id, asset.role) for asset in music_assets],
    )
    check(
        "il doppione del pool e' sostituito e dichiarato",
        music_assets[0].asset_id.startswith(auto.MUSIC_ASSET_PREFIX)
        and "audio_bed.wav" in (music.get("replaced") or []),
        music.get("replaced"),
    )


def test_plan_ducking(work: Path) -> None:
    print("\n-- 6. il brano e' un bus 'music' e il ducking c'e'")
    video = AssetInfo(
        asset_id="clip.mp4",
        kind="video",
        path=str(work / "clip.mp4"),
        width=1080,
        height=1920,
        duration_sec=8.0,
        has_audio=True,
    )
    music = AssetInfo(
        asset_id="music:tema.wav",
        kind="audio",
        path=str(work / "tema.wav"),
        duration_sec=30.0,
        has_audio=True,
        role="music",
    )
    payload = auto.decide.parse_and_decide(
        "reel verticale",
        asset_pool=[video, music],
        force_mode="autonomous",
        montage=True,
        bpm=120.0,
    )
    sources = {
        item.asset_id: SourceRef(
            item.path, "a" * 64, float(item.duration_sec)
        )
        for item in (video, music)
    }
    media = {
        item.asset_id: {
            "duration": float(item.duration_sec),
            "width": int(item.width),
            "height": int(item.height),
            "fps": 30.0 if item.kind == "video" else 0.0,
            "has_audio": bool(item.has_audio),
        }
        for item in (video, music)
    }
    plan = contract.payload_to_edl_plan(payload, sources, media, fps=30.0)
    tracks = {str(track.role): track for track in plan.audio_tracks}
    rows = auto._audio_tracks_report(plan)
    check(
        "audio_tracks con ruolo 'music'",
        "music" in tracks and any(row["role"] == "music" for row in rows),
        [(row["role"], row["clips"]) for row in rows],
    )
    check(
        "ducking dichiarato sotto il parlato",
        plan.ducking is not None
        and str(getattr(plan.ducking, "target_role", "")) == "music"
        and float(plan.ducking.ratio) > 1.0,
        getattr(plan.ducking, "target_role", None),
    )
    bed_gain = float(tracks["music"].clips[0].gain_db)
    check(
        "il letto entra col gain musicale, non a zero",
        abs(bed_gain - float(contract.THRESHOLDS["triage_music_gain_db"])) < 1e-6,
        bed_gain,
    )
    check(
        "il bus dialogo resta il sidechain",
        "dialogue" in tracks and bool(tracks["dialogue"].clips),
        list(tracks),
    )


def test_font_scope(work: Path) -> None:
    print("\n-- 7. i due font dichiarano il loro scope")
    proposal = {
        "chosen": "Inter",
        "pool": "local",
        "candidates": [
            {
                "family": "Inter",
                "licence": "ofl",
                "source": "bundled",
                "downloaded": False,
                "file": "",
            }
        ],
    }
    section = auto.font_gate(
        work / "font_gate",
        requirement="reel serrato",
        energy=0.8,
        gate=False,
        propose=lambda *a, **k: proposal,
        notify=lambda *_a, **_k: None,
    )
    check(
        "report.font.scope == 'captions'",
        section.get("scope") == "captions" and section.get("family") == "Inter",
        (section.get("scope"), section.get("family")),
    )
    overlay = auto._overlay_font(
        {"family": "Antonio", "note": "ripiego dichiarato", "fallback": False}
    )
    check(
        "typography.font.scope == 'overlays'",
        overlay.get("scope") == "overlays" and overlay.get("family") == "Antonio",
        overlay.get("scope"),
    )
    check(
        "la nota dice che puo' differire dal font dei sottotitoli",
        "sottotitoli" in str(overlay.get("note") or "")
        and "ripiego dichiarato" in str(overlay.get("note") or ""),
        overlay.get("note"),
    )


def test_missing_file(work: Path) -> None:
    print("\n-- 8. --music inesistente: exit 2, nessun master")
    assets = work / "empty_assets"
    assets.mkdir()
    out = work / "out" / "master.mp4"
    missing = work / "does_not_exist.wav"
    proc = _cli(
        [
            "auto",
            "--prompt",
            "reel verticale",
            "--assets",
            str(assets),
            "--music",
            str(missing),
            "--out",
            str(out),
            "--workdir",
            str(work / "missing_work"),
            "--no-panel",
            "--json",
        ]
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    check("exit code 2", proc.returncode == 2, proc.returncode)
    check(
        "messaggio chiaro: file inesistente",
        "inesistente" in text and "--music" in text,
        text.strip().splitlines()[-1][:120] if text.strip() else "",
    )
    check("nessun master scritto", not out.exists(), out)


def main() -> int:
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="vx-auto-music-") as tmp:
        work = Path(tmp)
        click, measured_plan = test_measure(work)
        test_declared(work, click)
        unavailable_plan = test_unavailable(work)
        test_decide(work, measured_plan, unavailable_plan)
        test_discover(work)
        test_plan_ducking(work)
        test_font_scope(work)
        test_missing_file(work)
    elapsed = time.time() - started
    print(f"\n{PASS} PASS / {FAIL} FAIL  ({elapsed:.2f}s)")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

