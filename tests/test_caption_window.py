"""La finestra di trascrizione copre la coda che il ponte può aggiungere.

    python tests/test_caption_window.py

W1  sorgente corta (entro 60 s): si legge tutta, così il parlato che riempie la
    coda ha i suoi sottotitoli;
W2  sorgente lunga: resta la finestra del piano (il costo non esplode) e il
    ponte dichiarerà l'eventuale coda non coperta;
W3  sorgente senza durata o non citata: comportamento di prima (max ``src_out``
    o ``None``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, edl_plan  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def _clip(source_id: str, src_out: float) -> SimpleNamespace:
    return SimpleNamespace(source_id=source_id, src_out=src_out, dst_out=src_out)


def _plan(duration: float | None, clips: list, sources: dict | None = None):
    refs = (
        sources
        if sources is not None
        else (
            {
                "c01": edl_plan.SourceRef(
                    path="c01.mp4",
                    sha256="a" * 64,
                    duration=duration,
                )
            }
            if duration is not None
            else {}
        )
    )
    return SimpleNamespace(
        sources=refs,
        video_tracks=(SimpleNamespace(clips=tuple(clips)),),
        audio_tracks=(),
    )


def main() -> int:
    print("\n1. W1 sorgente corta: si legge tutta")
    short = auto._caption_window(_plan(30.0, [_clip("c01", 12.0)]), "c01")
    check("W1 30 s di sorgente -> finestra 30 s", short == 30.0, short)

    print("\n2. W2 sorgente lunga: resta la finestra del piano")
    long = auto._caption_window(_plan(300.0, [_clip("c01", 18.0)]), "c01")
    check("W2 300 s di sorgente -> finestra 18 s", long == 18.0, long)

    print("\n3. W3 casi limite")
    unknown = auto._caption_window(_plan(30.0, [_clip("c01", 12.0)]), "c99")
    check("W3 sorgente non citata -> None", unknown is None, unknown)
    no_duration = auto._caption_window(
        _plan(None, [_clip("c01", 9.0)], sources={"c01": edl_plan.SourceRef(path="c01.mp4", sha256="a" * 64)}),
        "c01",
    )
    check("W3 senza durata -> max src_out", no_duration == 9.0, no_duration)
    multiple = auto._caption_window(
        _plan(45.0, [_clip("c01", 9.0), _clip("c01", 14.0)]), "c01"
    )
    check("W3 corta con più clip -> tutta la sorgente", multiple == 45.0, multiple)

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
