"""F2-T08: nested sequences behave like a source, not like a special case.

The plan is checked structurally (order, render count, keys) and then proved on
real pixels: the inner timeline is rendered once, its output becomes the outer
clip's source, and the outer frame must match the inner frame it came from.

    python tests/test_edl3_nested.py

Exit 0 means every check passed. Targets 320x568 and stays under 120 s.
"""

from __future__ import annotations

import re
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402
from vx import edl3_nested  # noqa: E402
from vx import edl3_render  # noqa: E402
from vx import golden  # noqa: E402
from vx.ffmpeg import run_ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FPS = 12
SIZE = (320, 568)
SECONDS = 1.0
KEY = re.compile(r"^[A-Za-z0-9_.-]+$")


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:62} {reason}")


# ------------------------------------------------------------------ helpers


def make_source(path: Path, color: str = "red") -> str:
    proc = run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={SIZE[0]}x{SIZE[1]}:r={FPS}:d={SECONDS}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        loglevel="error",
    )
    return "" if proc.ok and path.exists() else proc.tail()


def source_clip(clip_id: str, source: str, effect: Any = None) -> edl3.Clip3:
    return edl3.Clip3(
        id=clip_id,
        source=source,
        src_in=0.0,
        src_out=SECONDS,
        effects=[effect] if effect is not None else [],
    )


def sequence_clip(clip_id: str, sequence_id: str, start: float) -> edl3.Clip3:
    return edl3.Clip3(
        id=clip_id,
        sequence=sequence_id,
        src_in=0.0,
        src_out=SECONDS,
        dst_in=start,
        dst_out=start + SECONDS,
    )


def timeline(
    clips: List[edl3.Clip3], sequences: Dict[str, Any] = None
) -> edl3.Timeline3:
    return edl3.Timeline3(
        fps=FPS, size=SIZE, clips=clips, sequences=dict(sequences or {})
    )


def nesting_error(call, **kwargs) -> Tuple[str, str]:
    """Run a call expecting a NestingError; return (code, message)."""
    try:
        call(**kwargs)
    except edl3_nested.NestingError as exc:
        return exc.code, str(exc)
    except Exception as exc:  # noqa: BLE001 - a wrong exception type is a failure
        return f"__WRONG_TYPE__{type(exc).__name__}", str(exc)
    return "", ""


def chain_document(levels: int, source: str) -> edl3.Timeline3:
    """A document whose root references s1 -> s2 -> ... -> s{levels}."""
    leaf = timeline([source_clip(f"s{levels}_leaf", source)])
    inner = leaf
    for index in range(levels - 1, 0, -1):
        sequence_id = f"s{index + 1}"
        inner = timeline(
            [sequence_clip(f"s{index}_clip", sequence_id, 0.0)],
            {sequence_id: edl3.NestedSequence(id=sequence_id, timeline=inner)},
        )
    return timeline(
        [sequence_clip("root", "s1", 0.0)],
        {"s1": edl3.NestedSequence(id="s1", timeline=inner)},
    )


# --------------------------------------------------------------------- main


def main() -> int:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vx_nested_") as tmp:
        work = Path(tmp)
        try:
            run_checks(work)
        except Exception:  # noqa: BLE001 - a crash is a failing test, never a skip
            traceback.print_exc()
            check("test harness completed without an unexpected exception", False)

    elapsed = time.monotonic() - started
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  ({elapsed:.1f}s)")
    check("suite stays under the 120 s ceiling", elapsed < 120.0, f"{elapsed:.1f}s")
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


