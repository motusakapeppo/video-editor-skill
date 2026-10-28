"""F26-T07 -- every profile declares deliverables, and every one is proven.

    python tests/test_deliver.py

A matrix: for each of the nine profiles and each declared target,
:func:`vx.deliver.deliver` either produces a file that ffprobe confirms carries
what the target names, or refuses with ``DELIVERY_ENCODER_MISSING`` naming the
encoder. The summary counts produced against declared, so a missing one can
never look like a pass. The encodes are real, from one 2 s 320x180 master built
once; the same master is also encoded 20 dB quieter to prove the reported
loudness is measured on the output and not copied from the request.
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

from vx import deliver, deliver_probe, edl3, ffmpeg, profiles, qc  # noqa: E402
from vx.edl3 import EDLError  # noqa: E402

PASS = 0
FAIL = 0
SECONDS = 2.0
CANVAS = "320x180"
QUIET_DB = -20.0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:66} {detail}")


def build_master(work: Path, *, gain_db: float = 0.0, name: str = "master.mp4") -> Path:
    work.mkdir(parents=True, exist_ok=True)
    out = work / name
    tail = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p"]
    if gain_db:
        tail += ["-af", f"volume={gain_db:.1f}dB"]
    tail += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest"]
    proc = ffmpeg.run_ff(
        [
            "-f", "lavfi", "-i", f"testsrc2=size={CANVAS}:rate=30:duration={SECONDS:g}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={SECONDS:g}",
            *tail, str(out),
        ],
        step="deliver",
    )
    if not proc.ok:
        raise RuntimeError(f"could not build the master: {proc.tail(10)}")
    return out


def caption_document(chapters: bool) -> Any:
    """A document with three captions, two scene boundaries if asked."""
    data: Dict[str, Any] = {
        "version": edl3.EDL3_VERSION,
        "fps": 30,
        "size": [320, 180],
        "clips": [
            {"id": "c1", "source": "missing.mp4", "src_in": 0.0, "src_out": 1.0, "dst_in": 0.0},
            {"id": "c2", "source": "missing.mp4", "src_in": 1.0, "src_out": 2.0, "dst_in": 1.0},
        ],
        "captions": [
            {
                "clips": [
                    {"text": "uno", "in": 0.0, "out": 0.5},
                    {"text": "due", "in": 0.6, "out": 1.2},
                    {"text": "tre", "in": 1.3, "out": 1.9},
                ]
            }
        ],
    }
    if chapters:
        data["x-chapters"] = "scenes"
    return edl3.Timeline3.from_dict(data)


def refusal(call) -> str:
    try:
        call()
    except EDLError as exc:
        return exc.code
    return "produced"


def run(work: Path) -> None:
    started = time.monotonic()

    print("\na. what this ffmpeg build can actually encode")
    present = deliver.encoders()
    for name in deliver.PROBED_ENCODERS:
        print(f"    {name:<12} {'present' if name in present else 'ABSENT'}")
    check("a1. the build reports its encoders", bool(present), f"{len(present)} names")
    recipes = sorted({recipe.encoder for recipe in deliver.TARGETS.values()})
    check(
        "a2. every recipe's video encoder exists in this build",
        all(deliver.encoder_available(name) for name in recipes),
        ", ".join(f"{name}={'yes' if deliver.encoder_available(name) else 'NO'}" for name in recipes),
    )
    check(
        "a3. no profile declares a target that has no recipe",
        all(key in deliver.TARGETS for row in profiles.PROFILES.values() for key in deliver.targets(row)),
        f"{len(deliver.TARGETS)} recipes",
    )
    check(
        "a4. targets() returns exactly the profile's declaration",
        all(deliver.targets(row) == tuple(row.deliverables) for row in profiles.PROFILES.values()),
    )

    print("\nb. every declared target of every profile: produced, or refused by name")
    print(f"    {'profile':<12} {'target':<18} {'result':<10} {'frames':>6} {'loudness':>9}  detail")
    master = build_master(work / "media")
    source = deliver_probe.probe(master)
    check(
        "b0. the master is 2.0s of video with one audio stream",
        source["audio_streams"] == 1 and abs(source["duration"] - SECONDS) < 0.01,
        f"{source['width']}x{source['height']} {source['duration']}s a={source['audio_streams']}",
    )

    reports: Dict[Tuple[str, str], Dict[str, Any]] = {}
    declared = produced = missing_count = 0
    for row in profiles.PROFILES.values():
        row_produced = row_missing = 0
        for key in deliver.targets(row):
            declared += 1
            recipe = deliver.TARGETS[key]
            target_dir = work / "out" / row.id
            try:
                report = deliver.deliver(master, row, key, target_dir, work / "tmp" / row.id)
            except EDLError as exc:
                row_missing += 1
                missing_count += 1
                check(
                    f"{row.id}/{key} refused only as DELIVERY_ENCODER_MISSING",
                    exc.code == "DELIVERY_ENCODER_MISSING" and recipe.encoder in str(exc),
                    f"{exc.code} ({exc})",
                )
                print(f"    {row.id:<12} {key:<18} {'MISSING':<10} {'-':>6} {'-':>9}  needs {recipe.encoder}")
                continue
            row_produced += 1
            produced += 1
            reports[(row.id, key)] = report
            expected_frames = int(round(SECONDS * float(row.fps)))
            good = (
                report["video_codec"] == recipe.codec
                and report["pix_fmt"] == recipe.pix_fmt
                and report["container_family"] == recipe.family
                and (report["width"], report["height"]) == (row.width, row.height)
                and abs(report["frames"] - expected_frames) <= 1
                and abs(report["duration"] - SECONDS) <= 0.1
                and report["audio_codec"] == recipe.audio_codec
                and Path(report["path"]).is_file()
            )
            check(f"{row.id}/{key} produced and verified", good, "")
            print(
                f"    {row.id:<12} {key:<18} {'PRODUCED':<10} {report['frames']:>6} "
                f"{report['loudness_lufs']:>9}  {report['video_codec']}/{report['pix_fmt']} "
                f"{report['width']}x{report['height']} {report['audio_codec']} "
                f"{report['frames']}f {report['duration']}s"
            )
        print(f"    {row.id:<12} declared {row_produced + row_missing:>2}: produced {row_produced:>2}, missing {row_missing:>2}")

    check(
        "b1. produced + declared missing == every declared target",
        produced + missing_count == declared,
        f"{produced} produced + {missing_count} missing == {declared} declared",
    )
    check(
        "b2. no declared target was left missing in this build",
        missing_count == 0,
        f"{missing_count} missing",
    )

    print("\nc. a target the profile does not declare is refused by code")
    reel = profiles.PROFILES["reel"]
    invented = refusal(lambda: deliver.deliver(master, reel, "av1/mkv", work / "out" / "reel", work / "tmp"))
    check("c1. an invented target is DELIVERY_UNKNOWN_TARGET", invented == "DELIVERY_UNKNOWN_TARGET", invented)
    try:
        deliver.deliver(master, reel, "dnxhr/mov", work / "out" / "reel", work / "tmp")
        undeclared, detail = "produced", ""
    except EDLError as exc:
        undeclared, detail = exc.code, str(exc)
    check(
        "c2. a real target this profile does not declare is refused",
        undeclared == "DELIVERY_UNKNOWN_TARGET" and "h264/mp4" in detail,
        f"{undeclared} names the declared targets",
    )

    print("\nd. loudness is measured on the delivered file, not copied from the request")
    baseline = reports[("reel", "h264/mp4")]
    quiet = build_master(work / "media", gain_db=QUIET_DB, name="quiet.mp4")
    quiet_report = deliver.deliver(quiet, reel, "h264/mp4", work / "out" / "quiet", work / "tmp" / "quiet")
    loud_i = baseline["loudness_lufs"]
    quiet_i = quiet_report["loudness_lufs"]
    check(
        "d1. both deliverables report an integrated loudness",
        loud_i is not None and quiet_i is not None,
        f"master {loud_i} / {quiet_i}",
    )
    check(
        f"d2. the {abs(QUIET_DB):.0f} dB quieter master delivers a quieter number",
        loud_i is not None and quiet_i is not None and abs(loud_i - quiet_i) >= 10.0,
        f"{abs(loud_i - quiet_i):.1f} LU apart",
    )
    independent = qc.lufs_of(Path(baseline["path"])).get("input_i")
    check(
        "d3. the report matches an independent measurement of the output",
        independent is not None and abs(independent - loud_i) < 0.3,
        f"ffprobe pass {independent} vs report {loud_i}",
    )
    check(
        "d4. the report carries a true peak from the output",
        baseline["true_peak_db"] is not None and baseline["true_peak_db"] <= 0.0,
        baseline["true_peak_db"],
    )

    print("\ne. a document with captions leaves a sidecar, never a muxed stream")
    captioned = deliver.deliver(
        master, reel, "h264/mp4", work / "out" / "captions", work / "tmp" / "captions",
        document=caption_document(chapters=False),
    )
    sidecar = Path(captioned["sidecar"]) if captioned["sidecar"] else None
    body = sidecar.read_text(encoding="utf-8") if sidecar and sidecar.is_file() else ""
    check("e1. a sidecar .srt is written next to the deliverable", sidecar is not None and sidecar.is_file(), captioned["sidecar"])
    check("e2. the report counts the three cues", captioned["sidecar_cues"] == 3, captioned["sidecar_cues"])
    check("e3. the file carries three cue blocks", body.count(" --> ") == 3, body.count(" --> "))
    check(
        "e4. nothing was muxed: no subtitle stream in the file",
        captioned["subtitles"] is False and captioned["subtitle_streams"] == 0,
        f"subtitles={captioned['subtitles']} streams={captioned['subtitle_streams']}",
    )
    check("e5. the first cue starts at 00:00:00,000", "00:00:00,000 --> 00:00:00,500" in body, body.splitlines()[:2])

    print("\nf. chapters come from the document, and nowhere else")
    chaptered = deliver.deliver(
        master, reel, "h264/mp4", work / "out" / "chapters", work / "tmp" / "chapters",
        document=caption_document(chapters=True),
    )
    metadata = Path(chaptered["chapter_metadata"]) if chaptered["chapter_metadata"] else None
    text = metadata.read_text(encoding="utf-8") if metadata and metadata.is_file() else ""
    check("f1. the two scene boundaries declare two chapters", chaptered["chapter_count"] == 2, chaptered["chapter_count"])
    check("f2. the metadata file carries two [CHAPTER] blocks", text.count("[CHAPTER]") == 2, metadata)
    check("f3. the delivered file itself carries no chapter stream", chaptered["chapters"] == 0, chaptered["chapters"])
    check(
        "f4. a document with no declaration reports zero chapters and no file",
        baseline["chapter_count"] == 0 and baseline["chapter_metadata"] is None,
        f"count={baseline['chapter_count']} file={baseline['chapter_metadata']}",
    )

    print(
        f"\nsummary: {declared} target(s) declared, {produced} produced and verified, "
        f"{missing_count} declared missing, {len(profiles.PROFILES)} profile(s), "
        f"{time.monotonic() - started:.1f}s"
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx_deliver_gate_") as tmp:
        try:
            run(Path(tmp))
        except Exception:  # noqa: BLE001 - an unexpected crash is a failure, never a skip
            traceback.print_exc()
            print("\nharness crashed before the delivery matrix was measured")
            return 1
    print(f"\n{PASS} PASS / {FAIL} FAIL / 0 SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
