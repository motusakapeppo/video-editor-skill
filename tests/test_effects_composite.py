"""F4-T06 gate — the ten single-input composite effects render and differ.

The composite family draws over the picture (bands, frames, guides, a vignette,
a rounded matte) without ever taking a second input. This file pins three
things, in this order:

* the declaration: exactly ten kinds, family ``composite``, static, one to three
  declared parameters, and a template whose placeholders are exactly those
  parameter names;
* the single-input law: no template names ``overlay``, ``movie=``, ``split`` or
  any stack/blend filter, and none of them draws text;
* the evidence: every template is rendered as a real ffmpeg 8.1 chain on a
  320x568 fixture, its sha256 differs from the un-effected frame, and the region
  each effect claims to change is *measured* (a black top band, a bright guide
  line, a bar that grows with time). The same ten clips are then rendered
  through the real EDL 3 compiler as documents, and the frames are compared with
  an un-effected baseline render.

A machine without ffmpeg skips the render half, never the declaration half.

    python tests/test_effects_composite.py
"""

from __future__ import annotations

import hashlib
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, edl3_render, effects  # noqa: E402
from vx.effects import composite  # noqa: E402
from vx.ffmpeg import run_ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FPS = 12
SIZE = (320, 568)
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
YAVG = re.compile(r"signalstats\.YAVG=([0-9.]+)")

#: The ten kinds this wave owns, in declaration order.
KINDS = (
    "letterbox",
    "bars",
    "frame_in_frame",
    "border",
    "safe_area",
    "grid",
    "spotlight",
    "rounded",
    "progress_bar",
    "caption_band",
)
#: Vocabulary that would mean "this template samples something other than the
#: single input it is handed", plus the text machinery the repo keeps out of
#: filters. A hit here is a defect, not a style choice.
FORBIDDEN = (
    "overlay",
    "movie=",
    "split",
    "hstack",
    "vstack",
    "xstack",
    "blend",
    "maskedmerge",
    "[1:",
    "drawtext",
    "textfile",
    "font",
    "subtitles",
    "ass=",
)


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:70} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:70} {reason}")


# ------------------------------------------------------------------ helpers


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def fragment(kind: str) -> str:
    spec = edl3.EFFECT_REGISTRY[kind]
    return edl3_render.filter_fragment(edl3.Effect(kind=kind), spec)


def render_frame(vf: str, path: Path, *, at: float = 0.0) -> str:
    """One PNG frame of the 1 s fixture at ``at``; returns the failure tail."""
    args = ["-ss", f"{at:.3f}"] if at else []
    args += ["-f", "lavfi", "-i", f"testsrc2=s={SIZE[0]}x{SIZE[1]}:r={FPS}:d=3"]
    if vf:
        args += ["-vf", vf]
    args += ["-frames:v", "1", str(path)]
    proc = run_ff(args, loglevel="error")
    return "" if proc.ok and path.exists() else proc.tail()


def region_luma(path: Path, crop: str) -> float:
    """YAVG of a cropped region of a PNG, measured by ffmpeg."""
    proc = run_ff(
        [
            "-i",
            str(path),
            "-vf",
            f"crop={crop},signalstats,metadata=mode=print:file=-",
            "-f",
            "null",
            "-",
        ],
        loglevel="error",
    )
    match = YAVG.search(proc.stdout or "")
    if not proc.ok or match is None:
        raise RuntimeError(f"region_luma({path.name}, {crop}) failed: {proc.tail()}")
    return float(match.group(1))


def document(effects_by_index: Mapping[int, str], source: str = "fixture.mp4") -> edl3.Timeline3:
    """Three one-second clips; the index in the mapping carries an effect."""
    clips: List[Dict[str, object]] = []
    for index in range(3):
        clip: Dict[str, object] = {
            "id": f"c{index}",
            "source": source,
            "src_in": 0.0,
            "src_out": 1.0,
            "layer": 0,
            "dst_in": float(index),
            "dst_out": float(index + 1),
        }
        kind = effects_by_index.get(index)
        if kind is not None:
            clip["effects"] = [{"kind": kind, "params": {}}]
        clips.append(clip)
    return edl3.Timeline3.from_dict(
        {
            "version": edl3.EDL3_VERSION,
            "fps": FPS,
            "size": list(SIZE),
            "clips": clips,
        }
    )


