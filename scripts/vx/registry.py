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
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

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
_ANNOTATIONS_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "corpus_annotations.json"
)
_LEDGER_REL = "PLAN/MASTER_LEDGER.md"
_LEDGER = Path(__file__).resolve().parents[2] / "PLAN" / "MASTER_LEDGER.md"

# Human vocabulary from the T09 module. Keys are the de-accented, lower-cased
# Italian labels the annotator writes; values are the canonical tokens every
# consumer speaks. A label outside this table is not a level: the importer
# rejects it instead of guessing.
ANNOTATION_LEVELS: Dict[str, Dict[str, str]] = {
    "shake": {
        "ok": "ok",
        "lieve": "light",
        "evidente": "evident",
        "inguardabile": "unwatchable",
    },
    "exposure": {
        "ok": "ok",
        "scura": "dark",
        "molto scura": "very_dark",
        "chiara": "bright",
        "bruciata": "blown",
    },
    "cast": {
        "nessuna": "none",
        "lieve": "light",
        "evidente": "evident",
    },
    "noise": {
        "assente": "absent",
        "lieve": "light",
        "evidente": "evident",
        "fastidioso": "annoying",
    },
    "volume": {
        "ok": "ok",
        "basso": "low",
        "alto": "high",
        "sbilanciato": "unbalanced",
        "non c'e audio": "none",
    },
    "silences": {
        "nessuno": "none",
        "pochi": "few",
        "molti": "many",
        "non applicabile": "na",
    },
    "music": {
        "no": "no",
        "si (ok)": "yes_ok",
        "si (copre la voce)": "yes_masks",
        "non applicabile": "na",
    },
    "hook": {
        "forte": "strong",
        "medio": "medium",
        "debole": "weak",
    },
}

# Severity rank per canonical token, declared here so the percentile path is
# deterministic and documented instead of inferred. ``None`` means the token
# carries no severity (the axis does not apply) and is excluded from percentiles.
# Ranks are comparable in the severity direction only; they are never a quality
# score. Exposure has two directions (dark and bright) mapped to equal severity.
ANNOTATION_RANKS: Dict[str, Dict[str, Optional[int]]] = {
    "shake": {"ok": 0, "light": 1, "evident": 2, "unwatchable": 3},
    "exposure": {"ok": 0, "dark": 1, "very_dark": 2, "bright": 1, "blown": 2},
    "cast": {"none": 0, "light": 1, "evident": 2},
    "noise": {"absent": 0, "light": 1, "evident": 2, "annoying": 3},
    "volume": {"ok": 0, "low": 1, "high": 2, "unbalanced": 2, "none": 3},
    "silences": {"none": 0, "few": 1, "many": 2, "na": None},
    "music": {"no": 0, "yes_ok": 1, "yes_masks": 2, "na": None},
    "hook": {"strong": 0, "medium": 1, "weak": 2},
}


def _read_t09_status() -> Dict[str, Any]:
    """Read the F0-T09 row of the master ledger, safely.

    The ledger is the programme's memory: a missing or unreadable ledger must
    leave the thresholds ``provisional`` (the conservative reading), never
    silently promote them to measured.
    """
    status = "UNKNOWN"
    row = None
    if _LEDGER.is_file():
        try:
            lines = _LEDGER.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for number, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped.startswith("| F0-T09"):
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) >= 2:
                status = cells[-1]
                row = number
    return {
        "status": status,
        "done": status.upper().startswith("DONE"),
        "row": row,
        "source": _LEDGER_REL,
    }


_T09 = _read_t09_status()

# Status read from the ledger at import time. ``CORPUS_PROVISIONAL`` is the
# single gate every consumer can read: while T09 is not DONE, corpus-derived
# thresholds are provisional by definition (ledger rule D010).
T09_STATUS: str = str(_T09["status"])
T09_DONE: bool = bool(_T09["done"])
CORPUS_PROVISIONAL: bool = not T09_DONE


def _percentile(values: List[float], p: float) -> Optional[float]:
    """Linear-interpolated percentile, identical to numpy's default.

    ``tests/measure_corpus.py`` derives the shipped thresholds with
    ``numpy.percentile(..., method="linear")``. Re-implementing it here keeps the
    registry stdlib-only and lets an annotated corpus be scored without numpy.
    """
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return round(ordered[0], 4)
    position = (len(ordered) - 1) * p / 100.0
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return round(ordered[low], 4)
    fraction = position - low
    blended = ordered[low] * (1.0 - fraction) + ordered[high] * fraction
    return round(blended, 4)


def annotated_percentiles(clips: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """p33/p67 of the documented severity ranks for each annotated axis.

    Input is the ``clips`` object of a T09 annotation file: every clip carries
    ``axes`` (shake, exposure, cast, noise, volume, silences, music) and a
    top-level ``hook``, all in canonical tokens. Tokens with no severity rank
    (``na``) are excluded and counted out via ``n``. The result is deterministic:
    same annotations in, same p33/p67 out, rounded to four decimals like
    ``measure_corpus.py``.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for axis, ranks in ANNOTATION_RANKS.items():
        values: List[float] = []
        for clip in clips.values():
            if not isinstance(clip, dict):
                continue
            if axis == "hook":
                token = clip.get("hook")
            else:
                axes = clip.get("axes")
                token = axes.get(axis) if isinstance(axes, dict) else None
            rank = ranks.get(token) if isinstance(token, str) else None
            if rank is not None:
                values.append(float(rank))
        out[axis] = {
            "p33": _percentile(values, 33.0),
            "p67": _percentile(values, 67.0),
            "n": len(values),
        }
    return out


def _load_annotated() -> Optional[Dict[str, Dict[str, Any]]]:
    """Percentiles of the real annotation corpus, when the human module exists."""
    if not _ANNOTATIONS_FIXTURE.is_file():
        return None
    try:
        data = json.loads(_ANNOTATIONS_FIXTURE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    clips = data.get("clips") if isinstance(data, dict) else None
    if not isinstance(clips, dict) or not clips:
        return None
    return annotated_percentiles(clips)


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


def _threshold_state(has_numbers: bool, annotated: bool) -> str:
    if not has_numbers or CORPUS_PROVISIONAL:
        return "provisional"
    if annotated:
        return "annotated"
    return "measured"


def provenance() -> Dict[str, Any]:
    """Report the provenance of every calibrated threshold.

    States, in increasing strength:

    ``provisional``  no numbers, or T09 is not DONE in the ledger: the corpus
                     thresholds are a guess by the ledger's own rule (D010).
    ``measured``     T09 is DONE and numbers come from ``corpus_stats.json``.
    ``annotated``    T09 is DONE and a real human annotation corpus
                     (``corpus_annotations.json``) also backs the axes.

    The existing ``THRESHOLDS`` API and values are untouched; this is a read-only
    view over them plus the ledger status and the optional annotated percentiles.
    """
    annotated = _load_annotated()
    thresholds: Dict[str, Dict[str, Any]] = {}
    for axis, entry in THRESHOLDS.items():
        p33, p67 = entry.get("p33"), entry.get("p67")
        has_numbers = p33 is not None and p67 is not None
        thresholds[axis] = {
            "p33": p33,
            "p67": p67,
            "state": _threshold_state(has_numbers, annotated is not None),
            "source": entry.get("provenance"),
        }
    return {
        "t09": {
            "status": T09_STATUS,
            "done": T09_DONE,
            "row": _T09.get("row"),
            "source": _LEDGER_REL,
        },
        "corpus_provisional": CORPUS_PROVISIONAL,
        "annotations": annotated,
        "thresholds": thresholds,
    }


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