def run_checks(work: Path) -> None:
    # --- (g) two levels, each distinct sequence rendered exactly once -----
    inner = timeline([source_clip("i1", "asset.mp4")])
    outer = timeline(
        [sequence_clip("o1", "inner", 0.0), sequence_clip("o2", "inner", 1.0)],
        {"inner": edl3.NestedSequence(id="inner", timeline=inner)},
    )
    root = timeline(
        [sequence_clip("r1", "outer", 0.0), sequence_clip("r2", "outer", 2.0)],
        {"outer": edl3.NestedSequence(id="outer", timeline=outer)},
    )
    plan = edl3_nested.resolve(root)
    ids = [entry["id"] for entry in plan["renders"]]
    print(f"  nesting order: {plan['order']}")
    print(f"  renders: {ids}  sources: {plan['sources']}")
    check("(g) two-level nesting reports depth 2", plan["depth"] == 2, plan["depth"])
    check(
        "(g) one render per distinct inner sequence",
        len(plan["renders"]) == 2 and sorted(ids) == ["inner", "outer"],
        ids,
    )
    check(
        "(g) the sequence used twice is rendered once",
        ids.count("inner") == 1 and ids.count("outer") == 1,
        {name: ids.count(name) for name in set(ids)},
    )
    check(
        "(g) children render before their parent",
        plan["order"] == ["inner", "outer"],
        plan["order"],
    )
    outer_key = plan["renders"][ids.index("outer")]["virtual_source"]
    inner_key = plan["renders"][ids.index("inner")]["virtual_source"]
    check(
        "(g) both root clips point at the same outer render",
        plan["sources"].get("r1") == outer_key == plan["sources"].get("r2"),
        plan["sources"].get("r1"),
    )
    check(
        "(g) both clips inside the outer sequence point at the inner render",
        plan["sources"].get("outer/o1") == inner_key == plan["sources"].get("outer/o2"),
        plan["sources"].get("outer/o1"),
    )
    check(
        "(g) every render carries its own timeline and level",
        all(
            isinstance(entry["timeline"], edl3.Timeline3) and entry["depth"] >= 1
            for entry in plan["renders"]
        ),
        [(entry["id"], entry["depth"]) for entry in plan["renders"]],
    )
    check(
        "(g) a document without nesting resolves to an empty plan",
        edl3_nested.resolve(timeline([source_clip("only", "asset.mp4")]))
        == {"depth": 0, "order": [], "renders": [], "sources": {}},
    )

    # --- (h) cycle and depth ceiling --------------------------------------
    a_inner = timeline([sequence_clip("a1", "b", 0.0)])
    b_inner = timeline([sequence_clip("b1", "a", 0.0)])
    a_inner.sequences = {"b": edl3.NestedSequence(id="b", timeline=b_inner)}
    b_inner.sequences = {"a": edl3.NestedSequence(id="a", timeline=a_inner)}
    cyclic = timeline(
        [sequence_clip("root", "a", 0.0)],
        {"a": edl3.NestedSequence(id="a", timeline=a_inner)},
    )
    code, message = nesting_error(edl3_nested.resolve, doc=cyclic)
    print(f"  cycle refusal: {message}")
    check(
        "(h) a reference cycle is refused with NESTING_CYCLE",
        code == "NESTING_CYCLE",
        code,
    )

    deep = chain_document(4, "asset.mp4")
    code, message = nesting_error(edl3_nested.resolve, doc=deep, max_depth=3)
    print(f"  depth refusal: {message}")
    check(
        "(h) depth 4 with max 3 is refused with NESTING_TOO_DEEP",
        code == "NESTING_TOO_DEEP",
        code,
    )
    four = edl3_nested.resolve(deep, max_depth=4)
    check(
        "(h) the same document is accepted at max_depth 4",
        four["depth"] == 4 and len(four["renders"]) == 4,
        four["depth"],
    )
    three = edl3_nested.resolve(chain_document(3, "asset.mp4"), max_depth=3)
    check("(h) depth 3 at the declared ceiling is accepted", three["depth"] == 3)
    code, _ = nesting_error(
        edl3_nested.resolve,
        doc=timeline([sequence_clip("root", "ghost", 0.0)]),
    )
    check(
        "(h) an undeclared sequence is refused with NESTING_UNKNOWN",
        code == "NESTING_UNKNOWN",
        code,
    )
    code, _ = nesting_error(edl3_nested.resolve, doc=deep, max_depth=0)
    check(
        "(h) max_depth 0 is refused with NESTING_DEPTH_INVALID",
        code == "NESTING_DEPTH_INVALID",
        code,
    )

    # --- (i) virtual source ids -------------------------------------------
    first = edl3_nested.virtual_source_id("inner", inner)
    second = edl3_nested.virtual_source_id("inner", inner)
    print(f"  virtual source id: {first}")
    check("(i) virtual_source_id is deterministic", first == second, first)
    check("(i) the key is legal EDL source syntax", bool(KEY.match(first)), first)
    check(
        "(i) the id depends on the document, not only on the name",
        first != edl3_nested.virtual_source_id("inner", outer),
    )
    hostile = edl3_nested.virtual_source_id("my seq/../über", inner)
    check(
        "(i) a hostile sequence name still yields a legal key",
        bool(KEY.match(hostile)),
        hostile,
    )

    # --- (j) the virtual source really carries the inner result -----------
    asset = work / "asset.mp4"
    problem = make_source(asset)
    if problem:
        skip("(j) colour fixture", problem)
        return
    strong = edl3.Effect(
        kind="color",
        params={"brightness": -0.2, "contrast": 1.2, "saturation": 2.0},
        keyframes={},
    )
    inner_doc = timeline([source_clip("i1", str(asset), strong)])
    outer_doc = timeline(
        [sequence_clip("o1", "inner", 0.0)],
        {"inner": edl3.NestedSequence(id="inner", timeline=inner_doc)},
    )
    inner_plan = edl3_nested.resolve(outer_doc)
    check(
        "(j) the plan names a virtual source for the outer clip",
        inner_plan["sources"].get("o1") == inner_plan["renders"][0]["virtual_source"],
        inner_plan["sources"].get("o1"),
    )

    inner_render = work / "inner_render.mp4"
    edl3_render.render(inner_doc, inner_render, workdir=work / "inner")
    render_paths = {
        entry["virtual_source"]: str(inner_render) for entry in inner_plan["renders"]
    }
    wired = render_paths[inner_plan["sources"]["o1"]]
    outer_resolved = timeline([source_clip("o1", wired)])
    outer_render = work / "outer_render.mp4"
    edl3_render.render(outer_resolved, outer_render, workdir=work / "outer")

    inner_frames = golden.capture(inner_render, [0.5], work / "frames_inner")
    outer_frames = golden.capture(outer_render, [0.5], work / "frames_outer")
    asset_frames = golden.capture(asset, [0.5], work / "frames_asset")
    inner_png = Path(inner_frames["frames"][0]["path"])
    outer_png = Path(outer_frames["frames"][0]["path"])
    asset_png = Path(asset_frames["frames"][0]["path"])
    same = golden._pixel_diff(inner_png, outer_png)
    versus_asset = golden._pixel_diff(asset_png, outer_png)
    print(
        f"  MAD(inner, outer) = {same['mad']:.3f} "
        f"(r {same['mad_r']} g {same['mad_g']} b {same['mad_b']}); "
        f"MAD(asset, outer) = {versus_asset['mad']:.3f}"
    )
    check(
        "(j) the outer frame is the inner render (MAD <= 3)",
        same["mad"] <= 3.0,
        f"MAD {same['mad']:.3f}",
    )
    check(
        "(j) the outer frame is not the raw asset",
        versus_asset["mad"] > 3.0,
        f"MAD {versus_asset['mad']:.3f}",
    )


if __name__ == "__main__":
    raise SystemExit(main())
