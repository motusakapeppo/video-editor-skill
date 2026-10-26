"""2.11: cps 20, cue duration, overlap and the measured upscale ratio."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import contract, qc  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:56} {detail}")


def ass(path: Path, events: list) -> Path:
    lines = ["[Script Info]", "ScriptType: v4.00+", "", "[Events]"]
    for start, end, text in events:
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_qc(
    events: list | None = None,
    *,
    aspect: str = "16:9",
    out=(1920, 1080),
    media=(1920, 1080),
    crop: dict | None = None,
):
    saved = {
        name: getattr(qc, name)
        for name in (
            "probe",
            "lufs_of",
            "frame_count",
            "_streams",
            "_black_edges",
            "_nan_timestamps",
            "_faststart",
            "_font_fallbacks",
        )
    }
    handle = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    handle.write(b"\x00" * 16)
    handle.close()
    subs_path = None
    if events is not None:
        subs_path = str(ass(Path(handle.name + ".ass"), events))
    try:
        qc.probe = lambda path: SimpleNamespace(
            width=out[0],
            height=out[1],
            fps=30.0,
            duration=10.0,
            has_audio=True,
            audio_codec="aac",
            video_codec="h264",
            nb_frames=300,
        )
        qc.lufs_of = lambda path: {"input_i": -14.0, "input_tp": -1.0}
        qc.frame_count = lambda path: 300
        qc._streams = lambda path: [
            {"codec_type": "video", "start_time": "0.0"},
            {"codec_type": "audio", "start_time": "0.0"},
        ]
        qc._black_edges = lambda path, duration: (0, "none")
        qc._nan_timestamps = lambda path: (True, "0 NaN timestamp(s)")
        qc._faststart = lambda path: (True, "moov@0 mdat@100")
        qc._font_fallbacks = lambda target, subs: (0, "fonts ok")
        plan = contract.EditPlan(
            output={"aspect": aspect},
            treatments=[],
            cuts=[],
            render={"precision": "full", "crop": crop},
        )
        return qc.check_render(
            plan,
            {"width": media[0], "height": media[1], "fps": 30.0, "duration": 10.0},
            handle.name,
            subs_path=subs_path,
        )
    finally:
        for name, value in saved.items():
            setattr(qc, name, value)
        for suffix in ("", ".ass"):
            try:
                Path(handle.name + suffix).unlink()
            except OSError:
                pass


def by_code(result, code: str) -> dict:
    return next(c for c in result.checks if c["code"] == code)


def main() -> int:
    limit = contract.THRESHOLDS["sub_max_cps"]
    check("soglia cps a 20", limit == 20, limit)

    ok = run_qc([("0:00:00.00", "0:00:01.00", "a" * 19)])
    check(
        "19 cps passa",
        by_code(ok, "cue_cps_max")["pass"] is True,
        by_code(ok, "cue_cps_max"),
    )
    bad = run_qc([("0:00:00.00", "0:00:01.00", "a" * 21)])
    check(
        "21 cps fallisce",
        by_code(bad, "cue_cps_max")["pass"] is False,
        by_code(bad, "cue_cps_max"),
    )
    check("un cue illeggibile fa fallire il QC", bad.pass_ is False)

    short = run_qc([("0:00:00.00", "0:00:00.30", "ciao")])
    check(
        "cue da 0.3s fallisce la durata minima",
        by_code(short, "cue_duration_min")["pass"] is False,
        by_code(short, "cue_duration_min"),
    )
    long = run_qc([("0:00:00.00", "0:00:08.00", "ciao")])
    check(
        "cue da 8s fallisce la durata massima",
        by_code(long, "cue_duration_max")["pass"] is False,
        by_code(long, "cue_duration_max"),
    )
    good = run_qc([("0:00:00.00", "0:00:03.00", "ciao")])
    check(
        "cue da 3s passa entrambe le durate",
        by_code(good, "cue_duration_min")["pass"] is True
        and by_code(good, "cue_duration_max")["pass"] is True,
    )

    overlap = run_qc(
        [
            ("0:00:00.00", "0:00:02.00", "primo"),
            ("0:00:01.50", "0:00:03.00", "secondo"),
        ]
    )
    check(
        "due cue sovrapposti falliscono",
        by_code(overlap, "cue_overlap")["pass"] is False,
        by_code(overlap, "cue_overlap"),
    )
    adjacent = run_qc(
        [
            ("0:00:00.00", "0:00:02.00", "primo"),
            ("0:00:02.00", "0:00:04.00", "secondo"),
        ]
    )
    check(
        "cue adiacenti passano",
        by_code(adjacent, "cue_overlap")["pass"] is True,
        by_code(adjacent, "cue_overlap"),
    )

    flat = run_qc(None)
    check(
        "senza sottotitoli: cue_overlap dichiarato",
        by_code(flat, "cue_overlap")["detail"] == "no subtitles",
        by_code(flat, "cue_overlap"),
    )

    normal = run_qc(None, aspect="16:9", out=(1920, 1080), media=(1920, 1080))
    check(
        "upscale 1.0: checked, non warn",
        by_code(normal, "upscale_ratio")["value"] == 1.0
        and by_code(normal, "upscale_ratio")["status"] == "checked",
        by_code(normal, "upscale_ratio"),
    )
    vertical = run_qc(
        None,
        aspect="9:16",
        out=(1080, 1920),
        media=(1920, 1080),
        crop={"src_w": 1920, "src_h": 1080, "crop_w": 608, "crop_h": 1080},
    )
    check(
        "upscale 1.78: warn, ma il gate resta verde",
        by_code(vertical, "upscale_ratio")["status"] == "warn"
        and by_code(vertical, "upscale_ratio")["pass"] is True
        and abs(by_code(vertical, "upscale_ratio")["value"] - 1.778) < 0.01,
        by_code(vertical, "upscale_ratio"),
    )
    check("il warn non fa fallire il QC", vertical.pass_ is True)

    low = run_qc(
        None,
        aspect="9:16",
        out=(1080, 1920),
        media=(640, 360),
        crop={"src_w": 640, "src_h": 360, "crop_w": 202, "crop_h": 360},
    )
    check(
        "upscale 5.35: warn con il rapporto reale",
        by_code(low, "upscale_ratio")["status"] == "warn"
        and abs(by_code(low, "upscale_ratio")["value"] - 5.347) < 0.01,
        by_code(low, "upscale_ratio"),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
