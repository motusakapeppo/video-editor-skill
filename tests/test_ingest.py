"""Stage 00: VFR -> CFR, HDR -> BT.709, SAR -> 1:1, and the run integration."""

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
from vx import contract, ingest, probe  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:58} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"SKIP {label:58} {reason}")


def ffmpeg(args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
    )


def video_stream(path: Path) -> dict:
    return probe.raw_video_stream(str(path))


def main() -> int:
    check(
        "detect_vfr: 60 vs 29.97",
        ingest.detect_vfr({"r_frame_rate": "60/1", "avg_frame_rate": "2997/100"})
        == (True, 29.97),
    )
    check(
        "detect_vfr: 25 vs 25",
        ingest.detect_vfr({"r_frame_rate": "25/1", "avg_frame_rate": "25/1"})
        == (False, 25.0),
    )
    check(
        "detect_hdr: PQ",
        ingest.detect_hdr({"color_transfer": "smpte2084", "color_space": "bt2020nc"})
        is True,
    )
    check(
        "detect_hdr: HLG",
        ingest.detect_hdr({"color_transfer": "arib-std-b67", "color_space": "bt2020"})
        is True,
    )
    check(
        "detect_hdr: SDR",
        ingest.detect_hdr({"color_transfer": "bt709", "color_space": "bt709"}) is False,
    )
    check("detect_sar: 16:15", ingest.detect_sar({"sample_aspect_ratio": "16:15"}))
    check(
        "detect_sar: 1:1",
        ingest.detect_sar({"sample_aspect_ratio": "1:1"}) is False,
    )
    check(
        "detect_rotation: side data",
        ingest.detect_rotation({"side_data_list": [{"rotation": -90}]}) == -90,
    )

    try:
        graph, applied = ingest.build_ingest_filtergraph(
            was_vfr=True, was_hdr=True, was_sar=True, target_fps=30.0
        )
        check(
            "grafo ingest: VFR+HDR+SAR",
            all(
                token in graph
                for token in (
                    "scale=trunc(iw*sar/2)*2:ih",
                    "tonemap=hable",
                    "fps=fps=30",
                    "format=yuv420p",
                )
            ),
            graph,
        )
        check(
            "grafo ingest: applied",
            "fps=30" in applied and "tonemap_hdr_to_bt709" in applied,
        )
    except contract.MissingDependency as exc:
        skip("grafo ingest: VFR+HDR+SAR", str(exc))

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        stage = base / "stage"

        cfr = base / "cfr.mp4"
        if (
            ffmpeg(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=320x180:rate=30:duration=1",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(cfr),
                ]
            ).returncode
            != 0
        ):
            skip("passthrough CFR", "encoder unavailable")
        else:
            result = ingest.normalize_source(cfr, video_stream(cfr), stage)
            check("passthrough CFR: non stagia", result.is_staged is False)
            check(
                "passthrough CFR: path originale",
                Path(result.staged_path) == cfr.resolve(),
                result.staged_path,
            )
            check("passthrough CFR: nessun filtro", result.filters_applied == ())

        vfr = base / "vfr.mp4"
        proc = ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=320x180:rate=30:duration=2",
                "-vf",
                "settb=1/1000,setpts=PTS+20*sin(N*1.7)",
                "-fps_mode",
                "vfr",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(vfr),
            ]
        )
        if proc.returncode != 0:
            skip("VFR staging", "VFR generator unavailable")
        else:
            fraction, median = ingest.measure_frame_deltas(vfr)
            check("VFR rilevato dalle PTS", (fraction or 0) > 0.05, fraction)
            result = ingest.normalize_source(vfr, video_stream(vfr), stage)
            check("VFR: stagiato", result.is_staged is True)
            check("VFR: was_vfr", result.was_vfr is True)
            out = probe.probe(result.staged_path)
            check("VFR: output CFR 30", abs(out.fps - 30.0) < 0.01, out.fps)
            after, _ = ingest.measure_frame_deltas(result.staged_path)
            check("VFR: output senza delta anomali", after == 0.0, after)

        hdr = base / "hdr.mp4"
        proc = ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=320x180:rate=25:duration=1",
                "-vf",
                "format=yuv420p10le,setparams=color_primaries=bt2020:"
                "color_trc=smpte2084:colorspace=bt2020nc",
                "-c:v",
                "libx264",
                "-x264-params",
                "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
                "-color_primaries",
                "bt2020",
                "-color_trc",
                "smpte2084",
                "-colorspace",
                "bt2020nc",
                str(hdr),
            ]
        )
        if proc.returncode != 0:
            skip("HDR staging", "10-bit/HDR encoder unavailable")
        else:
            result = ingest.normalize_source(hdr, video_stream(hdr), stage)
            check("HDR: was_hdr", result.was_hdr is True)
            out = probe.probe(result.staged_path)
            check(
                "HDR: output BT.709",
                out.color.get("color_transfer") == "bt709"
                and out.color.get("color_space") == "bt709",
                out.color,
            )
            check("HDR: output 8-bit", out.pix_fmt == "yuv420p", out.pix_fmt)

        sar = base / "sar.mp4"
        proc = ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=720x576:rate=25:duration=1",
                "-vf",
                "setsar=16/15",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(sar),
            ]
        )
        if proc.returncode != 0:
            skip("SAR staging", "encoder unavailable")
        else:
            result = ingest.normalize_source(sar, video_stream(sar), stage)
            check("SAR: was_sar", result.was_sar is True)
            out = probe.probe(result.staged_path)
            check("SAR: larghezza desqueezed", out.width == 768, out.width)
            check(
                "SAR: output 1:1",
                video_stream(Path(result.staged_path)).get("sample_aspect_ratio")
                == "1:1",
            )

        if vfr.exists():
            brief = base / "brief.json"
            brief.write_text(
                json.dumps({"format": "16:9", "hook": "none", "subtitles": "none"}),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "scripts")
            env["VX_FFMPEG"] = FFMPEG
            work = base / "work"
            before = hashlib.sha256(vfr.read_bytes()).hexdigest()
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vx.cli",
                    "run",
                    "--input",
                    str(vfr),
                    "--brief",
                    str(brief),
                    "--workdir",
                    str(work),
                    "--json",
                    "--no-speech",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=env,
                timeout=600,
            )
            check("run VFR: exit 0", proc.returncode == 0, proc.stderr[-300:])
            summary = {}
            start = proc.stdout.find("{")
            if start >= 0:
                try:
                    summary = json.loads(proc.stdout[start:])
                except json.JSONDecodeError:
                    summary = {}
            check(
                "run VFR: qc pass",
                summary.get("qc_pass") is True,
                summary.get("qc_failures"),
            )
            report = json.loads((work / "report.json").read_text(encoding="utf-8"))
            normalized = (report.get("source") or {}).get("normalized") or {}
            check(
                "run VFR: report was_vfr", normalized.get("was_vfr") is True, normalized
            )
            check(
                "run VFR: report staged",
                (report.get("source") or {}).get("staged") is not None,
            )
            output = Path(summary.get("output") or "")
            check("run VFR: output esiste", output.is_file(), output)
            if output.is_file():
                out = probe.probe(str(output))
                check("run VFR: output CFR 30", abs(out.fps - 30.0) < 0.01, out.fps)
            after = hashlib.sha256(vfr.read_bytes()).hexdigest()
            check("run VFR: sorgente intatto", before == after)

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
