"""F3-T07 gate -- the Chromium backend really renders inside an EDL 3 document.

    python tests/test_edl3_generator.py

Before this task an effect that qualified Chromium was refused with
BACKEND_UNAVAILABLE no matter what; the engine existed but nothing carried its
pixels into the filtergraph. Now a clip may declare a `generator`, the renderer
captures it once, and ffmpeg overlays the alpha PNG sequence at the clip's
ordinal.

What it proves, in order:

a. a document with a base clip and a generator overlay validates and renders;
b. the generator input is an image2 sequence, carries -framerate/-loop/-t, and
   has no -ss of its own, while the base clip keeps its -ss;
c. the generator chain ends in format=rgba, so the alpha survives to the overlay,
   and the overlay is enabled exactly over the clip's window;
d. the pixels agree: the bottom-left region changes, the top region does not;
e. every refusal names its code -- shape, conflicting source, time effects, span,
   alpha on layer 0, missing capture, unknown template, bad params, a qualifying
   node with no generated layer, and an absent engine;
f. compile_timeline launches no process: the side effects live in render();
g. a literal source that does not exist still raises MISSING_SOURCE, unchanged;
h. the fixture set under tests/fixtures/edl3_generator/ is written and portable.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from PIL import Image  # noqa: E402

from vx import edl3, edl3_render  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402
from vx import motion  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "edl3_generator"
FPS = 12
WIDTH, HEIGHT = 320, 568
SPAN = 2.0
TEMPLATE = "vx-title-lower-third"
CEILING_SECONDS = 180.0

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:<58} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:<58} {reason}")


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


def base_clip(source: Path) -> Dict[str, Any]:
    return {
        "id": "base",
        "source": str(source),
        "src_in": 0.0,
        "src_out": SPAN,
        "dst_in": 0.0,
        "dst_out": SPAN,
        "layer": 0,
        "track": 0,
    }


def marker() -> Dict[str, Any]:
    return {"kind": "transform", "backend": "chromium", "properties": ["layout"]}


def generator_doc(base: Path, **overrides: Any) -> Dict[str, Any]:
    declaration = {
        "template": TEMPLATE,
        "params": {
            "title": "Ciao Mondo",
            "subtitle": "prova T07",
            "accent": "#FFD400",
            "position": "bottom-left",
        },
        "engine": "chromium",
        "alpha": True,
    }
    declaration.update(overrides)
    return {
        "version": edl3.EDL3_VERSION,
        "fps": FPS,
        "size": [WIDTH, HEIGHT],
        "clips": [
            base_clip(base),
            {
                "id": "gen",
                "source": "",
                "src_in": 0.0,
                "src_out": SPAN,
                "dst_in": 0.0,
                "dst_out": SPAN,
                "layer": 1,
                "track": 0,
                "generator": declaration,
                "effects": [marker()],
            },
        ],
    }


def code_of(
    doc: Mapping[str, Any],
    *,
    settings: Optional[Mapping[str, Any]] = None,
    generator_paths: Optional[Mapping[str, str]] = None,
) -> str:
    try:
        timeline = edl3.Timeline3.from_dict(copy.deepcopy(doc))
        edl3_render.compile_timeline(
            timeline,
            audio="refuse",
            settings=settings,
            generator_paths=generator_paths,
        )
    except edl3.EDLError as exc:
        return exc.code
    except edl3_render.CompileError as exc:
        return exc.code
    return ""


def grab(video: Path, at: float, out: Path) -> None:
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


def mad(left: Image.Image, right: Image.Image, box: tuple) -> float:
    first = list(left.crop(box).convert("L").getdata())
    second = list(right.crop(box).convert("L").getdata())
    return sum(abs(a - b) for a, b in zip(first, second)) / float(len(first))


def refusal_matrix(base: Path, doc: Mapping[str, Any], capture_dir: Path) -> None:
    def code(mutate, **kwargs) -> str:
        broken = copy.deepcopy(doc)
        mutate(broken)
        return code_of(broken, **kwargs)

    def gen_clip(payload: Dict[str, Any]) -> Dict[str, Any]:
        return payload["clips"][1]

    cases = [
        (
            "e. a generator that is not an object -> GENERATOR_SHAPE",
            lambda d: gen_clip(d).__setitem__("generator", "nope"),
            "GENERATOR_SHAPE",
        ),
        (
            "e. a generator beside a source -> GENERATOR_SOURCE_CONFLICT",
            lambda d: gen_clip(d).__setitem__("source", str(base)),
            "GENERATOR_SOURCE_CONFLICT",
        ),
        (
            "e. speed on a generator clip -> GENERATOR_TIME_FX",
            lambda d: gen_clip(d).__setitem__("speed", 2),
            "GENERATOR_TIME_FX",
        ),
        (
            "e. a span that is not the template's clock -> GENERATOR_SPAN",
            lambda d: (
                gen_clip(d).__setitem__("src_out", 1.5),
                gen_clip(d).__setitem__("dst_out", 1.5),
            ),
            "GENERATOR_SPAN",
        ),
        (
            "e. alpha on the base layer -> GENERATOR_ALPHA_LAYER",
            lambda d: (
                gen_clip(d).__setitem__("layer", 0),
                d.__setitem__("clips", [gen_clip(d)]),
            ),
            "GENERATOR_ALPHA_LAYER",
        ),
        (
            "e. a generator with no captured frames -> GENERATOR_CAPTURE",
            lambda d: None,
            "GENERATOR_CAPTURE",
        ),
        (
            "e. an unknown template -> GENERATOR_TEMPLATE",
            lambda d: gen_clip(d)["generator"].__setitem__("template", "vx-nope"),
            "GENERATOR_TEMPLATE",
        ),
        (
            "e. an unknown parameter -> GENERATOR_PARAMS",
            lambda d: gen_clip(d)["generator"].__setitem__("params", {"nope": 1}),
            "GENERATOR_PARAMS",
        ),
    ]
    for label, mutate, expected in cases:
        got = code(mutate)
        check(label, got == expected, got)

    check(
        "e. a stale capture directory -> GENERATOR_CAPTURE",
        code_of(doc, generator_paths={"gen": str(base.parent / "nowhere")})
        == "GENERATOR_CAPTURE",
        code_of(doc, generator_paths={"gen": str(base.parent / "nowhere")}),
    )
    check(
        "e. the same declaration with its real capture compiles",
        code_of(
            doc,
            generator_paths={"gen": str(capture_dir)},
            settings={"chromium_available": True},
        )
        == "",
        "no error",
    )

    sourced = {
        "version": edl3.EDL3_VERSION,
        "fps": FPS,
        "size": [WIDTH, HEIGHT],
        "clips": [base_clip(base)],
    }
    sourced["clips"][0]["effects"] = [marker()]
    check(
        "e. available engine, no generated layer -> CHROMIUM_NEEDS_GENERATOR",
        code_of(sourced, settings={"chromium_available": True})
        == "CHROMIUM_NEEDS_GENERATOR",
        code_of(sourced, settings={"chromium_available": True}),
    )
    check(
        "e. absent engine, qualifying effect -> BACKEND_UNAVAILABLE",
        code_of(sourced, settings={"chromium_available": False})
        == "BACKEND_UNAVAILABLE",
        code_of(sourced, settings={"chromium_available": False}),
    )


def purity(base: Path, capture_dir: Path, doc: Mapping[str, Any]) -> None:
    original_run = subprocess.run
    original_popen = subprocess.Popen

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("compile_timeline launched a process")

    subprocess.run = boom  # type: ignore[assignment]
    subprocess.Popen = boom  # type: ignore[assignment]
    try:
        timeline = edl3.Timeline3.from_dict(copy.deepcopy(doc))
        edl3_render.compile_timeline(
            timeline,
            audio="refuse",
            settings={"chromium_available": True},
            generator_paths={"gen": str(capture_dir)},
        )
        pure = True
        detail = "no browser, no ffmpeg"
    except AssertionError as exc:
        pure = False
        detail = str(exc)
    finally:
        subprocess.run = original_run  # type: ignore[assignment]
        subprocess.Popen = original_popen  # type: ignore[assignment]
    check("f. compile_timeline launches no process", pure, detail)

    missing = {
        "version": edl3.EDL3_VERSION,
        "fps": FPS,
        "size": [WIDTH, HEIGHT],
        "clips": [
            {
                "id": "x",
                "source": str(base.parent / "absent.mp4"),
                "src_in": 0.0,
                "src_out": SPAN,
                "dst_in": 0.0,
                "dst_out": SPAN,
                "layer": 0,
                "track": 0,
            }
        ],
    }
    check(
        "g. a literal source that does not exist still raises MISSING_SOURCE",
        code_of(missing) == "MISSING_SOURCE",
        code_of(missing),
    )


def write_fixtures(
    base: Path, doc: Mapping[str, Any], report: Mapping[str, Any], work: Path
) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    portable = copy.deepcopy(doc)
    portable["clips"][0]["source"] = "<base>/base.mp4"
    (FIXTURES / "doc.json").write_text(
        json.dumps(portable, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (FIXTURES / "filter_complex.txt").write_text(
        str(report["filter_script"]).rstrip("\n") + "\n", encoding="utf-8"
    )
    for name, at in (
        ("frame-t02.png", 0.2),
        ("frame-t10.png", 1.0),
        ("frame-t19.png", 1.9),
    ):
        grab(Path(report["output"]), at, FIXTURES / name)
    check(
        "h. the fixture set is on disk",
        all(
            (FIXTURES / item).is_file()
            for item in (
                "doc.json",
                "filter_complex.txt",
                "frame-t02.png",
                "frame-t10.png",
                "frame-t19.png",
            )
        ),
        str(FIXTURES.relative_to(ROOT)),
    )


def run(work: Path) -> None:
    probe = motion.available()
    check(
        "0. a system browser is available",
        bool(probe["available"]),
        f"engine={probe.get('engine')} reason={probe.get('reason')}",
    )
    if not probe["available"]:
        skip("the whole gate", "no Chromium channel to capture with")
        return

    base = build_base(work)
    check(
        "0. the base clip exists",
        base.is_file() and base.stat().st_size > 0,
        str(base.name),
    )

    doc = generator_doc(base)
    timeline = edl3.Timeline3.from_dict(copy.deepcopy(doc))
    check(
        "a. the document validates",
        timeline.validate() == [],
        str(timeline.validate()[:2]),
    )

    report = edl3_render.render(
        timeline, work / "out.mp4", workdir=work, timeout=180, audio="refuse"
    )
    check(
        "a. the document rendered",
        Path(report["output"]).is_file() and report["output_size"] > 0,
        f"{report['output_size']} bytes",
    )
    chromium = report["chromium"]
    check(
        "a. the qualified effect was realised by the generator",
        chromium["qualified"] >= 1
        and chromium["realized_by_generator"] >= 1
        and chromium["refused"] == 0,
        json.dumps(chromium, sort_keys=True),
    )
    check(
        "a. the report names the generator and its engine",
        len(report["generators"]) == 1
        and report["generators"][0]["engine"] in ("chrome", "msedge", "pillow")
        and report["generator_renders"] == 1,
        json.dumps(report["generators"][0], sort_keys=True)[:140],
    )
    check(
        "a. the settings came from the probe, not from a hand-set flag",
        report["settings_source"] == "probe",
        report["settings_source"],
    )

    parts = [str(part) for part in report["command"]]
    inputs = [index for index, part in enumerate(parts) if part == "-i"]
    generator_at = [
        index for index, part in enumerate(parts) if part.endswith("frame_%05d.png")
    ]
    check(
        "b. the command declares two inputs", len(inputs) == 2, f"{len(inputs)} inputs"
    )
    check(
        "b. the generator is an image2 sequence input",
        len(generator_at) == 1 and generator_at[0] - 1 in inputs,
        str(generator_at),
    )
    if len(inputs) == 2 and generator_at:
        base_segment = parts[: inputs[0] + 1]
        gen_segment = parts[inputs[0] + 1 : inputs[1]]
        check(
            "b. the base clip keeps its -ss",
            "-ss" in base_segment,
            " ".join(base_segment),
        )
        check(
            "b. the generator input has -framerate, -loop 1, -t and no -ss",
            "-framerate" in gen_segment
            and "-loop" in gen_segment
            and "-t" in gen_segment
            and "-ss" not in gen_segment,
            " ".join(gen_segment),
        )

    script = str(report["filter_script"])
    gen_line = next(
        (line for line in script.splitlines() if line.startswith("[1:v]")), ""
    )
    check(
        "c. the generator chain ends in format=rgba",
        "format=rgba" in gen_line and "format=yuv420p" not in gen_line,
        gen_line[:120],
    )
    check(
        "c. the overlay is enabled over the clip's window",
        f"overlay=enable='between(t,{0.0:.6f},{SPAN:.6f})'" in script,
        "window present" if "overlay=enable=" in script else "no overlay",
    )

    base_only = copy.deepcopy(doc)
    base_only["clips"] = base_only["clips"][:1]
    edl3_render.render(
        edl3.Timeline3.from_dict(base_only),
        work / "base_only.mp4",
        workdir=work / "base_only",
        timeout=180,
        audio="refuse",
    )
    for name, at in (("t02", 0.2), ("t10", 1.0), ("t19", 1.9)):
        grab(Path(report["output"]), at, work / f"gen_{name}.png")
        grab(work / "base_only.mp4", at, work / f"base_{name}.png")
    with (
        Image.open(work / "gen_t10.png") as over,
        Image.open(work / "base_t10.png") as plain,
    ):
        size = over.size
        bottom_left = (0, int(size[1] * 0.70), int(size[0] * 0.60), size[1])
        top = (0, 0, size[0], int(size[1] * 0.40))
        visible = mad(over, plain, bottom_left)
        untouched = mad(over, plain, top)
    check(
        "d. the overlay is visible where the template draws it",
        visible > 2.0,
        f"MAD bottom-left {visible:.3f}",
    )
    check(
        "d. the overlay leaves the rest of the frame alone",
        untouched == 0.0,
        f"MAD top {untouched:.3f}",
    )

    capture_dir = Path(report["generators"][0]["directory"])
    refusal_matrix(base, doc, capture_dir)
    purity(base, capture_dir, doc)
    write_fixtures(base, doc, report, work)


def main() -> int:
    global FAIL
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(
            prefix="vx_edl3_generator_", ignore_cleanup_errors=True
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
