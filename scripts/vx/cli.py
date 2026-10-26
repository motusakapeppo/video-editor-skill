"""vx.cli — the execution protocol (Fase 7).

Pipeline law:
    ingest  ->  gate (exit 3 = ask the human)  ->  plan  ->  subs  ->  proxy render
            ->  full render  ->  qc  ->  deliverables

Every command is non-interactive. Triage communicates through the exit code and a
JSON/markdown briefing on stdout; the agent driving this toolkit shows it and waits.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .contract import (
    EXIT_MISSING_DEP,
    EXIT_NEEDS_INPUT,
    EXIT_OK,
    EXIT_QC_FAIL,
    EXIT_RENDER_FAIL,
    EXIT_USAGE,
    SCHEMA,
    SAMPLE_FPS_FACE,
    SAMPLE_FPS_VIDEO,
    VERSION,
    MissingDependency,
    QCFailure,
    RenderFailure,
    TriageRequired,
    VxError,
)
from . import ffmpeg as ff
from . import winpath


# --------------------------------------------------------------------------- #
# io helpers
# --------------------------------------------------------------------------- #


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _out(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _load_json(path: str | Path) -> Dict[str, Any]:
    try:
        raw = Path(path).read_bytes()
        text = (
            raw.decode("utf-16")
            if raw[:2] in (b"\xff\xfe", b"\xfe\xff")
            else raw.decode("utf-8-sig")
        )
    except (OSError, UnicodeDecodeError) as exc:
        raise VxError(f"{path}: unreadable JSON ({exc})") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VxError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise VxError(f"{path}: expected a JSON object")
    return data


def _load_brief(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    brief = _load_json(path)
    brief["_path"] = str(Path(path).resolve())
    return brief


def _load_plan(path: str | Path):
    from .contract import EditPlan

    data = _load_json(path)
    known = set(EditPlan.__dataclass_fields__)
    return EditPlan(**{k: v for k, v in data.items() if k in known})


def _visual_from_brief(brief: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The renderer's ``visual`` dict from a ``vx.visual/1.0`` brief.

    ``build_visual_brief`` stores the full decision objects under ``_visual``;
    the renderer only needs the per-segment chains, so the single decisions are
    repeated for every segment (``i % len`` with one entry).
    """
    vis = brief.get("_visual") or {}
    if not isinstance(vis, dict) or not vis:
        return None
    return {
        "grade_nodes": [vis.get("grade_node") or {"type": "none"}],
        "animations": [vis.get("animation") or {"name": "none"}],
    }


def _corpus_hash() -> str:
    """The corpus hash the thresholds carry, or a clear failure."""
    from .registry import THRESHOLDS

    for entry in THRESHOLDS.values():
        provenance = str(entry.get("provenance") or "")
        if provenance.startswith("corpus_hash:"):
            return provenance.split(":", 1)[1]
    raise VxError(
        "soglie non misurate: serve tests/fixtures/corpus_stats.json per il corpus_hash"
    )


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cut_density_choice(measurements: Dict[str, Any]) -> Dict[str, Any]:
    """The cut-density choice from ``onsets_per_s`` and the corpus thresholds.

    Bands from ``registry.THRESHOLDS["cut_density"]``: under p33 -> 3.0 s,
    between p33 and p67 -> 2.0 s, over p67 -> 1.2 s. The confidence mirrors
    ``animplan``: 0.5 on the cut point, rising on a tenth of the band width.
    """
    from .registry import THRESHOLDS

    entry = THRESHOLDS.get("cut_density") or {}
    p33, p67 = entry.get("p33"), entry.get("p67")
    provenance = entry.get("provenance") or "unmeasured"
    onsets = measurements.get("onsets_per_s")
    if onsets is None or p33 is None or p67 is None:
        return {
            "value": 2.0,
            "driver_value": None,
            "threshold": {"p33": p33, "p67": p67},
            "provenance": provenance,
            "inaction_reason": (
                "onsets_per_s non misurato: densita' di default 2.0 s, inazione."
            ),
            "confidence": 0.5,
            "applied": False,
        }
    onsets = float(onsets)
    p33, p67 = float(p33), float(p67)
    if onsets < p33:
        value, distance = 3.0, p33 - onsets
    elif onsets <= p67:
        value, distance = 2.0, min(onsets - p33, p67 - onsets)
    else:
        value, distance = 1.2, onsets - p67
    spread = p67 - p33
    span = 0.1 * spread if spread > 0.0 else 0.0
    confidence = 0.5 + 0.5 * min(1.0, distance / span) if span > 0.0 else 1.0
    uncertain = 0.2 <= confidence <= 0.8
    if uncertain:
        reason = (
            f"confidenza {confidence:.3f} in [0.2, 0.8]: inazione, densita' di "
            "default 2.0 s."
        )
        value = 2.0
    elif onsets < p33:
        reason = f"onsets_per_s {onsets:.4f} < p33 {p33:.4f}: taglio largo 3.0 s."
    elif onsets <= p67:
        reason = (
            f"p33 {p33:.4f} <= onsets_per_s {onsets:.4f} <= p67 {p67:.4f}: "
            "taglio 2.0 s."
        )
    else:
        reason = f"onsets_per_s {onsets:.4f} > p67 {p67:.4f}: taglio stretto 1.2 s."
    return {
        "value": value,
        "driver_value": onsets,
        "threshold": {"p33": p33, "p67": p67},
        "provenance": provenance,
        "inaction_reason": reason,
        "confidence": round(float(confidence), 4),
        "applied": not uncertain,
    }


def _typography_choice(measurements: Dict[str, Any]) -> Dict[str, Any]:
    """The typography choice from the ink coverage measured on a render.

    Under 0.45% the body grows by 8 px, over 0.94% it shrinks by 8 px, in the
    band it stays. An unmeasured ink (source clips) is inaction at 0.5: nothing
    is known, nothing is changed.
    """
    ink = measurements.get("ink_coverage_pct")
    if ink is None:
        return {
            "value": 0,
            "driver_value": None,
            "threshold": {"low": 0.45, "high": 0.94},
            "provenance": "measured:delivery",
            "inaction_reason": (
                "ink_coverage_pct non misurata: corpo invariato, inazione."
            ),
            "confidence": 0.5,
            "applied": False,
        }
    ink = float(ink)
    if ink < 0.45:
        value, distance = 8, 0.45 - ink
        reason = f"ink {ink:.4f}% < 0.45%: corpo +8 px."
    elif ink > 0.94:
        value, distance = -8, ink - 0.94
        reason = f"ink {ink:.4f}% > 0.94%: corpo -8 px."
    else:
        value, distance = 0, min(ink - 0.45, 0.94 - ink)
        reason = f"ink {ink:.4f}% nella banda 0.45-0.94%: corpo invariato."
    confidence = min(1.0, distance / 0.12)
    uncertain = 0.2 <= confidence <= 0.8
    if uncertain:
        reason = (
            f"confidenza {confidence:.3f} in [0.2, 0.8]: inazione, corpo invariato."
        )
        value = 0
    return {
        "value": value,
        "driver_value": ink,
        "threshold": {"low": 0.45, "high": 0.94},
        "provenance": "measured:delivery",
        "inaction_reason": reason,
        "confidence": round(float(confidence), 4),
        "applied": not uncertain,
    }


