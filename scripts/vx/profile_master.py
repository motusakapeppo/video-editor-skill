"""vx.profile_master — normalise a rendered master to its declared genre profile.

F26-T02: the loudness target is a property of the *profile*, not a constant in
the engine (D033). This module turns a ``vx.profiles.Profile`` (or its id) into
the ``(LUFS, dBTP)`` pair the delivery asks for, and normalises an
already-rendered file to that pair with the same **two-pass loudnorm** the
renderer uses:

1. measure the source with ``loudnorm=print_format=json`` (``qc.lufs_of``);
2. apply a second pass with those measured values
   (``render.loudnorm_two_pass``) followed by the house true-peak limiter
   (``render.edl_loudness_graph``).

Every number in the returned report is a measurement of real audio: the input
loudness/peak come from pass 1, the output loudness/peak from a **fresh**
measurement of the produced file, and the applied gain is the difference of the
two measured loudnesses. Nothing is copied from the request, and nothing is
clamped to make a target look met.

A profile declares a 0.5 LU tolerance, and dynamic material can stop 0.9 LU short
of it (D-13). So the master keeps going: up to :data:`MAX_TRIM_PASSES` linear
trims of at most :data:`TRIM_LIMIT_DB`, each followed by a fresh measurement,
until the file is within :data:`CLOSE_ENOUGH_LU`. The report says how many trims
ran and whether the result is inside the declared tolerance; a miss beyond the
trim limit stays a declared miss.

Refusal codes
-------------
``LOUDNESS_NO_AUDIO``
    The input has no audio stream, so there is nothing to normalise.
``LOUDNESS_UNMEASURED``
    ``loudnorm`` ran but its JSON could not be parsed (or reported a
    non-finite loudness), so no target can be verified.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Dict, Tuple, Union

from . import ffmpeg as ff
from . import profiles, qc, render
from .audio import _audio_info
from .contract import VxError
from .edl3 import EDLError

__all__ = [
    "LOUDNESS_NO_AUDIO",
    "LOUDNESS_UNMEASURED",
    "target_for",
    "master",
]

LOUDNESS_NO_AUDIO = "LOUDNESS_NO_AUDIO"
LOUDNESS_UNMEASURED = "LOUDNESS_UNMEASURED"

#: A profile declares 0.5 LU. Loudnorm in dynamic mode can stop short on material
#: whose loudness moves (a music bed under a quiet voice): the 28/10 reel landed
#: 0.9 LU under the target. A trimmed gain is a linear shift, so it moves the
#: integrated value without touching the dynamics; beyond this limit the file is
#: a different master, not a trim, and the miss is declared instead of papered over.
CLOSE_ENOUGH_LU = 0.2
TRIM_LIMIT_DB = 2.0
MAX_TRIM_PASSES = 2
TOLERANCE_LU = 0.5

ProfileLike = Union[str, profiles.Profile]


def target_for(profile: ProfileLike) -> Tuple[float, float]:
    """The ``(loudness_lufs, true_peak_db)`` pair a profile declares.

    Accepts a :class:`vx.profiles.Profile` or a profile id; a string goes
    through :func:`vx.profiles.resolve`, so an unknown id is refused with
    ``PROFILE_UNKNOWN`` and every declared id in the message.
    """
    row = profile if isinstance(profile, profiles.Profile) else profiles.resolve(str(profile))
    return float(row.loudness_lufs), float(row.true_peak_db)


def _measure(path: Path, *, what: str) -> Dict[str, float]:
    """One loudnorm measurement pass, refused when no finite value comes back.

    ``qc.lufs_of`` is the house meter (the same one ``audit_master`` trusts):
    it returns the raw loudnorm block as floats and ``{}`` when the pass failed
    or produced no JSON. Silence reports ``-inf``, which is not a usable target
    reference, so it is refused like an unparsed block.
    """
    data = qc.lufs_of(path)
    try:
        lufs = float(data["input_i"])
        true_peak = float(data["input_tp"])
        lra = float(data.get("input_lra", 0.0))
        threshold = float(data.get("input_thresh", 0.0))
        offset = float(data.get("target_offset", 0.0))
    except (KeyError, TypeError, ValueError):
        raise EDLError(
            LOUDNESS_UNMEASURED,
            f"loudnorm produced no readable measurement for the {what} {path.name}",
            "profile_master",
        ) from None
    if not (math.isfinite(lufs) and math.isfinite(true_peak)):
        raise EDLError(
            LOUDNESS_UNMEASURED,
            f"loudnorm measured a non-finite loudness for the {what} {path.name}",
            "profile_master",
        )
    return {
        "lufs": lufs,
        "true_peak_db": true_peak,
        "lra": lra if math.isfinite(lra) else 0.0,
        "threshold": threshold if math.isfinite(threshold) else 0.0,
        "offset": offset if math.isfinite(offset) else 0.0,
    }


def master(source: Path, profile: ProfileLike, out: Path, workdir: Path) -> Dict[str, Any]:
    """Normalise ``source`` to ``profile``'s target and measure the result.

    The video is stream-copied; only the first audio stream is re-encoded. The
    returned dict carries ``input_lufs``/``input_true_peak_db`` (pass 1),
    ``applied_gain_db`` (measured output minus measured input) and
    ``output_lufs``/``output_true_peak_db`` (a fresh measurement of ``out``).
    """
    src = Path(source)
    target = Path(out)
    row = profile if isinstance(profile, profiles.Profile) else profiles.resolve(str(profile))
    want_lufs, want_tp = target_for(row)

    if not _audio_info(src).get("has_audio"):
        raise EDLError(
            LOUDNESS_NO_AUDIO,
            f"{src.name} has no audio stream to normalise",
            "profile_master",
        )

    # Pass 1: measure, do not touch the file.
    before = _measure(src, what="input")

    target.parent.mkdir(parents=True, exist_ok=True)
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)

    # Pass 2: the renderer's own builder, fed with pass 1's measured values,
    # then the house limiter so the encoded true peak stays under the declared
    # ceiling.
    stats = {
        "input_i": before["lufs"],
        "input_tp": before["true_peak_db"],
        "input_lra": before["lra"],
        "input_thresh": before["threshold"],
        "target_offset": before["offset"],
    }
    af_args = render.loudnorm_two_pass(stats, target_i=want_lufs, target_tp=want_tp)
    graph = render.edl_loudness_graph(af_args[1], ceiling_dbtp=want_tp)
    (work / "profile_loudness.txt").write_text(graph, encoding="utf-8")

    proc = ff.run_ff(
        [
            "-i",
            str(src),
            "-filter_complex",
            graph,
            "-map",
            "[aout]",
            "-map",
            "0:v:0?",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(target),
        ]
    )
    if not proc.ok or not target.is_file() or target.stat().st_size == 0:
        raise VxError(f"profile loudness pass failed: {proc.tail(8)}")

    # Fresh measurement of the produced file: the output numbers are never the
    # requested numbers.
    after = _measure(target, what="output")

    # Pass 3..n: a linear trim on the produced file, re-measured. Loudnorm's
    # dynamic mode can stop 0.9 LU short on material whose loudness moves; the
    # trim moves the integrated value without re-interpreting the dynamics, and
    # the house limiter keeps the declared true peak. A miss larger than
    # TRIM_LIMIT_DB is not a trim any more: it stays declared, not hidden.
    trims = []
    final = target
    for attempt in range(1, MAX_TRIM_PASSES + 1):
        delta = want_lufs - after["lufs"]
        if abs(delta) <= CLOSE_ENOUGH_LU or abs(delta) > TRIM_LIMIT_DB:
            break
        trimmed = work / f"{target.stem}.trim{attempt}{target.suffix}"
        graph = render.edl_loudness_graph(
            f"volume={delta:.3f}dB", ceiling_dbtp=want_tp
        )
        proc = ff.run_ff(
            [
                "-i",
                str(final),
                "-filter_complex",
                graph,
                "-map",
                "[aout]",
                "-map",
                "0:v:0?",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(trimmed),
            ]
        )
        if not proc.ok or not trimmed.is_file() or trimmed.stat().st_size == 0:
            raise VxError(f"profile loudness trim failed: {proc.tail(8)}")
        final = trimmed
        after = _measure(final, what="output")
        trims.append(
            {
                "pass": attempt,
                "requested_db": round(delta, 3),
                "output_lufs": round(after["lufs"], 3),
            }
        )

    if final != target:
        os.replace(final, target)
        after = _measure(target, what="output")

    return {
        "profile": row.id,
        "target": {"lufs": want_lufs, "true_peak_db": want_tp},
        "input_lufs": round(before["lufs"], 3),
        "input_true_peak_db": round(before["true_peak_db"], 3),
        "applied_gain_db": round(after["lufs"] - before["lufs"], 3),
        "output_lufs": round(after["lufs"], 3),
        "output_true_peak_db": round(after["true_peak_db"], 3),
        "tolerance_lu": TOLERANCE_LU,
        "within_tolerance": abs(after["lufs"] - want_lufs) <= TOLERANCE_LU + 1e-9,
        "trim_passes": trims,
        "master": str(target),
    }
