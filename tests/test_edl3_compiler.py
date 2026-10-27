"""F2-T04 acceptance: the EDL 3 compiler routes backends, layers and audio.

Every rule is measured, never inferred from the implementation: the routing
table is read from :func:`vx.edl3_render.select_backend`, the overlay is
measured as luma inside and outside its window, the ducking as RMS during
speech and after it, and byte parity is proved against an expected graph built
here from the pre-change construction.

    python tests/test_edl3_compiler.py
"""

from __future__ import annotations

import re
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402
from vx import edl3_render  # noqa: E402
from vx.ffmpeg import run_ff  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FPS = 12
SIZE = (320, 568)
DURATION = 2.0
YAVG = re.compile(r"signalstats\.YAVG=([0-9.]+)")
MEAN_DB = re.compile(r"mean_volume:\s*(-?[0-9.]+)\s*dB")


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:66} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:66} {reason}")


# ------------------------------------------------------------------ helpers


def make_source(path: Path, generator: str) -> str:
    proc = run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            generator,
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


def make_tone(path: Path, frequency: int, duration: float) -> str:
    args = [
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:duration={duration}",
        "-ar",
        "48000",
        "-ac",
        "1",
        str(path),
    ]
    proc = run_ff(args, loglevel="error")
    return "" if proc.ok and path.exists() else proc.tail()


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


def window_rms(path: Path, start: float, end: float) -> float:
    proc = run_ff(
        [
            "-i",
            str(path),
            "-af",
            f"atrim=start={start:.3f}:end={end:.3f},volumedetect",
            "-f",
            "null",
            "-",
        ],
        loglevel="info",
    )
    match = MEAN_DB.search(proc.stderr or "")
    if not proc.ok or match is None:
        raise RuntimeError(f"window_rms({start},{end}) failed: {proc.tail()}")
    return float(match.group(1))


def band_rms(path: Path, start: float, end: float, frequency: int) -> float:
    """RMS of one narrow band, so the music is measured while the voice plays."""
    proc = run_ff(
        [
            "-i",
            str(path),
            "-af",
            f"atrim=start={start:.3f}:end={end:.3f},"
            f"bandpass=f={frequency}:width_type=h:w=20,volumedetect",
            "-f",
            "null",
            "-",
        ],
        loglevel="info",
    )
    match = MEAN_DB.search(proc.stderr or "")
    if not proc.ok or match is None:
        raise RuntimeError(f"band_rms({start},{end},{frequency}) failed: {proc.tail()}")
    return float(match.group(1))


def count(graph: str, needle: str) -> int:
    return graph.count(needle)


def compile_code(document, **kwargs) -> str:
    try:
        edl3_render.compile_timeline(document, **kwargs)
    except edl3_render.CompileError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001 - a wrong error type is a failure
        return f"__WRONG_TYPE__{type(exc).__name__}: {exc}"
    return ""


def opacity_track(points):
    return edl3.KeyframeTrack([edl3.Keyframe(t=t, value=v) for t, v in points])


def transform_effect(points=None, *, scale: float = 1.0, **kwargs) -> edl3.Effect:
    keyframes = {}
    if points is not None:
        keyframes["opacity"] = opacity_track(points)
    return edl3.Effect(
        kind="transform",
        params={"scale": scale, "opacity": 1.0},
        keyframes=keyframes,
        **kwargs,
    )


def clip(
    cid: str,
    source: Path,
    *,
    src_in=0.0,
    src_out=DURATION,
    dst=None,
    layer=0,
    effects=None,
) -> edl3.Clip3:
    return edl3.Clip3(
        id=cid,
        source=str(source),
        src_in=src_in,
        src_out=src_out,
        dst_in=None if dst is None else dst[0],
        dst_out=None if dst is None else dst[1],
        layer=layer,
        effects=effects or [],
    )


def expected_direct_graph(index: int, label: str) -> str:
    """The pre-change construction for a clip with no transform effect."""
    return (
        f"[{index}:v]setpts=PTS-STARTPTS,"
        f"scale={SIZE[0]}:{SIZE[1]}:force_original_aspect_ratio=decrease:flags=bicubic,"
        f"pad={SIZE[0]}:{SIZE[1]}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
        f"[{label}]"
    )


# --------------------------------------------------------------------- main


