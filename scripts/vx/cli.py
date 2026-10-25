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
    ass = subs.write_ass(
        cues,
        out_dir / f"{stem}.ass",
        aspect=aspect,
        platform=args.platform,
        style=args.style,
        out_w=args.width,
        out_h=args.height,
    )
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
    work = Path(args.workdir) if args.workdir else winpath.work_root() / "manual"
    _log(f"[render] precision={args.precision}")
    result = render.render(
        built,
        media,
        precision=args.precision,
        workdir=work,
        subs_path=args.subs or None,
        crop_plan=(built.render or {}).get("crop"),
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
        ass = subs.write_ass(
            cues,
            work / "subs" / f"{stem}.ass",
            aspect=str(built.output.get("aspect") or "9:16"),
            platform=str(built.output.get("platform") or "tiktok"),
            style=str(params.get("style") or "karaoke"),
            out_w=int(built.output.get("width") or 1080),
            out_h=int(built.output.get("height") or 1920),
        )
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
    _log("[run] render anteprima (proxy)")
    preview = render.render(
        built,
        media,
        precision="proxy",
        workdir=work / "stage",
        subs_path=subs_path,
        crop_plan=crop,
    )
    _log(f"[run] anteprima: {preview['path']} ({preview['frames']} frame)")

    _log("[run] render finale (full-res)")
    final_plan = render.swap_proxy_to_full(built, media)
    final = render.render(
        final_plan,
        media,
        precision="full",
        workdir=work / "stage",
        subs_path=subs_path,
        crop_plan=crop,
    )

    _log("[run] controllo qualita")
    result = qc.check_render(
        final_plan,
        media,
        final["path"],
        subs_path=subs_path,
        source_fp=state["fingerprint"],
    )
    report_io.write(result.to_dict(), work / "qc.json")

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
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_run)

    p = add("deliverables", "matrice di consegna del piano")
    p.add_argument("--plan", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_deliverables)

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
        return int(args.func(args))
    except TriageRequired as exc:
        _out(exc.request)
        return EXIT_NEEDS_INPUT
    except MissingDependency as exc:
        _log(f"[missing] {exc}")
        return EXIT_MISSING_DEP
    except QCFailure as exc:
        _out(exc.result)
        return EXIT_QC_FAIL
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