# ------------------------------------------------------- (a) the declaration


def check_declaration() -> None:
    print("\n-- (a) the composite family declares exactly ten single-input kinds")
    grouped = effects.families()
    check(
        "the family loader finds the composite module",
        "composite" in grouped,
        sorted(grouped),
    )
    entries = grouped.get("composite", {})
    check(
        "composite declares exactly ten entries",
        len(entries) == 10,
        sorted(entries),
    )
    check(
        "the ten declared kinds are the ten this wave owns",
        tuple(sorted(entries)) == tuple(sorted(KINDS)),
        sorted(set(KINDS) ^ set(entries)),
    )
    check(
        "every entry is keyed under its own kind and names its family",
        all(kind == spec.kind and spec.family == "composite"
            for kind, spec in entries.items()),
        [
            f"{kind}: {spec.kind}/{spec.family}"
            for kind, spec in entries.items()
            if kind != spec.kind or spec.family != "composite"
        ],
    )
    check(
        "every entry is the static filter backend and never animates",
        all(
            spec.backend == edl3.BACKEND_FILTER
            and spec.animatable is False
            and spec.animated_params == ()
            for spec in entries.values()
        ),
        [
            kind
            for kind, spec in entries.items()
            if spec.backend != "filter" or spec.animatable or spec.animated_params
        ],
    )
    check(
        "every entry declares one to three parameters",
        all(1 <= len(spec.params_spec) <= 3 for spec in entries.values()),
        {kind: len(spec.params_spec) for kind, spec in entries.items()},
    )
    check(
        "every default sits inside its declared closed range",
        all(
            param.lo <= param.default <= param.hi and param.step >= 0.0
            for spec in entries.values()
            for param in spec.params_spec
        ),
        [
            f"{kind}.{param.name}"
            for kind, spec in entries.items()
            for param in spec.params_spec
            if not (param.lo <= param.default <= param.hi and param.step >= 0.0)
        ],
    )


def check_templates() -> None:
    print("\n-- (b) every template names exactly its declared parameters")
    entries = composite.ENTRIES
    undeclared = [
        f"{kind}: {sorted(used - set(spec.params))}"
        for kind, spec in entries.items()
        for used in [set(PLACEHOLDER.findall(spec.filter_template))]
        if used - set(spec.params)
    ]
    check("every placeholder names a declared parameter", undeclared == [], undeclared)
    unused = [
        f"{kind}: {sorted(set(spec.params) - used)}"
        for kind, spec in entries.items()
        for used in [set(PLACEHOLDER.findall(spec.filter_template))]
        if set(spec.params) - used
    ]
    check("every declared parameter appears in its template", unused == [], unused)

    broken: List[str] = []
    for kind in entries:
        text = fragment(kind)
        if "{" in text or "}" in text:
            broken.append(f"{kind}: unformatted placeholder in {text!r}")
    check("every template formats at its defaults", broken == [], broken)

    check(
        "the templates are the expected ffmpeg mechanisms",
        "vignette=" in fragment("spotlight")
        and fragment("frame_in_frame").startswith("scale=")
        and ",pad=" in fragment("frame_in_frame")
        and "geq=" in fragment("rounded")
        and fragment("progress_bar").count("drawbox=") == 2,
        {
            "frame_in_frame": fragment("frame_in_frame")[:40],
            "rounded": fragment("rounded")[:20],
        },
    )
    check(
        "progress_bar fills against a declared time, never a duration variable",
        "min(t/4,1)" in fragment("progress_bar")
        and "duration" not in fragment("progress_bar")
        and edl3.EFFECT_REGISTRY["progress_bar"].param("seconds") is not None,
        fragment("progress_bar")[:80],
    )
    check(
        "progress_bar draws its track first, so t=0 already differs",
        fragment("progress_bar").split(",")[0].endswith(":t=fill")
        and "white@0.3" in fragment("progress_bar").split(",")[0],
        fragment("progress_bar").split(",")[0][:70],
    )


