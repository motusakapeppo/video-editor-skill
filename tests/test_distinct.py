"""I cancelli di coppia e di copertura: devono saper dire no.

python tests/test_distinct.py
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import brief as brief_mod, prove  # noqa: E402
from vx.ffmpeg import ffmpeg as ff_bin  # noqa: E402

WORK = ROOT / "tests" / "_out" / "distinct"
PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:46} {detail}")


def _brief(**overrides) -> dict:
    """A minimal brief with the fields the pair gates read."""
    b = brief_mod.new_brief("test", "a" * 64, "b" * 64)
    b["measurements"] = {
        "transient_ratio": None,
        "transient_p33": None,
        "transient_p67": None,
        "hue_mass_top3": [
            {"hue": 210, "mass": 0.30},
            {"hue": 30, "mass": 0.10},
            {"hue": 90, "mass": 0.05},
        ],
        "ink_coverage_pct": None,
        "subject_offset": {"x": 0.10, "y": 0.0},
        "onsets_per_s": 2.0,
        "first_speech_onset_s": 5.0,
        "first3s_motion": 20.0,
        "face_present": True,
        "luma_p50": 0.30,
        "contrast": 0.70,
    }
    b["choices"]["palette"].update(
        value="dual", applied=True, inaction=False, confidence=0.9
    )
    b["choices"]["grade"].update(
        value="none", applied=False, inaction=True, confidence=0.5
    )
    b["choices"]["animation"].update(
        name="wipe", count=1, applied=True, inaction=False, confidence=0.9
    )
    b["choices"]["cut_density"].update(
        value=2.0, applied=True, inaction=False, confidence=0.9
    )
    b["choices"]["composition"].update(
        value={"x": 0.10, "y": 0.0}, applied=True, inaction=False, confidence=0.9
    )
    b["choices"]["typography"].update(
        value=0, applied=False, inaction=True, confidence=0.5
    )
    for key, value in overrides.items():
        axis, field = key.split(".")
        b["choices"][axis][field] = value
    return b


def _render(path: Path, args: list[str]) -> Path:
    import subprocess

    proc = subprocess.run(
        [ff_bin(), "-hide_banner", "-loglevel", "error", "-y", *args, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 or not path.is_file():
        raise RuntimeError(f"render di prova fallito: {proc.stderr[-200:]}")
    return path


def _synthetic_videos() -> dict:
    WORK.mkdir(parents=True, exist_ok=True)
    size = "320x568"
    base = [
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={size}:r=25:d=2",
        "-c:v",
        "libx264",
        "-crf",
        "30",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
    ]
    return {
        # una piccola tavoletta bianca: ~0.6% di inchiostro, dentro la banda
        "in_band": _render(
            WORK / "in_band.mp4",
            [*base, "-vf", "drawbox=x=100:y=400:w=48:h=24:color=white:t=fill"],
        ),
        "all_white": _render(
            WORK / "all_white.mp4",
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c=white:s={size}:r=25:d=2",
                "-c:v",
                "libx264",
                "-crf",
                "30",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
            ],
        ),
        "all_black": _render(WORK / "all_black.mp4", base),
    }


def main() -> int:
    videos = _synthetic_videos()

    same = _brief()
    other = _brief(
        **{
            "palette.value": "neutral",
            "palette.applied": False,
            "palette.inaction": True,
            "palette.confidence": 0.5,
            "cut_density.value": 3.0,
            "composition.value": {"x": 0.0, "y": 0.0},
        }
    )
    result = prove.distinct_surface(same, other)
    check(
        "coppia con geometria disponibile: geometry True",
        result["geometry_available"] and result["families"]["geometry"],
        str(result["families"]),
    )
    check(
        "coppia con geometria: ok True",
        result["ok"] is True,
        f"colour={result['families']['colour']} rhythm={result['families']['rhythm']}",
    )

    not_applied = _brief(
        **{
            "composition.applied": False,
            "composition.inaction": True,
            "composition.confidence": 0.5,
            "composition.value": {"x": 0.0, "y": 0.0},
        }
    )
    result = prove.distinct_surface(same, not_applied)
    check(
        "offset diversi ma composizione non applicata: geometry False",
        result["geometry_available"] and result["families"]["geometry"] is False,
        str(result["families"]),
    )

    close = _brief(**{"composition.value": {"x": -0.0774, "y": 0.0}})
    far = _brief(**{"composition.value": {"x": -0.1537, "y": 0.0}})
    result = prove.distinct_surface(far, close)
    check(
        "entrambe applicate con diff 0.0763: geometry True",
        result["geometry_available"] and result["families"]["geometry"] is True,
        str(result["families"]),
    )

    no_offset = _brief(**{"typography.value": 8, "composition.value": None})
    result = prove.distinct_surface(same, no_offset)
    check(
        "la tipografia non sostituisce la geometria",
        result["ok"] is False and not result["geometry_available"],
        f"typography={result['families']['typography']} ok={result['ok']}",
    )

    result = prove.distinct_surface(same, copy.deepcopy(same))
    check("coppia identica rifiutata", result["ok"] is False, str(result["families"]))

    hook_same_anim = _brief(**{"animation.name": "fade"})
    check(
        "hook_distinct richiede animazione diversa (P1)",
        prove.hook_distinct(same, hook_same_anim) is False,
        f"hook {prove.hook_type(same)} vs {prove.hook_type(hook_same_anim)}",
    )

    hook_same_type = copy.deepcopy(same)
    hook_same_type["measurements"]["first3s_motion"] = 1.0  # sotto p67 -> title
    check(
        "hook_distinct richiede hook_type diverso (P1)",
        prove.hook_distinct(same, hook_same_type) is False,
        f"hook {prove.hook_type(same)} vs {prove.hook_type(hook_same_type)}",
    )

    hook_other = _brief(
        **{
            "animation.name": "none",
            "animation.count": 0,
            "animation.applied": False,
            "animation.inaction": True,
            "animation.confidence": 0.5,
        }
    )
    hook_other["measurements"]["first3s_motion"] = 1.0
    check(
        "hook_distinct vero con tipo e animazione diversi",
        prove.hook_distinct(same, hook_other) is True,
        f"hook {prove.hook_type(same)} vs {prove.hook_type(hook_other)}",
    )

    coverage = prove.coverage_gate(same, videos["in_band"])
    check(
        "copertura nella banda passa",
        coverage["ok"] is True,
        f"ink {coverage['ink_pct']}%",
    )
    check(
        "overlay == ink (P5)",
        coverage["overlay_pct"] == coverage["ink_pct"],
        str(coverage["overlay_pct"]),
    )

    white = copy.deepcopy(same)
    white["measurements"]["luma_p50"] = 0.10
    coverage = prove.coverage_gate(white, videos["all_white"])
    check(
        "copertura sopra 2% rifiutata",
        coverage["ok"] is False,
        f"ink {coverage['ink_pct']}%",
    )

    coverage = prove.coverage_gate(same, videos["all_black"])
    check(
        "copertura sotto 0.30% rifiutata",
        coverage["ok"] is False,
        f"ink {coverage['ink_pct']}%",
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
