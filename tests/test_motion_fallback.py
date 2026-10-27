"""F3-T06 acceptance — the Pillow raster fallback, measured against the contract.

    python tests/test_motion_fallback.py

Script style: every check prints PASS/FAIL, the process exits 0 only when all
checks pass. Nothing is committed and nothing leaves the machine.

What it proves:

a. three families (title, chart, subtitle) render twice with the same parameters
   and give byte-identical PNGs and identical sha256 for every frame;
b. a different parameter changes the picture (the mid frame hash moves);
c. the fallback contract matches ``motion.capture``: frame names, frame count,
   fps and size are the same for the same template;
d. ``resolve_engine`` follows the declared matrix exactly, including the
   unavailable branch and the engine-only property set that may never be
   downgraded;
e. the module source contains no ``playwright`` token and importing it starts no
   browser (checked in a fresh interpreter);
f. the two gates of the browser path are the fallback's gates too: the static
   lint refuses a broken template before any pixel, and the shared validator
   refuses an unknown parameter with PARAM_UNKNOWN.

The measured numbers (mid-frame sha256, bytes, seconds per frame) are printed and
written to ``tests/fixtures/motion_fallback_proof.json``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from PIL import Image  # noqa: E402

from vx import motion  # noqa: E402
from vx import motion_fallback  # noqa: E402
from vx import motion_lint  # noqa: E402

CEILING_SECONDS = 480.0
FPS = 10.0
SIZE = (320, 568)
PROOF = ROOT / "tests" / "fixtures" / "motion_fallback_proof.json"

PASS = 0
FAIL = 0
SKIP = 0

#: (family, template, parameters, one changed parameter)
CASES = (
    (
        "title",
        "vx-title-lower-third",
        {
            "title": "Ada Lovelace",
            "subtitle": "Analytical Engine",
            "accent": "#38E1FF",
            "position": "bottom-left",
        },
        {"title": "Grace Hopper"},
    ),
    (
        "chart",
        "vx-counter-odometer",
        {
            "value": 4820,
            "caption": "Total downloads",
            "note": "last 30 days",
            "accent": "#FFD400",
        },
        {"value": 7},
    ),
    (
        "subtitle",
        "vx-subtitle-box",
        {
            "text": "Qui comincia il viaggio",
            "accent": "#FF6B4A",
            "radius": 26,
            "casing": "none",
        },
        {"text": "Altra frase, stesse regole"},
    ),
)


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:68} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:68} {reason}")


def rgba_bytes(path: str | Path) -> bytes:
    with Image.open(path) as handle:
        handle.load()
        return handle.convert("RGBA").tobytes()


def transparent_share(alpha: bytes) -> float:
    if not alpha:
        return 0.0
    return alpha.count(0) / float(len(alpha))


def mid_of(report: dict) -> int:
    return report["frame_count"] // 2


def write_broken_template(root: Path) -> Path:
    """A template the static linter must refuse without a browser."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        "<!doctype html><html><body>"
        '<img src="https://example.com/x.png" alt="">'
        "</body></html>",
        encoding="utf-8",
    )
    (root / "template.json").write_text(
        '{"name": "broken-probe", "fps": 10, "duration": 0.2,'
        ' "size": [160, 160], "params": {}}',
        encoding="utf-8",
    )
    return root


