"""F27-T01 gate -- a clip starts from a template and its exposed values reach the frame.

    python tests/test_template_overrides.py

Before this task ``template_ref`` and ``exposed_params`` were carried by the
contract and read by nobody: a clip that named a template still rendered the
template's declared defaults. Now the clip's generator is built from the
template it names, and its parameters are the template's declared defaults, then
``generator.params``, then ``exposed_params`` (last wins).

What it proves, in order:

a. the merge order is the declared one, and a bare value is as good as a
   ``{type, default}`` declaration;
b. a clip that names ``template_ref`` and declares no picture of its own is a
   generator clip, while one that carries a source is not;
c. the four refusal codes -- ``TEMPLATE_UNKNOWN`` (listing the declared
   templates), ``PARAM_NOT_EXPOSED``, ``PARAM_TYPE`` and ``PARAM_OUT_OF_RANGE``
   -- each name their own problem and each stop a compile;
d. two different exposed values produce two different frames (sha256 differ),
   and the difference is visible where the template paints the accent;
e. the same template with no overrides produces a frame byte-identical to the
   template's own default render, and re-rendering gives the same bytes;
f. a clip that still uses ``generator.params`` renders exactly as before;
g. the capture record carries the merged parameters, so the proof downstream
   compares the values the frame was actually drawn with.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from PIL import Image, ImageChops  # noqa: E402

from vx import edl3, edl3_render, motion  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402

FPS = 2
WIDTH, HEIGHT = 320, 568
TEMPLATE = "vx-title-full-frame"
SPAN = 2.2
MOMENT = 1.0
FRAME = int(round(MOMENT * FPS))
CEILING_SECONDS = 420.0

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<62} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:<62} {reason}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def template_doc(
    clip: Mapping[str, Any],
    *,
    span: float = SPAN,
    template: str = TEMPLATE,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "id": "gen",
        "source": "",
        "src_in": 0.0,
        "src_out": span,
        "layer": 0,
        "template_ref": template,
    }
    body.update(clip)
    return {
        "version": edl3.EDL3_VERSION,
        "fps": FPS,
        "size": [WIDTH, HEIGHT],
        "clips": [body],
    }


def codes(doc: Mapping[str, Any]) -> list:
    timeline = edl3.Timeline3.from_dict(copy.deepcopy(doc))
    return [item["code"] for item in timeline.validate()]


def problems(doc: Mapping[str, Any]) -> list:
    timeline = edl3.Timeline3.from_dict(copy.deepcopy(doc))
    return timeline.validate()


def compile_code(doc: Mapping[str, Any]) -> str:
    timeline = edl3.Timeline3.from_dict(copy.deepcopy(doc))
    try:
        edl3_render.compile_timeline(
            timeline, audio="refuse", settings={"chromium_available": True}
        )
    except edl3.EDLError as exc:
        return exc.code
    except edl3_render.CompileError as exc:
        return exc.code
    return ""


def grab(video: Path, at: float, out: Path) -> Path:
    ff.run_ff(
        [
            "-y",
            "-ss",
            f"{at:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(out),
        ],
        loglevel="error",
    )
    return out


def frame_delta(left: Path, right: Path) -> Tuple[int, float]:
    with Image.open(left) as first, Image.open(right) as second:
        difference = ImageChops.difference(
            first.convert("RGB"), second.convert("RGB")
        )
        peak = max(bound for _low, bound in difference.getextrema())
        values = list(difference.convert("L").getdata())
    return peak, sum(values) / float(len(values))


def build_base(work: Path) -> Path:
    base = work / "base.mp4"
    ff.run_ff(
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:s={WIDTH}x{HEIGHT}:d={SPAN}",
            "-r",
            str(FPS),
            "-pix_fmt",
            "yuv420p",
            str(base),
        ],
        loglevel="error",
    )
    return base


def render_case(
    work: Path,
    name: str,
    clip: Mapping[str, Any],
    *,
    span: float = SPAN,
    template: str = TEMPLATE,
) -> Tuple[Dict[str, Any], Path]:
    doc = template_doc(clip, span=span, template=template)
    timeline = edl3.Timeline3.from_dict(copy.deepcopy(doc))
    report = edl3_render.render(
        timeline,
        work / f"{name}.mp4",
        workdir=work / name,
        timeout=180,
        audio="refuse",
    )
    return report, Path(report["generators"][0]["directory"])


def capture_params(directory: Path) -> Dict[str, Any]:
    record = json.loads((directory / "capture.json").read_text(encoding="utf-8"))
    return dict(record["params"])


def merge_order_checks() -> None:
    both = edl3.Clip3(
        id="gen",
        src_in=0.0,
        src_out=2.0,
        generator={
            "template": "vx-title-lower-third",
            "params": {"title": "Dai parametri"},
            "engine": "chromium",
            "alpha": False,
        },
        template_ref="vx-title-lower-third",
        exposed_params={"title": "Dagli esposti"},
    )
    declaration = both.generator_declaration or {}
    check(
        "a. exposed_params wins over generator.params",
        declaration.get("params", {}).get("title") == "Dagli esposti",
        declaration.get("params"),
    )

    generator_only = edl3.Clip3(
        id="gen",
        src_in=0.0,
        src_out=2.0,
        generator={
            "template": "vx-title-lower-third",
            "params": {"title": "Solo generator"},
            "engine": "chromium",
            "alpha": False,
        },
        template_ref="vx-title-lower-third",
    )
    check(
        "a. generator.params survives when nothing is exposed",
        (generator_only.generator_declaration or {}).get("params", {}).get("title")
        == "Solo generator",
        (generator_only.generator_declaration or {}).get("params"),
    )

    declared = edl3.Clip3(
        id="gen",
        src_in=0.0,
        src_out=SPAN,
        template_ref=TEMPLATE,
        exposed_params={"title": {"type": "string", "default": "Da una dichiarazione"}},
    )
    check(
        "a. a {type, default} declaration contributes its default",
        (declared.generator_declaration or {}).get("params", {}).get("title")
        == "Da una dichiarazione",
        edl3.exposed_param_value({"type": "string", "default": "x"}),
    )

    bare = edl3.Clip3(id="gen", src_in=0.0, src_out=SPAN, template_ref=TEMPLATE)
    synthesized = bare.generator_declaration or {}
    check(
        "b. template_ref alone builds a generator from that template",
        synthesized.get("template") == TEMPLATE
        and synthesized.get("engine") == "auto"
        and synthesized.get("alpha") is False
        and synthesized.get("params") == {},
        synthesized,
    )

    sourced = edl3.Clip3(
        id="gen",
        source="a.mp4",
        src_in=0.0,
        src_out=1.0,
        template_ref=TEMPLATE,
    )
    check(
        "b. a clip with its own picture is not turned into a generator",
        sourced.generator_declaration is None,
        sourced.generator_declaration,
    )

    meta = motion.load_template(TEMPLATE)
    resolved = motion.validate_params(meta, synthesized.get("params") or {})
    check(
        "a. the defaults are the template's own, once resolved",
        resolved
        == {
            "title": "Il Momento",
            "subtitle": "Una storia in due atti",
            "accent": "#FFD400",
            "bars": True,
        },
        resolved,
    )

    overridden = edl3.Clip3(
        id="gen",
        src_in=0.0,
        src_out=SPAN,
        template_ref=TEMPLATE,
        exposed_params={"accent": "#FF4D4D"},
    )
    merged = motion.validate_params(
        meta, (overridden.generator_declaration or {}).get("params") or {}
    )
    check(
        "a. one override moves exactly one parameter",
        merged["accent"] == "#FF4D4D"
        and {k: v for k, v in merged.items() if k != "accent"}
        == {k: v for k, v in resolved.items() if k != "accent"},
        merged,
    )


def validation_checks() -> None:
    clean = template_doc({"exposed_params": {"accent": "#FF4D4D"}})
    check(
        "c. a valid override validates clean",
        codes(clean) == [],
        codes(clean),
    )

    unknown = template_doc({"exposed_params": {"accent": "#FF4D4D"}}, template="vx-nope")
    found = problems(unknown)
    detail = next(
        (item["detail"] for item in found if item["code"] == "TEMPLATE_UNKNOWN"), ""
    )
    check(
        "c. an unknown template is refused",
        [item["code"] for item in found] == ["TEMPLATE_UNKNOWN"],
        [item["code"] for item in found],
    )
    check(
        "c. the refusal lists the declared templates",
        "vx-title-full-frame" in detail and "vx-transition-luma" in detail,
        detail[:80] + ("..." if len(detail) > 80 else ""),
    )

    unexposed = template_doc({"exposed_params": {"nope": "x"}})
    check(
        "c. an undeclared parameter is refused",
        codes(unexposed) == ["PARAM_NOT_EXPOSED"],
        codes(unexposed),
    )

    wrong_type = template_doc({"exposed_params": {"accent": 5}})
    check(
        "c. a value of the wrong type is refused",
        codes(wrong_type) == ["PARAM_TYPE"],
        problems(wrong_type),
    )

    wrong_bool = template_doc({"exposed_params": {"bars": "yes"}})
    check(
        "c. a non-boolean for a bool parameter is refused",
        codes(wrong_bool) == ["PARAM_TYPE"],
        codes(wrong_bool),
    )

    out_of_range = template_doc(
        {"exposed_params": {"feather": 0.9}}, span=2.0, template="vx-transition-luma"
    )
    check(
        "c. a number above the declared maximum is refused",
        codes(out_of_range) == ["PARAM_OUT_OF_RANGE"],
        problems(out_of_range),
    )

    below_min = template_doc(
        {"exposed_params": {"feather": 0.0}}, span=2.0, template="vx-transition-luma"
    )
    check(
        "c. a number below the declared minimum is refused",
        codes(below_min) == ["PARAM_OUT_OF_RANGE"],
        codes(below_min),
    )

    off_palette = template_doc({"exposed_params": {"accent": "#000000"}})
    check(
        "c. a colour outside the declared values is refused",
        codes(off_palette) == ["PARAM_OUT_OF_RANGE"],
        problems(off_palette),
    )

    in_range = template_doc(
        {"exposed_params": {"feather": 0.3}}, span=2.0, template="vx-transition-luma"
    )
    check(
        "c. a number inside the declared window validates clean",
        codes(in_range) == [],
        codes(in_range),
    )

    round_trip = edl3.Timeline3.from_dict(clean)
    back = edl3.Timeline3.from_dict(round_trip.to_dict())
    check(
        "c. template_ref and exposed_params survive the round trip",
        back.clips[0].template_ref == TEMPLATE
        and back.clips[0].exposed_params == {"accent": "#FF4D4D"},
        (back.clips[0].template_ref, back.clips[0].exposed_params),
    )

    for label, doc, expected in (
        ("TEMPLATE_UNKNOWN", unknown, "TEMPLATE_UNKNOWN"),
        ("PARAM_NOT_EXPOSED", unexposed, "PARAM_NOT_EXPOSED"),
        ("PARAM_TYPE", wrong_type, "PARAM_TYPE"),
        ("PARAM_OUT_OF_RANGE", off_palette, "PARAM_OUT_OF_RANGE"),
    ):
        got = compile_code(doc)
        check(f"c. {label} is hard: compile refuses it", got == expected, got)


def run(work: Path) -> None:
    merge_order_checks()
    validation_checks()

    probe = motion.available()
    check(
        "0. a system browser is available",
        bool(probe["available"]),
        f"engine={probe.get('engine')} reason={probe.get('reason')}",
    )
    if not probe["available"]:
        skip("the render half", "no Chromium channel to capture with")
        return

    report_a, dir_a = render_case(
        work, "warm", {"exposed_params": {"accent": "#FF4D4D"}}
    )
    report_b, dir_b = render_case(
        work, "cool", {"exposed_params": {"accent": "#38E1FF"}}
    )
    report_c, dir_c = render_case(work, "default", {})
    report_c2, dir_c2 = render_case(work, "default_again", {})

    for label, report in (
        ("d. the warm override renders", report_a),
        ("d. the cool override renders", report_b),
        ("e. the default override-free clip renders", report_c),
        ("e. the same clip renders twice", report_c2),
    ):
        output = Path(report["output"])
        check(
            label,
            output.is_file() and report["output_size"] > 0,
            f"{report['output_size']} bytes",
        )

    check(
        "g. the capture record carries the merged parameters",
        capture_params(dir_a).get("accent") == "#FF4D4D"
        and capture_params(dir_b).get("accent") == "#38E1FF",
        (capture_params(dir_a), capture_params(dir_b)),
    )
    check(
        "g. the other parameters keep the template's defaults",
        capture_params(dir_a).get("title") == "Il Momento"
        and capture_params(dir_a).get("bars") is True,
        capture_params(dir_a),
    )

    frame_a = dir_a / f"frame_{FRAME:05d}.png"
    frame_b = dir_b / f"frame_{FRAME:05d}.png"
    frame_c = dir_c / f"frame_{FRAME:05d}.png"
    frame_c2 = dir_c2 / f"frame_{FRAME:05d}.png"
    sha_a, sha_b = sha256(frame_a), sha256(frame_b)
    check(
        "d. two different exposed values give two different captured frames",
        sha_a != sha_b,
        f"{sha_a[:12]}... != {sha_b[:12]}...",
    )

    meta = motion.load_template(TEMPLATE)
    direct = work / "direct"
    motion.capture(meta["dir"], {}, direct, fps=FPS, size=(WIDTH, HEIGHT))
    frame_default = direct / f"frame_{FRAME:05d}.png"
    sha_default = sha256(frame_default)
    sha_c = sha256(frame_c)
    check(
        "e. no overrides is byte-identical to the template's own default render",
        sha_c == sha_default,
        f"{sha_c[:12]}... == {sha_default[:12]}...",
    )
    check(
        "e. rendering the same clip twice gives the same frame",
        sha_c == sha256(frame_c2),
        f"{sha_c[:12]}... == {sha256(frame_c2)[:12]}...",
    )

    hot_a = grab(Path(report_a["output"]), MOMENT, work / "warm.png")
    hot_b = grab(Path(report_b["output"]), MOMENT, work / "cool.png")
    hot_c = grab(Path(report_c["output"]), MOMENT, work / "default.png")
    hot_c2 = grab(Path(report_c2["output"]), MOMENT, work / "default_again.png")
    check(
        "d. the rendered frames differ, not just the captures",
        sha256(hot_a) != sha256(hot_b),
        f"{sha256(hot_a)[:12]}... != {sha256(hot_b)[:12]}...",
    )
    peak, low_mean = frame_delta(hot_a, hot_b)
    check(
        "d. the difference is visible in the picture",
        peak > 20,
        f"peak delta {peak}, mean {low_mean:.3f}",
    )
    check(
        "e. two renders of the default clip agree frame for frame",
        sha256(hot_c) == sha256(hot_c2),
        f"{sha256(hot_c)[:12]}... == {sha256(hot_c2)[:12]}...",
    )

    base = build_base(work)
    legacy = {
        "version": edl3.EDL3_VERSION,
        "fps": FPS,
        "size": [WIDTH, HEIGHT],
        "clips": [
            {
                "id": "base",
                "source": str(base),
                "src_in": 0.0,
                "src_out": SPAN,
                "dst_in": 0.0,
                "dst_out": SPAN,
                "layer": 0,
            },
            {
                "id": "gen",
                "source": "",
                "src_in": 0.0,
                "src_out": SPAN,
                "dst_in": 0.0,
                "dst_out": SPAN,
                "layer": 1,
                "generator": {
                    "template": TEMPLATE,
                    "params": {"accent": "#7CFF6B", "title": "Ciao Mondo"},
                    "engine": "chromium",
                    "alpha": True,
                },
            },
        ],
    }
    check(
        "f. a generator.params clip still validates",
        codes(legacy) == [],
        codes(legacy),
    )
    timeline = edl3.Timeline3.from_dict(copy.deepcopy(legacy))
    legacy_report = edl3_render.render(
        timeline,
        work / "legacy.mp4",
        workdir=work / "legacy",
        timeout=180,
        audio="refuse",
    )
    legacy_dir = Path(legacy_report["generators"][0]["directory"])
    check(
        "f. a generator.params clip still renders",
        Path(legacy_report["output"]).is_file()
        and legacy_report["output_size"] > 0
        and legacy_report["generator_renders"] == 1,
        f"{legacy_report['output_size']} bytes",
    )
    legacy_params = capture_params(legacy_dir)
    check(
        "f. generator.params are the values the frame was drawn with",
        legacy_params
        == {
            "title": "Ciao Mondo",
            "subtitle": "Una storia in due atti",
            "accent": "#7CFF6B",
            "bars": True,
        },
        legacy_params,
    )
    legacy_frame = legacy_dir / f"frame_{FRAME:05d}.png"
    check(
        "f. the legacy clip draws its own parameters, not the exposed ones",
        sha256(legacy_frame) != sha_a,
        f"{sha256(legacy_frame)[:12]}... != {sha_a[:12]}...",
    )


def main() -> int:
    global FAIL
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(
            prefix="vx_template_overrides_", ignore_cleanup_errors=True
        ) as tmp:
            run(Path(tmp))
    except edl3.EDLError as exc:
        FAIL += 1
        print(f"\nFAIL: declared document error {exc.code}: {exc}")
    except edl3_render.CompileError as exc:
        FAIL += 1
        print(f"\nFAIL: declared compile error {exc.code}: {exc}")
    except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
        FAIL += 1
        print(f"\nFAIL: {type(exc).__name__}: {exc}")
    elapsed = time.perf_counter() - started
    print(
        f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP, {elapsed:.1f}s "
        f"(ceiling {CEILING_SECONDS:.0f}s)"
    )
    if elapsed > CEILING_SECONDS:
        print(f"FAIL: the gate took {elapsed:.1f}s, above its ceiling")
        return 1
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
