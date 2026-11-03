"""EDL3 text layers are rendered, not just validated.

Measured here, with real ffmpeg at 320x568, no network:

* (a) a document with two ``text_layers`` on different clips paints bright
  pixels inside each layer's window and none outside it;
* (b) the text is drawn **after** the concat (and after a crossfade), so a
  per-clip effect or a transition cannot move it or cut it;
* (c) the anchor follows ``align`` and the normalised ``position``: the bright
  centroid sits left/right and high/low exactly where the document asked;
* (d) the font is the work's own file (``assets/fonts``), never a system one;
* (e) a field the emitter does not render (``keyframes.*``, multi-line
  ``text``) is ``PARAM_NOT_RENDERED`` naming the field, with no output file;
* (f) a family with no file of the work is ``FONT_MISSING``, with no output
  file, and a pinned path that does not exist is refused the same way;
* (g) a chunked render refuses a text document instead of dropping the text.

    python tests/test_edl3_text.py
"""

from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402
from vx import edl3_render  # noqa: E402
from vx.ffmpeg import run_ff  # noqa: E402

PASS = 0
FAIL = 0
FPS = 12
WIDTH, HEIGHT = 320, 568
CEILING = 120.0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:68} {detail}")


# ------------------------------------------------------------------ fixtures


def encode(work: Path, name: str, spec: str) -> Path:
    output = work / f"{name}.mp4"
    proc = run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            spec,
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


def bright_pixels(frame: bytes) -> int:
    return sum(
        1
        for index in range(0, len(frame), 3)
        if frame[index] >= 200 and frame[index + 1] >= 200 and frame[index + 2] >= 200
    )


def bright_centroid(frame: bytes) -> Tuple[float, float]:
    total_x = 0.0
    total_y = 0.0
    count = 0
    for offset in range(0, len(frame), 3):
        if (
            frame[offset] >= 200
            and frame[offset + 1] >= 200
            and frame[offset + 2] >= 200
        ):
            pixel = offset // 3
            total_x += pixel % WIDTH
            total_y += pixel // WIDTH
            count += 1
    if not count:
        return (-1.0, -1.0)
    return (total_x / count, total_y / count)


# ----------------------------------------------------------------- documents


def document(
    clips: List[Dict[str, Any]],
    *,
    text_layers: List[Dict[str, Any]] | None = None,
    captions: List[Dict[str, Any]] | None = None,
) -> edl3.Timeline3:
    data: Dict[str, Any] = {
        "version": edl3.EDL3_VERSION,
        "fps": FPS,
        "size": [WIDTH, HEIGHT],
        "clips": clips,
    }
    if text_layers is not None:
        data["text_layers"] = text_layers
    if captions is not None:
        data["captions"] = captions
    return edl3.Timeline3.from_dict(data)


def clip(cid: str, source: Path, *, src_in: float = 0.0, **extra: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": cid,
        "source": str(source),
        "src_in": src_in,
        "src_out": src_in + 2.0,
    }
    data.update(extra)
    return data


def layer(
    text: str,
    start: float,
    end: float,
    *,
    align: str = "center",
    x: float = 0.5,
    y: float = 0.5,
    size: int = 40,
    stroke: float = 0.0,
    family: str = "Antonio",
    **extra: Any,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "text": text,
        "style": {
            "family": family,
            "size": size,
            "color": "#ffffff",
            "stroke": stroke,
            "position": [x, y],
            "align": align,
        },
        "in": start,
        "out": end,
    }
    data.update(extra)
    return data


def code_of(call: Any) -> Tuple[str, str]:
    """The stable code and the declared field of a refusal, never a traceback."""
    try:
        call()
    except Exception as exc:  # noqa: BLE001 - the code, not the type, is asserted
        return (str(getattr(exc, "code", "NO_CODE")), str(getattr(exc, "where", "")))
    return ("", "")


# --------------------------------------------------------------------- cases


