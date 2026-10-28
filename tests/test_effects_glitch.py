"""F4-T04 gate — the glitch family is declared, valid and renders.

:mod:`vx.effects.glitch` declares the twelve deliberate signal-damage looks as
``filter`` templates. This file pins the declaration rather than restating it:

* the family is exactly the twelve declared kinds and nothing else;
* every entry is complete (family, backend, trigger, fallback, cap, notes) and
  static (``animatable=False``), with one to three coherent ``ParamSpec``s;
* every ``{placeholder}`` names a declared parameter and every declared
  parameter appears in the template, so a typo cannot hide;
* each template formats at its defaults with no exponent and no leftover brace;
* every filter name a template uses exists in the local ``ffmpeg`` build;
* the registry and the compiler route all twelve to the ``filter`` backend, a
  keyframed instance is refused, and an out-of-range value is named;
* the twelve templates render through the real binary and every one of them
  changes the picture, plus one real EDL 3 document that compiles and renders.

Renders are evidence, not decoration: a template that ffmpeg rejects fails here
instead of at the first client render. They are skipped only when no ffmpeg
binary can be found, and the skip is printed.

    python tests/test_effects_glitch.py
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402
from vx import edl3_render  # noqa: E402
from vx.effects import glitch  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

GLITCH_KINDS = (
    "rgb_split",
    "chromatic_aberration",
    "scanlines",
    "vhs",
    "glitch_blocks",
    "datamosh",
    "echo",
    "ghosting",
    "motion_blur",
    "smear",
    "slit_scan",
    "interference",
)
#: Filters whose read is per-pixel or that hold frame history, so the entry
#: must declare a cap rather than run unbounded on a long clip.
CAPPED = ("scanlines", "datamosh", "motion_blur", "slit_scan", "interference")


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def skip(label: str, detail: object = "") -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:64} {detail}")


def ffmpeg_binary() -> str:
    declared = os.environ.get("VX_FFMPEG", "")
    if declared and Path(declared).exists():
        return declared
    return shutil.which("ffmpeg") or ""


def chain_parts(template: str) -> List[str]:
    """Split a filter chain on the commas that are real separators."""
    parts: List[str] = []
    current: List[str] = []
    quoted = False
    for char in template:
        if char == "'":
            quoted = not quoted
        if char == "," and not quoted:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def document(effects_: List[Mapping[str, Any]], source: str = "unused.mp4") -> edl3.Timeline3:
    return edl3.Timeline3.from_dict(
        {
            "version": edl3.EDL3_VERSION,
            "fps": 12,
            "size": [320, 568],
            "clips": [
                {
                    "id": "c1",
                    "source": source,
                    "src_in": 0.0,
                    "src_out": 1.0,
                    "layer": 0,
                    "dst_in": None,
                    "dst_out": None,
                    "effects": list(effects_),
                }
            ],
        }
    )


def check_shape() -> None:
    print("\n-- (a) the family is exactly the twelve declared kinds")
    check("glitch exports a dict ENTRIES", isinstance(glitch.ENTRIES, dict), type(glitch.ENTRIES).__name__)
    check("the family declares twelve entries", len(glitch.ENTRIES) == 12, len(glitch.ENTRIES))
    check(
        "the twelve declared kinds are present",
        set(GLITCH_KINDS) == set(glitch.ENTRIES),
        sorted(set(GLITCH_KINDS) ^ set(glitch.ENTRIES)),
    )
    check(
        "every entry is registered under its own kind",
        all(kind == spec.kind for kind, spec in glitch.ENTRIES.items()),
        [kind for kind, spec in glitch.ENTRIES.items() if kind != spec.kind],
    )
    check(
        "every entry names the glitch family",
        all(spec.family == "glitch" for spec in glitch.ENTRIES.values()),
        [kind for kind, spec in glitch.ENTRIES.items() if spec.family != "glitch"],
    )


def check_metadata(entries: Mapping[str, Any]) -> None:
    print("\n-- (b) every entry publishes the planning metadata")
    check(
        "every entry uses the filter backend",
        all(spec.backend == edl3.BACKEND_FILTER for spec in entries.values()),
        [kind for kind, spec in entries.items() if spec.backend != "filter"],
    )
    check(
        "no entry claims to animate",
        all(not spec.animatable and not spec.animated_params for spec in entries.values()),
        [kind for kind, spec in entries.items() if spec.animatable or spec.animated_params],
    )
    check(
        "every entry carries a one-line trigger",
        all(spec.trigger.strip() and "\n" not in spec.trigger for spec in entries.values()),
        [kind for kind, spec in entries.items() if not spec.trigger.strip()],
    )
    check(
        "every entry records its measured constraint",
        all(len(spec.notes.strip()) >= 40 for spec in entries.values()),
        [kind for kind, spec in entries.items() if len(spec.notes.strip()) < 40],
    )
    check(
        "every entry declares a known fallback",
        {spec.fallback for spec in entries.values()} <= {"skip", "static"},
        sorted({spec.fallback for spec in entries.values()}),
    )
    uncapped = [
        kind
        for kind in CAPPED
        if kind in entries and entries[kind].cap_seconds <= 0.0
    ]
    check("every per-pixel or history entry declares a cap", uncapped == [], uncapped)
    check(
        "no entry declares a negative cap",
        all(spec.cap_seconds >= 0.0 for spec in entries.values()),
        [kind for kind, spec in entries.items() if spec.cap_seconds < 0.0],
    )


def check_params(entries: Mapping[str, Any]) -> None:
    print("\n-- (c) every ParamSpec is coherent")
    check(
        "every entry declares one to three parameters",
        all(1 <= len(spec.params_spec) <= 3 for spec in entries.values()),
        {kind: len(spec.params_spec) for kind, spec in entries.items()},
    )
    check(
        "the derived name tuple is exactly the params_spec order",
        all(spec.params == tuple(p.name for p in spec.params_spec) for spec in entries.values()),
        [kind for kind, spec in entries.items() if not spec.params_spec],
    )
    bounded = [
        f"{kind}.{param.name}"
        for kind, spec in entries.items()
        for param in spec.params_spec
        if not (param.lo <= param.default <= param.hi)
    ]
    check("every default sits inside its declared range", bounded == [], bounded)
    duplicates = [
        kind
        for kind, spec in entries.items()
        if len({p.name for p in spec.params_spec}) != len(spec.params_spec)
    ]
    check("no entry declares a parameter twice", duplicates == [], duplicates)
    check(
        "no parameter declares a negative step",
        all(p.step >= 0.0 for spec in entries.values() for p in spec.params_spec),
        [
            f"{kind}.{p.name}"
            for kind, spec in entries.items()
            for p in spec.params_spec
            if p.step < 0.0
        ],
    )
    check(
        "a signed offset entry really declares both signs",
        glitch.ENTRIES["rgb_split"].param("offset_b").default < 0.0
        and glitch.ENTRIES["chromatic_aberration"].param("chroma_b").default < 0.0,
        [
            glitch.ENTRIES["rgb_split"].default_params(),
            glitch.ENTRIES["chromatic_aberration"].default_params(),
        ],
    )


def check_templates(entries: Mapping[str, Any]) -> None:
    print("\n-- (d) every template is complete and formats")
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
    scientific: List[str] = []
    for kind, spec in entries.items():
        try:
            fragment = edl3_render.filter_fragment(edl3.Effect(kind=kind), spec)
        except Exception as exc:  # noqa: BLE001 - an unformattable template fails
            broken.append(f"{kind}: {type(exc).__name__}: {exc}")
            continue
        if "{" in fragment or "}" in fragment:
            broken.append(f"{kind}: unformatted placeholder in {fragment!r}")
        for param in spec.params_spec:
            text = edl3_render.filter_number(param.default)
            if "e" in text.lower():
                scientific.append(f"{kind}.{param.name}={text}")
    check("every template formats at its declared defaults", broken == [], broken)
    check("no formatted value uses scientific notation", scientific == [], scientific)
    empties = [kind for kind, spec in entries.items() if not spec.filter_template.strip()]
    check("every entry carries a template", empties == [], empties)
    check(
        "the comma inside a quoted expression is not a chain separator",
        chain_parts(glitch.ENTRIES["scanlines"].filter_template) == [
            "geq=lum='lum(X,Y)*(1-{depth}*max(0,sin(2*PI*(Y/{period}-{speed}*T))))'"
        ],
        chain_parts(glitch.ENTRIES["scanlines"].filter_template),
    )
    check(
        "a stacked template really declares two filters",
        [part.split("=")[0] for part in chain_parts(glitch.ENTRIES["vhs"].filter_template)]
        == ["noise", "chromashift"],
        chain_parts(glitch.ENTRIES["vhs"].filter_template),
    )


def check_filters_exist(entries: Mapping[str, Any]) -> None:
    print("\n-- (e) every named filter exists in the local ffmpeg build")
    binary = ffmpeg_binary()
    if not binary:
        skip("every named filter exists in the local ffmpeg build", "no ffmpeg")
        return
    listed = subprocess.run(
        [binary, "-hide_banner", "-filters"], capture_output=True, text=True
    )
    known = {
        line.split()[1]
        for line in listed.stdout.splitlines()
        if len(line.split()) > 2 and line.split()[1].isidentifier()
    }
    used = sorted(
        {
            part.split("=")[0].strip()
            for spec in entries.values()
            for part in chain_parts(spec.filter_template)
        }
    )
    missing = [name for name in used if name not in known]
    check("every named filter exists in the local ffmpeg build", missing == [], missing)
    check("the family really uses more than one mechanism", len(used) >= 6, used)


def check_registry(entries: Mapping[str, Any], source: str) -> None:
    print("\n-- (f) the registry and the compiler route all twelve to filter")
    check(
        "every glitch kind is in EFFECT_REGISTRY",
        set(entries) <= set(edl3.EFFECT_REGISTRY),
        sorted(set(entries) - set(edl3.EFFECT_REGISTRY)),
    )
    check(
        "every glitch kind is renderable",
        set(entries) <= set(edl3.RENDERABLE_EFFECTS),
        sorted(set(entries) - set(edl3.RENDERABLE_EFFECTS)),
    )
    check(
        "the pure rule returns filter for every glitch kind",
        all(edl3.select_backend(kind, 0, 1.0) == "filter" for kind in entries),
        {kind: edl3.select_backend(kind, 0, 1.0) for kind in entries},
    )
    check(
        "the compiler registers exactly the filter backend",
        all(
            edl3_render.IMPLEMENTED_BACKENDS.get(kind) == frozenset({"filter"})
            for kind in entries
        ),
        {kind: sorted(edl3_render.IMPLEMENTED_BACKENDS.get(kind, ())) for kind in entries},
    )
    missing: List[str] = []
    for kind, spec in entries.items():
        fragment = edl3_render.filter_fragment(edl3.Effect(kind=kind), spec)
        plan = edl3_render.compile_timeline(
            document([{"kind": kind, "params": {}}], source=source)
        )
        if fragment not in str(plan.get("filter_script", "")):
            missing.append(f"{kind}: {fragment}")
    check(
        "every template reaches the compiled filter script",
        missing == [],
        [entry[:60] for entry in missing],
    )


def check_validation(source: str) -> None:
    print("\n-- (g) the generic validator enforces the declared ranges")
    param = glitch.ENTRIES["scanlines"].param("depth")
    assert param is not None
    expected = f"scanlines.depth=4.0 outside [{param.lo:g}, {param.hi:g}]"
    check(
        "an out-of-range value returns the declared-range message",
        edl3.param_domain_problem("scanlines", "depth", 4.0) == expected,
        edl3.param_domain_problem("scanlines", "depth", 4.0),
    )
    problems = document([{"kind": "scanlines", "params": {"depth": 4.0}}]).validate()
    ranges = [
        problem for problem in problems
        if problem["code"] == "EFFECT_RANGE" and problem["where"].endswith("depth")
    ]
    check(
        "a document with that value reports EFFECT_RANGE",
        len(ranges) == 1 and ranges[0]["detail"] == expected,
        ranges,
    )
    check(
        "an unknown name is named, not guessed",
        edl3.param_domain_problem("vhs", "grainy", 1.0)
        == "vhs.grainy is not a declared parameter",
        edl3.param_domain_problem("vhs", "grainy", 1.0),
    )
    check(
        "an in-range value is accepted",
        all(
            edl3.param_domain_problem(kind, p.name, p.default) is None
            for kind, spec in glitch.ENTRIES.items()
            for p in spec.params_spec
        ),
        "defaults refused",
    )
    keyed = document(
        [
            {
                "kind": "rgb_split",
                "params": {},
                "keyframes": {"offset_r": [{"t": 0.0, "value": 2.0}, {"t": 0.5, "value": 8.0}]},
            }
        ],
        source=source,
    )
    codes = [problem["code"] for problem in keyed.validate()]
    check(
        "a keyframed instance reports EFFECT_NOT_ANIMATABLE",
        codes.count("EFFECT_NOT_ANIMATABLE") == 1,
        codes,
    )
    refusal = ""
    try:
        edl3_render.compile_timeline(keyed)
    except edl3_render.CompileError as exc:
        refusal = exc.code
    check("the compiler refuses it with EFFECT_NOT_ANIMATABLE", refusal == "EFFECT_NOT_ANIMATABLE", refusal)


def _render(binary: str, target: Path, vf: str, seek: float) -> Tuple[bool, str]:
    proc = subprocess.run(
        [
            binary, "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=s=320x568:r=12:d=2",
            "-ss", str(seek), "-vf", vf, "-frames:v", "1", str(target),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not target.exists():
        return False, (proc.stderr.strip().splitlines() or ["no output"])[-1]
    return True, hashlib.sha256(target.read_bytes()).hexdigest()


def check_renders(entries: Mapping[str, Any]) -> None:
    print("\n-- (h) every template renders through the real binary and changes the frame")
    binary = ffmpeg_binary()
    if not binary:
        skip("every template renders and changes the frame", "no ffmpeg")
        return
    with tempfile.TemporaryDirectory(prefix="vx_glitch_gate_") as tmp:
        work = Path(tmp)
        ok, base = _render(binary, work / "base.png", "null", 0.6)
        check("the un-effected reference frame renders", ok, base[:12])
        literal: List[str] = []
        changed: List[str] = []
        sizes: Dict[str, int] = {}
        for kind, spec in entries.items():
            fragment = edl3_render.filter_fragment(edl3.Effect(kind=kind), spec)
            first_ok, _ = _render(binary, work / f"{kind}_first.png", fragment, 0.0)
            late_ok, digest = _render(binary, work / f"{kind}.png", fragment, 0.6)
            if not first_ok:
                literal.append(kind)
            if not late_ok or digest == base:
                changed.append(kind)
            if (work / f"{kind}.png").exists():
                sizes[kind] = (work / f"{kind}.png").stat().st_size
        check("every template renders at -frames:v 1", literal == [], literal)
        check(
            "every template changes the picture",
            changed == [],
            changed,
        )
        check(
            "every render wrote a non-trivial PNG",
            len(sizes) == 12 and all(size > 4000 for size in sizes.values()),
            sizes,
        )


def check_edl3_render() -> None:
    print("\n-- (i) one real EDL 3 document compiles and renders")
    binary = ffmpeg_binary()
    if not binary:
        skip("one real EDL 3 document compiles and renders", "no ffmpeg")
        return
    with tempfile.TemporaryDirectory(prefix="vx_glitch_edl3_") as tmp:
        work = Path(tmp)
        source = work / "source.mp4"
        built = subprocess.run(
            [
                binary, "-y", "-v", "error",
                "-f", "lavfi", "-i", "testsrc2=s=320x568:r=12:d=4",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "6", str(source),
            ],
            capture_output=True,
            text=True,
        )
        check("the EDL 3 source renders", built.returncode == 0 and source.exists(), built.returncode)
        timeline = edl3.Timeline3.from_dict(
            {
                "version": edl3.EDL3_VERSION,
                "fps": 12,
                "size": [320, 568],
                "clips": [
                    {
                        "id": f"c{index}",
                        "source": str(source),
                        "src_in": float(index),
                        "src_out": float(index) + 1.0,
                        "layer": 0,
                        "dst_in": None,
                        "dst_out": None,
                        "effects": [{"kind": kind, "params": params}],
                    }
                    for index, (kind, params) in enumerate(
                        (
                            ("scanlines", {"period": 4.0, "depth": 0.6, "speed": 0.25}),
                            ("vhs", {"grain": 18.0, "shift": 6.0}),
                            ("datamosh", {"block": 32.0, "frames": 3.0}),
                        )
                    )
                ],
            }
        )
        problems = timeline.validate()
        check("the document validates clean", problems == [], problems)
        output = work / "glitch.mp4"
        try:
            edl3_render.render(timeline, output, workdir=work / "stage")
        except Exception as exc:  # noqa: BLE001 - a failed render is a failure
            check("the EDL 3 document renders", False, f"{type(exc).__name__}: {exc}")
            return
        check(
            "the EDL 3 document renders",
            output.exists() and output.stat().st_size > 10000,
            output.stat().st_size if output.exists() else 0,
        )
        frame = work / "frame.png"
        extracted = subprocess.run(
            [binary, "-y", "-v", "error", "-ss", "1.5", "-i", str(output),
             "-frames:v", "1", str(frame)],
            capture_output=True,
            text=True,
        )
        check(
            "a frame can be extracted from the rendered document",
            extracted.returncode == 0 and frame.exists() and frame.stat().st_size > 4000,
            frame.stat().st_size if frame.exists() else 0,
        )


def main() -> int:
    entries = glitch.ENTRIES
    with tempfile.TemporaryDirectory(prefix="vx_glitch_doc_") as tmp:
        # compile_timeline checks that the source path exists and never decodes it,
        # so a zero-byte placeholder is enough to reach the filter plan.
        stand_in = Path(tmp) / "source.mp4"
        stand_in.write_bytes(b"")
        check_shape()
        check_metadata(entries)
        check_params(entries)
        check_templates(entries)
        check_filters_exist(entries)
        check_registry(entries, str(stand_in))
        check_validation(str(stand_in))
    check_renders(entries)
    check_edl3_render()
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
