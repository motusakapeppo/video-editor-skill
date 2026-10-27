"""F1-T04 (engine half) -- two music beds, each with its own ducking.

    python tests/test_f1_tracks.py

Today the model carries one `Optional[DuckingSpec]` keyed by role, and the renderer
splits clips into exactly two buckets (`music if role == "music" else dialogue`),
then builds one `[music]` bus and applies one `sidechaincompress`. Two beds are
therefore merged and share one ducking.

The contract this file pins:

a. `EDLPlan.ducking_tracks` carries one spec per bed, additive next to the global
   `ducking`, and `validate()` accepts a plan with two beds;
b. the graph has one music bus per bed and one `sidechaincompress` per bed, with
   that bed's own threshold -- not two beds sharing one chain;
c. the final `amix` takes dialogue plus every bed (`inputs=3` for two beds);
d. the single-bed graph is untouched: one `[music]` bus, one sidechain, `inputs=2`.

The single-bed case is the regression: it is pinned today by
`tests/test_render_audio_buses.py` and must stay byte-identical.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl_plan, render  # noqa: E402

PASS = 0
FAIL = 0

THRESHOLDS = re.compile(r"sidechaincompress=[^[]*?threshold=([0-9.]+)")


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def base(roles: tuple[str, ...]):
    source = {"c01": edl_plan.SourceRef(path="c01.mp4", sha256="a" * 64, duration=6.0)}
    video = edl_plan.ClipSegment(
        clip_id="v0_0",
        source_id="c01",
        src_in=0.0,
        src_out=6.0,
        dst_in=0.0,
        dst_out=6.0,
        layer=0,
        role="main_talking_head",
    )
    dialogue = edl_plan.ClipSegment(
        clip_id="a0_0",
        source_id="c01",
        src_in=0.0,
        src_out=6.0,
        dst_in=0.0,
        dst_out=6.0,
        layer=0,
        role="dialogue",
    )
    beds = tuple(
        edl_plan.Track(
            index + 1,
            (
                edl_plan.ClipSegment(
                    clip_id=f"a{index + 1}_0",
                    source_id="c01",
                    src_in=0.0,
                    src_out=6.0,
                    dst_in=0.0,
                    dst_out=6.0,
                    layer=0,
                    role=role,
                    gain_db=-8.0,
                ),
            ),
            role,
        )
        for index, role in enumerate(roles)
    )
    return (
        edl_plan.EDLPlan(
            fps=25.0,
            duration_sec=6.0,
            aspect_ratio="9:16",
            resolution=(1080, 1920),
            sources=source,
            video_tracks=(edl_plan.Track(0, (video,), "a_roll"),),
            audio_tracks=(edl_plan.Track(0, (dialogue,), "dialogue"),) + beds,
            ducking=edl_plan.DuckingSpec(),
        ),
        beds,
    )


def two_bed_plan():
    plan, _ = base(("music_a", "music_b"))
    return edl_plan.EDLPlan(
        fps=plan.fps,
        duration_sec=plan.duration_sec,
        aspect_ratio=plan.aspect_ratio,
        resolution=plan.resolution,
        sources=dict(plan.sources),
        video_tracks=plan.video_tracks,
        audio_tracks=plan.audio_tracks,
        ducking=None,
        ducking_tracks=(
            edl_plan.DuckingSpec(target_role="music_a", threshold=0.02, ratio=4.0),
            edl_plan.DuckingSpec(target_role="music_b", threshold=0.1, ratio=2.0),
        ),
    )


def main() -> int:
    try:
        plan = two_bed_plan()
    except TypeError as exc:
        check(
            "0. EDLPlan carries per-bed ducking (ducking_tracks)",
            False,
            f"{type(exc).__name__}: {exc}",
        )
        print(f"\n{PASS} PASS / {FAIL} FAIL")
        return 1

    check("0. the plan validates", plan.validate() == [], plan.validate()[:2])
    try:
        restored = edl_plan.EDLPlan.from_dict(plan.to_dict())
        wired = (
            restored.validate() == []
            and [item.target_role for item in restored.ducking_tracks]
            == ["music_a", "music_b"]
            and [float(item.threshold) for item in restored.ducking_tracks]
            == [0.02, 0.1]
        )
        detail = [item.target_role for item in restored.ducking_tracks]
    except Exception as exc:  # noqa: BLE001 - a dropped field is the failure
        wired = False
        detail = f"{type(exc).__name__}: {exc}"
    check("0. ducking_tracks survives the wire round-trip", wired, detail)

    script = render.build_edl_filtergraph(plan, {"c01": 0}, has_audio=True)
    found = sorted(float(match) for match in THRESHOLDS.findall(script))
    check(
        "a. one sidechaincompress per bed",
        script.count("sidechaincompress=") == 2,
        script.count("sidechaincompress="),
    )
    check(
        "b. each bed keeps its own threshold",
        found == [0.02, 0.1],
        found,
    )
    check(
        "b. the two beds are separate buses",
        "[music_a]" in script and "[music_b]" in script,
        [label for label in ("[music_a]", "[music_b]") if label in script],
    )
    check(
        "c. the final mix takes dialogue plus both beds",
        "amix=inputs=3" in script,
        [line for line in script.split(";") if "amix=" in line][-1:],
    )

    single, _ = base(("music",))
    single_script = render.build_edl_filtergraph(single, {"c01": 0}, has_audio=True)
    check(
        "d. the single-bed graph is unchanged",
        single_script.count("sidechaincompress=") == 1
        and "amix=inputs=2" in single_script
        and "[music]" in single_script,
        (
            single_script.count("sidechaincompress="),
            "amix=inputs=2" in single_script,
        ),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
