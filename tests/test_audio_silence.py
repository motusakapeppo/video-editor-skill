"""Silent audio must not reach loudnorm with measured_I=-inf."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import plan, render  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:58} {detail}")


def report(input_i: object, has_audio: bool = True) -> dict:
    return {
        "source": {
            "width": 1080,
            "height": 1920,
            "duration": 12.0,
            "has_audio": has_audio,
        },
        "analysis": {
            "audio": {
                "loudness": {
                    "input_i": input_i,
                    "input_tp": -1.0,
                    "input_lra": 5.0,
                }
            }
        },
    }


def brief() -> dict:
    return {"format": "9:16", "hook": "none", "subtitles": "none"}


def ids(payload: dict) -> set:
    return {f.get("id") for f in payload.get("findings") or [] if isinstance(f, dict)}


def loudnorm(built):
    return next((t for t in built.treatments if t.get("name") == "loudnorm"), None)


def main() -> int:
    inf = report("-inf")
    inf_plan = plan.build_plan(inf, brief())
    check("input_i=-inf: audio.silent", "audio.silent" in ids(inf), sorted(ids(inf)))
    check(
        "input_i=-inf: nessun loudness.off",
        "audio.loudness.off" not in ids(inf),
        sorted(ids(inf)),
    )
    check("input_i=-inf: nessun loudnorm", loudnorm(inf_plan) is None)

    quiet = report(-75.5)
    quiet_plan = plan.build_plan(quiet, brief())
    check(
        "input_i=-75.5: audio.silent", "audio.silent" in ids(quiet), sorted(ids(quiet))
    )
    check("input_i=-75.5: nessun loudnorm", loudnorm(quiet_plan) is None)

    valid = report(-18.2)
    valid_plan = plan.build_plan(valid, brief())
    check(
        "input_i=-18.2: loudness.off presente",
        "audio.loudness.off" in ids(valid),
        sorted(ids(valid)),
    )
    check("input_i=-18.2: nessun audio.silent", "audio.silent" not in ids(valid))
    check("input_i=-18.2: loudnorm presente", loudnorm(valid_plan) is not None)

    muted = report(None, has_audio=False)
    plan.build_plan(muted, brief())
    check(
        "has_audio=False: audio.silent",
        "audio.silent" in ids(muted),
        sorted(ids(muted)),
    )

    silent = render._silent_loudness
    check("-inf e' silenzio", silent({"input_i": "-inf", "input_tp": "-inf"}) is True)
    check("nan e' silenzio", silent({"input_i": "nan"}) is True)
    check("-75.5 e' silenzio", silent({"input_i": "-75.5", "input_tp": "-60"}) is True)
    check(
        "-18.2 non e' silenzio",
        silent({"input_i": "-18.2", "input_tp": "-2.1"}) is False,
    )

    args = render.loudnorm_two_pass(
        {
            "input_i": "-inf",
            "input_tp": "-inf",
            "input_lra": "0.0",
            "input_thresh": "-70",
        }
    )
    check("two-pass non passa measured_I=-inf", "measured_I" not in args[1], args[1])

    args = render.loudnorm_two_pass(
        {
            "input_i": "-18.2",
            "input_tp": "-2.1",
            "input_lra": "5.0",
            "input_thresh": "-28.0",
        }
    )
    check(
        "two-pass su audio valido usa measured_I",
        "measured_I=-18.20" in args[1],
        args[1],
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
