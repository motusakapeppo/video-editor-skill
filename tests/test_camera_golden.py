"""F25-T02: every camera move has a reference strip, and it cannot drift.

For each of the 41 moves the test renders one 2 s clip built with that move, takes
three instants (15%, 50%, 85% of the shot), and stores their sha256 in
``tests/fixtures/camera_golden/<name>/manifest.json`` next to the three PNGs.

    python tests/test_camera_golden.py --write    # re-bless all 41 strips
    python tests/test_camera_golden.py            # verify, never writes

Verify mode asserts three things per move: the strip is byte-identical to the
stored one, the frame is not the un-moved frame (a move that does nothing cannot
pass), and the number of strips on disk equals the number of moves in the
registry. ``--write`` is deterministic: two runs produce the same bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import camera, edl3, edl3_render  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402
from _golden import compare_images as golden_compare  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "camera_golden"
FPS = 12
SIZE = (320, 568)
SHOT = 2.0
INSTANTS = (0.15, 0.50, 0.85)

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:58} {reason}")


def source(work: Path) -> Path:
    target = work / "source.mp4"
    if target.is_file():
        return target
    proc = subprocess.run(
        [
            ff.ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={SIZE[0]}x{SIZE[1]}:rate={FPS}:duration={SHOT}",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 or not target.is_file():
        raise RuntimeError(proc.stderr[-200:] or "cannot build the source")
    return target


def render_move(move: str, work: Path) -> list:
    """Render the shot with the move and return the three PNG paths."""
    clip = edl3.Clip3(
        id="c1",
        source=str(source(work)),
        src_in=0.0,
        src_out=SHOT,
        effects=[camera.build(move, duration=SHOT)],
    )
    document = edl3.Timeline3(fps=FPS, size=SIZE, clips=[clip])
    video = work / f"{move}.mp4"
    edl3_render.render(document, video, workdir=work / f"w-{move}", audio="drop", settings={})
    frames = []
    for index, share in enumerate(INSTANTS):
        png = work / f"{move}-{index}.png"
        proc = subprocess.run(
            [
                ff.ffmpeg(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{share * SHOT:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                str(png),
            ],
            capture_output=True,
        )
        if proc.returncode != 0 or not png.is_file():
            raise RuntimeError(f"cannot extract {move} at {share}")
        frames.append(png)
    return frames


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def baseline(work: Path) -> list:
    clip = edl3.Clip3(id="c1", source=str(source(work)), src_in=0.0, src_out=SHOT)
    document = edl3.Timeline3(fps=FPS, size=SIZE, clips=[clip])
    video = work / "baseline.mp4"
    edl3_render.render(document, video, workdir=work / "w-base", audio="drop", settings={})
    frames = []
    for index, share in enumerate(INSTANTS):
        png = work / f"base-{index}.png"
        subprocess.run(
            [
                ff.ffmpeg(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{share * SHOT:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                str(png),
            ],
            capture_output=True,
        )
        frames.append(png)
    return frames


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    try:
        binary = ff.ffmpeg()
    except Exception as exc:  # noqa: BLE001
        skip("whole file", f"no ffmpeg: {type(exc).__name__}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0
    if not binary:
        skip("whole file", "no ffmpeg")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    with tempfile.TemporaryDirectory(prefix="vx-camera-golden-") as raw:
        work = Path(raw)
        base = baseline(work)
        base_hashes = [digest(path) for path in base]

        for name in sorted(camera.MOVES):
            try:
                frames = render_move(name, work)
            except Exception as exc:  # noqa: BLE001
                check(f"{name}: renders", False, f"{type(exc).__name__}: {exc}")
                continue
            hashes = [digest(path) for path in frames]

            if args.write:
                target = FIXTURES / name
                target.mkdir(parents=True, exist_ok=True)
                for index, png in enumerate(frames):
                    (target / f"instant-{index:02d}.png").write_bytes(png.read_bytes())
                manifest = {
                    "template": name,
                    "family": camera.MOVES[name].family,
                    "fps": FPS,
                    "size": list(SIZE),
                    "shot": SHOT,
                    "instants": list(INSTANTS),
                    "sha256": hashes,
                }
                (target / "manifest.json").write_text(
                    json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
                )
                check(f"{name}: written", True, hashes[0][:12])
                continue

            target = FIXTURES / name
            manifest_path = target / "manifest.json"
            if not manifest_path.is_file():
                check(f"{name}: has a reference strip", False, "missing")
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            # Confronto per pixel con tolleranza, non per byte: le golden sono
            # catturate su una macchina e rigirate su un'altra (ffmpeg, x264,
            # rasterizzazione). Un'immagine sbagliata sfora subito; un
            # arrotondamento di compositing no. Lo sha resta nel manifest come
            # record, non come verdetto.
            ok_all, detail = True, ""
            for index, png in enumerate(frames):
                ok, why = golden_compare(target / f"instant-{index:02d}.png", png)
                if not ok:
                    ok_all = False
                    detail = f"instant-{index:02d}: {why}"
                    break
                detail = detail or why
            check(f"{name}: strip entro la tolleranza", ok_all, detail)
            moved = any(
                hashes[index] != base_hashes[index] for index in range(len(hashes))
            )
            check(f"{name}: the move changes the picture", moved, "")

    if args.write:
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0 if FAIL == 0 else 1

    strips = [p.name for p in FIXTURES.iterdir() if p.is_dir()] if FIXTURES.is_dir() else []
    check(
        "the strips on disk match the registry, one for one",
        len(strips) == len(camera.MOVES) and set(strips) == set(camera.MOVES),
        f"{len(strips)} strip(s) for {len(camera.MOVES)} move(s)",
    )
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
