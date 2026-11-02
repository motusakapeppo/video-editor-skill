"""vx.auto — one command from a prompt and a folder to a delivered master.

Fixed order, no stage skipped:

    discover -> triage -> decision -> staging -> EDL -> grammar -> Gate 1
    -> render (master loudness) -> master QC -> deliverables -> report

Exit codes are the protocol's, not this module's invention: a usage error is a
``VxError`` (2), a failed Gate 1 or master QC is a ``QCFailure`` (5), a failed
render is a ``RenderFailure`` (6). Nothing is swallowed here; the exception
travels to ``cli.main`` unchanged. The only exception translated is
``GrammarError``, which has no exit code of its own and is reported by
``cmd_auto`` as a usage error with the violations attached.

The source folder is read-only. Every staged file, render and report lives
under ``--workdir``; the master is the only file written outside it, at
``--out``. The sha256 of every input is taken before staging and re-checked at
the end: a changed source is a hard QC failure, never a warning.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from . import (
    asset_triage,
    contract,
    decide,
    deliverables,
    edl_plan,
    estimate,
    gates,
    grammar,
    ingest,
    lock,
    probe,
    profile_master,
    profiles,
    qc,
    render,
    settings,
)

# The delivery spec the renderer is asked to hit and the QC gate re-measures.
TARGET_LUFS = -14.0
TARGET_TP = -1.0

#: Il cancello dei font: la proposta che ``fontproposal`` scrive, la scelta che
#: la vista deposita e quanto si aspetta una persona prima di decidere da soli.
FONT_PROPOSAL_NAME = "font_proposal.json"
FONT_CHOICE_NAME = "font_choice.json"
FONT_WAIT_DEFAULT = 180.0


def loudness_target(document: Any = None) -> tuple[float, float]:
    """(LUFS, dBTP) declared by ``document.profile_ref``, else the -14/-1 default.

    The document is the payload the pipeline was built from (a mapping) or a
    ``Timeline3``-like object. An unknown id is not swallowed: ``profiles.resolve``
    refuses it with ``PROFILE_UNKNOWN``, the same code the document validator
    uses, so a typo can never silently fall back to the social target.
    """
    ref = None
    if isinstance(document, Mapping):
        ref = document.get("profile_ref")
    elif document is not None:
        ref = getattr(document, "profile_ref", None)
    if ref is None or not str(ref).strip():
        return float(TARGET_LUFS), float(TARGET_TP)
    return profile_master.target_for(str(ref))


#: Quando c'e' un narratore, le righe tecniche tacciono: una voce sola.
_NARRATING = False


def _log(msg: str) -> None:
    if _NARRATING and msg.startswith("[auto]"):
        return
    print(msg, file=sys.stderr, flush=True)


def _emit(log: Optional[Any], stage: str, status: str, detail: str = "") -> None:
    """Write one boundary to the event log; a broken log never breaks a render."""
    if log is None:
        return
    try:
        log.emit(stage, f"auto.{stage}", status, detail=detail)
    except Exception:  # noqa: BLE001
        pass


#: Sotto questa differenza la durata consegnata e' quella chiesta.
DURATION_SHORTFALL_TOLERANCE = 0.5


def duration_shortfall(requested: Any, delivered: Any) -> Optional[Dict[str, Any]]:
    """The gap between what was asked and what the timeline can deliver.

    Una richiesta di 60 s su materiale che ne regge 21 non e' un successo: e' una
    consegna piu' corta del chiesto, e va dichiarata con i due numeri.
    """
    try:
        want = float(requested)
        got = float(delivered)
    except (TypeError, ValueError):
        return None
    if want <= 0 or got <= 0:
        return None
    gap = want - got
    if gap <= DURATION_SHORTFALL_TOLERANCE:
        return None
    return {
        "requested_sec": round(want, 3),
        "delivered_sec": round(got, 3),
        "shortfall_sec": round(gap, 3),
    }


def _notify(
    progress: Optional[Callable[[float, str], None]],
    percent: float,
    stage: str,
    *,
    log: Optional[Any] = None,
    status: str = "done",
    detail: str = "",
) -> None:
    """Call the observer, swallowing anything it raises: a broken observer must
    never break a render. The same boundary is written to the event log first, so
    a crash during a stage still leaves that stage's ``start`` on disk."""
    _emit(log, stage, status, detail)
    if progress is None:
        return
    try:
        progress(percent, stage)
    except Exception:  # noqa: BLE001
        pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _staged_path(result: Any) -> Path:
    """``IngestResult`` names the staged file ``staged_path``; keep ``path`` as a fallback."""
    value = getattr(result, "staged_path", None) or getattr(result, "path", None)
    if not value:
        raise contract.VxError("staging non ha restituito un percorso")
    return Path(value)


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def fill_upscale(src_size: Sequence[int], target_size: Sequence[int]) -> float:
    """How far the source has to grow to cover the canvas, declared in the report.

    The cover scale needs ``max(width ratio, height ratio)``; a source that
    already covers the target needs no upscale at all, so the value is clamped
    to exactly ``1.0`` instead of being reported as a fraction below it. The
    arithmetic is pure Python: no probe, no subprocess, no invented number.
    """
    src_w, src_h = (int(value) for value in src_size)
    target_w, target_h = (int(value) for value in target_size)
    if src_w <= 0 or src_h <= 0:
        raise contract.VxError("fill: geometria sorgente non positiva")
    factor = max(target_w / float(src_w), target_h / float(src_h))
    if factor <= 1.0:
        return 1.0
    return round(factor, 3)


def _fill_declaration(
    plan: Any, media_info_map: Mapping[str, Mapping[str, Any]], mode: str
) -> List[Dict[str, Any]]:
    """One entry per layer-0 (a_roll) clip: policy, geometry, declared upscale."""
    target = [int(value) for value in plan.resolution]
    entries: List[Dict[str, Any]] = []
    for track in getattr(plan, "video_tracks", ()):
        for clip in getattr(track, "clips", ()):
            if int(getattr(clip, "layer", 0)) != 0:
                continue
            media = media_info_map.get(clip.source_id)
            if media is None:
                raise contract.VxError(
                    f"fill: nessun media_info per {clip.source_id!r}"
                )
            src = [int(media.get("width") or 0), int(media.get("height") or 0)]
            entries.append(
                {
                    "clip_id": clip.clip_id,
                    "mode": mode,
                    "src_size": src,
                    "target_size": list(target),
                    "upscale": fill_upscale(src, target),
                }
            )
    return entries


