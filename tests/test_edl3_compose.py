"""F2-T07: PiP, masks, blend modes and chroma key, measured on real pixels.

Every fragment is spliced into a real ``-filter_complex_script`` graph and the
result is decoded to RGB. Nothing here trusts a filter string: the assertions
read pixel triples and mean luma out of ffmpeg itself.

    python tests/test_edl3_compose.py

Exit 0 means every check passed. The suite targets 320x568 and stays under the
120 s ceiling declared by the ledger.
"""

from __future__ import annotations

import re
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3_compose  # noqa: E402
from vx.ffmpeg import run_ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FPS = 12
WIDTH = 320
HEIGHT = 568
SECONDS = 0.5
YAVG = re.compile(r"signalstats\.YAVG=([0-9.]+)")


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


def make_source(path: Path, color: str) -> str:
    """A flat-colour clip of SECONDS at the test frame rate."""
    proc = run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={WIDTH}x{HEIGHT}:r={FPS}:d={SECONDS}",
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


def render_graph(
    inputs: Sequence[Path], chains: Sequence[str], output: Path, work: Path
) -> str:
    """Run one filtergraph through ``-filter_complex_script``; '' means success."""
    script = work / f"{output.stem}_graph.txt"
    script.write_text(";\n".join(chains) + "\n", encoding="utf-8", newline="\n")
    args: List[Any] = []
    for path in inputs:
        args.extend(["-i", str(path)])
    args.extend(
        [
            "-filter_complex_script",
            str(script),
            "-map",
            "[out]",
            "-an",
            "-sn",
            "-dn",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-t",
            f"{SECONDS:.3f}",
            str(output),
        ]
    )
    proc = run_ff(args, loglevel="error")
    if not proc.ok or not output.exists():
        return proc.tail()
    return ""


def frame_rgb(video: Path, t: float, work: Path, tag: str) -> bytes:
    """Decode the frame at ``t`` to packed rgb24 (full range)."""
    raw = work / f"frame_{tag}.raw"
    if raw.exists():
        raw.unlink()
    proc = run_ff(
        [
            "-ss",
            f"{t:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-an",
            "-sn",
            "-dn",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            str(raw),
        ],
        loglevel="error",
    )
    if not proc.ok or not raw.exists():
        raise RuntimeError(f"frame at {t:.3f}s undecodable: {proc.tail()}")
    return raw.read_bytes()


def pixel(data: bytes, x: int, y: int) -> Tuple[int, int, int]:
    offset = (y * WIDTH + x) * 3
    return data[offset], data[offset + 1], data[offset + 2]


def mean_luma(video: Path, t: float) -> float:
    proc = run_ff(
        [
            "-ss",
            f"{t:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            "signalstats,metadata=mode=print:file=-",
            "-f",
            "null",
            "-",
        ],
        loglevel="error",
    )
    match = YAVG.search(proc.stdout or "")
    if not proc.ok or match is None:
        raise RuntimeError(f"mean_luma({t}) failed: {proc.tail()}")
    return float(match.group(1))


def refusal(builder, **kwargs) -> str:
    """Call a builder expecting a refusal; return its code or a wrong-type tag."""
    try:
        builder(**kwargs)
    except edl3_compose.ComposeError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001 - a wrong exception type is a failure
        return f"__WRONG_TYPE__{type(exc).__name__}: {exc}"
    return ""


def show(name: str, fragment: Dict[str, Any]) -> None:
    print(f"  fragment {name}: " + " ; ".join(fragment["filters"]))
    print(f"           consumes {fragment['consumes']} -> {fragment['produces']}")


# --------------------------------------------------------------------- main


def main() -> int:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vx_compose_") as tmp:
        work = Path(tmp)
        base_blue = work / "base_blue.mp4"
        fg_red = work / "fg_red.mp4"
        fg_green = work / "fg_green.mp4"
        problems = [
            make_source(base_blue, "blue"),
            make_source(fg_red, "red"),
            # ffmpeg's "green" is 0x008000; "lime" is the pure 0x00FF00 the
            # chroma case keys, so the fixture must say which green it means.
            make_source(fg_green, "lime"),
        ]
        bad = [problem for problem in problems if problem]
        if bad:
            skip("colour fixtures", bad[0])
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 0
        try:
            run_checks(work, base_blue, fg_red, fg_green)
        except Exception:  # noqa: BLE001 - a crash is a failing test, never a skip
            traceback.print_exc()
            check("test harness completed without an unexpected exception", False)

    elapsed = time.monotonic() - started
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  ({elapsed:.1f}s)")
    check("suite stays under the 120 s ceiling", elapsed < 120.0, f"{elapsed:.1f}s")
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


