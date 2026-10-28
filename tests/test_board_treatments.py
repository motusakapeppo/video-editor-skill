"""D035: the number of treatments is not a creative limit; what is visible still is."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import board, edl3, registry  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def animation(kinds, cue_span_ms=1200.0):
    cues = [{"start": 0.0, "end": cue_span_ms / 1000.0}] * len(kinds)
    return (
        {
            "per_cue": [
                {"cue": index, "in": kind, "ms": 200.0}
                for index, kind in enumerate(kinds)
            ]
        },
        cues,
    )


def main() -> int:
    kinds = sorted(registry.ANIMATION_CAPABILITIES)
    check("the registry declares at least three treatments", len(kinds) >= 3, kinds)

    for count in (3, len(kinds)):
        sample = kinds[:count]
        payload, cues = animation(sample)
        errors = board.verify_animation(payload, cues)
        check(
            f"{count} distinct treatments are not refused any more",
            not any("trattament" in error for error in errors),
            errors or "no treatment error",
        )

    single = kinds[:1]
    payload, cues = animation(single)
    check(
        "one treatment still produces no error",
        board.verify_animation(payload, cues) == [],
    )

    # the limit that stays: what the viewer sees, at most 3 looks every 10 s
    dense = edl3.Timeline3(
        fps=12,
        size=(320, 568),
        clips=[
            edl3.Clip3(
                id="c1",
                source=str(Path(__file__)),
                src_in=0.0,
                src_out=20.0,
                effects=[edl3.Effect("vignette", {}) for _ in range(4)],
            )
        ],
    )
    check(
        "the visible-effect cap still refuses four looks in one clip",
        len(edl3.check_effect_density(dense)) == 1,
        edl3.check_effect_density(dense),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
