"""Face boxes must live in the analysis frame, not in the proxy frame.

`detect_faces_tracked` reports pixels of the file it opened; `build_crop_plan`
reads the analysis source dimensions. When detection runs on a 640 px proxy
those are different systems, so the virtual camera follows a box several times
too small. This pins the single conversion point and its invariants.

Run:
    python tests/test_face_coords.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import autoframe, faces  # noqa: E402
from vx.contract import FaceTracks  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:58} {detail}")


def _tracks(samples: list, **extra) -> FaceTracks:
    data = {
        "detected_ratio": 0.5,
        "tracks": [
            {
                "id": 1,
                "start": samples[0]["t"],
                "end": samples[-1]["t"],
                "samples": samples,
                "mean_area": 1600.0,
                "duration_s": 1.0,
                "centrality": 0.5,
                "score": 1.0,
                "persistence": 1.0,
            }
        ],
        "max_concurrent": 1,
        "framing": "single",
        "detector": "yunet",
        "span_x_ratio": 0.25,
    }
    data.update(extra)
    return FaceTracks(**data)


def main() -> int:
    box = {
        "t": 0.0,
        "x": 100.0,
        "y": 50.0,
        "w": 40.0,
        "h": 40.0,
        "eye_y": 70.0,
        "mar": 0.1,
        "score": 0.9,
    }
    scaled = faces.rescale_tracks(_tracks([box]), 640, 360, 1920, 1080)
    sample = scaled.tracks[0]["samples"][0]
    check("640->1920: x", sample["x"] == 300.0, sample["x"])
    check("640->1920: y", sample["y"] == 150.0, sample["y"])
    check("640->1920: w", sample["w"] == 120.0, sample["w"])
    check("640->1920: h", sample["h"] == 120.0, sample["h"])
    check("640->1920: eye_y", sample["eye_y"] == 210.0, sample["eye_y"])
    check("640->1920: mar e score invariati", sample["mar"] == 0.1, sample["mar"])
    check(
        "640->1920: mean_area riscalata",
        scaled.tracks[0]["mean_area"] == 14400.0,
        scaled.tracks[0]["mean_area"],
    )
    check(
        "640->1920: score ricalcolato",
        scaled.tracks[0]["score"] == 90.0,
        scaled.tracks[0]["score"],
    )
    check(
        "640->1920: ratio invarianti",
        (
            scaled.detected_ratio,
            scaled.span_x_ratio,
            scaled.max_concurrent,
            scaled.framing,
            scaled.detector,
        )
        == (0.5, 0.25, 1, "single", "yunet"),
        scaled.to_dict(),
    )

    edge = {
        "t": 0.0,
        "x": 620.0,
        "y": 340.0,
        "w": 40.0,
        "h": 40.0,
        "eye_y": 350.0,
        "mar": 0.0,
        "score": 0.5,
    }
    clamped = faces.rescale_tracks(_tracks([edge]), 640, 360, 1920, 1080)
    sample = clamped.tracks[0]["samples"][0]
    check(
        "bordo: il box non esce dal frame",
        sample["x"] + sample["w"] <= 1920.0 and sample["y"] + sample["h"] <= 1080.0,
        (sample["x"], sample["y"], sample["w"], sample["h"]),
    )

    same = _tracks([box])
    check(
        "stessa geometria: nessuna conversione",
        faces.rescale_tracks(same, 1920, 1080, 1920, 1080) is same,
    )

    series = [
        {
            "t": round(t * 0.25, 2),
            "x": 300.0,
            "y": 150.0,
            "w": 120.0,
            "h": 120.0,
            "eye_y": 210.0,
            "mar": 0.1,
            "score": 0.9,
        }
        for t in range(5)
    ]
    plan = autoframe.build_crop_plan(_tracks(series), 1920, 1080, aspect="9:16")
    inside = all(
        0.0 <= float(box["x"]) <= plan.src_w - plan.crop_w
        and 0.0 <= float(box["y"]) <= plan.src_h - plan.crop_h
        for box in plan.boxes
    )
    check(
        "crop plan: box dentro i limiti 1920x1080",
        inside and plan.src_w == 1920 and plan.crop_w == 608,
        (plan.src_w, plan.crop_w, len(plan.boxes)),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