def _fill_mode(value: Any) -> str:
    """A usage error (exit 2), not a render failure: the flag is wrong, not ffmpeg."""
    mode = str(value or "fit").strip().lower()
    if mode not in render.FILL_POLICIES:
        raise contract.VxError(
            f"--fill {mode!r} non in {'/'.join(render.FILL_POLICIES)}"
        )
    return mode


def _resolve_workdir(args: Any) -> Path:
    explicit = getattr(args, "workdir", None)
    if explicit:
        return Path(explicit)
    out = Path(args.out)
    return out.parent / f"{out.stem}-auto"


def progress_path(args: Any) -> Path:
    """Il file ``-progress`` di ffmpeg per questo lavoro.

    Sta accanto al grafo del render (``<workdir>/render/``) perche' e' li' che
    ``render_edl`` scrive i suoi passaggi; ``vx serve`` lo legge per dire a che
    punto e' il render invece di stimarlo.
    """
    return _resolve_workdir(args) / "render" / "ffmpeg-progress.txt"


def _discover(args: Any) -> List[asset_triage.AssetInfo]:
    try:
        assets = asset_triage.discover_assets(
            args.assets, manifest=getattr(args, "manifest", None)
        )
    except ValueError as exc:
        raise contract.VxError(f"--assets: {exc}") from exc
    if not assets:
        raise contract.VxError(f"--assets: nessun asset riconosciuto in {args.assets}")
    return assets


def _fps(
    args: Any,
    selected: List[Mapping[str, Any]],
    assets_by_id: Mapping[str, asset_triage.AssetInfo],
) -> float:
    """The frame rate comes from the flag, then from a selected video, never a constant."""
    explicit = getattr(args, "fps", None)
    if explicit is not None:
        value = float(explicit)
        if value <= 0:
            raise contract.VxError(f"--fps deve essere > 0, ricevuto {explicit!r}")
        return value

    for entry in selected:
        if entry.get("type") != "video":
            continue
        asset = assets_by_id.get(entry.get("asset_id"))
        if asset is None:
            continue
        info = probe.probe_asset(asset.path)
        if float(info.fps or 0.0) > 0:
            return float(info.fps)

    if any(entry.get("type") == "image" for entry in selected):
        raise contract.VxError(
            "serve --fps: il pool contiene immagini e nessun video da cui dedurre "
            "il frame rate"
        )
    raise contract.VxError(
        "serve --fps: nessun video selezionato da cui dedurre il frame rate"
    )


def _timeline_est(
    selected: List[Mapping[str, Any]],
    assets_by_id: Mapping[str, asset_triage.AssetInfo],
    rejected: Sequence[Mapping[str, Any]] = (),
) -> float:
    total = 0.0
    for entry in selected:
        if entry.get("role") != "a_roll":
            continue
        asset_id = entry.get("asset_id")
        asset = assets_by_id.get(asset_id)
        if asset is None:
            raise contract.VxError(f"asset {asset_id!r} assente dal pool")
        try:
            target = float(entry.get("target_duration_sec"))
        except (TypeError, ValueError) as exc:
            raise contract.VxError(
                f"asset {asset_id!r}: target_duration_sec non numerico"
            ) from exc
        total += min(target, float(asset.duration_sec))
    if total <= 0:
        discarded = ", ".join(
            f"{entry.get('asset_id')}={entry.get('rejection_reason')}"
            for entry in rejected
        )
        detail = f"; scarti: {discarded}" if discarded else ""
        raise contract.VxError(
            "timeline stimata non positiva: nessun a_roll utilizzabile" + detail
        )
    return total


def _specs(
    selected: List[Mapping[str, Any]],
    assets_by_id: Mapping[str, asset_triage.AssetInfo],
    fps: float,
    timeline_est: float,
) -> Dict[str, Dict[str, Any]]:
    """Only images need a spec: they have no duration of their own."""
    specs: Dict[str, Dict[str, Any]] = {}
    for entry in selected:
        asset_id = entry.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise contract.VxError("asset selezionato senza asset_id")
        if entry.get("type") != "image":
            specs[asset_id] = {}
            continue
        asset = assets_by_id.get(asset_id)
        if asset is None:
            raise contract.VxError(f"immagine {asset_id!r} assente dal pool")
        if int(asset.width) <= 0 or int(asset.height) <= 0:
            raise contract.VxError(f"immagine {asset_id!r}: geometria non valida")
        try:
            target = float(entry.get("target_duration_sec"))
        except (TypeError, ValueError) as exc:
            raise contract.VxError(
                f"immagine {asset_id!r}: target_duration_sec non numerico"
            ) from exc
        duration = min(target, timeline_est)
        if duration <= 0:
            raise contract.VxError(f"immagine {asset_id!r}: durata non positiva")
        specs[asset_id] = {
            "kind": "image",
            "width": int(asset.width),
            "height": int(asset.height),
            "fps": float(fps),
            "duration": duration,
            "treatment": entry.get("treatment") or "ken_burns_zoom_in",
        }
    return specs


def _stage(
    sources_map: Mapping[str, str],
    specs: Mapping[str, Mapping[str, Any]],
    stage_dir: Path,
) -> Dict[str, ingest.IngestResult]:
    try:
        return ingest.ingest_sources(sources_map, stage_dir, specs=specs)
    except ingest.IngestError as exc:
        raise contract.VxError(f"staging fallito: {exc}") from exc


