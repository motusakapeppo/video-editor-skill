"""F3-T01 + F3-T02 gate -- the Motion Studio vertical slice, on the real engine.

    python tests/test_motion_slice.py

Script-style: every check prints PASS/FAIL, the process exits 0 only when all
checks pass. It drives the system Chrome (or Edge) through Playwright's channel;
nothing is downloaded and nothing reaches the network.

What it proves, in order:

a. `available()` names the engine that capture then really launched.
b. two captures with identical parameters give identical sha256 for every frame.
c. a mid-animation frame is at least 30% fully transparent (real alpha).
d. the picture moves: MAD between t=0.2 and t=1.2 is above 3 on visible pixels.
e. our template makes no request; a template with a remote image is blocked and
   the request is recorded instead of fetched.
f. `validate_params` refuses an unknown parameter and an out-of-domain value
   with PARAM_UNKNOWN / PARAM_RANGE.
g. `to_video` writes VP9/WebM with yuva420p when alpha is asked and H.264/MP4
   with yuv420p when it is not; the encoded alpha survives a decode.
h. seconds per frame at 1080x1920, stated against the 5 s per-template budget
   even when it does not fit.
i. a template whose `__vxSeek` never resolves dies at the declared timeout with
   a Frame error naming the index, and leaves no browser process behind.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from PIL import Image  # noqa: E402

from vx import ffmpeg as ff  # noqa: E402
from vx import motion  # noqa: E402

TEMPLATE = ROOT / "assets" / "templates" / "vx-title-lower-third"
BUDGET_SECONDS = 5.0
CEILING_SECONDS = 120.0
SMALL = (360, 640)

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:<58} {detail}")


def note(label: str, detail: str) -> None:
    print(f"    [INFO] {label:<58} {detail}")


def rgba_bytes(path: str | Path) -> bytes:
    with Image.open(path) as handle:
        handle.load()
        return handle.convert("RGBA").tobytes()


def transparent_share(alpha: bytes) -> float:
    if not alpha:
        return 0.0
    return alpha.count(0) / float(len(alpha))


def mad_visible(first: str | Path, second: str | Path) -> float:
    a, b = rgba_bytes(first), rgba_bytes(second)
    total = 0
    count = 0
    for index in range(0, len(a), 4):
        if a[index + 3] or b[index + 3]:
            total += (
                abs(a[index] - b[index])
                + abs(a[index + 1] - b[index + 1])
                + abs(a[index + 2] - b[index + 2])
            )
            count += 3
    return total / float(count) if count else 0.0


def browser_pids(engine: str) -> set:
    image = "chrome.exe" if engine == "chrome" else "msedge.exe"
    if os.name == "nt":
        proc = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return {pid for _, pid in re.findall(r'"([^"]+)","(\d+)"', proc.stdout or "")}
    proc = subprocess.run(
        ["ps", "-A", "-o", "pid=,comm="],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    stem = image.replace(".exe", "")
    return {
        line.split()[0]
        for line in (proc.stdout or "").splitlines()
        if len(line.split()) >= 2 and stem in line.split()[1]
    }


def write_remote_template(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        "<!doctype html><html><body>"
        '<img src="https://example.com/x.png" alt="">'
        "<script>window.__vxSeek = function () {"
        " return new Promise(function (r) { requestAnimationFrame(r); }); };"
        "</script></body></html>",
        encoding="utf-8",
    )
    (root / "template.json").write_text(
        '{"name": "remote-probe", "fps": 5, "duration": 0.4,'
        ' "size": [160, 160], "params": {}}',
        encoding="utf-8",
    )
    return root


def write_stuck_template(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        "<!doctype html><html><body style='background:transparent'>"
        "<script>window.__vxSeek = function () {"
        " return new Promise(function () {}); };"
        "</script></body></html>",
        encoding="utf-8",
    )
    (root / "template.json").write_text(
        '{"name": "never-paints", "fps": 10, "duration": 1.0,'
        ' "size": [320, 320], "params": {}}',
        encoding="utf-8",
    )
    return root


def run(work: Path) -> Optional[float]:
    params = {
        "title": "Ada Lovelace",
        "subtitle": "Analytical Engine",
        "accent": "#38E1FF",
        "position": "bottom-left",
    }

    probe = motion.available()
    check(
        "a. available() names a system engine",
        bool(probe["available"]) and probe["engine"] in ("chrome", "msedge"),
        f"engine={probe['engine']} reason={probe['reason']}",
    )
    if not probe["available"]:
        return None
    engine = probe["engine"]

    first = motion.capture(
        TEMPLATE, params, work / "run1", fps=10, size=SMALL, frame_timeout_s=30.0
    )
    second = motion.capture(
        TEMPLATE, params, work / "run2", fps=10, size=SMALL, frame_timeout_s=30.0
    )
    check(
        "a. the engine launched is the engine reported",
        first["engine"] == second["engine"] == engine,
        f"reported={engine} used={first['engine']}",
    )

    check(
        "b. two captures: same frame count",
        first["frame_count"] == second["frame_count"] == 20,
        f"{first['frame_count']} / {second['frame_count']}",
    )
    mismatched = [
        index
        for index, (left, right) in enumerate(
            zip(first["frame_sha256"], second["frame_sha256"])
        )
        if left != right
    ]
    check(
        "b. two captures: identical sha256 for every frame",
        not mismatched and first["frame_sha256"] == second["frame_sha256"],
        f"{len(first['frame_sha256'])} frames, {len(mismatched)} differ",
    )
    check(
        "b. identical bytes on disk, not just equal hashes",
        Path(first["frames"][0]).read_bytes() == Path(second["frames"][0]).read_bytes(),
        Path(first["frames"][0]).name,
    )

    mid = rgba_bytes(first["frames"][10])[3::4]
    share = transparent_share(mid)
    check(
        "c. alpha is real on a mid-animation frame",
        share >= 0.30,
        f"{share * 100:.1f}% fully transparent",
    )

    movement = mad_visible(first["frames"][2], first["frames"][12])
    check(
        "d. the picture moves between t=0.2 and t=1.2",
        movement > 3.0,
        f"MAD={movement:.2f} on visible pixels",
    )

    check(
        "e. our template makes no external request",
        first["blocked_requests"] == [],
        f"blocked={first['blocked_requests']}",
    )
    remote = motion.capture(
        write_remote_template(work / "remote"),
        {},
        work / "remote_out",
        frame_timeout_s=30.0,
        skip_lint=True,
    )
    blocked = remote["blocked_requests"]
    check(
        "e. a remote image is blocked and recorded",
        bool(blocked) and all("example.com" in url for url in blocked),
        f"blocked={blocked}",
    )
    check(
        "e. the blocked capture still produced frames",
        remote["frame_count"] == 2,
        f"{remote['frame_count']} frames",
    )

    resolved = motion.validate_params(TEMPLATE, {"title": "Grace"})
    check(
        "f. defaults are resolved for omitted parameters",
        resolved.get("title") == "Grace"
        and resolved.get("position") == "bottom-left"
        and resolved.get("accent") == "#FFD400",
        str(sorted(resolved)),
    )
    try:
        motion.validate_params(TEMPLATE, {"nope": 1})
    except motion.MotionError as exc:
        check(
            "f. unknown parameter -> PARAM_UNKNOWN",
            exc.code == "PARAM_UNKNOWN" and "nope" in str(exc),
            str(exc),
        )
    else:
        check("f. unknown parameter -> PARAM_UNKNOWN", False, "no error raised")
    try:
        motion.validate_params(TEMPLATE, {"position": "middle"})
    except motion.MotionError as exc:
        check(
            "f. out-of-domain value -> PARAM_RANGE naming it",
            exc.code == "PARAM_RANGE"
            and "position" in str(exc)
            and "bottom-left" in str(exc),
            str(exc),
        )
    else:
        check(
            "f. out-of-domain value -> PARAM_RANGE naming it", False, "no error raised"
        )

    webm = motion.to_video(work / "run1", work / "slice.webm", fps=10, alpha=True)
    check(
        "g. alpha output is VP9/WebM with yuva420p",
        webm["codec"] == "vp9"
        and webm["pix_fmt"] == "yuva420p"
        and webm["alpha_mode"] == "1",
        f"codec={webm['codec']} pix_fmt={webm['pix_fmt']} "
        f"alpha_mode={webm['alpha_mode']} base={webm.get('container_pix_fmt')}",
    )
    decoded = work / "decoded.png"
    decoded_ok = False
    detail = "decode failed"
    proc = ff.run_ff(
        [
            "-c:v",
            "libvpx-vp9",
            "-ss",
            "1.2",
            "-i",
            str(work / "slice.webm"),
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgba",
            str(decoded),
        ],
        loglevel="error",
    )
    if proc.ok and decoded.is_file():
        alpha = rgba_bytes(decoded)[3::4]
        decoded_ok = alpha.count(0) > 0 and alpha.count(255) > 0
        detail = f"decoded alpha: {transparent_share(alpha) * 100:.1f}% transparent"
    check("g. encoded alpha survives a decode", decoded_ok, detail)

    mp4 = motion.to_video(work / "run1", work / "slice.mp4", fps=10, alpha=False)
    check(
        "g. opaque output is H.264/MP4 with yuv420p",
        mp4["codec"] == "h264"
        and mp4["pix_fmt"] == "yuv420p"
        and not mp4["alpha_detected"],
        f"codec={mp4['codec']} pix_fmt={mp4['pix_fmt']}",
    )

    full = motion.capture(
        TEMPLATE, params, work / "full", frame_timeout_s=30.0, capture_timeout_s=120.0
    )
    per_frame = full["seconds_per_frame"]
    fits = full["elapsed_s"] <= BUDGET_SECONDS
    note(
        "h. 1080x1920 timing",
        f"{full['frame_count']} frames, {full['elapsed_s']:.2f}s total, "
        f"{per_frame * 1000:.0f} ms/frame, budget {BUDGET_SECONDS:.2f}s -> "
        f"{'fits' if fits else 'over budget'}",
    )
    check(
        "h. full-res capture produced every declared frame",
        full["frame_count"] == 60,
        f"{full['frame_count']} frames at {full['size'][0]}x{full['size'][1]}",
    )

    before = browser_pids(engine)
    timed_out = None
    started = time.perf_counter()
    try:
        motion.capture(
            write_stuck_template(work / "stuck"),
            {},
            work / "stuck_out",
            frame_timeout_s=1.0,
            capture_timeout_s=8.0,
        )
    except motion.MotionError as exc:
        timed_out = exc
    waited = time.perf_counter() - started
    check(
        "i. a never-resolving seek is killed at the declared timeout",
        timed_out is not None
        and timed_out.code == "FRAME_TIMEOUT"
        and "frame 0" in str(timed_out),
        f"{timed_out.code if timed_out else 'no error'} after {waited:.2f}s",
    )
    orphans: set = set()
    for _ in range(20):
        orphans = browser_pids(engine) - before
        if not orphans:
            break
        time.sleep(0.5)
    check(
        "i. no browser process is left behind",
        not orphans,
        f"orphans={sorted(orphans)}" if orphans else "process list back to baseline",
    )
    return full["elapsed_s"]


def main() -> int:
    global PASS, FAIL
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(
            prefix="vx_motion_slice_", ignore_cleanup_errors=True
        ) as tmp:
            run(Path(tmp))
    except motion.MotionError as exc:
        print(f"\nFAIL: declared error {exc.code}: {exc}")
        print(exc.payload)
        FAIL += 1
    except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
        print(f"\nFAIL: {type(exc).__name__}: {exc}")
        FAIL += 1
    elapsed = time.perf_counter() - started
    slow = elapsed > CEILING_SECONDS
    print(
        f"\n{PASS} PASS / {FAIL} FAIL, {elapsed:.1f}s (ceiling {CEILING_SECONDS:.0f}s)"
    )
    if slow:
        print(f"FAIL: suite took {elapsed:.1f}s, above the ceiling")
        return 1
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
