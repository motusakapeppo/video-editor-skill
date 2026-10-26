"""`vx run --payload` end-to-end: prompt -> payload -> EditPlan -> render -> QC."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import decide  # noqa: E402

PASS = 0
FAIL = 0
PYTHON = sys.executable
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"
FFPROBE = os.environ.get("VX_FFPROBE") or "ffprobe"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:58} {detail}")


def run_cli(args: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "scripts")
    env["VX_FFMPEG"] = FFMPEG
    return subprocess.run(
        [PYTHON, "-m", "vx.cli", "run", *args, "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )


def summary_of(stdout: str) -> dict:
    start = stdout.find("{")
    if start < 0:
        return {}
    try:
        return json.loads(stdout[start:])
    except json.JSONDecodeError:
        return {}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        clip = base / "clip.mp4"
        subprocess.run(
            [
                FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=640x360:rate=30:duration=2",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(clip),
            ],
            check=True,
        )

        payload = decide.parse_and_decide(
            "un reel con stile vintage e transizioni dinamiche"
        )
        payload_path = base / "scene_payload.json"
        payload_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        work = base / "work"
        proc = run_cli(
            [
                "--input",
                str(clip),
                "--payload",
                str(payload_path),
                "--workdir",
                str(work),
            ]
        )
        check("payload: exit 0", proc.returncode == 0, proc.stderr[-300:])
        summary = summary_of(proc.stdout)
        check(
            "payload: qc pass",
            summary.get("qc_pass") is True,
            summary.get("qc_failures"),
        )
        output = Path(summary.get("output") or "")
        check(
            "payload: output esiste",
            output.is_file() and output.stat().st_size > 0,
            output,
        )
        if output.is_file():
            probe = subprocess.run(
                [
                    FFPROBE,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "csv=p=0",
                    str(output),
                ],
                capture_output=True,
                text=True,
            )
            check(
                "payload: output 1080x1920",
                probe.stdout.strip() == "1080,1920",
                probe.stdout.strip(),
            )

        plan = json.loads((work / "plan.json").read_text(encoding="utf-8"))
        color = next(
            (t for t in plan["treatments"] if t.get("name") == "color_fix"), None
        )
        check("payload: color_fix attivo", bool(color and color.get("enabled")), color)
        filter_text = ((color or {}).get("params") or {}).get("filter", "")
        check(
            "payload: duotone nel piano",
            "hue=s=0" in filter_text and "curves=" in filter_text,
            filter_text,
        )
        check(
            "payload: evidence autonoma",
            (color or {}).get("evidence")
            == [{"finding_id": "brief.autonomous", "t0": 0.0, "t1": 2.0}],
            (color or {}).get("evidence"),
        )
        bridge = plan["brief"].get("bridge") or {}
        check(
            "payload: bridge.executed",
            "color_fix" in (bridge.get("executed") or []),
            bridge.get("executed"),
        )
        finding = (plan["brief"].get("autonomous_findings") or [{}])[0]
        check("payload: finding declared", finding.get("declared") is True, finding)

        graph_path = work / "stage" / "stage" / "03_graph.txt"
        graph = graph_path.read_text(encoding="utf-8") if graph_path.exists() else ""
        check("payload: grafo con hue", "hue=s=0" in graph)
        check("payload: grafo con curves", "curves=" in graph)

        strict = run_cli(
            [
                "--input",
                str(clip),
                "--prompt",
                "Reel 1080x1920, font Antonio, stile vintage, 120 bpm, "
                "15 sec, engine=pillow",
                "--workdir",
                str(base / "strict"),
            ]
        )
        check(
            "prompt strict ambiguo: exit 3", strict.returncode == 3, strict.returncode
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
