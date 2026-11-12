"""Test del cut plan: il taglio si sposta solo dentro un silenzio misurato.

Le asserzioni sono sul contratto: i confini si muovono solo se l'onset cade
dentro un silenzio, l'input non viene mutato, l'aritmetica dei frame resta
esatta e il grafo che ne esce passa il Gate 2.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import contract, cutplan, edl as edl_mod, gates  # noqa: E402
from vx.probe import probe  # noqa: E402

PASS = 0
FAIL = 0
CLIP = ROOT / "tests" / "stock_corpus" / "bright_45.mp4"


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:46} {detail}")


def build_edl(fps: float, label: str) -> edl_mod.Edl:
    shots = []
    for index in range(4):
        src_in = float(index)
        frames = int(round(1.0 * fps))
        shots.append(
            edl_mod.EdlShot(
                label=label,
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
    return edl_mod.Edl(
        fps=fps,
        shots=shots,
        total=4.0,
        frames=sum(shot.frames for shot in shots),
        rhythmic_shots=4,
        inaction_shots=0,
    )


def main() -> int:
    if not CLIP.is_file():
        # Il clip di corpus non e' versionato: senza, non c'e' niente da
        # tagliare. Si dichiara e si esce 0: un file assente non e' un difetto.
        print(f"  [SKIP] clip di corpus assente: {CLIP}")
        return 0

    info = probe(str(CLIP))
    fps = float(info.fps)
    print(f"clip: {CLIP.name} {info.duration:.2f}s @ {fps:.2f} fps")

    original = build_edl(fps, CLIP.stem)
    onsets = [1.03, 2.03, 3.03]
    silences = [{"start": 0.9, "end": 3.1}]

    print("\nonset dentro il silenzio: tutti i confini si muovono")
    snapped = cutplan.snap(original, silences, onsets, fps=fps)
    moved = [shot for shot in snapped.shots if shot.note.startswith("snap:")]
    check("tre confini mossi", len(moved) == 3, f"{len(moved)}/3")
    check(
        "ogni confine nel silenzio",
        all(
            any(s["start"] < shot.src_out < s["end"] for s in silences)
            for shot in snapped.shots[:-1]
        ),
        str([round(shot.src_out, 3) for shot in snapped.shots[:-1]]),
    )
    check(
        "note snap con delta",
        all(
            shot.note.startswith("snap:") and shot.note != "snap:0ms" for shot in moved
        ),
        str([shot.note for shot in moved]),
    )

    print("\nl'input non viene mutato")
    check(
        "src_out originali intatti",
        [shot.src_out for shot in original.shots] == [1.0, 2.0, 3.0, 4.0],
        str([shot.src_out for shot in original.shots]),
    )
    check(
        "note originali intatte",
        all(shot.note == "" for shot in original.shots),
        str([shot.note for shot in original.shots]),
    )

    print("\nonset fuori dal silenzio: nessun movimento")
    outside = cutplan.snap(original, [{"start": 4.0, "end": 5.0}], onsets, fps=fps)
    check(
        "nessuna nota snap",
        all(not shot.note.startswith("snap:") for shot in outside.shots),
        str([shot.note for shot in outside.shots]),
    )
    check(
        "confini invariati",
        [shot.src_out for shot in outside.shots]
        == [shot.src_out for shot in original.shots],
        str([round(shot.src_out, 3) for shot in outside.shots]),
    )

    print("\nnessun onset in tolleranza: nessun movimento")
    far = cutplan.snap(original, silences, [1.5, 2.5, 3.5], fps=fps)
    check(
        "nessuna nota snap",
        all(not shot.note.startswith("snap:") for shot in far.shots),
        str([shot.note for shot in far.shots]),
    )

    print("\naritmetica dei frame")
    check(
        "somma frame == edl.frames",
        sum(shot.frames for shot in snapped.shots) == snapped.frames,
        f"{snapped.frames}",
    )
    check(
        "totale == frame/fps",
        abs(snapped.total - snapped.frames / fps) < 1e-6,
        f"{snapped.total} vs {snapped.frames / fps:.6f}",
    )
    check(
        "ogni frame == round(durata*fps)",
        all(shot.frames == int(round(shot.duration * fps)) for shot in snapped.shots),
        str([shot.frames for shot in snapped.shots]),
    )
    check(
        "timeline contigua",
        all(
            abs(snapped.shots[i].t_out - snapped.shots[i + 1].t_in) < 1e-6
            for i in range(len(snapped.shots) - 1)
        ),
        str([round(shot.t_in, 3) for shot in snapped.shots]),
    )

    print("\nGate 2 sul grafo dell'EDL snappato")
    graph = edl_mod.filtergraph(snapped, {CLIP.stem: 0}, fps=fps)
    try:
        gates.graph(graph)
        check("Gate 2 accetta il grafo", True, "trim + atrim + concat")
    except contract.QCFailure as exc:
        check("Gate 2 accetta il grafo", False, str(exc.result.get("checks")))

    print("\nsilence_windows non solleva mai")
    check(
        "path inesistente -> lista vuota",
        cutplan.silence_windows(str(ROOT / "tests" / "non_esiste.mp4")) == [],
        "[]",
    )
    windows = cutplan.silence_windows(str(CLIP))
    check(
        "clip reale -> lista di finestre",
        isinstance(windows, list)
        and all(set(window) == {"start", "end"} for window in windows),
        f"{len(windows)} finestre",
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

