"""Measure the nine-clip corpus and write the fixtures every threshold comes from.

Why: a threshold without a corpus hash is a guess. This script measures the same
nine clips the verdict names, records every number with its basis, and writes
``tests/fixtures/corpus_stats.json``. When a measurement cannot be computed it is
stored as ``null`` with the reason — never replaced by a plausible number.

Fixtures are written only with an explicit ``--write``; without it the run is a
dry run and no file is touched. ``renderer_delta.json`` is written only when the
approved delivery is configured and measurable, so a machine without the private
reference cannot overwrite the recorded baseline with ``null``.

Run with ``PYTHONPATH=<repo>/scripts`` and the video venv.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import aesthetics, analyze, grammar, probe, prove, subs  # noqa: E402

CORPUS = ROOT / "tests" / "stock_corpus"
FIXTURES = ROOT / "tests" / "fixtures"
SAMPLES = 12
WORK_WIDTH = 320


def _clips() -> List[Tuple[str, Path]]:
    items = [(path.stem, path) for path in sorted(CORPUS.glob("*.mp4"))]
    items += [
        (f"real_{path.stem}", path) for path in sorted((CORPUS / "real").glob("*.webm"))
    ]
    return items


def _corpus_hash(items: List[Tuple[str, Path]]) -> str:
    digest = hashlib.sha256()
    for _name, path in sorted(items, key=lambda item: item[0]):
        relative = path.relative_to(CORPUS).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(str(path.stat().st_size).encode("ascii"))
        with path.open("rb") as handle:
            digest.update(handle.read(1024 * 1024))
    return digest.hexdigest()


def _hue_mass_top3(path: Path, duration: float) -> List[Dict[str, Any]]:
    import cv2
    import numpy as np

    capture = cv2.VideoCapture(str(path))
    hist_sum = np.zeros(180, dtype=np.float64)
    frames = 0
    for index in range(SAMPLES):
        at = duration * (index + 0.5) / SAMPLES
        capture.set(cv2.CAP_PROP_POS_MSEC, at * 1000.0)
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hue = hsv[:, :, 0]
        saturation = hsv[:, :, 1]
        value = hsv[:, :, 2]
        mask = (saturation >= 40) & (value >= 40)
        if not bool(mask.any()):
            continue
        hist = np.bincount(hue[mask].ravel(), minlength=180).astype(np.float64)
        total = float(hist.sum())
        if total <= 0.0:
            continue
        hist_sum += hist / total
        frames += 1
    capture.release()

    if frames == 0:
        return [{"hue": 0, "mass": 0.0} for _ in range(3)]

    hist = hist_sum / frames
    peaks: List[Dict[str, Any]] = []
    work = hist.copy()
    for _ in range(3):
        peak = int(np.argmax(work))
        if work[peak] <= 0.0:
            break
        low = max(0, peak - 5)
        high = min(180, peak + 6)
        peaks.append(
            {"hue": int(peak * 2), "mass": round(float(hist[low:high].sum()), 4)}
        )
        work[max(0, peak - 10) : min(180, peak + 11)] = 0.0
    while len(peaks) < 3:
        peaks.append({"hue": 0, "mass": 0.0})
    return peaks


def _luma_stats(path: Path, duration: float) -> Tuple[Optional[float], Optional[float]]:
    import cv2
    import numpy as np

    capture = cv2.VideoCapture(str(path))
    chunks: List[Any] = []
    for index in range(SAMPLES):
        at = duration * (index + 0.5) / SAMPLES
        capture.set(cv2.CAP_PROP_POS_MSEC, at * 1000.0)
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        height, width = frame.shape[:2]
        small_h = max(2, int(round(height * WORK_WIDTH / float(width))))
        small = cv2.resize(frame, (WORK_WIDTH, small_h), interpolation=cv2.INTER_AREA)
        blue = small[:, :, 0].astype(np.float32)
        green = small[:, :, 1].astype(np.float32)
        red = small[:, :, 2].astype(np.float32)
        luma = (0.114 * blue + 0.587 * green + 0.299 * red) / 255.0
        chunks.append(luma.ravel())
    capture.release()

    if not chunks:
        return None, None
    values = np.concatenate(chunks)
    p01 = float(np.percentile(values, 1))
    p50 = float(np.percentile(values, 50))
    p99 = float(np.percentile(values, 99))
    return round(p50, 4), round(p99 - p01, 4)


def _subject(
    path: Path, duration: float, width: int, height: int
) -> Tuple[Optional[Dict[str, float]], bool]:
    offsets: List[Tuple[float, float]] = []
    face_present = False
    for fraction in (0.25, 0.5, 0.75):
        placement = aesthetics.place(
            path, duration * fraction, src_w=width, src_h=height
        )
        if not placement.face_found:
            continue
        face_present = True
        centre_x = (width - placement.crop_w) / 2.0
        centre_y = (height - placement.crop_h) / 2.0
        offsets.append(
            (
                (placement.x - centre_x) / float(width),
                (placement.y - centre_y) / float(height),
            )
        )
    if not offsets:
        return None, face_present
    x = sum(item[0] for item in offsets) / len(offsets)
    y = sum(item[1] for item in offsets) / len(offsets)
    return {"x": round(x, 4), "y": round(y, 4)}, face_present


def _onsets(path: Path) -> Tuple[List[float], float]:
    grid = grammar.beat_grid(str(path))
    return [float(value) for value in grid.onsets], float(grid.duration)


def _first3s_motion(path: Path) -> Optional[float]:
    motion = analyze.measure_motion(str(path), sample_fps=10.0)
    per_second = list(motion.get("per_second") or [])
    window = per_second[:3]
    if not window:
        return None
    return round(sum(window) / len(window), 4)


def _frame_diffs(path: Path, fps: float) -> Tuple[List[Optional[float]], float]:
    """Per-frame mean |Δluma| at 320 px, mirroring analyze._pixel_metrics.

    ``analyze.measure_motion`` exposes only per-second buckets, so the per-frame
    series is rebuilt here with the same formula. Passing ``fps`` equal to the
    source rate makes ``step == 1`` and keeps the frame index aligned with time.
    """
    import cv2
    import numpy as np

    capture = cv2.VideoCapture(str(path))
    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    if source_fps <= 0.0:
        source_fps = float(fps) if fps > 0.0 else 25.0
    step = max(1, int(round(source_fps / float(fps)))) if fps > 0.0 else 1

    previous: Any = None
    per_frame: List[Optional[float]] = []
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if index % step:
            index += 1
            continue
        height, width = frame.shape[:2]
        small_h = max(2, int(round(height * WORK_WIDTH / float(width))))
        small = cv2.resize(frame, (WORK_WIDTH, small_h), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        if previous is None:
            per_frame.append(None)
        else:
            diff = float(
                np.mean(np.abs(gray.astype(np.float32) - previous.astype(np.float32)))
            )
            per_frame.append(diff)
        previous = gray
        index += 1
    capture.release()
    return per_frame, source_fps


def _transient_ratio(
    per_frame: List[Optional[float]], fps: float, onsets: List[float]
) -> Optional[float]:
    import numpy as np

    values = [value for value in per_frame if value is not None]
    if not values or not onsets:
        return None
    median = float(np.median(values))
    if median <= 0.0:
        return None
    window: List[float] = []
    for onset in onsets:
        frame = int(round(float(onset) * fps))
        for offset in range(frame - 2, frame + 3):
            if 0 <= offset < len(per_frame) and per_frame[offset] is not None:
                window.append(float(per_frame[offset]))
    if not window:
        return None
    return round(float(np.percentile(window, 90)) / median, 4)


def _percentiles(values: List[Optional[float]]) -> Dict[str, Any]:
    import numpy as np

    clean = [float(value) for value in values if value is not None]
    if not clean:
        return {"p33": None, "p67": None, "n": 0}
    return {
        "p33": round(float(np.percentile(clean, 33)), 4),
        "p67": round(float(np.percentile(clean, 67)), 4),
        "n": len(clean),
    }


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="measure the nine-clip corpus")
    parser.add_argument(
        "--write",
        action="store_true",
        help="write tests/fixtures/*.json (default: dry run, writes nothing)",
    )
    args = parser.parse_args(argv)

    items = _clips()
    if len(items) != 9:
        print(f"corpus inatteso: {len(items)} clip invece di 9")
        return 1

    status = prove.approved_status()
    if not args.write:
        print(f"dry run: {len(items)} clip, nessun fixture scritto (usa --write)")
        if status["available"]:
            print(f"delivery approvato: {status['path']}")
        elif status["path"] is None:
            print(
                "SKIPPED: approved delivery not configured "
                f"({status['reason']}) — renderer_delta.json lasciato intatto"
            )
        else:
            print(f"delivery approvato configurato ma assente: {status['reason']}")
        return 0

    if status["path"] is not None and not status["available"]:
        print(f"delivery approvato configurato ma assente: {status['reason']}")
        return 1

    corpus_hash = _corpus_hash(items)
    print(f"corpus: {len(items)} clip, hash {corpus_hash[:16]}...")

    clips: List[Dict[str, Any]] = []
    for name, path in items:
        info = probe.probe(str(path))
        duration = float(info.duration)
        fps = float(info.fps)
        width = int(info.width)
        height = int(info.height)
        print(f"  {name}: {duration:.2f}s @ {fps:.2f} {width}x{height}")

        hue_mass = _hue_mass_top3(path, duration)
        luma_p50, contrast = _luma_stats(path, duration)
        subject_offset, face_present = _subject(path, duration, width, height)
        onsets, audio_duration = _onsets(path)
        onsets_per_s = (
            round(len(onsets) / audio_duration, 4) if audio_duration > 0.0 else None
        )
        first3s_motion = _first3s_motion(path)
        per_frame, source_fps = _frame_diffs(path, fps)
        transcript = subs.transcribe(str(path))
        words = list(transcript.words or [])

        clips.append(
            {
                "name": name,
                "path": path.relative_to(CORPUS).as_posix(),
                "duration_s": round(duration, 3),
                "fps": round(fps, 3),
                "width": width,
                "height": height,
                "hue_mass_top3": hue_mass,
                "luma_p50": luma_p50,
                "contrast": contrast,
                "ink_coverage_pct": None,
                "ink_coverage_note": (
                    "source clip: ink coverage is only meaningful on a rendered "
                    "deliverable"
                ),
                "subject_offset": subject_offset,
                "face_present": face_present,
                "onsets_per_s": onsets_per_s,
                "first_speech_onset_s": (
                    round(float(words[0]["start"]), 3) if words else None
                ),
                "first3s_motion": first3s_motion,
                "transient_ratio": None,
                "transient_basis": None,
                "transient_provenance": None,
                "words": len(words),
                "_per_frame": per_frame,
                "_source_fps": source_fps,
                "_onsets": onsets,
                "_word_onsets": [float(word["start"]) for word in words],
            }
        )

    word_clips = [clip for clip in clips if clip["words"] > 0]
    use_words = len(word_clips) >= 3
    basis = "words" if use_words else "audio_energy_onsets"
    print(f"transient basis: {basis} ({len(word_clips)}/{len(clips)} clip con parole)")

    for clip in clips:
        per_frame = clip.pop("_per_frame")
        source_fps = clip.pop("_source_fps")
        onsets = clip.pop("_onsets")
        word_onsets = clip.pop("_word_onsets")
        if use_words:
            if word_onsets:
                clip["transient_ratio"] = _transient_ratio(
                    per_frame, source_fps, word_onsets
                )
                clip["transient_basis"] = "words"
                clip["transient_provenance"] = "measured:word_onsets"
            else:
                clip["transient_ratio"] = None
                clip["transient_basis"] = "no_words"
                clip["transient_provenance"] = "unmeasured:no_words"
        else:
            if onsets:
                clip["transient_ratio"] = _transient_ratio(
                    per_frame, source_fps, onsets
                )
                clip["transient_basis"] = "audio_energy_onsets"
                clip["transient_provenance"] = "declared:audio_energy_onsets"
            else:
                clip["transient_ratio"] = None
                clip["transient_basis"] = "no_onsets"
                clip["transient_provenance"] = "unmeasured:no_onsets"

    drivers = {
        "transient_ratio": _percentiles([clip["transient_ratio"] for clip in clips]),
        "hue_mass_top3_mass": _percentiles(
            [clip["hue_mass_top3"][0]["mass"] for clip in clips]
        ),
        "luma_p50": _percentiles([clip["luma_p50"] for clip in clips]),
        "onsets_per_s": _percentiles([clip["onsets_per_s"] for clip in clips]),
        "first3s_motion": _percentiles([clip["first3s_motion"] for clip in clips]),
    }

    notes = [
        "ink_coverage_pct is null on source clips: it is only meaningful on a "
        "rendered deliverable.",
        f"transient_ratio basis: {basis}; "
        f"{len(word_clips)}/{len(clips)} clips yielded words.",
    ]
    if not use_words:
        notes.append(
            "fewer than 3 clips yielded words: transient_ratio uses audio-energy "
            "onsets and is declared, not measured."
        )

    payload = {
        "schema": "vx.corpus_stats/1.0",
        "corpus_hash": corpus_hash,
        "generated_by": "tests/measure_corpus.py",
        "clips": clips,
        "drivers": drivers,
        "notes": notes,
    }
    FIXTURES.mkdir(parents=True, exist_ok=True)
    stats_path = FIXTURES / "corpus_stats.json"
    stats_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"scritto {stats_path}")

    delta_path = FIXTURES / "renderer_delta.json"
    if not status["available"]:
        print(
            "SKIPPED: approved delivery not configured "
            f"({status['reason']}) — {delta_path.name} lasciato intatto"
        )
        return 0
    try:
        baseline = prove.renderer_delta(status["path"])
    except Exception as exc:  # noqa: BLE001 - never null the baseline on a failed measure
        print(
            f"renderer_delta non misurabile: {exc} — {delta_path.name} lasciato intatto"
        )
        return 1
    delta_note = (
        "max |ffmpeg-pillow| su p01/p50/p99, nodo V2 approvato, "
        f"12 campioni su {Path(status['path']).name}"
    )
    delta_path.write_text(
        json.dumps(
            {"baseline": baseline, "n_samples": SAMPLES, "note": delta_note},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"scritto {delta_path}: baseline={baseline}")

    for key, value in drivers.items():
        print(f"  {key}: p33={value['p33']} p67={value['p67']} n={value['n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
