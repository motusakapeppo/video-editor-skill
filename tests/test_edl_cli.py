"""CLI multi-source: vx edl validate/render, missing source, Gate 1 exit 5."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import probe  # noqa: E402
from vx.edl_plan import ClipSegment, EDLPlan, SourceRef, Track, Transition  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:56} {reason}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _make(path: Path, color: str) -> bool:
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
            f"color=c={color}:s=320x180:r=25:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
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
            str(path),
        ],
        capture_output=True,
    )
    return proc.returncode == 0 and path.is_file()


def _plan(a: Path, b: Path, *, bad: bool = False) -> EDLPlan:
    duration = 3.0 if bad else 2.0
    end = 3.0 if bad else 2.0
    sources = {
        "src_A": SourceRef(str(a), _sha256(a), 2.0),
        "src_B": SourceRef(str(b), _sha256(b), 2.0),
    }
    c1 = ClipSegment("c1", "src_A", 0.0, 1.0, 0.0, 1.0, 0, "main_talking_head")
    c2 = ClipSegment("c2", "src_B", 1.0, end, 1.0, end, 0, "main_talking_head")
    a1 = ClipSegment("a1", "src_A", 0.0, 1.0, 0.0, 1.0, 0, "dialogue")
    a2 = ClipSegment("a2", "src_B", 1.0, end, 1.0, end, 0, "dialogue")
    b1 = ClipSegment(
        "b1",
        "src_B",
        0.0,
        0.5,
        0.5,
        1.0,
        1,
        "broll_overlay",
        Transition("fade", 0.3),
        Transition("fade", 0.3),
    )
    return EDLPlan(
        fps=25.0,
        duration_sec=duration,
        aspect_ratio="9:16",
        resolution=(320, 180),
        sources=sources,
        video_tracks=(Track(0, (c1, c2), "main"), Track(1, (b1,), "broll")),
        audio_tracks=(Track(0, (a1, a2), "dialogue"),),
    )


def _cli(args: list) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "scripts")
    env["PYTHONIOENCODING"] = "utf-8"
    env["VX_FFMPEG"] = FFMPEG
    return subprocess.run(
        [sys.executable, "-m", "vx.cli", *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-edl-cli-") as raw:
        tmp = Path(raw)
        a, b = tmp / "a.mp4", tmp / "b.mp4"
        if not (_make(a, "blue") and _make(b, "red")):
            skip("fixture", "ffmpeg non disponibile")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 0

        good = tmp / "edl.json"
        good.write_text(json.dumps(_plan(a, b).to_dict()), encoding="utf-8")
        valid = _cli(["edl", "validate", "--plan", str(good), "--json"])
        check("validate rc 0", valid.returncode == 0, valid.stderr[-160:])
        try:
            payload = json.loads(valid.stdout)
        except Exception as exc:  # noqa: BLE001
            check("validate JSON", False, f"{type(exc).__name__}: {exc}")
        else:
            check("validate ok", payload.get("ok") is True, payload)

        out = tmp / "out.mp4"
        run = _cli(
            [
                "edl",
                "render",
                "--plan",
                str(good),
                "--source",
                f"src_A={a}",
                "--source",
                f"src_B={b}",
                "--workdir",
                str(tmp / "work"),
                "--out",
                str(out),
                "--crf",
                "30",
                "--preset",
                "ultrafast",
                "--json",
            ]
        )
        check("render rc 0", run.returncode == 0, run.stderr[-200:])
        check("render file", out.is_file() and out.stat().st_size > 0)
        if out.is_file():
            info = probe.probe(str(out))
            check(
                "render geometria",
                (info.width, info.height) == (320, 180),
                (info.width, info.height),
            )
            check(
                "render durata",
                abs(info.duration - 2.0) <= 0.15,
                info.duration,
            )

        missing = _cli(
            [
                "edl",
                "render",
                "--plan",
                str(good),
                "--source",
                f"src_A={a}",
                "--workdir",
                str(tmp / "work_missing"),
                "--out",
                str(tmp / "missing.mp4"),
                "--json",
            ]
        )
        check("sorgente mancante rc 2", missing.returncode == 2, missing.returncode)
        check("sorgente mancante nominata", "src_B" in missing.stdout, missing.stdout)

        bad = tmp / "bad.json"
        bad.write_text(json.dumps(_plan(a, b, bad=True).to_dict()), encoding="utf-8")
        gate = _cli(
            [
                "edl",
                "render",
                "--plan",
                str(bad),
                "--source",
                f"src_A={a}",
                "--source",
                f"src_B={b}",
                "--workdir",
                str(tmp / "work_bad"),
                "--out",
                str(tmp / "bad.mp4"),
                "--json",
            ]
        )
        check("Gate 1 rc 5", gate.returncode == 5, gate.returncode)
        check("Gate 1 nomina src_B", "src_B" in gate.stdout, gate.stdout[-200:])

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
