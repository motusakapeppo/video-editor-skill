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
import sys
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
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
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


def build_visual_brief(
    source: str | Path,
    measurements: Dict[str, Any],
    *,
    delivery: str | Path | None = None,
    project: str | None = None,
) -> Dict[str, Any]:
    """Build a ``vx.visual/1.0`` brief from the measured clip.

    The six choices come from the decision modules (``palette``, ``gradeplan``,
    ``animplan``, ``screen`` at one timestamp 10% into the clip) plus the
    cut-density and typography rules. Two normalisations happen here, by
    contract:

    * P3 — ``analyze.measure_color`` returns the cast in pixel units; any cast
      whose absolute value exceeds 1.0 is divided by 255 before it reaches
      ``gradeplan``, and ``cast_normalised`` is recorded on the brief.
    * P4 — ``gradeplan``/``animplan`` return ``inaction`` as a sentence; the
      brief stores the boolean (``not applied``) and the sentence goes to
      ``inaction_reason``.

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
    if duration is None or not width or not height:
        info = probe_mod.probe(str(src))
        duration = float(info.duration) if duration is None else float(duration)
        width = int(info.width) if not width else int(width)
        height = int(info.height) if not height else int(height)

    pal = palette_mod.decide(m)
    grade = gradeplan.decide(m, pal)
    ratio = m.get("transient_ratio")
    anim = animplan.decide(
        None if ratio is None else float(ratio), m.get("onsets_per_s")
    )
    comp = screen_mod.decide(str(src), float(duration) * 0.10, int(width), int(height))[
        "composition"
    ]
    cut = _cut_density_choice(m)
    typo = _typography_choice(m)

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
    fac = faces.detect_faces_tracked(args.proxy or str(src), sample_fps=SAMPLE_FPS_FACE)

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


def _transcribe(
    src: str, model: str = "tiny", lang: Optional[str] = None
) -> tuple[Dict[str, Any], Any]:
    """Speech stats for the report plus the transcript object, so `run` never transcribes twice.

    A failed transcription is data too, never a fatal error.
    """
    try:
        from . import subs

        tr = subs.transcribe(src, model=model, language=lang)
        stats = subs.transcript_stats(tr)
        block = {
            "language": tr.language or "",
            "word_count": int(stats.get("word_count") or 0),
            "wpm": float(stats.get("wpm") or 0.0),
            "fillers": int(stats.get("fillers") or 0),
            "model": tr.model or model,
            "skipped_reason": tr.skipped_reason or "",
        }
        return block, tr
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


def cmd_render(args: argparse.Namespace) -> int:
    from . import probe as probe_mod
    from . import render

    built = _load_plan(args.plan)
    source = args.input or (built.brief or {}).get("source")
    if not source:
        raise VxError("serve --input: il piano non contiene la sorgente")
    media = probe_mod.probe(source).to_dict()
    visual = (
        _visual_from_brief(_load_json(args.visual_brief))
        if getattr(args, "visual_brief", None)
        else None
    )
    work = Path(args.workdir) if args.workdir else winpath.work_root() / "manual"
    _log(f"[render] precision={args.precision}")
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
    result = qc.check_render(built, media, args.render, subs_path=args.subs or None)
    if args.out:
        Path(args.out).write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    _out(result.to_dict())
    return EXIT_OK if result.pass_ else EXIT_QC_FAIL


# --------------------------------------------------------------------------- #
# commands — the protocol
# --------------------------------------------------------------------------- #


def _ingest(src: Path, args: argparse.Namespace) -> Dict[str, Any]:
    """Fase 1 + Fase 4: proxy, pre-analysis, findings. Never mutates the source."""
    from . import analyze, audio, faces
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

    _log("[1/6] proxy a bassa risoluzione")
    proxy_info = proxy_mod.make_proxy(
        str(src), work / "proxy" / "proxy.mp4", width=args.width
    )
    proxy_path = proxy_info["path"]

    _log("[2/6] pre-analisi video (proxy)")
    video = analyze.analyze_video(
        str(src), sample_fps=args.sample_fps, proxy_path=proxy_path
    )
    _log("[3/6] pre-analisi audio")
    aud = audio.analyze_audio(str(src))
    _log("[4/7] volti e speaker")
    fac = faces.detect_faces_tracked(proxy_path, sample_fps=SAMPLE_FPS_FACE)
    _log("[5/7] parlato")
    speech, transcript = ({"skipped_reason": "disabled"}, None)
    if not getattr(args, "no_speech", False) and probe_data.get("has_audio"):
        speech, transcript = _transcribe(str(src), args.model, args.lang)
    _log("[6/7] anteprime")
    thumbs = proxy_mod.thumbs(str(src), work / "analysis", count=args.thumbs)
    sheet = proxy_mod.contact_sheet(thumbs, work / "analysis" / "contact.jpg")

    rep = report_io.new_report(probe_data, tool=_tool_block())
    report_path = work / "report.json"
    rep["source"]["sha256"] = fingerprint["sha256"]
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
    report_io.merge(
        rep,
        "analysis",
        {
            "video": video.to_dict(),
            "audio": aud.to_dict(),
            "faces": fac.to_dict(),
            "speech": speech,
        },
    )
    report_io.merge(rep, "findings", plan_mod.detect_findings(rep))

    brief = _load_brief(args.brief)
    _log("[7/7] triage")
    request = plan_mod.needs_triage(brief, rep)
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
        "probe": probe_data,
        "thumbs": thumbs,
        "contact_sheet": sheet,
        "transcript": transcript,
    }


def cmd_ingest(args: argparse.Namespace) -> int:
    from . import triage

    src = Path(args.input)
    state = _ingest(src, args)

    request = state["triage"]
    if args.json:
        _out(
            {
                "workdir": str(state["workdir"]),
                "report": str(state["report_path"]),
                "proxy": state["proxy"],
                "triage": request,
                "verdict": "needs_user_input" if request else "ready_for_plan",
                "resume_command": (request or {}).get("resume_command"),
            }
        )
    else:
        if request is not None:
            print(triage.to_markdown(request))
            print(f"\nReport: {state['report_path']}")
            print(f"Anteprime: {state['contact_sheet']}")
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

    state = _ingest(src, args)

    rep = state["report"]
    work: Path = state["workdir"]
    media = state["probe"]
    brief = _load_brief(args.brief)
    if answers is None:
        request = state["triage"] or plan_mod.needs_triage(brief, rep)
        if request is not None:
            if args.json:
                _out(request)
            else:
                print(triage.to_markdown(request))
            return EXIT_NEEDS_INPUT
    else:
        brief["_answers_path"] = str(Path(args.answers).resolve())

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
        speech, transcript = _transcribe(str(src), args.model, args.lang)
        report_io.merge(rep, "analysis", {"speech": speech})
        state["transcript"] = transcript

    _log("[run] piano")
    built = plan_mod.build_plan(rep, brief, answers or {})
    errors = plan_mod.validate(built, rep)
    if errors:
        for err in errors:
            _log(f"[plan] {err}")
        return EXIT_USAGE
    # ---- subtitles
    subs_path: Optional[str] = None
    if _enabled(built, "subtitles"):
        _log("[run] sottotitoli dinamici")
        params = _params(built, "subtitles")
        transcript = state.get("transcript") or subs.transcribe(
            str(src), model=args.model, language=args.lang
        )
        cues = subs.build_cues(
            transcript,
            style=str(params.get("style") or "karaoke"),
            aspect=str(built.output.get("aspect") or "9:16"),
            max_chars=params.get("max_chars"),
            max_lines=params.get("max_lines"),
            max_cps=params.get("max_cps"),
        )
        stem = winpath.safe_stem(src.name)
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
        subs_path = ass
        rep["artifacts"]["ass"] = ass
        rep["artifacts"]["srt"] = srt
        rep["artifacts"]["fonts"] = fonts
        if not fonts.get("ok", True):
            _log(f"[warn] font sostituito da libass: {fonts.get('fonts_used')}")

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

    report_io.write(built.to_dict(), work / "plan.json")

    # ---- preview on the proxy, then full-res delivery
    gates.pre_render(built, media)
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
    )

    winpath.heartbeat(work, "qc")
    _log("[run] controllo qualita")
    result = qc.check_render(
        final_plan,
        media,
        final["path"],
        subs_path=subs_path,
        source_fp=state["fingerprint"],
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
    rep["deliverables"] = plan_mod.deliverables(final_plan, media)
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
    p.add_argument("--answers", default=None)
    p.add_argument("--workdir", default=None)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--thumbs", type=int, default=8)
    p.add_argument("--sample-fps", type=float, default=SAMPLE_FPS_VIDEO)
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


if __name__ == "__main__":
    raise SystemExit(main())
