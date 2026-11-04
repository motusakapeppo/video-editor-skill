"""The fast analysis path: proxy metrics, one decode, selected hashes, one model.

What is asserted here is not "it is faster" (that is the bench's job) but that
the shortcuts cannot change a verdict:

* pixel metrics on a proxy decode already at 320 px, and the same code path
  when there is no proxy;
* one ffmpeg pass for cuts + freezes gives the same counts as the three
  separate passes on a clip with two hard cuts and one freeze;
* the hash guard covers exactly the selected assets;
* whisper is built once and reused, and ``max_duration`` reaches the engine.

    python tests/test_analyze_fast.py
"""

from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import analyze, auto, ffmpeg as ff, subs  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def fixture(path: Path) -> None:
    """Three 1 s segments: moving, still, moving -> two cuts and one freeze."""
    proc = ff.run_ff(
        [
            "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=1",
            "-f", "lavfi", "-i", "color=c=red:s=640x360:r=25:d=1",
            "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=1",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
            str(path),
        ],
        loglevel="error",
    )
    if not proc.ok:
        raise RuntimeError(proc.tail())


def test_proxy_decode(tmp: Path) -> Path:
    print("\n-- metriche px: proxy decodificata a 320, non rimpicciolita dopo")
    clip = tmp / "clip.mp4"
    fixture(clip)
    seen: dict = {}
    real_ff = analyze._ffmpeg_frames
    real_cv = analyze._cv2_frames

    def spy_ff(path, rate, width, max_seconds, meta):
        seen["width"] = width
        for item in real_ff(path, rate, width, max_seconds, meta):
            yield item

    def forbidden_cv(*args, **kwargs):
        seen["cv2"] = True
        raise AssertionError("cv2 path used on a proxy")

    analyze._ffmpeg_frames = spy_ff
    analyze._cv2_frames = forbidden_cv
    try:
        result = analyze._pixel_metrics(
            clip, 10.0, decode_width=320, source_width=640
        )
    finally:
        analyze._ffmpeg_frames = real_ff
        analyze._cv2_frames = real_cv
    check("ffmpeg decodes already at the declared width", seen.get("width") == 320, seen)
    check("the full-resolution cv2 path is not touched", not seen.get("cv2"))
    check("exposure still has a verdict", result["exposure"].get("verdict") not in (None, "unknown"), result["exposure"])
    check("jitter is reported in source pixels", float(result["jitter"]["mean_residual_px"]) >= 0.0, result["jitter"])

    shapes = []
    meta: dict = {}
    for _t, frame in analyze._ffmpeg_frames(clip, 10.0, 320, None, meta):
        shapes.append(frame.shape)
        break
    check("decoded frame shape is exactly 320 wide", shapes and shapes[0][1] == 320, shapes)
    check("declared height follows the source aspect", meta.get("decode_height") == shapes[0][0], (meta.get("decode_height"), shapes))
    return clip


def test_fallback(clip: Path) -> None:
    print("\n-- senza proxy: la via di oggi, dichiarata, non un errore")
    seen: dict = {}
    real_ff = analyze._ffmpeg_frames
    real_cv = analyze._cv2_frames

    def spy_cv(path, rate, max_seconds, meta):
        seen["cv2"] = True
        for item in real_cv(path, rate, max_seconds, meta):
            yield item

    def forbidden_ff(*args, **kwargs):
        raise AssertionError("ffmpeg path used without a proxy")

    analyze._ffmpeg_frames = forbidden_ff
    analyze._cv2_frames = spy_cv
    try:
        result = analyze._pixel_metrics(clip, 10.0)
    finally:
        analyze._ffmpeg_frames = real_ff
        analyze._cv2_frames = real_cv
    check("the full-resolution path is the fallback", seen.get("cv2") is True)
    check("the fallback still returns real numbers", result["exposure"]["mean_luma"] > 0.0, result["exposure"]["mean_luma"])

    seen.clear()
    analyze._ffmpeg_frames = forbidden_ff
    analyze._cv2_frames = spy_cv
    try:
        whole = analyze.analyze_video(
            clip, proxy_path=str(clip.parent / "missing_proxy.mp4")
        )
    finally:
        analyze._ffmpeg_frames = real_ff
        analyze._cv2_frames = real_cv
    check("a missing proxy falls back to the full decode", seen.get("cv2") is True)
    check("the fallback is not degraded", whole.exposure.get("verdict") != "unknown", whole.exposure)


