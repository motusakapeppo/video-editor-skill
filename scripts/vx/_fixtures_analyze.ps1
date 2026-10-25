param(
    [string]$Out = (Join-Path $env:TEMP "vx_fixtures")
)

# Generates the synthetic clips used to verify vx.analyze (jitter, cuts, exposure).
# Run:  powershell -ExecutionPolicy Bypass -File _fixtures_analyze.ps1 [-Out <dir>]

$ErrorActionPreference = "Stop"
$FF = "ffmpeg"
$OUT = $Out
New-Item -ItemType Directory -Force -Path $OUT | Out-Null

function Invoke-FF([string[]]$FArgs) {
    & $FF -hide_banner -nostdin -y -loglevel error @FArgs
    if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed: $($FArgs -join ' ')" }
}

# 1. stable: moving test pattern, no camera motion
Invoke-FF @(
    "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=6",
    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
    "$OUT\vid_static.mp4"
)

# 2. shaky: same pattern, window jittered every frame by +-12 px, then rescaled.
#    `crop` evaluates x/y once at init in this build, so the jitter is applied by
#    overlaying the pattern on a larger canvas with a per-frame (t-based) offset.
Invoke-FF @(
    "-f", "lavfi", "-i", "color=c=black:size=1360x800:rate=30:duration=6",
    "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=6",
    "-filter_complex", "[0:v][1:v]overlay=x='40+12*sin(t*47)':y='40+12*cos(t*61)':shortest=1,crop=1280:720:40:40",
    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
    "$OUT\vid_shaky.mp4"
)

# 3. dark: underexposed by ~100 luma levels
Invoke-FF @(
    "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=4",
    "-vf", "eq=brightness=-0.55:saturation=0.7",
    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
    "$OUT\vid_dark.mp4"
)

# 4. three hard cuts at 2.0 / 4.0 / 6.0 (four 2 s segments)
Invoke-FF @(
    "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=2",
    "-f", "lavfi", "-i", "color=c=0xE03030:size=1280x720:rate=30:duration=2",
    "-f", "lavfi", "-i", "smptebars=size=1280x720:rate=30:duration=2",
    "-f", "lavfi", "-i", "color=c=0x3050E0:size=1280x720:rate=30:duration=2",
    "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]",
    "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
    "$OUT\vid_cuts3.mp4"
)

# 5. 10 s 720p clip for the wall-clock budget check
Invoke-FF @(
    "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=10",
    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
    "$OUT\vid_10s.mp4"
)

Get-ChildItem "$OUT\vid_*.mp4" | Select-Object Name, Length
