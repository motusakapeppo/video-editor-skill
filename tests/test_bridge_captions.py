"""A4: word anchors, captions remapped on the cut timeline, b-roll citations, ASS.

python tests/test_bridge_captions.py

No ffmpeg, no whisper, no network: the transcript is a stand-in object with
``.words``/``.segments``, the audio analysis is synthetic and the loudness
measurement is a stub. The cuts are the A5 fixture's two silences, so every
destination time here is arithmetic on the kept fragments:

    kept  [0.00, 1.12] [1.68, 4.12] [4.48, 6.00]
    dst   [0.00, 1.12] [1.12, 3.56] [3.56, 5.08]
    cut   [1.12, 1.68] [4.12, 4.48]
"""

from __future__ import annotations

import inspect
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import bridge, edl_plan, render  # noqa: E402

PASS = 0
FAIL = 0

SPEECH = [(0.0, 1.0), (1.8, 4.0), (4.6, 6.0)]
SILENCES = [(1.0, 1.8), (4.0, 4.6)]

WORDS = [
    (0.10, 0.30, "Ciao", 0.95),
    (0.35, 0.55, "a", 0.90),
    (0.60, 0.80, "tutti", 0.20),
    (0.90, 1.05, "oggi.", 0.92),
    (1.20, 1.30, "silenzio", 0.88),
    (1.35, 1.40, "perso", 0.88),
    (1.85, 1.95, "uno", 0.90),
    (2.00, 2.10, "due", 0.90),
    (2.15, 2.25, "tre", 0.90),
    (2.30, 2.45, "quattro", 0.90),
    (2.50, 2.65, "cinque", 0.90),
    (2.70, 2.80, "sei", 0.90),
    (2.85, 2.95, "sette", 0.90),
    (3.00, 3.10, "otto", 0.90),
    (3.15, 3.23, "nove", 0.90),
    (3.27, 3.35, "dieci", 0.93),
    (3.80, 3.95, "test", 0.90),
    (4.00, 4.11, "senza", 0.90),
    (4.20, 4.30, "buco", 0.90),
    (4.50, 4.70, "dopo", 0.93),
]


class FakeTranscript:
    """The shape ``subs.transcribe`` returns, without faster-whisper."""

    def __init__(self, words):
        self.words = [
            {"start": t0, "end": t1, "word": token, "probability": probability}
            for t0, t1, token, probability in words
        ]
        self.segments = [
            {"start": 0.0, "end": 6.0, "text": " ".join(w[2] for w in words)}
        ]
        self.skipped_reason = ""


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def analysis() -> dict:
    return {
        "speech_regions": [{"start": s, "end": e} for s, e in SPEECH],
        "dead_air": [{"start": s, "end": e, "duration": e - s} for s, e in SILENCES],
    }


def loudness(path, spans):
    return {"input_i": -24.0, "input_tp": -2.0, "input_lra": 4.0}


def plan(broll_at: float = 1.30, broll_length: float = 0.30) -> edl_plan.EDLPlan:
    sources = {"c01": edl_plan.SourceRef(path="c01.mp4", sha256="a" * 64, duration=6.0)}
    video = edl_plan.ClipSegment(
        clip_id="v0_0",
        source_id="c01",
        src_in=0.0,
        src_out=6.0,
        dst_in=0.0,
        dst_out=6.0,
        layer=0,
        role="main_talking_head",
    )
    dialogue = edl_plan.ClipSegment(
        clip_id="a0_0",
        source_id="c01",
        src_in=0.0,
        src_out=6.0,
        dst_in=0.0,
        dst_out=6.0,
        layer=0,
        role="dialogue",
    )
    broll = edl_plan.ClipSegment(
        clip_id="v1_0",
        source_id="c01",
        src_in=0.0,
        src_out=broll_length,
        dst_in=broll_at,
        dst_out=broll_at + broll_length,
        layer=1,
        role="b_roll",
    )
    return edl_plan.EDLPlan(
        fps=25.0,
        duration_sec=6.0,
        aspect_ratio="9:16",
        resolution=(1080, 1920),
        sources=sources,
        video_tracks=(
            edl_plan.Track(0, (video,), "a_roll"),
            edl_plan.Track(1, (broll,), "b_roll"),
        ),
        audio_tracks=(edl_plan.Track(0, (dialogue,), "dialogue"),),
    )


def run(caption_style=None, transcripts=...):
    source = FakeTranscript(WORDS) if transcripts is ... else transcripts
    return bridge.build(
        plan(),
        {"c01": analysis()},
        target_lufs=-14.0,
        loudness_spans=loudness,
        transcripts={"c01": source} if source is not None else None,
        caption_style=caption_style,
    )


def style_row(ass_text: str) -> list:
    for line in ass_text.splitlines():
        if line.startswith("Style:"):
            return line.split(",")
    return []