def test_single_pass(clip: Path) -> None:
    print("\n-- un passaggio solo: stessi tagli e freeze di tre passaggi")
    one_cuts, one_freezes = analyze._scan_cuts_freezes(clip)
    three_cuts = analyze.detect_scene_cuts(clip)
    three_freezes = analyze.detect_freezes(clip)
    check("cut count identical", len(one_cuts) == len(three_cuts), f"{one_cuts} vs {three_cuts}")
    check("cut times identical", one_cuts == three_cuts, f"{one_cuts} vs {three_cuts}")
    check("freeze count identical", len(one_freezes) == len(three_freezes), f"{one_freezes} vs {three_freezes}")
    check("freeze spans identical", one_freezes == three_freezes, f"{one_freezes} vs {three_freezes}")
    check("the synthetic clip really has two cuts", len(one_cuts) == 2, one_cuts)


def test_hashes() -> None:
    print("\n-- hash: solo quello che entra nel master")
    assets = [
        SimpleNamespace(asset_id="a", path=Path("a.mov")),
        SimpleNamespace(asset_id="b", path=Path("b.mov")),
        SimpleNamespace(asset_id="c", path=Path("c.mov")),
    ]
    by_id = {asset.asset_id: asset for asset in assets}
    selected = [
        {"asset_id": "c"},
        {"asset_id": "a"},
        {"asset_id": "a"},
        {"asset_id": "missing"},
    ]
    chosen = auto._selected_asset_infos(selected, by_id)
    check("selected order is preserved", [a.asset_id for a in chosen] == ["c", "a"], [a.asset_id for a in chosen])
    check("duplicates collapse", len(chosen) == 2, len(chosen))
    check("a selected id absent from the pool is not invented", "missing" not in [a.asset_id for a in chosen])
    real = auto._sha256
    calls: list = []
    auto._sha256 = lambda path: calls.append(str(path)) or "x"
    try:
        hashed = auto._source_hashes(chosen)
    finally:
        auto._sha256 = real
    check("only selected assets are hashed", sorted(calls) == ["a.mov", "c.mov"], calls)
    check("the map has one entry per selected asset", set(hashed) == {"a", "c"}, sorted(hashed))


def test_threads() -> None:
    print("\n-- thread proporzionali, poi tagliati dai worker")
    check("4K asks for more than two threads", auto._threads_for("analysis", 3840) >= 4, auto._threads_for("analysis", 3840))
    check("HD asks for fewer", auto._threads_for("analysis", 1280) <= auto._threads_for("analysis", 3840), auto._threads_for("analysis", 1280))
    capped = auto._cap_threads([("analysis", "a.mov", {}, None, 8)] * 6, workers=6)
    check("six workers cannot each take eight threads", all(job[4] <= max(2, (auto.os.cpu_count() or 4) // 6) for job in capped), [job[4] for job in capped])


def test_whisper(tmp: Path) -> None:
    print("\n-- whisper: un modello per run, max_duration al motore")
    first = tmp / "one.wav"
    second = tmp / "two.wav"
    first.write_bytes(b"RIFF" + b"\x00" * 64)
    second.write_bytes(b"RIFF" + b"\x00" * 64)

    class FakeInfo:
        duration = 1.0
        language = "it"

    class FakeModel:
        builds = 0
        calls: list = []

        def __init__(self, model, device, compute_type):
            type(self).builds += 1

        def transcribe(self, path, **kwargs):
            type(self).calls.append(kwargs)
            return iter(()), FakeInfo()

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = FakeModel
    real_module = sys.modules.get("faster_whisper")
    sys.modules["faster_whisper"] = fake
    subs.reset_whisper_cache()
    try:
        subs.transcribe(first, device="cpu", compute_type="int8", max_duration=3.0)
        subs.transcribe(second, device="cpu", compute_type="int8")
    finally:
        subs.reset_whisper_cache()
        if real_module is not None:
            sys.modules["faster_whisper"] = real_module
        else:
            sys.modules.pop("faster_whisper", None)
    check("the model is instantiated once for two files", FakeModel.builds == 1, FakeModel.builds)
    check("both files went through the same engine", len(FakeModel.calls) == 2, len(FakeModel.calls))
    check("max_duration bounds the engine, not just the output", FakeModel.calls and FakeModel.calls[0].get("clip_timestamps") == [0.0, 3.0], FakeModel.calls[0] if FakeModel.calls else None)


def main() -> int:
    if not analyze._CV_OK:
        print(f"OpenCV unavailable: {analyze._CV_WHY}")
        return 1
    with tempfile.TemporaryDirectory(prefix="vx-analyze-fast-") as raw:
        tmp = Path(raw)
        clip = test_proxy_decode(tmp)
        test_fallback(clip)
        test_single_pass(clip)
    test_hashes()
    test_threads()
    with tempfile.TemporaryDirectory(prefix="vx-analyze-fast-w-") as raw:
        test_whisper(Path(raw))
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
