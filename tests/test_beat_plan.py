"""Il piano taglia sulla griglia di battuta, e ogni taglio dice su quale.

Niente rete. L'audio e' un click sintetico a 115 BPM generato qui, cosi' il BPM
vero e la fase vera sono noti e lo scarto in frame e' misurabile invece che
stimato. La griglia numpy e' provata direttamente, quindi il fallback senza
librosa resta provato anche su una macchina che librosa ce l'ha.

    python tests/test_beat_plan.py
"""

from __future__ import annotations

import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import coherence, decide, grammar  # noqa: E402

PASS = 0
FAIL = 0
SR = 16000
BPM = 115.0
PERIOD = 60.0 / BPM


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:52} {detail}")


def click_track(*, bpm: float = BPM, seconds: float = 16.0) -> np.ndarray:
    """Un click di 12 ms ogni battuta: l'unico contenuto e' il tempo."""
    x = np.zeros(int(SR * seconds), dtype=np.float32)
    period = 60.0 / bpm
    width = int(0.012 * SR)
    k = 0
    while k * period < seconds:
        start = int(round(k * period * SR))
        if start + width <= x.size:
            x[start : start + width] += np.hanning(width).astype(np.float32)
        k += 1
    return x


def write_wav(path: Path, pcm: np.ndarray) -> None:
    data = np.clip(pcm, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes((data * 32767).astype("<i2").tobytes())


def nearest_beat_error_frames(times, bpm: float) -> float:
    period = 60.0 / bpm
    errors = [abs(t - round(t / period) * period) * 30.0 for t in times]
    return max(errors) if errors else 0.0


def main() -> int:
    print("\n[1] griglia numpy: nessuna libreria, solo inviluppo + autocorrelazione")
    pcm = click_track()
    grid = grammar.numpy_beat_grid(pcm, SR)
    check("BPM misurato 114..116", 114.0 <= grid.bpm <= 116.0, round(grid.bpm, 3))
    check("metodo dichiarato numpy", grid.method == "numpy-onset-autocorr", grid.method)
    check(
        "battute generate",
        len(grid.beats) >= 20,
        len(grid.beats),
    )
    check(
        "confini del rilevamento entro 1 frame dal vero",
        nearest_beat_error_frames(grid.beats, BPM) <= 1.0,
        round(nearest_beat_error_frames(grid.beats, BPM), 3),
    )
    check(
        "onset rilevati",
        len(grid.onsets) >= 20,
        len(grid.onsets),
    )

    raw = Path(tempfile.gettempdir()) / "vx_beat_plan_click.wav"
    write_wav(raw, pcm)
    try:
        from_file = grammar.beat_grid(str(raw), engine="numpy")
        check(
            "fallback da file decodificato",
            from_file.method.startswith("numpy-onset"),
            from_file.method,
        )
        check(
            "stesso BPM dal file",
            abs(from_file.bpm - grid.bpm) < 0.5,
            round(from_file.bpm, 3),
        )
    finally:
        raw.unlink(missing_ok=True)

    print("\n[2] piano a 115 BPM forzato: densita' e griglia")
    materials = [
        {"label": "a", "duration": 6.0},
        {"label": "b", "duration": 6.0},
        {"label": "c", "duration": 6.0},
        {"label": "d", "duration": 6.0},
    ]
    plan = grammar.plan_beat_shots(materials, bpm=BPM, fps=30.0)
    check("densita' >= 0.7 tagli/s", plan.density >= 0.7, plan.density)
    check(
        "shot medio sotto i 0.67 s del reel di riferimento",
        plan.report["mean_shot_sec"] <= 0.67,
        plan.report["mean_shot_sec"],
    )
    check(
        "ogni taglio entro 1 frame dal multiplo di battuta",
        plan.report["within_1_frame"] == len(plan.cuts) and plan.report["max_frame_error"] <= 0.5,
        plan.report["frame_error_hist"],
    )
    check(
        "provenienza beat su ogni taglio",
        all(cut["provenance"]["kind"] == "beat" for cut in plan.cuts),
        plan.cuts[0],
    )
    beats = [cut["beat_number"] for cut in plan.cuts]
    check(
        "numero di battuta crescente di uno",
        beats == list(range(beats[0], beats[0] + len(beats))),
        beats[:5],
    )
    check(
        "report dichiara gli scarti",
        set(plan.report) >= {"max_frame_error", "within_1_frame", "off_grid"},
        sorted(plan.report),
    )
    half = grammar.plan_beat_shots(materials, bpm=BPM, fps=30.0, subdivision=2)
    check(
        "1/2 di battuta raddoppia la densita'",
        half.report["density_cuts_per_sec"] > 2.0 * plan.density * 0.9,
        half.density,
    )
    check("energia alta -> 1/4", grammar.subdivision_for_energy(0.8) == 4)
    check("energia media -> 1/2", grammar.subdivision_for_energy(0.5) == 2)
    check("energia bassa -> battuta", grammar.subdivision_for_energy(0.1) == 1)

    print("\n[3] materiale piu' corto di una battuta")
    short = grammar.plan_beat_shots([0.2, 0.2, 0.2], bpm=BPM, fps=30.0)
    check("tre tagli fuori griglia", short.off_grid == 3, short.off_grid)
    check(
        "il report lo dice",
        short.report["off_grid"] == 3 and bool(short.report["off_grid_reason"]),
        short.report["off_grid_reason"],
    )
    check("durata = somma del materiale", abs(short.total - 0.6) < 1e-6, short.total)

    print("\n[4] --bpm e --beat-grid su decide")
    plain = decide.parse_and_decide("un reel, durata 12 sec")
    check(
        "auto senza BPM resta standard",
        plain["beat_grid"]["enabled"] is False,
        plain["beat_grid"],
    )
    forced = decide.parse_and_decide("un reel, durata 12 sec", bpm=115)
    check(
        "--bpm accende la griglia",
        forced["beat_grid"]["enabled"] is True
        and forced["beat_grid"]["source"] == "flag"
        and forced["beat_grid"]["bpm"] == 115.0,
        forced["beat_grid"],
    )
    edit = decide.payload_to_edit_plan(forced, {"duration": 12.0}, fps=30.0)
    check("piano con tagli multipli", len(edit.cuts) > 10, len(edit.cuts))
    check(
        "tagli con provenienza beat",
        all(cut.get("provenance", {}).get("kind") == "beat" for cut in edit.cuts),
        edit.cuts[1],
    )
    check(
        "BPM eseguito, non rimandato",
        "beat_grid" in (edit.brief.get("bridge") or {}).get("executed", [])
        and "pacing.bpm_target"
        not in {
            item.get("field")
            for item in (edit.brief.get("bridge") or {}).get("unsupported", [])
        },
        edit.brief.get("beat_grid"),
    )
    off = decide.parse_and_decide("un reel 115 bpm, durata 12 sec", beat_grid="off")
    off_plan = decide.payload_to_edit_plan(off, {"duration": 12.0}, fps=30.0)
    check(
        "--beat-grid off torna al taglio unico",
        off_plan.cuts == [{"in": 0.0, "out": 12.0}],
        off_plan.cuts,
    )
    off_fields = {
        item.get("field")
        for item in (off_plan.brief.get("bridge") or {}).get("unsupported", [])
    }
    check("off lo dichiara", "pacing.bpm_target" in off_fields, off_fields)

    print("\n[5] la coerenza riconosce la provenienza")
    edit_dict = edit.to_dict()
    check(
        "canale di provenienza = plan",
        coherence._provenance_channel(
            edit_dict, coherence._shots(edit_dict), {}
        )
        == "plan",
        coherence._provenance_channel(edit_dict, coherence._shots(edit_dict), {}),
    )
    second = edit.cuts[0]
    check(
        "cut_reason = beat sul taglio",
        coherence._plan_cut_reason(edit_dict, second["out"]) == "beat",
        coherence._plan_cut_reason(edit_dict, second["out"]),
    )

    print("\n[6] griglia misurata dall'audio, usata per pianificare")
    offset = grid.beats[0] % PERIOD
    measured = grammar.plan_beat_shots(
        [{"label": "main", "duration": 12.0}],
        bpm=grid.bpm,
        fps=30.0,
        offset=offset,
        total=12.0,
    )
    check(
        "piano su griglia misurata entro 1 frame dal vero",
        nearest_beat_error_frames(
            [cut["t"] for cut in measured.cuts], BPM
        )
        <= 1.0,
        round(nearest_beat_error_frames([cut["t"] for cut in measured.cuts], BPM), 3),
    )
    check(
        "densita' misurata >= 0.7 tagli/s",
        measured.density >= 0.7,
        measured.density,
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
