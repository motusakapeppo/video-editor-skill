"""Crop-expression smoke test: synthetic face track -> ffmpeg render -> probe.

Builds a 6 s synthetic track, turns it into a crop expression, renders it with
ffmpeg and probes the result. Expected: 180 frames at 1080x1920.

Usage:
    python examples/track_expr_test.py

Environment:
    VX_PYTHON   interpreter that can import the vx package (default: sys.executable)
    VX_FFMPEG   ffmpeg binary (default: "ffmpeg" from PATH)
    VX_FFPROBE  ffprobe binary (default: "ffprobe" from PATH)
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent

sys.path.insert(0, str(SKILL_DIR / "scripts"))
from vx.autoframe import build_crop_plan, crop_expression  # noqa: E402
from vx.contract import FaceTracks  # noqa: E402

FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"
FFPROBE = os.environ.get("VX_FFPROBE") or "ffprobe"

samples = [
    {
        "t": i * 0.25,
        "x": 200 + i * 90.0,
        "y": 300,
        "w": 200,
        "h": 220,
        "eye_y": 360,
        "mar": 0.02,
    }
    for i in range(24)
]
tracks = FaceTracks(
    detected_ratio=1.0,
    tracks=[
        {
            "id": 1,
            "start": 0.0,
            "end": 6.0,
            "samples": samples,
            "mean_area": 44000.0,
            "duration_s": 6.0,
            "centrality": 0.7,
            "score": 1.4,
            "persistence": 1.0,
        }
    ],
    max_concurrent=1,
    framing="single",
    detector="synthetic",
    span_x_ratio=0.2,
)
plan = build_crop_plan(tracks, 1920, 1080, aspect="9:16")
expr = crop_expression(plan)
graph = (
    f"[0:v]crop=w={plan.crop_w}:h={plan.crop_h}:x='{expr['x']}':y='{expr['y']}',"
    "scale=1080:1920,setsar=1,format=yuv420p[v]"
)
script = tempfile.NamedTemporaryFile(
    "w", suffix=".txt", delete=False, encoding="utf-8", newline="\n"
)
script.write(graph)
script.close()
out = str(Path(tempfile.mkdtemp(prefix="vx_track_")) / "track_test.mp4")
cmd = [
    FFMPEG,
    "-hide_banner",
    "-nostdin",
    "-y",
    "-loglevel",
    "error",
    "-f",
    "lavfi",
    "-i",
    "testsrc2=size=1920x1080:rate=30:duration=6",
    "-filter_complex_script",
    script.name,
    "-map",
    "[v]",
    "-c:v",
    "libx264",
    "-crf",
    "23",
    out,
]
proc = subprocess.run(
    cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
)
print("ffmpeg rc:", proc.returncode, (proc.stderr or "")[-300:])
probe = subprocess.run(
    [
        FFPROBE,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-count_frames",
        "-show_entries",
        "stream=nb_read_frames,width,height",
        "-of",
        "csv=p=0",
        out,
    ],
    capture_output=True,
    text=True,
)
print("out:", probe.stdout.strip(), "(atteso 180 frame 1080x1920)")
print("mode:", plan.mode, "| crop:", plan.crop_w, "x", plan.crop_h)
os.unlink(script.name)
