"""Stage 00 resolution guard: pure geometry, graph placement, real staging."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import ingest, probe  # noqa: E402

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


def make_video(
    path: Path, size: str, extra: list | None = None
) -> subprocess.CompletedProcess:
    args = ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration=1"]
    if extra:
        args.extend(extra)
    args.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    return ffmpeg(args)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage_and_check(
    label: str, path: Path, stage_dir: Path, expect_w: int, expect_h: int
) -> None:
    before = sha256(path)
    result = ingest.normalize_source(path, probe.raw_video_stream(str(path)), stage_dir)
    after = sha256(path)
    check(f"{label}: staged", result.is_staged is True, result.is_staged)
    check(
        f"{label}: was_downscaled", result.was_downscaled is True, result.was_downscaled
    )
    check(f"{label}: source untouched", before == after)
    info = result.downscale or {}
    check(
        f"{label}: downscale dict",
        info.get("dst_w") == expect_w and info.get("dst_h") == expect_h,
        info,
    )
    check(
        f"{label}: to_dict exposes downscale",
        result.to_dict().get("was_downscaled") is True
        and result.to_dict().get("downscale") == info,
        result.to_dict().get("downscale"),
    )
    out = probe.probe(result.staged_path)
    check(
        f"{label}: size",
        (out.width, out.height) == (expect_w, expect_h),
        (out.width, out.height),
    )
    check(f"{label}: fps", abs(out.fps - 25.0) < 0.01, out.fps)
    check(f"{label}: pix_fmt", out.pix_fmt == "yuv420p", out.pix_fmt)
    sar = probe.raw_video_stream(result.staged_path).get("sample_aspect_ratio")
    check(f"{label}: SAR 1:1", sar == "1:1", sar)
    check(f"{label}: rotation 0", out.rotation == 0, out.rotation)


def no_stage_and_check(label: str, path: Path, stage_dir: Path) -> None:
    before = sha256(path)
    result = ingest.normalize_source(path, probe.raw_video_stream(str(path)), stage_dir)
    after = sha256(path)
    check(f"{label}: not staged", result.is_staged is False, result.is_staged)
    check(
        f"{label}: was_downscaled false",
        result.was_downscaled is False,
        result.was_downscaled,
    )
    check(f"{label}: downscale None", result.downscale is None, result.downscale)
    check(f"{label}: source untouched", before == after)


GEOMETRY_CASES = [
    ("4K UHD", 3840, 2160, {}, (3412, 1920)),
    ("8K UHD", 7680, 4320, {}, (3412, 1920)),
    ("portrait 4K", 2160, 3840, {}, (1080, 1920)),
    ("rotated 4K", 3840, 2160, {"rotation": 90}, (1080, 1920)),
    ("DCI 4K", 4096, 2160, {}, (3640, 1920)),
    ("square 4K", 4000, 4000, {}, (1920, 1920)),
    ("wide 4K", 3840, 1608, {}, None),
    ("1080p", 1920, 1080, {}, None),
    ("PAL anamorphic", 720, 576, {"sar": 16 / 15}, None),
    ("anamorphic 2:1", 1920, 2160, {"sar": 2.0}, (3412, 1920)),
    ("anamorphic rotated", 1920, 2160, {"sar": 2.0, "rotation": 90}, (1080, 1920)),
]


def main() -> int:
    for label, width, height, kwargs, expected in GEOMETRY_CASES:
        got = ingest.plan_downscale(width, height, **kwargs)
        check(f"plan_downscale: {label}", got == expected, got)
        if expected is None:
            continue
        check(
            f"plan_downscale even: {label}",
            got[0] % 2 == 0 and got[1] % 2 == 0,
            got,
        )
        dw, dh = ingest.effective_dimensions(width, height, **kwargs)
        src_aspect = dw / dh
        dst_aspect = got[0] / got[1]
        check(
            f"plan_downscale aspect: {label}",
            abs(dst_aspect - src_aspect) < 0.01,
            f"{dst_aspect:.4f} vs {src_aspect:.4f}",
        )

    check(
        "effective_dimensions: rotation swaps",
        ingest.effective_dimensions(3840, 2160, rotation=90) == (2160, 3840),
    )
    check(
        "effective_dimensions: sar widens",
        ingest.effective_dimensions(1920, 2160, sar=2.0) == (3840, 2160),
    )
    check(
        "effective_dimensions: sar prima, rotazione dopo",
        ingest.effective_dimensions(1920, 2160, sar=2.0, rotation=90) == (2160, 3840),
    )

    check(
        "detect_downscale: 4K",
        ingest.detect_downscale(
            {"width": 3840, "height": 2160, "sample_aspect_ratio": "1:1"}
        )
        == (True, 3840, 2160),
    )
    check(
        "detect_downscale: rotated",
        ingest.detect_downscale(
            {
                "width": 3840,
                "height": 2160,
                "sample_aspect_ratio": "1:1",
                "side_data_list": [{"rotation": 90}],
            }
        )
        == (True, 2160, 3840),
    )
    check(
        "detect_downscale: 1080p",
        ingest.detect_downscale(
            {"width": 1920, "height": 1080, "sample_aspect_ratio": "1:1"}
        )
        == (False, 1920, 1080),
    )

    check(
        "build_downscale_filter: 4K",
        ingest.build_downscale_filter(3840, 2160)
        == (
            "scale=3412:1920:flags=lanczos",
            "downscale_4k_to_1080p (3840x2160 -> 3412x1920)",
        ),
    )
    check(
        "build_downscale_filter: 1080p",
        ingest.build_downscale_filter(1920, 1080) == ("", ""),
    )

    graph, applied = ingest.build_ingest_filtergraph(
        was_vfr=True,
        was_hdr=True,
        was_sar=True,
        target_fps=30.0,
        downscale=(3412, 1920),
    )
    check(
        "graph: one lanczos scale",
        graph.count("scale=3412:1920:flags=lanczos") == 1,
        graph,
    )
    check(
        "graph: no intermediate SAR scale",
        "scale=trunc(iw*sar/2)*2:ih" not in graph,
        graph,
    )
    check(
        "graph: scale < tonemap < fps < format",
        graph.find("scale=3412")
        < graph.find("tonemap")
        < graph.find("fps=")
        < graph.find("format=yuv420p"),
        graph,
    )
    check("graph: applied downscale", "downscale=3412x1920" in applied, applied)

    old_graph, old_applied = ingest.build_ingest_filtergraph(
        was_vfr=True, was_hdr=True, was_sar=True, target_fps=30.0
    )
    expected_graph = (
        "[0:v]scale=trunc(iw*sar/2)*2:ih,setsar=1,"
        "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
        "tonemap=hable:desat=0.0,zscale=t=bt709:m=bt709:r=limited,"
        "fps=fps=30,format=yuv420p[v]"
    )
    check("graph: None byte-identical", old_graph == expected_graph, old_graph)
    check(
        "graph: None applied",
        old_applied == ("scale_sar_to_square_pixels", "tonemap_hdr_to_bt709", "fps=30"),
        old_applied,
    )

    plain_graph, plain_applied = ingest.build_ingest_filtergraph(
        was_vfr=False, was_hdr=False, was_sar=False, target_fps=25.0
    )
    check(
        "graph: None plain",
        plain_graph == "[0:v]setsar=1,format=yuv420p[v]",
        plain_graph,
    )
    check("graph: None plain applied", plain_applied == ("setsar=1",), plain_applied)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        stage_dir = base / "stage"

        uhd = base / "uhd.mp4"
        proc = make_video(uhd, "3840x2160")
        if proc.returncode != 0:
            skip("4K UHD staging", "encoder unavailable")
        else:
            stage_and_check("4K UHD", uhd, stage_dir, 3412, 1920)

        portrait = base / "portrait.mp4"
        proc = make_video(portrait, "2160x3840")
        if proc.returncode != 0:
            skip("portrait staging", "encoder unavailable")
        else:
            stage_and_check("portrait", portrait, stage_dir, 1080, 1920)

        anamorphic = base / "anamorphic.mp4"
        proc = make_video(anamorphic, "1920x2160", ["-vf", "setsar=2/1"])
        if proc.returncode != 0:
            skip("anamorphic staging", "encoder unavailable")
        else:
            stage_and_check("anamorphic", anamorphic, stage_dir, 3412, 1920)

        rot_base = base / "rot_base.mp4"
        proc = make_video(rot_base, "3840x2160")
        if proc.returncode != 0:
            skip("rotated staging", "encoder unavailable")
        else:
            rotated = base / "rotated.mp4"
            # Fixture-only remux: -display_rotation is an input option, so the
            # display matrix is attached without touching the staging path.
            proc = ffmpeg(
                [
                    "-display_rotation",
                    "90",
                    "-i",
                    str(rot_base),
                    "-c",
                    "copy",
                    str(rotated),
                ]
            )
            if proc.returncode != 0:
                skip("rotated staging", "ffmpeg rejects -display_rotation")
            else:
                stage_and_check("rotated", rotated, stage_dir, 1080, 1920)

        wide = base / "wide.mp4"
        proc = make_video(wide, "1920x800")
        if proc.returncode != 0:
            skip("1920x800 passthrough", "encoder unavailable")
        else:
            no_stage_and_check("1920x800", wide, stage_dir)

        letterbox = base / "letterbox.mp4"
        proc = make_video(letterbox, "3840x1608")
        if proc.returncode != 0:
            skip("3840x1608 passthrough", "encoder unavailable")
        else:
            no_stage_and_check("3840x1608", letterbox, stage_dir)

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
