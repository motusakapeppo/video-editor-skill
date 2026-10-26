"""Phase 2.2: chunked, resumable analysis engine.

Run:
    python tests/test_chunk_checkpoints.py

Script-style on purpose: the suite is a set of standalone programs, not pytest.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import checkpoint, subs, vad  # noqa: E402
from vx.analyze import (  # noqa: E402
    ANALYSIS_CHUNK_OVERLAP_SEC,
    ANALYSIS_CHUNK_SEC,
    Chunk,
    analyze_chunked,
    merge_transcripts,
    merge_vad,
    plan_chunks,
    run_chunk_analysis,
    should_chunk,
)
from vx.contract import Transcript  # noqa: E402
from vx.vad import VadResult  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:64} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"SKIP {label:64} {reason}")


def ffmpeg(args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
    )


def ident(source: str = "a" * 64, analysis: str = "b" * 64, **overrides) -> dict:
    base = {
        "chunk_sec": 300.0,
        "model": "small",
        "language": "it",
        "transcribe": True,
        "vad_config": {},
    }
    base.update(overrides)
    return checkpoint.identity(source, analysis, **base)


def payload(index: int, word: str = "hello") -> dict:
    return {
        "transcript": Transcript(
            words=[
                {
                    "start": 1.5,
                    "end": 2.0,
                    "word": f"{word}{index}",
                    "probability": 0.9,
                }
            ],
            segments=[
                {
                    "start": 1.5,
                    "end": 2.0,
                    "text": f"{word}{index}",
                    "avg_logprob": -0.1,
                    "no_speech_prob": 0.01,
                }
            ],
            language="it",
            duration=5.0,
        ).to_dict(),
        "vad": VadResult(
            engine="silero-v5",
            available=True,
            regions=[{"start": 1.0, "end": 2.0}],
            speech_ratio=0.2,
            speech_dbfs=-20.0,
            noise_dbfs=-50.0,
            snr_db=30.0,
            windows=10,
            threshold=0.5,
            duration=5.0,
        ).to_dict(),
    }


def test_plan_chunks() -> None:
    chunks = plan_chunks(2700)
    check("plan_chunks(2700): 9 chunks", len(chunks) == 9, len(chunks))
    check(
        "plan_chunks(2700): own starts",
        [c.own_start for c in chunks] == [float(i * 300) for i in range(9)],
        [c.own_start for c in chunks],
    )
    check(
        "plan_chunks(2700): own ends",
        [c.own_end for c in chunks] == [float((i + 1) * 300) for i in range(9)],
        [c.own_end for c in chunks],
    )
    check(
        "plan_chunks(2700): decode starts",
        [c.decode_start for c in chunks]
        == [0.0] + [float(i * 300 - 1) for i in range(1, 9)],
        [c.decode_start for c in chunks],
    )
    check(
        "plan_chunks(2700): decode ends",
        [c.decode_end for c in chunks]
        == [float(i * 300 + 1) for i in range(1, 9)] + [2700.0],
        [c.decode_end for c in chunks],
    )

    one = plan_chunks(45)
    check(
        "plan_chunks(45): one chunk",
        len(one) == 1 and one[0].own_start == 0.0 and one[0].own_end == 45.0,
        one,
    )
    check("plan_chunks(0): empty", plan_chunks(0) == [])
    check("plan_chunks(300): one chunk", len(plan_chunks(300)) == 1)

    tail = plan_chunks(300.5)
    check("plan_chunks(300.5): two chunks", len(tail) == 2, tail)
    check(
        "plan_chunks(300.5): second own [300, 300.5]",
        tail[1].own_start == 300.0 and tail[1].own_end == 300.5,
        tail[1],
    )
    check(
        "plan_chunks(300.5): second decode [299, 300.5]",
        tail[1].decode_start == 299.0 and tail[1].decode_end == 300.5,
        tail[1],
    )

    check("should_chunk(301, True)", should_chunk(301, True) is True)
    check("should_chunk(300, True)", should_chunk(300, True) is False)
    check("should_chunk(301, False)", should_chunk(301, False) is False)
    check(
        "constants",
        ANALYSIS_CHUNK_SEC == 300.0 and ANALYSIS_CHUNK_OVERLAP_SEC == 1.0,
    )


def test_merge_transcripts() -> None:
    c0 = Chunk(0, 0.0, 300.0, 0.0, 301.0)
    c1 = Chunk(1, 300.0, 600.0, 299.0, 601.0)
    t0 = Transcript(
        words=[{"start": 299.8, "end": 301.1, "word": "seam", "probability": 0.9}],
        segments=[
            {
                "start": 299.5,
                "end": 301.5,
                "text": "seam segment",
                "avg_logprob": -0.2,
                "no_speech_prob": 0.1,
            }
        ],
        language="it",
    )
    t1 = Transcript(
        words=[{"start": 0.8, "end": 2.1, "word": "seam", "probability": 0.8}],
        segments=[
            {
                "start": 0.5,
                "end": 2.5,
                "text": "seam segment",
                "avg_logprob": -0.2,
                "no_speech_prob": 0.1,
            }
        ],
        language="it",
    )
    merged = merge_transcripts(
        [
            {"chunk": c0, "transcript": t0, "error": ""},
            {"chunk": c1, "transcript": t1, "error": ""},
        ],
        duration=600.0,
    )
    check(
        "merge_transcripts: seam word exactly once",
        len(merged.words) == 1 and merged.words[0]["word"] == "seam",
        merged.words,
    )
    check(
        "merge_transcripts: duration is global",
        merged.duration == 600.0,
        merged.duration,
    )
    check(
        "merge_transcripts: wpm coherent",
        merged.wpm == round(1 / (600.0 / 60.0), 1),
        merged.wpm,
    )
    check(
        "merge_transcripts: text contains seam once",
        merged.text.count("seam") == 1,
        merged.text,
    )
    check(
        "merge_transcripts: segment kept once",
        len(merged.segments) == 1,
        merged.segments,
    )
    check(
        "merge_transcripts: segment metrics preserved",
        merged.segments[0]["avg_logprob"] == -0.2
        and merged.segments[0]["no_speech_prob"] == 0.1,
        merged.segments[0],
    )

    clamp_chunk = Chunk(0, 0.0, 12.0, 0.0, 12.0)
    clamp_tr = Transcript(
        words=[{"start": 11.0, "end": 13.0, "word": "tail"}], language="it"
    )
    clamped = merge_transcripts(
        [{"chunk": clamp_chunk, "transcript": clamp_tr, "error": ""}],
        duration=12.0,
    )
    check(
        "merge_transcripts: word clamped to duration",
        len(clamped.words) == 1 and clamped.words[0]["end"] == 12.0,
        clamped.words,
    )

    skipped = merge_transcripts(
        [
            {"chunk": c0, "transcript": None, "error": "boom"},
            {"chunk": c1, "transcript": None, "error": "bang"},
        ],
        duration=600.0,
    )
    check(
        "merge_transcripts: all skipped -> reason",
        bool(skipped.skipped_reason)
        and "boom" in skipped.skipped_reason
        and "bang" in skipped.skipped_reason,
        skipped.skipped_reason,
    )

    cue_tr = Transcript(
        words=[
            {"start": 10.0, "end": 10.5, "word": "a"},
            {"start": 10.6, "end": 11.0, "word": "b"},
            {"start": 11.1, "end": 13.0, "word": "c"},
        ],
        language="it",
    )
    cue_merged = merge_transcripts(
        [{"chunk": clamp_chunk, "transcript": cue_tr, "error": ""}],
        duration=12.0,
    )
    cues = subs.build_cues(cue_merged)
    check(
        "merge_transcripts: cues never end past duration",
        bool(cues) and all(cue.end <= 12.0 + 1e-6 for cue in cues),
        [(cue.start, cue.end) for cue in cues],
    )


def test_merge_vad() -> None:
    c0 = Chunk(0, 0.0, 300.0, 0.0, 301.0)
    c1 = Chunk(1, 300.0, 600.0, 300.0, 600.0)
    v0 = VadResult(
        engine="silero-v5",
        available=True,
        regions=[{"start": 299.0, "end": 300.0}],
        speech_dbfs=-20.0,
        noise_dbfs=-50.0,
        windows=10,
        duration=301.0,
    )
    v1 = VadResult(
        engine="silero-v5",
        available=True,
        regions=[{"start": 0.0, "end": 0.5}],
        speech_dbfs=-20.0,
        noise_dbfs=-50.0,
        windows=10,
        duration=302.0,
    )
    merged = merge_vad(
        [
            {"chunk": c0, "vad": v0, "error": ""},
            {"chunk": c1, "vad": v1, "error": ""},
        ],
        duration=600.0,
    )
    check(
        "merge_vad: seam regions merge",
        merged.regions == [{"start": 299.0, "end": 300.5}],
        merged.regions,
    )
    check(
        "merge_vad: all available -> silero-v5",
        merged.available is True and merged.engine == "silero-v5",
        (merged.available, merged.engine),
    )

    unavailable = VadResult(engine="none", available=False, reason="no model")
    broken = merge_vad(
        [
            {"chunk": c0, "vad": v0, "error": ""},
            {"chunk": c1, "vad": unavailable, "error": ""},
        ],
        duration=600.0,
    )
    check(
        "merge_vad: one unavailable -> available False",
        broken.available is False and bool(broken.reason),
        (broken.available, broken.reason),
    )

    a = Chunk(0, 0.0, 1.0, 0.0, 1.0)
    b = Chunk(1, 1.0, 2.0, 1.0, 2.0)
    va = VadResult(
        engine="silero-v5",
        available=True,
        regions=[],
        speech_dbfs=-20.0,
        noise_dbfs=-50.0,
        windows=1,
        duration=1.0,
    )
    vb = VadResult(
        engine="silero-v5",
        available=True,
        regions=[],
        speech_dbfs=-40.0,
        noise_dbfs=-50.0,
        windows=1,
        duration=1.0,
    )
    weighted = merge_vad(
        [
            {"chunk": a, "vad": va, "error": ""},
            {"chunk": b, "vad": vb, "error": ""},
        ],
        duration=2.0,
    )
    check(
        "merge_vad: energy-weighted dBFS ~ -23.01",
        abs(weighted.speech_dbfs - (-23.01)) < 0.1,
        weighted.speech_dbfs,
    )
    check(
        "merge_vad: not the arithmetic mean -30",
        abs(weighted.speech_dbfs - (-30.0)) > 1.0,
        weighted.speech_dbfs,
    )

    clamp_chunk = Chunk(0, 0.0, 12.0, 0.0, 12.0)
    clamp_vad = VadResult(
        engine="silero-v5",
        available=True,
        regions=[{"start": 11.0, "end": 13.0}],
        speech_dbfs=-20.0,
        noise_dbfs=-50.0,
        windows=1,
        duration=12.0,
    )
    clamped = merge_vad(
        [{"chunk": clamp_chunk, "vad": clamp_vad, "error": ""}], duration=12.0
    )
    check(
        "merge_vad: region end clamped to 12.0",
        clamped.regions == [{"start": 11.0, "end": 12.0}],
        clamped.regions,
    )


def test_store() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        i1 = ident(source="a" * 64)
        i2 = ident(source="c" * 64)
        i3 = ident(source="a" * 64, model="medium")
        s1 = checkpoint.Store(tmp, i1)
        s2 = checkpoint.Store(tmp, i2)
        check(
            "Store: different source -> different dir",
            s1.path != s2.path,
            (s1.path.name, s2.path.name),
        )
        check(
            "Store: different config -> different dir",
            checkpoint.dir_name(i1) != checkpoint.dir_name(i3),
            (checkpoint.dir_name(i1), checkpoint.dir_name(i3)),
        )

        body = {"transcript": {"words": []}, "vad": {"available": True}}
        s1.write_chunk(0, body)
        check("Store: chunk round-trip", s1.load_chunk(0) == body)
        check(
            "Store: no temp file after write",
            not list(s1.path.glob("*.tmp")),
            list(s1.path.glob("*.tmp")),
        )

        original_replace = checkpoint.os.replace

        def failing_replace(src, dst):
            raise OSError("replace failed")

        checkpoint.os.replace = failing_replace
        raised = False
        try:
            s1.write_chunk(0, {"transcript": {"words": [{"word": "new"}]}})
        except OSError:
            raised = True
        finally:
            checkpoint.os.replace = original_replace
        check("Store: replace failure raises", raised)
        check("Store: previous chunk intact", s1.load_chunk(0) == body)
        check(
            "Store: no temp file after failure",
            not list(s1.path.glob("*.tmp")),
            list(s1.path.glob("*.tmp")),
        )

        s1.chunk_path(0).write_text("{", encoding="utf-8")
        check("Store: corrupt chunk -> None", s1.load_chunk(0) is None)

        s1.write_chunk(0, body)
        raw = json.loads(s1.chunk_path(0).read_text(encoding="utf-8"))
        raw["identity"]["model"] = "other"
        s1.chunk_path(0).write_text(json.dumps(raw), encoding="utf-8")
        check(
            "Store: chunk header identity mismatch -> None",
            s1.load_chunk(0) is None,
        )

        s1.write_manifest(1, 300.0)
        check("Store: manifest round-trip", s1.load_manifest() is not None)

        manifest = s1.load_manifest()
        manifest["schema"] = "wrong"
        s1.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        check("Store: wrong schema -> None", s1.load_manifest() is None)

        s1.write_manifest(1, 300.0)
        manifest = s1.load_manifest()
        manifest["identity"]["model"] = "other"
        s1.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        check("Store: wrong identity -> None", s1.load_manifest() is None)

        s1.write_manifest(1, 300.0)
        manifest = s1.load_manifest()
        manifest["completed"] = [99]
        s1.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        check(
            "Store: completed out of range -> None",
            s1.load_manifest() is None,
        )

        s1.write_manifest(1, 300.0)
        check(
            "Store: find_previous same cfg, different sha",
            s2.find_previous() == str(s1.path),
            s2.find_previous(),
        )


def test_resume_counters() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = checkpoint.Store(tmp, ident())
        for index in range(8):
            store.write_chunk(index, payload(index))
        store.write_manifest(9, 300.0)

        calls: list = []

        def loader(chunk):
            calls.append(chunk.index)
            return payload(chunk.index)

        result = run_chunk_analysis(2700.0, store=store, loader=loader)
        stats = result["stats"]
        check(
            "resume: 8 cached / 1 computed",
            stats["chunks_cached"] == 8 and stats["chunks_computed"] == 1,
            stats,
        )
        check("resume: only chunk 8 loaded", calls == [8], calls)
        check("resume: 9 chunks total", stats["chunks_total"] == 9, stats)
        check("resume: no failures", stats["chunk_failures"] == 0, stats)
        check("resume: no manifest rebuild", stats["manifest_rebuilds"] == 0, stats)

    with tempfile.TemporaryDirectory() as tmp:
        store = checkpoint.Store(tmp, ident())
        for index in range(8):
            store.write_chunk(index, payload(index))
        store.write_manifest(8, 300.0)
        store.chunk_path(4).write_text("{", encoding="utf-8")

        calls = []

        def loader(chunk):
            calls.append(chunk.index)
            return payload(chunk.index)

        result = run_chunk_analysis(2400.0, store=store, loader=loader)
        stats = result["stats"]
        check(
            "corrupt chunk 4: 7 cached / 1 computed",
            stats["chunks_cached"] == 7 and stats["chunks_computed"] == 1,
            stats,
        )
        check("corrupt chunk 4: only chunk 4 loaded", calls == [4], calls)

    with tempfile.TemporaryDirectory() as tmp:
        store = checkpoint.Store(tmp, ident())
        for index in range(3):
            store.write_chunk(index, payload(index))
        store.write_manifest(3, 300.0)
        store.invalidate()

        calls = []

        def loader(chunk):
            calls.append(chunk.index)
            return payload(chunk.index)

        result = run_chunk_analysis(900.0, store=store, loader=loader)
        stats = result["stats"]
        check(
            "invalidate: 3 computed / 0 cached",
            stats["chunks_computed"] == 3 and stats["chunks_cached"] == 0,
            stats,
        )
        check(
            "invalidate: manifest rebuilt once",
            stats["manifest_rebuilds"] == 1,
            stats,
        )

    with tempfile.TemporaryDirectory() as tmp:
        store = checkpoint.Store(tmp, ident())
        calls = []

        def bad_loader(chunk):
            calls.append(chunk.index)
            if chunk.index == 1:
                raise RuntimeError("boom")
            return payload(chunk.index)

        result = run_chunk_analysis(900.0, store=store, loader=bad_loader)
        stats = result["stats"]
        check("loader raises: one failure", stats["chunk_failures"] == 1, stats)
        check("loader raises: two computed", stats["chunks_computed"] == 2, stats)
        check("loader raises: chunk 1 not written", store.load_chunk(1) is None)

        calls = []

        def good_loader(chunk):
            calls.append(chunk.index)
            return payload(chunk.index)

        retry = run_chunk_analysis(900.0, store=store, loader=good_loader)
        retry_stats = retry["stats"]
        check(
            "retry: only chunk 1 computed",
            retry_stats["chunks_computed"] == 1 and retry_stats["chunks_cached"] == 2,
            retry_stats,
        )
        check("retry: only chunk 1 loaded", calls == [1], calls)

    with tempfile.TemporaryDirectory() as tmp:
        previous = checkpoint.Store(tmp, ident(source="d" * 64))
        previous.write_manifest(9, 300.0)
        store = checkpoint.Store(tmp, ident(source="a" * 64))

        def loader(chunk):
            return payload(chunk.index)

        result = run_chunk_analysis(2700.0, store=store, loader=loader)
        check(
            "stale source: flagged",
            result["stats"]["stale_source"] is True,
            result["stats"],
        )


def test_integration() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        clip = base / "clip.mp4"
        proc = ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=25:duration=12",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=16000:duration=12",
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
                "64k",
                str(clip),
            ]
        )
        if proc.returncode != 0:
            skip("integration: 12 s clip", "ffmpeg/libx264/aac unavailable")
            return

        def stub_transcribe(wav, *, model, language):
            return Transcript(
                words=[
                    {
                        "start": 1.5,
                        "end": 2.0,
                        "word": "hello",
                        "probability": 0.9,
                    }
                ],
                segments=[
                    {
                        "start": 1.5,
                        "end": 2.0,
                        "text": "hello",
                        "avg_logprob": -0.1,
                        "no_speech_prob": 0.01,
                    }
                ],
                language="it",
                duration=5.0,
            )

        def stub_vad(wav, **kwargs):
            return VadResult(
                engine="silero-v5",
                available=True,
                regions=[{"start": 1.0, "end": 2.0}],
                speech_ratio=0.2,
                speech_dbfs=-20.0,
                noise_dbfs=-50.0,
                snr_db=30.0,
                windows=10,
                threshold=0.5,
                duration=5.0,
            )

        work = base / "work"
        first = analyze_chunked(
            clip,
            workdir=work,
            source_sha256="a" * 64,
            analysis_sha256="b" * 64,
            chunk_sec=4.0,
            model="tiny",
            language="it",
            vad_config={},
            transcribe=True,
            transcribe_fn=stub_transcribe,
            vad_fn=stub_vad,
        )
        stats = first["stats"]
        check(
            "integration: first run computes 3 chunks",
            stats["chunks_total"] == 3 and stats["chunks_computed"] == 3,
            stats,
        )
        check(
            "integration: 3 chunk files on disk",
            len(list(Path(stats["checkpoint_dir"]).glob("analysis_chunk_*.json"))) == 3,
            stats["checkpoint_dir"],
        )

        second = analyze_chunked(
            clip,
            workdir=work,
            source_sha256="a" * 64,
            analysis_sha256="b" * 64,
            chunk_sec=4.0,
            model="tiny",
            language="it",
            vad_config={},
            transcribe=True,
            transcribe_fn=stub_transcribe,
            vad_fn=stub_vad,
        )
        check(
            "integration: rerun computes 0 chunks",
            second["stats"]["chunks_computed"] == 0
            and second["stats"]["chunks_cached"] == 3,
            second["stats"],
        )
        check(
            "integration: rerun transcript equal",
            first["transcript"].to_dict() == second["transcript"].to_dict(),
        )

        try:
            import faster_whisper  # noqa: F401

            asr_available = True
        except Exception:
            asr_available = False
        if not asr_available or not vad.available():
            skip(
                "integration: real ASR/VAD",
                "faster-whisper or silero unavailable",
            )
            return

        real_work = base / "real"
        real_first = analyze_chunked(
            clip,
            workdir=real_work,
            source_sha256="a" * 64,
            analysis_sha256="b" * 64,
            chunk_sec=4.0,
            model="small",
            language="it",
            vad_config={},
            transcribe=True,
        )
        check(
            "integration: real ASR computes 3 chunks",
            real_first["stats"]["chunks_computed"] == 3,
            real_first["stats"],
        )
        real_second = analyze_chunked(
            clip,
            workdir=real_work,
            source_sha256="a" * 64,
            analysis_sha256="b" * 64,
            chunk_sec=4.0,
            model="small",
            language="it",
            vad_config={},
            transcribe=True,
        )
        check(
            "integration: real ASR rerun computes 0",
            real_second["stats"]["chunks_computed"] == 0
            and real_second["stats"]["chunks_cached"] == 3,
            real_second["stats"],
        )
        check(
            "integration: real ASR transcript equal",
            real_first["transcript"].to_dict() == real_second["transcript"].to_dict(),
        )


def main() -> int:
    test_plan_chunks()
    test_merge_transcripts()
    test_merge_vad()
    test_store()
    test_resume_counters()
    test_integration()
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