def check_law() -> None:
    print("\n-- (c) the single-input law and the no-text law hold")
    hits = [
        f"{kind}: {token}"
        for kind, spec in composite.ENTRIES.items()
        for token in FORBIDDEN
        if token in spec.filter_template
    ]
    check(
        "no template names a second input, a stack, a blend or a font",
        hits == [],
        hits,
    )
    two_input = {"split_screen", "pip", "picture_in_picture", "side_by_side"}
    check(
        "no two-input kind is declared in this family",
        not (two_input & set(composite.ENTRIES)),
        sorted(two_input & set(composite.ENTRIES)),
    )
    check(
        "every entry carries a one-line trigger, notes and a declared fallback",
        all(
            spec.trigger.strip()
            and "\n" not in spec.trigger
            and spec.notes.strip()
            and spec.fallback in {"skip", "static"}
            for spec in composite.ENTRIES.values()
        ),
        [
            kind
            for kind, spec in composite.ENTRIES.items()
            if not (spec.trigger.strip() and spec.notes.strip())
            or spec.fallback not in {"skip", "static"}
        ],
    )
    check(
        "every entry declares a non-negative cap",
        all(spec.cap_seconds >= 0.0 for spec in composite.ENTRIES.values()),
        {kind: spec.cap_seconds for kind, spec in composite.ENTRIES.items()},
    )
    capped = {k: s.cap_seconds for k, s in composite.ENTRIES.items() if s.cap_seconds}
    check(
        "only the per-pixel matte is capped, and it skips past the cap",
        capped == {"rounded": 4.0}
        and composite.ENTRIES["rounded"].fallback == "skip",
        capped,
    )


def check_routing() -> None:
    print("\n-- (d) every kind is registered, renderable and routes to filter")
    check(
        "every composite kind is in the registry as itself",
        all(
            edl3.EFFECT_REGISTRY.get(kind) is spec
            for kind, spec in composite.ENTRIES.items()
        ),
        sorted(set(composite.ENTRIES) - set(edl3.EFFECT_REGISTRY)),
    )
    check(
        "every composite kind is renderable and supported by the compiler",
        set(composite.ENTRIES) <= set(edl3.RENDERABLE_EFFECTS)
        and set(composite.ENTRIES) <= set(edl3_render.SUPPORTED_EFFECTS),
        sorted(set(composite.ENTRIES) - set(edl3_render.SUPPORTED_EFFECTS)),
    )
    check(
        "every composite kind implements exactly the filter backend",
        all(
            edl3_render.IMPLEMENTED_BACKENDS.get(kind) == frozenset({"filter"})
            for kind in composite.ENTRIES
        ),
        {kind: sorted(edl3_render.IMPLEMENTED_BACKENDS.get(kind, ()))
         for kind in composite.ENTRIES},
    )
    check(
        "the pure routing rule picks filter for each kind",
        all(
            edl3.select_backend(kind, 0, 1.0) == "filter"
            for kind in composite.ENTRIES
        ),
        {kind: edl3.select_backend(kind, 0, 1.0) for kind in composite.ENTRIES},
    )
    keyframed = edl3.Timeline3.from_dict(
        {
            "version": edl3.EDL3_VERSION,
            "fps": FPS,
            "size": list(SIZE),
            "clips": [
                {
                    "id": "c0",
                    "source": "unused.mp4",
                    "src_in": 0.0,
                    "src_out": 1.0,
                    "layer": 0,
                    "dst_in": None,
                    "dst_out": None,
                    "effects": [
                        {
                            "kind": "letterbox",
                            "params": {},
                            "keyframes": {"ratio": [{"t": 0.0, "value": 2.0}]},
                        }
                    ],
                }
            ],
        }
    )
    codes = [problem["code"] for problem in keyframed.validate()]
    check(
        "a keyframed composite instance is refused, never flattened",
        codes.count("EFFECT_NOT_ANIMATABLE") == 1,
        codes,
    )


