"""Crop boxes are in source time; the crop filter runs on timeline time.

With ``cut_silence`` the two clocks diverge by the cumulative removed spans.
This test pins the source -> timeline remap, the render wiring, and a real
two-cut render where a moving white square must stay centred.

python tests/test_crop_remap.py [--remap-only|--render-only|--real-only]
"""

from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import autoframe, contract, render  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


# --------------------------------------------------------------------------- #
# tiny AST evaluator for the ffmpeg crop expression
# --------------------------------------------------------------------------- #


def _eval_expr(expr: str, t: float) -> float:
    """Evaluate an ffmpeg crop expression (if/lt/arithmetic/t) at time ``t``."""
    text = expr.replace("\\,", ",").replace("if(", "iff(")
    tree = ast.parse(text, mode="eval")

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id == "t":
                return float(t)
            raise ValueError(f"unknown name {node.id!r}")
        if isinstance(node, ast.UnaryOp):
            value = ev(node.operand)
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return value
            raise ValueError(type(node.op).__name__)
        if isinstance(node, ast.BinOp):
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            raise ValueError(type(node.op).__name__)
        if isinstance(node, ast.Call):
            name = node.func.id  # type: ignore[attr-defined]
            args = [ev(arg) for arg in node.args]
            if name == "iff":
                return args[1] if args[0] else args[2]
            if name == "lt":
                return 1.0 if args[0] < args[1] else 0.0
            raise ValueError(f"unknown function {name!r}")
        raise ValueError(type(node).__name__)

    return ev(tree)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


def _crop_plan(boxes: list, notes: list, mode: str = "track") -> contract.CropPlan:
    return contract.CropPlan(
        mode=mode,
        aspect="9:16",
        src_w=1920,
        src_h=1080,
        crop_w=608,
        crop_h=1080,
        out_w=1080,
        out_h=1920,
        boxes=boxes,
        notes=notes,
    )


def _media() -> dict:
    return {
        "path": "x.mp4",
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "has_audio": False,
        "duration": 5.0,
    }


def _render_plan() -> contract.EditPlan:
    return contract.EditPlan(
        output={"aspect": "9:16", "width": 1080, "height": 1920},
        cuts=[{"in": 0.0, "out": 2.0}, {"in": 3.0, "out": 5.0}],
        render={"precision": "full"},
        treatments=[{"name": "autoframe", "enabled": True, "params": {}}],
    )


def _track_crop() -> dict:
    return {
        "mode": "track",
        "aspect": "9:16",
        "src_w": 1920,
        "src_h": 1080,
        "crop_w": 608,
        "crop_h": 1080,
        "out_w": 1080,
        "out_h": 1920,
        "boxes": [
            {"t": 0.0, "x": 0, "y": 0},
            {"t": 1.0, "x": 100, "y": 0},
            {"t": 3.0, "x": 300, "y": 0},
            {"t": 4.0, "x": 400, "y": 0},
        ],
        "notes": ["fps=30"],
    }


# --------------------------------------------------------------------------- #
# 1. remap unit surface
# --------------------------------------------------------------------------- #


