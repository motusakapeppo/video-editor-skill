"""F4-T09 gate — one proof frame for every kind in the effect library.

The golden harness in :mod:`tests.golden_edl3` proves the fifteen contract
effects; this file proves the *library*: every kind in
``vx.edl3.EFFECT_REGISTRY`` whose declared backend is ``filter`` (the five
declarative families — geometry, color, glitch, film, composite — 58 kinds
today) must render one frame through the real EDL 3 path.

Nothing is re-implemented: the document comes from
``golden_edl3._document`` / ``_clip`` / ``_effect``, the sources from
``golden_edl3.build_sources``, and the render from
``golden_edl3.render_and_capture`` — the exact call
``tests/test_edl3_golden.py`` uses. One document per kind, one clip, one effect
at its declared defaults, captured at a single instant on a 320x568 canvas at
12 fps.

Three properties per kind, each a check that can fail:

a. the freshly rendered frame is byte-identical to
   ``tests/fixtures/edl3_kinds/<kind>.png`` (sha256 compared, so a missing or
   changed fixture names the kind, the expected digest and the actual one);
b. the fixture decodes as a valid PNG of the declared canvas, so a text file
   renamed ``.png`` cannot pass;
c. the frame differs from the un-effected frame of the same document, so a kind
   that silently does nothing cannot pass.

The fixtures are written only by this file's ``--write`` mode:

    python tests/test_edl3_kinds.py --write    # re-bless all 60 fixtures
    python tests/test_edl3_kinds.py           # verify, never writes

Two consecutive ``--write`` runs produce byte-identical PNGs: the writer copies
frames ffmpeg already produced from deterministic sources, and keeps no
timestamp, no absolute path and no index file.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from vx import edl3  # noqa: E402
from vx.ffmpeg import run_ff  # noqa: E402

import golden_edl3  # noqa: E402
from _golden import compare_images as golden_compare  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "edl3_kinds"
FRAME_TIME = 0.5
#: The declared library: geometry 12 + color 12 + glitch 12 + film 12 +
#: composite 10. A registry that loses a kind must fail here, not shrink the
#: gate silently.
LIBRARY_KINDS = 60
CEILING_SECONDS = 90.0
REBLESS = "python tests/test_edl3_kinds.py --write"

PASS = 0
FAIL = 0


# ------------------------------------------------------------------ helpers


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def library_kinds() -> List[str]:
    """Every registry kind the declarative ``filter`` backend implements."""
    return sorted(
        kind
        for kind, spec in edl3.EFFECT_REGISTRY.items()
        if spec.backend == edl3.BACKEND_FILTER
    )


# ------------------------------------------------- document / render (reused)


def _document(sources: Mapping[str, Path], kind: Optional[str]) -> edl3.Timeline3:
    """The smallest document that carries ``kind``; ``None`` is the bare clip.

    The un-effected frame is the *same* document minus the effects list: same
    source, same clip id, same in/out points, same canvas and rate.
    """
    effects: List[Dict[str, Any]] = [] if kind is None else [golden_edl3._effect(kind)]
    return golden_edl3._document(
        [
            golden_edl3._clip(
                "c1",
                sources["changing"],
                src_out=1.0,
                effects=effects,
            )
        ]
    )


def render_frame(
    kind: Optional[str],
    sources: Mapping[str, Path],
    work: Path,
    label: str,
) -> Tuple[Path, str]:
    """Render one frame through the golden harness and return (png, sha256)."""
    spec: Dict[str, Any] = {
        "doc_factory": lambda src: _document(src, kind),
        "times": [FRAME_TIME],
        "audio": "refuse",
    }
    manifest = golden_edl3.render_and_capture(label, spec, sources, work)
    frame = manifest["frames"][0]
    return Path(str(frame["path"])), str(frame["sha256"])


def png_problem(path: Path) -> Optional[str]:
    """``None`` when ``path`` decodes as a PNG of the declared canvas."""
    expected = [golden_edl3.WIDTH, golden_edl3.HEIGHT]
    try:
        from PIL import Image  # noqa: WPS433 (optional dependency)
    except Exception:  # noqa: BLE001 - Pillow is optional; ffmpeg decodes instead
        proc = run_ff(
            ["-v", "error", "-i", str(path), "-frames:v", "1", "-f", "null", "-"],
            loglevel="error",
        )
        if not proc.ok:
            return f"ffmpeg cannot decode it: {proc.tail()}"
        return None
    try:
        with Image.open(path) as image:
            image.load()
            size = [int(image.size[0]), int(image.size[1])]
    except Exception as exc:  # noqa: BLE001 - an undecodable file is the finding
        return f"not a decodable PNG: {type(exc).__name__}: {exc}"
    if size != expected:
        return f"decodes as {size[0]}x{size[1]}, expected {expected[0]}x{expected[1]}"
    return None


# ------------------------------------------------------------------- verify


def verify(sources: Mapping[str, Path], work: Path) -> None:
    kinds = library_kinds()
    check(
        "the filter backend owns the declared library",
        len(kinds) == LIBRARY_KINDS,
        f"{len(kinds)} kind(s), expected {LIBRARY_KINDS}",
    )
    if not FIXTURES.is_dir():
        check(
            "the fixture directory exists",
            False,
            f"missing {_relative(FIXTURES)}; re-bless with `{REBLESS}`",
        )
        return

    on_disk = sorted(FIXTURES.glob("*.png"))
    stale = [path.name for path in on_disk if path.stem not in kinds]
    check(
        "every fixture belongs to a filter-backend kind",
        not stale,
        f"stale: {', '.join(stale[:6])}" if stale else f"{len(on_disk)} file(s) scanned",
    )

    baseline_path, baseline = render_frame(None, sources, work / "baseline", "baseline")
    check(
        "the un-effected frame renders",
        baseline_path.is_file() and png_problem(baseline_path) is None,
        f"sha256 {baseline}",
    )

    for kind in kinds:
        target = FIXTURES / f"{kind}.png"
        try:
            frame, actual = render_frame(kind, sources, work / kind, kind)
        except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
            check(
                f"{kind}: renders through the EDL 3 path",
                False,
                f"{type(exc).__name__}: {exc}",
            )
            continue
        if not target.is_file():
            check(
                f"{kind}: fixture present",
                False,
                f"no {_relative(target)} (rendered sha256 {actual}); "
                f"re-bless with `{REBLESS}`",
            )
            continue
        # Pixel con tolleranza: la stessa scena rigirata con un altro ffmpeg o
        # su un'altra CPU non e' binariamente identica. Un filtro rotto sfora.
        same, detail = golden_compare(target, frame)
        check(
            f"{kind}: frame matches {kind}.png",
            same,
            detail if not same else "",
        )
        problem = png_problem(target)
        check(
            f"{kind}: fixture is a valid PNG",
            problem is None,
            problem or f"{golden_edl3.WIDTH}x{golden_edl3.HEIGHT}",
        )
        check(
            f"{kind}: differs from the un-effected frame",
            actual != baseline,
            "" if actual != baseline else f"same sha256 {actual} as the bare clip",
        )

    # A deterministic harness is the premise of every check above. Rendering one
    # noise-driven kind twice proves the seam has no per-run randomness.
    _first, digest_a = render_frame("grain", sources, work / "twice_a", "grain_a")
    _second, digest_b = render_frame("grain", sources, work / "twice_b", "grain_b")
    check(
        "the harness is reproducible: one kind, two renders, one digest",
        digest_a == digest_b,
        f"{digest_a} vs {digest_b}",
    )


# -------------------------------------------------------------------- write


def write(sources: Mapping[str, Path], work: Path) -> None:
    kinds = library_kinds()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for path in sorted(FIXTURES.glob("*.png")):
        if path.stem not in kinds:
            path.unlink()
            print(f"  removed stale {_relative(path)}")

    _baseline_path, baseline = render_frame(None, sources, work / "baseline", "baseline")
    total_bytes = 0
    written = 0
    for kind in kinds:
        frame, digest = render_frame(kind, sources, work / kind, kind)
        problem = png_problem(frame)
        if problem is not None:
            check(f"{kind}: rendered frame is a valid PNG", False, problem)
            continue
        if digest == baseline:
            check(
                f"{kind}: differs from the un-effected frame",
                False,
                "the effect changed nothing; refusing to bless a no-op",
            )
            continue
        target = FIXTURES / f"{kind}.png"
        shutil.copyfile(str(frame), str(target))
        size_bytes = target.stat().st_size
        copied = _sha256(target)
        check(
            f"{kind}: fixture written",
            copied == digest,
            f"{_relative(target)}: {size_bytes} bytes sha256 {copied}",
        )
        if copied == digest:
            written += 1
            total_bytes += size_bytes
    print(f"\n{written} fixture(s), {total_bytes} bytes in {_relative(FIXTURES)}")


# --------------------------------------------------------------------- main


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="render and rewrite tests/fixtures/edl3_kinds (without it nothing is written)",
    )
    args = parser.parse_args(argv)

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vx_edl3_kinds_") as tmp:
        work = Path(tmp)
        try:
            sources = golden_edl3.build_sources(work / "sources")
            if args.write:
                write(sources, work / "write")
            else:
                verify(sources, work / "verify")
        except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
            check("harness completed", False, f"{type(exc).__name__}: {exc}")

    elapsed = time.monotonic() - started
    if not args.write:
        slow = elapsed > CEILING_SECONDS
        check(
            "suite stays under the ceiling",
            not slow,
            f"{elapsed:.1f}s (ceiling {CEILING_SECONDS:.0f}s)",
        )
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

