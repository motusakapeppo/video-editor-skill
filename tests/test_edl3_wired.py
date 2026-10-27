"""F2-T09 — the four planning modules wired into the EDL 3 compiler.

A document is the only entry point: every case below is built as a serialised
``vx.edl3/1`` mapping, loaded with ``Timeline3.from_dict`` and rendered, so a
parameter that is not carried by the document is not reachable at all.

Measured here, with real ffmpeg at 320x568:

* (a) speed, reverse, freeze, pip, mask, blend and chroma change the pixels;
* (b) the emitted chain keeps the canonical order, proven by index positions;
* (c) the explicit ``format=rgba``/``format=yuv420p`` conversions are where the
  keying and compositing nodes need them;
* (d) every refusal arrives with the module's own declared code;
* (e) an inner sequence used twice is rendered once and the outer frame is the
  inner render (MAD <= 3);
* (f) a document without the new parameters produces the pre-change graph,
  byte for byte.

    python tests/test_edl3_wired.py
"""

from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402
from vx import edl3_extremes  # noqa: E402
from vx import edl3_render  # noqa: E402
from vx.ffmpeg import run_ff, run_fp  # noqa: E402

PASS = 0
FAIL = 0
FPS = 12
WIDTH, HEIGHT = 320, 568
CEILING = 150.0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:66} {detail}")


# ------------------------------------------------------------------ fixtures


def lavfi(spec: str) -> List[str]:
    return ["-f", "lavfi", "-i", spec]


