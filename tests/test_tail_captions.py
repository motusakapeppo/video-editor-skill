"""La coda trascritta a parte: i tratti dal referto e la fusione delle parole.

    python tests/test_tail_captions.py

T1  i tratti da trascrivere escono dal referto (`captions_tail_uncovered`),
    fonte per fonte, e un referto senza quel motivo non produce niente;
T2  la fusione riporta le parole dell'estratto al tempo sorgente (offset), le
    ordina, e non duplica una parola già presente;
T3  segmenti, testo e durata si fondono senza perdere il resto.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, contract  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def transcript(words, segments=(), text="", duration=0.0):
    return contract.Transcript(
        language="it",
        words=[{"start": a, "end": b, "word": w, "probability": 1.0} for a, b, w in words],
        segments=[{"start": a, "end": b, "text": t} for a, b, t in segments],
        text=text,
        duration=duration,
    )


def main() -> int:
    print("\n1. T1 i tratti escono dal referto")
    report = SimpleNamespace(
        not_applied=[
            {"reason": "captions_tail_uncovered", "source_id": "c01", "seconds": 2.0,
             "spans": [[34.0, 36.0]]},
            {"reason": "captions_tail_uncovered", "source_id": "c01", "seconds": 1.0,
             "spans": [[39.0, 40.0]]},
            {"reason": "tail_filled_with_speech", "source_id": "c01", "seconds": 3.0,
             "spans": [[34.0, 36.0], [39.0, 40.0]]},
            {"reason": "captions_tail_uncovered", "source_id": "c02", "seconds": 0.0,
             "spans": []},
        ]
    )
    spans = auto._tail_caption_spans(report)
    check("T1 un solo source con tratti validi", list(spans) == ["c01"], list(spans))
    check("T1 due tratti, in ordine", spans["c01"] == [(34.0, 36.0), (39.0, 40.0)], spans["c01"])
    check("T1 referto senza motivo: niente", auto._tail_caption_spans(SimpleNamespace(not_applied=[])) == {})

    print("\n2. T2 fusione con offset e senza duplicati")
    base = transcript([(0.0, 0.5, "ciao"), (0.5, 1.0, "a")], [(0.0, 1.0, "ciao a")], "ciao a", 12.0)
    extra = transcript([(0.0, 0.4, "dopo"), (0.4, 0.9, "pausa")], [(0.0, 0.9, "dopo pausa")], "dopo pausa", 6.0)
    merged = auto._merge_transcripts(base, extra, 34.0)
    starts = [round(item["start"], 3) for item in merged.words]
    check("T2 parole in ordine assoluto", starts == [0.0, 0.5, 34.0, 34.4], starts)
    check("T2 stessa parola allo stesso tempo non si duplica", len(merged.words) == 4, len(merged.words))
    again = auto._merge_transcripts(merged, extra, 34.0)
    check("T2 rifondere lo stesso estratto non aggiunge niente", len(again.words) == 4, len(again.words))

    print("\n3. T3 segmenti, testo e durata")
    check(
        "T3 i segmenti seguono le parole",
        [round(item["start"], 3) for item in merged.segments] == [0.0, 34.0],
        merged.segments,
    )
    check("T3 il testo si unisce", merged.text == "ciao a dopo pausa", merged.text)
    check("T3 la durata è la più lontana", abs(merged.duration - 40.0) <= 1e-6, merged.duration)
    check("T3 lingua e modello restano", merged.language == "it" and merged.model == base.model, (merged.language, merged.model))

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