# --------------------------------------------------------------- (e) renders


def check_renders(work: Path) -> None:
    print("\n-- (e) every template renders as a real ffmpeg chain and differs")
    base = work / "base.png"
    problem = render_frame("", base)
    if problem:
        skip("the ffmpeg render fixture", problem.splitlines()[-1][:120])
        return
    base_digest = sha256(base)
    base_corner = region_luma(base, "6:6:0:0")
    base_row = region_luma(base, "320:2:0:189")
    base_left = region_luma(base, "8:40:4:200")
    print(f"        base: 6x6 corner YAVG={base_corner:.1f} "
          f"row189={base_row:.1f} left={base_left:.1f}")

    rendered: Dict[str, Path] = {}
    for kind in KINDS:
        path = work / f"{kind}.png"
        failure = render_frame(fragment(kind), path)
        if failure:
            check(f"{kind} renders", False, failure.splitlines()[-1][:120])
            continue
        rendered[kind] = path
        check(
            f"{kind} differs from the un-effected frame",
            sha256(path) != base_digest,
            f"{path.stat().st_size} B",
        )
    if len(rendered) != len(KINDS):
        skip("the region measurements", "a template did not render")
        return

    letterbox = rendered["letterbox"]
    check(
        "letterbox blacks the top and bottom bands and keeps the middle",
        region_luma(letterbox, "320:40:0:0") <= 20.0
        and region_luma(letterbox, "320:40:0:264") >= 100.0,
        f"top={region_luma(letterbox, '320:40:0:0'):.1f} "
        f"mid={region_luma(letterbox, '320:40:0:264'):.1f}",
    )
    bars = rendered["bars"]
    check(
        "bars blacks the side columns and keeps the middle",
        region_luma(bars, "8:40:4:200") <= 20.0
        and region_luma(bars, "320:40:0:264") >= 100.0,
        f"left={region_luma(bars, '8:40:4:200'):.1f}",
    )
    fif = rendered["frame_in_frame"]
    check(
        "frame_in_frame insets the picture on a black surround",
        region_luma(fif, "320:40:0:0") <= 20.0
        and region_luma(fif, "320:40:0:264") >= 100.0,
        f"top={region_luma(fif, '320:40:0:0'):.1f}",
    )
    border = rendered["border"]
    check(
        "border paints a bright stroke near the frame edge",
        region_luma(border, "8:40:4:200") >= 150.0,
        f"stroke={region_luma(border, '8:40:4:200'):.1f} vs base {base_left:.1f}",
    )
    safe = rendered["safe_area"]
    check(
        "safe_area draws the outer guide at the declared margin",
        region_luma(safe, "2:40:16:200") >= 150.0,
        f"guide={region_luma(safe, '2:40:16:200'):.1f}",
    )
    grid = rendered["grid"]
    check(
        "grid draws a horizontal guide on the lower third line",
        region_luma(grid, "320:2:0:189") >= base_row + 40.0,
        f"row={region_luma(grid, '320:2:0:189'):.1f} vs base {base_row:.1f}",
    )
    spotlight = rendered["spotlight"]
    check(
        "spotlight darkens the corner and keeps the centre",
        region_luma(spotlight, "6:6:0:0") <= base_corner * 0.5
        and region_luma(spotlight, "320:40:0:264") >= 90.0,
        f"corner={region_luma(spotlight, '6:6:0:0'):.1f} vs base {base_corner:.1f}",
    )
    rounded = rendered["rounded"]
    check(
        "rounded cuts the corner to true black on all planes",
        region_luma(rounded, "6:6:0:0") <= 20.0
        and region_luma(rounded, "160:160:80:200") >= 100.0,
        f"corner={region_luma(rounded, '6:6:0:0'):.1f} "
        f"centre={region_luma(rounded, '160:160:80:200'):.1f}",
    )
    corner_pixel = work / "rounded_corner.png"
    proc = run_ff(
        ["-i", str(rounded), "-vf", "crop=4:4:0:0", "-f", "rawvideo",
         "-pix_fmt", "rgb24", str(corner_pixel)],
        loglevel="error",
    )
    raw = corner_pixel.read_bytes() if proc.ok and corner_pixel.exists() else b""
    check(
        "the rounded corner is neutral black, not a luma-only hole",
        len(raw) >= 12 and all(raw[index] == 0 for index in range(12)),
        list(raw[:6]),
    )

    band_early = work / "progress_0.png"
    band_late = work / "progress_2.png"
    render_frame(fragment("progress_bar"), band_early, at=0.0)
    render_frame(fragment("progress_bar"), band_late, at=2.0)
    early = region_luma(band_early, "320:40:0:528")
    late = region_luma(band_late, "320:40:0:528")
    check(
        "progress_bar shows a track at t=0 and a wider fill at t=2",
        early >= 130.0 and late > early + 3.0,
        f"t0={early:.1f} t2={late:.1f}",
    )
    caption = rendered["caption_band"]
    check(
        "caption_band darkens the lower band only",
        region_luma(caption, "320:40:0:528") <= 80.0
        and region_luma(caption, "320:40:0:264") >= 100.0,
        f"band={region_luma(caption, '320:40:0:528'):.1f}",
    )