def run_checks(work: Path, base: Path, red: Path, green: Path) -> None:
    # --- (a) picture-in-picture ------------------------------------------
    probe = edl3_compose.pip_fragment(width=WIDTH, height=HEIGHT, x=0, y=0)
    pip_w, pip_h = probe["notes"]["size"]
    check(
        "(a) PiP geometry is derived from scale, not hard-coded",
        abs(pip_w - WIDTH * 0.35) <= edl3_compose.ALIGN
        and abs(pip_h - HEIGHT * 0.35) <= edl3_compose.ALIGN,
        f"0.35 * {WIDTH}x{HEIGHT} -> {pip_w}x{pip_h}",
    )
    pip = edl3_compose.pip_fragment(
        width=WIDTH, height=HEIGHT, x=WIDTH - pip_w, y=HEIGHT - pip_h, scale=0.35
    )
    show("pip", pip)
    problem = render_graph(
        [base, red],
        [
            f"[0:v]format=rgba,setsar=1[base]",
            f"[1:v]format=rgba,setsar=1[fg]",
            *pip["filters"],
            "[base][pip]overlay=0:0:format=auto[out]",
        ],
        work / "pip.mp4",
        work,
    )
    check("(a) pip graph rendered", problem == "", problem.splitlines()[-1:] or "")
    if not problem:
        data = frame_rgb(work / "pip.mp4", 0.3, work, "pip")
        inside = pixel(data, int(WIDTH * 0.85), int(HEIGHT * 0.85))
        outside = pixel(data, int(WIDTH * 0.15), int(HEIGHT * 0.15))
        check(
            "(a) inside the PiP the red overlay dominates",
            inside[0] > inside[2] + 60,
            f"rgb{inside}",
        )
        check(
            "(a) outside the PiP the blue base dominates",
            outside[2] > outside[0] + 60,
            f"rgb{outside}",
        )

    # --- (b) rectangular mask keeps only the left half --------------------
    mask = edl3_compose.mask_fragment(
        shape="rect", x=0, y=0, width=WIDTH // 2, height=HEIGHT
    )
    show("mask", mask)
    problem = render_graph(
        [base, red],
        [
            "[0:v]format=rgba,setsar=1[base]",
            "[1:v]format=rgba,setsar=1[fg]",
            *mask["filters"],
            "[base][masked]overlay=0:0:format=auto[out]",
        ],
        work / "mask.mp4",
        work,
    )
    check("(b) mask graph rendered", problem == "", problem.splitlines()[-1:] or "")
    if not problem:
        data = frame_rgb(work / "mask.mp4", 0.3, work, "mask")
        left = pixel(data, 80, HEIGHT // 2)
        right = pixel(data, 240, HEIGHT // 2)
        check(
            "(b) the kept half shows the overlay",
            left[0] > left[2] + 60,
            f"rgb{left}",
        )
        check(
            "(b) the masked-out half shows the base",
            right[2] > right[0] + 60,
            f"rgb{right}",
        )

    # --- (c) feather is a real blur on the mask ---------------------------
    # The alpha plane is measured directly (alphaextract -> gray): compositing
    # over a yuv420p base lets chroma subsampling smear a one-pixel edge across
    # the neighbouring block, which would flatter the "hard" step.
    hard = edl3_compose.mask_fragment(
        shape="rect", x=0, y=0, width=WIDTH // 2, height=HEIGHT, feather=0
    )
    soft = edl3_compose.mask_fragment(
        shape="rect", x=0, y=0, width=WIDTH // 2, height=HEIGHT, feather=12
    )
    show("feather", soft)
    edge_values: Dict[str, Tuple[int, int]] = {}
    for name, fragment in (("hard", hard), ("soft", soft)):
        target = work / f"edge_{name}.mp4"
        problem = render_graph(
            [red],
            [
                "[0:v]format=rgba,setsar=1[fg]",
                *fragment["filters"],
                "[masked]alphaextract,format=gray[out]",
            ],
            target,
            work,
        )
        check(f"(c) {name} edge graph rendered", problem == "")
        if problem:
            return
        data = frame_rgb(target, 0.3, work, f"edge_{name}")
        edge_values[name] = (
            pixel(data, WIDTH // 2 - 1, HEIGHT // 2)[0],
            pixel(data, WIDTH // 2 + 1, HEIGHT // 2)[0],
        )
    hard_step = abs(edge_values["hard"][0] - edge_values["hard"][1])
    soft_step = abs(edge_values["soft"][0] - edge_values["soft"][1])
    check(
        "(c) feather softens the mask edge",
        soft_step < hard_step,
        f"hard alpha {edge_values['hard'][0]}/{edge_values['hard'][1]} -> "
        f"{hard_step}; feather alpha {edge_values['soft'][0]}/"
        f"{edge_values['soft'][1]} -> {soft_step}",
    )
    check(
        "(c) the feathered step is not a hard switch",
        soft_step < 80 and hard_step > 200,
        f"hard {hard_step} vs feathered {soft_step}",
    )

    # --- (d) blend=screen -------------------------------------------------
    grey_dark = work / "grey_dark.mp4"
    grey_light = work / "grey_light.mp4"
    for path, value in ((grey_dark, "0x707070"), (grey_light, "0x909090")):
        problem = make_source(path, value)
        if problem:
            skip(f"(d) fixture {path.name}", problem)
            return
    screen = edl3_compose.blend_fragment("screen")
    show("blend", screen)
    problem = render_graph(
        [grey_dark, grey_light],
        [
            "[0:v]format=yuv420p,setsar=1[base]",
            "[1:v]format=yuv420p,setsar=1[fg]",
            *screen["filters"],
            "[blend]format=yuv420p[out]",
        ],
        work / "screen.mp4",
        work,
    )
    check("(d) screen graph rendered", problem == "", problem.splitlines()[-1:] or "")
    if not problem:
        luma_dark = mean_luma(grey_dark, 0.3)
        luma_light = mean_luma(grey_light, 0.3)
        luma_out = mean_luma(work / "screen.mp4", 0.3)
        check(
            "(d) screen output luma >= max(input luma)",
            luma_out >= max(luma_dark, luma_light),
            f"{luma_dark:.2f} / {luma_light:.2f} -> {luma_out:.2f}",
        )
        check(
            "(d) screen is strictly brighter than both mid-greys",
            luma_out > max(luma_dark, luma_light) + 5.0,
            f"delta {luma_out - max(luma_dark, luma_light):.2f}",
        )

    # --- (e) chroma key ---------------------------------------------------
    chroma = edl3_compose.chroma_fragment(color="green", similarity=0.2)
    show("chroma", chroma)
    check(
        "(e) the fragment states the colour and both numbers",
        chroma["notes"]["color_hex"] == "0x00FF00"
        and chroma["notes"]["similarity"] == 0.2
        and chroma["notes"]["blend"] == 0.0,
        chroma["notes"],
    )
    problem = render_graph(
        [base, green],
        [
            "[0:v]format=rgba,setsar=1[base]",
            "[1:v]format=rgba,setsar=1[fg]",
            *chroma["filters"],
            "[base][chroma]overlay=0:0:format=auto[out]",
        ],
        work / "chroma.mp4",
        work,
    )
    check("(e) chroma graph rendered", problem == "", problem.splitlines()[-1:] or "")
    plain = work / "chroma_off.mp4"
    problem_off = render_graph(
        [base, green],
        [
            "[0:v]format=rgba,setsar=1[base]",
            "[1:v]format=rgba,setsar=1[fg]",
            "[base][fg]overlay=0:0:format=auto[out]",
        ],
        plain,
        work,
    )
    check("(e) unkeyed graph rendered", problem_off == "")
    if not problem and not problem_off:
        keyed = pixel(
            frame_rgb(work / "chroma.mp4", 0.3, work, "keyed"), 160, HEIGHT // 2
        )
        raw = pixel(frame_rgb(plain, 0.3, work, "plain"), 160, HEIGHT // 2)
        check(
            "(e) with chroma the blue base shows through the centre",
            keyed[2] > keyed[1] + 60,
            f"rgb{keyed}",
        )
        check(
            "(e) without chroma the green source covers the centre",
            raw[1] > raw[2] + 60,
            f"rgb{raw}",
        )

    # --- (f) op table and refusals ---------------------------------------
    check(
        "(f) COMPOSE_OPS covers every builder",
        set(edl3_compose.COMPOSE_OPS)
        == set(edl3_compose.BUILDERS)
        == {"pip", "mask", "blend", "chroma"},
        sorted(edl3_compose.COMPOSE_OPS),
    )
    check(
        "(f) every required parameter is declared",
        all(
            edl3_compose.COMPOSE_OPS[op] == expected
            for op, expected in (
                ("pip", ("width", "height", "x", "y")),
                ("mask", ("shape", "x", "y", "width", "height")),
                ("blend", ("mode",)),
                ("chroma", ()),
            )
        ),
        edl3_compose.COMPOSE_OPS,
    )
    check(
        "(f) pip refuses a scale outside its range",
        refusal(
            edl3_compose.pip_fragment,
            width=WIDTH,
            height=HEIGHT,
            x=0,
            y=0,
            scale=2.0,
        )
        == "PIP_GEOMETRY",
    )
    check(
        "(f) pip refuses a border thicker than the declared range",
        refusal(
            edl3_compose.pip_fragment,
            width=WIDTH,
            height=HEIGHT,
            x=0,
            y=0,
            border=200,
        )
        == "PIP_BORDER",
    )
    check(
        "(f) mask refuses a shape outside the declared set",
        refusal(
            edl3_compose.mask_fragment,
            shape="hexagon",
            x=0,
            y=0,
            width=10,
            height=10,
        )
        == "MASK_SHAPE",
    )
    check(
        "(f) blend refuses an unknown mode",
        refusal(edl3_compose.blend_fragment, mode="dissolve") == "BLEND_MODE",
    )
    check(
        "(f) chroma refuses an unknown colour",
        refusal(edl3_compose.chroma_fragment, color="chartreuse") == "CHROMA_COLOR",
    )
    check(
        "(f) chroma refuses a similarity outside its range",
        refusal(edl3_compose.chroma_fragment, similarity=2.0) == "CHROMA_RANGE",
    )
    check(
        "(f) validate_op refuses an unknown op without calling a builder",
        refusal(edl3_compose.validate_op, op="rotate", params={}) == "COMPOSE_OP",
    )
    check(
        "(f) validate_op refuses a missing required parameter",
        refusal(
            edl3_compose.validate_op, op="pip", params={"width": 320, "height": 568}
        )
        == "COMPOSE_PARAMS",
    )
    check(
        "(f) validate_op accepts a complete declaration",
        edl3_compose.validate_op(
            "mask", {"shape": "rect", "x": 0, "y": 0, "width": 10, "height": 10}
        )
        is None,
    )
    moved = edl3_compose.relabel(pip, {"fg": "layer1", "pip": "layer2"})
    check(
        "(f) relabel rewires both the tokens and the contract",
        moved["consumes"] == ["[layer1]"]
        and moved["produces"] == "[layer2]"
        and "[layer1]" in moved["filters"][0]
        and "[layer2]" in moved["filters"][0],
        moved["filters"][0][:60],
    )
    check(
        "(f) every fragment returns the four contract keys",
        all(
            set(edl3_compose.BUILDERS[op](**params))
            >= {
                "filters",
                "consumes",
                "produces",
                "notes",
            }
            for op, params in (
                ("pip", {"width": WIDTH, "height": HEIGHT, "x": 0, "y": 0}),
                (
                    "mask",
                    {"shape": "rect", "x": 0, "y": 0, "width": 10, "height": 10},
                ),
                ("blend", {"mode": "multiply"}),
                ("chroma", {"color": "magenta"}),
            )
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