def run_checks(work: Path) -> None:
    source = encode(
        work, "noise", f"color=c=0x101010:s={WIDTH}x{HEIGHT}:r={FPS}:d=4"
    )
    clips = [clip("a", source, src_in=0.0), clip("b", source, src_in=2.0)]
    two_layers = [
        layer("CIAO", 0.3, 1.2, align="left", x=0.10, y=0.30),
        layer("MONDO", 2.3, 3.2, align="right", x=0.90, y=0.70),
    ]

    print("\n-- (a) compile: text records, drawtext after the concat")
    timeline = document(clips, text_layers=two_layers)
    check("document validates with two text layers", timeline.validate() == [], timeline.validate())
    plan = edl3_render.compile_timeline(timeline)
    graph = plan["filter_script"]
    check("two drawtext filters are emitted", graph.count("drawtext=") == 2, graph.count("drawtext="))
    concat_at = graph.find("concat=n=2")
    first_at = graph.find("drawtext=")
    check("drawtext comes after the concat", 0 <= concat_at < first_at, f"concat@{concat_at} text@{first_at}")
    check("the map is the last text label", plan["map"] == "[tx1]", plan["map"])
    check("both layers are reported", len(plan["text_layers"]) == 2, len(plan["text_layers"]))
    font = plan["text_layers"][0]["font"]
    check(
        "fontfile is the work's own file, not a system font",
        "assets" in font and font.endswith("Antonio-Regular.ttf") and Path(font).is_file(),
        Path(font).name,
    )
    check(
        "size/colour/align/position are carried into the record",
        plan["text_layers"][0]["size"] == 40
        and plan["text_layers"][0]["color"] == "#ffffff"
        and plan["text_layers"][0]["align"] == "left"
        and plan["text_layers"][0]["position"] == [0.10, 0.30],
        plan["text_layers"][0],
    )
    check(
        "timing is enable='between(t,start,end)'",
        "enable='between(t,0.300000,1.200000)'" in graph
        and "enable='between(t,2.300000,3.200000)'" in graph,
        "two windows",
    )

    stroke_plan = edl3_render.compile_timeline(
        document(clips, text_layers=[layer("BORDO", 0.4, 1.0, stroke=3.0)])
    )
    check(
        "stroke is a real borderw",
        "borderw=3:bordercolor=black" in stroke_plan["filter_script"],
        "borderw=3",
    )

    print("\n-- (b) two layers on two clips: pixels inside, none outside")
    plain_out = work / "plain.mp4"
    text_out = work / "text.mp4"
    edl3_render.render(document(clips), plain_out, workdir=work / "wd_plain")
    report = edl3_render.render(timeline, text_out, workdir=work / "wd_text")
    check("the text render reports both layers", len(report["text_layers"]) == 2, len(report["text_layers"]))
    check("both outputs exist", plain_out.is_file() and text_out.is_file())

    counts: Dict[float, Tuple[int, int]] = {}
    for t in (0.7, 1.7, 2.7, 3.7):
        plain = bright_pixels(frame_rgb(work, plain_out, t, f"plain_{t}"))
        text = bright_pixels(frame_rgb(work, text_out, t, f"text_{t}"))
        counts[t] = (plain, text)
    for t in (0.7, 2.7):
        plain, text = counts[t]
        check(
            f"t={t:g}s is inside a text window: bright pixels appear",
            plain == 0 and text >= 300,
            f"plain={plain} text={text} delta={text - plain}",
        )
    for t in (1.7, 3.7):
        plain, text = counts[t]
        check(
            f"t={t:g}s is outside every window: no bright pixels",
            plain == 0 and text == 0,
            f"plain={plain} text={text}",
        )

    print("\n-- (c) align and position move the anchor")
    low_left = bright_centroid(frame_rgb(work, text_out, 0.7, "centroid_low"))
    high_right = bright_centroid(frame_rgb(work, text_out, 2.7, "centroid_high"))
    check(
        "layer 1 is left of centre and high",
        low_left[0] < WIDTH / 2 and low_left[1] < HEIGHT / 2,
        f"centroid=({low_left[0]:.1f}, {low_left[1]:.1f})",
    )
    check(
        "layer 2 is right of centre and low",
        high_right[0] > WIDTH / 2 and high_right[1] > HEIGHT / 2,
        f"centroid=({high_right[0]:.1f}, {high_right[1]:.1f})",
    )

    print("\n-- (d) text after a crossfade and a per-clip effect")
    second = encode(
        work, "second", f"color=c=0x202020:s={WIDTH}x{HEIGHT}:r={FPS}:d=2"
    )
    crossfade_clips = [
        clip("red", source, src_in=0.0),
        clip(
            "blue",
            second,
            src_in=0.0,
            transition_in={"kind": "crossfade", "duration": 0.5, "style": "fade"},
            effects=[{"kind": "color", "params": {"contrast": 1.15}}],
        ),
    ]
    cross_timeline = document(
        crossfade_clips,
        text_layers=[layer("SEAM", 1.6, 2.2, y=0.5)],
    )
    cross_plan = edl3_render.compile_timeline(cross_timeline)
    cross_graph = cross_plan["filter_script"]
    xfade_at = cross_graph.find("xfade=")
    text_at = cross_graph.find("drawtext=")
    check(
        "drawtext comes after the crossfade",
        0 <= xfade_at < text_at,
        f"xfade@{xfade_at} text@{text_at}",
    )
    check(
        "the per-clip colour effect is still in the graph",
        "eq@grade1_0" in cross_graph,
        "eq@grade1_0",
    )
    cross_out = work / "cross.mp4"
    edl3_render.render(cross_timeline, cross_out, workdir=work / "wd_cross")
    seam = bright_pixels(frame_rgb(work, cross_out, 1.9, "seam"))
    check(
        "the text is painted over the dissolve",
        seam >= 300,
        f"bright={seam}",
    )

    print("\n-- (e) a field with no emitter is refused by name")
    bad_keyframes = work / "bad_keyframes.mp4"
    code, where = code_of(
        lambda: edl3_render.render(
            document(
                clips,
                text_layers=[
                    layer(
                        "X",
                        0.1,
                        0.9,
                        keyframes={
                            "opacity": [
                                {"t": 0.2, "value": 0.5, "interp": "linear"}
                            ]
                        },
                    )
                ],
            ),
            bad_keyframes,
            workdir=work / "wd_bad_kf",
        )
    )
    check(
        "keyframes.opacity is PARAM_NOT_RENDERED with the field name",
        code == "PARAM_NOT_RENDERED" and where == "text_layers[0].keyframes.opacity",
        f"{code} {where}",
    )
    check("no file is written for the refused keyframes", not bad_keyframes.exists())

    bad_multiline = work / "bad_multiline.mp4"
    code, where = code_of(
        lambda: edl3_render.render(
            document(clips, text_layers=[layer("A\nB", 0.1, 0.9)]),
            bad_multiline,
            workdir=work / "wd_bad_ml",
        )
    )
    check(
        "multi-line text is PARAM_NOT_RENDERED with text_layers[0].text",
        code == "PARAM_NOT_RENDERED" and where == "text_layers[0].text",
        f"{code} {where}",
    )
    check("no file is written for the refused text", not bad_multiline.exists())

    print("\n-- (f) a font that is not a file of the work is FONT_MISSING")
    missing_font = work / "missing_font.mp4"
    code, where = code_of(
        lambda: edl3_render.render(
            document(clips, text_layers=[layer("X", 0.1, 0.9, family="NoSuchFont-XYZ")]),
            missing_font,
            workdir=work / "wd_missing_font",
        )
    )
    check(
        "unknown family is FONT_MISSING at style.family",
        code == "FONT_MISSING" and where == "text_layers[0].style.family",
        f"{code} {where}",
    )
    check("no file is written for the missing font", not missing_font.exists())

    pinned = work / "pinned.mp4"
    code, where = code_of(
        lambda: edl3_render.render(
            document(clips, text_layers=[layer("X", 0.1, 0.9)]),
            pinned,
            workdir=work / "wd_pinned",
            font_files={"Antonio": str(work / "not_there.ttf")},
        )
    )
    check(
        "a pinned path that does not exist is FONT_MISSING",
        code == "FONT_MISSING" and where == "text_layers[0].style.family",
        f"{code} {where}",
    )
    check("no file is written for the bad pin", not pinned.exists())

    pinned_ok = work / "pinned_ok.mp4"
    bundled_inter = ROOT / "assets" / "fonts" / "Inter-Variable.ttf"
    report = edl3_render.render(
        document(clips, text_layers=[layer("PIN", 0.2, 0.8)]),
        pinned_ok,
        workdir=work / "wd_pinned_ok",
        font_files={"Antonio": str(bundled_inter)},
    )
    check(
        "a valid pin replaces the bundled file",
        pinned_ok.is_file()
        and Path(report["text_layers"][0]["font"]).resolve()
        == bundled_inter.resolve(),
        report["text_layers"][0]["font"],
    )

    print("\n-- (g) a chunked render refuses instead of dropping the text")
    chunked = work / "chunked.mp4"
    code, where = code_of(
        lambda: edl3_render.render(
            timeline, chunked, workdir=work / "wd_chunked", chunks=2
        )
    )
    check(
        "chunks=2 with text_layers is CHUNK_UNSUPPORTED",
        code == "CHUNK_UNSUPPORTED" and where == "text_layers",
        f"{code} {where}",
    )
    check("no file is written for the chunked text render", not chunked.exists())

    print("\n-- (h) the boundary: a caption track still declares no style")
    caption_timeline = document(
        clips,
        captions=[{"clips": [{"text": "ciao", "in": 0.0, "out": 1.0}]}],
    )
    code, where = code_of(
        lambda: edl3_render.compile_timeline(caption_timeline)
    )
    check(
        "captions remain UNSUPPORTED_EFFECT",
        code == "UNSUPPORTED_EFFECT" and where == "captions[0]",
        f"{code} {where}",
    )

    print("\n-- (i) a document without text keeps the pre-change graph")
    plain_plan = edl3_render.compile_timeline(document(clips))
    check(
        "no drawtext and the map is the concat output",
        "drawtext=" not in plain_plan["filter_script"] and plain_plan["map"] == "[out]",
        plain_plan["map"],
    )


# ---------------------------------------------------------------------- main


def main() -> int:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vx_edl3_text_") as tmp:
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


if __name__ == "__main__":
    raise SystemExit(main())