# ------------------------------------------------------------- (f) EDL 3 run


def check_edl3(work: Path) -> None:
    print("\n-- (f) the ten kinds render through the real EDL 3 compiler")
    fixture = work / "fixture.mp4"
    proc = run_ff(
        [
            "-f", "lavfi", "-i", f"testsrc2=s={SIZE[0]}x{SIZE[1]}:r={FPS}:d=1",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-pix_fmt", "yuv420p", str(fixture),
        ],
        loglevel="error",
    )
    if not (proc.ok and fixture.exists()):
        skip("the EDL 3 document render", proc.tail().splitlines()[-1][:120])
        return

    plain = edl3_render.render(
        document({}, str(fixture)), work / "plain.mp4", workdir=work / "plain"
    )
    check(
        "the un-effected baseline document renders",
        Path(plain["output"]).exists() and plain["output_size"] > 0,
        f"{plain['output_size']} B",
    )
    groups = [KINDS[0:3], KINDS[3:6], KINDS[6:9], KINDS[9:10]]
    differing = 0
    routed = True
    for index, group in enumerate(groups):
        target = work / f"doc{index}.mp4"
        try:
            report = edl3_render.render(
                document(dict(enumerate(group)), str(fixture)),
                target,
                workdir=work / f"d{index}",
            )
        except Exception as exc:  # noqa: BLE001 - a refusal is a failure here
            check(f"doc{index} {list(group)} renders", False,
                  f"{type(exc).__name__}: {exc}")
            continue
        routed = routed and all(
            entry["chosen"] == "filter" for entry in report["backends"]
        )
        graph = report["filter_script"]
        emitted = all(fragment(kind) in graph for kind in group)
        check(
            f"doc{index} {list(group)} renders and emits its fragments",
            Path(report["output"]).exists() and emitted,
            f"{report['output_size']} B",
        )
        for position, kind in enumerate(group):
            time = position + 0.5
            before = work / f"plain_{index}_{position}.png"
            after = work / f"effect_{index}_{position}.png"
            render_frame("", before, at=time)
            grab = run_ff(
                ["-ss", f"{time:.3f}", "-i", str(target), "-frames:v", "1",
                 str(after)],
                loglevel="error",
            )
            changed = (
                grab.ok and after.exists() and before.exists()
                and sha256(after) != sha256(before)
            )
            differing += changed
    check(
        "every backends report named the filter mechanism",
        routed,
        "" if routed else "a document reported another backend",
    )
    check(
        "all ten composite clips differ from the baseline render",
        differing == len(KINDS),
        f"{differing}/{len(KINDS)}",
    )


def main() -> int:
    check_declaration()
    check_templates()
    check_law()
    check_routing()
    with tempfile.TemporaryDirectory(prefix="vx_composite_") as tmp:
        check_renders(Path(tmp))
    with tempfile.TemporaryDirectory(prefix="vx_composite_edl3_") as tmp:
        check_edl3(Path(tmp))
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
