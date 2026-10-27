"""The delivered SRT must say what the burned ASS says, at the same times.

A master can ship with captions burned in and still be accompanied by an SRT
that claims "no speech": the two came from different paths. This pins them
together.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import bridge, deliverables  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:54} {detail}")


def captions() -> list:
    return [
        {
            "caption_id": "cap_1",
            "dst_in": 1.59,
            "dst_out": 3.23,
            "text": "amore, pero dai piedi",
        },
        {
            "caption_id": "cap_2",
            "dst_in": 3.46,
            "dst_out": 5.72,
            "text": "dai piedi,   dai piedi",
        },
        {
            "caption_id": "cap_3",
            "dst_in": 10.0,
            "dst_out": 13.5,
            "text": "si, qua sopra",
        },
    ]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        srt = bridge.caption_srt(captions(), tmp / "captions.srt")
        text = Path(srt).read_text(encoding="utf-8")
        blocks = [b for b in text.strip().split("\n\n") if b.strip()]
        check("SRT scritto con una voce per caption", len(blocks) == 3, len(blocks))
        check(
            "SRT numerazione da 1",
            blocks[0].splitlines()[0] == "1",
            blocks[0].splitlines()[0],
        )
        check(
            "SRT tempo con virgola e millisecondi",
            blocks[0].splitlines()[1] == "00:00:01,590 --> 00:00:03,230",
            blocks[0].splitlines()[1],
        )
        check(
            "SRT spazi normalizzati nel testo",
            blocks[1].splitlines()[2] == "dai piedi, dai piedi",
            blocks[1].splitlines()[2],
        )

        dirty = captions() + [
            {"caption_id": "cap_x", "dst_in": 6.0, "dst_out": 6.0, "text": "zero"},
            {"caption_id": "cap_y", "dst_in": 7.0, "dst_out": 8.0, "text": "   "},
            {
                "caption_id": "cap_z",
                "dst_in": None,
                "dst_out": 9.0,
                "text": "senza inizio",
            },
        ]
        clean_srt = bridge.caption_srt(dirty, tmp / "dirty.srt")
        clean_blocks = [
            b
            for b in Path(clean_srt).read_text(encoding="utf-8").strip().split("\n\n")
            if b.strip()
        ]
        check(
            "SRT scarta durata zero, testo vuoto, tempo mancante",
            len(clean_blocks) == 3,
            len(clean_blocks),
        )

        none_path = tmp / "vuoto.srt"
        result = bridge.caption_srt([], none_path)
        check(
            "nessuna caption: nessun SRT",
            result is None and not none_path.exists(),
            result,
        )

        ass = bridge.caption_ass(
            captions(), tmp / "captions.ass", out_w=1080, out_h=1920
        )
        ass_text = Path(ass).read_text(encoding="utf-8")
        ass_lines = [ln for ln in ass_text.splitlines() if ln.startswith("Dialogue:")]
        check(
            "ASS e SRT hanno lo stesso numero di voci",
            len(ass_lines) == len(blocks),
            (len(ass_lines), len(blocks)),
        )
        ass_words = [ln.split(")}", 1)[1].strip() for ln in ass_lines]
        srt_words = [b.splitlines()[2].strip() for b in blocks]
        check(
            "ASS e SRT dicono le stesse parole, nello stesso ordine",
            ass_words == srt_words,
            srt_words,
        )

        master = tmp / "master.mp4"
        master.write_bytes(b"\x00" * 2048)
        real_probe = deliverables._probe
        real_run = deliverables._run_ff
        try:
            deliverables._probe = lambda path: {
                "duration": 12.0,
                "width": 1080,
                "height": 1920,
            }
            deliverables._run_ff = lambda args, target: Path(target).write_bytes(
                b"\x00" * 64
            )

            out_dir = tmp / "out"
            produced = deliverables.produce(
                {}, master, out_dir, srt_source=Path(srt), workdir=tmp
            )
            subs = next(item for item in produced if item["kind"] == "subtitles")
            check(
                "con srt_source il deliverable sottotitoli e' disponibile",
                subs["available"] is True,
                subs,
            )
            check(
                "il file consegnato e' l'SRT del ponte",
                Path(subs["path"]).read_text(encoding="utf-8") == text,
                subs["path"],
            )

            out_dir2 = tmp / "out2"
            produced2 = deliverables.produce({}, master, out_dir2, workdir=tmp)
            subs2 = next(item for item in produced2 if item["kind"] == "subtitles")
            check(
                "senza srt_source resta dichiarato non disponibile",
                subs2["available"] is False and subs2.get("reason") == "no speech",
                subs2,
            )
        finally:
            deliverables._probe = real_probe
            deliverables._run_ff = real_run

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
