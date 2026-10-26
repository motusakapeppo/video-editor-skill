"""Semantic B-roll: keyword rules become anchored, deduplicated candidates."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import cutplan  # noqa: E402
from vx.edl import Edl, EdlShot  # noqa: E402
from vx.edl_plan import SourceRef  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def _words(*pairs) -> list:
    return [{"word": word, "start": start, "end": end} for word, start, end in pairs]


def _sources() -> dict:
    return {
        "src_01": SourceRef("main.mp4", "a" * 64, 20.0),
        "src_02": SourceRef("broll.mp4", "b" * 64, 20.0),
    }


def _edl() -> Edl:
    shots = [
        EdlShot("main", 0.0, 10.0, 10.0, 0.0, 10.0, 250, "bar", 1.5),
        EdlShot("main", 10.0, 20.0, 10.0, 10.0, 20.0, 250, "bar", 1.5),
    ]
    return Edl(
        fps=25.0,
        shots=shots,
        total=20.0,
        frames=500,
        rhythmic_shots=2,
        inaction_shots=0,
    )


def main() -> int:
    words = _words(
        ("Il", 0.0, 0.2),
        ("vino", 0.2, 0.6),
        ("e", 0.6, 0.8),
        ("la", 0.8, 1.0),
        ("città", 1.0, 1.4),
    )
    rule = cutplan.BrollRule("vino", "src_02", 0.0, 1.0)
    got = cutplan.select_broll(words, [rule], source_id="src_01")
    check("una parola chiave -> un insert", len(got) == 1, len(got))
    if got:
        candidate = got[0]
        check("at e' il punto medio", abs(candidate.at - 0.4) < 1e-9, candidate.at)
        check("span = parola", (candidate.t0, candidate.t1) == (0.2, 0.6))
        check(
            "witness sulla fonte del dialogo",
            candidate.witness()["source_id"] == "src_01",
        )
        check("footage dal B-roll", candidate.broll_source_id == "src_02")

    accented = cutplan.select_broll(
        _words(("Città", 1.0, 1.4)),
        [cutplan.BrollRule("citta", "src_02", 0.0, 1.0)],
        source_id="src_01",
    )
    check("accenti e maiuscole", len(accented) == 1, len(accented))

    punctuated = cutplan.select_broll(
        _words(("vino,", 0.2, 0.6)),
        [cutplan.BrollRule("vino", "src_02", 0.0, 1.0)],
        source_id="src_01",
    )
    check("punteggiatura ignorata", len(punctuated) == 1, len(punctuated))

    check(
        "nessuna parola -> nessun insert",
        cutplan.select_broll(
            _words(("acqua", 0.0, 0.4)),
            [cutplan.BrollRule("vino", "src_02", 0.0, 1.0)],
            source_id="src_01",
        )
        == [],
    )

    spaced = cutplan.select_broll(
        _words(("vino", 0.0, 0.4), ("vino", 3.0, 3.4), ("vino", 6.0, 6.4)),
        [cutplan.BrollRule("vino", "src_02", 0.0, 1.0, max_uses=2, min_gap_sec=2.0)],
        source_id="src_01",
    )
    check("max_uses rispettato", len(spaced) == 2, len(spaced))
    check(
        "min_gap rispettato",
        len(spaced) == 2 and spaced[1].at - spaced[0].at >= 2.0 - 1e-9,
        [round(item.at, 2) for item in spaced],
    )

    close = cutplan.select_broll(
        _words(("vino", 0.0, 0.4), ("vino", 1.0, 1.4), ("vino", 3.0, 3.4)),
        [cutplan.BrollRule("vino", "src_02", 0.0, 1.0, max_uses=3, min_gap_sec=2.0)],
        source_id="src_01",
    )
    check("il match troppo vicino e' soppresso", len(close) == 2, len(close))

    first_wins = cutplan.select_broll(
        _words(("vino", 0.0, 0.4)),
        [
            cutplan.BrollRule("vino", "src_02", 0.0, 1.0),
            cutplan.BrollRule("vino", "src_03", 1.0, 2.0),
        ],
        source_id="src_01",
    )
    check(
        "la prima regola vince",
        len(first_wins) == 1 and first_wins[0].broll_source_id == "src_02",
        [item.broll_source_id for item in first_wins],
    )

    for label, bad in (
        ("keyword vuota", cutplan.BrollRule("", "src_02", 0.0, 1.0)),
        ("src_out <= src_in", cutplan.BrollRule("vino", "src_02", 1.0, 1.0)),
        ("max_uses 0", cutplan.BrollRule("vino", "src_02", 0.0, 1.0, max_uses=0)),
        (
            "min_gap negativo",
            cutplan.BrollRule("vino", "src_02", 0.0, 1.0, min_gap_sec=-1),
        ),
    ):
        try:
            cutplan.select_broll(words, [bad], source_id="src_01")
        except ValueError:
            check(f"regola rifiutata: {label}", True)
        except Exception as exc:  # noqa: BLE001
            check(f"regola rifiutata: {label}", False, f"{type(exc).__name__}: {exc}")
        else:
            check(f"regola rifiutata: {label}", False, "accettata")

    built = cutplan.build_edl_plan(
        _edl(),
        source_id="src_01",
        sources=_sources(),
        broll=cutplan.select_broll(
            _words(("vino", 3.0, 3.4)),
            [cutplan.BrollRule("vino", "src_02", 0.0, 1.0)],
            source_id="src_01",
        ),
        fps=25.0,
        duration=20.0,
    )
    broll_clips = [
        clip
        for track in built.plan.video_tracks
        for clip in track.clips
        if clip.layer == 1
    ]
    check("regia -> EDL con B-roll", len(broll_clips) == 1, len(broll_clips))
    if broll_clips:
        clip = broll_clips[0]
        check(
            "B-roll al tempo mappato",
            abs(clip.dst_in - 3.2) < 1e-9,
            clip.dst_in,
        )
        check(
            "witness ancora il dialogo",
            built.witness(clip.clip_id)["source_id"] == "src_01",
            built.witness(clip.clip_id),
        )
        check("piano valido", built.plan.validate() == [], built.plan.validate())

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