def _median(values: List[float]) -> Optional[float]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _aggregate_composition(plans: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One composition decision from the whole-clip ``screen.plan`` samples.

    Framing is a project-level decision: the offset is the median of the
    sampled offsets that actually found a face, and the crop box is the
    placement whose offset is closest to that median. At least two of the five
    samples must carry a face; otherwise the clip is centred. The threshold and
    the uncertainty band are the ones ``screen`` declares: this function
    aggregates, it does not invent a second rule.
    """
    from . import screen as screen_mod

    offsets = [
        plan["composition"]["value"]
        for plan in plans
        if plan.get("face_found")
        and isinstance(plan.get("composition"), dict)
        and plan["composition"].get("value") is not None
    ]
    samples = {
        "n": len(plans),
        "with_face": sum(1 for plan in plans if plan.get("face_found")),
        "median": None,
        "rule": "declared:at_least_2_of_5",
    }

    if len(offsets) < 2:
        return {
            "name": "centre",
            "crop": {},
            "driver": screen_mod.DRIVER,
            "value": None,
            "provenance": screen_mod.PROVENANCE,
            "inaction": True,
            "inaction_reason": (
                "meno di due volti nella clip: inquadratura al centro, nessuna scelta."
            ),
            "confidence": screen_mod.NO_FACE_CONFIDENCE,
            "applied": False,
            "samples": samples,
        }

    median_x = _median([float(item["x"]) for item in offsets])
    median_y = _median([float(item["y"]) for item in offsets])
    samples["median"] = {"x": round(median_x, 4), "y": round(median_y, 4)}
    magnitude = max(abs(median_x), abs(median_y))
    confidence = min(1.0, max(0.0, magnitude / screen_mod.OFFSET_THRESHOLD))
    if screen_mod.UNCERTAIN_LOW <= confidence <= screen_mod.UNCERTAIN_HIGH:
        name, applied = "centre", False
        reason = (
            f"offset mediano {magnitude:.4f} vicino alla soglia "
            f"{screen_mod.OFFSET_THRESHOLD}: inazione, inquadratura al centro."
        )
    elif magnitude >= screen_mod.OFFSET_THRESHOLD:
        name, applied = "offset", True
        reason = (
            f"offset mediano {magnitude:.4f} oltre la soglia "
            f"{screen_mod.OFFSET_THRESHOLD}: il crop segue il soggetto."
        )
    else:
        name, applied = "centre", True
        reason = (
            f"offset mediano {magnitude:.4f} sotto la soglia "
            f"{screen_mod.OFFSET_THRESHOLD}: il soggetto e' al centro."
        )

    closest = min(
        (
            plan
            for plan in plans
            if plan.get("face_found")
            and isinstance(plan.get("composition"), dict)
            and plan["composition"].get("value") is not None
        ),
        key=lambda plan: (
            abs(float(plan["composition"]["value"]["x"]) - median_x)
            + abs(float(plan["composition"]["value"]["y"]) - median_y)
        ),
    )
    crop = dict((closest.get("composition") or {}).get("crop") or {})
    return {
        "name": name,
        "crop": crop,
        "driver": screen_mod.DRIVER,
        "value": {"x": round(median_x, 4), "y": round(median_y, 4)},
        "provenance": screen_mod.PROVENANCE,
        "inaction": not applied,
        "inaction_reason": reason,
        "confidence": round(float(confidence), 4),
        "applied": applied,
        "samples": samples,
    }


def _downgrade_no_filter(
    choice: Dict[str, Any],
    reason: str,
    *,
    value: Any = None,
    note: Optional[str] = None,
    reason_field: str = "inaction_reason",
) -> None:
    """Record an inaction when the decision has no executable filter."""
    choice["applied"] = False
    choice["confidence"] = 0.5
    choice[reason_field] = reason
    if reason_field != "inaction":
        choice["inaction"] = True
    if value is not None:
        choice["value"] = value
    if note is not None:
        choice["note"] = note


def build_visual_brief(
    source: str | Path,
    measurements: Dict[str, Any],
    *,
    delivery: str | Path | None = None,
    project: str | None = None,
) -> Dict[str, Any]:
    """Build a ``vx.visual/1.0`` brief from the measured clip.

    The six choices come from the decision modules (``palette``, ``gradeplan``,
    ``animplan``, ``screen`` over five whole-clip timestamps) plus the
    cut-density and typography rules. Two normalisations happen here, by
    contract:

    * P3 — ``analyze.measure_color`` returns the cast in pixel units; any cast
      whose absolute value exceeds 1.0 is divided by 255 before it reaches
      ``gradeplan``, and ``cast_normalised`` is recorded on the brief.
    * P4 — ``gradeplan``/``animplan`` return ``inaction`` as a sentence; the
      brief stores the boolean (``not applied``) and the sentence goes to
      ``inaction_reason``.

    A third normalisation is the wave's invariant: an applied choice must point
    at a filter that actually reaches the render. Palette and typography have no
    filter in this wave, grade and animation must produce a non-empty chain, and
    composition must be an offset crop that differs from the centred default.
    Anything else is downgraded to inaction, with the original decision kept in
    ``note``.

    ``executed`` is left empty: the caller fills it from the real render. The
    full decision objects are kept under ``_visual`` so the renderer can splice
    the chains without re-deciding.
    """
    from . import animplan, brief as brief_mod, gradeplan
    from . import palette as palette_mod
    from . import probe as probe_mod
    from . import screen as screen_mod
    from .registry import THRESHOLDS

    src = Path(source)
    if not src.is_file():
        raise VxError(f"sorgente mancante: {src}")
    m = dict(measurements or {})

    cast_normalised = False
    for key in ("cast_rg", "cast_by"):
        value = m.get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and abs(float(value)) > 1.0
        ):
            m[key] = float(value) / 255.0
            cast_normalised = True

    duration = m.get("duration_s")
    width = m.get("width")
    height = m.get("height")
    fps = m.get("fps")
    if duration is None or not width or not height or fps is None:
        info = probe_mod.probe(str(src))
        duration = float(info.duration) if duration is None else float(duration)
        width = int(info.width) if not width else int(width)
        height = int(info.height) if not height else int(height)
        fps = float(info.fps) if fps is None else float(fps)

    pal = palette_mod.decide(m)
    grade = gradeplan.decide(m, pal)
    ratio = m.get("transient_ratio")
    anim = animplan.decide(
        None if ratio is None else float(ratio), m.get("onsets_per_s")
    )
    stamps = [float(duration) * (index + 0.5) / 5.0 for index in range(5)]
    comp = _aggregate_composition(
        screen_mod.plan(str(src), stamps, int(width), int(height))
    )
    cut = _cut_density_choice(m)
    typo = _typography_choice(m)

    if pal["applied"]:
        original_family = pal["family"]
        pal["family"] = "neutral"
        pal["value"] = "neutral"
        pal["note"] = (
            f"{pal.get('note', '')}; famiglia originale {original_family}"
        ).strip("; ")
        _downgrade_no_filter(
            pal,
            "decisione presa ma nessun filtro eseguibile in questa ondata: "
            "inazione registrata, famiglia neutra",
            value="neutral",
        )

    if grade["applied"] and not gradeplan.chain_for_shot(grade["node"]):
        original_node = (grade.get("node") or {}).get("type")
        grade["node"] = {"type": "none"}
        grade["value"] = "none"
        grade["note"] = f"nodo originale {original_node}"
        _downgrade_no_filter(
            grade,
            "decisione presa ma nessun filtro eseguibile in questa ondata: "
            "inazione registrata, nessun nodo cromatico",
            value="none",
            reason_field="inaction",
        )

    shot = {
        "duration": float(duration),
        "width": int(width),
        "height": int(height),
        "fps": float(fps),
    }
    if anim["applied"] and not animplan.chain_for_shot(anim, shot):
        original_anim = anim["name"]
        anim["name"] = "none"
        anim["count"] = 0
        anim["value"] = "none"
        anim["note"] = f"animazione originale {original_anim}"
        _downgrade_no_filter(
            anim,
            "decisione presa ma nessun filtro eseguibile in questa ondata: "
            "inazione registrata, nessuna animazione",
            value="none",
            reason_field="inaction",
        )

    crop = comp.get("crop") or {}
    crop_w = int(crop.get("crop_w") or 0)
    crop_h = int(crop.get("crop_h") or 0)
    centre_x = (int(width) - crop_w) // 2 if crop_w else None
    centre_y = (int(height) - crop_h) // 2 if crop_h else None
    crop_differs = bool(
        crop_w
        and crop_h
        and (
            int(crop.get("x", centre_x)) != centre_x
            or int(crop.get("y", centre_y)) != centre_y
        )
    )
    if comp["applied"] and not (comp["name"] == "offset" and crop_differs):
        original_comp = comp["name"]
        comp["value"] = "centre"
        comp["note"] = f"inquadratura originale {original_comp}"
        _downgrade_no_filter(
            comp,
            "decisione presa ma nessun filtro eseguibile in questa ondata: "
            "inazione registrata, inquadratura al centro",
            value="centre",
        )

    if typo["applied"]:
        original_typo = typo["value"]
        typo["note"] = f"valore originale {original_typo}"
        _downgrade_no_filter(
            typo,
            "decisione presa ma nessun filtro eseguibile in questa ondata: "
            "inazione registrata, corpo invariato",
            value=0,
        )

    anim_th = THRESHOLDS.get("animation") or {}
    brief = brief_mod.new_brief(project or src.stem, _sha256_file(src), _corpus_hash())
    brief["measurements"] = {
        "transient_ratio": m.get("transient_ratio"),
        "transient_p33": anim_th.get("p33"),
        "transient_p67": anim_th.get("p67"),
        "hue_mass_top3": m.get("hue_mass_top3") or [],
        "ink_coverage_pct": m.get("ink_coverage_pct"),
        "subject_offset": m.get("subject_offset"),
        "onsets_per_s": m.get("onsets_per_s"),
        "first_speech_onset_s": m.get("first_speech_onset_s"),
        "first3s_motion": m.get("first3s_motion"),
        "face_present": bool(m.get("face_present")),
        "luma_p50": m.get("luma_p50"),
        "contrast": m.get("contrast"),
    }
    brief["cast_normalised"] = cast_normalised
    if delivery is not None:
        brief["delivery"] = str(delivery)
    brief["_visual"] = {
        "grade_node": grade["node"],
        "animation": anim,
        "palette": pal,
        "composition": comp,
    }

    choices = brief["choices"]
    choices["palette"].update(
        driver=pal["driver"],
        value=pal["family"],
        provenance=pal["provenance"],
        inaction=bool(pal["inaction"]),
        inaction_reason=pal["inaction_reason"],
        confidence=pal["confidence"],
        applied=pal["applied"],
    )
    choices["grade"].update(
        driver=grade["driver"],
        value=grade["node"]["type"],
        provenance=grade["provenance"],
        inaction=not grade["applied"],
        inaction_reason=grade["inaction"],
        confidence=grade["confidence"],
        applied=grade["applied"],
    )
    choices["animation"].update(
        driver=anim["driver"],
        value=anim["name"],
        name=anim["name"],
        count=anim["count"],
        direction_basis="asserted",
        provenance=anim["provenance"],
        inaction=not anim["applied"],
        inaction_reason=anim["inaction"],
        confidence=anim["confidence"],
        applied=anim["applied"],
    )
    choices["cut_density"].update(
        driver="onsets_per_s",
        value=cut["value"],
        provenance=cut["provenance"],
        inaction=not cut["applied"],
        inaction_reason=cut["inaction_reason"],
        confidence=cut["confidence"],
        applied=cut["applied"],
    )
    choices["composition"].update(
        driver=comp["driver"],
        value=comp["value"] if comp["value"] is not None else "centre",
        provenance=comp["provenance"],
        inaction=bool(comp["inaction"]),
        inaction_reason=comp["inaction_reason"],
        confidence=comp["confidence"],
        applied=comp["applied"],
    )
    choices["typography"].update(
        driver="ink_coverage_pct",
        value=typo["value"],
        provenance=typo["provenance"],
        inaction=not typo["applied"],
        inaction_reason=typo["inaction_reason"],
        confidence=typo["confidence"],
        applied=typo["applied"],
    )

    brief_mod.add_reason(
        brief,
        "palette",
        pal["driver"],
        pal["value"],
        {
            "p33": (THRESHOLDS.get("palette") or {}).get("p33"),
            "p67": (THRESHOLDS.get("palette") or {}).get("p67"),
        },
        pal["provenance"],
        pal["inaction_reason"],
    )
    brief_mod.add_reason(
        brief,
        "grade",
        grade["driver"],
        grade["value"],
        {
            "p33": (THRESHOLDS.get("grade") or {}).get("p33"),
            "p67": (THRESHOLDS.get("grade") or {}).get("p67"),
        },
        grade["provenance"],
        grade["inaction"],
    )
    brief_mod.add_reason(
        brief,
        "animation",
        anim["driver"],
        anim["value"],
        {"p33": anim_th.get("p33"), "p67": anim_th.get("p67")},
        anim["provenance"],
        anim["inaction"],
        direction_basis="asserted",
    )
    brief_mod.add_reason(
        brief,
        "cut_density",
        "onsets_per_s",
        cut["driver_value"],
        cut["threshold"],
        cut["provenance"],
        cut["inaction_reason"],
    )
    brief_mod.add_reason(
        brief,
        "composition",
        comp["driver"],
        comp["value"],
        screen_mod.OFFSET_THRESHOLD,
        comp["provenance"],
        comp["inaction_reason"],
    )
    brief_mod.add_reason(
        brief,
        "typography",
        "ink_coverage_pct",
        typo["driver_value"],
        typo["threshold"],
        typo["provenance"],
        typo["inaction_reason"],
    )
    return brief


def cmd_visual(args: argparse.Namespace) -> int:
    measurements = _load_json(args.measurements)
    brief = build_visual_brief(
        args.source, measurements, delivery=args.delivery, project=args.project
    )
    if args.out:
        out = Path(args.out)
    else:
        src = Path(args.source)
        out = src.parent / f"{src.stem}.visual.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    from . import brief as brief_mod

    out.write_text(brief_mod.to_json(brief), encoding="utf-8")
    print(str(out))
    return EXIT_OK


def _enabled(plan, name: str) -> bool:
    for item in plan.treatments or []:
        if item.get("name") == name:
            return bool(item.get("enabled"))
    return False


def _params(plan, name: str) -> Dict[str, Any]:
    for item in plan.treatments or []:
        if item.get("name") == name:
            return item.get("params") or {}
    return {}


def _tool_block() -> Dict[str, Any]:
    return {
        "name": "video-editor",
        "version": VERSION,
        "ffmpeg": ff.version(),
        "python": sys.version.split()[0],
        "schema": SCHEMA,
    }


# --------------------------------------------------------------------------- #
# commands — inspection
# --------------------------------------------------------------------------- #


def cmd_doctor(args: argparse.Namespace) -> int:
    from . import doctor

    rep = doctor.scan()
    if args.json:
        _out(rep)
    else:
        print(doctor.to_text(rep))
    return EXIT_OK if rep.get("verdict") != "broken" else EXIT_MISSING_DEP


def cmd_probe(args: argparse.Namespace) -> int:
    from . import probe as probe_mod
    from . import report as report_io

    result = probe_mod.probe(args.input, count_frames=args.count_frames).to_dict()
    if args.report:
        rep = report_io.merge(report_io.read(args.report), "source", result)
        report_io.write(rep, args.report)
    _out(result)
    return EXIT_OK


def cmd_proxy(args: argparse.Namespace) -> int:
    from . import proxy as proxy_mod

    src = Path(args.input)
    work = Path(args.workdir) if args.workdir else winpath.make_workdir(str(src))
    dst = Path(args.out) if args.out else work / "proxy" / "proxy.mp4"
    _log(f"[proxy] {src.name} -> {dst} @ {args.width}px")
    result = proxy_mod.make_proxy(str(src), dst, width=args.width, workdir=work)
    if not args.no_previews:
        thumbs = proxy_mod.thumbs(str(src), work / "analysis", count=args.thumbs)
        result["thumbs"] = thumbs
        result["contact_sheet"] = proxy_mod.contact_sheet(
            thumbs, work / "analysis" / "contact.jpg"
        )
    _out(result)
    return EXIT_OK


def cmd_analyze(args: argparse.Namespace) -> int:
    from . import analyze, audio, faces
    from . import probe as probe_mod
    from . import report as report_io

    src = Path(args.input)
    probe_data = probe_mod.probe(str(src)).to_dict()
    _log("[1/3] video: jitter, esposizione, colore, freeze, tagli, ritmo")
    video = analyze.analyze_video(
        str(src), sample_fps=args.sample_fps, proxy_path=args.proxy or None
    )
    _log("[2/3] audio: noise floor, dead air, loudness, clipping")
    aud = audio.analyze_audio(str(src))
    _log("[3/3] volti e speaker")
    face_src = args.proxy or str(src)
    fac = faces.detect_faces_tracked(face_src, sample_fps=SAMPLE_FPS_FACE)
    if args.proxy:
        proxy_probe = probe_mod.probe(face_src).to_dict()
        fac = faces.rescale_tracks(
            fac,
            int(proxy_probe.get("width") or 0),
            int(proxy_probe.get("height") or 0),
            int(probe_data.get("width") or 0),
            int(probe_data.get("height") or 0),
        )

    payload = {
        "video": video.to_dict(),
        "audio": aud.to_dict(),
        "faces": fac.to_dict(),
    }
    if args.speech:
        payload["speech"], _ = _transcribe(str(src), args.model, args.lang)

    if args.report:
        rep = report_io.read(args.report)
        report_io.merge(rep, "analysis", payload)
        report_io.write(rep, args.report)
        _log(f"[ok] report aggiornato: {args.report}")
    _out({"source": probe_data, "analysis": payload})
    return EXIT_OK


def _speech_block(transcript, model: str = "") -> Dict[str, Any]:
    """Report-safe speech stats: counts and reasons, never the word list."""
    from . import subs

    if transcript is None:
        return {"skipped_reason": "no transcript"}
    stats = subs.transcript_stats(transcript)
    return {
        "language": transcript.language or "",
        "word_count": int(stats.get("word_count") or 0),
        "wpm": float(stats.get("wpm") or 0.0),
        "fillers": int(stats.get("fillers") or 0),
        "model": transcript.model or model,
        "skipped_reason": transcript.skipped_reason or "",
    }


def _vad_config() -> Dict[str, Any]:
    from . import settings

    cfg = settings.current().get("audio") or {}
    return {
        "threshold": float(cfg.get("vad_threshold", 0.5)),
        "min_speech": float(cfg.get("vad_min_speech", 0.25)),
        "min_silence": float(cfg.get("vad_min_silence", 0.35)),
    }


def _analysis_chunk_sec(args=None) -> float:
    value = getattr(args, "chunk_sec", None) if args is not None else None
    if value is not None:
        try:
            return max(1.0, float(value))
        except (TypeError, ValueError):
            pass
    from . import settings

    try:
        return float(settings.get("analysis.chunk_sec", 300.0))
    except (TypeError, ValueError):
        return 300.0


def _chunked_report(state: Dict[str, Any]) -> Dict[str, Any]:
    stats = dict(state.get("stats") or {})
    vad = state.get("vad")
    regions = len(getattr(vad, "regions", []) or [])
    return {
        "stats": stats,
        "vad": {
            "engine": str(getattr(vad, "engine", "none") or "none"),
            "available": bool(getattr(vad, "available", False)),
            "speech_ratio": float(getattr(vad, "speech_ratio", 0.0) or 0.0),
            "snr_db": (float(getattr(vad, "snr_db", 0.0) or 0.0) if regions else None),
            "regions": regions,
            "reason": str(getattr(vad, "reason", "") or ""),
        },
    }


def _analyze_speech(
    src: str,
    *,
    workdir: Path,
    source_sha256: str,
    analysis_sha256: str,
    media: Dict[str, Any],
    model: str,
    language: Optional[str],
    transcribe: bool,
    chunk_sec: Optional[float] = None,
) -> tuple[Dict[str, Any], Any, Optional[Dict[str, Any]]]:
    """Speech stats, transcript and chunked state; short clips keep the old path."""
    from . import analyze

    if not media.get("has_audio"):
        return {"skipped_reason": "no audio"}, None, None
    duration = float(media.get("duration") or 0.0)
    window = float(chunk_sec) if chunk_sec is not None else _analysis_chunk_sec()
    if not analyze.should_chunk(duration, True, chunk_sec=window):
        if not transcribe:
            return {"skipped_reason": "disabled"}, None, None
        block, transcript = _transcribe(src, model, language)
        return block, transcript, None

    state = analyze.analyze_chunked(
        src,
        workdir=workdir,
        source_sha256=source_sha256,
        analysis_sha256=analysis_sha256,
        chunk_sec=window,
        model=model,
        language=language,
        vad_config=_vad_config(),
        transcribe=transcribe,
    )
    stats = state.get("stats") or {}
    _log(
        "[chunk] {total} blocchi: {cached} da cache, {computed} calcolati, "
        "{failed} falliti".format(
            total=stats.get("chunks_total", 0),
            cached=stats.get("chunks_cached", 0),
            computed=stats.get("chunks_computed", 0),
            failed=stats.get("chunk_failures", 0),
        )
    )
    if not transcribe:
        return {"skipped_reason": "disabled"}, None, state
    transcript = state.get("transcript")
    return _speech_block(transcript, model), transcript, state


def _transcribe(
    src: str, model: str = "tiny", lang: Optional[str] = None
) -> tuple[Dict[str, Any], Any]:
    """Speech stats for the report plus the transcript object, so `run` never transcribes twice.

    A failed transcription is data too, never a fatal error.
    """
    try:
        from . import subs

        tr = subs.transcribe(src, model=model, language=lang)
        return _speech_block(tr, model), tr
    except Exception as exc:  # noqa: BLE001 - a missing model must not kill the run
        _log(f"[warn] trascrizione non disponibile: {exc}")
        return {"skipped_reason": str(exc)}, None


# --------------------------------------------------------------------------- #
# commands — subtitles
# --------------------------------------------------------------------------- #


def _layout_zone(out_w: int, out_h: int, overlay_paths: Optional[Sequence[str]]):
    """Misura l'ingombro grafico, oppure None se nessun overlay e' dichiarato."""
    from . import layout as layout_mod  # noqa: PLC0415

    overlays = {Path(p).stem: p for p in (overlay_paths or [])}
    if not overlays:
        return None
    return layout_mod.build(int(out_w), int(out_h), overlays)


def _layout_gate(zone, ass_path: str) -> Dict[str, Any]:
    """Cancello 4 sul box dichiarato. Solleva LayoutFailure (uscita 6) se collide."""
    from . import gates  # noqa: PLC0415
    from . import subs  # noqa: PLC0415

    declared = subs.declared_box(ass_path)
    box = zone.text_box(
        lines=declared["lines"],
        font_px=declared["font_px"],
        line_px=declared["line_px"],
        outline_px=declared["outline_px"],
    )
    return gates.layout(zone, box).to_dict()


def cmd_subs(args: argparse.Namespace) -> int:
    from . import subs

    if args.style == "none":
        _out({"skipped": "style none"})
        return EXIT_OK

    aspect = args.aspect
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = winpath.safe_stem(Path(args.input).name)

    _log(f"[subs] whisper model={args.model} lang={args.lang or 'auto'}")
    transcript = subs.transcribe(str(args.input), model=args.model, language=args.lang)
    if transcript.skipped_reason:
        _log(f"[warn] {transcript.skipped_reason}")

    cues = subs.build_cues(transcript, style=args.style, aspect=aspect)

    zone = _layout_zone(args.width, args.height, getattr(args, "overlay", None))
    ass = subs.write_ass(
        cues,
        out_dir / f"{stem}.ass",
        aspect=aspect,
        platform=args.platform,
        style=args.style,
        out_w=args.width,
        out_h=args.height,
        bottom_limit_px=zone.text_limit_y if zone else None,
        margin_lr=zone.margin_lr if zone else None,
    )
    gate4 = _layout_gate(zone, ass) if zone else None
    srt = subs.write_srt(cues, out_dir / f"{stem}.srt")
    fonts = subs.verify_fonts(ass)

    _out(
        {
            "ass": ass,
            "srt": srt,
            "cues": len(cues),
            "words": len(transcript.words),
            "wpm": subs.transcript_stats(transcript).get("wpm", 0.0),
            "language": transcript.language,
            "style": args.style,
            "fonts": fonts,
            "burn_filter": subs.burn_filter(ass),
            "layout": zone.to_dict() if zone else None,
            "gate4": gate4,
        }
    )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# commands — triage / plan
# --------------------------------------------------------------------------- #


def cmd_triage(args: argparse.Namespace) -> int:
    from . import plan as plan_mod
    from . import report as report_io
    from . import triage

    rep = report_io.read(args.report)
    rep.setdefault("_path", str(Path(args.report).resolve()))
    brief = _load_brief(args.brief)
    req = plan_mod.needs_triage(brief, rep)
    if req is None:
        print("Brief completo: nessun triage necessario.")
        return EXIT_OK
    if args.json:
        _out(req)
    else:
        print(triage.to_markdown(req))
    return EXIT_NEEDS_INPUT


def cmd_plan(args: argparse.Namespace) -> int:
    from . import plan as plan_mod
    from . import report as report_io
    from . import triage

    rep = report_io.read(args.report)
    rep.setdefault("_path", str(Path(args.report).resolve()))
    brief = _load_brief(args.brief)
    answers = _load_json(args.answers) if args.answers else None
    if answers and args.answers:
        brief["_answers_path"] = str(Path(args.answers).resolve())

    req = plan_mod.needs_triage(brief, rep)
    if req is not None and answers is None:
        if args.json:
            _out(req)
        else:
            print(triage.to_markdown(req))
        return EXIT_NEEDS_INPUT

    brief.setdefault("source", (rep.get("source") or {}).get("path"))
    built = plan_mod.build_plan(rep, brief, answers)
    errors = plan_mod.validate(built, rep)
    if errors:
        for err in errors:
            _log(f"[plan] {err}")
        return EXIT_USAGE

    if args.out:
        report_io.write(built.to_dict(), args.out)
        _log(f"[ok] plan scritto: {args.out}")
    _out(built.to_dict())
    return EXIT_OK


def cmd_deliverables(args: argparse.Namespace) -> int:
    from . import plan as plan_mod

    built = _load_plan(args.plan)
    items = plan_mod.deliverables(built)
    if args.out:
        Path(args.out).write_text(
            json.dumps({"deliverables": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    _out({"deliverables": items})
    return EXIT_OK


# --------------------------------------------------------------------------- #
# commands — render / qc
# --------------------------------------------------------------------------- #


def _norm_source_path(value: Any) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(value)))
    except (OSError, ValueError):
        return str(value)


def _same_number(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) <= 1e-3
    except (TypeError, ValueError):
        return a == b


def _resolve_report(
    args: argparse.Namespace, plan_path: Path
) -> Optional[Dict[str, Any]]:
    """Explicit ``--report`` -> sibling ``report.json`` -> None."""
    explicit = getattr(args, "report", None)
    if explicit:
        path: Optional[Path] = Path(explicit)
    else:
        sibling = plan_path.parent / "report.json"
        path = sibling if sibling.is_file() else None
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VxError(f"report illeggibile: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise VxError(f"report non e' un oggetto JSON: {path}")
    return data


def _verify_source_identity(report: Dict[str, Any], media: Dict[str, Any]) -> None:
    """The report must describe the same source the renderer is about to read."""
    source = report.get("source")
    if not isinstance(source, dict):
        raise VxError("report.source mancante: impossibile provare la sorgente")
    if _norm_source_path(source.get("path")) != _norm_source_path(media.get("path")):
        raise VxError("report.source.path non combacia con il media analizzato")
    for key in ("size", "mtime", "duration", "width", "height"):
        if key in source and key in media and not _same_number(source[key], media[key]):
            raise VxError(f"report.source.{key} non combacia con il media analizzato")


def cmd_render(args: argparse.Namespace) -> int:
    from . import gates
    from . import probe as probe_mod
    from . import render

    built = _load_plan(args.plan)
    source = args.input or (built.brief or {}).get("source")
    if not source:
        raise VxError("serve --input: il piano non contiene la sorgente")
    media = probe_mod.probe(source).to_dict()

    report = _resolve_report(args, Path(args.plan))
    if report is None:
        enabled = [t for t in (built.treatments or []) if t.get("enabled")]
        if enabled:
            _log(
                "[render] piano con trattamenti attivi ma nessun report: "
                "l'evidenza non e' verificabile"
            )
            return EXIT_USAGE
    else:
        _verify_source_identity(report, media)

    if report is not None:
        declared = (built.brief or {}).get("autonomous_findings") or []
        if declared:
            report.setdefault("findings", [])
            seen = {f.get("id") for f in report["findings"] if isinstance(f, dict)}
            report["findings"].extend(f for f in declared if f.get("id") not in seen)

    visual = (
        _visual_from_brief(_load_json(args.visual_brief))
        if getattr(args, "visual_brief", None)
        else None
    )
    work = Path(args.workdir) if args.workdir else winpath.work_root() / "manual"
    _log(f"[render] precision={args.precision}")
    gates.pre_render(built, media, findings=(report or {}).get("findings"))
    result = render.render(
        built,
        media,
        precision=args.precision,
        workdir=work,
        subs_path=args.subs or None,
        crop_plan=(built.render or {}).get("crop"),
        visual=visual,
    )
    _out({k: v for k, v in result.items() if k not in ("graph", "script")})
    return EXIT_OK


def cmd_qc(args: argparse.Namespace) -> int:
    from . import probe as probe_mod
    from . import qc

    built = _load_plan(args.plan)
    source = args.input or (built.brief or {}).get("source")
    if not source:
        raise VxError("serve --input: il piano non contiene la sorgente")
    media = probe_mod.probe(source).to_dict()
    executed = None
    sidecar = Path(args.plan).parent / "render.json"
    if sidecar.is_file():
        executed = (_load_json(sidecar) or {}).get("target")
    result = qc.check_render(
        built,
        media,
        args.render,
        subs_path=args.subs or None,
        executed_target=executed,
    )
    if args.out:
        Path(args.out).write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    _out(result.to_dict())
    return EXIT_OK if result.pass_ else EXIT_QC_FAIL


# --------------------------------------------------------------------------- #
# commands — the protocol
# --------------------------------------------------------------------------- #


def _ingest(
    src: Path,
    args: argparse.Namespace,
    brief_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fase 1 + Fase 4: proxy, pre-analysis, findings. Never mutates the source."""
    from . import analyze, audio, faces
    from . import ingest as ingest_mod
    from . import plan as plan_mod
    from . import probe as probe_mod
    from . import proxy as proxy_mod
    from . import report as report_io
    from . import settings
    from . import triage

    fingerprint = winpath.source_fingerprint(str(src))
    probe_data = probe_mod.probe(str(src)).to_dict()
    if args.workdir:
        work = Path(args.workdir)
        work.mkdir(parents=True, exist_ok=True)
    else:
        stale = (
            winpath.work_root()
            / f"{winpath.safe_stem(src.name)}-{fingerprint['sha256'][:8]}"
        )
        winpath.clear_lock(stale)
        work = winpath.make_workdir(str(src))

    raw_stream = probe_mod.raw_video_stream(str(src))
    normalization = ingest_mod.normalize_source(str(src), raw_stream, work / "stage_00")
    analysis_src = Path(normalization.staged_path)
    analysis_probe = (
        probe_mod.probe(str(analysis_src)).to_dict()
        if normalization.is_staged
        else probe_data
    )
    if normalization.is_staged:
        _log(f"[0/6] ingest: {', '.join(normalization.filters_applied)}")

    _log("[1/6] proxy a bassa risoluzione")
    proxy_info = proxy_mod.make_proxy(
        str(analysis_src), work / "proxy" / "proxy.mp4", width=args.width
    )
    proxy_path = proxy_info["path"]

    _log("[2/7] pre-analisi video (proxy)")
    video = analyze.analyze_video(
        str(analysis_src), sample_fps=args.sample_fps, proxy_path=proxy_path
    )
    analysis_sha = (
        winpath.source_fingerprint(str(analysis_src))["sha256"]
        if normalization.is_staged
        else fingerprint["sha256"]
    )
    _log("[3/7] parlato")
    speech, transcript, chunked = _analyze_speech(
        str(analysis_src),
        workdir=work,
        source_sha256=fingerprint["sha256"],
        analysis_sha256=analysis_sha,
        media=analysis_probe,
        model=args.model,
        language=args.lang,
        transcribe=not getattr(args, "no_speech", False),
        chunk_sec=_analysis_chunk_sec(args),
    )
    _log("[4/7] pre-analisi audio")
    aud = audio.analyze_audio(str(analysis_src), vad_result=(chunked or {}).get("vad"))
    _log("[5/7] volti e speaker")
    fac = faces.detect_faces_tracked(proxy_path, sample_fps=SAMPLE_FPS_FACE)
    # Detection ran on the proxy; every consumer below reads `analysis_probe`
    # dimensions (crop, report, plan). Convert once, here.
    fac = faces.rescale_tracks(
        fac,
        int(proxy_info.get("width") or 0),
        int(proxy_info.get("height") or 0),
        int(analysis_probe.get("width") or 0),
        int(analysis_probe.get("height") or 0),
    )
    _log("[6/7] anteprime")
    thumbs = proxy_mod.thumbs(str(src), work / "analysis", count=args.thumbs)
    sheet = proxy_mod.contact_sheet(thumbs, work / "analysis" / "contact.jpg")

    rep = report_io.new_report(probe_data, tool=_tool_block())
    report_path = work / "report.json"
    rep["source"]["sha256"] = fingerprint["sha256"]
    rep["source"]["analysis_duration"] = analysis_probe.get("duration")
    rep["source"]["staged"] = (
        normalization.staged_path if normalization.is_staged else None
    )
    rep["source"]["normalized"] = normalization.to_dict()
    rep["workdir"] = str(work)
    rep["settings"] = settings.describe()
    rep["_path"] = str(report_path)
    rep["artifacts"] = {
        "proxy": proxy_path,
        "thumbs": thumbs,
        "contact_sheet": sheet,
        "ass": None,
        "srt": None,
        "render": None,
        "report": str(report_path),
        "qc": None,
    }
    analysis_block = {
        "video": video.to_dict(),
        "audio": aud.to_dict(),
        "faces": fac.to_dict(),
        "speech": speech,
    }
    if chunked is not None:
        analysis_block["chunked"] = _chunked_report(chunked)
    report_io.merge(rep, "analysis", analysis_block)
    report_io.merge(rep, "findings", plan_mod.detect_findings(rep))

    brief = brief_override if brief_override is not None else _load_brief(args.brief)
    autonomous = bool(getattr(args, "autonomous", False))
    _log("[7/7] triage")
    request = plan_mod.needs_triage(brief, rep, autonomous=autonomous)
    if autonomous:
        rep["triage"] = {
            "status": "autonomous",
            "questions": [],
            "defaults": plan_mod.autonomous_defaults(rep, brief),
        }
    else:
        rep["triage"] = request
    report_io.write(rep, report_path)
    if request is not None:
        report_io.write(request, work / "triage.json")
        (work / "triage.md").write_text(triage.to_markdown(request), encoding="utf-8")
    winpath.clear_lock(work)

    return {
        "workdir": work,
        "report": rep,
        "report_path": report_path,
        "proxy": proxy_info,
        "triage": request,
        "fingerprint": fingerprint,
        "probe": analysis_probe,
        "analysis_source": str(analysis_src),
        "analysis_sha256": analysis_sha,
        "normalization": normalization.to_dict(),
        "thumbs": thumbs,
        "contact_sheet": sheet,
        "transcript": transcript,
        "chunked": chunked,
    }


def cmd_ingest(args: argparse.Namespace) -> int:
    from . import triage

    src = Path(args.input)
    state = _ingest(src, args)

    request = state["triage"]
    recorded = (state["report"] or {}).get("triage")
    autonomous = bool(getattr(args, "autonomous", False))
    if args.json:
        _out(
            {
                "workdir": str(state["workdir"]),
                "report": str(state["report_path"]),
                "proxy": state["proxy"],
                "triage": recorded,
                "verdict": (
                    "autonomous"
                    if autonomous
                    else ("needs_user_input" if request else "ready_for_plan")
                ),
                "resume_command": (request or {}).get("resume_command"),
            }
        )
    else:
        if request is not None:
            print(triage.to_markdown(request))
            print(f"\nReport: {state['report_path']}")
            print(f"Anteprime: {state['contact_sheet']}")
        elif autonomous:
            print("Modalita' autonoma: nessuna domanda, default registrati nel report.")
            print(f"Report: {state['report_path']}")
        else:
            print("Brief completo: pronto per il piano.")
            print(f"Report: {state['report_path']}")
    return EXIT_NEEDS_INPUT if request is not None else EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    """Fase 7 end-to-end: ingest -> plan -> subs -> proxy -> full -> qc -> deliverables."""
    from . import autoframe, render, subs
    from . import gates
    from . import plan as plan_mod
    from . import qc, triage
    from .contract import FaceTracks
    from . import report as report_io

    src = Path(args.input)
    answers = _load_json(args.answers) if args.answers else None

    payload = None
    brief_override: Optional[Dict[str, Any]] = None
    if getattr(args, "payload", None):
        payload = _load_json(args.payload)
    elif getattr(args, "prompt", None):
        from . import decide as decide_mod

        try:
            payload = decide_mod.parse_and_decide(args.prompt)
        except decide_mod.AmbiguousContractError as exc:
            _log(f"[run] prompt ambiguo in STRICT: {exc}")
            return EXIT_NEEDS_INPUT
    if payload is not None:
        if not isinstance(payload, dict) or "scene" not in payload:
            _log("[run] payload non valido: manca scene")
            return EXIT_USAGE
        brief_override = {
            "format": payload["scene"]["aspect_ratio"],
            "hook": "none",
            "subtitles": "none",
            "source": str(src),
        }
        args.no_speech = True
        answers = {}

    state = _ingest(src, args, brief_override=brief_override)

    rep = state["report"]
    work: Path = state["workdir"]
    media = state["probe"]
    brief = brief_override if brief_override is not None else _load_brief(args.brief)
    if answers is None and getattr(args, "autonomous", False):
        answers = {}
    if answers is None:
        request = state["triage"] or plan_mod.needs_triage(brief, rep)
        if request is not None:
            if args.json:
                _out(request)
            else:
                print(triage.to_markdown(request))
            return EXIT_NEEDS_INPUT
    else:
        brief["_answers_path"] = (
            None if not args.answers else str(Path(args.answers).resolve())
        )

    brief = plan_mod.apply_answers(brief, answers or {})
    brief.setdefault("source", (rep.get("source") or {}).get("path"))
    visual = (
        _visual_from_brief(_load_json(args.visual_brief))
        if getattr(args, "visual_brief", None)
        else None
    )

    if (
        str(brief.get("subtitles") or "auto") != "none"
        and state.get("transcript") is None
    ):
        _log("[run] trascrizione (serve al piano e ai sottotitoli)")
        speech, transcript, chunked = _analyze_speech(
            state.get("analysis_source", str(src)),
            workdir=work,
            source_sha256=state["fingerprint"]["sha256"],
            analysis_sha256=state.get("analysis_sha256")
            or state["fingerprint"]["sha256"],
            media=state["probe"],
            model=args.model,
            language=args.lang,
            transcribe=True,
            chunk_sec=_analysis_chunk_sec(args),
        )
        report_io.merge(rep, "analysis", {"speech": speech})
        if chunked is not None:
            report_io.merge(rep, "analysis", {"chunked": _chunked_report(chunked)})
        state["transcript"] = transcript

    _log("[run] piano")
    if payload is not None:
        from . import contract as contract_mod

        built = contract_mod.payload_to_edit_plan(payload, state["probe"])
        bridge = (built.brief or {}).get("bridge") or {}
        _log(f"[bridge] eseguiti: {bridge.get('executed')}")
        if bridge.get("unsupported"):
            _log(f"[bridge] non eseguiti: {bridge.get('unsupported')}")
    else:
        built = plan_mod.build_plan(rep, brief, answers or {})
    declared = (built.brief or {}).get("autonomous_findings") or []
    if declared:
        rep.setdefault("findings", [])
        seen = {f.get("id") for f in rep["findings"] if isinstance(f, dict)}
        rep["findings"].extend(f for f in declared if f.get("id") not in seen)
    errors = plan_mod.validate(built, rep)
    if errors:
        for err in errors:
            _log(f"[plan] {err}")
        return EXIT_USAGE
    # ---- subtitles
    subs_path: Optional[str] = None
    hook_path: Optional[str] = None
    stem = winpath.safe_stem(src.name)
    if _enabled(built, "subtitles"):
        _log("[run] sottotitoli dinamici")
        params = _params(built, "subtitles")
        transcript = state.get("transcript") or subs.transcribe(
            str(src), model=args.model, language=args.lang
        )
        quality = subs.transcript_quality(getattr(transcript, "words", []) or [])
        if not quality["ok"]:
            _log(f"[run] sottotitoli non incisi: {quality['reason']}")
            for t in built.treatments or []:
                if t.get("name") == "subtitles":
                    t["enabled"] = False
                    t.setdefault("reason", {})["explain"] = (
                        f"non incisi: {quality['reason']}"
                    )
            rep.setdefault("skipped", []).append({"treatment": "subtitles", **quality})
        else:
            cues = subs.build_cues(
                transcript,
                style=str(params.get("style") or "karaoke"),
                aspect=str(built.output.get("aspect") or "9:16"),
                max_chars=params.get("max_chars"),
                max_lines=params.get("max_lines"),
                max_cps=params.get("max_cps"),
            )
            zone = _layout_zone(
                int(built.output.get("width") or 1080),
                int(built.output.get("height") or 1920),
                getattr(args, "overlay", None),
            )
            ass = subs.write_ass(
                cues,
                work / "subs" / f"{stem}.ass",
                aspect=str(built.output.get("aspect") or "9:16"),
                platform=str(built.output.get("platform") or "tiktok"),
                style=str(params.get("style") or "karaoke"),
                out_w=int(built.output.get("width") or 1080),
                out_h=int(built.output.get("height") or 1920),
                bottom_limit_px=zone.text_limit_y if zone else None,
                margin_lr=zone.margin_lr if zone else None,
            )
            if zone is not None:
                rep["artifacts"]["gate4"] = _layout_gate(zone, ass)
                rep["artifacts"]["layout"] = zone.to_dict()
            srt = subs.write_srt(cues, work / "subs" / f"{stem}.srt")
            fonts = subs.verify_fonts(ass)
            if not fonts.get("ok", True):
                raise QCFailure(
                    {
                        "step": "fonts",
                        "failures": [
                            f"font sostituito da libass: {fonts.get('fonts_used')}"
                        ],
                        "metrics": fonts,
                    }
                )
            subs_path = ass
            rep["artifacts"]["ass"] = ass
            rep["artifacts"]["srt"] = srt
            rep["artifacts"]["fonts"] = fonts

    # ---- hook overlay
    hook_treatment = next(
        (t for t in built.treatments or [] if t.get("name") == "hook_overlay"), None
    )
    if hook_treatment is not None:
        text = (brief.get("hook_text") or "").strip()
        if not text:
            words = getattr(state.get("transcript"), "words", []) or []
            text = " ".join(
                str(w.get("word") or "").strip()
                for w in words[:8]
                if str(w.get("word") or "").strip()
            ).strip()
        if not text:
            hook_treatment["enabled"] = False
            hook_treatment.setdefault("reason", {})["explain"] = (
                "nessun testo disponibile: overlay non inciso"
            )
            rep.setdefault("skipped", []).append(
                {
                    "treatment": "hook_overlay",
                    "reason": "nessun testo disponibile: overlay non inciso",
                }
            )
            _log("[run] hook overlay non inciso: nessun testo disponibile")
        elif _enabled(built, "hook_overlay"):
            _log("[run] hook overlay")
            params = _params(built, "hook_overlay")
            hook_path = str(
                subs.write_hook_ass(
                    work / "subs" / f"{stem}.hook.ass",
                    text,
                    out_w=int(built.output.get("width") or 1080),
                    out_h=int(built.output.get("height") or 1920),
                    start_s=float(params.get("at") or 0.0),
                    duration_s=float(params.get("duration") or 3.0),
                )
            )
            rep["artifacts"]["hook"] = hook_path

    # ---- crop plan
    crop: Optional[Dict[str, Any]] = None
    if _enabled(built, "autoframe"):
        _log("[run] auto-framing")
        faces_data = rep["analysis"].get("faces") or {}
        known = set(FaceTracks.__dataclass_fields__)
        tracks = FaceTracks(**{k: v for k, v in faces_data.items() if k in known})
        params = _params(built, "autoframe")
        crop_plan = autoframe.build_crop_plan(
            tracks,
            int(media.get("width") or 0),
            int(media.get("height") or 0),
            aspect=str(built.output.get("aspect") or "9:16"),
            eye_line=float(params.get("eye_line") or 0.38),
            scene_cuts=(rep["analysis"].get("video") or {}).get("scene_cuts"),
            precision="full",
        )
        crop = crop_plan.to_dict()
        built.render["crop"] = crop

    # ---- preview on the proxy, then full-res delivery
    gates.pre_render(built, media, findings=rep.get("findings"))
    report_io.write(built.to_dict(), work / "plan.json")
    winpath.heartbeat(work, "render_preview")
    _log("[run] render anteprima (proxy)")
    preview = render.render(
        built,
        media,
        precision="proxy",
        workdir=work / "stage",
        subs_path=subs_path,
        crop_plan=crop,
        visual=visual,
        hook_path=hook_path,
    )
    _log(f"[run] anteprima: {preview['path']} ({preview['frames']} frame)")

    winpath.heartbeat(work, "render_full")
    _log("[run] render finale (full-res)")
    final_plan = render.swap_proxy_to_full(built, media)
    final = render.render(
        final_plan,
        media,
        precision="full",
        workdir=work / "stage",
        subs_path=subs_path,
        crop_plan=crop,
        visual=visual,
        hook_path=hook_path,
    )

    winpath.heartbeat(work, "qc")
    _log("[run] controllo qualita")
    report_io.write(
        {
            "target": final.get("target"),
            "loudness": final.get("loudness"),
            "stages": final.get("stages", []),
        },
        work / "render.json",
    )
    result = qc.check_render(
        final_plan,
        media,
        final["path"],
        subs_path=subs_path,
        source_fp=state["fingerprint"],
        executed_target=final.get("target"),
    )
    report_io.write(result.to_dict(), work / "qc.json")
    planned_total = (
        sum(float(c["out"]) - float(c["in"]) for c in (final_plan.cuts or [])) or None
    )
    gates.post_render(
        final_plan, media, final["path"], workdir=work, planned_total=planned_total
    )

    rep["plan"] = final_plan.to_dict()
    rep["qc"] = result.to_dict()
    rep["artifacts"]["render"] = final["path"]
    rep["artifacts"]["preview"] = preview["path"]
    rep["artifacts"]["qc"] = str(work / "qc.json")
    from . import deliverables

    out_dir = work / "out"
    srt_source = Path(rep["artifacts"]["srt"]) if rep["artifacts"].get("srt") else None
    produced = deliverables.produce(
        final_plan,
        Path(final["path"]),
        out_dir,
        srt_source=srt_source,
        workdir=work,
    )
    report_io.write({"deliverables": produced}, out_dir / "deliverables.json")
    rep["deliverables"] = produced
    report_io.write(rep, work / "report.json")

    if not args.keep_proxy:
        Path(state["proxy"]["path"]).unlink(missing_ok=True)
        rep["artifacts"]["proxy"] = None
        report_io.write(rep, work / "report.json")

    summary = {
        "output": final["path"],
        "preview": preview["path"],
        "frames": final["frames"],
        "expected_frames": final["expected_frames"],
        "loudness": final["loudness"],
        "qc_pass": result.pass_,
        "qc_failures": result.failures,
        "subs": subs_path,
        "report": str(work / "report.json"),
        "plan": str(work / "plan.json"),
        "deliverables": rep["deliverables"],
    }
    if args.json:
        _out(summary)
    else:
        print(f"Output: {final['path']}")
        print(f"Frame: {final['frames']} (attesi {final['expected_frames']})")
        print(f"QC: {'PASS' if result.pass_ else 'FAIL ' + ', '.join(result.failures)}")
        print(f"Report: {work / 'report.json'}")
    return EXIT_OK if result.pass_ else EXIT_QC_FAIL


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def cmd_edl(args: argparse.Namespace) -> int:
    from . import gates, grammar, ingest, render
    from . import probe as probe_mod
    from .edl_plan import EDLPlan, EDLValidationError

    try:
        plan = EDLPlan.from_dict(_load_json(Path(args.plan)))
    except EDLValidationError as exc:
        _out({"ok": False, "errors": exc.errors})
        return 2

    violations = grammar.validate_cinematic_rules(plan)
    exemptions = grammar.camera_exemptions(plan)
    if violations:
        _out(
            {
                "ok": False,
                "errors": [item.details for item in violations],
                "grammar": [item.to_dict() for item in violations],
                "camera_exemptions": exemptions,
            }
        )
        return 2

    if args.edl_cmd == "validate":
        _out(
            {
                "ok": True,
                "contract_version": plan.contract_version,
                "sources": list(plan.sources),
                "video_tracks": len(plan.video_tracks),
                "audio_tracks": len(plan.audio_tracks),
                "duration_sec": plan.duration_sec,
                "resolution": list(plan.resolution),
                "camera_exemptions": exemptions,
            }
        )
        return EXIT_OK

    provided: Dict[str, str] = {}
    for item in args.source or []:
        source_id, separator, path = str(item).partition("=")
        if not separator or not source_id or not path:
            _out({"ok": False, "error": f"--source must be ID=PATH, got {item!r}"})
            return 2
        provided[source_id] = path
    declared = set(plan.sources)
    missing = sorted(declared - set(provided))
    if missing:
        _out({"ok": False, "error": "missing sources: " + ", ".join(missing)})
        return 2
    unknown = sorted(set(provided) - declared)
    if unknown:
        _out({"ok": False, "error": "unknown sources: " + ", ".join(unknown)})
        return 2

    work = Path(args.workdir) if args.workdir else Path.cwd() / "edl_work"
    work.mkdir(parents=True, exist_ok=True)
    staged = ingest.ingest_sources(provided, work / "stage_00")
    staged_paths = {
        source_id: result.staged_path for source_id, result in staged.items()
    }
    media_info = {
        source_id: probe_mod.probe_asset(path).to_dict()
        for source_id, path in staged_paths.items()
    }
    gates.validate_gate1_durations(plan, media_info)
    audio_source_ids = {
        clip.source_id for track in plan.audio_tracks for clip in track.clips
    }
    has_audio = any(
        media_info.get(source_id, {}).get("has_audio") for source_id in audio_source_ids
    )
    if plan.audio_tracks and not has_audio:
        _out(
            {
                "ok": False,
                "error": "plan declares audio but a staged source has no audio",
            }
        )
        return 2

    output = Path(args.out)
    render.render_edl(
        plan,
        staged_paths,
        output,
        workdir=work,
        crf=args.crf,
        preset=args.preset,
    )
    _out(
        {
            "ok": True,
            "output": str(output),
            "sources": staged_paths,
            "stage": {
                source_id: result.to_dict() for source_id, result in staged.items()
            },
        }
    )
    return EXIT_OK


def cmd_auto(args: argparse.Namespace) -> int:
    """Prompt + folder -> triage -> EDL -> render -> QC -> deliverables -> report."""
    from . import auto
    from .grammar import GrammarError

    try:
        report = auto.run(args)
    except GrammarError as exc:
        _out(
            {
                "ok": False,
                "errors": [item.details for item in exc.violations],
                "grammar": [item.to_dict() for item in exc.violations],
                "camera_exemptions": getattr(exc, "camera_exemptions", []),
            }
        )
        return EXIT_USAGE

    if getattr(args, "json", False):
        _out(report)
    else:
        _log(
            f"[auto] ok: {report['master']} "
            f"({report['timeline_sec']:.3f}s, {report['assets']['selected']} selezionati)"
        )
    return EXIT_OK


def cmd_mcp(args: argparse.Namespace) -> int:
    """Run the stdio MCP server (debug convenience; no flags, the transport is the interface)."""
    from . import mcp_server

    return mcp_server.main([])


def _apply_settings(args: argparse.Namespace) -> None:
    """Load the cascade before any analysis: defaults -> config -> --settings -> --override."""
    from . import settings

    settings.apply(
        settings.build(
            settings_file=getattr(args, "settings", None),
            overrides=getattr(args, "override", None) or [],
        )
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="vx.py",
        description="video-editor skill toolkit — senior editor pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version=f"vx {VERSION}")
    sub = ap.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "--settings",
            default=None,
            metavar="FILE",
            help="profilo JSON per-job (sovrascrive il config)",
        )
        p.add_argument(
            "--override",
            action="append",
            default=[],
            metavar="CHIAVE=VALORE",
            help="override puntuale, es. thresholds.jitter_shaky=0.5 (ripetibile)",
        )
        return p

    p = add("doctor", "scansione ambiente e verdetto (Fase 1)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = add("probe", "sonda tecnica del file (read-only)")
    p.add_argument("--input", required=True)
    p.add_argument("--report", default=None)
    p.add_argument("--count-frames", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_probe)

    p = add("proxy", "proxy a bassa risoluzione + anteprime (Fase 1)")
    p.add_argument("--input", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--workdir", default=None)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--thumbs", type=int, default=8)
    p.add_argument("--no-previews", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_proxy)

    p = add("analyze", "pre-analisi silenziosa video+audio+volti (Fase 4)")
    p.add_argument("--input", required=True)
    p.add_argument("--report", default=None)
    p.add_argument("--proxy", default=None)
    p.add_argument("--sample-fps", type=float, default=SAMPLE_FPS_VIDEO)
    p.add_argument(
        "--speech",
        action="store_true",
        help="accetta per compatibilita: il parlato e attivo di default",
    )
    p.add_argument("--model", default="tiny")
    p.add_argument("--lang", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_analyze)

    p = add("subs", "trascrizione + sottotitoli dinamici ASS/SRT")
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--style", default="karaoke", choices=["karaoke", "clean", "none"])
    p.add_argument("--lang", default=None)
    p.add_argument("--model", default="small")
    p.add_argument("--aspect", default="9:16")
    p.add_argument("--platform", default="tiktok")
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument(
        "--overlay",
        action="append",
        default=None,
        metavar="PNG",
        help="asset grafico (con alpha) che occupa il quadrante inferiore; "
        "ripetibile. Attiva la misura delle safe area e il cancello 4",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_subs)

    p = add("triage", "briefing interattivo (Fase 5) — esce 3 se serve l'utente")
    p.add_argument("--report", required=True)
    p.add_argument("--brief", default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--markdown", action="store_true")
    p.set_defaults(func=cmd_triage)

    p = add("plan", "matrice decisionale -> EditPlan (Fase 5/6)")
    p.add_argument("--report", required=True)
    p.add_argument("--brief", default=None)
    p.add_argument("--answers", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = add("render", "render da piano (proxy o full)")
    p.add_argument("--plan", required=True)
    p.add_argument("--input", default=None)
    p.add_argument("--precision", default="full", choices=["proxy", "full"])
    p.add_argument("--workdir", default=None)
    p.add_argument("--subs", default=None)
    p.add_argument(
        "--report",
        default=None,
        help="report.json con findings/source: default il sibling del piano",
    )
    p.add_argument(
        "--visual-brief",
        default=None,
        help="brief vx.visual/1.0: inserisce le catene grade+animazione nel grafo",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_render)

    p = add("qc", "controllo qualita sul file renderizzato")
    p.add_argument("--plan", required=True)
    p.add_argument("--render", required=True)
    p.add_argument("--input", default=None)
    p.add_argument("--subs", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_qc)

    p = add("ingest", "protocollo: probe+proxy+pre-analisi+triage (Fase 4/5)")
    p.add_argument("--input", required=True)
    p.add_argument("--brief", default=None)
    p.add_argument("--workdir", default=None)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--thumbs", type=int, default=8)
    p.add_argument("--sample-fps", type=float, default=SAMPLE_FPS_VIDEO)
    p.add_argument(
        "--chunk-sec",
        type=float,
        default=None,
        help="finestra di analisi a chunk in secondi (default: settings)",
    )
    p.add_argument(
        "--autonomous",
        action="store_true",
        help="non chiede mai: registra i default nel report e prosegue",
    )
    p.add_argument(
        "--speech",
        action="store_true",
        help="accetta per compatibilita: il parlato e attivo di default",
    )
    p.add_argument("--model", default="tiny")
    p.add_argument("--lang", default=None)
    p.add_argument(
        "--no-speech",
        action="store_true",
        help="salta la trascrizione durante l'ingest",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ingest)

    p = add("run", "pipeline completa: ingest->plan->subs->render->qc (Fase 7)")
    p.add_argument("--input", required=True)
    p.add_argument("--brief", default=None)
    p.add_argument("--payload", default=None)
    p.add_argument("--prompt", default=None)
    p.add_argument("--answers", default=None)
    p.add_argument("--workdir", default=None)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--thumbs", type=int, default=8)
    p.add_argument("--sample-fps", type=float, default=SAMPLE_FPS_VIDEO)
    p.add_argument(
        "--chunk-sec",
        type=float,
        default=None,
        help="finestra di analisi a chunk in secondi (default: settings)",
    )
    p.add_argument(
        "--autonomous",
        action="store_true",
        help="non chiede mai: usa i default registrati e prosegue",
    )
    p.add_argument(
        "--speech",
        action="store_true",
        help="accetta per compatibilita: il parlato e attivo di default",
    )
    p.add_argument("--model", default="small")
    p.add_argument("--lang", default=None)
    p.add_argument(
        "--no-speech",
        action="store_true",
        help="salta la trascrizione (nessun sottotitolo)",
    )
    p.add_argument("--keep-proxy", action="store_true")
    p.add_argument(
        "--visual-brief",
        default=None,
        help="brief vx.visual/1.0: inserisce le catene grade+animazione nel grafo",
    )
    p.add_argument(
        "--overlay",
        action="append",
        default=None,
        metavar="PNG",
        help="asset grafico (con alpha) che occupa il quadrante inferiore; "
        "ripetibile. Attiva la misura delle safe area e il cancello 4",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_run)

    p = add("deliverables", "matrice di consegna del piano")
    p.add_argument("--plan", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_deliverables)

    p = add("visual", "brief vx.visual/1.0 dalle misure del clip (W2.5)")
    p.add_argument("--source", required=True, help="sorgente del clip")
    p.add_argument("--measurements", required=True, help="JSON con le misure del clip")
    p.add_argument("--delivery", default=None, help="delivery di riferimento (audit)")
    p.add_argument("--out", default=None, help="percorso del brief JSON")
    p.add_argument("--project", default=None, help="project_id del brief")
    p.set_defaults(func=cmd_visual)

    p = add(
        "auto",
        "pipeline completa da prompt+cartella: triage->EDL->render->QC->consegna",
    )
    p.add_argument("--prompt", required=True, help="richiesta in linguaggio naturale")
    p.add_argument(
        "--assets", required=True, help="cartella degli asset (sola lettura)"
    )
    p.add_argument("--out", required=True, help="percorso del master")
    p.add_argument(
        "--workdir",
        default=None,
        help="default: <out parent>/<out stem>-auto",
    )
    p.add_argument(
        "--fps",
        type=float,
        default=None,
        help="frame rate della timeline; default: dedotto dal primo video selezionato",
    )
    p.add_argument("--manifest", default=None, help="manifest JSON con ruoli e ancore")
    p.add_argument(
        "--autonomous",
        action="store_true",
        help="forza la modalita autonoma: nessuna domanda, i default sono registrati",
    )
    p.add_argument("--crf", type=int, default=None)
    p.add_argument("--preset", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_auto)

    p = add("edl", "EDL v2.0 multi-sorgente: validate o render")
    edl_sub = p.add_subparsers(dest="edl_cmd", required=True)
    v = edl_sub.add_parser("validate", help="valida un EDL v2.0")
    v.add_argument("--plan", required=True)
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_edl)
    r = edl_sub.add_parser("render", help="staging di N sorgenti, Gate 1 e render")
    r.add_argument("--plan", required=True)
    r.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="ID=PATH",
        help="sorgente dichiarata dall'EDL, ripetibile",
    )
    r.add_argument("--workdir", default=None)
    r.add_argument("--out", required=True)
    r.add_argument("--crf", type=int, default=17)
    r.add_argument("--preset", default="fast")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_edl)

    p = sub.add_parser("mcp", help="server MCP su stdio (JSON-RPC per agenti)")
    p.set_defaults(func=cmd_mcp)

    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - older/captured streams
            pass

    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        _apply_settings(args)
        return int(args.func(args))
    except TriageRequired as exc:
        _out(exc.request)
        return EXIT_NEEDS_INPUT
    except MissingDependency as exc:
        _log(f"[missing] {exc}")
        return EXIT_MISSING_DEP
    except QCFailure as exc:
        _out(exc.result)
        return int(getattr(exc, "exit_code", EXIT_QC_FAIL))
    except RenderFailure as exc:
        _log(f"[render] {exc}")
        return EXIT_RENDER_FAIL
    except VxError as exc:
        _log(f"[error] {exc}")
        return EXIT_USAGE
    except KeyboardInterrupt:
        _log("[aborted]")
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 - no raw traceback may escape the CLI
        _log(f"[internal] {type(exc).__name__}: {exc}")
        _log(traceback.format_exc())
        return EXIT_RENDER_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
