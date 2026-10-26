"""vx.cutplan — sposta i tagli dentro il silenzio misurato, non sul battito sognato.

Perche' esiste: ``grammar.snap_cuts`` mette il taglio su un accento, ma un accento
puo' cadere in mezzo a una parola. Un taglio in mezzo a una parola si sente come un
errore, non come ritmo. Qui la regola e' piu' stretta: il taglio si sposta su un
onset solo se il nuovo confine cade DENTRO un silenzio misurato dal VAD. Se non
c'e' un onset qualificato, il confine resta dov'era.

Il risultato e' un nuovo ``Edl``: l'input non viene mai mutato. Ogni spezzone
mantiene ``frames = round(duration * fps)`` e il totale e' la somma dei frame, non
un arrotondamento indipendente che puo' divergere.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import contract, grammar, vad
from .edl import Edl, EdlShot, filtergraph
from .edl_plan import (
    ClipSegment,
    EDLPlan,
    EDLValidationError,
    SourceRef,
    Track,
    Transition,
)


def _inside(t: float, silences: List[Dict[str, float]]) -> bool:
    """Strettamente dentro un silenzio: i bordi sono parlato, non pausa."""
    return any(float(s["start"]) < t < float(s["end"]) for s in silences)


def snap(
    edl: Edl,
    silences: List[Dict[str, float]],
    onsets: List[float],
    *,
    fps: Optional[float] = None,
) -> Edl:
    """Sposta ogni confine interno su un onset che cade dentro un silenzio.

    Il confine e' il tempo sorgente dell'out-point dello spezzone uscente
    (``src_out``): e' li' che il VAD e gli onset sono misurati. Il confine nuovo
    viene quantizzato alla griglia dei frame (``round(t*fps)/fps``) e i due
    spezzoni adiacenti vengono ricalcolati perche' i loro frame restino esatti.
    """
    rate = float(fps or edl.fps)
    tolerance = grammar.frame_tolerance(rate)
    shots = [EdlShot(**shot.to_dict()) for shot in edl.shots]

    for index in range(len(shots) - 1):
        outgoing, incoming = shots[index], shots[index + 1]
        boundary = float(outgoing.src_out)
        candidates: List[tuple[float, float, float]] = []
        for raw in onsets:
            onset = float(raw)
            if abs(onset - boundary) > tolerance:
                continue
            snapped = round(onset * rate) / rate
            # Il confine che conta e' quello quantizzato: se il frame piu' vicino
            # esce dal silenzio, il taglio non e' sicuro e non si fa.
            if not _inside(onset, silences) or not _inside(snapped, silences):
                continue
            candidates.append((abs(onset - boundary), onset, snapped))
        if not candidates:
            continue

        _, _, snapped = min(candidates, key=lambda item: item[0])
        delta = snapped - boundary
        if abs(delta) < 1e-9:
            # L'onset piu' vicino cade sullo stesso frame: non e' un movimento.
            continue
        frames_out = int(round((snapped - float(outgoing.src_in)) * rate))
        frames_in = int(round((float(incoming.src_out) - snapped) * rate))
        if frames_out < 1 or frames_in < 1:
            continue

        outgoing.frames = frames_out
        outgoing.duration = round(frames_out / rate, 4)
        outgoing.src_out = round(float(outgoing.src_in) + outgoing.duration, 4)
        incoming.src_in = round(snapped, 4)
        incoming.frames = frames_in
        incoming.duration = round(frames_in / rate, 4)
        incoming.src_out = round(float(incoming.src_in) + incoming.duration, 4)
        outgoing.note = f"snap:{int(round(delta * 1000))}ms"

    cursor = 0.0
    for shot in shots:
        shot.t_in = round(cursor, 4)
        shot.t_out = round(cursor + shot.frames / rate, 4)
        cursor = shot.t_out

    total = round(cursor, 4)
    frames = sum(shot.frames for shot in shots)
    inaction = sum(1 for shot in shots if shot.cut_on == "inaction")
    return Edl(
        fps=edl.fps,
        shots=shots,
        total=total,
        frames=frames,
        rhythmic_shots=len(shots) - inaction,
        inaction_shots=inaction,
        notes=list(edl.notes),
    )


def silence_windows(path: str) -> List[Dict[str, float]]:
    """I tratti non parlati misurati dal VAD, come ``[{"start","end"}]``.

    Il VAD e' un miglioramento, non un requisito: se il modello o onnxruntime
    mancano, o se il file non si decodifica, si restituisce una lista vuota e il
    chiamante resta sui confini originali. Non solleva mai.
    """
    try:
        result = vad.detect(path)
    except Exception:  # noqa: BLE001 - il VAD non deve fermare un montaggio
        return []
    if not result.available:
        return []
    try:
        gaps = vad.gaps_from(result.regions, result.duration)
    except Exception:  # noqa: BLE001 - stessa regola: degrada, non esplode
        return []
    return [{"start": float(gap["start"]), "end": float(gap["end"])} for gap in gaps]


@dataclass(frozen=True)
class BrollCandidate:
    """A B-roll insert anchored to a semantic finding on the main dialogue.

    ``at`` is a SOURCE timestamp on the A-Roll source, not a timeline second:
    the builder maps it through the shot that contains it. ``finding_source_id``
    is the source the finding's ``t0``/``t1`` refer to, and it is the one written
    into the witness: the B-roll source is the footage, not the evidence.
    """

    broll_source_id: str
    src_in: float
    src_out: float
    at: float
    finding_id: str
    finding_source_id: str
    t0: float
    t1: float
    role: str = "broll_overlay"
    transition_sec: float = 0.3

    def witness(self) -> Dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "t0": float(self.t0),
            "t1": float(self.t1),
            "source_id": self.finding_source_id,
        }


@dataclass(frozen=True)
class BrollRule:
    """Keyword -> footage: one semantic rule, not a montage decision."""

    keyword: str
    broll_source_id: str
    src_in: float
    src_out: float
    max_uses: int = 1
    min_gap_sec: float = 2.0
    role: str = "broll_overlay"
    transition_sec: float = 0.3


def _norm_token(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return "".join(ch for ch in without_marks if ch.isalnum())


def select_broll(
    words: Sequence[Mapping[str, Any]],
    rules: Sequence[BrollRule],
    *,
    source_id: str,
    finding_id: str = "transcript.keyword_match",
) -> List[BrollCandidate]:
    """Turn keyword matches into anchored B-roll candidates, in transcript order.

    Tokens are compared case- and accent-insensitively, punctuation stripped.
    The candidate's ``at`` is the matched word's midpoint on the A-Roll source;
    the builder maps it to the timeline. ``min_gap_sec`` is measured from the
    last accepted insert across all rules, so two keywords cannot stack the
    same visual moment. The first matching rule in ``rules`` wins.
    """
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("source_id must be a non-empty string")
    prepared: List[Tuple[BrollRule, str]] = []
    for rule in rules:
        if not isinstance(rule, BrollRule):
            raise ValueError("rules must contain BrollRule objects")
        token = _norm_token(rule.keyword)
        if not token:
            raise ValueError("BrollRule.keyword must contain at least one alphanumeric")
        if float(rule.src_out) <= float(rule.src_in):
            raise ValueError(f"BrollRule {rule.keyword!r}: src_out must be > src_in")
        if int(rule.max_uses) < 1:
            raise ValueError(f"BrollRule {rule.keyword!r}: max_uses must be >= 1")
        if float(rule.min_gap_sec) < 0:
            raise ValueError(f"BrollRule {rule.keyword!r}: min_gap_sec must be >= 0")
        prepared.append((rule, token))

    uses = [0] * len(prepared)
    last_at: Optional[float] = None
    selected: List[BrollCandidate] = []
    for word in sorted(words, key=lambda item: float(item.get("start") or 0.0)):
        start = word.get("start")
        end = word.get("end")
        if start is None or end is None or float(end) <= float(start):
            continue
        token = _norm_token(word.get("word") or word.get("text") or "")
        if not token:
            continue
        for index, (rule, key) in enumerate(prepared):
            if token != key or uses[index] >= int(rule.max_uses):
                continue
            at = (float(start) + float(end)) / 2.0
            if last_at is not None and abs(at - last_at) < float(rule.min_gap_sec):
                continue
            selected.append(
                BrollCandidate(
                    broll_source_id=rule.broll_source_id,
                    src_in=float(rule.src_in),
                    src_out=float(rule.src_out),
                    at=at,
                    finding_id=finding_id,
                    finding_source_id=source_id,
                    t0=float(start),
                    t1=float(end),
                    role=rule.role,
                    transition_sec=float(rule.transition_sec),
                )
            )
            uses[index] += 1
            last_at = at
            break
    return selected


@dataclass(frozen=True)
class BuiltEdl:
    """The EDL plus the witness that justifies every B-roll clip."""

    plan: EDLPlan
    witnesses: Mapping[str, Dict[str, object]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "witnesses", MappingProxyType(dict(self.witnesses)))

    def witness(self, clip_id: str) -> Dict[str, object]:
        return self.witnesses[clip_id]


def build_edl_plan(
    edl: Edl,
    *,
    source_id: str,
    sources: Mapping[str, SourceRef],
    broll: Sequence[BrollCandidate] = (),
    fps: Optional[float] = None,
    duration: Optional[float] = None,
    aspect_ratio: str = "9:16",
    resolution: Optional[Tuple[int, int]] = None,
) -> BuiltEdl:
    """Build the multi-track EDL v2.0: A-Roll, mirrored dialogue, anchored B-roll.

    The A-Roll keeps the Edl's cut timing. The dialogue track mirrors those
    same clips, so the audio/video offset is zero and the J/L law is satisfied
    by construction. B-roll lives in its own video track (layer 1): putting it
    in the A-Roll track would violate the no-overlap invariant.
    """
    if not isinstance(edl, Edl) or not edl.shots:
        raise EDLValidationError(["edl: at least one shot is required"])
    if source_id not in sources:
        raise EDLValidationError([f"sources: {source_id} is not declared"])
    frame_rate = float(fps if fps is not None else edl.fps)
    total = float(duration if duration is not None else edl.total)
    if frame_rate <= 0:
        raise EDLValidationError(["timeline.fps must be > 0"])
    if total <= 0:
        raise EDLValidationError(["timeline.duration_sec must be > 0"])
    if aspect_ratio not in contract.ASPECTS:
        raise EDLValidationError(
            [f"timeline.aspect_ratio {aspect_ratio!r} is not a known aspect"]
        )
    width, height = resolution or contract.output_size(aspect_ratio)

    main_clips = [
        ClipSegment(
            clip_id=f"c{index}",
            source_id=source_id,
            src_in=float(shot.src_in),
            src_out=float(shot.src_out),
            dst_in=float(shot.t_in),
            dst_out=float(shot.t_out),
            layer=0,
            role="main_talking_head",
        )
        for index, shot in enumerate(edl.shots, 1)
    ]
    audio_clips = [
        ClipSegment(
            clip_id=f"a{index}",
            source_id=clip.source_id,
            src_in=clip.src_in,
            src_out=clip.src_out,
            dst_in=clip.dst_in,
            dst_out=clip.dst_out,
            layer=0,
            role="dialogue",
        )
        for index, clip in enumerate(main_clips, 1)
    ]

    broll_clips: List[ClipSegment] = []
    witnesses: Dict[str, Dict[str, object]] = {}
    for index, candidate in enumerate(broll, 1):
        if candidate.finding_source_id != source_id:
            raise EDLValidationError(
                [
                    f"broll[{index}]: finding source {candidate.finding_source_id} "
                    f"is not the main dialogue source {source_id}"
                ]
            )
        if candidate.broll_source_id not in sources:
            raise EDLValidationError(
                [f"broll[{index}]: source {candidate.broll_source_id} is not declared"]
            )
        if not (float(candidate.src_out) > float(candidate.src_in)):
            raise EDLValidationError([f"broll[{index}]: src_out must be > src_in"])
        if not (float(candidate.t0) <= float(candidate.at) <= float(candidate.t1)):
            raise EDLValidationError(
                [
                    f"broll[{index}]: at {candidate.at} is outside the finding "
                    f"span [{candidate.t0}, {candidate.t1}]"
                ]
            )
        host = next(
            (
                clip
                for clip in main_clips
                if clip.src_in - 1e-6 <= float(candidate.at) <= clip.src_out + 1e-6
            ),
            None,
        )
        if host is None:
            raise EDLValidationError(
                [f"broll[{index}]: at {candidate.at} is outside every A-Roll shot"]
            )
        if (
            float(candidate.t0) < host.src_in - 1e-6
            or float(candidate.t1) > host.src_out + 1e-6
        ):
            raise EDLValidationError(
                [
                    f"broll[{index}]: finding span [{candidate.t0}, "
                    f"{candidate.t1}] crosses the host shot "
                    f"[{host.src_in}, {host.src_out}]"
                ]
            )
        declared = sources[candidate.broll_source_id].duration
        if declared is not None and float(candidate.src_out) > float(declared) + 1e-6:
            raise EDLValidationError(
                [
                    f"broll[{index}]: src_out {candidate.src_out} exceeds "
                    f"{candidate.broll_source_id} duration {declared}"
                ]
            )
        clip_duration = float(candidate.src_out) - float(candidate.src_in)
        timeline_in = host.dst_in + (float(candidate.at) - host.src_in)
        timeline_out = timeline_in + clip_duration
        if timeline_out > total + 1e-6:
            raise EDLValidationError(
                [
                    f"broll[{index}]: ends at {timeline_out:.3f}, past the "
                    f"timeline {total:.3f}"
                ]
            )
        clip_id = f"b{index}"
        broll_clips.append(
            ClipSegment(
                clip_id=clip_id,
                source_id=candidate.broll_source_id,
                src_in=float(candidate.src_in),
                src_out=float(candidate.src_out),
                dst_in=timeline_in,
                dst_out=timeline_out,
                layer=1,
                role=candidate.role,
                transition_in=Transition("fade", float(candidate.transition_sec)),
                transition_out=Transition("fade", float(candidate.transition_sec)),
            )
        )
        witnesses[clip_id] = candidate.witness()

    video_tracks = [Track(track_id=0, clips=tuple(main_clips), role="main")]
    if broll_clips:
        video_tracks.append(Track(track_id=1, clips=tuple(broll_clips), role="broll"))
    audio_tracks = (
        (Track(track_id=0, clips=tuple(audio_clips), role="dialogue"),)
        if audio_clips
        else ()
    )

    plan = EDLPlan(
        fps=frame_rate,
        duration_sec=total,
        aspect_ratio=aspect_ratio,
        resolution=(int(width), int(height)),
        sources=sources,
        video_tracks=tuple(video_tracks),
        audio_tracks=audio_tracks,
    )
    errors = plan.validate()
    if errors:
        raise EDLValidationError(errors)
    return BuiltEdl(plan=plan, witnesses=witnesses)


def _selftest() -> int:
    from pathlib import Path

    from . import contract, gates
    from .probe import probe

    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:44} {detail}")

    root = Path(__file__).resolve().parents[2]
    clip = root / "tests" / "stock_corpus" / "bright_45.mp4"
    if not clip.is_file():
        print(f"  [FAIL] clip di corpus assente: {clip}")
        return 1

    info = probe(str(clip))
    fps = float(info.fps)
    print(f"clip: {clip.name} {info.duration:.2f}s @ {fps:.2f} fps")

    shots: List[EdlShot] = []
    for index in range(4):
        src_in = float(index)
        frames = int(round(1.0 * fps))
        shots.append(
            EdlShot(
                label=clip.stem,
                src_in=src_in,
                src_out=round(src_in + frames / fps, 4),
                duration=round(frames / fps, 4),
                t_in=float(index),
                t_out=float(index + 1),
                frames=frames,
                cut_on="bar",
                contrast=1.5,
            )
        )
    edl = Edl(
        fps=fps,
        shots=shots,
        total=4.0,
        frames=sum(shot.frames for shot in shots),
        rhythmic_shots=4,
        inaction_shots=0,
    )

    onsets = [1.03, 2.03, 3.03]
    silences = [{"start": 0.9, "end": 3.1}]
    snapped = snap(edl, silences, onsets, fps=fps)
    moved = [shot for shot in snapped.shots if shot.note.startswith("snap:")]
    check("tutti i confini si muovono", len(moved) == 3, f"{len(moved)}/3")
    check(
        "ogni confine resta nel silenzio",
        all(_inside(shot.src_out, silences) for shot in snapped.shots[:-1]),
        str([round(shot.src_out, 3) for shot in snapped.shots[:-1]]),
    )
    check(
        "input non mutato",
        edl.shots[0].src_out == 1.0 and edl.shots[0].note == "",
        f"src_out {edl.shots[0].src_out}",
    )
    check(
        "aritmetica frame esatta",
        sum(shot.frames for shot in snapped.shots) == snapped.frames,
        f"{snapped.frames} frame",
    )
    check(
        "totale = frame/fps",
        abs(snapped.total - snapped.frames / fps) < 1e-6,
        f"{snapped.total} vs {snapped.frames / fps:.6f}",
    )

    outside = snap(edl, [{"start": 4.0, "end": 5.0}], onsets, fps=fps)
    check(
        "nessun movimento fuori dal silenzio",
        all(not shot.note.startswith("snap:") for shot in outside.shots),
        str([shot.note for shot in outside.shots]),
    )
    check(
        "confini invariati fuori dal silenzio",
        [shot.src_out for shot in outside.shots]
        == [shot.src_out for shot in edl.shots],
        str([round(shot.src_out, 3) for shot in outside.shots]),
    )

    graph = filtergraph(snapped, {clip.stem: 0}, fps=fps)
    try:
        gates.graph(graph)
        check("Gate 2 sul grafo", True, "trim + atrim + concat")
    except contract.QCFailure as exc:
        check("Gate 2 sul grafo", False, str(exc.result.get("checks")))

    print(f"{'OK' if failures == 0 else 'FALLITO'} — {failures} fallimenti")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="vx.cutplan selftest")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    parser.print_help()
    raise SystemExit(2)
