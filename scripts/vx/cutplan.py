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

from typing import Dict, List, Optional

from . import grammar, vad
from .edl import Edl, EdlShot, filtergraph


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
