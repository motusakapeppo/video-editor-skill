"""F2-T03 acceptance: the EDL 2.2 -> vx.edl3/1 migrator is measured on pixels.

A two-clip 2.2 document is built from two synthetic sources, rendered through
the existing 2.2 pipeline (:func:`vx.render.render_edl`) and through the
migrated ``vx.edl3/1`` document (:func:`vx.edl3_render.render`), with the same
encoder settings. Frames at three timestamps are compared with
``golden.capture`` / ``golden._pixel_diff``: same frame count and resolution,
and per-channel mean absolute difference <= 2.0.

    python tests/test_edl3_migrate.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402
from vx import edl3_migrate  # noqa: E402
from vx import edl3_render  # noqa: E402
from vx import edl_plan  # noqa: E402
from vx import golden  # noqa: E402
from vx import render  # noqa: E402
from vx.ffmpeg import run_ff, run_fp  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FPS = 12
SIZE = (320, 568)
DURATION = 2.0
TIMES = [0.25, 1.0, 1.75]
MAD_TOLERANCE = 2.0


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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frame_count(path: Path) -> int:
    proc = run_fp(
        [
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "json",
            str(path),
        ]
    )
    try:
        streams = json.loads(proc.stdout or "{}")["streams"]
        return int(streams[0]["nb_read_frames"])
    except (KeyError, IndexError, ValueError, TypeError):
        return -1


def build_edl2(source_a: Path, source_b: Path, music: Path) -> dict:
    return {
        "contract_version": "2.2.0",
        "timeline": {
            "fps": float(FPS),
            "duration_sec": DURATION,
            "aspect_ratio": "9:16",
            "resolution": [SIZE[0], SIZE[1]],
        },
        "sources": {
            "a": {"path": str(source_a), "sha256": sha256(source_a), "camera": {}},
            "b": {"path": str(source_b), "sha256": sha256(source_b), "camera": {}},
            "bed": {"path": str(music), "sha256": sha256(music), "camera": {}},
        },
        "tracks": {
            "video": [
                {
                    "track_id": 0,
                    "role": "main",
                    "clips": [
                        {
                            "clip_id": "c1",
                            "source_id": "a",
                            "src_in": 0.0,
                            "src_out": 1.0,
                            "dst_in": 0.0,
                            "dst_out": 1.0,
                            "layer": 0,
                            "role": "main_talking_head",
                        },
                        {
                            "clip_id": "c2",
                            "source_id": "b",
                            "src_in": 0.5,
                            "src_out": 1.5,
                            "dst_in": 1.0,
                            "dst_out": 2.0,
                            "layer": 0,
                            "role": "main_talking_head",
                        },
                    ],
                }
            ],
            "audio": [
                {
                    "track_id": 0,
                    "role": "dialogue",
                    "source_id": "a",
                    "src_in": 0.0,
                    "src_out": DURATION,
                    "dst_in": 0.0,
                    "dst_out": DURATION,
                },
                {
                    "track_id": 1,
                    "role": "music",
                    "clips": [
                        {
                            "clip_id": "m1",
                            "source_id": "bed",
                            "src_in": 0.0,
                            "src_out": DURATION,
                            "dst_in": 0.0,
                            "dst_out": DURATION,
                            "gain_db": -6.0,
                        }
                    ],
                },
            ],
        },
        "ducking": {
            "sidechain_role": "dialogue",
            "target_role": "music",
            "threshold": 0.05,
            "ratio": 8.0,
            "attack_ms": 20.0,
            "release_ms": 250.0,
            "makeup_db": 0.0,
        },
    }


def music_gain(document: dict) -> float:
    for track in document.get("audio") or []:
        if track.get("role") == "music":
            clips = track.get("clips") or []
            if clips:
                return float(clips[0].get("gain_db", 0.0))
    return 0.0


def plan_music_gain(plan: edl_plan.EDLPlan) -> float:
    for track in plan.audio_tracks:
        if track.role == "music" and track.clips:
            return float(track.clips[0].gain_db)
    return 0.0


def edl2_clips(document: dict) -> list:
    clips = []
    for track in document["tracks"]["video"]:
        clips.extend(track.get("clips") or [])
    return sorted(clips, key=lambda clip: (float(clip["dst_in"]), clip["clip_id"]))


# --------------------------------------------------------------------- main


def main() -> int:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vx_edl3_migrate_") as tmp:
        work = Path(tmp)
        source_a = work / "a.mp4"
        source_b = work / "b.mp4"
        music = work / "music.mp4"
        problems = [
            make_source(source_a, f"testsrc2=s={SIZE[0]}x{SIZE[1]}:r={FPS}:d=3"),
            make_source(source_b, f"smptehdbars=s={SIZE[0]}x{SIZE[1]}:r={FPS}:d=3"),
            make_source(music, f"color=c=0x203040:s={SIZE[0]}x{SIZE[1]}:r={FPS}:d=3"),
        ]
        problems = [problem for problem in problems if problem]
        if problems:
            skip("synthetic sources", problems[0])
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 0

        try:
            run_checks(work, source_a, source_b, music)
        except Exception:  # noqa: BLE001 - a crash is a failing test, never a skip
            traceback.print_exc()
            check("test harness completed without an unexpected exception", False)

    elapsed = time.monotonic() - started
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP  ({elapsed:.1f}s)")
    check("suite stays under the 120 s ceiling", elapsed < 120.0, f"{elapsed:.1f}s")
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


def run_checks(work: Path, source_a: Path, source_b: Path, music: Path) -> None:
    edl2 = build_edl2(source_a, source_b, music)
    plan = edl_plan.EDLPlan.from_dict(edl2)
    check("the 2.2 fixture is a valid document", plan.validate() == [], plan.validate())

    migrated = edl3_migrate.migrate(edl2)
    print(f"\n  migrated document: {json.dumps(migrated, indent=2)[:800]}\n")
    check(
        "migrate returns a vx.edl3/1 document",
        migrated["version"] == edl3.EDL3_VERSION,
        migrated["version"],
    )
    timeline = edl3.Timeline3.from_dict(migrated)
    check(
        "the migrated document validates cleanly",
        timeline.validate() == [],
        timeline.validate(),
    )
    check(
        "migrate adds no effect",
        all(not clip.effects for clip in timeline.clips),
        [len(clip.effects) for clip in timeline.clips],
    )

    source_clips = edl2_clips(edl2)
    check(
        "clip count is unchanged",
        len(source_clips) == len(migrated["clips"]),
        f"{len(source_clips)} -> {len(migrated['clips'])}",
    )
    check(
        "clip order, src and dst times are identical",
        all(
            (
                left["clip_id"],
                float(left["src_in"]),
                float(left["src_out"]),
                float(left["dst_in"]),
                float(left["dst_out"]),
            )
            == (
                right["id"],
                right["src_in"],
                right["src_out"],
                right["dst_in"],
                right["dst_out"],
            )
            for left, right in zip(source_clips, migrated["clips"])
        ),
        [
            (right["id"], right["src_in"], right["dst_in"])
            for right in migrated["clips"]
        ],
    )
    check(
        "music gain is identical",
        plan_music_gain(plan) == music_gain(migrated),
        f"{plan_music_gain(plan)} dB -> {music_gain(migrated)} dB",
    )
    check(
        "ducking is carried field for field",
        (migrated.get("ducking") or {}) == (edl2.get("ducking") or {}),
        migrated.get("ducking"),
    )

    legacy = work / "legacy.mp4"
    render.render_edl(
        plan,
        {"a": str(source_a), "b": str(source_b), "bed": str(music)},
        legacy,
        workdir=work / "legacy_work",
        has_audio=False,
        crf=18,
        preset="ultrafast",
    )
    migrated_out = work / "migrated.mp4"
    report = edl3_render.render(
        timeline, migrated_out, workdir=work / "migrated_work", audio="drop"
    )
    check(
        "both masters exist",
        legacy.exists() and migrated_out.exists(),
        f"{legacy.stat().st_size} / {migrated_out.stat().st_size} bytes",
    )
    check(
        "the migrated render states the audio policy",
        report["audio_policy"] == "drop",
        report["audio_policy"],
    )

    legacy_frames = golden.capture(legacy, TIMES, work / "frames_legacy")
    migrated_frames = golden.capture(migrated_out, TIMES, work / "frames_migrated")

    check(
        "frame count is identical",
        frame_count(legacy) == frame_count(migrated_out),
        f"{frame_count(legacy)} vs {frame_count(migrated_out)}",
    )
    check(
        "resolution is identical",
        legacy_frames["size"] == migrated_frames["size"] == list(SIZE),
        f"{legacy_frames['size']} vs {migrated_frames['size']}",
    )
    check(
        "one compared frame per timestamp",
        len(legacy_frames["frames"]) == len(migrated_frames["frames"]) == len(TIMES),
        len(legacy_frames["frames"]),
    )

    identical = 0
    within = 0
    measured = []
    for left, right in zip(legacy_frames["frames"], migrated_frames["frames"]):
        if left["sha256"] == right["sha256"]:
            identical += 1
            measured.append((left["time"], 0.0, 0.0, 0.0, 0.0, "hash"))
            continue
        difference = golden._pixel_diff(Path(left["path"]), Path(right["path"]))
        measured.append(
            (
                left["time"],
                difference["mad"],
                difference["mad_r"],
                difference["mad_g"],
                difference["mad_b"],
                difference["method"],
            )
        )
        if (
            difference["mad_r"] <= MAD_TOLERANCE
            and difference["mad_g"] <= MAD_TOLERANCE
            and difference["mad_b"] <= MAD_TOLERANCE
        ):
            within += 1

    print("\n  measured 2.2 vs migrated (mean absolute difference, 0-255):")
    for time_value, mad, mad_r, mad_g, mad_b, method in measured:
        print(
            f"    t={time_value:.3f}s  MAD={mad:8.4f}  "
            f"r={mad_r:8.4f} g={mad_g:8.4f} b={mad_b:8.4f}  ({method})"
        )
    check(
        "every compared frame is within 2.0 MAD per channel",
        within + identical == len(measured),
        f"{within + identical}/{len(measured)} frames",
    )
    check(
        "identical frame hashes",
        identical == len(measured),
        f"{identical}/{len(measured)} frames byte-identical",
    )
    check(
        "golden.compare with the 2.0 tolerance reports no difference",
        golden.compare(legacy_frames, migrated_frames, pixel_tolerance=2) == [],
        len(golden.compare(legacy_frames, migrated_frames, pixel_tolerance=2)),
    )


if __name__ == "__main__":
    raise SystemExit(main())