def main() -> int:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vx_edl3_compiler_") as tmp:
        work = Path(tmp)
        dark = work / "dark.mp4"
        white = work / "white.mp4"
        speech = work / "speech.wav"
        bed = work / "bed.wav"
        problems = [
            make_source(
                dark, f"color=c=0x101010:s={SIZE[0]}x{SIZE[1]}:r={FPS}:d={DURATION}"
            ),
            make_source(
                white, f"color=c=white:s={SIZE[0]}x{SIZE[1]}:r={FPS}:d={DURATION}"
            ),
            make_tone(speech, 440, 1.0),
            make_tone(bed, 220, DURATION),
        ]
        problems = [problem for problem in problems if problem]
        if problems:
            skip("synthetic sources", problems[0])
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 0

        try:
            run_checks(work, dark, white, speech, bed)
        except Exception:  # noqa: BLE001 - a crash is a failing test, never a skip
            traceback.print_exc()
            check("test harness completed without an unexpected exception", False)

    elapsed = time.monotonic() - started
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  ({elapsed:.1f}s)")
    check("suite stays under the 120 s ceiling", elapsed < 120.0, f"{elapsed:.1f}s")
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


def run_checks(work: Path, dark: Path, white: Path, speech: Path, bed: Path) -> None:
    print("\n-- (a) routing table")
    two = transform_effect([(0.0, 1.0), (DURATION, 0.5)])
    six = transform_effect(
        [(0.0, 1.0), (0.4, 0.9), (0.8, 0.8), (1.2, 0.7), (1.6, 0.6), (DURATION, 0.5)]
    )
    entry = edl3_render.select_backend(two)
    check(
        "(a) 2 keyframes -> expr / default",
        entry["chosen"] == "expr" and entry["rule"] == "default",
        f"{entry['chosen']}/{entry['rule']}",
    )
    entry = edl3_render.select_backend(six)
    check(
        "(a) 6 keyframes -> sendcmd / keyframe_threshold",
        entry["chosen"] == "sendcmd" and entry["rule"] == "keyframe_threshold",
        f"{entry['chosen']}/{entry['rule']}",
    )
    four = transform_effect([(0.0, 1.0), (0.6, 0.8), (1.2, 0.6), (DURATION, 0.4)])
    doc_four = edl3.Timeline3(
        fps=FPS, size=SIZE, clips=[clip("c1", dark, effects=[four])]
    )
    check(
        "(a) one constant decides: 4 keyframes flip for compiler and report",
        edl3_render.choose_backend(doc_four) == "sendcmd"
        and edl3_render.select_backend(four)["rule"] == "keyframe_threshold"
        and edl3_render.SENDCMD_THRESHOLD == 3,
        edl3_render.choose_backend(doc_four),
    )

    chromium_layout = transform_effect(
        [(0.0, 1.0), (DURATION, 0.5)],
        backend="chromium",
        properties=("layout",),
    )
    entry = edl3_render.select_backend(chromium_layout)
    check(
        "(a) chromium+layout, flag False -> chromium_unavailable",
        entry["rule"] == "chromium_unavailable"
        and entry["chosen"] == ""
        and entry["available"] is False,
        f"{entry['chosen']}/{entry['rule']}/{entry['available']}",
    )
    entry = edl3_render.select_backend(
        chromium_layout, settings={"chromium_available": True}
    )
    check(
        "(a) chromium+layout, flag True -> chromium_qualified",
        entry["rule"] == "chromium_qualified"
        and entry["chosen"] == "chromium"
        and entry["available"] is True,
        f"{entry['chosen']}/{entry['rule']}",
    )
    check(
        "(a) every declared rule is reachable and no rule is undeclared",
        edl3_render.RULES
        == {
            "default",
            "keyframe_threshold",
            "chromium_qualified",
            "chromium_redirected",
            "chromium_unavailable",
            "no_fallback_refused",
        },
        sorted(edl3_render.RULES),
    )

    redirected = transform_effect(
        [(0.0, 1.0), (DURATION, 0.5)],
        backend="chromium",
        properties=(),
    )
    entry = edl3_render.select_backend(redirected)
    check(
        "(a) chromium without qualifying property -> chromium_redirected",
        entry["rule"] == "chromium_redirected"
        and entry["chosen"] == "expr"
        and entry["fallback"] == "expr",
        f"{entry['chosen']}/{entry['rule']}",
    )
    routed_doc = edl3.Timeline3(
        fps=FPS, size=SIZE, clips=[clip("c1", dark, effects=[redirected])]
    )
    routed_plan = edl3_render.compile_timeline(routed_doc)
    check(
        "(a) the redirected node compiles with its fallback (geq, no sendcmd)",
        "geq=" in routed_plan["filter_script"]
        and "sendcmd" not in routed_plan["filter_script"],
        routed_plan["backends"],
    )
    check(
        "(a) the report states the rule that decided",
        routed_plan["backends"][0]["rule"] == "chromium_redirected"
        and routed_plan["chromium"]["redirected"] == 1
        and routed_plan["chromium"]["available"] is False,
        routed_plan["chromium"],
    )

    no_fallback = edl3.Effect(kind="chroma_key", params={}, backend="chromium")
    entry = edl3_render.select_backend(no_fallback)
    check(
        "(a) chromium with no property and no fallback -> no_fallback_refused",
        entry["rule"] == "no_fallback_refused" and entry["chosen"] == "",
        f"{entry['chosen']}/{entry['rule']}",
    )
    refusing_doc = edl3.Timeline3(
        fps=FPS, size=SIZE, clips=[clip("c1", dark, effects=[no_fallback])]
    )
    check(
        "(a) that node is refused with the declared BACKEND_REFUSED",
        compile_code(refusing_doc) == "BACKEND_REFUSED",
        compile_code(refusing_doc),
    )
    unavailable_doc = edl3.Timeline3(
        fps=FPS, size=SIZE, clips=[clip("c1", dark, effects=[chromium_layout])]
    )
    check(
        "(a) a qualified node is refused, not faked, while the engine is absent",
        compile_code(unavailable_doc) == "BACKEND_UNAVAILABLE",
        compile_code(unavailable_doc),
    )
    check(
        "(a) with the engine available but no generated layer, it names what it needs",
        compile_code(unavailable_doc, settings={"chromium_available": True})
        == "CHROMIUM_NEEDS_GENERATOR",
        compile_code(unavailable_doc, settings={"chromium_available": True}),
    )
    check(
        "(a) an undeclared property is refused, not ignored",
        compile_code(
            edl3.Timeline3(
                fps=FPS,
                size=SIZE,
                clips=[
                    clip(
                        "c1",
                        dark,
                        effects=[
                            transform_effect(
                                backend="chromium", properties=("backdrop-filter",)
                            )
                        ],
                    )
                ],
            )
        )
        == "BACKEND_PROPERTY",
        "backdrop-filter (hyphen) is not a declared property",
    )
    check(
        "(a) an unknown setting is a usage error, not a silent no-op",
        compile_code(routed_doc, settings={"chromium": True}) == "SETTINGS_UNKNOWN",
        compile_code(routed_doc, settings={"chromium": True}),
    )
    check(
        "(a) declaring a mechanism the compiler cannot emit is refused",
        compile_code(
            edl3.Timeline3(
                fps=FPS,
                size=SIZE,
                clips=[
                    clip(
                        "c1",
                        dark,
                        effects=[
                            edl3.Effect(
                                kind="color",
                                params={"saturation": 1.0},
                                backend="expr",
                            )
                        ],
                    )
                ],
            )
        )
        == "BACKEND_UNIMPLEMENTED",
        "color declares expr, but eq is only reachable through sendcmd",
    )

    print("\n-- (b) multi-layer video")
    overlay_doc = edl3.Timeline3(
        fps=FPS,
        size=SIZE,
        clips=[
            clip("base", dark, dst=(0.0, DURATION), layer=0),
            clip("broll", white, src_in=0.0, src_out=0.4, dst=(0.3, 0.7), layer=1),
        ],
    )
    check(
        "(b) the two-layer document validates cleanly",
        overlay_doc.validate() == [],
        overlay_doc.validate(),
    )
    overlay_plan = edl3_render.compile_timeline(overlay_doc)
    overlay_graph = overlay_plan["filter_script"]
    check(
        "(b) the overlay is gated by enable='between(t,start,end)'",
        "enable='between(t,0.300000,0.700000)'" in overlay_graph,
        [line for line in overlay_graph.splitlines() if "overlay=" in line],
    )
    check(
        "(b) the overlay input is shifted to its window start",
        "[c1]setpts=PTS+0.300000/TB[ov0]" in overlay_graph,
        "[c1]setpts=PTS+0.300000/TB[ov0]",
    )
    check(
        "(b) layer 0 is still concatenated as before",
        "concat" not in overlay_graph
        and overlay_graph.splitlines()[0].startswith("[0:v]setpts=PTS-STARTPTS"),
        "single base clip: no concat",
    )
    overlay_report = edl3_render.render(
        overlay_doc, work / "overlay.mp4", workdir=work / "overlay"
    )
    luma_inside = mean_luma(work / "overlay.mp4", 0.5)
    luma_outside = mean_luma(work / "overlay.mp4", 1.2)
    delta = luma_inside - luma_outside
    check(
        "(b) luma inside the window exceeds outside by more than 8",
        delta > 8.0,
        f"inside {luma_inside:.2f} - outside {luma_outside:.2f} = {delta:.2f}",
    )
    check(
        "(b) the report says one overlay was compiled",
        overlay_report["overlays"] == 1,
        overlay_report["overlays"],
    )
    ordered_doc = edl3.Timeline3(
        fps=FPS,
        size=SIZE,
        clips=[
            clip("top", white, src_in=0.0, src_out=0.4, dst=(1.2, 1.6), layer=2),
            clip("base", dark, dst=(0.0, DURATION), layer=0),
            clip("under", white, src_in=0.0, src_out=0.4, dst=(0.3, 0.7), layer=1),
        ],
    )
    ordered_graph = edl3_render.compile_timeline(ordered_doc)["filter_script"]
    first = ordered_graph.index("[c2]setpts=PTS+0.300000/TB[ov0]")
    second = ordered_graph.index("[c0]setpts=PTS+1.200000/TB[ov1]")
    check(
        "(b) overlays are ordered by layer, then start",
        first < second and "[base0][ov1]overlay=" in ordered_graph,
        "layer 1 before layer 2 even when the document lists layer 2 first",
    )

    print("\n-- (c) same-layer overlap")
    overlap_doc = edl3.Timeline3(
        fps=FPS,
        size=SIZE,
        clips=[
            clip("base", dark, dst=(0.0, DURATION), layer=0),
            clip("o1", white, src_in=0.0, src_out=0.6, dst=(0.2, 0.8), layer=1),
            clip("o2", white, src_in=0.0, src_out=0.6, dst=(0.6, 1.2), layer=1),
        ],
    )
    check(
        "(c) overlapping windows on one layer are refused with LAYER_OVERLAP",
        compile_code(overlap_doc) == "LAYER_OVERLAP",
        compile_code(overlap_doc),
    )
    check(
        "(c) the declared code is exported",
        "LAYER_OVERLAP" in edl3_render.REFUSAL_CODES,
        sorted(edl3_render.REFUSAL_CODES),
    )

    print("\n-- (d) audio bus and ducking")
    audio_doc = edl3.Timeline3(
        fps=FPS,
        size=SIZE,
        clips=[clip("base", dark, dst=(0.0, DURATION), layer=0)],
        audio=[
            edl3.AudioTrack3(
                role="dialogue",
                clips=[
                    edl3.AudioClip3(
                        source=str(speech),
                        src_in=0.0,
                        src_out=1.0,
                        dst_in=0.0,
                        dst_out=1.0,
                        gain_db=0.0,
                    )
                ],
            ),
            edl3.AudioTrack3(
                role="music",
                clips=[
                    edl3.AudioClip3(
                        source=str(bed),
                        src_in=0.0,
                        src_out=DURATION,
                        dst_in=0.0,
                        dst_out=DURATION,
                        gain_db=-6.0,
                    )
                ],
            ),
        ],
        ducking=edl3.Ducking3(threshold=0.01, ratio=8.0),
    )
    check(
        "(d) the audio document validates cleanly",
        audio_doc.validate() == [],
        audio_doc.validate(),
    )
    audio_report = edl3_render.render(
        audio_doc,
        work / "audio.mp4",
        workdir=work / "audio",
        audio="mix",
    )
    audio_graph = audio_report["filter_script"]
    check(
        "(d) sidechaincompress appears exactly once",
        count(audio_graph, "sidechaincompress") == 1,
        count(audio_graph, "sidechaincompress"),
    )
    check(
        "(d) adelay appears exactly once",
        count(audio_graph, "adelay") == 1,
        count(audio_graph, "adelay"),
    )
    check(
        "(d) amix appears exactly once",
        count(audio_graph, "amix") == 1,
        count(audio_graph, "amix"),
    )
    check(
        "(d) the dialogue is split so the sidechain does not consume it",
        "asplit=2[dialog_mix][dialog_sc]" in audio_graph
        and "[dialog_sc]apad=whole_dur=2.000000[dialog_sc_pad]" in audio_graph
        and "[mus0][dialog_sc_pad]sidechaincompress" in audio_graph,
        [line for line in audio_graph.splitlines() if "asplit" in line],
    )
    check(
        "(d) the report names the mixed bus and its map",
        audio_report["audio_map"] == "[aout]" and audio_report["audio_policy"] == "mix",
        f"{audio_report['audio_policy']} {audio_report['audio_map']}",
    )
    dialogue_db = window_rms(work / "audio.mp4", 0.15, 0.90)
    music_during_db = band_rms(work / "audio.mp4", 0.15, 0.90, 220)
    music_after_db = band_rms(work / "audio.mp4", 1.10, 1.90, 220)
    ducking_db = music_after_db - music_during_db
    check(
        "(d) music is at least 6 dB lower during speech than after it ends",
        ducking_db >= 6.0,
        f"music during {music_during_db:.2f} dB, after {music_after_db:.2f} dB, "
        f"ducking {ducking_db:.2f} dB",
    )
    check(
        "(d) the dialogue window is louder than the music-only window",
        dialogue_db - music_after_db >= 6.0,
        f"dialogue window {dialogue_db:.2f} dB, music-only window "
        f"{music_after_db:.2f} dB",
    )
    print(
        f"       measured: dialogue window 0.15-0.90s = {dialogue_db:.2f} dB, "
        f"music band during speech = {music_during_db:.2f} dB, "
        f"music band after = {music_after_db:.2f} dB "
        f"(ducking {ducking_db:.2f} dB)"
    )

    print("\n-- (e) byte parity with the pre-change construction")
    single_doc = edl3.Timeline3(
        fps=FPS,
        size=SIZE,
        clips=[clip("c1", dark, dst=(0.0, DURATION), layer=0)],
    )
    single_plan = edl3_render.compile_timeline(single_doc)
    expected_single = expected_direct_graph(0, "c0") + "\n"
    check(
        "(e) one-clip graph is byte-identical to the slice construction",
        single_plan["filter_script"] == expected_single,
        repr(single_plan["filter_script"]),
    )
    check(
        "(e) its map is the same label",
        single_plan["map"] == "[c0]",
        single_plan["map"],
    )
    check(
        "(e) its input arguments are unchanged",
        single_plan["input_args"]
        == ["-ss", "0.000000", "-t", "2.000000", "-i", str(dark)],
        single_plan["input_args"],
    )
    two_doc = edl3.Timeline3(
        fps=FPS,
        size=SIZE,
        clips=[
            clip("c1", dark, src_in=0.0, src_out=1.0, dst=(0.0, 1.0), layer=0),
            clip("c2", dark, src_in=1.0, src_out=2.0, dst=(1.0, 2.0), layer=0),
        ],
    )
    expected_two = (
        expected_direct_graph(0, "c0")
        + ";\n"
        + expected_direct_graph(1, "c1")
        + ";\n[c0][c1]concat=n=2:v=1:a=0[out]\n"
    )
    check(
        "(e) two-clip graph is byte-identical to the slice construction",
        edl3_render.compile_timeline(two_doc)["filter_script"] == expected_two,
        repr(edl3_render.compile_timeline(two_doc)["filter_script"]),
    )
    audio_dropped = edl3_render.compile_timeline(audio_doc, audio="drop")
    check(
        "(e) a one-clip, one-audio document dropped to picture keeps the same graph",
        audio_dropped["filter_script"] == expected_single
        and audio_dropped["audio_map"] is None,
        repr(audio_dropped["filter_script"]),
    )
    check(
        "(e) dropping audio adds no audio chain",
        "[aout]" not in audio_dropped["filter_script"]
        and ":a]" not in audio_dropped["filter_script"],
        audio_dropped["audio_map"],
    )

    print("\n-- (f) a document without audio builds no audio graph")
    plain_report = edl3_render.render(
        single_doc, work / "plain.mp4", workdir=work / "plain"
    )
    check(
        "(f) no audio graph in the script",
        "aout" not in plain_report["filter_script"]
        and ":a]" not in plain_report["filter_script"],
        plain_report["filter_script"].splitlines()[-1],
    )
    check(
        "(f) no audio map in the report",
        plain_report["audio_map"] is None,
        plain_report["audio_map"],
    )
    check(
        "(f) the command carries -an and no audio codec or audio -map",
        "-an" in plain_report["command"]
        and "-c:a" not in plain_report["command"]
        and plain_report["command"].count("-map") == 1,
        [item for item in plain_report["command"] if item in ("-an", "-map", "-c:a")],
    )


if __name__ == "__main__":
    raise SystemExit(main())