def fresh_import_has_no_browser() -> tuple:
    """Import the module in a new interpreter and look at ``sys.modules``."""
    script = (
        "import sys\n"
        "import vx.motion_fallback as fallback\n"
        "print('PLAYWRIGHT_LOADED=' + str('playwright' in sys.modules))\n"
        "print('MARKER=' + fallback.FALLBACK_DEGRADED[0])\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "scripts")
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, text


def resolve_matrix() -> None:
    print("\n-- (d) resolve_engine, the whole matrix")
    ok_probe = {
        "available": True,
        "engine": "chrome",
        "reason": "ok",
        "candidates": ["chrome"],
    }
    no_probe = {
        "available": False,
        "engine": None,
        "reason": "ENGINE_NOT_FOUND: neither Chrome nor Edge is installed",
        "candidates": [],
    }
    cases = [
        ("pillow", no_probe, (), "pillow", False, []),
        ("pillow", ok_probe, ("reflow",), "pillow", False, []),
        ("chromium", ok_probe, (), "chromium", False, []),
        ("chromium", ok_probe, ("layout",), "chromium", False, []),
        ("chromium", no_probe, (), "", False, []),
        ("chromium", no_probe, ("layout",), "", False, []),
        ("chromium", no_probe, ("reflow", "layout"), "", False, []),
        ("auto", ok_probe, (), "chromium", False, []),
        ("auto", ok_probe, ("reflow",), "chromium", False, []),
        ("auto", no_probe, (), "pillow", True, ["html", "layout", "effects"]),
        ("auto", no_probe, ("layout",), "pillow", True, ["html", "layout", "effects"]),
        ("auto", no_probe, ("reflow",), "", False, []),
        ("auto", no_probe, ("backdrop_filter",), "", False, []),
        ("auto", no_probe, ("blend_animated",), "", False, []),
        ("auto", no_probe, ("layout", "reflow"), "", False, []),
    ]
    for declared, probe, properties, engine, fallback, degraded in cases:
        first = motion_fallback.resolve_engine(declared, probe, properties)
        second = motion_fallback.resolve_engine(declared, dict(probe), list(properties))
        label = f"{declared} / probe={'ok' if probe['available'] else 'no'} / {list(properties) or 'none'}"
        check(
            f"{label:<52} -> {engine or 'unavailable'}",
            first["engine"] == engine
            and first["fallback"] is fallback
            and first["degraded"] == degraded
            and bool(first["reason"])
            and first == second,
            f"engine={first['engine'] or 'unavailable'} fallback={first['fallback']}",
        )
    try:
        motion_fallback.resolve_engine("blink", ok_probe, ())
    except ValueError as exc:
        check(
            "an undeclared engine is a usage error, not a silent default",
            True,
            str(exc)[:64],
        )
    else:
        check(
            "an undeclared engine is a usage error, not a silent default",
            False,
            "no error",
        )
    check(
        "engine-only properties are the three the card cannot fake",
        motion_fallback.ENGINE_ONLY_PROPERTIES
        == {"backdrop_filter", "blend_animated", "reflow"},
        sorted(motion_fallback.ENGINE_ONLY_PROPERTIES),
    )


def main() -> int:
    started = time.perf_counter()
    proof_rows = []
    browser = motion.available()
    print(
        f"\n-- engine probe: available={browser['available']} "
        f"engine={browser['engine']} reason={browser['reason']}"
    )
    try:
        with tempfile.TemporaryDirectory(
            prefix="vx_motion_fallback_", ignore_cleanup_errors=True
        ) as tmp:
            work = Path(tmp)
            for family, template, params, changed in CASES:
                print(f"\n-- ({family}) {template}")
                first = motion_fallback.capture(
                    template, params, work / f"{family}_a", fps=FPS, size=SIZE
                )
                second = motion_fallback.capture(
                    template, params, work / f"{family}_b", fps=FPS, size=SIZE
                )
                mid = mid_of(first)
                check(
                    f"a. {family}: same frame count and fps on both runs",
                    first["frame_count"] == second["frame_count"]
                    and first["fps"] == second["fps"] == FPS
                    and first["size"] == second["size"] == list(SIZE),
                    f"{first['frame_count']} frames, {first['fps']} fps, {first['size']}",
                )
                check(
                    f"a. {family}: identical sha256 for every frame",
                    first["frame_sha256"] == second["frame_sha256"]
                    and len(first["frame_sha256"]) == first["frame_count"],
                    f"{len(first['frame_sha256'])} frame(s), mismatch="
                    f"{sum(1 for a, b in zip(first['frame_sha256'], second['frame_sha256']) if a != b)}",
                )
                check(
                    f"a. {family}: the mid frame is byte-identical on disk",
                    Path(first["frames"][mid]).read_bytes()
                    == Path(second["frames"][mid]).read_bytes(),
                    Path(first["frames"][mid]).name,
                )
                names = [Path(path).name for path in first["frames"]]
                check(
                    f"a. {family}: frames are frame_%05d.png",
                    names == [f"frame_{index:05d}.png" for index in range(len(names))],
                    f"{names[0]} .. {names[-1]}",
                )
                alpha = rgba_bytes(first["frames"][mid])[3::4]
                share = transparent_share(alpha)
                check(
                    f"a. {family}: RGBA with a transparent canvas",
                    share > 0.5 and 0 < alpha.count(255) < len(alpha),
                    f"{share * 100:.1f}% fully transparent, "
                    f"{alpha.count(255)} opaque px",
                )
                check(
                    f"a. {family}: engine/fallback/degraded are declared",
                    first["engine"] == "pillow"
                    and first["fallback"] is True
                    and first["degraded"] == ["html", "layout", "effects"]
                    and first["blocked_requests"] == [],
                    f"{first['engine']} / {first['degraded']}",
                )
                check(
                    f"a. {family}: fps maths is max(1, round(duration * rate))",
                    first["frame_count"] == max(1, int(round(first["duration"] * FPS))),
                    f"{first['duration']}s x {FPS} -> {first['frame_count']}",
                )

                other_params = dict(params)
                other_params.update(changed)
                other = motion_fallback.capture(
                    template, other_params, work / f"{family}_c", fps=FPS, size=SIZE
                )
                check(
                    f"b. {family}: a different parameter changes the picture",
                    other["frame_sha256"][mid] != first["frame_sha256"][mid],
                    f"{other['frame_sha256'][mid][:16]} != "
                    f"{first['frame_sha256'][mid][:16]} (mid {mid})",
                )

                if not browser["available"]:
                    skip(
                        f"c. {family}: parity with motion.capture",
                        "no system browser, the browser capture cannot run",
                    )
                else:
                    reference = motion.capture(
                        template,
                        params,
                        work / f"{family}_ref",
                        fps=FPS,
                        size=SIZE,
                        frame_timeout_s=30.0,
                    )
                    check(
                        f"c. {family}: same frames, fps, size and frame count",
                        [Path(p).name for p in reference["frames"]]
                        == [Path(p).name for p in first["frames"]]
                        and reference["fps"] == first["fps"]
                        and reference["size"] == first["size"]
                        and reference["frame_count"] == first["frame_count"],
                        f"{reference['frame_count']} browser frames vs "
                        f"{first['frame_count']} card frames",
                    )
                    check(
                        f"c. {family}: the two engines really differ",
                        reference["frame_sha256"][mid] != first["frame_sha256"][mid],
                        "browser and raster hash the same frame differently",
                    )

                mid_bytes = Path(first["frames"][mid]).stat().st_size
                print(
                    f"       measured: {first['frame_count']} frames, "
                    f"{first['elapsed_s']:.3f}s total, "
                    f"{first['seconds_per_frame'] * 1000:.1f} ms/frame, "
                    f"mid sha256 {first['frame_sha256'][mid][:16]}, {mid_bytes} bytes"
                )
                proof_rows.append(
                    {
                        "name": family,
                        "template": first["template"],
                        "frame": Path(first["frames"][mid]).name,
                        "mid_sha256": first["frame_sha256"][mid],
                        "bytes": mid_bytes,
                        "engine": first["engine"],
                        "degraded": first["degraded"],
                        "frame_count": first["frame_count"],
                        "fps": first["fps"],
                        "size": first["size"],
                        "elapsed_s": first["elapsed_s"],
                        "seconds_per_frame": first["seconds_per_frame"],
                    }
                )

            print("\n-- (e) no browser, no Playwright")
            source = Path(motion_fallback.__file__).read_text(encoding="utf-8")
            check(
                "e. the module source contains no playwright token",
                "playwright" not in source,
                f"{len(source)} chars scanned",
            )
            launched, text = fresh_import_has_no_browser()
            check(
                "e. importing it in a fresh interpreter loads no Playwright",
                launched and "PLAYWRIGHT_LOADED=False" in text,
                [line for line in text.splitlines() if "PLAYWRIGHT" in line],
            )

            print("\n-- (f) the same two gates as the browser path")
            broken = write_broken_template(work / "broken")
            try:
                motion_fallback.capture(broken, {}, work / "broken_out")
            except motion_lint.MotionLintError as exc:
                check(
                    "f. the static lint refuses a broken template first",
                    bool(getattr(exc, "findings", None))
                    and not (work / "broken_out").exists(),
                    f"{len(exc.findings)} finding(s), no frame written",
                )
            except Exception as exc:  # noqa: BLE001 - wrong error type is a failure
                check(
                    "f. the static lint refuses a broken template first",
                    False,
                    f"{type(exc).__name__}: {exc}",
                )
            else:
                check(
                    "f. the static lint refuses a broken template first",
                    False,
                    "no error raised",
                )
            try:
                motion_fallback.capture(
                    "vx-title-lower-third",
                    {"nope": "x"},
                    work / "params_out",
                    fps=FPS,
                    size=SIZE,
                )
            except motion.MotionError as exc:
                check(
                    "f. the shared validator refuses an unknown parameter",
                    exc.code == "PARAM_UNKNOWN" and "nope" in str(exc),
                    str(exc),
                )
            except Exception as exc:  # noqa: BLE001 - wrong error type is a failure
                check(
                    "f. the shared validator refuses an unknown parameter",
                    False,
                    f"{type(exc).__name__}: {exc}",
                )
            else:
                check(
                    "f. the shared validator refuses an unknown parameter",
                    False,
                    "no error raised",
                )

            resolve_matrix()

            PROOF.parent.mkdir(parents=True, exist_ok=True)
            PROOF.write_text(
                json.dumps(
                    {
                        "schema": "vx.motion.fallback.proof/1",
                        "fps": FPS,
                        "size": list(SIZE),
                        "rows": proof_rows,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            check(
                "g. the proof fixture lists one row per rendered family",
                len(proof_rows) == len(CASES) and PROOF.is_file(),
                f"{len(proof_rows)} row(s) in {PROOF.name}",
            )
    except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
        import traceback

        traceback.print_exc()
        check("harness completed without an unexpected exception", False, str(exc))

    elapsed = time.perf_counter() - started
    slow = elapsed > CEILING_SECONDS
    print(
        f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  "
        f"({elapsed:.1f}s, ceiling {CEILING_SECONDS:.0f}s)"
    )
    if slow:
        print(f"FAIL: suite took {elapsed:.1f}s, above the ceiling")
        return 1
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
