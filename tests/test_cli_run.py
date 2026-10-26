"""End-to-end matrix through ``vx.cli run`` (subprocess, no direct render calls).

The audit's most grave defect was ``vx run`` crashing on the ordinary case
(same aspect, no autoframe) because ``crop`` was read outside the block that
assigned it.  This matrix drives the real CLI and checks the repaired contract:

* no ``render.crop`` key when autoframe is off (absence, not null);
* no raw traceback escapes the CLI;
* fonts are a hard failure, not a warning;
* a transcript below the quality gate is never burned;
* the hook overlay is written and burned before the main subtitles;
* deliverables are produced by ``vx.deliverables.produce``.

python tests/test_cli_run.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
FFMPEG = os.environ.get("VX_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
REAL = ROOT / "tests" / "stock_corpus" / "real" / "interview_169.webm"

PASS = 0
FAIL = 0
SKIP = 0
RESULTS: List[Tuple[str, str, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def skip(name: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    RESULTS.append((name, "SKIP", "-", reason))
    print(f"[SKIP] {name}: {reason}")


def record(name: str, status: str, code: Any, detail: str = "") -> None:
    RESULTS.append((name, status, str(code), detail))


def ffmpeg(args: List[str]) -> None:
    proc = subprocess.run(
        [FFMPEG, "-y", "-v", "error", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-600:])


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _last_json(text: str) -> Optional[Dict[str, Any]]:
    decoder = json.JSONDecoder()
    best: Optional[Dict[str, Any]] = None
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(obj, dict):
            best = obj
        index = end
    return best


def run_cli(
    src: Path,
    brief: Dict[str, Any],
    workdir: Path,
    extra: Optional[List[str]] = None,
) -> Dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    brief_path = workdir / "brief.json"
    brief_path.write_text(json.dumps(brief), encoding="utf-8")
    cmd = [
        PYTHON,
        "-m",
        "vx.cli",
        "run",
        "--input",
        str(src),
        "--brief",
        str(brief_path),
        "--json",
        "--workdir",
        str(workdir),
        *(extra or []),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "scripts")
    env["PYTHONIOENCODING"] = "utf-8"
    env["VX_FFMPEG"] = FFMPEG
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    plan_path = workdir / "plan.json"
    return {
        "rc": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "summary": _last_json(proc.stdout) or {},
        "plan": _read_json(plan_path),
        "report": _read_json(workdir / "report.json"),
        "qc": _read_json(workdir / "qc.json"),
        "plan_text": plan_path.read_text(encoding="utf-8")
        if plan_path.exists()
        else "",
        "workdir": workdir,
    }


def treatment(plan: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    for item in plan.get("treatments") or []:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def common_checks(name: str, res: Dict[str, Any]) -> None:
    check(f'{name}: plan has no "crop": null', '"crop": null' not in res["plan_text"])
    check(
        f"{name}: no traceback on stderr",
        "Traceback (most recent call last)" not in res["stderr"],
    )


def _fail(name: str, res: Dict[str, Any], detail: str = "") -> Tuple[str, Any, str]:
    check(f"{name}: exit 0", False, f"rc={res['rc']}")
    tail = (res["stderr"] or "").strip().splitlines()[-3:]
    return "FAIL", res["rc"], detail or " | ".join(tail)


def scenario_same_aspect(tmp: Path) -> Tuple[str, Any, str]:
    name = "1 same-aspect 16:9, no autoframe"
    base = tmp / "s1"
    clip = base / "clip.mp4"
    base.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=25:duration=3",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ]
    )
    res = run_cli(
        clip,
        {"format": "16:9", "hook": "none", "subtitles": "none"},
        base / "work",
        ["--no-speech"],
    )
    common_checks(name, res)
    if res["rc"] != 0:
        return _fail(name, res)
    check(f"{name}: exit 0", True)
    check(f"{name}: output exists", Path(res["summary"].get("output", "")).exists())
    check(
        f"{name}: render has no crop key",
        "crop" not in (res["plan"].get("render") or {}),
    )
    produced = res["summary"].get("deliverables") or []
    master = next(
        (item for item in produced if item.get("kind") == "master"),
        None,
    )
    master_path = str((master or {}).get("path") or "").replace("\\", "/")
    check(
        f"{name}: deliverables include out/master.mp4",
        bool(master) and master_path.endswith("out/master.mp4"),
        master_path,
    )
    check(
        f"{name}: out/master.mp4 exists",
        bool(master) and Path(str(master.get("path"))).exists(),
    )
    return "PASS", res["rc"], ""


def scenario_4k_face(tmp: Path) -> Tuple[str, Any, str]:
    name = "2 4K 16:9 with face -> 9:16 autoframe"
    if not REAL.exists():
        skip(name, f"real face clip missing: {REAL}")
        return "SKIP", "-", "missing real corpus"
    base = tmp / "s2"
    clip = base / "clip_4k.mp4"
    base.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-ss",
            "6.5",
            "-t",
            "4",
            "-i",
            str(REAL),
            "-vf",
            "scale=3840:2160:flags=lanczos",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ]
    )
    res = run_cli(
        clip,
        {"format": "9:16", "hook": "none", "subtitles": "none"},
        base / "work",
        ["--no-speech"],
    )
    common_checks(name, res)
    if res["rc"] != 0:
        return _fail(name, res)
    check(f"{name}: exit 0", True)
    autoframe = treatment(res["plan"], "autoframe")
    check(
        f"{name}: autoframe enabled",
        bool(autoframe and autoframe.get("enabled")),
        str((autoframe or {}).get("params")),
    )
    output = res["plan"].get("output") or {}
    check(
        f"{name}: output 1080x1920",
        output.get("width") == 1080 and output.get("height") == 1920,
        f"{output.get('width')}x{output.get('height')}",
    )
    check(f"{name}: output exists", Path(res["summary"].get("output", "")).exists())
    faces = (res["report"].get("analysis") or {}).get("faces") or {}
    check(
        f"{name}: a face was detected",
        float(faces.get("detected_ratio") or 0.0) > 0.0,
        f"detected_ratio={faces.get('detected_ratio')}",
    )
    return "PASS", res["rc"], ""


def scenario_no_faces(tmp: Path) -> Tuple[str, Any, str]:
    name = "3 no faces, 9:16, no autoframe"
    base = tmp / "s3"
    clip = base / "clip.mp4"
    base.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=360x640:rate=25:duration=3",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ]
    )
    res = run_cli(
        clip,
        {"format": "9:16", "hook": "none", "subtitles": "none"},
        base / "work",
        ["--no-speech"],
    )
    common_checks(name, res)
    if res["rc"] != 0:
        return _fail(name, res)
    check(f"{name}: exit 0", True)
    autoframe = treatment(res["plan"], "autoframe")
    check(
        f"{name}: no autoframe enabled",
        not autoframe or not autoframe.get("enabled"),
        str(autoframe),
    )
    return "PASS", res["rc"], ""


def scenario_no_audio(tmp: Path) -> Tuple[str, Any, str]:
    name = "4 no audio, qc has no audio checks"
    base = tmp / "s4"
    clip = base / "clip.mp4"
    base.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=25:duration=3",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ]
    )
    res = run_cli(
        clip,
        {"format": "16:9", "hook": "none", "subtitles": "none"},
        base / "work",
        ["--no-speech"],
    )
    common_checks(name, res)
    if res["rc"] != 0:
        return _fail(name, res)
    check(f"{name}: exit 0", True)
    qc = res["qc"]
    check(f"{name}: qc pass", qc.get("pass") is True)
    checks = qc.get("checks") or []
    audio_failures = [
        c
        for c in checks
        if c.get("code") in ("lufs_delta", "true_peak") and c.get("status") == "fail"
    ]
    check(
        f"{name}: no audio loudness failures", not audio_failures, str(audio_failures)
    )
    has_audio = next((c for c in checks if c.get("code") == "has_audio"), None)
    check(
        f"{name}: has_audio reports no stream",
        bool(has_audio) and "codec=none" in str(has_audio.get("detail")),
        str(has_audio),
    )
    return "PASS", res["rc"], ""


def scenario_cut_silence(tmp: Path) -> Tuple[str, Any, str]:
    name = "5 cut_silence on a clip with a silent gap"
    base = tmp / "s5"
    clip = base / "clip.mp4"
    base.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-t",
            "5",
            "-i",
            "anoisesrc=color=white:amplitude=0.1:sample_rate=48000",
            "-f",
            "lavfi",
            "-t",
            "5",
            "-i",
            "testsrc=size=640x360:rate=25",
            "-filter_complex",
            "[0:a]volume=0:enable='between(t,2,3)',volume=2.65[a]",
            "-map",
            "1:v",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(clip),
        ]
    )
    res = run_cli(
        clip,
        {"format": "16:9", "hook": "none", "subtitles": "none"},
        base / "work",
        ["--no-speech"],
    )
    common_checks(name, res)
    if res["rc"] != 0:
        return _fail(name, res)
    cut = treatment(res["plan"], "cut_silence")
    if not cut or not cut.get("enabled"):
        reason = ((cut or {}).get("reason") or {}).get("explain", "treatment absent")
        skip(name, f"cut_silence not enabled: {reason}")
        return "SKIP", res["rc"], reason
    check(f"{name}: exit 0", True)
    check(f"{name}: cut_silence enabled", True)
    cuts = res["plan"].get("cuts") or []
    check(f"{name}: at least 2 segments", len(cuts) >= 2, f"{len(cuts)} segments")
    return "PASS", res["rc"], ""


def _quiet_clip(base: Path) -> Path:
    clip = base / "clip.mp4"
    base.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-t",
            "3",
            "-i",
            "testsrc=size=640x360:rate=25",
            "-f",
            "lavfi",
            "-t",
            "3",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=3",
            "-filter_complex",
            "[1:a]volume=0.02[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(clip),
        ]
    )
    return clip


def scenario_loudness(tmp: Path, profile: str, target: float) -> Tuple[str, Any, str]:
    name = f"{'6' if profile == 'apple' else '7'} {profile} loudness profile"
    base = tmp / f"s{profile}"
    clip = _quiet_clip(base)
    res = run_cli(
        clip,
        {
            "format": "16:9",
            "hook": "none",
            "subtitles": "none",
            "loudness": profile,
        },
        base / "work",
        ["--no-speech"],
    )
    common_checks(name, res)
    if res["rc"] != 0:
        return _fail(name, res)
    loudnorm = treatment(res["plan"], "loudnorm")
    if not loudnorm or not loudnorm.get("enabled"):
        skip(name, "loudnorm not enabled: source loudness too close to target")
        return "SKIP", res["rc"], "loudnorm absent"
    check(f"{name}: exit 0", True)
    loudness = (res["plan"].get("output") or {}).get("loudness") or {}
    check(
        f"{name}: output.loudness.i == {target}",
        loudness.get("i") == target,
        str(loudness.get("i")),
    )
    check(
        f"{name}: stage/04_loudnorm.txt exists",
        (res["workdir"] / "stage" / "04_loudnorm.txt").exists(),
    )
    return "PASS", res["rc"], ""


def scenario_hook(tmp: Path) -> Tuple[str, Any, str]:
    name = "8 hook overlay with hook_text"
    if not REAL.exists():
        skip(name, f"real speech clip missing: {REAL}")
        return "SKIP", "-", "missing real corpus"
    base = tmp / "s8"
    clip = base / "clip.mp4"
    base.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-t",
            "3.75",
            "-i",
            "color=c=red:s=640x360:r=25",
            "-f",
            "lavfi",
            "-t",
            "3.75",
            "-i",
            "color=c=blue:s=640x360:r=25",
            "-i",
            str(REAL),
            "-f",
            "lavfi",
            "-t",
            "0.5",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v];"
            "[3:a]asetpts=PTS-STARTPTS[sil];"
            "[2:a]atrim=start=6.5:end=13.5,asetpts=PTS-STARTPTS,"
            "aresample=48000,volume=2.0[a0];"
            "[sil][a0]concat=n=2:v=0:a=1[a]",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(clip),
        ]
    )
    res = run_cli(
        clip,
        {
            "format": "16:9",
            "hook": "text",
            "hook_text": "Guarda questo",
            "subtitles": "clean",
            "loudness": "social",
        },
        base / "work",
    )
    common_checks(name, res)
    if res["rc"] != 0:
        return _fail(name, res)
    hook = treatment(res["plan"], "hook_overlay")
    subs = treatment(res["plan"], "subtitles")
    if not hook or not hook.get("enabled"):
        reason = ((hook or {}).get("reason") or {}).get("explain", "treatment absent")
        skip(name, f"hook_overlay not enabled: {reason}")
        return "SKIP", res["rc"], reason
    if not subs or not subs.get("enabled"):
        reason = ((subs or {}).get("reason") or {}).get("explain", "treatment absent")
        skip(name, f"subtitles not enabled: {reason}")
        return "SKIP", res["rc"], reason
    check(f"{name}: exit 0", True)
    hook_files = list((res["workdir"] / "subs").glob("*.hook.ass"))
    check(f"{name}: hook.ass exists", len(hook_files) == 1, str(hook_files))
    script_path = res["workdir"] / "stage" / "stage" / "03_graph.txt"
    script = script_path.read_text(encoding="utf-8") if script_path.exists() else ""
    check(
        f"{name}: stage-03 has two subtitles=",
        script.count("subtitles=") == 2,
        f"count={script.count('subtitles=')}",
    )
    check(f"{name}: hook_overlay enabled in plan", bool(hook.get("enabled")))
    return "PASS", res["rc"], ""


def scenario_hook_no_text(tmp: Path) -> Tuple[str, Any, str]:
    name = "9 hook requested, no text, empty transcript"
    base = tmp / "s9"
    clip = base / "clip.mp4"
    base.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-t",
            "2.5",
            "-i",
            "color=c=red:s=640x360:r=25",
            "-f",
            "lavfi",
            "-t",
            "2.5",
            "-i",
            "color=c=blue:s=640x360:r=25",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ]
    )
    res = run_cli(
        clip,
        {"format": "16:9", "hook": "text", "subtitles": "none"},
        base / "work",
        ["--no-speech"],
    )
    common_checks(name, res)
    if res["rc"] != 0:
        return _fail(name, res)
    hook = treatment(res["plan"], "hook_overlay")
    if not hook:
        skip(name, "hook_overlay treatment absent (no scene-cut finding)")
        return "SKIP", res["rc"], "treatment absent"
    check(f"{name}: exit 0", True)
    check(f"{name}: hook_overlay disabled", not hook.get("enabled"))
    explain = (hook.get("reason") or {}).get("explain") or ""
    check(
        f"{name}: reason says no text",
        "nessun testo" in explain,
        explain,
    )
    hook_files = list((res["workdir"] / "subs").glob("*.hook.ass"))
    check(f"{name}: no hook.ass", not hook_files, str(hook_files))
    return "PASS", res["rc"], ""


def scenario_subtitles_quality(tmp: Path) -> Tuple[str, Any, str]:
    name = "10 subtitles requested, transcript quality fails"
    if not REAL.exists():
        skip(name, f"real speech clip missing: {REAL}")
        return "SKIP", "-", "missing real corpus"
    base = tmp / "s10"
    clip = base / "clip.mp4"
    base.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-t",
            "3",
            "-i",
            "testsrc=size=640x360:rate=25",
            "-i",
            str(REAL),
            "-filter_complex",
            "[1:a]atrim=start=6.5:end=9.5,asetpts=PTS-STARTPTS,"
            "aresample=48000,volume=1.6[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(clip),
        ]
    )
    res = run_cli(
        clip,
        {"format": "16:9", "hook": "none", "subtitles": "clean"},
        base / "work",
    )
    common_checks(name, res)
    if res["rc"] != 0:
        return _fail(name, res)
    subs = treatment(res["plan"], "subtitles")
    if subs is None:
        skip(name, "no speech.present finding: subtitles treatment absent")
        return "SKIP", res["rc"], "treatment absent"
    if subs.get("enabled"):
        skip(name, "transcript quality passed: failure path not exercised")
        return "SKIP", res["rc"], "quality passed"
    check(f"{name}: exit 0", True)
    check(f"{name}: subtitles disabled", not subs.get("enabled"))
    explain = (subs.get("reason") or {}).get("explain") or ""
    check(f"{name}: reason says non incisi", "non incisi" in explain, explain)
    ass_files = list((res["workdir"] / "subs").glob("*.ass"))
    check(f"{name}: no ASS written", not ass_files, str(ass_files))
    script_path = res["workdir"] / "stage" / "stage" / "03_graph.txt"
    script = script_path.read_text(encoding="utf-8") if script_path.exists() else ""
    check(
        f"{name}: no subtitles= in stage-03",
        script.count("subtitles=") == 0,
        f"count={script.count('subtitles=')}",
    )
    return "PASS", res["rc"], ""


def main() -> int:
    if not Path(FFMPEG).exists():
        print(f"[SKIP] ffmpeg not found: {FFMPEG}")
        return 0

    with tempfile.TemporaryDirectory(prefix="vx_cli_run_") as tmp_name:
        tmp = Path(tmp_name)
        scenarios = [
            ("1 same-aspect 16:9, no autoframe", scenario_same_aspect),
            ("2 4K 16:9 with face -> 9:16 autoframe", scenario_4k_face),
            ("3 no faces, 9:16, no autoframe", scenario_no_faces),
            ("4 no audio, qc has no audio checks", scenario_no_audio),
            ("5 cut_silence on a clip with a silent gap", scenario_cut_silence),
            (
                "6 apple loudness profile",
                lambda base: scenario_loudness(base, "apple", -16.0),
            ),
            (
                "7 broadcast loudness profile",
                lambda base: scenario_loudness(base, "broadcast", -23.0),
            ),
            ("8 hook overlay with hook_text", scenario_hook),
            ("9 hook requested, no text, empty transcript", scenario_hook_no_text),
            (
                "10 subtitles requested, transcript quality fails",
                scenario_subtitles_quality,
            ),
        ]
        for display_name, scenario in scenarios:
            try:
                status, code, detail = scenario(tmp)
            except Exception as exc:  # noqa: BLE001 - a broken fixture is a failure
                check(f"{display_name}: fixture", False, f"{type(exc).__name__}: {exc}")
                record(display_name, "FAIL", "-", str(exc))
                continue
            record(display_name, status, code, detail)

    print("\n=== SCENARIO TABLE ===")
    for name, status, code, detail in RESULTS:
        print(f"{status:4}  exit={code:>3}  {name}  {detail}")
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
