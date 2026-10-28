"""F27-T05 — a ``preset_ref`` reaches the rendered frame, proven by sha256.

    python tests/test_preset_render.py

D034: a preset is a starting point. F27-T03 proved the merge; this file proves
the merge *reaches the pixels*, with real ffmpeg renders at 320x568 from one
synthetic source and a sha256 of every extracted frame:

a. the same clip rendered with no preset and with a preset produces different
   frames, and the plan/report say which effect was synthesized;
b. a preset with no overrides renders byte-identically to the same effect
   written by hand with the preset's base values -- the merge is faithful;
c. two different presets produce different frames;
d. an override inside the exposed range changes the frame, while an override
   equal to the base changes nothing;
e. a clip that declares a preset and its own effect of the same kind is refused
   with ``PRESET_EFFECT_CONFLICT`` by ``validate`` and by ``compile_timeline``,
   and a clip may still declare effects of other kinds, which apply after the
   preset's;
f. every one of the fifteen presets changes the frame relative to the same
   effect at its own registry defaults -- a preset that changes nothing fails
   loudly, row by row;
g. a document with no preset produces the pre-change graph, byte for byte.

The golden document (no presets) is untouched: ``tests/test_edl3_golden.py``
still matches its 34 frames.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, edl3_render, presets  # noqa: E402
from vx.ffmpeg import run_ff  # noqa: E402

PASS = 0
FAIL = 0
FPS = 12
WIDTH, HEIGHT = 320, 568
#: The two moments a render is compared at: early enough for a temporal effect
#: to have moved, late enough that a crop window has too.
TIMES = (0.25, 0.75)
CEILING = 240.0

Signature = Tuple[str, ...]
#: tag -> (frame signature, render report, compile plan), so one document renders once.
RENDERS: Dict[str, Tuple[Signature, Dict[str, Any], Dict[str, Any]]] = {}


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:66} {detail}")


def code_of(call: Callable[[], Any]) -> str:
    try:
        call()
    except Exception as exc:  # noqa: BLE001 - the code, not the type, is asserted
        return f"{type(exc).__name__}:{getattr(exc, 'code', 'NO_CODE')}"
    return ""


# ------------------------------------------------------------------ fixtures


def synthetic_source(work: Path) -> Path:
    """The one source every render in this file reads: a textured, moving card."""
    output = work / "source.mp4"
    proc = run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s={WIDTH}x{HEIGHT}:r={FPS}:d=1.5",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "1",
            str(output),
        ],
        loglevel="error",
    )
    if not proc.ok or not output.exists():
        raise RuntimeError(f"synthetic source failed: {proc.tail()}")
    return output


def frame_sha(work: Path, video: Path, t: float, name: str) -> str:
    """sha256 of one decoded rgb24 frame, so the proof is byte-level."""
    path = work / f"{name}.rgb"
    if path.exists():
        path.unlink()
    proc = run_ff(
        [
            "-ss",
            f"{t:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-y",
            str(path),
        ],
        loglevel="error",
    )
    if not proc.ok or not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"frame at {t:.3f}s of {video.name} failed: {proc.tail()}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def document(clips: List[Dict[str, Any]]) -> edl3.Timeline3:
    return edl3.Timeline3.from_dict(
        {
            "version": edl3.PRESET_APPLIED_VERSION,
            "fps": FPS,
            "size": [WIDTH, HEIGHT],
            "clips": clips,
        }
    )


def clip(cid: str, source: Any, **extra: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": cid,
        "source": str(source),
        "src_in": 0.0,
        "src_out": 1.0,
    }
    data.update(extra)
    return data


def rendered(
    work: Path, tag: str, timeline: edl3.Timeline3
) -> Tuple[Signature, Dict[str, Any], Dict[str, Any]]:
    """Render once per tag, and return its frame signatures, report and plan."""
    if tag in RENDERS:
        return RENDERS[tag]
    output = work / f"{tag}.mp4"
    report = edl3_render.render(timeline, output, workdir=work / tag)
    signature = tuple(
        frame_sha(work, output, t, f"{tag}_{index}")
        for index, t in enumerate(TIMES)
    )
    plan = edl3_render.compile_timeline(timeline)
    RENDERS[tag] = (signature, report, plan)
    return RENDERS[tag]


def short(signature: Signature) -> str:
    return "/".join(item[:12] for item in signature)


# --------------------------------------------------------------------- main


def main() -> int:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vx_preset_render_") as tmp:
        work = Path(tmp)
        try:
            run_checks(work)
        except Exception:  # noqa: BLE001 - a crash is a failing test, never a skip
            traceback.print_exc()
            check("harness completed without an unexpected exception", False)

    elapsed = time.monotonic() - started
    print("\n-- sha256 pairs (frame at 0.25s / 0.75s)")
    for tag in (
        "plain",
        "slow_push",
        "hand_zoompan",
        "slow_push_120",
        "slow_push_same",
        "lens_wide",
        "profile_clean",
    ):
        if tag in RENDERS:
            print(f"  {tag:16} {'  '.join(RENDERS[tag][0])}")
    check(
        f"suite stays under the {CEILING:g} s ceiling",
        elapsed < CEILING,
        f"{elapsed:.1f}s",
    )
    print(f"\n{PASS} PASS / {FAIL} FAIL  ({elapsed:.1f}s)")
    return 0 if FAIL == 0 else 1


def run_checks(work: Path) -> None:
    source = synthetic_source(work)
    run_preset_changes_frame(work, source)
    run_merge_is_faithful(work, source)
    run_two_presets_differ(work, source)
    run_override(work, source)
    run_conflict(work, source)
    run_every_preset_moves(work, source)
    run_no_preset_untouched(work, source)


# ------------------------------------------------------------------ (a) ---


def run_preset_changes_frame(work: Path, source: Path) -> None:
    print("\n-- (a) a preset_ref changes the rendered frame")
    plain = rendered(work, "plain", document([clip("c1", source)]))
    look = rendered(
        work, "slow_push", document([clip("c1", source, preset_ref="slow_push")])
    )
    check(
        "(a) no preset and slow_push render different frames",
        plain[0] != look[0],
        f"{short(plain[0])} vs {short(look[0])}",
    )
    check(
        "(a) both extracted frames differ, not just one",
        plain[0][0] != look[0][0] and plain[0][1] != look[0][1],
        f"{short(plain[0])} vs {short(look[0])}",
    )
    check(
        "(a) the graph gains exactly one zoompan node",
        look[2]["filter_script"].count("zoompan=") == 1
        and "zoompan=" not in plain[2]["filter_script"],
        look[2]["filter_script"].count("zoompan="),
    )
    check(
        "(a) the plan records the preset and an empty change set",
        look[2]["clips"][0].get("preset")
        == {"preset": "slow_push", "changed": {}},
        look[2]["clips"][0].get("preset"),
    )
    check(
        "(a) the routing table names the synthesized effect's mechanism",
        any(
            row["where"] == "clips[0].preset_ref"
            and row["effect"] == "zoompan"
            and row["chosen"] == "filter"
            for row in look[2]["backends"]
        ),
        [row for row in look[2]["backends"] if row["where"].endswith("preset_ref")],
    )
    check(
        "(a) a clip without a preset carries no preset record",
        "preset" not in plain[2]["clips"][0],
        sorted(plain[2]["clips"][0]),
    )


# ------------------------------------------------------------------ (b) ---


def run_merge_is_faithful(work: Path, source: Path) -> None:
    print("\n-- (b) the merge is faithful: preset base == the same effect by hand")
    look = rendered(
        work, "slow_push", document([clip("c1", source, preset_ref="slow_push")])
    )
    hand = rendered(
        work,
        "hand_zoompan",
        document(
            [
                clip(
                    "c1",
                    source,
                    effects=[{"kind": "zoompan", "params": {"zoom": 1.12}}],
                )
            ]
        ),
    )
    check(
        "(b) the preset render and the hand-written base are byte-identical",
        look[0] == hand[0],
        f"{short(look[0])} vs {short(hand[0])}",
    )
    check(
        "(b) the 0.25s frame hashes match",
        look[0][0] == hand[0][0],
        f"{look[0][0]}",
    )
    check(
        "(b) the 0.75s frame hashes match",
        look[0][1] == hand[0][1],
        f"{look[0][1]}",
    )
    check(
        "(b) the two filter graphs are identical, character for character",
        look[2]["filter_script"] == hand[2]["filter_script"],
        repr(look[2]["filter_script"]),
    )
    check(
        "(b) the template received the preset's base value",
        "zoompan=z=1.12:" in look[2]["filter_script"],
        [
            fragment
            for fragment in look[2]["filter_script"].split(",")
            if fragment.startswith("zoompan=")
        ],
    )


# ------------------------------------------------------------------ (c) ---


def run_two_presets_differ(work: Path, source: Path) -> None:
    print("\n-- (c) two different presets produce different frames")
    look = rendered(
        work, "slow_push", document([clip("c1", source, preset_ref="slow_push")])
    )
    wide = rendered(
        work, "lens_wide", document([clip("c1", source, preset_ref="lens_wide")])
    )
    clean = rendered(
        work,
        "profile_clean",
        document([clip("c1", source, preset_ref="profile_clean")]),
    )
    check(
        "(c) slow_push and lens_wide differ",
        look[0] != wide[0],
        f"{short(look[0])} vs {short(wide[0])}",
    )
    check(
        "(c) slow_push and profile_clean differ",
        look[0] != clean[0],
        f"{short(look[0])} vs {short(clean[0])}",
    )
    check(
        "(c) lens_wide and profile_clean differ",
        wide[0] != clean[0],
        f"{short(wide[0])} vs {short(clean[0])}",
    )
    check(
        "(c) each graph names its own effect and only its own",
        "lenscorrection=" in wide[2]["filter_script"]
        and "curves=master=" in clean[2]["filter_script"]
        and "lenscorrection=" not in clean[2]["filter_script"]
        and "curves=master=" not in wide[2]["filter_script"],
        [wide[2]["clips"][0]["preset"], clean[2]["clips"][0]["preset"]],
    )


# ------------------------------------------------------------------ (d) ---


def run_override(work: Path, source: Path) -> None:
    print("\n-- (d) an override inside the exposed range reaches the frame")
    look = rendered(
        work, "slow_push", document([clip("c1", source, preset_ref="slow_push")])
    )
    pushed = rendered(
        work,
        "slow_push_120",
        document(
            [
                clip(
                    "c1",
                    source,
                    preset_ref="slow_push",
                    exposed_params={"zoom": 1.2},
                )
            ]
        ),
    )
    same = rendered(
        work,
        "slow_push_same",
        document(
            [
                clip(
                    "c1",
                    source,
                    preset_ref="slow_push",
                    exposed_params={"zoom": 1.12},
                )
            ]
        ),
    )
    check(
        "(d) zoom 1.2 changes the frame relative to the base preset",
        pushed[0] != look[0],
        f"{short(look[0])} vs {short(pushed[0])}",
    )
    check(
        "(d) the graph carries the override, not the base",
        "zoompan=z=1.2:" in pushed[2]["filter_script"]
        and "zoompan=z=1.12" not in pushed[2]["filter_script"],
        [line for line in pushed[2]["filter_script"].splitlines() if "zoompan" in line],
    )
    check(
        "(d) the render report records the preset and the changed key",
        pushed[1]["clips"][0]["preset"]
        == {"preset": "slow_push", "changed": {"zoom": 1.2}},
        pushed[1]["clips"][0]["preset"],
    )
    check(
        "(d) the compile plan records the same provenance",
        pushed[2]["clips"][0]["preset"]
        == {"preset": "slow_push", "changed": {"zoom": 1.2}},
        pushed[2]["clips"][0]["preset"],
    )
    check(
        "(d) an override equal to the base renders the base frame, byte for byte",
        same[0] == look[0],
        f"{short(same[0])} vs {short(look[0])}",
    )
    check(
        "(d) and it records no change",
        same[2]["clips"][0]["preset"] == {"preset": "slow_push", "changed": {}},
        same[2]["clips"][0]["preset"],
    )


# ------------------------------------------------------------------ (e) ---


def run_conflict(work: Path, source: Path) -> None:
    print("\n-- (e) a preset and the clip's own effect of the same kind are refused")
    conflict = document(
        [
            clip(
                "c1",
                source,
                preset_ref="slow_push",
                effects=[{"kind": "zoompan", "params": {"zoom": 1.3}}],
            )
        ]
    )
    found = conflict.validate()
    check(
        "(e) validate reports PRESET_EFFECT_CONFLICT",
        [problem["code"] for problem in found] == ["PRESET_EFFECT_CONFLICT"],
        [problem["code"] for problem in found],
    )
    check(
        "(e) the refusal points at the preset reference",
        bool(found) and found[0]["where"] == "clips[0].preset_ref",
        found[0]["where"] if found else "",
    )
    check(
        "(e) the refusal names the preset, the kind and the competing effect",
        bool(found)
        and "'slow_push'" in found[0]["detail"]
        and "'zoompan'" in found[0]["detail"]
        and "effects[0]" in found[0]["detail"],
        found[0]["detail"] if found else "",
    )
    code = code_of(lambda: edl3_render.compile_timeline(conflict))
    check(
        "(e) compile_timeline refuses it with the same code",
        code == "CompileError:PRESET_EFFECT_CONFLICT",
        code,
    )
    check(
        "(e) the code is hard, so the document never reaches the encoder",
        "PRESET_EFFECT_CONFLICT" in edl3_render.HARD_CODES,
        sorted(edl3_render.HARD_CODES),
    )
    curves = document(
        [
            clip(
                "c1",
                source,
                preset_ref="profile_clean",
                effects=[{"kind": "curves", "params": {"mid": 0.4}}],
            )
        ]
    )
    check(
        "(e) the conflict is judged by kind, not by zoompan",
        [problem["code"] for problem in curves.validate()]
        == ["PRESET_EFFECT_CONFLICT"],
        [problem["code"] for problem in curves.validate()],
    )

    beside = document(
        [
            clip(
                "c1",
                source,
                preset_ref="lens_wide",
                effects=[{"kind": "hueshift", "params": {"angle": 40.0}}],
            )
        ]
    )
    check(
        "(e) a clip may declare effects of other kinds beside its preset",
        beside.validate() == [],
        beside.validate(),
    )
    plan = edl3_render.compile_timeline(beside)
    script = plan["filter_script"]
    check(
        "(e) the preset's effect is emitted before the clip's own",
        "lenscorrection=" in script
        and "hue=h=40" in script
        and script.index("lenscorrection=") < script.index("hue=h=40"),
        (script.index("lenscorrection="), script.index("hue=h=40")),
    )
    check(
        "(e) the plan records both, and only the preset as a preset",
        plan["clips"][0]["preset"] == {"preset": "lens_wide", "changed": {}},
        plan["clips"][0]["preset"],
    )


# ------------------------------------------------------------------ (f) ---


def run_every_preset_moves(work: Path, source: Path) -> None:
    print("\n-- (f) every preset changes the frame relative to its effect's defaults")
    print(f"  {'preset':<20} {'effect':<14} {'result':<8} base vs default params")
    rows = 0
    for name in presets.names():
        preset = presets.PRESETS[name]
        defaults = edl3.EFFECT_REGISTRY[preset.effect].default_params()
        applied = rendered(
            work, f"preset_{name}", document([clip("c1", source, preset_ref=name)])
        )
        plain = rendered(
            work,
            f"defaults_{name}",
            document(
                [
                    clip(
                        "c1",
                        source,
                        effects=[{"kind": preset.effect, "params": {}}],
                    )
                ]
            ),
        )
        moved = applied[0] != plain[0]
        rows += bool(moved)
        print(
            f"  {name:<20} {preset.effect:<14} "
            f"{'DIFFERS' if moved else 'SAME':<8} {preset.base} vs {defaults}"
        )
        check(
            f"(f) {name} renders a different frame than {preset.effect} defaults",
            moved,
            f"{short(applied[0])} vs {short(plain[0])}",
        )
        check(
            f"(f) {name} declares values that differ from the registry defaults",
            preset.base != defaults,
            f"{preset.base} vs {defaults}",
        )
    check(
        "(f) all fifteen declared presets were checked",
        rows == len(presets.names()) == 15,
        f"{rows} of {len(presets.names())}",
    )
    problems = {
        name: detail
        for name, detail in presets.check_all().items()
        if detail
    }
    check("(f) the registry itself validates clean", problems == {}, problems)
    wrong = [
        name
        for name, preset in presets.PRESETS.items()
        if edl3.EFFECT_REGISTRY[preset.effect].backend != edl3.BACKEND_FILTER
    ]
    check(
        "(f) every preset instantiates a filter-backend effect",
        wrong == [],
        wrong,
    )


# ------------------------------------------------------------------ (g) ---


def expected_direct(index: int, label: str) -> str:
    """The pre-change construction for a clip with no transform effect."""
    return (
        f"[{index}:v]setpts=PTS-STARTPTS,"
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease:flags=bicubic,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
        f"[{label}]"
    )


def run_no_preset_untouched(work: Path, source: Path) -> None:
    print("\n-- (g) a document without a preset produces the pre-change graph")
    plain = rendered(work, "plain", document([clip("c1", source)]))
    check(
        "(g) the one-clip graph is the pre-change graph, byte for byte",
        plain[2]["filter_script"] == expected_direct(0, "c0") + "\n",
        repr(plain[2]["filter_script"]),
    )
    check(
        "(g) its routing table has no row for a preset",
        all(not row["where"].endswith("preset_ref") for row in plain[2]["backends"]),
        plain[2]["backends"],
    )
    check(
        "(g) its input arguments are unchanged",
        plain[2]["input_args"]
        == ["-ss", "0.000000", "-t", "1.000000", "-i", str(source)],
        plain[2]["input_args"],
    )


if __name__ == "__main__":
    raise SystemExit(main())
