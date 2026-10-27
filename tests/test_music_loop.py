"""A12: una musica piu' corta della timeline gira in loop, con giunzioni in acrossfade.

python tests/test_music_loop.py

Nessun render: si guarda il grafo e il referto. Il caso "musica lunga" fissa la
regressione dura: senza loop il grafo deve restare quello di prima, byte per byte.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import bridge, contract, render  # noqa: E402
from vx.edl_plan import ClipSegment, EDLPlan, SourceRef, Track  # noqa: E402

PASS = 0
FAIL = 0

CROSSFADE = float(contract.THRESHOLDS["music_loop_crossfade_sec"])


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def _plan(timeline: float, media: float) -> EDLPlan:
    """Il piano che il bridge produce oggi: clip musica = min(media, timeline)."""
    played = min(media, timeline)
    sources = {
        "v": SourceRef("v.mp4", "a" * 64, timeline),
        "m": SourceRef("m.mp4", "b" * 64, media),
    }
    video = ClipSegment("c1", "v", 0.0, timeline, 0.0, timeline, 0, "main_talking_head")
    music = ClipSegment("m1", "m", 0.0, played, 0.0, played, 0, "music", gain_db=-8.0)
    return EDLPlan(
        fps=25.0,
        duration_sec=timeline,
        aspect_ratio="16:9",
        resolution=(320, 180),
        sources=sources,
        video_tracks=(Track(0, (video,), "main"),),
        audio_tracks=(Track(1, (music,), "music"),),
    )


def _graph(plan: EDLPlan) -> str:
    return render.build_edl_filtergraph(plan, {"v": 0, "m": 1})


def main() -> int:
    started = time.time()

    # -- 10 s di timeline, 4 s di musica: 3 copie, 2 giunzioni ----------------- #
    short = contract.plan_music_loop(4.0, 10.0, CROSSFADE)
    check(
        "4 s su 10 s: 3 copie e crossfade 0.400",
        (short.looped, short.copies, short.crossfade_sec) == (True, 3, 0.4),
        short.to_dict(),
    )

    looped = _graph(_plan(10.0, 4.0))
    check(
        "asplit=3 sulla musica", "[1:a]" in looped and "asplit=3" in looped, "asplit=3"
    )
    check(
        "tre copie etichettate",
        all(f"[mum1loop{index}]" in looped for index in (1, 2, 3)),
    )
    check(
        "acrossfade=d=0.400 esattamente due volte",
        looped.count("acrossfade=d=0.400000") == 2,
        looped.count("acrossfade=d=0.400000"),
    )
    check(
        "la catena musica chiude con atrim a 10 s",
        "[mum1xfade2]atrim=start=0:end=10.000000,asetpts=PTS-STARTPTS,"
        "volume=-8.000dB,adelay=0|0[mum1]" in looped,
    )
    check(
        "il bus musica resta a 10 s",
        "[music_bus]apad=whole_dur=10.000000,atrim=start=0:end=10.000000" in looped,
    )
    check("gain musica invariato", "volume=-8.000dB" in looped)

    # -- 20 s di timeline, 30 s di musica: nessun loop, grafo di prima -------- #
    long_graph = _graph(_plan(20.0, 30.0))
    check("musica lunga: nessun asplit", "asplit" not in long_graph)
    check("musica lunga: nessun acrossfade", "acrossfade" not in long_graph)
    check(
        "musica lunga: catena singola identica a oggi",
        "[1:a]atrim=start=0.000000:end=20.000000,asetpts=PTS-STARTPTS,"
        "volume=-8.000dB,adelay=0|0[mum1]" in long_graph,
    )
    same_as_today = _graph(_plan(20.0, 20.0))
    check("musica lunga: grafo byte-identico a oggi", long_graph == same_as_today)

    # -- traccia patologica: rifiutata, non emessa --------------------------- #
    refused = contract.plan_music_loop(0.01, 600.0, CROSSFADE)
    check(
        "0.01 s su 600 s: rifiutata dal limite",
        refused.looped is False
        and refused.copies == 1
        and str(contract.MAX_MUSIC_LOOP_COPIES) in refused.refused,
        refused.to_dict(),
    )
    pathological = _graph(_plan(600.0, 0.01))
    check(
        "traccia patologica: nessun grafo enorme",
        "asplit" not in pathological and "acrossfade" not in pathological,
    )

    # -- referto: la dichiarazione, e src_out alla durata del media ---------- #
    report = bridge.BridgeReport()
    short_plan = _plan(10.0, 4.0)
    retimed = bridge._retime_music(short_plan, short_plan.audio_tracks[0], 10.0, report)
    clip = retimed.clips[0] if retimed is not None else None
    check(
        "_retime_music: src_out = durata del media, non della timeline",
        clip is not None and abs(float(clip.src_out) - 4.0) <= 1e-9,
        None if clip is None else clip.src_out,
    )
    entry = next((item for item in report.audio if item.get("role") == "music"), None)
    check("referto: una voce per la musica", entry is not None, report.audio)
    check(
        "referto: looped true, loops 3, crossfade 0.4",
        entry is not None
        and entry.get("looped") is True
        and entry.get("loops") == 3
        and abs(float(entry.get("crossfade_sec", 0.0)) - 0.4) <= 1e-9,
        entry,
    )
    check(
        "referto: declared in una riga",
        entry is not None
        and isinstance(entry.get("declared"), str)
        and "\n" not in entry["declared"]
        and bool(entry["declared"].strip()),
        None if entry is None else entry.get("declared"),
    )

    long_report = bridge.BridgeReport()
    long_plan = _plan(20.0, 30.0)
    bridge._retime_music(long_plan, long_plan.audio_tracks[0], 20.0, long_report)
    long_entry = next(
        (item for item in long_report.audio if item.get("role") == "music"), None
    )
    check(
        "referto: musica lunga dichiarata non in loop",
        long_entry is not None
        and long_entry.get("looped") is False
        and long_entry.get("loops") == 1
        and long_entry.get("crossfade_sec") == 0.0,
        long_entry,
    )

    bad_report = bridge.BridgeReport()
    bad_plan = _plan(600.0, 0.01)
    bridge._retime_music(bad_plan, bad_plan.audio_tracks[0], 600.0, bad_report)
    bad_entry = next(
        (item for item in bad_report.audio if item.get("role") == "music"), None
    )
    check(
        "referto: loop rifiutato dichiarato",
        bad_entry is not None
        and bad_entry.get("looped") is False
        and "rifiutato" in str(bad_entry.get("declared"))
        and any(
            item.get("reason") == "music_loop_refused"
            for item in bad_report.not_applied
        ),
        bad_entry,
    )

    elapsed = time.time() - started
    print(f"\n{PASS} PASS / {FAIL} FAIL  ({elapsed:.2f}s)")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
