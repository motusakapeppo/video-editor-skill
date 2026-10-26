"""vx.registry — the single vocabulary every visual axis must speak.

Why: the same effect used to be named in three places (board, motion, render) and
each place could drift. A brief could declare ``fade`` while the renderer only knew
``pop``, and nothing failed until the pixels were wrong. This module is the one
place where names, engines and thresholds live; everything else derives from it.

The thresholds are not invented here. They are loaded from
``tests/fixtures/corpus_stats.json`` when that file exists, and every number carries
the hash of the corpus that produced it. When the corpus has not been measured the
entry is ``None`` with provenance ``"unmeasured"`` — a missing number is honest, a
made-up one is not.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA = "vx.visual/1.0"

# The approved delivery is never re-rendered: only its hash is compared. The hash
# is machine-local, so it is read from the environment and never hardcoded here.
# An empty value means "not configured": the delivery gate then reports
# ``skipped`` instead of pretending the reference was verified.
APPROVED_DELIVERY_SHA256_ENV = "VX_APPROVED_SHA256"
APPROVED_DELIVERY_SHA256 = os.environ.get(APPROVED_DELIVERY_SHA256_ENV, "").strip()


def approved_sha() -> Optional[str]:
    """The expected SHA-256 of the approved delivery, or ``None`` when unset."""
    return APPROVED_DELIVERY_SHA256 or None


# Families are the surface dimensions a brief may diverge on.
FAMILIES = ("colour", "rhythm", "geometry", "typography")

# Exactly four names. The five ghost effects (blur_in, glitch, mask_wipe,
# blend_difference, elastic_3d) are gone: they were declared but never executed.
# ``template`` uses ``{d}`` for the animation duration in seconds, ``{fps}``
# for the frame rate and ``{w}``/``{h}`` for the output size; the caller
# substitutes them before handing the filter to ffmpeg. All four are
# single-stream ffmpeg filters.
ANIMATIONS: Dict[str, Dict[str, Any]] = {
    "none": {
        "engine": "ffmpeg",
        "template": "null",
        "max_per_video": 0,
    },
    "fade": {
        "engine": "ffmpeg",
        "template": "fade=t=in:st=0:d={d}",
        "max_per_video": 2,
    },
    "wipe": {
        "engine": "ffmpeg",
        "template": (
            "geq=lum='if(lt(X,W*T/{d}),lum(X,Y),0)':"
            "cb='if(lt(X,W*T/{d}),cb(X,Y),128)':"
            "cr='if(lt(X,W*T/{d}),cr(X,Y),128)'"
        ),
        "max_per_video": 2,
    },
    "punch": {
        "engine": "ffmpeg",
        "template": (
            "zoompan=z='min(1.0+0.05*on/({d}*{fps}),1.05)':d=1:"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"
        ),
        "max_per_video": 2,
    },
}

# Derived, never hand-written: a capability that is not in ANIMATIONS cannot exist.
ANIMATION_CAPABILITIES: Dict[str, str] = {
    name: spec["engine"] for name, spec in ANIMATIONS.items()
}

# Never executable, but still refuseable: the docs gate scans prose for these
# names so a retired effect cannot come back as a promise.
RETIRED_EFFECTS = (
    "blur_in",
    "glitch",
    "mask_wipe",
    "blend_difference",
    "elastic_3d",
)

AXES = (
    "palette",
    "grade",
    "animation",
    "cut_density",
    "composition",
    "typography",
)

# Which corpus driver feeds each axis. ``None`` means the axis is not derived from
# the corpus (composition is declared, typography is measured on the delivery).
_DRIVER_KEYS: Dict[str, Optional[str]] = {
    "palette": "hue_mass_top3_mass",
    "grade": "luma_p50",
    "animation": "transient_ratio",
    "cut_density": "onsets_per_s",
    "composition": None,
    "typography": None,
    "hook": "first3s_motion",
}

_FIXTURE = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "corpus_stats.json"
)


def _load_thresholds() -> Dict[str, Dict[str, Any]]:
    """Read the measured corpus, or return honest ``None`` entries."""
    data: Dict[str, Any] = {}
    if _FIXTURE.is_file():
        try:
            data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a broken fixture must not break import
            data = {}

    corpus_hash = str(data.get("corpus_hash") or "")
    drivers = data.get("drivers") or {}
    out: Dict[str, Dict[str, Any]] = {}
    for axis, key in _DRIVER_KEYS.items():
        entry: Dict[str, Any] = {
            "p33": None,
            "p67": None,
            "provenance": "unmeasured",
        }
        if key and corpus_hash and isinstance(drivers.get(key), dict):
            driver = drivers[key]
            p33, p67 = driver.get("p33"), driver.get("p67")
            if p33 is not None and p67 is not None:
                entry = {
                    "p33": float(p33),
                    "p67": float(p67),
                    "provenance": f"corpus_hash:{corpus_hash}",
                }
        out[axis] = entry
    return out


THRESHOLDS: Dict[str, Dict[str, Any]] = _load_thresholds()


def reload_thresholds() -> Dict[str, Dict[str, Any]]:
    """Re-read the fixture in place (used after a corpus measurement run)."""
    global THRESHOLDS
    THRESHOLDS = _load_thresholds()
    return THRESHOLDS


def _selftest() -> int:
    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:38} {detail}")

    print("capacità derivate dal vocabolario:")
    for name in sorted(ANIMATION_CAPABILITIES):
        print(f"  {name}: {ANIMATION_CAPABILITIES[name]}")

    check("schema", SCHEMA == "vx.visual/1.0", SCHEMA)
    sha = approved_sha()
    if sha is None:
        print(
            "  [SKIP] hash del delivery non configurato "
            "(VX_APPROVED_SHA256 unset) — declared"
        )
    else:
        check(
            "hash del delivery",
            len(sha) == 64 and all(char in "0123456789abcdefABCDEF" for char in sha),
            sha[:12] + "...",
        )
    check(
        "capacità == vocabolario",
        set(ANIMATION_CAPABILITIES) == set(ANIMATIONS),
        f"{sorted(ANIMATION_CAPABILITIES)}",
    )
    check(
        "quattro nomi esatti",
        set(ANIMATIONS) == {"none", "fade", "wipe", "punch"},
        f"{sorted(ANIMATIONS)}",
    )
    ghosts = ("blur_in", "glitch", "mask_wipe", "blend_difference", "elastic_3d")
    check(
        "nessun effetto fantasma",
        all(g not in ANIMATIONS for g in ghosts),
        ", ".join(ghosts),
    )
    check(
        "famiglie",
        FAMILIES == ("colour", "rhythm", "geometry", "typography"),
        f"{FAMILIES}",
    )
    check(
        "assi",
        AXES
        == (
            "palette",
            "grade",
            "animation",
            "cut_density",
            "composition",
            "typography",
        ),
        f"{AXES}",
    )
    check(
        "soglie: sei assi + hook",
        set(THRESHOLDS) == set(AXES) | {"hook"},
        f"{sorted(THRESHOLDS)}",
    )
    for axis, entry in THRESHOLDS.items():
        prov = str(entry.get("provenance"))
        ok = prov == "unmeasured" or prov.startswith("corpus_hash:")
        check(f"soglia {axis} con provenienza", ok, prov)

    print(f"{'OK' if failures == 0 else 'FALLITO'} — {failures} fallimenti")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="vx.registry selftest")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    parser.print_help()
    raise SystemExit(2)
