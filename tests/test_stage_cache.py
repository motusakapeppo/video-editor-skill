"""Stage cache: a render stage is keyed on what can change its bytes.

Acceptance for F0-T04/F0-T05:
    (a) second identical run -> 0 stages executed, every stage a hit;
    (b) a changed subtitle -> exactly the dependent stages re-execute;
    (c) a missing or truncated cached artifact is a miss, never a false hit;
    (d) a corrupt cache JSON is ignored, not fatal;
    (e) a parameter change invalidates only the stages that read that parameter.

The cache is opt-in: with ``features.stage_cache`` off (the default) a render
must touch no cache at all — zero lookups, zero writes, no file whose name
carries ``stage_cache``, and no counter in the report.

The counts are the assertion: elapsed time is reported, never asserted.

python tests/test_stage_cache.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import contract, features, render, settings, stagecache  # noqa: E402
from vx.edl_plan import ClipSegment, EDLPlan, SourceRef, Track  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"
WIDTH, HEIGHT = 320, 568
ALL_STAGES = ["01_trim", "02_detect", "03_graph", "04_loudness"]

ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 1136

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Antonio,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:60} {reason}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _clip(path: Path) -> bool:
    proc = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={WIDTH}x{HEIGHT}:rate=25:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(path),
        ],
        capture_output=True,
    )
    return proc.returncode == 0 and path.is_file()


def _write_ass(path: Path, text: str) -> None:
    path.write_text(
        ASS_HEAD + f"Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,{text}\n",
        encoding="utf-8",
    )


def _evidence(finding_id: str) -> list:
    return [{"finding_id": finding_id, "t0": 0.0, "t1": 2.0}]


def _plan(*, loudness_i: float = -14.0, smoothing: int = 10) -> contract.EditPlan:
    return contract.EditPlan(
        output={
            "aspect": "9:16",
            "loudness": {"i": loudness_i, "tp": -1.0, "lra": 11.0},
        },
        cuts=[{"in": 0.0, "out": 2.0}],
        render={"precision": "proxy"},
        treatments=[
            {
                "name": "stabilize",
                "enabled": True,
                "params": {
                    "shakiness": 5,
                    "accuracy": 9,
                    "smoothing": smoothing,
                    "zoom": 0,
                },
                "reason": {"finding_id": "video.shake", "explain": "test"},
                "evidence": _evidence("video.shake"),
            },
            {
                "name": "loudnorm",
                "enabled": True,
                "params": {"I": -14.0, "TP": -1.0, "LRA": 11.0},
                "reason": {"finding_id": "audio.loudness.off", "explain": "test"},
                "evidence": _evidence("audio.loudness.off"),
            },
            {
                "name": "subtitles",
                "enabled": True,
                "params": {},
                "reason": {"finding_id": "audio.speech.present", "explain": "test"},
                "evidence": _evidence("audio.speech.present"),
            },
        ],
    )


def _media(source: Path) -> dict:
    return {
        "path": str(source),
        "width": WIDTH,
        "height": HEIGHT,
        "fps": 25.0,
        "duration": 2.0,
        "has_audio": True,
    }


def _run(plan: contract.EditPlan, media: dict, work: Path, subs: Path) -> dict:
    started = time.time()
    result = render.render(
        plan, media, precision="proxy", workdir=work, subs_path=str(subs)
    )
    print(f"        ({time.time() - started:.2f}s)")
    return result


def _entries(work: Path) -> list:
    return sorted((work / stagecache.CACHE_DIR).glob("*.json"))


def _edl_plan(source: Path) -> EDLPlan:
    sources = {"s": SourceRef(str(source), _sha256(source), 2.0)}
    video = ClipSegment("v", "s", 0.0, 2.0, 0.0, 2.0, 0, "main_talking_head")
    dialogue = ClipSegment("d", "s", 0.0, 2.0, 0.0, 2.0, 0, "dialogue")
    return EDLPlan(
        fps=25.0,
        duration_sec=2.0,
        aspect_ratio="9:16",
        resolution=(WIDTH, HEIGHT),
        sources=sources,
        video_tracks=(Track(0, (video,), "main"),),
        audio_tracks=(Track(0, (dialogue,), "dialogue"),),
    )


def _count_calls(module, names):
    """Count calls to ``module.<name>`` and return the undo callable."""
    counts = {name: 0 for name in names}
    saved = {name: getattr(module, name) for name in names}
    for name in names:

        def wrapper(*args, _name=name, _fn=saved[name], **kwargs):
            counts[_name] += 1
            return _fn(*args, **kwargs)

        setattr(module, name, wrapper)

    def restore() -> None:
        for item, original in saved.items():
            setattr(module, item, original)

    return counts, restore


def test_flag_off(root: Path) -> None:
    """Default off means the cache does not exist, not that it is silently inert."""
    check(
        "flag: declared, default off, mirrored in settings.DEFAULTS",
        features.FEATURES["stage_cache"].default is False
        and settings.DEFAULTS["features"]["stage_cache"] is False,
        (
            features.FEATURES["stage_cache"].default,
            settings.DEFAULTS["features"]["stage_cache"],
        ),
    )
    settings.apply(settings.build(overrides=["features.stage_cache=off"]))
    check("flag: off is the effective value", features.enabled("stage_cache") is False)

    source = root / "off.mp4"
    if not source.exists() and not _clip(source):
        skip("flag off", "ffmpeg/libx264/aac unavailable")
        return
    subs = root / "off.ass"
    _write_ass(subs, "Ciao mondo")
    work = root / "off_work"
    calls, restore = _count_calls(stagecache, ("key", "lookup", "record"))
    try:
        result = render.render(
            _plan(),
            _media(source),
            precision="proxy",
            workdir=work,
            subs_path=str(subs),
        )
    finally:
        restore()

    check("flag off: zero lookups", calls["lookup"] == 0, calls)
    check(
        "flag off: zero keys and zero writes",
        calls["key"] == 0 and calls["record"] == 0,
        calls,
    )
    stray = [item.name for item in work.rglob("*") if "stage_cache" in item.name]
    check("flag off: no *stage_cache* file anywhere", stray == [], stray)
    check(
        "flag off: the render report carries no counter",
        "stage_cache" not in result,
        sorted(result),
    )
    check(
        "flag off: every stage still ran",
        result["stages"] == ["01_trim", "02_detect", "03_graph", "04_loudnorm"],
        result["stages"],
    )
    master = Path(result["path"])
    check(
        "flag off: master produced",
        master.is_file() and master.stat().st_size > 0,
    )

    edl_work = root / "off_auto" / "render"
    edl_stats: dict = {}
    calls, restore = _count_calls(stagecache, ("key", "lookup", "record"))
    try:
        render.render_edl(
            _edl_plan(source),
            {"s": str(source)},
            root / "off_edl_master.mp4",
            workdir=edl_work,
            crf=35,
            preset="ultrafast",
            loudness=(-14.0, -1.0),
            cache_report=edl_stats,
        )
    finally:
        restore()
    check(
        "flag off: render_edl makes zero cache calls",
        calls == {"key": 0, "lookup": 0, "record": 0},
        calls,
    )
    stray = [
        str(item.relative_to(root / "off_auto"))
        for item in (root / "off_auto").rglob("*")
        if "stage_cache" in item.name
    ]
    check("flag off: no work/render/stage_cache* file", stray == [], stray)
    check("flag off: render_edl leaves cache_report empty", edl_stats == {}, edl_stats)


def test_units() -> None:
    """The cache contract itself, without ffmpeg."""
    inputs = {"source_sha256": "a" * 64, "graph": "[0:v]null[vout]"}
    base = stagecache.key("03_graph", inputs, {"has_audio": True}, "1")
    check(
        "key: deterministic",
        base == stagecache.key("03_graph", inputs, {"has_audio": True}, "1"),
        base[:16],
    )
    check(
        "key: stage sensitive",
        base != stagecache.key("02_detect", inputs, {"has_audio": True}, "1"),
    )
    check(
        "key: input sensitive",
        base
        != stagecache.key(
            "03_graph",
            {**inputs, "graph": "[0:v]null,eq=1[vout]"},
            {"has_audio": True},
            "1",
        ),
    )
    check(
        "key: param sensitive",
        base != stagecache.key("03_graph", inputs, {"has_audio": False}, "1"),
    )
    check(
        "key: version sensitive",
        base != stagecache.key("03_graph", inputs, {"has_audio": True}, "2"),
    )

    with tempfile.TemporaryDirectory(prefix="vx-stagecache-") as raw:
        work = Path(raw)
        artifact = work / "out.bin"
        artifact.write_bytes(b"x" * 32)
        digest = stagecache.key("03_graph", inputs, {}, "1")
        check("lookup: unknown key is a miss", stagecache.lookup(work, digest) is None)
        entry = stagecache.record(work, digest, artifact, {"stage": "03_graph"})
        check("record: writes an entry", entry is not None and entry.is_file())
        found = stagecache.lookup(work, digest)
        check(
            "lookup: round-trip path + meta",
            found is not None
            and found["path"] == str(artifact)
            and found["meta"] == {"stage": "03_graph"}
            and found["size"] == 32,
            found,
        )
        artifact.unlink()
        check(
            "lookup: deleted artifact is a miss",
            stagecache.lookup(work, digest) is None,
        )
        artifact.write_bytes(b"x" * 7)
        check(
            "lookup: truncated artifact is a miss",
            stagecache.lookup(work, digest) is None,
        )
        artifact.write_bytes(b"x" * 32)
        check(
            "lookup: restored artifact hits again",
            stagecache.lookup(work, digest) is not None,
        )
        artifact.write_bytes(b"y" * 32)
        check(
            "lookup: same size, different bytes is a miss",
            stagecache.lookup(work, digest) is None,
        )
        artifact.write_bytes(b"x" * 32)
        entry.write_text("{not json", encoding="utf-8")
        check(
            "lookup: corrupt entry is a miss", stagecache.lookup(work, digest) is None
        )
        entry.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
        check("lookup: wrong schema is a miss", stagecache.lookup(work, digest) is None)
        check(
            "record: missing artifact records nothing",
            stagecache.record(work, digest, work / "absent.bin", {}) is None,
        )

    check(
        "dependents: subtitle invalidates 03 and 04",
        stagecache.dependents(render.STAGE_DEPENDS, ["subtitles"])
        == {"03_graph", "04_loudness"},
    )
    check(
        "dependents: 01 invalidates 02, 03 and 04",
        stagecache.dependents(render.STAGE_DEPENDS, ["01_trim"])
        == {"02_detect", "03_graph", "04_loudness"},
    )
    check(
        "dependents: 04 is a leaf",
        stagecache.dependents(render.STAGE_DEPENDS, ["loudness"]) == {"04_loudness"},
    )
    check(
        "dependents: the source invalidates everything",
        stagecache.dependents(render.STAGE_DEPENDS, ["source_sha256"])
        == set(ALL_STAGES),
    )
    check(
        "dependents: every stage is covered by the map",
        set(render.STAGE_DEPENDS) == set(ALL_STAGES),
        sorted(render.STAGE_DEPENDS),
    )


def test_stage_counts(root: Path) -> None:
    source = root / "src.mp4"
    if not _clip(source):
        skip("stage counts", "ffmpeg/libx264/aac unavailable")
        return
    media = _media(source)
    subs = root / "captions.ass"
    _write_ass(subs, "Ciao mondo")
    work = root / "work"

    calls, restore = _count_calls(stagecache, ("lookup", "record"))
    try:
        first = _run(_plan(), media, work, subs)
    finally:
        restore()
    check(
        "flag on: four lookups and four records on the first run",
        calls == {"lookup": 4, "record": 4},
        calls,
    )
    check(
        "run 1: every stage computed",
        first["stage_cache"]["misses"] == 4 and first["stage_cache"]["hits"] == 0,
        first["stage_cache"],
    )
    check(
        "run 1: executed == the four stages",
        first["stage_cache"]["executed"] == ALL_STAGES,
        first["stage_cache"]["executed"],
    )
    first_sha = _sha256(Path(first["path"]))
    report_file = work / "stage_cache_report.json"
    check("run 1: counters written to the workdir", report_file.is_file())

    # (a) second identical run
    second = _run(_plan(), media, work, subs)
    check(
        "(a) second run: 0 stages executed",
        second["stage_cache"]["executed"] == [],
        second["stage_cache"]["executed"],
    )
    check(
        "(a) second run: 4 hits, 0 misses",
        second["stage_cache"]["hits"] == 4 and second["stage_cache"]["misses"] == 0,
        second["stage_cache"],
    )
    check(
        "(a) second run: every stage reused",
        second["stage_cache"]["reused"] == ALL_STAGES,
        second["stage_cache"]["reused"],
    )
    check(
        "(a) second run: the master is the cached artifact",
        _sha256(Path(second["path"])) == first_sha,
    )

    # (b) a subtitle rewritten in place: the graph text does not change, the
    # bytes do, so only the stages that render subtitles may re-execute.
    _write_ass(subs, "Ciao mondo, versione due")
    third = _run(_plan(), media, work, subs)
    expected = {"03_graph", "04_loudness"}
    check(
        "(b) changed subtitle: exactly {03_graph, 04_loudness}",
        set(third["stage_cache"]["executed"]) == expected,
        third["stage_cache"]["executed"],
    )
    check(
        "(b) changed subtitle: 01_trim and 02_detect reused",
        set(third["stage_cache"]["reused"]) == {"01_trim", "02_detect"},
        third["stage_cache"]["reused"],
    )
    check(
        "(b) changed subtitle: the map predicts the same set",
        stagecache.dependents(render.STAGE_DEPENDS, ["subtitles"]) == expected,
    )
    check(
        "(b) changed subtitle: the master changed",
        _sha256(Path(third["path"])) != first_sha,
    )

    # (c) artifact tampering
    stage03 = work / "stage" / "03_video.mp4"
    stage03.write_bytes(b"\x00" * 1024)
    fourth = _run(_plan(), media, work, subs)
    check(
        "(c) truncated stage 03 artifact: 03 re-executes",
        fourth["stage_cache"]["executed"] == ["03_graph"],
        fourth["stage_cache"]["executed"],
    )
    check(
        "(c) truncated stage 03 artifact: 04 still reuses its own artifact",
        "04_loudness" in fourth["stage_cache"]["reused"],
        fourth["stage_cache"]["reused"],
    )
    check("(c) truncated artifact was rewritten", stage03.stat().st_size > 4096)

    final = Path(fourth["path"])
    final.unlink()
    fifth = _run(_plan(), media, work, subs)
    check(
        "(c) missing master: only 04_loudness re-executes",
        fifth["stage_cache"]["executed"] == ["04_loudness"],
        fifth["stage_cache"]["executed"],
    )
    check(
        "(c) missing master was rebuilt", final.is_file() and final.stat().st_size > 0
    )

    # (d) a corrupt entry is a miss, never a crash
    entries = _entries(work)
    check("(d) cache entries on disk", len(entries) >= 4, len(entries))
    for entry in entries:
        entry.write_text("{ truncated", encoding="utf-8")
    sixth = _run(_plan(), media, work, subs)
    check(
        "(d) corrupt cache JSON: ignored, every stage recomputes",
        sixth["stage_cache"]["executed"] == ALL_STAGES,
        sixth["stage_cache"]["executed"],
    )
    check(
        "(d) corrupt cache JSON: the render still produced a master",
        Path(sixth["path"]).is_file(),
    )
    check(
        "(d) corrupt cache JSON: the current entries are valid again",
        all(
            stagecache.lookup(work, digest)
            for digest in sixth["stage_cache"]["keys"].values()
        ),
        sixth["stage_cache"]["keys"],
    )

    # (e) the loudness target is read by stage 04 alone
    seventh = _run(_plan(loudness_i=-16.0), media, work, subs)
    check(
        "(e) loudness target change: only 04_loudness re-executes",
        seventh["stage_cache"]["executed"] == ["04_loudness"],
        seventh["stage_cache"]["executed"],
    )
    check(
        "(e) loudness target change: three stages reused",
        seventh["stage_cache"]["reused"] == ["01_trim", "02_detect", "03_graph"],
        seventh["stage_cache"]["reused"],
    )

    # (e) smoothing is read by 02 (detect pass), 03 (transform chain) and
    # transitively 04; the trim stage never sees it.
    eighth = _run(_plan(loudness_i=-16.0, smoothing=12), media, work, subs)
    expected = {"02_detect", "03_graph", "04_loudness"}
    check(
        "(e) smoothing change: exactly {02_detect, 03_graph, 04_loudness}",
        set(eighth["stage_cache"]["executed"]) == expected,
        eighth["stage_cache"]["executed"],
    )
    check(
        "(e) smoothing change: 01_trim reused",
        eighth["stage_cache"]["reused"] == ["01_trim"],
        eighth["stage_cache"]["reused"],
    )
    check(
        "(e) smoothing change: the map predicts the same set",
        {s for s in stagecache.dependents(render.STAGE_DEPENDS, ["stabilize"])}
        | {"03_graph", "04_loudness"}
        == expected,
    )


def test_edl_path(root: Path) -> None:
    """The pipeline auto.py runs: render_edl caches its two file-producing stages."""
    source = root / "edl.mp4"
    if not source.exists() and not _clip(source):
        skip("render_edl cache", "ffmpeg/libx264/aac unavailable")
        return
    plan = _edl_plan(source)
    check("EDL fixture valida", plan.validate() == [], plan.validate())
    work = root / "edl_work"
    out = root / "edl_master.mp4"

    for label, expected_misses in (("first", 2), ("second", 0)):
        stats: dict = {}
        started = time.time()
        render.render_edl(
            plan,
            {"s": str(source)},
            out,
            workdir=work,
            crf=35,
            preset="ultrafast",
            loudness=(-14.0, -1.0),
            cache_report=stats,
        )
        print(f"        ({time.time() - started:.2f}s)")
        check(
            f"render_edl {label} run: {expected_misses} stage(s) executed",
            stats.get("misses") == expected_misses,
            stats,
        )
    check(
        "render_edl second run: every stage reused",
        stats.get("reused") == ["03_graph", "04_loudness"],
        stats.get("reused"),
    )
    check("render_edl: master written", out.is_file() and out.stat().st_size > 0)
    check(
        "render_edl: counters in edl_render.json",
        (work / "final_pre_loudness.mp4").is_file()
        and (work / "stage_cache_report.json").is_file(),
    )


def main() -> int:
    started = time.time()
    test_units()
    with tempfile.TemporaryDirectory(prefix="vx-stage-cache-") as raw:
        root = Path(raw)
        test_flag_off(root)
        settings.apply(settings.build(overrides=["features.stage_cache=on"]))
        check(
            "flag on: the effective value is on",
            features.enabled("stage_cache") is True,
        )
        try:
            test_stage_counts(root)
            test_edl_path(root)
        finally:
            settings.reset()
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  ({time.time() - started:.1f}s)")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