def main() -> int:
    signature = inspect.signature(bridge.build).parameters
    check(
        "build: transcripts keyword-only, default None",
        signature["transcripts"].kind is inspect.Parameter.KEYWORD_ONLY
        and signature["transcripts"].default is None,
        signature["transcripts"].default,
    )
    check(
        "build: caption_style keyword-only, default None",
        signature["caption_style"].kind is inspect.Parameter.KEYWORD_ONLY
        and signature["caption_style"].default is None,
        signature["caption_style"].default,
    )

    new_plan, report = run()
    check("plan valido dopo il ponte", new_plan.validate() == [], new_plan.validate())

    # -- word anchors ---------------------------------------------------- #
    words = [a for a in report.anchors if a.kind == "word_span"]
    check(
        "un'ancora per ogni parola sopra soglia",
        len(words) == 19 and all(a.source_id == "c01" for a in words),
        len(words),
    )
    check(
        "ancora con tempo sorgente, testo e confidenza",
        all(
            a.t1 is not None and a.t1 >= a.t0 and a.text and a.confidence is not None
            for a in words
        ),
        words[0].to_dict() if words else None,
    )
    rejected = [
        item
        for item in report.not_applied
        if item.get("reason") == "word_below_min_confidence"
    ]
    check(
        "parola sotto soglia contata e dichiarata",
        len(rejected) == 1 and rejected[0].get("count") == 1,
        rejected,
    )
    check(
        "la parola rifiutata non ha ancora",
        not any(a.text == "tutti" for a in words),
        [a.text for a in words if a.text == "tutti"],
    )
    outside = [
        item
        for item in report.not_applied
        if item.get("reason") == "word_outside_timeline"
    ]
    check(
        "parole fuori timeline dichiarate",
        len(outside) == 1 and outside[0].get("count") == 3,
        outside,
    )

    # -- captions -------------------------------------------------------- #
    texts = [c["text"] for c in report.captions]
    check(
        "caption: 5 gruppi, taglio sul punto fermo",
        texts[0] == "Ciao a oggi.",
        texts,
    )
    check(
        "caption: chiusura su max_chars (42)",
        texts[1] == "uno due tre quattro cinque sei sette otto" and len(texts[1]) <= 42,
        texts[1] if len(texts) > 1 else None,
    )
    check("caption: chiusura sul gap", texts[2] == "nove dieci", texts[2:3])
    check(
        "didascalie in ordine e dentro la timeline",
        all(
            float(c["dst_in"]) < float(c["dst_out"]) <= new_plan.duration_sec + 1e-6
            for c in report.captions
        )
        and [float(c["dst_in"]) for c in report.captions]
        == sorted(float(c["dst_in"]) for c in report.captions),
        [round(float(c["dst_in"]), 3) for c in report.captions],
    )

    split = [c for c in report.captions if c["text"] in ("test senza", "dopo")]
    check(
        "caption a cavallo del taglio si divide in due",
        len(split) == 2,
        [c["text"] for c in split],
    )
    if len(split) == 2:
        head, tail = split
        head_texts = {
            head["anchor_ids"][i]: head["text"].split()[i]
            for i in range(len(head["anchor_ids"]))
        }
        check(
            "ogni pezzo cita le proprie ancore",
            set(head_texts.values()) == {"test", "senza"}
            and all(
                anchor_id in [a.anchor_id for a in report.anchors]
                for anchor_id in head["anchor_ids"] + tail["anchor_ids"]
            )
            and len(tail["anchor_ids"]) == 1,
            (head["anchor_ids"], tail["anchor_ids"]),
        )
        check(
            "parola nel silenzio esclusa e contata sulla caption",
            head.get("words_dropped") == 1 and tail.get("words_dropped") == 0,
            (head.get("words_dropped"), tail.get("words_dropped")),
        )
        check(
            "i due pezzi restano nella timeline",
            abs(float(head["dst_out"]) - 3.55) <= 0.01
            and abs(float(tail["dst_in"]) - 3.58) <= 0.01,
            (head["dst_out"], tail["dst_in"]),
        )
    dropped = [
        item
        for item in report.not_applied
        if item.get("reason") == "caption_in_silence"
    ]
    check(
        "caption interamente nel silenzio scartata con conteggio",
        len(dropped) == 1 and dropped[0].get("words_dropped") == 2,
        dropped,
    )

    # -- b-roll citations ------------------------------------------------ #
    check(
        "b-roll copre ancore: anchor_ids popolati",
        len(report.broll) == 1
        and bool(report.broll[0].get("anchor_ids"))
        and report.broll[0].get("accepted") is True,
        report.broll[0] if report.broll else None,
    )
    check(
        "b-roll senza ancore: lista vuota, nessun falso silenzio",
        not any(
            anchor_id.startswith("a_silence_")
            for anchor_id in report.broll[0].get("anchor_ids", [])
        ),
        report.broll[0].get("anchor_ids") if report.broll else None,
    )
    _, lonely = bridge.build(
        plan(broll_at=4.60, broll_length=0.20),
        {"c01": analysis()},
        target_lufs=-14.0,
        loudness_spans=loudness,
        transcripts={"c01": FakeTranscript(WORDS)},
    )
    shifted = [
        item
        for item in lonely.not_applied
        if item.get("reason") == "broll_shifted_to_speech"
    ]
    check(
        "b-roll senza ancora: si sposta sul parlato invece di restare muto",
        bool(lonely.broll)
        and bool(lonely.broll[0].get("anchor_ids"))
        and len(shifted) == 1
        and shifted[0].get("score") is not None,
        (lonely.broll[0].get("anchor_ids") if lonely.broll else None, shifted),
    )

    # -- un b-roll lontano da ogni parola resta senza ancora e lo dichiara -- #
    _, far = bridge.build(
        plan(broll_at=4.60, broll_length=0.20),
        {"c01": analysis()},
        target_lufs=-14.0,
        loudness_spans=loudness,
        transcripts={
            "c01": FakeTranscript([(0.10, 0.30, "Ciao", 0.95), (0.35, 0.55, "a", 0.90)])
        },
    )
    empty = [
        item
        for item in far.not_applied
        if item.get("reason") == "broll_without_anchor"
    ]
    check(
        "b-roll lontano da ogni parola: senza ancora, dichiarato",
        bool(far.broll)
        and far.broll[0].get("anchor_ids") == []
        and len(empty) == 1
        and empty[0].get("asset_id") == "c01",
        (far.broll[0].get("anchor_ids") if far.broll else None, empty),
    )

    # -- ASS -------------------------------------------------------------- #
    with tempfile.TemporaryDirectory(prefix="vx-cap-") as raw:
        target = Path(raw) / "captions.ass"
        written = bridge.caption_ass(
            report.captions, target, aspect="9:16", out_w=1080, out_h=1920
        )
        text = target.read_text(encoding="utf-8")
        check(
            "ASS scritto per le caption",
            written is not None and Path(written).is_file(),
            written,
        )
        check(
            "una riga Dialogue per caption",
            sum(1 for line in text.splitlines() if line.startswith("Dialogue:"))
            == len(report.captions),
            len(report.captions),
        )
        check(
            "PlayRes uguale alla tela",
            "PlayResX: 1080" in text and "PlayResY: 1920" in text,
            [line for line in text.splitlines() if line.startswith("PlayRes")],
        )
        check(
            "MarginV dal canvas: round(1920*0.08) = 154",
            style_row(text)[21] == "154",
            style_row(text)[21] if len(style_row(text)) > 21 else None,
        )
        low = Path(raw) / "low.ass"
        bridge.caption_ass(
            report.captions,
            low,
            out_h=1920,
            caption_style={"bottom_inset_ratio": 0.0},
        )
        check(
            "MarginV clampato al minimo 24",
            style_row(low.read_text(encoding="utf-8"))[21] == "24",
            style_row(low.read_text(encoding="utf-8"))[21],
        )
        high = Path(raw) / "high.ass"
        bridge.caption_ass(
            report.captions,
            high,
            out_h=1920,
            caption_style={"bottom_inset_ratio": 0.9},
        )
        check(
            "MarginV clampato a out_h // 4",
            style_row(high.read_text(encoding="utf-8"))[21] == "480",
            style_row(high.read_text(encoding="utf-8"))[21],
        )
        empty_ass = Path(raw) / "empty.ass"
        check(
            "nessuna caption: nessun ASS",
            bridge.caption_ass([], empty_ass) is None and not empty_ass.exists(),
            empty_ass.exists(),
        )

        # -- burn: the ASS enters the EDL graph after concat and overlays -- #
        mapping = {"c01": 0}
        plain = render.build_edl_filtergraph(new_plan, mapping, has_audio=False)
        burned = render.build_edl_filtergraph(
            new_plan, mapping, has_audio=False, subtitles=str(target)
        )
        check(
            "grafo senza ASS: nessun filtro subtitles",
            "subtitles=" not in plain and plain.endswith("null[vout]"),
            plain[-40:],
        )
        check(
            "grafo con ASS: filtro subtitles prima di [vout]",
            "subtitles=" in burned
            and render.ass_filter_path(target) in burned
            and burned.endswith("null[vout]"),
            burned[-90:],
        )
        check(
            "grafo con ASS: subtitles dopo concat e dopo gli overlay",
            burned.index("subtitles=") > burned.index("concat=")
            and burned.index("subtitles=") > burned.index("overlay="),
            [
                burned.index("concat="),
                burned.index("overlay="),
                burned.index("subtitles="),
            ],
        )

    # -- a failed transcript is declared, never fatal -------------------- #
    failed_plan, failed = run(transcripts=RuntimeError("model not downloaded"))
    failures = [
        item
        for item in failed.not_applied
        if str(item.get("reason", "")).startswith("transcript_failed:")
    ]
    check(
        "trascrizione fallita: dichiarata, nessuna caption",
        len(failures) == 1
        and "model not downloaded" in failures[0]["reason"]
        and failed.captions == []
        and failed_plan.validate() == [],
        failures,
    )
    check(
        "trascrizione fallita: nessuna ancora di parola",
        not [a for a in failed.anchors if a.kind == "word_span"],
        len(failed.anchors),
    )
    _, off = run(transcripts=None)
    check(
        "caption disattivate: dichiarato",
        off.captions == []
        and any(item.get("reason") == "captions_disabled" for item in off.not_applied),
        [item.get("reason") for item in off.not_applied],
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
