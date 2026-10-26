"""vx.prove — the independent prover: it must be able to say no.

Why: a brief that grades itself is not evidence. This module re-reads the ledger
and re-measures the renderer without importing any decision logic from the
registry — only the vocabulary constants. If the brief claims an effect that was
not executed, or a confidence that contradicts its own inaction rule, the prover
fails it.

Wave 0 shipped the ledger and the renderer delta. Wave 2.5 completes the prover
with the two pair gates (``distinct_surface``, ``hook_distinct``), the render
gate (``coverage_gate``) and the documentation gate (``docs_match``). Every gate
is binary: it returns ok or it does not, and a measurement it cannot take is a
failure with the reason, never a fabricated number.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .palette import INK_DARK, INK_LIGHT  # the two neutral inks, constants only
from .registry import (
    ANIMATIONS,
    APPROVED_DELIVERY_SHA256,
    AXES,
    RETIRED_EFFECTS,
    THRESHOLDS,
)

# The approved delivery is machine-local and never re-rendered. The environment
# variable exists so a different workstation can point at its own copy.
APPROVED_DELIVERY_PATH = Path(
    os.environ.get(
        "VX_APPROVED_DELIVERY",
        r"D:\VOX POPULI - ALBUM\SOCIAL\PUBBLICAZIONI\P_25-10-26\video\MOTUS_vox_FINALE_delivery.mp4",
    )
)

# The V2 grade the delivery was approved with. ``node=None`` means this node, not
# "no grade": a baseline of zero would make the 0.5x stop rule meaningless.
APPROVED_GRADE: Dict[str, Any] = {
    "type": "curve",
    "master": [[0.0, 0.0], [0.5, 0.50], [0.85, 0.88], [1.0, 1.0]],
    "channels": {
        "r": [[0, 0.0400], [0.25, 0.4401], [0.5, 0.8989], [0.75, 0.9419], [1, 1.0]],
        "g": [[0, 0.0120], [0.25, 0.1761], [0.5, 0.3416], [0.75, 0.7064], [1, 0.99]],
        "b": [[0, 0.0120], [0.25, 0.1320], [0.5, 0.2697], [0.75, 0.4710], [1, 0.9694]],
    },
    "desaturate": True,
    "grain": 4,
    "vignette": math.pi / 9.0,
}

_PROVENANCE_PREFIXES = ("corpus_hash:", "measured:", "declared:")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_ledger(brief: Dict[str, Any]) -> List[str]:
    """Re-read the ledger. Every returned string is a reason to reject the brief."""
    errors: List[str] = []
    if not isinstance(brief, dict):
        return ["brief non è un oggetto"]

    choices = brief.get("choices")
    if not isinstance(choices, dict):
        return ["choices mancante o non è un oggetto"]

    executed = brief.get("executed")
    if not isinstance(executed, list):
        errors.append("executed mancante o non è una lista")
        executed = []
    by_axis: Dict[str, Dict[str, Any]] = {}
    for item in executed:
        if isinstance(item, dict) and item.get("axis") in AXES:
            by_axis[item["axis"]] = item

    for axis in AXES:
        choice = choices.get(axis)
        if not isinstance(choice, dict):
            errors.append(f"{axis}: scelta mancante")
            continue

        for field in (
            "driver",
            "value",
            "provenance",
            "inaction",
            "confidence",
            "applied",
        ):
            if field not in choice:
                errors.append(f"{axis}: campo {field} mancante")
        if not isinstance(choice.get("inaction"), bool):
            errors.append(f"{axis}: inaction non booleano")

        provenance = choice.get("provenance")
        if not isinstance(provenance, str) or not provenance.startswith(
            _PROVENANCE_PREFIXES
        ):
            errors.append(f"{axis}: provenienza non ammessa: {provenance!r}")

        confidence = choice.get("confidence")
        applied = choice.get("applied")
        if not _is_number(confidence):
            errors.append(f"{axis}: confidence non numerica")
        elif not isinstance(applied, bool):
            errors.append(f"{axis}: applied non booleano")
        else:
            uncertain = 0.2 <= float(confidence) <= 0.8
            if uncertain and applied:
                errors.append(
                    f"{axis}: confidence {confidence} in [0.2, 0.8] ma applied true"
                )
            if not uncertain and not applied:
                errors.append(
                    f"{axis}: confidence {confidence} fuori [0.2, 0.8] ma applied false"
                )

        if axis == "animation" and choice.get("direction_basis") != "asserted":
            errors.append("animation: direction_basis deve essere 'asserted'")

        item = by_axis.get(axis)
        if item is None:
            errors.append(f"{axis}: executed mancante")
            continue
        if item.get("rc") != 0:
            errors.append(f"{axis}: executed.rc != 0")
        frames = item.get("frames")
        if not isinstance(frames, int) or isinstance(frames, bool) or frames <= 0:
            errors.append(f"{axis}: executed.frames <= 0")
        filter_text = item.get("filter")
        if not isinstance(filter_text, str) or not filter_text.strip():
            errors.append(f"{axis}: executed.filter vuoto")
        if applied is False and item.get("name") != "none":
            errors.append(f"{axis}: applied false ma executed.name != 'none'")
        if applied is True:
            # Applied means executed: a decision that claims to be applied must
            # point at a filter that actually ran. ``none``/``null`` is the
            # ledger's way of saying "nothing executed", so it cannot back an
            # applied choice.
            if (
                item.get("name") == "none"
                or not isinstance(filter_text, str)
                or filter_text.strip() in ("", "null")
            ):
                errors.append(f"{axis}: applied true ma nessun filtro eseguito")

    if brief.get("skeleton") != "deferred":
        errors.append("skeleton deve essere 'deferred'")

    reasons = brief.get("reasons")
    if not isinstance(reasons, list) or len(reasons) != 6:
        errors.append("reasons deve avere 6 voci")

    if len(executed) != 6:
        errors.append("executed deve avere 6 voci")

    return errors


def reproduce_sha256(path: str | Path) -> bool:
    """True only when the file's SHA-256 equals the approved delivery hash."""
    source = Path(path)
    if not source.is_file():
        return False
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper() == APPROVED_DELIVERY_SHA256.upper()


