"""Gate 1 multi-source: every clip is checked against its own source duration."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import contract, gates  # noqa: E402
from vx.edl_plan import ClipSegment, EDLPlan, SourceRef, Track  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def _plan() -> EDLPlan:
    sources = {
        "src_A": SourceRef("a.mp4", "a" * 64),
        "src_B": SourceRef("b.mp4", "b" * 64),
    }
    video = (
        Track(
            0,
            (
                ClipSegment(
                    "c1", "src_A", 0.0, 10.0, 0.0, 10.0, 0, "main_talking_head"
                ),
                ClipSegment(
                    "c2", "src_B", 0.0, 2.0, 10.0, 12.0, 0, "main_talking_head"
                ),
            ),
            "main",
        ),
    )
    audio = (
        Track(
            0,
            (
                ClipSegment("a1", "src_A", 0.0, 10.0, 0.0, 10.0, 0, "dialogue"),
                ClipSegment("a2", "src_B", 0.0, 2.0, 10.0, 12.0, 0, "dialogue"),
            ),
            "dialogue",
        ),
    )
    return EDLPlan(
        fps=25.0,
        duration_sec=20.0,
        aspect_ratio="9:16",
        resolution=(1080, 1920),
        sources=sources,
        video_tracks=video,
        audio_tracks=audio,
    )


def _failure(plan: EDLPlan, media: dict) -> str:
    try:
        gates.validate_gate1_durations(plan, media)
    except contract.QCFailure as exc:
        return json.dumps(exc.result, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return f"WRONG EXCEPTION {type(exc).__name__}: {exc}"
    return ""


def main() -> int:
    plan = _plan()
    errors = plan.validate()
    check("fixture valida", errors == [], errors)

    valid = {
        "src_A": {"format": {"duration": 10.0}},
        "src_B": {"format": {"duration": 2.0}},
    }
    try:
        result = gates.validate_gate1_durations(plan, valid)
    except Exception as exc:  # noqa: BLE001
        check("mappa valida accettata", False, f"{type(exc).__name__}: {exc}")
    else:
        check("mappa valida accettata", result.ok, result.to_dict())

    text = _failure(
        plan,
        {
            "src_A": {"format": {"duration": 10.0}},
            "src_B": {"format": {"duration": 1.0}},
        },
    )
    check("src_out oltre la sorgente B fallisce", bool(text), text[:80])
    check("il messaggio nomina la sorgente", "src_B" in text, text[-160:])
    check("il messaggio nomina il clip", "c2" in text, text[-160:])
    check("il messaggio dice 'exceeds'", "exceeds" in text, text[-160:])

    flat = {
        "src_A": {"duration": 10.0},
        "src_B": {"duration": 2.0},
    }
    try:
        gates.validate_gate1_durations(plan, flat)
    except Exception as exc:  # noqa: BLE001
        check("durata piatta accettata", False, f"{type(exc).__name__}: {exc}")
    else:
        check("durata piatta accettata", True)

    missing = _failure(plan, {"src_A": {"format": {"duration": 10.0}}})
    check("sorgente mancante fallisce", bool(missing), missing[:80])
    check(
        "la mancanza nomina media_info_map", "media_info_map" in missing, missing[-160:]
    )
    check("la mancanza nomina src_B", "src_B" in missing, missing[-160:])

    audio_plan = _plan()
    audio_plan = EDLPlan(
        fps=audio_plan.fps,
        duration_sec=audio_plan.duration_sec,
        aspect_ratio=audio_plan.aspect_ratio,
        resolution=audio_plan.resolution,
        sources=audio_plan.sources,
        video_tracks=audio_plan.video_tracks,
        audio_tracks=(
            Track(
                0,
                (
                    ClipSegment("a1", "src_A", 0.0, 10.0, 0.0, 10.0, 0, "dialogue"),
                    ClipSegment("a2", "src_B", 0.0, 2.0, 10.0, 12.0, 0, "dialogue"),
                ),
                "dialogue",
            ),
        ),
    )
    audio_text = _failure(
        audio_plan,
        {
            "src_A": {"format": {"duration": 10.0}},
            "src_B": {"format": {"duration": 1.0}},
        },
    )
    check(
        "anche l'audio e' controllato", "audio clip a2" in audio_text, audio_text[-160:]
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