def run_remap() -> None:
    print("\n-- remap --")
    segments = [(0.0, 2.0), (3.0, 5.0)]
    plan = _crop_plan(
        [
            {"t": 1.0, "x": 100, "y": 0},
            {"t": 2.5, "x": 999, "y": 0},
            {"t": 3.0, "x": 300, "y": 0},
            {"t": 4.0, "x": 400, "y": 0},
        ],
        ["fps=30", "seg=4.0"],
    )
    remapped = autoframe.remap_to_timeline(plan, segments, fps=30.0)
    check(
        "box in the removed gap is dropped",
        all(box["x"] != 999 for box in remapped.boxes),
        [box["x"] for box in remapped.boxes],
    )
    seg_notes = [
        float(note.split("=", 1)[1])
        for note in remapped.notes
        if note.startswith("seg=")
    ]
    check("seg notes are cumulative timeline boundaries", seg_notes == [2.0], seg_notes)

    expr = autoframe.crop_expression(remapped, offset_s=0.0)

    def x(value: float) -> float:
        return _eval_expr(expr["x"], value)

    check("x(1.0) = 100", abs(x(1.0) - 100) <= 1e-6, x(1.0))
    check("x(2.0) = 300", abs(x(2.0) - 300) <= 1e-6, x(2.0))
    check("x(2.5) = 350", abs(x(2.5) - 350) <= 1e-6, x(2.5))
    check("x(3.0) = 400", abs(x(3.0) - 400) <= 1e-6, x(3.0))
    check("hold at 1.999", abs(x(1.999) - 100) <= 1e-6, x(1.999))
    check("hold at 3.999", abs(x(3.999) - 400) <= 1e-6, x(3.999))

    empty = autoframe.remap_to_timeline(_crop_plan([], ["fps=30"]), segments, fps=30.0)
    empty_expr = autoframe.crop_expression(empty, offset_s=0.0)
    check(
        "empty boxes -> centre 656/0",
        empty_expr == {"x": "656", "y": "0"},
        empty_expr,
    )

    duplicate = autoframe.remap_to_timeline(
        _crop_plan(
            [{"t": 1.0, "x": 100, "y": 0}, {"t": 1.0, "x": 200, "y": 0}],
            ["fps=30"],
        ),
        [(0.0, 2.0)],
        fps=30.0,
    )
    at_one = [box for box in duplicate.boxes if abs(box["t"] - 1.0) <= 1e-9]
    check(
        "duplicate t: last wins",
        len(at_one) == 1 and at_one[0]["x"] == 200,
        at_one,
    )

    no_fps = autoframe.remap_to_timeline(
        _crop_plan([{"t": 1.0, "x": 100, "y": 0}], ["seg=4.0"]),
        segments,
        fps=30,
    )
    check("fps note prepended when absent", no_fps.notes[0] == "fps=30", no_fps.notes)

    keep_fps = autoframe.remap_to_timeline(
        _crop_plan([{"t": 1.0, "x": 100, "y": 0}], ["fps=25", "seg=4.0"]),
        segments,
        fps=30.0,
    )
    fps_notes = [note for note in keep_fps.notes if note.startswith("fps=")]
    check("existing fps note is kept", fps_notes == ["fps=25"], fps_notes)


# --------------------------------------------------------------------------- #
# 2. render wiring
# --------------------------------------------------------------------------- #


def run_render() -> None:
    print("\n-- render wiring --")
    plan, media = _render_plan(), _media()

    plain = render._crop_scale(plan, media, None, "full")
    check(
        "no crop plan -> centre crop",
        plain == "crop=608:1080:656:0,scale=1080:1920:flags=lanczos",
        plain,
    )

    lock = {
        "mode": "lock",
        "crop_w": 606,
        "crop_h": 1080,
        "boxes": [{"x": 0, "y": 0}],
    }
    locked = render._crop_scale(plan, media, lock, "full")
    check(
        "lock branch unchanged",
        locked == "crop=606:1080:0:0,scale=1080:1920:flags=lanczos",
        locked,
    )

    track = render._crop_scale(plan, media, _track_crop(), "full")
    check("track branch emits a time expression", "if(lt(t" in track, track[:80])

    calls: list = []
    original = autoframe.remap_to_timeline

    def recorder(crop_plan, segments, fps=None):
        calls.append((list(segments), fps))
        return original(crop_plan, segments, fps=fps)

    autoframe.remap_to_timeline = recorder  # type: ignore[assignment]
    try:
        render._crop_scale(plan, media, _track_crop(), "full")
    finally:
        autoframe.remap_to_timeline = original  # type: ignore[assignment]
    check(
        "remap receives the concat segments",
        bool(calls) and calls[0][0] == [(0.0, 2.0), (3.0, 5.0)],
        calls[0][0] if calls else None,
    )
    check(
        "remap receives the media fps",
        bool(calls) and abs(float(calls[0][1]) - 30.0) <= 1e-9,
        calls[0][1] if calls else None,
    )

    check(
        "build_filtergraph crop_plan=None is a no-op",
        render.build_filtergraph(plan, media)
        == render.build_filtergraph(plan, media, crop_plan=None),
    )
    check("_track_expr is gone", not hasattr(render, "_track_expr"))


# --------------------------------------------------------------------------- #
# 3. real surface: two ffmpeg renders
# --------------------------------------------------------------------------- #


