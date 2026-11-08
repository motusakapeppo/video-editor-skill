"""Il ponte restituisce la durata chiesta: riempie la coda col parlato che segue.

    python tests/test_bridge_fill_speech.py

F1  un parlato con pause che finisce prima del piano: la coda si riempie con i
    tratti di parlato **successivi**, saltando la pausa, e la durata torna quella
    del piano;
F2  il video e il mirror audio restano 1:1 (stesso numero di clip, stessi dst):
    un riempimento che disallinea il parlato sarebbe peggio del buco;
F3  nessun secondo aggiunto cade in un silenzio misurato (il morto tolto non
    rientra);
F4  se dopo il taglio non c'è più materiale, non si inventa niente e il report
    non dichiara un riempimento che non c'è;
F5  il piano valido e l'input non mutato.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import bridge, edl_plan  # noqa: E402

PASS = 0
FAIL = 0

SPEECH = [(0.0, 3.0), (5.0, 8.0), (10.0, 12.0), (14.0, 17.0), (18.0, 20.0)]
SILENCES = [(3.0, 5.0), (8.0, 10.0), (12.0, 14.0), (17.0, 18.0)]


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def analysis(speech=SPEECH, silences=SILENCES) -> dict:
    return {
        "speech_regions": [{"start": s, "end": e} for s, e in speech],
        "dead_air": [{"start": s, "end": e, "duration": e - s} for s, e in silences],
    }


def plan(duration: float = 12.0, source_duration: float = 20.0) -> edl_plan.EDLPlan:
    source = {
        "c01": edl_plan.SourceRef(
            path="c01.mp4", sha256="a" * 64, duration=source_duration
        )
    }
    video = edl_plan.ClipSegment(
        clip_id="v0_0",
        source_id="c01",
        src_in=0.0,
        src_out=duration,
        dst_in=0.0,
        dst_out=duration,
        layer=0,
        role="main_talking_head",
    )
    dialogue = edl_plan.ClipSegment(
        clip_id="a0_0",
        source_id="c01",
        src_in=0.0,
        src_out=duration,
        dst_in=0.0,
        dst_out=duration,
        layer=0,
        role="dialogue",
    )
    return edl_plan.EDLPlan(
        fps=25.0,
        duration_sec=duration,
        aspect_ratio="9:16",
        resolution=(1080, 1920),
        sources=source,
        video_tracks=(edl_plan.Track(0, (video,), "a_roll"),),
        audio_tracks=(edl_plan.Track(0, (dialogue,), "dialogue"),),
    )


def stub_loudness(path, spans):
    return {"input_i": -20.0, "input_tp": -1.0, "input_lra": 5.0}


def inside(t0: float, t1: float, spans) -> bool:
    return all(any(start - 1e-6 <= t <= end + 1e-6 for start, end in spans) for t in (t0, t1))


def test_caption_shortfall() -> None:
    print("\n3. F6 la coda senza sottotitoli si misura oltre l'ultima parola")

    class Words:
        words = [{"start": 5.0, "end": 9.8, "word": "parola"}]

    check(
        "F6 dentro la finestra non c'e' scoperto",
        bridge._uncaptioned_seconds(Words(), [(5.2, 9.5)]) == 0.0,
        bridge._uncaptioned_seconds(Words(), [(5.2, 9.5)]),
    )
    check(
        "F6 oltre l'ultima parola conta solo la coda",
        abs(bridge._uncaptioned_seconds(Words(), [(9.0, 10.5)]) - 0.7) <= 1e-6,
        bridge._uncaptioned_seconds(Words(), [(9.0, 10.5)]),
    )
    check(
        "F6 senza parole tutto il tratto e' scoperto",
        abs(bridge._uncaptioned_seconds(None, [(1.0, 2.0)]) - 1.0) <= 1e-6,
        bridge._uncaptioned_seconds(None, [(1.0, 2.0)]),
    )


def main() -> int:
    print("\n1. F1/F2/F3 il ponte riempie la coda col parlato che segue")
    original = plan()
    new_plan, report = bridge.build(
        original,
        {"c01": analysis()},
        target_lufs=-14.0,
        loudness_spans=stub_loudness,
    )
    check("F5 il piano valido", new_plan.validate() == [], new_plan.validate())
    check(
        "F5 l'input non e' mutato",
        len(original.video_tracks[0].clips) == 1,
        len(original.video_tracks[0].clips),
    )

    video = list(new_plan.video_tracks[0].clips)
    dialogue = list(new_plan.audio_tracks[0].clips)
    check(
        "F1 la durata torna quella del piano",
        abs(new_plan.duration_sec - 12.0) <= 1e-6,
        new_plan.duration_sec,
    )
    check(
        "F1 tre frammenti tenuti + due aggiunti",
        len(video) == 5,
        [(round(c.src_in, 2), round(c.src_out, 2)) for c in video],
    )
    check(
        "F1 la coda viene dal parlato dopo la pausa",
        all(clip.src_in >= 14.0 - 1e-6 for clip in video[3:]),
        [(round(c.src_in, 2), round(c.src_out, 2)) for c in video[3:]],
    )
    check(
        "F2 video e dialogo 1:1 (numero e dst)",
        len(dialogue) == len(video)
        and all(
            abs(a.dst_in - b.dst_in) <= 1e-6 and abs(a.dst_out - b.dst_out) <= 1e-6
            for a, b in zip(video, dialogue)
        ),
        [(len(video), len(dialogue))],
    )
    check(
        "F2 anche i src coincidono",
        all(
            abs(a.src_in - b.src_in) <= 1e-6 and abs(a.src_out - b.src_out) <= 1e-6
            for a, b in zip(video, dialogue)
        ),
        None,
    )
    check(
        "F3 la coda aggiunta viene da parlato misurato",
        all(inside(clip.src_in, clip.src_out, SPEECH) for clip in video[3:]),
        [(round(c.src_in, 2), round(c.src_out, 2)) for c in video[3:]],
    )
    filled = [
        row
        for row in report.not_applied
        if row.get("reason") == "tail_filled_with_speech"
    ]
    check(
        "F1 il report dichiara il riempimento con i numeri",
        bool(filled)
        and float(filled[0].get("seconds") or 0.0) > 3.0
        and int(filled[0].get("fragments") or 0) == 2,
        filled,
    )

    print("\n2. F4 senza materiale dopo il taglio non si inventa niente")
    short = plan(duration=12.0, source_duration=12.0)
    short_plan, short_report = bridge.build(
        short,
        {"c01": analysis()},
        target_lufs=-14.0,
        loudness_spans=stub_loudness,
    )
    check(
        "F4 la durata resta quella del parlato disponibile (con i pad)",
        abs(short_plan.duration_sec - 8.48) <= 1e-6,
        short_plan.duration_sec,
    )
    check(
        "F4 e nessun riempimento viene dichiarato",
        not any(
            row.get("reason") == "tail_filled_with_speech"
            for row in short_report.not_applied
        ),
        short_report.not_applied,
    )
    check(
        "F4 video e dialogo restano 1:1",
        len(short_plan.video_tracks[0].clips) == len(short_plan.audio_tracks[0].clips),
        (
            len(short_plan.video_tracks[0].clips),
            len(short_plan.audio_tracks[0].clips),
        ),
    )

    test_caption_shortfall()

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
