"""EDL loudness: optional two-pass loudnorm with measured evidence on disk.

python tests/test_edl_loudness.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import qc, render  # noqa: E402
from vx.contract import RenderFailure  # noqa: E402
from vx.edl_plan import ClipSegment, EDLPlan, SourceRef, Track  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
CALLS: list[list[str]] = []
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:56} {reason}")


class _Proc:
    ok = True
    rc = 0
    stdout = ""
    stderr = ""

    def tail(self, lines: int = 25) -> str:
        return ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _plan(*, duration: float = 2.0, with_audio: bool = True) -> EDLPlan:
    sources = {"src_A": SourceRef("a.mp4", "a" * 64, duration)}
    c1 = ClipSegment(
        "c1", "src_A", 0.0, duration, 0.0, duration, 0, "main_talking_head"
    )
    audio = ()
    if with_audio:
        a1 = ClipSegment("a1", "src_A", 0.0, duration, 0.0, duration, 0, "dialogue")
        audio = (Track(0, (a1,), "dialogue"),)
    return EDLPlan(
        fps=25.0,
        duration_sec=duration,
        aspect_ratio="16:9",
        resolution=(320, 180),
        sources=sources,
        video_tracks=(Track(0, (c1,), "main"),),
        audio_tracks=audio,
    )


def _install_fakes() -> tuple:
    original_run = render.ff.run_ff
    original_measure = render._measure_loudness

    def fake_run_ff(args, **kwargs):
        argv = [str(a) for a in args]
        CALLS.append(argv)
        target = Path(argv[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
        return _Proc()

    render.ff.run_ff = fake_run_ff
    render._measure_loudness = lambda path: {
        "input_i": -21.77,
        "input_tp": -19.7,
        "input_lra": 0.1,
        "input_thresh": -31.77,
        "target_offset": 0.02,
    }
    return original_run, original_measure


def _restore(original: tuple) -> None:
    render.ff.run_ff, render._measure_loudness = original


def _make(path: Path, duration: float) -> bool:
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
            f"color=c=black:s=320x180:r=25:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
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


def main() -> int:
    plan = _plan()
    check("piano valido", plan.validate() == [], plan.validate())

    expected = [
        "-i",
        "a.mp4",
        "-filter_complex_script",
        "g.txt",
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-crf",
        "17",
        "-preset",
        "fast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        "out.mp4",
    ]
    args = render.build_edl_command(
        plan, {"src_A": "a.mp4"}, "out.mp4", graph_path="g.txt"
    )
    check("build_edl_command invariato", args == expected, args)
    check("build_edl_command senza -af", "-af" not in args)
    check("build_edl_command senza -c copy", "copy" not in args)

    without_progress = render.build_edl_command(
        plan, {"src_A": "a.mp4"}, "out.mp4", graph_path="g.txt", progress_file=None
    )
    with_progress = render.build_edl_command(
        plan,
        {"src_A": "a.mp4"},
        "out.mp4",
        graph_path="g.txt",
        progress_file="progress.txt",
    )
    check(
        "progress_file=None: argv identico al precedente",
        without_progress == expected,
        without_progress,
    )
    check(
        "progress_file: differisce solo per -progress <path>",
        with_progress == expected[:-1] + ["-progress", "progress.txt", expected[-1]],
        with_progress,
    )
    check(
        "progress_file: coppia subito prima dell'output",
        with_progress[-3:] == ["-progress", "progress.txt", "out.mp4"],
        with_progress[-3:],
    )

    original = _install_fakes()
    try:
        with tempfile.TemporaryDirectory(prefix="vx-edl-loud-") as raw:
            tmp = Path(raw)
            CALLS.clear()
            out = tmp / "plain.mp4"
            render.render_edl(
                plan,
                {"src_A": "a.mp4"},
                out,
                workdir=tmp / "work",
                crf=30,
                preset="ultrafast",
            )
            check("loudness=None: una sola chiamata", len(CALLS) == 1, len(CALLS))
            check(
                "loudness=None: nessun 04_loudness.txt",
                not (tmp / "work" / "04_loudness.txt").exists(),
            )
            check(
                "loudness=None: nessun edl_render.json",
                not (tmp / "work" / "edl_render.json").exists(),
            )

        with tempfile.TemporaryDirectory(prefix="vx-edl-loud-") as raw:
            tmp = Path(raw)
            CALLS.clear()
            out = tmp / "loud.mp4"
            render.render_edl(
                plan,
                {"src_A": "a.mp4"},
                out,
                workdir=tmp / "work",
                crf=30,
                preset="ultrafast",
                loudness=(-14.0, -1.0),
            )
            check("loudness: due chiamate", len(CALLS) == 2, len(CALLS))
            script04 = tmp / "work" / "04_loudness.txt"
            check("loudness: 04_loudness.txt su disco", script04.is_file())
            check(
                "loudness: il grafo contiene loudnorm",
                script04.is_file()
                and "loudnorm=I=-14.0:TP=-1.0" in script04.read_text(encoding="utf-8"),
            )
            payload_path = tmp / "work" / "edl_render.json"
            check("loudness: edl_render.json su disco", payload_path.is_file())
            payload = {}
            if payload_path.is_file():
                payload = json.loads(payload_path.read_text(encoding="utf-8"))
            check(
                "loudness: json con pre/final/target/measured",
                set(payload) == {"pre", "final", "loudness"}
                and payload["loudness"]["target"] == {"i": -14.0, "tp": -1.0}
                and "input_i" in payload["loudness"]["measured"],
                sorted(payload),
            )
            second = CALLS[-1]
            joined = " ".join(second)
            check(
                "seconda passata: -filter_complex_script",
                "-filter_complex_script" in second,
            )
            check("seconda passata: -map 0:v:0", "0:v:0" in second)
            check("seconda passata: -map [aout]", "[aout]" in second)
            check("seconda passata: -c:v copy", "-c:v copy" in joined, joined[-80:])
            check("seconda passata: nessun -af", "-af" not in second)

        with tempfile.TemporaryDirectory(prefix="vx-edl-loud-") as raw:
            tmp = Path(raw)
            CALLS.clear()
            out = tmp / "progress.mp4"
            progress_path = str(tmp / "progress.txt")
            render.render_edl(
                plan,
                {"src_A": "a.mp4"},
                out,
                workdir=tmp / "work",
                crf=30,
                preset="ultrafast",
                loudness=(-14.0, -1.0),
                progress_file=progress_path,
            )
            first, second = CALLS[0], CALLS[1]
            check(
                "progress_file: solo la prima passata lo riceve",
                "-progress" in first and "-progress" not in second,
                (first[-4:], second[-4:]),
            )
            check(
                "progress_file: coppia prima dell'output della prima passata",
                first[-3:]
                == [
                    "-progress",
                    progress_path,
                    str(tmp / "work" / "final_pre_loudness.mp4"),
                ],
                first[-3:],
            )
    finally:
        _restore(original)

    no_audio = _plan(with_audio=False)
    raised = False
    try:
        render.render_edl(
            no_audio,
            {},
            "out.mp4",
            workdir=tempfile.mkdtemp(prefix="vx-edl-loud-"),
            loudness=(-14.0, -1.0),
        )
    except RenderFailure as exc:
        raised = True
        check("piano senza audio + loudness: RenderFailure", True, str(exc)[:60])
    except Exception as exc:  # noqa: BLE001
        check("piano senza audio + loudness: RenderFailure", False, type(exc).__name__)
    else:
        check("piano senza audio + loudness: RenderFailure", False, "nessuna eccezione")

    with tempfile.TemporaryDirectory(prefix="vx-edl-loud-real-") as raw:
        tmp = Path(raw)
        src = tmp / "src.mp4"
        if not _make(src, 5.0):
            skip("master 5 s a -14 LUFS", "ffmpeg non disponibile")
        else:
            sources = {"src_A": SourceRef(str(src), _sha256(src), 5.0)}
            c1 = ClipSegment("c1", "src_A", 0.0, 5.0, 0.0, 5.0, 0, "main_talking_head")
            a1 = ClipSegment("a1", "src_A", 0.0, 5.0, 0.0, 5.0, 0, "dialogue")
            real = EDLPlan(
                fps=25.0,
                duration_sec=5.0,
                aspect_ratio="16:9",
                resolution=(320, 180),
                sources=sources,
                video_tracks=(Track(0, (c1,), "main"),),
                audio_tracks=(Track(0, (a1,), "dialogue"),),
            )
            out = tmp / "master.mp4"
            try:
                render.render_edl(
                    real,
                    {"src_A": str(src)},
                    out,
                    workdir=tmp / "work",
                    crf=30,
                    preset="ultrafast",
                    loudness=(-14.0, -1.0),
                )
            except Exception as exc:  # noqa: BLE001
                check("master 5 s a -14 LUFS", False, f"{type(exc).__name__}: {exc}")
            else:
                result = qc.audit_master(out, target_lufs=-14.0, target_tp=-1.0)
                check(
                    "master 5 s a -14 LUFS: audit ok",
                    result["ok"] is True,
                    result["measured"],
                )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
