"""F3-T03 gate: the template library is 54 real templates, not 54 colour variants.

This file is named `_spec_` on purpose: `run_all.py` globs only `test_*.py`, so
the suite stays green while the authors work. Renaming it to
`test_motion_library.py` IS the T03 gate.

What each check proves, and why it can fail:

* the catalogue is exactly the promised 54 names -- 4 layouts for each of 11
  families plus 10 subtitle styles. A missing or extra directory fails.
* every directory declares at least three params and at least one closed domain
  (``values``, ``min``/``max`` or ``max_length``), so ``validate_call`` can
  refuse a bad value by naming the parameter.
* every template really renders: one capture per template, the middle frame is
  RGBA at the declared size, and it has both visible and transparent pixels.
  A background is the one family that must be fully opaque.
* distinctness: the 54 middle frames produce 54 different hashes, and 4
  different hashes inside each family. Anything less is a colour variant.

python tests/_spec_motion_library.py
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from PIL import Image  # noqa: E402

from vx import motion, motion_registry  # noqa: E402

CEILING_SECONDS = 900.0
SAMPLES_PER_SECOND = 2.0

#: family -> the four layouts it owes. The family is the first segment after
#: `vx-`, which is exactly what `motion_registry._derive_category` reports.
CATALOGUE = {
    "title": [
        "vx-title-lower-third",
        "vx-title-full-frame",
        "vx-title-split",
        "vx-title-kicker",
    ],
    "kinetic": [
        "vx-kinetic-word-pop",
        "vx-kinetic-slide-line",
        "vx-kinetic-mask-reveal",
        "vx-kinetic-stack",
    ],
    "callout": [
        "vx-callout-pointer",
        "vx-callout-bracket",
        "vx-callout-badge",
        "vx-callout-arrow",
    ],
    "progress": [
        "vx-progress-bar",
        "vx-progress-ring",
        "vx-progress-steps",
        "vx-progress-arc",
    ],
    "counter": [
        "vx-counter-odometer",
        "vx-counter-ticker",
        "vx-counter-countdown",
        "vx-counter-percent",
    ],
    "chart": [
        "vx-chart-bars",
        "vx-chart-line",
        "vx-chart-donut",
        "vx-chart-stack",
    ],
    "background": [
        "vx-background-gradient",
        "vx-background-grid",
        "vx-background-waves",
        "vx-background-bokeh",
    ],
    "sticker": [
        "vx-sticker-burst",
        "vx-sticker-doodle",
        "vx-sticker-frame",
        "vx-sticker-arrow-loop",
    ],
    "card": [
        "vx-card-quote",
        "vx-card-stat",
        "vx-card-profile",
        "vx-card-cta",
    ],
    "intro": [
        "vx-intro-logo-lockup",
        "vx-intro-countdown",
        "vx-intro-curtain",
        "vx-intro-glitch",
    ],
    "transition": [
        "vx-transition-wipe",
        "vx-transition-iris",
        "vx-transition-slide",
        "vx-transition-luma",
    ],
    "subtitle": [
        "vx-subtitle-pop",
        "vx-subtitle-karaoke",
        "vx-subtitle-box",
        "vx-subtitle-underline",
        "vx-subtitle-highlight",
        "vx-subtitle-typewriter",
        "vx-subtitle-bounce",
        "vx-subtitle-minimal",
        "vx-subtitle-bold",
        "vx-subtitle-split",
    ],
}

EXPECTED = sorted(name for names in CATALOGUE.values() for name in names)

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    mark = "PASS" if ok else "FAIL"
    print(f"    [{mark}] {label:<62} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:<62} {reason}")


def opaque_fraction(path: Path) -> float:
    """Share of pixels with a non-zero alpha channel, from the alpha histogram."""
    with Image.open(path) as image:
        alpha = image.convert("RGBA").getchannel("A")
        histogram = alpha.histogram()
    total = sum(histogram)
    if total == 0:
        return 0.0
    return 1.0 - (histogram[0] / total)


def pick_domain(params: dict) -> tuple[str, dict]:
    """The first declaration that can refuse a value, with the declaration."""
    for name, declaration in sorted(params.items()):
        if declaration.get("values"):
            return name, declaration
        kind = declaration.get("type")
        if kind in ("number", "int") and ("min" in declaration or "max" in declaration):
            return name, declaration
        if declaration.get("max_length"):
            return name, declaration
    return "", {}


def bad_value(declaration: dict) -> object:
    if declaration.get("values"):
        return "__not_a_member__"
    kind = declaration.get("type")
    if kind == "int":
        return int(declaration.get("max", declaration.get("min", 0))) + 1000
    if kind == "number":
        return float(declaration.get("max", declaration.get("min", 0))) + 1000.0
    return "x" * (int(declaration["max_length"]) + 10)


def run(work: Path) -> None:
    registry = motion_registry.Registry()
    names = registry.names()
    blocked = registry.blocked()

    check(
        "the catalogue is exactly the promised names",
        names == EXPECTED,
        f"{len(names)} admitted, {len(EXPECTED)} expected",
    )
    check(
        "nothing is blocked by the linter",
        blocked == {},
        str({key: item["code"] for key, item in blocked.items()}),
    )
    if names != EXPECTED:
        missing = sorted(set(EXPECTED) - set(names))
        extra = sorted(set(names) - set(EXPECTED))
        check("missing names", not missing, str(missing[:8]))
        check("unexpected names", not extra, str(extra[:8]))

    for family, owed in CATALOGUE.items():
        admitted = [name for name in names if name.startswith(f"vx-{family}-")]
        check(
            f"family {family!r} has {len(owed)} layouts",
            len(admitted) == len(owed),
            f"{len(admitted)} admitted",
        )

    hashes: dict[str, str] = {}
    for name in names:
        entry = registry.get(name)
        if entry is None:
            continue
        directory = Path(entry.directory)
        check(f"{name}: declared name is the directory", entry.name == directory.name)

        check(
            f"{name}: at least three declared params",
            len(entry.params) >= 3,
            f"{len(entry.params)} params",
        )
        domain_name, declaration = pick_domain(entry.params)
        check(
            f"{name}: at least one closed domain",
            bool(domain_name),
            f"{domain_name} {declaration.get('type', '')}",
        )
        if domain_name:
            try:
                registry.validate_call(name, {domain_name: bad_value(declaration)})
                refused = False
                detail = "accepted a value outside its domain"
            except motion.MotionError as exc:
                refused = exc.code == "PARAM_RANGE" and domain_name in str(exc)
                detail = f"{exc.code}: {exc}"
            check(f"{name}: out-of-domain value refused", refused, detail)

        out_dir = work / "frames" / name
        try:
            result = motion.capture(
                directory,
                {},
                out_dir,
                fps=SAMPLES_PER_SECOND,
                workdir=work / "cache",
            )
        except motion.MotionError as exc:
            if exc.code == "MOTION_UNAVAILABLE":
                skip(f"{name}: render", str(exc))
                continue
            check(f"{name}: render", False, f"{exc.code}: {exc}")
            continue

        frames = [Path(item) for item in result["frames"]]
        check(
            f"{name}: at least three sampled frames",
            result["frame_count"] >= 3,
            f"{result['frame_count']} frames at {result['fps']} fps",
        )
        if len(frames) < 2:
            continue
        mid = frames[len(frames) // 2]
        with Image.open(mid) as image:
            size = image.size
            mode = image.mode
        check(
            f"{name}: middle frame is RGBA at the declared size",
            mode == "RGBA" and list(size) == list(entry.size),
            f"{mode} {size} wanted RGBA {entry.size}",
        )
        visible = opaque_fraction(mid)
        if name.startswith("vx-background-"):
            check(
                f"{name}: a background is fully opaque",
                visible == 1.0,
                f"opaque {visible:.4f}",
            )
        else:
            check(
                f"{name}: both visible and transparent pixels",
                0.0005 < visible < 0.9995,
                f"opaque {visible:.4f}",
            )
        hashes[name] = hashlib.sha256(mid.read_bytes()).hexdigest()

    if not hashes:
        # Nessun render possibile (di solito: playwright/Chromium assente).
        # Le catture mancanti sono gia' state dichiarate una per una; qui non
        # si accusa il codice di non aver prodotto frame che non poteva fare.
        print("  [SKIP] nessuna cattura: motore grafico non disponibile")
        return
    check(
        "54 distinct middle frames",
        len(set(hashes.values())) == len(hashes) and len(hashes) == len(EXPECTED),
        f"{len(set(hashes.values()))} distinct over {len(hashes)} captures",
    )
    for family, owed in CATALOGUE.items():
        family_hashes = {
            name: digest
            for name, digest in hashes.items()
            if name.startswith(f"vx-{family}-")
        }
        check(
            f"family {family!r} has {len(owed)} distinct layouts",
            len(set(family_hashes.values())) == len(family_hashes)
            and len(family_hashes) == len(owed),
            f"{len(set(family_hashes.values()))} distinct over {len(family_hashes)}",
        )


def main() -> int:
    global FAIL
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(
            prefix="vx_motion_library_", ignore_cleanup_errors=True
        ) as tmp:
            run(Path(tmp))
    except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
        FAIL += 1
        print(f"\nFAIL: {type(exc).__name__}: {exc}")
    elapsed = time.monotonic() - started
    print(
        f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP"
        f"  ({elapsed:.1f}s, ceiling {CEILING_SECONDS:.0f}s)"
    )
    if elapsed > CEILING_SECONDS:
        print(f"FAIL: the gate took {elapsed:.1f}s, above its ceiling")
        return 1
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