def encode(work: Path, name: str, spec: str) -> Path:
    output = work / f"{name}.mp4"
    proc = run_ff(
        lavfi(spec)
        + [
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
        raise RuntimeError(f"fixture {name} failed: {proc.tail()}")
    return output


def encode_black_then_white(work: Path) -> Path:
    """One second of black then one second of white, in a single file."""
    script = (
        "[0:v]trim=start=0:end=1,setpts=PTS-STARTPTS[b];\n"
        "[1:v]trim=start=0:end=1,setpts=PTS-STARTPTS[w];\n"
        "[b][w]concat=n=2:v=1:a=0[out]"
    )
    script_path = work / "bw.filter.txt"
    script_path.write_text(script + "\n", encoding="utf-8", newline="\n")
    output = work / "black_white.mp4"
    proc = run_ff(
        lavfi(f"color=c=black:s={WIDTH}x{HEIGHT}:r={FPS}:d=1")
        + lavfi(f"color=c=white:s={WIDTH}x{HEIGHT}:r={FPS}:d=1")
        + [
            "-filter_complex_script",
            str(script_path),
            "-map",
            "[out]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ],
        loglevel="error",
    )
    if not proc.ok or not output.exists():
        raise RuntimeError(f"fixture black_white failed: {proc.tail()}")
    return output


def frame_rgb(work: Path, video: Path, t: float, name: str) -> bytes:
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
    return path.read_bytes()


def rgb_at(frame: bytes, x: int, y: int) -> Tuple[int, int, int]:
    offset = (y * WIDTH + x) * 3
    return (frame[offset], frame[offset + 1], frame[offset + 2])


def luma(frame: bytes) -> float:
    total = 0
    for index in range(0, len(frame), 3):
        total += (
            299 * frame[index] + 587 * frame[index + 1] + 114 * frame[index + 2]
        ) // 1000
    return total / (len(frame) / 3.0)


def mad(first: bytes, second: bytes) -> float:
    if len(first) != len(second):
        raise RuntimeError(f"frame sizes differ: {len(first)} vs {len(second)}")
    total = 0
    for left, right in zip(first, second):
        total += abs(left - right)
    return total / float(len(first))


def duration_of(video: Path) -> float:
    proc = run_fp(
        [
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(video),
        ]
    )
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if not proc.ok or not lines:
        raise RuntimeError(f"duration of {video.name} unreadable: {proc.tail()}")
    return float(lines[-1])


# ----------------------------------------------------------------- documents


def document(clips: List[Dict[str, Any]], **extra: Any) -> edl3.Timeline3:
    data: Dict[str, Any] = {
        "version": edl3.EDL3_VERSION,
        "fps": FPS,
        "size": [WIDTH, HEIGHT],
        "clips": clips,
    }
    data.update(extra)
    return edl3.Timeline3.from_dict(data)


def clip(
    cid: str,
    source: Any,
    *,
    src_in: float = 0.0,
    src_out: float = 2.0,
    layer: int = 0,
    dst: Optional[Tuple[float, float]] = None,
    **extra: Any,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": cid,
        "source": str(source),
        "src_in": src_in,
        "src_out": src_out,
        "layer": layer,
        "dst_in": None if dst is None else dst[0],
        "dst_out": None if dst is None else dst[1],
    }
    data.update(extra)
    return data


def sequence_clip(cid: str, sequence: str, duration: float) -> Dict[str, Any]:
    return {
        "id": cid,
        "sequence": sequence,
        "src_in": 0.0,
        "src_out": duration,
    }


def code_of(call: Any) -> str:
    try:
        call()
    except Exception as exc:  # noqa: BLE001 - the code, not the type, is asserted
        return f"{type(exc).__name__}:{getattr(exc, 'code', 'NO_CODE')}"
    return ""


# --------------------------------------------------------------------- main


def main() -> int:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vx_edl3_wired_") as tmp:
        work = Path(tmp)
        try:
            run_checks(work)
        except Exception:  # noqa: BLE001 - a crash is a failing test, never a skip
            traceback.print_exc()
            check("harness completed without an unexpected exception", False)

    elapsed = time.monotonic() - started
    check(
        f"suite stays under the {CEILING:g} s ceiling",
        elapsed < CEILING,
        f"{elapsed:.1f}s",
    )
    print(f"\n{PASS} PASS / {FAIL} FAIL  ({elapsed:.1f}s)")
    return 0 if FAIL == 0 else 1


def run_checks(work: Path) -> None:
    blue = encode(work, "blue", f"color=c=0x0000FF:s={WIDTH}x{HEIGHT}:r={FPS}:d=2")
    red = encode(work, "red", f"color=c=0xFF0000:s={WIDTH}x{HEIGHT}:r={FPS}:d=2")
    green = encode(work, "green", f"color=c=0x00FF00:s={WIDTH}x{HEIGHT}:r={FPS}:d=2")
    dark_grey = encode(
        work, "grey64", f"color=c=0x404040:s={WIDTH}x{HEIGHT}:r={FPS}:d=2"
    )
    mid_grey = encode(
        work, "grey128", f"color=c=0x808080:s={WIDTH}x{HEIGHT}:r={FPS}:d=2"
    )
    changing = encode(work, "changing", f"testsrc2=s={WIDTH}x{HEIGHT}:r={FPS}:d=2")
    black_white = encode_black_then_white(work)

    run_speed(work, blue)
    run_reverse(work, black_white)
    run_freeze(work, changing)
    run_pip(work, blue, red)
    run_mask(work, blue, red)
    run_blend(work, dark_grey, mid_grey)
    run_chroma(work, blue, green)
    run_order(work, blue, red)
    run_formats(work, blue, green, dark_grey, mid_grey)
    run_refusals(work, blue)
    run_nested(work, blue)
    run_parity(work, blue)


# ------------------------------------------------------------------ (a) ---


def run_speed(work: Path, blue: Path) -> None:
    print("\n-- (a) speed 2x halves the picture")
    doc = document([clip("c1", blue, src_out=2.0, speed=2.0)])
    report = edl3_render.render(doc, work / "speed.mp4", workdir=work / "speed")
    measured = duration_of(work / "speed.mp4")
    check(
        "(a) 2s at 2x renders 1s within two frames",
        abs(measured - 1.0) <= 2.0 / FPS + 1e-3,
        f"{measured:.3f}s (declared {report['duration']:.3f}s)",
    )
    entry = report["clips"][0]
    check(
        "(a) the report carries the ramp, the policy and the order",
        entry["speed"] == 2.0
        and entry["audio_policy"] == "segments"
        and entry["audio_policy_declared"] is False
        and entry["order"]
        == ["source", "time_fx", "speed", "geometry", "keying", "compose"],
        [entry["speed"], entry["audio_policy"], entry["order"]],
    )
    check(
        "(a) the undeclared audio policy is declared as a default in the report",
        any("segments" in warning for warning in report["warnings"]),
        report["warnings"],
    )
    check(
        "(a) the audio side planned the same ramp with atempo",
        entry["audio"] is not None
        and entry["audio"]["policy"] == "segments"
        and any(item.startswith("atempo=") for item in entry["audio"]["filters"]),
        None if entry["audio"] is None else entry["audio"]["filters"],
    )


def run_reverse(work: Path, black_white: Path) -> None:
    print("\n-- (a) reverse turns the head bright")
    doc = document([clip("c1", black_white, src_out=2.0, reverse=True)])
    edl3_render.render(doc, work / "reverse.mp4", workdir=work / "reverse")
    head = luma(frame_rgb(work, work / "reverse.mp4", 0.2, "rev_head"))
    tail = luma(frame_rgb(work, work / "reverse.mp4", 1.8, "rev_tail"))
    check(
        "(a) after reverse the head is bright and the tail dark",
        head > 150.0 and tail < 60.0 and head - tail > 100.0,
        f"luma 0.2s={head:.1f} 1.8s={tail:.1f}",
    )


def run_freeze(work: Path, changing: Path) -> None:
    print("\n-- (a) freeze holds one frame")
    doc = document([clip("c1", changing, src_out=2.0, freeze={"t": 0.5, "hold": 0.8})])
    plan = edl3_render.compile_timeline(doc)
    edl3_render.render(doc, work / "freeze.mp4", workdir=work / "freeze")
    hashes = chain_frame_hashes(work, plan, "freeze_chain")
    early_frame, late_frame = 7, 15
    check(
        "(a) the compiled chain holds one frame: two frames share one hash",
        hashes.get(early_frame) is not None
        and hashes.get(early_frame) == hashes.get(late_frame),
        f"frame {early_frame} {str(hashes.get(early_frame))[:16]} vs "
        f"frame {late_frame} {str(hashes.get(late_frame))[:16]}",
    )
    early = frame_rgb(work, work / "freeze.mp4", 0.6, "frz_early")
    late = frame_rgb(work, work / "freeze.mp4", 1.2, "frz_late")
    difference = mad(early, late)
    check(
        "(a) the rendered file is the same picture at both times (MAD <= 1)",
        difference <= 1.0,
        f"MAD={difference:.3f}",
    )
    source_early = frame_rgb(work, changing, 0.5, "frz_src_early")
    source_late = frame_rgb(work, changing, 1.2, "frz_src_late")
    check(
        "(a) the source really changes, so the hold is meaningful",
        mad(source_early, source_late) > 2.0,
        f"MAD(source 0.5, 1.2)={mad(source_early, source_late):.3f}",
    )


def chain_frame_hashes(work: Path, plan: Dict[str, Any], name: str) -> Dict[int, str]:
    """MD5 of every frame the compiled chain emits, before the encoder."""
    script_path = work / f"{name}.filter.txt"
    script_path.write_text(plan["filter_script"], encoding="utf-8", newline="\n")
    proc = run_ff(
        list(plan["input_args"])
        + [
            "-filter_complex_script",
            str(script_path),
            "-map",
            plan["map"],
            "-f",
            "framemd5",
            "-",
        ],
        loglevel="error",
    )
    if not proc.ok:
        raise RuntimeError(f"framemd5 of {name} failed: {proc.tail()}")
    hashes: Dict[int, str] = {}
    for line in (proc.stdout or "").splitlines():
        parts = [item.strip() for item in line.split(",")]
        if len(parts) < 6 or not parts[0].isdigit():
            continue
        hashes[int(parts[2])] = parts[5]
    return hashes
    source_early = frame_rgb(work, changing, 0.5, "frz_src_early")
    source_late = frame_rgb(work, changing, 1.2, "frz_src_late")
    check(
        "(a) the source really changes, so the hold is meaningful",
        mad(source_early, source_late) > 2.0,
        f"MAD(source 0.5, 1.2)={mad(source_early, source_late):.3f}",
    )


def run_pip(work: Path, blue: Path, red: Path) -> None:
    print("\n-- (a) pip places the foreground inside the base")
    doc = document(
        [
            clip("base", blue, dst=(0.0, 2.0), layer=0),
            clip(
                "fg",
                red,
                src_out=1.0,
                dst=(0.0, 1.0),
                layer=1,
                compose={"op": "pip", "x": 10, "y": 10, "scale": 0.4},
            ),
        ]
    )
    edl3_render.render(doc, work / "pip.mp4", workdir=work / "pip")
    frame = frame_rgb(work, work / "pip.mp4", 0.5, "pip_050")
    inside = rgb_at(frame, 110, 200)
    outside = rgb_at(frame, 280, 400)
    edge = rgb_at(frame, 144, 200)
    check(
        "(a) the pixel inside the pip is red, outside it is the blue base",
        inside[0] > 120 and inside[2] < 90 and outside[2] > 120 and outside[0] < 90,
        f"inside {inside} outside {outside}",
    )
    check(
        "(a) the pip is 40% wide once, not 40% of 40%",
        edge[2] > 120 and edge[0] < 90,
        f"pixel at x=144 (past the 138 px pip edge) is {edge}",
    )


def run_mask(work: Path, blue: Path, red: Path) -> None:
    print("\n-- (a) mask keeps the fg inside the rect and reveals the base")
    doc = document(
        [
            clip("base", blue, dst=(0.0, 2.0), layer=0),
            clip(
                "fg",
                red,
                src_out=1.0,
                dst=(0.0, 1.0),
                layer=1,
                compose={
                    "op": "mask",
                    "shape": "rect",
                    "x": 0,
                    "y": 0,
                    "width": WIDTH // 2,
                    "height": HEIGHT,
                },
            ),
        ]
    )
    edl3_render.render(doc, work / "mask.mp4", workdir=work / "mask")
    frame = frame_rgb(work, work / "mask.mp4", 0.5, "mask_050")
    kept = rgb_at(frame, 40, 300)
    masked_out = rgb_at(frame, 280, 300)
    check(
        "(a) the kept half is the fg, the masked-out half is the base",
        kept[0] > 120 and kept[2] < 90 and masked_out[2] > 120 and masked_out[0] < 90,
        f"kept {kept} masked-out {masked_out}",
    )


def run_blend(work: Path, dark_grey: Path, mid_grey: Path) -> None:
    print("\n-- (a) blend screen is brighter than both inputs")
    base_source = rgb_at(frame_rgb(work, dark_grey, 0.5, "bl_base"), 160, 284)
    fg_source = rgb_at(frame_rgb(work, mid_grey, 0.5, "bl_fg"), 160, 284)
    doc = document(
        [
            clip("base", dark_grey, dst=(0.0, 2.0), layer=0),
            clip(
                "fg",
                mid_grey,
                src_out=1.0,
                dst=(0.5, 1.5),
                layer=1,
                compose={"op": "blend", "mode": "screen"},
            ),
        ]
    )
    edl3_render.render(doc, work / "blend.mp4", workdir=work / "blend")
    inside = rgb_at(frame_rgb(work, work / "blend.mp4", 1.0, "bl_in"), 160, 284)
    after = rgb_at(frame_rgb(work, work / "blend.mp4", 1.8, "bl_out"), 160, 284)
    brightest = max(luma(bytes(base_source)), luma(bytes(fg_source)))
    check(
        "(a) the blended pixel is brighter than both inputs",
        luma(bytes(inside)) > brightest + 10.0,
        f"blend {inside} luma {luma(bytes(inside)):.1f}, "
        f"base {base_source}, fg {fg_source}",
    )
    check(
        "(a) outside the declared window the base is untouched",
        mad(bytes(after), bytes(base_source)) <= 3.0,
        f"MAD(after window, base)={mad(bytes(after), bytes(base_source)):.3f}",
    )


def run_chroma(work: Path, blue: Path, green: Path) -> None:
    print("\n-- (a) chroma keys the green fg out and shows the blue base")
    doc = document(
        [
            clip("base", blue, dst=(0.0, 2.0), layer=0),
            clip(
                "fg",
                green,
                src_out=1.0,
                dst=(0.0, 1.0),
                layer=1,
                compose={"op": "chroma", "color": "green", "similarity": 0.35},
            ),
        ]
    )
    edl3_render.render(doc, work / "chroma.mp4", workdir=work / "chroma")
    frame = frame_rgb(work, work / "chroma.mp4", 0.5, "chroma_050")
    centre = rgb_at(frame, 160, 284)
    check(
        "(a) the whole keyed frame is the blue base",
        centre[2] > 120 and centre[0] < 90 and centre[1] < 90,
        centre,
    )


# ------------------------------------------------------------------ (b) ---


def order_document(blue: Path, red: Path) -> edl3.Timeline3:
    return document(
        [
            clip("base", blue, dst=(0.0, 2.0), layer=0),
            clip(
                "fg",
                red,
                src_out=2.0,
                dst=(0.0, 2.0),
                layer=1,
                reverse=True,
                speed=2.0,
                effects=[
                    {
                        "kind": "crop",
                        "params": {"x": 0.0, "y": 0.0, "width": 0.5, "height": 1.0},
                    }
                ],
                compose={
                    "op": "mask",
                    "shape": "rect",
                    "x": 0,
                    "y": 0,
                    "width": WIDTH,
                    "height": HEIGHT,
                },
            ),
        ]
    )


def run_order(work: Path, blue: Path, red: Path) -> None:
    print("\n-- (b) canonical order: time fx, ramp, geometry, keying")
    doc = order_document(blue, red)
    plan = edl3_render.compile_timeline(doc)
    graph = plan["filter_script"]
    markers = {
        "time_fx(reverse)": "reverse",
        "speed(setpts/2)": "setpts=(PTS-STARTPTS)/2",
        "geometry(crop)": "crop=w=",
        "keying(alphamerge)": "alphamerge",
    }
    positions = {name: graph.index(token) for name, token in markers.items()}
    ordered = list(positions.values())
    print("      chain of the (b) clip:")
    for line in graph.splitlines():
        if line.startswith("[1:v]"):
            print(f"        {line}")
    check(
        "(b) the marker index positions strictly increase",
        ordered == sorted(ordered) and len(set(ordered)) == len(ordered),
        positions,
    )
    check(
        "(b) the reverse precedes the ramp",
        positions["time_fx(reverse)"] < positions["speed(setpts/2)"],
        f"{positions['time_fx(reverse)']} < {positions['speed(setpts/2)']}",
    )
    check(
        "(b) crop never precedes the speed ramp",
        positions["speed(setpts/2)"] < positions["geometry(crop)"],
        f"{positions['speed(setpts/2)']} < {positions['geometry(crop)']}",
    )
    check(
        "(b) keying is the last stage of the clip chain",
        positions["geometry(crop)"] < positions["keying(alphamerge)"],
        f"{positions['geometry(crop)']} < {positions['keying(alphamerge)']}",
    )
    check(
        "(b) the report states the same order",
        plan["clips"][1]["order"]
        == ["source", "time_fx", "speed", "geometry", "keying", "compose"]
        and plan["clips"][1]["time_fx"] == ["reverse"]
        and plan["clips"][1]["compose"] == ["mask"],
        [plan["clips"][1]["order"], plan["clips"][1]["time_fx"]],
    )


# ------------------------------------------------------------------ (c) ---


def run_formats(work: Path, blue: Path, green: Path, dark: Path, mid: Path) -> None:
    print("\n-- (c) explicit format conversions around keying and compositing")
    chroma_doc = document(
        [
            clip("base", blue, dst=(0.0, 2.0), layer=0),
            clip(
                "fg",
                green,
                src_out=1.0,
                dst=(0.0, 1.0),
                layer=1,
                compose={"op": "chroma", "color": "green"},
            ),
        ]
    )
    graph = edl3_render.compile_timeline(chroma_doc)["filter_script"]
    keyed = graph.index("colorkey=")
    before = graph[:keyed].count("format=rgba")
    check(
        "(c) exactly one format=rgba precedes the colorkey node",
        before == 1,
        f"{before} occurrence(s) before index {keyed}",
    )
    check(
        "(c) the rgba conversion is the step right before colorkey",
        "format=rgba,colorkey=" in graph,
        [line for line in graph.splitlines() if "colorkey" in line],
    )
    overlay_lines = [line for line in graph.splitlines() if "overlay=0:0:" in line]
    check(
        "(c) the keyed layer is composited by one overlay",
        len(overlay_lines) == 1,
        overlay_lines,
    )
    if overlay_lines:
        tail = overlay_lines[0][overlay_lines[0].index("overlay=0:0:") :]
        conversions = tail.count("format=yuv420p")
        check(
            "(c) exactly one format=yuv420p follows the overlay, before the label",
            conversions == 1 and tail.index("format=yuv420p") < tail.index("[base0]"),
            tail,
        )

    mask_doc = document(
        [
            clip("base", blue, dst=(0.0, 2.0), layer=0),
            clip(
                "fg",
                green,
                src_out=1.0,
                dst=(0.0, 1.0),
                layer=1,
                compose={
                    "op": "mask",
                    "shape": "ellipse",
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 200,
                },
            ),
        ]
    )
    mask_graph = edl3_render.compile_timeline(mask_doc)["filter_script"]
    masked = mask_graph.index("alphamerge")
    check(
        "(c) exactly one format=rgba precedes alphamerge",
        mask_graph[:masked].count("format=rgba") == 1,
        f"{mask_graph[:masked].count('format=rgba')} before index {masked}",
    )

    blend_doc = document(
        [
            clip("base", dark, dst=(0.0, 2.0), layer=0),
            clip(
                "fg",
                mid,
                src_out=1.0,
                dst=(0.5, 1.5),
                layer=1,
                compose={"op": "blend", "mode": "screen"},
            ),
        ]
    )
    blend_graph = edl3_render.compile_timeline(blend_doc)["filter_script"]
    blend_lines = [
        line for line in blend_graph.splitlines() if "blend=all_mode" in line
    ]
    check("(c) the blend is one compositing node", len(blend_lines) == 1, blend_lines)
    if blend_lines:
        tail = blend_lines[0][blend_lines[0].index("blend=all_mode") :]
        check(
            "(c) exactly one format=yuv420p follows the blend, before the label",
            tail.count("format=yuv420p") == 1
            and tail.index("format=yuv420p") < tail.index("[blended0]"),
            tail,
        )


# ------------------------------------------------------------------ (d) ---


def run_refusals(work: Path, blue: Path) -> None:
    print("\n-- (d) refusals keep the modules' own codes")
    over_reverse = document(
        [
            clip(
                "c1",
                blue,
                src_out=edl3_extremes.MAX_REVERSE_DURATION_SEC + 570.0,
                reverse=True,
            )
        ]
    )
    code = code_of(lambda: edl3_render.compile_timeline(over_reverse))
    check(
        "(d) a reverse over the cap returns REVERSE_TOO_LONG",
        code == "ExtremesError:REVERSE_TOO_LONG",
        code,
    )

    over_freeze = document(
        [
            clip(
                "c1",
                blue,
                src_out=2.0,
                freeze={"t": 0.5, "hold": edl3_extremes.MAX_FREEZE_DURATION_SEC + 1.0},
            )
        ]
    )
    code = code_of(lambda: edl3_render.compile_timeline(over_freeze))
    check(
        "(d) a freeze over the cap returns FREEZE_TOO_LONG",
        code == "ExtremesError:FREEZE_TOO_LONG",
        code,
    )

    bad_blend = document(
        [
            clip("base", blue, dst=(0.0, 2.0), layer=0),
            clip(
                "fg",
                blue,
                src_out=1.0,
                dst=(0.0, 1.0),
                layer=1,
                compose={"op": "blend", "mode": "dissolve"},
            ),
        ]
    )
    code = code_of(lambda: edl3_render.compile_timeline(bad_blend))
    check(
        "(d) an unknown blend mode returns BLEND_MODE",
        code == "ComposeError:BLEND_MODE",
        code,
    )

    bad_mask = document(
        [
            clip("base", blue, dst=(0.0, 2.0), layer=0),
            clip(
                "fg",
                blue,
                src_out=1.0,
                dst=(0.0, 1.0),
                layer=1,
                compose={
                    "op": "mask",
                    "shape": "hexagon",
                    "x": 0,
                    "y": 0,
                    "width": 10,
                    "height": 10,
                },
            ),
        ]
    )
    code = code_of(lambda: edl3_render.compile_timeline(bad_mask))
    check(
        "(d) an unknown mask shape returns MASK_SHAPE",
        code == "ComposeError:MASK_SHAPE",
        code,
    )

    cycle = nesting_cycle_document()
    code = code_of(lambda: edl3_render.compile_timeline(cycle))
    check(
        "(d) a sequence cycle returns NESTING_CYCLE",
        code == "NestingError:NESTING_CYCLE",
        code,
    )

    deep = nesting_chain_document(blue, 3)
    code = code_of(lambda: edl3_render.compile_timeline(deep))
    check(
        "(d) nesting past the declared ceiling returns NESTING_TOO_DEEP",
        code == "NestingError:NESTING_TOO_DEEP",
        code,
    )
    for label, value in (
        ("reverse", over_reverse),
        ("freeze", over_freeze),
        ("blend", bad_blend),
        ("mask", bad_mask),
        ("deep", deep),
    ):
        codes = {problem["code"] for problem in value.validate()}
        check(
            f"(d) {label}: the document model itself does not refuse it generically",
            "RENDER_FAILED" not in codes and "UNSUPPORTED_SEQUENCE" not in codes,
            sorted(codes),
        )


def nesting_cycle_document() -> edl3.Timeline3:
    inner_a = edl3.Timeline3(
        fps=FPS,
        size=(WIDTH, HEIGHT),
        clips=[edl3.Clip3(id="ca", sequence="b", src_in=0.0, src_out=1.0)],
    )
    inner_b = edl3.Timeline3(
        fps=FPS,
        size=(WIDTH, HEIGHT),
        clips=[edl3.Clip3(id="cb", sequence="a", src_in=0.0, src_out=1.0)],
    )
    inner_a.sequences = {"b": edl3.NestedSequence(id="b", timeline=inner_b)}
    inner_b.sequences = {"a": edl3.NestedSequence(id="a", timeline=inner_a)}
    return edl3.Timeline3(
        fps=FPS,
        size=(WIDTH, HEIGHT),
        clips=[edl3.Clip3(id="r", sequence="a", src_in=0.0, src_out=1.0)],
        sequences={"a": edl3.NestedSequence(id="a", timeline=inner_a)},
    )


def nesting_chain_document(source: Path, levels: int) -> edl3.Timeline3:
    inner = edl3.Timeline3(
        fps=FPS,
        size=(WIDTH, HEIGHT),
        clips=[edl3.Clip3(id="leaf", source=str(source), src_in=0.0, src_out=1.0)],
    )
    for level in range(levels, 0, -1):
        sequence_id = f"s{level}"
        inner = edl3.Timeline3(
            fps=FPS,
            size=(WIDTH, HEIGHT),
            clips=[
                edl3.Clip3(id="root", sequence=sequence_id, src_in=0.0, src_out=1.0)
            ],
            sequences={
                sequence_id: edl3.NestedSequence(id=sequence_id, timeline=inner)
            },
        )
    return inner


# ------------------------------------------------------------------ (e) ---


def run_nested(work: Path, blue: Path) -> None:
    print("\n-- (e) an inner sequence used twice is rendered once")
    inner = edl3.Timeline3(
        fps=FPS,
        size=(WIDTH, HEIGHT),
        clips=[edl3.Clip3(id="i1", source=str(blue), src_in=0.0, src_out=1.0)],
    )
    outer = document(
        [
            sequence_clip("o1", "inner", 1.0),
            sequence_clip("o2", "inner", 1.0),
        ],
        sequences={"inner": edl3.NestedSequence(id="inner", timeline=inner)},
    )
    report = edl3_render.render(outer, work / "outer.mp4", workdir=work / "outer")
    files = sorted((work / "outer" / "nested").glob("*.mp4"))
    check(
        "(e) one inner render for two references",
        len(files) == 1 and report["nested_renders"] == 1,
        f"{len(files)} file(s), nested_renders={report['nested_renders']}",
    )
    if not files:
        return
    inner_render = files[0]
    first = frame_rgb(work, work / "outer.mp4", 0.3, "nest_first")
    second = frame_rgb(work, work / "outer.mp4", 1.3, "nest_second")
    reference = frame_rgb(work, inner_render, 0.3, "nest_inner")
    check(
        "(e) the first use is the inner render (MAD <= 3)",
        mad(first, reference) <= 3.0,
        f"MAD={mad(first, reference):.3f}",
    )
    check(
        "(e) the second use is the same inner render (MAD <= 3)",
        mad(second, reference) <= 3.0,
        f"MAD={mad(second, reference):.3f}",
    )
    check(
        "(e) the outer really is two seconds of the inner render",
        abs(duration_of(work / "outer.mp4") - 2.0) <= 2.0 / FPS,
        f"{duration_of(work / 'outer.mp4'):.3f}s",
    )


# ------------------------------------------------------------------ (f) ---


def expected_direct(index: int, label: str) -> str:
    """The pre-change construction for a clip with no transform effect."""
    return (
        f"[{index}:v]setpts=PTS-STARTPTS,"
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease:flags=bicubic,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
        f"[{label}]"
    )


def run_parity(work: Path, blue: Path) -> None:
    print("\n-- (f) a document without the new parameters is byte-identical")
    single = document([clip("c1", blue, dst=(0.0, 2.0), layer=0)])
    plan = edl3_render.compile_timeline(single)
    check(
        "(f) the one-clip graph is the pre-change graph",
        plan["filter_script"] == expected_direct(0, "c0") + "\n",
        repr(plan["filter_script"]),
    )
    check("(f) its map is unchanged", plan["map"] == "[c0]", plan["map"])
    check(
        "(f) its input arguments are unchanged",
        plan["input_args"] == ["-ss", "0.000000", "-t", "2.000000", "-i", str(blue)],
        plan["input_args"],
    )

    two = document(
        [
            clip("c1", blue, src_in=0.0, src_out=1.0, dst=(0.0, 1.0), layer=0),
            clip("c2", blue, src_in=1.0, src_out=2.0, dst=(1.0, 2.0), layer=0),
        ]
    )
    expected = (
        expected_direct(0, "c0")
        + ";\n"
        + expected_direct(1, "c1")
        + ";\n[c0][c1]concat=n=2:v=1:a=0[out]\n"
    )
    actual = edl3_render.compile_timeline(two)["filter_script"]
    check(
        "(f) the two-clip graph is the pre-change graph, byte for byte",
        actual == expected,
        repr(actual),
    )

    overlay = document(
        [
            clip("base", blue, dst=(0.0, 2.0), layer=0),
            clip("broll", blue, src_out=0.4, dst=(0.3, 0.7), layer=1),
        ]
    )
    graph = edl3_render.compile_timeline(overlay)["filter_script"]
    check(
        "(f) a plain overlay keeps its pre-change shift and gate",
        "[c1]setpts=PTS+0.300000/TB[ov0]" in graph
        and "[c0][ov0]overlay=enable='between(t,0.300000,0.700000)':"
        "eof_action=pass:format=auto[base0]"
        in graph,
        [line for line in graph.splitlines() if "overlay" in line],
    )
    check(
        "(f) a plain overlay gains no format conversion",
        "format=yuv420p[base0]" not in graph,
        [line for line in graph.splitlines() if "overlay" in line],
    )


if __name__ == "__main__":
    raise SystemExit(main())