def _source_refs(
    staged: Mapping[str, ingest.IngestResult],
) -> tuple[Dict[str, edl_plan.SourceRef], Dict[str, Dict[str, Any]]]:
    """Probe the staged file, hash it, and describe it as Gate 1 expects (dicts)."""
    refs: Dict[str, edl_plan.SourceRef] = {}
    media: Dict[str, Dict[str, Any]] = {}
    for asset_id, result in staged.items():
        path = _staged_path(result)
        info = probe.probe_asset(path)
        refs[asset_id] = edl_plan.SourceRef(
            path=str(path),
            sha256=_sha256(path),
            duration=float(info.duration),
        )
        media[asset_id] = {
            "duration": float(info.duration),
            "width": int(info.width),
            "height": int(info.height),
            "fps": float(info.fps),
            "has_audio": bool(info.has_audio),
        }
    return refs, media


def _media_by_source_key(
    plan: Any,
    staged: Mapping[str, ingest.IngestResult],
    media_info_map: Mapping[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Re-key the probe results by the EDL ``source_id`` the plan cites.

    The triage report and ``selected[]`` keep the human-readable asset id; the
    EDL carries the slugged key ``contract.source_key`` builds. Gate 1, the fill
    declaration and the face geometry look their media up by ``clip.source_id``,
    so the map has to follow the plan: ``SourceRef.path`` is the staged file the
    asset produced.
    """
    owner = {str(_staged_path(result)): asset_id for asset_id, result in staged.items()}
    media: Dict[str, Dict[str, Any]] = {}
    for key, ref in plan.sources.items():
        path = str(getattr(ref, "path", "") or "")
        asset_id = owner.get(path)
        if asset_id is None:
            raise contract.VxError(
                f"source {key!r}: nessun file in staging per {path!r}"
            )
        info = media_info_map.get(asset_id)
        if info is None:
            raise contract.VxError(
                f"source {key!r}: nessun media_info per {asset_id!r}"
            )
        media[key] = dict(info)
    return media


def _source_hashes(assets: List[asset_triage.AssetInfo]) -> Dict[str, str]:
    return {asset.asset_id: _sha256(Path(asset.path)) for asset in assets}


def _assert_sources_unchanged(
    assets: List[asset_triage.AssetInfo], before: Mapping[str, str]
) -> None:
    changed: List[str] = []
    for asset in assets:
        try:
            current = _sha256(Path(asset.path))
        except OSError:
            current = ""
        if current != before.get(asset.asset_id):
            changed.append(asset.asset_id)
    if changed:
        raise contract.QCFailure(
            {
                "ok": False,
                "failures": ["source_sha_unchanged"],
                "checks": [
                    {
                        "code": "source_sha_unchanged",
                        "status": "fail",
                        "detail": "sorgenti modificate: " + ", ".join(changed),
                    }
                ],
                "changed": changed,
            }
        )


def _a_roll_sources(plan: Any) -> List[str]:
    seen: List[str] = []
    for track in getattr(plan, "video_tracks", ()):
        for clip in getattr(track, "clips", ()):
            if int(getattr(clip, "layer", 0)) != 0:
                continue
            source_id = getattr(clip, "source_id", None)
            if isinstance(source_id, str) and source_id and source_id not in seen:
                seen.append(source_id)
    return seen


def _bridge_plan(
    plan: Any,
    *,
    captions: bool = True,
    media: Optional[Mapping[str, Any]] = None,
    target_lufs: float = TARGET_LUFS,
) -> Any:
    """Run the bridge: analyses, transcripts, faces and motion per a_roll source.

    A transcription is attempted only when ``captions`` is on. ``subs.transcribe``
    never raises, but it reports a skip as ``skipped_reason``; both that and any
    exception are handed to ``bridge.build`` as a value, which turns them into a
    ``transcript_failed`` declaration instead of a dead run.

    Face detection and the silent video analysis feed A6 and A7 the same way: a
    detector without OpenCV, a missing model or a clip the analyser cannot decode
    travels as the exception it raised and becomes a declared skip, never a crash
    and never a filter applied without a finding. ``media`` supplies the frame
    size of each staged source, because face boxes are pixels and the crop the
    renderer animates is normalised.
    """
    from . import analyze, audio, bridge, faces, subs

    analyses: Dict[str, Any] = {}
    transcripts: Dict[str, Any] = {}
    face_tracks: Dict[str, Any] = {}
    motion: Dict[str, Any] = {}
    sources = _a_roll_sources(plan)
    for source_id in sources:
        ref = plan.sources.get(source_id)
        path = str(getattr(ref, "path", "") or "")
        try:
            analyses[source_id] = audio.analyze_audio(path)
        except Exception as exc:  # noqa: BLE001 - the bridge turns it into a declaration
            analyses[source_id] = exc
        try:
            motion[source_id] = analyze.analyze_video(path)
        except Exception as exc:  # noqa: BLE001 - declared per clip
            motion[source_id] = exc
        try:
            info = (media or {}).get(source_id) or {}
            tracked = faces.detect_faces_tracked(
                path, sample_fps=contract.SAMPLE_FPS_FACE
            )
            payload: Dict[str, Any] = dict(tracked.to_dict())
            payload["width"] = info.get("width")
            payload["height"] = info.get("height")
            cuts = getattr(motion[source_id], "scene_cuts", None)
            if cuts:
                payload["scene_cuts"] = list(cuts)
            face_tracks[source_id] = payload
        except Exception as exc:  # noqa: BLE001 - declared per clip
            face_tracks[source_id] = exc
        if not captions:
            continue
        try:
            transcript = subs.transcribe(path)
            reason = str(getattr(transcript, "skipped_reason", "") or "")
            if reason:
                raise RuntimeError(reason)
            transcripts[source_id] = transcript
        except Exception as exc:  # noqa: BLE001 - the bridge declares it
            transcripts[source_id] = exc
    new_plan, report = bridge.build(
        plan,
        analyses,
        target_lufs=float(target_lufs),
        loudness_spans=audio.loudness_spans,
        transcripts=transcripts if captions else None,
        faces=face_tracks,
        motion=motion,
    )
    return new_plan, report, analyses


def audio_summary(
    analyses: Mapping[str, Any],
    ceiling_dbtp: Optional[float],
) -> Dict[str, Any]:
    """The audio block of the report: measured values, each with its threshold.

    ``snr_db`` takes the worst source and ``music_ratio`` the most musical one,
    because averaging would hide both: the report has to name the clip that needed
    denoise most and the one that is a bed candidate. A source whose analysis
    raised is skipped, never counted as zero. With no measurement the value is
    None and the threshold is still declared, so the absence stays readable.
    """

    def field(item: Any, name: str) -> Optional[float]:
        value = (
            item.get(name) if isinstance(item, Mapping) else getattr(item, name, None)
        )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    snr = [
        value
        for value in (field(item, "snr_db") for item in analyses.values())
        if value is not None
    ]
    music = [
        value
        for value in (field(item, "music_ratio") for item in analyses.values())
        if value is not None
    ]
    return {
        "ceiling_dbtp": ceiling_dbtp,
        "limiter": "alimiter" if ceiling_dbtp is not None else None,
        "snr_db": round(min(snr), 2) if snr else None,
        "snr_threshold_db": float(contract.THRESHOLDS["noise_snr_low"]),
        "music_ratio": round(max(music), 3) if music else None,
        "music_ratio_threshold": float(contract.THRESHOLDS["triage_music_ratio_low"]),
    }


def music_summary(plan: Any, report: Any) -> List[Dict[str, Any]]:
    """One entry per music bed: what the bridge measured and the ducking it applied.

    The block is a list because a bed is a track, not a slot: two beds can carry
    different gain, different loop decisions and, above all, different ducking.
    A report that flattens them to one number cannot be audited.
    """
    specs = {
        str(getattr(spec, "target_role", "")): spec
        for spec in getattr(plan, "ducking_tracks", ()) or ()
    }
    default_spec = getattr(plan, "ducking", None)
    measured = {
        item.get("clip_id"): item
        for item in (getattr(report, "audio", ()) or ())
        if str(item.get("role", "")).startswith("music")
    }
    beds: List[Dict[str, Any]] = []
    for track in getattr(plan, "audio_tracks", ()):
        role = str(getattr(track, "role", "") or "")
        if not (role == "music" or role.startswith("music_")):
            continue
        spec = specs.get(role, default_spec)
        for clip in getattr(track, "clips", ()):
            item = measured.get(clip.clip_id) or {}
            ref = getattr(plan, "sources", {}).get(clip.source_id)
            beds.append(
                {
                    "role": role,
                    "track_id": int(track.track_id),
                    "clip_id": clip.clip_id,
                    "source_id": clip.source_id,
                    "sha256": str(getattr(ref, "sha256", "") or ""),
                    "gain_db": float(clip.gain_db),
                    "looped": item.get("looped"),
                    "loops": item.get("loops"),
                    "crossfade_sec": item.get("crossfade_sec"),
                    "ducking_threshold": (
                        round(float(spec.threshold), 6) if spec is not None else None
                    ),
                    "ducking_ratio": (
                        round(float(spec.ratio), 6) if spec is not None else None
                    ),
                    "ducking_ratio": (float(spec.ratio) if spec is not None else None),
                }
            )
    return beds


def _render_effects(report: Any) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """The render parameters the report declared: keyframes and resolved filters.

    Only what the bridge measured reaches the graph: a clip without keyframes is
    absent from ``reframe`` and keeps its fill policy, and a clip without a
    justified finding is absent from ``restore``.
    """
    reframe: Dict[str, Any] = {
        item["clip_id"]: item["keyframes"]
        for item in getattr(report, "reframe", ())
        if item.get("keyframes")
    }
    restore: Dict[str, Any] = {
        item["clip_id"]: {
            "denoise": item.get("denoise_filter") if item.get("denoise") else None,
            "stabilize": bool(item.get("stabilize")),
        }
        for item in getattr(report, "restore", ())
    }
    return reframe, restore


def _say(msg: str) -> None:
    """Una riga per chi guarda: stdout col narratore, stderr con ``--json``.

    Con ``--json`` lo stdout e' il documento, quindi l'avviso non puo' finirci;
    il narratore invece parla proprio li' e la riga va letta insieme alle altre.
    """
    stream = sys.stdout if _NARRATING else sys.stderr
    try:
        print(msg, file=stream, flush=True)
    except Exception:  # noqa: BLE001 - uno stream chiuso non ferma il montaggio
        pass


def font_network_available(timeout: float = 0.8) -> bool:
    """Se il catalogo risponde, in meno di un secondo e senza scaricare niente.

    La proposta di font e' l'unico punto che puo' uscire in rete; quando il
    socket non si apre si resta sul pool locale invece di aspettare un timeout
    di download per ogni famiglia.
    """
    import socket

    try:
        with socket.create_connection(("raw.githubusercontent.com", 443), timeout):
            return True
    except OSError:
        return False


def _rationale(payload: Any) -> Mapping[str, Any]:
    """Il blocco creativo del payload, o un vuoto: qui non si inventa nulla."""
    if not isinstance(payload, Mapping):
        return {}
    context = payload.get("execution_context")
    if not isinstance(context, Mapping):
        return {}
    rationale = context.get("creative_rationale")
    return rationale if isinstance(rationale, Mapping) else {}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def font_requirement(prompt: Any, payload: Any, plan: Any) -> str:
    """La richiesta da dare a ``fontproposal``: prompt piu' tipo di contenuto.

    Il solo prompt non basta: un reel "120 bpm, duotone, taglio a battuta" chiede
    un carattere diverso da un montaggio lento. I token arrivano dal payload gia'
    deciso e dal piano gia' costruito, non da una seconda interpretazione.
    """
    parts: List[str] = []
    text = " ".join(str(prompt or "").split())
    if text:
        parts.append(text)
    rationale = _rationale(payload)
    pacing = rationale.get("pacing")
    pacing = pacing if isinstance(pacing, Mapping) else {}
    bpm = pacing.get("bpm_target")
    if isinstance(bpm, (int, float)) and not isinstance(bpm, bool) and bpm > 0:
        parts.append(f"{int(bpm)} bpm")
    cut = str(pacing.get("cut_policy") or "").strip()
    if cut and cut != "standard":
        parts.append(cut.replace("_", " "))
    color = rationale.get("color_palette")
    if isinstance(color, Mapping) and str(color.get("type") or "") == "duotone":
        parts.append("vintage")
    genre = str(rationale.get("genre_detected") or "").strip()
    if genre:
        parts.append(genre.replace("_", " "))
    if len(parts) == (1 if text else 0):
        # Nessun look dichiarato e nessun prompt: il piano resta un verticale.
        parts.append(f"verticale {getattr(plan, 'aspect_ratio', '') or '9:16'}")
    return ", ".join(part for part in parts if part)


def font_energy(payload: Any, plan: Any) -> float:
    """0..1 dai numeri del piano: BPM, politica di taglio, densita' dei tagli.

    ``fontchooser.rank`` usa l'energia per chiedere un peso piu' o meno nero;
    una costante sarebbe una bugia. Il BPM viene dal payload, la densita' dai
    clip che il piano ha davvero, e la politica di taglio dice se il ritmo e'
    sincronizzato o narrativo.
    """
    rationale = _rationale(payload)
    pacing = rationale.get("pacing")
    pacing = pacing if isinstance(pacing, Mapping) else {}
    bpm = pacing.get("bpm_target")
    if isinstance(bpm, (int, float)) and not isinstance(bpm, bool) and bpm > 0:
        bpm_term = _clamp01((float(bpm) - 70.0) / 80.0)
    else:
        bpm_term = 0.5
    cut = str(pacing.get("cut_policy") or "").strip()
    cut_term = 1.0 if cut in ("beat_synced", "onset_synced") else 0.35
    duration = 0.0
    try:
        duration = float(getattr(plan, "duration_sec", 0.0) or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    cuts = sum(len(getattr(track, "clips", ()) or ()) for track in getattr(plan, "video_tracks", ()))
    density_term = _clamp01((cuts / duration) / 0.8) if duration > 0 and cuts else 0.35
    return round(
        _clamp01(0.5 * bpm_term + 0.25 * cut_term + 0.25 * density_term), 3
    )


def _choice_family(workdir: Path) -> str:
    """La famiglia scritta dalla vista, o ``""``: un file rotto non e' una scelta."""
    path = Path(workdir) / FONT_CHOICE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, Mapping):
        return ""
    return str(data.get("family") or "").strip()


def _candidate_map(proposal: Any) -> Dict[str, Mapping[str, Any]]:
    raw = proposal.get("candidates") if isinstance(proposal, Mapping) else None
    items = raw if isinstance(raw, list) else []
    out: Dict[str, Mapping[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        family = str(item.get("family") or "").strip()
        if family:
            out.setdefault(family, item)
    return out


def font_gate(
    workdir: Any,
    *,
    requirement: str,
    energy: float,
    allow_network: bool = False,
    wait: float = FONT_WAIT_DEFAULT,
    gate: bool = True,
    view_url: str = "",
    propose: Optional[Callable[..., Dict[str, Any]]] = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    notify: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Proponi i font, poi aspetta una scelta umana senza mai bloccarsi.

    Ordine dei casi, e sono tutti finiti:

    1. una ``font_choice.json`` gia' presente vince subito, nessuna attesa;
    2. senza scelta, se il cancello e' aperto (vista accesa, non
       ``--no-font-gate``) si scrive dove scegliere e si controlla il file ogni
       secondo per al massimo ``wait`` secondi;
    3. alla scelta si usa quella famiglia (``selected_by="user"``); allo scadere
       si usa ``proposal["chosen"]`` e lo si dichiara
       (``selected_by="auto_after_timeout"``);
    4. cancello chiuso: si usa subito ``chosen`` (``selected_by="auto"``).

    Se ``propose`` solleva, la sezione torna con ``family=""`` e un ``error``
    in italiano: il chiamante prosegue col font del preset invece di fermare il
    montaggio. ``propose``, ``clock`` e ``sleep`` sono iniettabili per provare il
    cancello senza rete e senza aspettare davvero.
    """
    root = Path(workdir)
    say = notify if notify is not None else _say
    section: Dict[str, Any] = {
        "family": "",
        "licence": "",
        "source": "",
        "downloaded": False,
        "selected_by": "auto",
        "candidates": [],
        "pool": "local",
        "waited_sec": 0.0,
    }
    runner = propose
    if runner is None:
        from . import fontproposal  # noqa: PLC0415 - the network path stays lazy

        runner = fontproposal.propose
    try:
        proposal = runner(
            root,
            requirement=str(requirement or ""),
            energy=float(energy),
            allow_network=bool(allow_network),
        )
    except Exception as exc:  # noqa: BLE001 - un font mancante non ferma un render
        section["error"] = (
            f"proposta font non disponibile, resta il preset: {exc}"
        )
        say(f"Font: {section['error']}")
        return section
    if not isinstance(proposal, Mapping):
        section["error"] = (
            "proposta font non valida (non e' un oggetto JSON), resta il preset"
        )
        say(f"Font: {section['error']}")
        return section

    by_family = _candidate_map(proposal)
    section["candidates"] = [
        {"family": family, "score": item.get("score")}
        for family, item in by_family.items()
    ]
    section["pool"] = str(proposal.get("pool") or "local")

    started = clock()
    family = _choice_family(root)
    selected_by = "user" if family else "auto"
    waited = 0.0
    if not family and gate and float(wait) > 0:
        if str(view_url or "").strip():
            say(f"Scegli il font nella vista: {view_url}")
        else:
            say(f"Scegli il font nella cartella: {root} (file {FONT_CHOICE_NAME})")
        deadline = started + float(wait)
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                break
            sleep(min(1.0, remaining))
            family = _choice_family(root)
            if family:
                break
        waited = max(0.0, clock() - started)

    if family:
        selected_by = "user"
    if not family:
        chosen = str(proposal.get("chosen") or "").strip()
        if chosen and chosen in by_family:
            family = chosen
            selected_by = (
                "auto_after_timeout" if (gate and float(wait) > 0) else "auto"
            )
    section["waited_sec"] = round(waited, 3)

    item = by_family.get(family)
    if item is not None:
        section.update(
            {
                "family": family,
                "licence": str(item.get("licence") or ""),
                "source": str(item.get("source") or ""),
                "downloaded": bool(item.get("downloaded")),
                "file": str(item.get("file") or ""),
                "selected_by": selected_by,
            }
        )
    elif family:
        # Una scelta che la proposta non contiene non e' un Fontname valido.
        section["family"] = family
        section["error"] = (
            f"{family}: famiglia non presente fra i candidati proposti, "
            "il render resta sul preset"
        )
        selected_by = "user"

    if section["family"]:
        if selected_by == "user":
            say(f"Font: {section['family']} — scelta nella vista.")
        elif selected_by == "auto_after_timeout":
            say(
                f"Font: {section['family']} — scelto in automatico dopo "
                f"{float(wait):g} s senza scelta."
            )
        else:
            say(f"Font: {section['family']} — scelto in automatico.")
    elif not section.get("error"):
        reason = str(proposal.get("reason_if_none") or "").strip()
        section["error"] = reason or "nessun font proposto: resta il preset"
    if section.get("error") and not section["family"]:
        say(f"Font: {section['error']}")
    return section


def _run_font_gate(
    args: Any,
    workdir: Path,
    plan: Any,
    payload: Any,
    *,
    view_url: str = "",
    view_active: bool = False,
) -> Dict[str, Any]:
    """Legge i flag, traduce il piano in richiesta/energia, apre o chiude il cancello."""
    allow_network = getattr(args, "font_network", None)
    if allow_network is None:
        allow_network = bool(settings.get("font.network", True)) and (
            font_network_available()
        )
    wait = getattr(args, "font_wait", None)
    if wait is None:
        wait = float(settings.get("font.wait_sec", FONT_WAIT_DEFAULT))
    gate = bool(view_active) and not bool(getattr(args, "no_font_gate", False))
    return font_gate(
        workdir,
        requirement=font_requirement(getattr(args, "prompt", ""), payload, plan),
        energy=font_energy(payload, plan),
        allow_network=bool(allow_network),
        wait=float(wait),
        gate=gate,
        view_url=str(view_url or ""),
        notify=_say,
    )


def _caption_font(workdir: Path) -> Dict[str, str]:
    """Famiglia scelta e cartella font per l'ASS dell'auto; vuota = preset.

    ``cli._job_font`` e' l'unico posto che legge ``font_choice.json`` /
    ``font_proposal.json.chosen`` e copia il TTF in ``<workdir>/fonts``: qui si
    usa lo stesso risultato del percorso ``run``, cosi' ``Fontname`` e
    ``fontsdir`` non possono divergere. Quando la scelta c'e' ma il file non
    arriva nella cartella, la famiglia torna vuota e ``fallback_reason`` dice
    perche': il report non deve far credere che il pixel sia cambiato.
    """
    from . import cli as cli_mod  # noqa: PLC0415 -- cli importa auto a runtime

    try:
        picked = cli_mod._job_font(Path(workdir))
    except Exception as exc:  # noqa: BLE001 - un font non ferma il montaggio
        return {
            "family": "",
            "fonts_dir": "",
            "fallback_reason": f"font scelto non applicabile: {exc}",
        }
    family = str(picked.get("family") or "")
    fonts_dir = str(picked.get("fonts_dir") or "")
    note = str(picked.get("note") or "")
    if not family:
        return {"family": "", "fonts_dir": "", "fallback_reason": note}
    if not Path(fonts_dir).is_dir():
        return {
            "family": "",
            "fonts_dir": "",
            "fallback_reason": (
                f"{family}: cartella {fonts_dir} mancante, il master resta sul preset"
            ),
        }
    return {"family": family, "fonts_dir": fonts_dir, "fallback_reason": ""}


def _run_locked(
    args: Any,
    workdir: Path,
    *,
    progress: Optional[Callable[[float, str], None]] = None,
    progress_file: Optional[str] = None,
    montage: bool = False,
    fill: str = "fit",
    view_url: str = "",
    view_active: bool = False,
) -> Dict[str, Any]:
    from . import events as events_mod
    from . import features as features_mod

    log = events_mod.EventLog(workdir / "events.jsonl")
    flags = features_mod.effective()
    _emit(log, "run", "start", Path(args.out).name)

    assets = _discover(args)
    assets_by_id = {asset.asset_id: asset for asset in assets}
    source_sha = _source_hashes(assets)
    fill_mode = _fill_mode(fill)
    montage_mode = bool(montage)

    try:
        payload = decide.parse_and_decide(
            args.prompt,
            asset_pool=assets,
            force_mode="autonomous" if getattr(args, "autonomous", False) else None,
            montage=montage_mode,
        )
    except decide.AmbiguousContractError as exc:
        raise contract.VxError(f"prompt ambiguo in STRICT: {exc}") from exc

    block = payload.get("asset_triage")
    if not isinstance(block, Mapping) or not block:
        raise contract.VxError("payload senza asset_triage: triage non eseguito")
    selected = list(block.get("selected_assets") or [])
    rejected = list(block.get("rejected_assets") or [])
    target_lufs, target_tp = loudness_target(payload)
    _notify(progress, 5.0, "triage", log=log)

    fps = _fps(args, selected, assets_by_id)
    timeline_est = _timeline_est(selected, assets_by_id, rejected)

    sources_map: Dict[str, str] = {}
    for entry in selected:
        asset_id = entry.get("asset_id")
        asset = assets_by_id.get(asset_id)
        if asset is None:
            raise contract.VxError(f"asset selezionato {asset_id!r} assente dal pool")
        sources_map[asset_id] = asset.path

    specs = _specs(selected, assets_by_id, fps, timeline_est)
    _log(f"[auto] staging {len(sources_map)} sorgenti a {fps:g} fps")
    staged = _stage(sources_map, specs, workdir / "stage_00")
    source_refs, media_info_map = _source_refs(staged)
    _notify(progress, 20.0, "ingest", log=log)

    plan = contract.payload_to_edl_plan(payload, source_refs, media_info_map, fps=fps)
    errors = plan.validate()
    if errors:
        raise contract.VxError("EDL non valido: " + "; ".join(errors))
    media_by_source = _media_by_source_key(plan, staged, media_info_map)

    bridge_summary: Dict[str, Any] = {
        "enabled": False,
        "cuts": 0,
        "gained_clips": 0,
        "not_applied": 0,
        "captions": 0,
        "reframe": 0,
        "restore": 0,
        "subtitles": None,
        "report": None,
    }
    reframe_map: Dict[str, Any] = {}
    restore_map: Dict[str, Any] = {}
    captions_ass: Optional[str] = None
    captions_srt: Optional[str] = None
    bridge_gains_applied = False
    analyses: Dict[str, Any] = {}
    bridge_report: Any = None
    if not bool(getattr(args, "no_bridge", False)):
        _notify(progress, 22.0, "bridge", log=log, status="start")
        # Un testo sopra il video non si scrive se nessuno l'ha chiesto.
        subs_enabled = bool(getattr(args, "subs", False))
        plan, bridge_report, analyses = _bridge_plan(
            plan,
            captions=subs_enabled,
            media=media_by_source,
            target_lufs=target_lufs,
        )
        reframe_map, restore_map = _render_effects(bridge_report)
        bridge_gains_applied = any("gain_db" in item for item in bridge_report.audio)
        bridged_errors = plan.validate()
        if bridged_errors:
            raise contract.VxError(
                "EDL dopo il ponte non valido: " + "; ".join(bridged_errors)
            )
        if bridge_report.captions:
            from . import bridge as bridge_mod

            # L'ASS nasce dopo il cancello dei font: la famiglia scelta deve
            # arrivare nel `Fontname`, non solo nel report. Il sidecar SRT non
            # ha font e si scrive subito.
            sidecar = bridge_mod.caption_srt(
                bridge_report.captions,
                workdir / "captions.srt",
            )
            captions_srt = str(sidecar) if sidecar is not None else None
        report_path = (workdir / "bridge_report.json").resolve()
        bridge_report.write(report_path)
        bridge_summary = {
            "enabled": True,
            "cuts": len(bridge_report.cuts),
            "gained_clips": len(bridge_report.audio),
            "not_applied": len(bridge_report.not_applied),
            "captions": len(bridge_report.captions),
            "reframe": sum(
                1 for item in bridge_report.reframe if item.get("keyframes")
            ),
            "restore": sum(
                1
                for item in bridge_report.restore
                if item.get("denoise") or item.get("stabilize")
            ),
            "subtitles": captions_ass,
            "subtitles_srt": captions_srt,
            "report": str(report_path),
        }
        _log(
            f"[auto] bridge: {len(bridge_report.cuts)} tagli, "
            f"{len(bridge_report.audio)} gain, "
            f"{len(bridge_report.captions)} caption, "
            f"{len(bridge_report.not_applied)} non applicati"
        )
    _emit(
        log,
        "bridge",
        "done" if bridge_summary["enabled"] else "skip",
        "eseguito" if bridge_summary["enabled"] else "--no-bridge",
    )

    (workdir / "plan.json").write_text(
        json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Il piano esiste: ora si propongono i font e si aspetta la scelta, prima del
    # render. Il cancello e' aperto solo se c'e' una vista e non l'hanno spento:
    # in autonomia senza pannello si prende il ``chosen`` e lo si dichiara.
    font_section = _run_font_gate(
        args, workdir, plan, payload, view_url=view_url, view_active=view_active
    )
    _emit(
        log,
        "font",
        "done" if font_section.get("family") else "skip",
        str(font_section.get("family") or font_section.get("error") or ""),
    )

    # La scelta deve arrivare al pixel, non solo al report: l'ASS si scrive qui,
    # dopo il cancello, con il `Fontname` di `cli._job_font` e la sua cartella.
    caption_font = _caption_font(workdir)
    if bridge_report is not None and getattr(bridge_report, "captions", None):
        from . import bridge as bridge_mod

        written = bridge_mod.caption_ass(
            bridge_report.captions,
            workdir / "captions.ass",
            aspect=str(plan.aspect_ratio),
            out_w=int(plan.resolution[0]),
            out_h=int(plan.resolution[1]),
            font_family=caption_font["family"] or None,
        )
        captions_ass = str(written) if written is not None else None
        bridge_summary["subtitles"] = captions_ass
    if not caption_font["family"]:
        chosen = str(font_section.get("family") or "")
        if chosen:
            font_section["fallback_reason"] = caption_font.get(
                "fallback_reason"
            ) or (
                f"{chosen}: nessun file in {workdir / 'fonts'}, "
                "il master resta sul preset"
            )

    exemptions = grammar.camera_exemptions(plan)
    try:
        grammar.assert_cinematic_rules(plan)
    except grammar.GrammarError as exc:
        # The CLI reports the violations; the exemptions travel with them so an
        # undeclared camera is never silently dropped from the failure report.
        exc.camera_exemptions = exemptions  # type: ignore[attr-defined]
        raise

    gate1 = gates.validate_gate1_durations(plan, media_by_source, fps=fps)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    staged_paths = {
        key: str(getattr(ref, "path", "") or "") for key, ref in plan.sources.items()
    }
    _log(f"[auto] render {plan.duration_sec:.3f}s -> {out}")
    render_estimate = estimate.begin(
        plan,
        workdir,
        crf=int(getattr(args, "crf", None) or settings.get("render.crf", 18)),
        preset=str(
            getattr(args, "preset", None) or settings.get("render.preset", "medium")
        ),
    )
    _notify(progress, 25.0, "render", log=log, status="start")
    ceiling_dbtp = qc.true_peak_ceiling(target_tp) if bridge_gains_applied else None
    render_cache: Dict[str, Any] = {}
    if progress_file:
        # Un file lasciato da un render precedente direbbe il falso: si riparte da zero.
        Path(progress_file).unlink(missing_ok=True)
    render.render_edl(
        plan,
        staged_paths,
        out,
        workdir=workdir / "render",
        loudness=(target_lufs, target_tp),
        crf=int(getattr(args, "crf", None) or settings.get("render.crf", 18)),
        preset=str(
            getattr(args, "preset", None) or settings.get("render.preset", "medium")
        ),
        progress_file=progress_file,
        fill=fill_mode,
        subtitles=captions_ass,
        reframe=reframe_map,
        restore=restore_map,
        ceiling_dbtp=ceiling_dbtp,
        fonts_dir=caption_font["fonts_dir"] or None,
        cache_report=render_cache,
    )
    estimate.finish(render_estimate)
    _notify(progress, 92.0, "render_done", log=log, detail="master scritto")

    qc_result = qc.audit_master(out, target_lufs=target_lufs, target_tp=target_tp)
    _notify(progress, 96.0, "qc", log=log)

    produced = deliverables.produce(
        plan,
        out,
        workdir / "out",
        srt_source=Path(captions_srt) if captions_srt else None,
        workdir=workdir,
    )
    _notify(progress, 100.0, "deliverables", log=log)

    _assert_sources_unchanged(assets, source_sha)

    a_roll_indices: Dict[str, int] = {}
    for entry in selected:
        if entry.get("role") != "a_roll":
            continue
        asset_id = entry.get("asset_id")
        if isinstance(asset_id, str) and asset_id not in a_roll_indices:
            a_roll_indices[asset_id] = len(a_roll_indices)

    scene_block = payload.get("scene") if isinstance(payload, dict) else None
    requested_sec = (
        scene_block.get("duration_sec") if isinstance(scene_block, dict) else None
    )
    shortfall = duration_shortfall(requested_sec, plan.duration_sec)

    report: Dict[str, Any] = {
        "ok": True,
        "payload_version": str(payload.get("contract_version") or contract.VERSION),
        "edl_version": str(plan.contract_version),
        "fps": float(fps),
        "loudness_target": {"lufs": target_lufs, "true_peak_db": target_tp},
        "timeline_sec": float(plan.duration_sec),
        "assets": {
            "total": len(assets),
            "selected": len(selected),
            "rejected": len(rejected),
        },
        "montage": {
            "mode": "montage" if montage_mode else "single",
            "a_roll_count": len(a_roll_indices),
        },
        "fill": _fill_declaration(plan, media_by_source, fill_mode),
        "font": font_section,
        "bridge": bridge_summary,
        "render_estimate": render_estimate,
        "audio": audio_summary(analyses, ceiling_dbtp),
        "music": music_summary(plan, bridge_report),
        "selected": [
            {
                "asset_id": entry.get("asset_id"),
                "role": entry.get("role"),
                "type": entry.get("type"),
                "target_duration_sec": entry.get("target_duration_sec"),
                "treatment": entry.get("treatment"),
                "a_roll_index": a_roll_indices.get(entry.get("asset_id")),
            }
            for entry in selected
        ],
        "rejected": [
            {
                "asset_id": entry.get("asset_id"),
                "type": entry.get("type"),
                "rejection_reason": entry.get("rejection_reason"),
                "details": entry.get("details") or {},
            }
            for entry in rejected
        ],
        "not_evaluated": list(block.get("not_evaluated") or []),
        "plan_notes": list(getattr(plan, "notes", ()) or ()),
        "camera_exemptions": exemptions,
        "gate1": gate1.to_dict(),
        "qc": qc_result,
        "deliverables": produced,
        "master": str(out.resolve()),
        "workdir": str(workdir.resolve()),
        "events_path": str(log.path.resolve()),
        "features": flags,
        "source_sha": source_sha,
        "sources_unchanged": True,
    }
    if shortfall:
        report["duration_shortfall"] = shortfall
        report.setdefault("warnings", []).append(
            "duration_shortfall: chiesti {requested_sec:g} s, consegnati "
            "{delivered_sec:g} s".format(**shortfall)
        )
    if render_cache:
        report["stage_cache"] = render_cache
    _emit(log, "run", "done", f"{len(produced)} consegne")
    (workdir / "auto_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log.close()
    return report


def run(
    args: Any,
    *,
    progress: Optional[Callable[[float, str], None]] = None,
    progress_file: Optional[str] = None,
    montage: Optional[bool] = None,
    fill: Optional[str] = None,
    view_url: str = "",
    view_active: Optional[bool] = None,
) -> Dict[str, Any]:
    """Execute the whole pipeline under the workdir lock and return the report.

    ``montage`` and ``fill`` default to ``None``: the flags on ``args`` win
    unless a caller passes an explicit value, so an existing caller that only
    has ``args`` keeps the single-a_roll and ``fit`` behaviour unchanged.
    ``view_active`` says whether a human can actually see this job (panel or
    ``--watch``); when it is ``None`` a narrator carrying a panel counts as one.
    """
    out = Path(args.out)
    assets_dir = Path(args.assets)
    global _NARRATING
    _NARRATING = progress is not None
    workdir = _resolve_workdir(args)
    montage_mode = (
        bool(getattr(args, "montage", False)) if montage is None else bool(montage)
    )
    fill_mode = (
        str(getattr(args, "fill", "fit") or "fit") if fill is None else str(fill)
    )

    if _inside(out, assets_dir):
        raise contract.VxError(
            "--out non puo' stare dentro --assets: la cartella sorgente non si scrive"
        )
    if _inside(workdir, assets_dir):
        raise contract.VxError(
            "--workdir non puo' stare dentro --assets: la cartella sorgente non si scrive"
        )

    workdir.mkdir(parents=True, exist_ok=True)
    if view_active is None:
        panel = getattr(progress, "panel", None) if progress is not None else None
        view_active = bool(view_url) or panel is not None
    beat = lock.acquire(
        workdir,
        stage="auto",
        heartbeat_seconds=float(settings.get("lock.heartbeat_seconds", 30.0)),
    )
    try:
        return _run_locked(
            args,
            workdir,
            progress=progress,
            progress_file=progress_file,
            montage=montage_mode,
            fill=fill_mode,
            view_url=view_url,
            view_active=bool(view_active),
        )
    except Exception as exc:  # noqa: BLE001 - re-raised unchanged, only logged
        from . import events as events_mod

        log = events_mod.EventLog(workdir / "events.jsonl")
        _emit(log, "run", "fail", f"{type(exc).__name__}: {exc}")
        log.close()
        raise
    finally:
        lock.release(beat)