def _centroid(frame) -> tuple[float, int]:
    import numpy as np

    gray = frame if frame.ndim == 2 else frame[:, :, 0]
    ys, xs = np.nonzero(gray > 200)
    if len(xs) == 0:
        return float("nan"), 0
    return float(xs.mean()), int(len(xs))


def _frame(path: Path, index: int):
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = cap.read()
        if not ok:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    finally:
        cap.release()


def run_real() -> None:
    print("\n-- real surface --")
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"[SKIP] cv2/numpy unavailable: {exc}")
        return

    from vx import ffmpeg as ff

    try:
        ff.ffmpeg()
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"[SKIP] ffmpeg unavailable: {exc}")
        return

    with tempfile.TemporaryDirectory(prefix="vx_crop_remap_") as tmp:
        work = Path(tmp)
        source = work / "moving_square.mp4"
        proc = ff.run_ff(
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=1920x1080:r=30:d=5",
                "-f",
                "lavfi",
                "-i",
                "color=c=white:s=80x80:r=30:d=5",
                "-filter_complex",
                "[0:v][1:v]overlay=x='200+300*t':y=500",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                str(source),
            ]
        )
        if not proc.ok:  # pragma: no cover - environment dependent
            print(f"[SKIP] synthesis failed: {proc.tail(5)}")
            return

        media = {
            "path": str(source),
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "has_audio": False,
            "duration": 5.0,
        }
        plan = contract.EditPlan(
            output={"aspect": "9:16", "width": 1080, "height": 1920},
            cuts=[{"in": 0.0, "out": 2.0}, {"in": 3.0, "out": 5.0}],
            render={
                "precision": "full",
                "encoder": "libx264",
                "preset": "veryfast",
                "crf": 20,
            },
            treatments=[{"name": "autoframe", "enabled": True, "params": {}}],
        )
        crop = _track_crop()
        crop["boxes"] = [
            {
                "t": round(i * 0.5, 4),
                "x": max(0, min(1312, int(round(240 + 300 * (i * 0.5) - 304)))),
                "y": 0,
            }
            for i in range(11)
        ]
        crop["notes"] = ["fps=30"]

        positive = render.render(
            plan, media, precision="full", workdir=work / "pos", crop_plan=crop
        )
        frame_105 = _frame(Path(positive["path"]), 105)
        frame_30 = _frame(Path(positive["path"]), 30)
        cx_105, area_105 = _centroid(frame_105)
        cx_30, area_30 = _centroid(frame_30)
        print(
            f"    positive: frame105 cx={cx_105:.1f} area={area_105}; "
            f"frame30 cx={cx_30:.1f} area={area_30}"
        )
        check(
            "frame 105 (timeline 3.5) stays centred",
            abs(cx_105 - 540) <= 12,
            f"{cx_105:.1f}",
        )
        check("frame 105 has a visible square", area_105 > 5000, area_105)
        check(
            "frame 30 (timeline 1.0) stays centred",
            abs(cx_30 - 540) <= 12,
            f"{cx_30:.1f}",
        )
        check("frame 30 has a visible square", area_30 > 5000, area_30)

        original = autoframe.remap_to_timeline

        def identity(crop_plan, segments, fps=None):
            return crop_plan

        autoframe.remap_to_timeline = identity  # type: ignore[assignment]
        try:
            negative = render.render(
                plan, media, precision="full", workdir=work / "neg", crop_plan=crop
            )
        finally:
            autoframe.remap_to_timeline = original  # type: ignore[assignment]
        neg_frame = _frame(Path(negative["path"]), 105)
        neg_cx, neg_area = _centroid(neg_frame)
        print(f"    negative: frame105 cx={neg_cx:.1f} area={neg_area}")
        check(
            "without remap the square drifts right",
            neg_cx > 800,
            f"{neg_cx:.1f}",
        )


# --------------------------------------------------------------------------- #
# entry
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    remap_only = "--remap-only" in args
    render_only = "--render-only" in args
    real_only = "--real-only" in args
    run_all = not (remap_only or render_only or real_only)

    if run_all or remap_only:
        run_remap()
    if run_all or render_only:
        run_render()
    if run_all or real_only:
        run_real()

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
