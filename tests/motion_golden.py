"""F3 -- golden instants for the Motion Studio template library.

One table, one writer: this module is the ONLY producer of
``tests/fixtures/motion_golden``. It captures every template the registry
admits, at the same virtual rate, with the frame cache explicitly off, and
keeps three instants per template:

    tests/fixtures/motion_golden/<template>/instant-00.png
    tests/fixtures/motion_golden/<template>/instant-01.png
    tests/fixtures/motion_golden/<template>/instant-02.png
    tests/fixtures/motion_golden/<template>/manifest.json
    tests/fixtures/motion_golden/index.json

The three instants are the first frame, the middle frame and the last frame of
a 3 fps capture, so a drift in the entrance, the hold or the exit is visible.

Why the cache is off: ``workdir=None`` makes ``motion.capture`` render every
frame from scratch. A cached frame would compare equal by construction and hide
exactly the regression this fixture exists to catch.

Why ``engine`` is ``chromium``: ``capture()['engine']`` reports the launch
channel (``chrome`` or ``msedge``), which is a local installation detail. The
rasteriser family is Chromium either way, and ``engine_version`` is what pins
the actual build, so the manifest stores the family and the version separately.

No absolute path is written: ``file`` is a bare POSIX name and the manifest
carries no directory at all. ``generated_at`` is a date, never a wall clock, so
two runs on the same day are byte-identical.

    python tests/motion_golden.py --write
    python tests/motion_golden.py --write --only subtitle
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import motion  # noqa: E402
from vx.motion_registry import Registry  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "motion_golden"
SCHEMA = "vx.motion.golden/1"
ENGINE = "chromium"
FPS = 3.0
INSTANT_COUNT = 3

__all__ = [
    "FIXTURES",
    "SCHEMA",
    "ENGINE",
    "FPS",
    "INSTANT_COUNT",
    "write",
    "main",
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _write_template(
    name: str, directory: str, destination: Path, work: Path
) -> Tuple[Dict[str, Any], int]:
    """Capture one template and write its three instants plus manifest."""
    output = work / name
    result = motion.capture(directory, {}, output, fps=FPS, workdir=None)
    count = int(result["frame_count"])
    if count < INSTANT_COUNT:
        raise RuntimeError(
            f"{name}: capture produced {count} frame(s), need at least {INSTANT_COUNT}"
        )
    indices = [0, count // 2, count - 1]
    rate = float(result["fps"])
    destination.mkdir(parents=True, exist_ok=True)
    for stale in destination.glob("instant-*.png"):
        stale.unlink()
    instants: List[Dict[str, Any]] = []
    total_bytes = 0
    for slot, index in enumerate(indices):
        target = destination / f"instant-{slot:02d}.png"
        shutil.copyfile(str(result["frames"][index]), str(target))
        data = target.read_bytes()
        total_bytes += len(data)
        instants.append(
            {
                "index": index,
                "t": round(index / rate, 6),
                "file": target.name,
                "sha256": _sha256(data),
                "bytes": len(data),
            }
        )
    manifest = {
        "template": name,
        "engine": ENGINE,
        "engine_version": str(result.get("engine_version", "") or ""),
        "fps": rate,
        "frame_count": count,
        "size": [int(result["size"][0]), int(result["size"][1])],
        "instants": instants,
    }
    _write_json(destination / "manifest.json", manifest)
    return manifest, total_bytes


def _rebuild_index(fixtures: Path) -> Dict[str, Any]:
    """Derive the index from the manifests on disk, so `--only` stays consistent."""
    manifests: List[Dict[str, Any]] = []
    for child in sorted(fixtures.iterdir()):
        manifest_path = child / "manifest.json"
        if child.is_dir() and manifest_path.is_file():
            manifests.append(json.loads(manifest_path.read_text(encoding="utf-8")))
    if not manifests:
        raise RuntimeError("no per-template manifest to index")
    versions = sorted({str(item.get("engine_version", "") or "") for item in manifests})
    if len(versions) != 1:
        raise RuntimeError(
            "the fixtures were captured by different browser versions: "
            + ", ".join(repr(item) for item in versions)
        )
    return {
        "schema": SCHEMA,
        "templates": len(manifests),
        "frames": sum(len(item.get("instants") or []) for item in manifests),
        "engine": ENGINE,
        "engine_version": versions[0],
        "generated_at": date.today().isoformat(),
    }


def write(fixtures: str | Path = FIXTURES, only: str = "") -> List[Tuple[str, int]]:
    """Capture every admitted template, write the fixtures and the index."""
    root = Path(fixtures)
    registry = Registry()
    known = registry.names()
    selected = [name for name in known if not only or only in name]
    if not selected:
        raise RuntimeError(f"no template matches {only!r}; known: {known}")

    root.mkdir(parents=True, exist_ok=True)
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name not in known:
            shutil.rmtree(child)

    written: List[Tuple[str, int]] = []
    total_bytes = 0
    with tempfile.TemporaryDirectory(prefix="vx_motion_golden_") as tmp:
        work = Path(tmp)
        for name in selected:
            entry = registry.get(name)
            if entry is None:
                raise RuntimeError(f"template {name!r} vanished from the registry")
            manifest, written_bytes = _write_template(
                name, entry.directory, root / name, work
            )
            total_bytes += written_bytes
            frames = len(manifest["instants"])
            written.append((name, frames))
            print(
                f"wrote {(root / name).relative_to(ROOT).as_posix()}: {frames} frame(s)"
            )

    _write_json(root / "index.json", _rebuild_index(root))
    print(f"wrote {(root / 'index.json').relative_to(ROOT).as_posix()}")

    frames = sum(count for _name, count in written)
    print(
        f"\n{len(written)} template / {frames} frame / {total_bytes / (1024 * 1024):.1f} MB"
    )
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="capture and rewrite the golden artefacts (without it nothing is written)",
    )
    parser.add_argument(
        "--only",
        default="",
        metavar="SUBSTRING",
        help="restrict --write to templates whose name contains SUBSTRING",
    )
    args = parser.parse_args(argv)
    if not args.write:
        print(
            "refusing to write: pass --write to rewrite tests/fixtures/motion_golden",
            file=sys.stderr,
        )
        return 2
    try:
        write(only=args.only)
    except motion.MotionError as exc:
        print(f"FAIL: {exc.code}: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
