"""Cinematic grammar: 30-degree jump cuts and 180-degree axis crossing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import grammar  # noqa: E402
from vx.edl_plan import (  # noqa: E402
    CameraSpec,
    ClipSegment,
    EDLPlan,
    SourceRef,
    Track,
)


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


PASS = 0
FAIL = 0


def _source(name: str, angle: float, focal: float) -> SourceRef:
    return SourceRef(f"{name}.mp4", (name[0] * 64), 20.0, CameraSpec(angle, focal))


def _plan(
    first: SourceRef,
    second: SourceRef,
    *,
    broll: bool = False,
    third: SourceRef | None = None,
) -> EDLPlan:
    sources = {"src_a": first, "src_b": second}
    clips = [
        ClipSegment("c1", "src_a", 0.0, 5.0, 0.0, 5.0, 0, "main_talking_head"),
        ClipSegment("c2", "src_b", 0.0, 5.0, 5.0, 10.0, 0, "main_talking_head"),
    ]
    duration = 10.0
    if third is not None:
        sources["src_c"] = third
        clips.append(
            ClipSegment("c3", "src_c", 0.0, 5.0, 10.0, 15.0, 0, "main_talking_head")
        )
        duration = 15.0
    tracks = [Track(0, tuple(clips), "main")]
    if broll:
        tracks.append(
            Track(
                1,
                (
                    ClipSegment(
                        "b1",
                        "src_b",
                        0.0,
                        1.5,
                        4.5,
                        6.0,
                        1,
                        "broll_overlay",
                    ),
                ),
                "broll",
            )
        )
    return EDLPlan(
        fps=25.0,
        duration_sec=duration,
        aspect_ratio="9:16",
        resolution=(1080, 1920),
        sources=sources,
        video_tracks=tuple(tracks),
        audio_tracks=(),
    )


def main() -> int:
    bad30 = _plan(_source("a", 0.0, 35.0), _source("b", 10.0, 35.0))
    check("fixture 30 valida", bad30.validate() == [], bad30.validate())
    violations = grammar.validate_cinematic_rules(bad30)
    check("30: una violazione", len(violations) == 1, violations)
    if violations:
        check("30: regola", violations[0].rule == "30_DEGREE_RULE", violations[0].rule)
        check(
            "30: messaggio", "Jump cut" in violations[0].details, violations[0].details
        )
        check(
            "30: taglio al secondo 5",
            abs(violations[0].cut_time_sec - 5.0) < 1e-9,
            violations[0].cut_time_sec,
        )

    fixed = _plan(_source("a", 0.0, 35.0), _source("b", 10.0, 35.0), broll=True)
    check("30: B-roll copre", grammar.validate_cinematic_rules(fixed) == [])

    focal = _plan(_source("a", 0.0, 35.0), _source("b", 0.0, 55.0))
    check("30: focale >= 20mm", grammar.validate_cinematic_rules(focal) == [])

    angle = _plan(_source("a", 0.0, 35.0), _source("b", 30.0, 35.0))
    check("30: angolo >= 30", grammar.validate_cinematic_rules(angle) == [])

    crossing = _plan(_source("a", -45.0, 50.0), _source("b", 45.0, 50.0))
    check("180: fixture valida", crossing.validate() == [], crossing.validate())
    violations = grammar.validate_cinematic_rules(crossing)
    check("180: una violazione", len(violations) == 1, violations)
    if violations:
        check(
            "180: regola", violations[0].rule == "180_DEGREE_RULE", violations[0].rule
        )
        check(
            "180: messaggio",
            "Scavalcamento" in violations[0].details,
            violations[0].details,
        )

    neutral = _plan(
        _source("a", -45.0, 50.0),
        _source("b", 0.0, 50.0),
        third=_source("c", 45.0, 50.0),
    )
    check("180: neutra intermedia", grammar.validate_cinematic_rules(neutral) == [])

    covered = _plan(_source("a", -45.0, 50.0), _source("b", 45.0, 50.0), broll=True)
    check("180: B-roll copre", grammar.validate_cinematic_rules(covered) == [])

    same = EDLPlan(
        fps=25.0,
        duration_sec=10.0,
        aspect_ratio="9:16",
        resolution=(1080, 1920),
        sources={"src_a": _source("a", 0.0, 35.0)},
        video_tracks=(
            Track(
                0,
                (
                    ClipSegment("c1", "src_a", 0.0, 5.0, 0.0, 5.0, 0, "main"),
                    ClipSegment("c2", "src_a", 5.0, 10.0, 5.0, 10.0, 0, "main"),
                ),
                "main",
            ),
        ),
        audio_tracks=(),
    )
    check(
        "stessa sorgente: nessun angolo", grammar.validate_cinematic_rules(same) == []
    )

    try:
        grammar.assert_cinematic_rules(bad30)
    except grammar.GrammarError as exc:
        check("assert solleva GrammarError", len(exc.violations) == 1, exc)
    except Exception as exc:  # noqa: BLE001
        check("assert solleva GrammarError", False, f"{type(exc).__name__}: {exc}")
    else:
        check("assert solleva GrammarError", False, "nessuna eccezione")

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