def _luma_percentiles(image: Any) -> Dict[str, float]:
    import numpy as np

    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    luma = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    return {
        "p01": float(np.percentile(luma, 1)),
        "p50": float(np.percentile(luma, 50)),
        "p99": float(np.percentile(luma, 99)),
    }


def renderer_delta(video: str | Path, node: Optional[Any] = None) -> float:
    """Max |ffmpeg - pillow| on p01/p50/p99 over 12 evenly spaced frames.

    ``node=None`` means the approved V2 grade, not "no grade": the baseline has to
    be a real renderer discrepancy or the 0.5x stop rule has nothing to compare
    against. A dict is parsed with :func:`vx.grade.parse`; a ``GradeNode`` is used
    as-is. The Pillow side reuses :func:`vx.grade.apply_pillow` on the same decoded
    frame, so the only difference measured is the renderer, not the decoder.

    Raises ``RuntimeError`` when no frame can be measured. It never returns a
    fabricated number.
    """
    from PIL import Image

    from . import ffmpeg as ff
    from . import grade as grade_mod
    from .probe import probe

    if node is None:
        grade_node = grade_mod.parse(APPROVED_GRADE)
    elif isinstance(node, dict):
        grade_node = grade_mod.parse(node)
    else:
        grade_node = node

    info = probe(str(video))
    duration = float(info.duration)
    if duration <= 0.0:
        raise RuntimeError(f"durata non misurabile: {video}")

    work = Path(tempfile.mkdtemp(prefix="vxdelta-"))
    worst = 0.0
    samples = 0
    for index in range(12):
        at = duration * (index + 0.5) / 12.0
        source = work / f"frame_{index:02d}.png"
        proc = subprocess.run(
            [
                ff.ffmpeg(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{at:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                str(source),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0 or not source.is_file():
            continue

        via_ffmpeg = work / f"frame_{index:02d}_ff.png"
        proc = subprocess.run(
            [
                ff.ffmpeg(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-vf",
                grade_mod.ffmpeg_filter(grade_node),
                "-frames:v",
                "1",
                str(via_ffmpeg),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0 or not via_ffmpeg.is_file():
            continue

        ff_stats = _luma_percentiles(Image.open(via_ffmpeg))
        pillow_stats = _luma_percentiles(
            grade_mod.apply_pillow(Image.open(source), grade_node)
        )
        for key in ("p01", "p50", "p99"):
            worst = max(worst, abs(ff_stats[key] - pillow_stats[key]))
        samples += 1

    if samples == 0:
        raise RuntimeError(f"nessun frame misurabile in {video}")
    return round(worst, 6)


# --------------------------------------------------------------------------- #
# Wave 2.5 — the binary gates
# --------------------------------------------------------------------------- #

# The ink mask tolerance. Declared, not invented: it is the half-width within
# which a pixel counts as the declared ink, and it is validated on the approved
# delivery, where the same mask measures a 0.458% median — the 0.45% lower edge
# the typography thresholds were cut from.
INK_TOLERANCE = 0.06
# The healthy ink band, in percent of the frame. The typography driver measures
# 0.45–0.94% on the delivery; the gate tolerates the wider band.
COVERAGE_MIN = 0.30
COVERAGE_MAX = 2.00
COVERAGE_SAMPLES = 12

# Pair-gate thresholds. Every one is declared in the verdict, not measured here.
HUE_L1_THRESHOLD = 0.15
RHYTHM_TARGET_DELTA = 0.4
GEOMETRY_OFFSET = 0.05
TYPOGRAPHY_SIZE_DELTA = 8
# P1: a hook that opens on speech within 0.4 s (and a face) is a "text" hook.
HOOK_ONSET_TEXT = 0.4

# The three sentences every documentation must carry, verbatim.
MANDATORY_SENTENCES = (
    (
        "La direzione ratio↑→animazione più dura è asserita, non misurata; "
        "solo i punti di taglio sono empirici."
    ),
    (
        "Il gate `distinct_surface` certifica solo la divergenza di superficie "
        "fra due brief; non certifica la differenza di montaggio e non pretende "
        "di certificare la distinzione su materiale arbitrario futuro."
    ),
    (
        "Riduzione di scope rispetto all'ODG: le decorazioni sono fuori da "
        "questa tornata per motivo di budget, non per motivo di gate; il gate "
        "non le richiede e la loro assenza non è un fallimento."
    ),
)


def canonical_json(obj: Any) -> str:
    """The repo's canonical JSON: sorted keys, stable whitespace."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _choice(brief: Dict[str, Any], axis: str) -> Dict[str, Any]:
    choice = (brief.get("choices") or {}).get(axis)
    return choice if isinstance(choice, dict) else {}


def _measure(brief: Dict[str, Any], key: str) -> Any:
    return (brief.get("measurements") or {}).get(key)


def _hue_l1(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """L1 distance between the two hue-mass vectors, as stored (peak rank)."""
    pairs_a = _measure(a, "hue_mass_top3") or []
    pairs_b = _measure(b, "hue_mass_top3") or []
    total = 0.0
    for i in range(3):
        ma = pairs_a[i].get("mass") if i < len(pairs_a) else None
        mb = pairs_b[i].get("mass") if i < len(pairs_b) else None
        total += abs(float(ma or 0.0) - float(mb or 0.0))
    return round(total, 6)


def _size_delta(choice: Dict[str, Any]) -> float:
    """The typography size delta in px; a non-numeric value counts as 0."""
    value = choice.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def hook_type(brief: Dict[str, Any]) -> str:
    """P1 hook taxonomy: ``text`` | ``visual`` | ``title``.

    ``text`` only when speech starts within 0.4 s on a faced clip; otherwise
    ``visual`` when the first-3-seconds motion reaches the corpus p67; else
    ``title``. A missing measurement never invents a bucket.
    """
    onset = _measure(brief, "first_speech_onset_s")
    if (
        onset is not None
        and float(onset) <= HOOK_ONSET_TEXT
        and bool(_measure(brief, "face_present"))
    ):
        return "text"
    motion = _measure(brief, "first3s_motion")
    p67 = (THRESHOLDS.get("hook") or {}).get("p67")
    if motion is not None and p67 is not None and float(motion) >= float(p67):
        return "visual"
    return "title"


def hook_distinct(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """P1: the hooks differ only when hook_type AND animation.name differ.

    The Lead's "3 of 4 components" version is rejected: it could pass with
    identical animation, which would certify a hook the renderer never
    distinguishes.
    """
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    name_a = _choice(a, "animation").get("name")
    name_b = _choice(b, "animation").get("name")
    return hook_type(a) != hook_type(b) and name_a != name_b


def distinct_surface(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """The surface-divergence gate between two briefs. Binary, with receipts.

    Families:
      colour     palette family differs OR grade node type differs OR hue L1 >= 0.15
      rhythm     |cut target delta| >= 0.4 OR animation name/count differs
      geometry   both composition values measured AND both compositions applied
                 AND |dx| >= 0.05 or |dy| >= 0.05
      typography font differs OR size delta >= 8 px

    When geometry is available the gate needs geometry AND two of the other
    three; when it is not, typography cannot substitute it: all three remaining
    families must differ. Scope is ``surface_only`` by contract.
    """
    fam_a = str(_choice(a, "palette").get("value"))
    fam_b = str(_choice(b, "palette").get("value"))
    node_a = str(_choice(a, "grade").get("value"))
    node_b = str(_choice(b, "grade").get("value"))
    l1 = _hue_l1(a, b)
    colour = fam_a != fam_b or node_a != node_b or l1 >= HUE_L1_THRESHOLD

    target_a = _choice(a, "cut_density").get("value")
    target_b = _choice(b, "cut_density").get("value")
    anim_a, anim_b = _choice(a, "animation"), _choice(b, "animation")
    rhythm_target = (
        isinstance(target_a, (int, float))
        and isinstance(target_b, (int, float))
        and not isinstance(target_a, bool)
        and not isinstance(target_b, bool)
        and abs(float(target_a) - float(target_b)) >= RHYTHM_TARGET_DELTA
    )
    rhythm = (
        rhythm_target
        or anim_a.get("name") != anim_b.get("name")
        or anim_a.get("count") != anim_b.get("count")
    )

    comp_a = _choice(a, "composition")
    comp_b = _choice(b, "composition")
    value_a = comp_a.get("value")
    value_b = comp_b.get("value")
    geometry_available = isinstance(value_a, dict) and isinstance(value_b, dict)
    geometry = False
    if geometry_available:
        dx = abs(float(value_a.get("x") or 0.0) - float(value_b.get("x") or 0.0))
        dy = abs(float(value_a.get("y") or 0.0) - float(value_b.get("y") or 0.0))
        geometry = (
            comp_a.get("applied") is True
            and comp_b.get("applied") is True
            and (dx >= GEOMETRY_OFFSET or dy >= GEOMETRY_OFFSET)
        )

    typo_a, typo_b = _choice(a, "typography"), _choice(b, "typography")
    typography = (
        typo_a.get("font") != typo_b.get("font")
        or abs(_size_delta(typo_a) - _size_delta(typo_b)) >= TYPOGRAPHY_SIZE_DELTA
    )

    if geometry_available:
        ok = geometry and sum((colour, rhythm, typography)) >= 2
    else:
        ok = colour and rhythm and typography

    return {
        "ok": bool(ok),
        "scope": "surface_only",
        "families": {
            "colour": bool(colour),
            "rhythm": bool(rhythm),
            "geometry": bool(geometry),
            "typography": bool(typography),
        },
        "geometry_available": bool(geometry_available),
        "hue_l1": l1,
        "hook_types": {"a": hook_type(a), "b": hook_type(b)},
    }


def coverage_gate(brief: Dict[str, Any], render_path: str | Path) -> Dict[str, Any]:
    """The render gate: the ink coverage must sit in the healthy band.

    Twelve evenly spaced frames are decoded from the render; a pixel counts as
    ink when its max-channel distance from the declared ink colour is within
    ``INK_TOLERANCE``. The ink colour is the one the palette would pick for this
    brief (light ink under luma 0.5, dark ink above — the same rule
    ``palette._ink`` applies). ``ink`` is the median fraction in percent.

    P5: with decorations out of scope there is no second layer to measure, so
    ``overlay_pct`` IS ``ink_pct`` — the same number under the name the contract
    uses for it. This is not a skipped measurement; it is the honest statement
    that only one layer exists.

    A render that cannot be read is a failed gate with the reason, never a
    fabricated number.
    """
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    result: Dict[str, Any] = {
        "ok": False,
        "ink_pct": None,
        "overlay_pct": None,
        "n_frames": 0,
        "thresholds": {"ink_min": COVERAGE_MIN, "ink_max": COVERAGE_MAX},
        "tolerance": INK_TOLERANCE,
    }
    path = Path(render_path)
    if not path.is_file():
        result["error"] = f"render mancante: {path}"
        return result

    luma = _measure(brief, "luma_p50")
    value = 0.5 if luma is None else float(luma)
    ink = INK_LIGHT if value < 0.5 else INK_DARK
    result["ink"] = "#{:02x}{:02x}{:02x}".format(*ink)
    ink_arr = np.array(ink, dtype=np.float32) / 255.0

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        result["error"] = f"render non apribile: {path}"
        return result
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    duration = frames / fps if frames > 0 and fps > 0 else 0.0
    if duration <= 0.0:
        capture.release()
        result["error"] = f"durata non misurabile: {path}"
        return result

    fractions: List[float] = []
    for index in range(COVERAGE_SAMPLES):
        at = duration * (index + 0.5) / COVERAGE_SAMPLES
        capture.set(cv2.CAP_PROP_POS_MSEC, at * 1000.0)
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        rgb = frame[:, :, ::-1].astype(np.float32) / 255.0
        distance = np.abs(rgb - ink_arr).max(axis=2)
        fractions.append(float((distance <= INK_TOLERANCE).mean()) * 100.0)
    capture.release()

    if not fractions:
        result["error"] = f"nessun frame leggibile: {path}"
        return result

    ink_pct = round(float(np.median(fractions)), 4)
    result["ink_pct"] = ink_pct
    # P5: decorations are out of scope, so the overlay layer does not exist and
    # its coverage is the text ink coverage. One measurement, two contract names.
    result["overlay_pct"] = ink_pct
    result["n_frames"] = len(fractions)
    result["ok"] = bool(
        COVERAGE_MIN <= ink_pct <= COVERAGE_MAX
        and result["overlay_pct"] <= COVERAGE_MAX
    )
    return result


def _declared_effects(brief: Dict[str, Any]) -> set:
    """The effect names the choices declare, per axis.

    ``grade`` declares its node type, ``animation`` its name, ``cut_density``
    the snap and ``composition`` the crop — the latter two only when applied.
    ``palette`` and ``typography`` declare no ffmpeg effect in
    ``vx.visual/1.0`` (the palette drives colours for a later wave, typography
    has no burn of its own), so they always declare ``none``.
    """
    declared: set = set()
    for axis in AXES:
        choice = _choice(brief, axis)
        if axis == "grade":
            declared.add(str(choice.get("value") or "none"))
        elif axis == "animation":
            declared.add(str(choice.get("name") or "none"))
        elif axis == "cut_density":
            declared.add("snap" if choice.get("applied") else "none")
        elif axis == "composition":
            declared.add("crop" if choice.get("applied") else "none")
        else:  # palette, typography
            declared.add("none")
    return declared


def _named_effects(text: str) -> set:
    """Effect names that appear in prose, executable or retired."""
    names = (set(ANIMATIONS) - {"none"}) | set(RETIRED_EFFECTS)
    return {
        name
        for name in names
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE)
    }


def docs_match(brief: Dict[str, Any], docs_text: str) -> bool:
    """The documentation gate: the docs must describe exactly what executed.

    Four conditions, all binary: the ledger hash (sha256 of the canonical
    ``executed`` list) appears in the text; the text's sha256 equals the
    ``docs_match.doc_sha256`` the brief carries; the declared effect names equal
    the executed names; and the three mandatory sentences are present verbatim.
    """
    if not isinstance(brief, dict) or not isinstance(docs_text, str):
        return False
    executed = brief.get("executed")
    if not isinstance(executed, list):
        return False

    ledger_hash = hashlib.sha256(canonical_json(executed).encode("utf-8")).hexdigest()
    if ledger_hash not in docs_text:
        return False

    docs = brief.get("docs_match") or {}
    doc_hash = docs.get("doc_sha256")
    if not isinstance(doc_hash, str) or not doc_hash:
        return False
    if doc_hash != _sha256_text(docs_text):
        return False

    executed_names = {item.get("name") for item in executed if isinstance(item, dict)}
    if _declared_effects(brief) != executed_names:
        return False

    if _named_effects(docs_text) - executed_names:
        return False

    for sentence in MANDATORY_SENTENCES:
        if sentence not in docs_text:
            return False
    return True


def _selftest() -> int:
    import copy

    from . import brief as brief_mod

    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:44} {detail}")

    valid = brief_mod._valid_brief()
    for item in valid["executed"]:
        if item.get("name") != "none":
            item["filter"] = "curves=m='0/0 1/1'"
    errors = check_ledger(valid)
    check("brief con tutti gli assi applicati passa", errors == [], str(errors))

    false_applied = copy.deepcopy(valid)
    false_applied["choices"]["palette"]["applied"] = False
    false_applied["choices"]["palette"]["inaction"] = True
    false_applied["choices"]["palette"]["confidence"] = 0.5
    errors = check_ledger(false_applied)
    check(
        "applied false con effetto eseguito fallisce",
        errors != [],
        str(errors[:1]),
    )

    uncertain = copy.deepcopy(valid)
    uncertain["choices"]["grade"]["confidence"] = 0.5
    uncertain["choices"]["grade"]["applied"] = True
    errors = check_ledger(uncertain)
    check(
        "confidence 0.5 con applied true fallisce",
        errors != [],
        str(errors[:1]),
    )

    if APPROVED_DELIVERY_PATH.is_file():
        check(
            "reproduce_sha256 sul delivery approvato",
            reproduce_sha256(APPROVED_DELIVERY_PATH),
            str(APPROVED_DELIVERY_PATH),
        )
    else:
        print(f"  [SKIP] delivery approvato non presente: {APPROVED_DELIVERY_PATH}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as handle:
        handle.write(b"not the approved delivery")
        temp_path = Path(handle.name)
    try:
        check(
            "reproduce_sha256 su un file estraneo",
            reproduce_sha256(temp_path) is False,
            str(temp_path),
        )
    finally:
        temp_path.unlink(missing_ok=True)

    print(f"{'OK' if failures == 0 else 'FALLITO'} — {failures} fallimenti")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="vx.prove selftest")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    parser.print_help()
    raise SystemExit(2)
