"""F2-T09 — golden frames for every effect the EDL 3 compiler can render.

One declared table (:data:`EFFECTS`) drives both this generator and
``tests/test_edl3_golden.py``. Each entry builds the smallest ``vx.edl3/1``
document that exercises one effect, renders it at 320x568 with the real
compiler, extracts frames with ``vx.golden``, and writes:

    tests/fixtures/edl3_golden/<effect>/<t>.png
    tests/fixtures/edl3_golden/<effect>/manifest.json
    tests/fixtures/edl3_golden/index.json

The index carries no wall-clock timestamp and no absolute path, so two runs of

    python tests/golden_edl3.py --update

produce byte-identical artefacts. ``video_sha256`` in each manifest covers the
rendered file; the frames themselves are what the gate compares.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3  # noqa: E402
from vx import edl3_render  # noqa: E402
from vx import golden  # noqa: E402
from vx.ffmpeg import ffmpeg as ffmpeg_binary  # noqa: E402
from vx.ffmpeg import run, run_ff  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "edl3_golden"
FPS = 12
WIDTH, HEIGHT = 320, 568
SIZE = (WIDTH, HEIGHT)
ENCODER_SETTINGS = (
    "libx264 -preset ultrafast -crf 18 -pix_fmt yuv420p; "
    f"fps={FPS}; size={WIDTH}x{HEIGHT}"
)
_YAVG = re.compile(r"signalstats\.YAVG=([0-9.]+)")

__all__ = [
    "EFFECTS",
    "FIXTURES",
    "ENCODER_SETTINGS",
    "build_sources",
    "render_and_capture",
    "ffmpeg_version",
    "update",
    "main",
]


# ------------------------------------------------------------------ helpers


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _encode_video(path: Path, spec: str) -> Path:
    proc = run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            spec,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "1",
            str(path),
        ],
        loglevel="error",
    )
    if not proc.ok or not path.exists():
        raise RuntimeError(f"fixture {path.name} failed: {proc.tail()}")
    return path


def _encode_tone(path: Path, frequency: int, duration: float) -> Path:
    proc = run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration}",
            "-ar",
            "48000",
            "-ac",
            "1",
            str(path),
        ],
        loglevel="error",
    )
    if not proc.ok or not path.exists():
        raise RuntimeError(f"tone {path.name} failed: {proc.tail()}")
    return path


def _encode_two_tone(path: Path) -> Path:
    """One second of black then one second of white, in a single file."""
    proc = run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={WIDTH}x{HEIGHT}:r={FPS}:d=1",
            "-f",
            "lavfi",
            "-i",
            f"color=c=white:s={WIDTH}x{HEIGHT}:r={FPS}:d=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "1",
            str(path),
        ],
        loglevel="error",
    )
    if not proc.ok or not path.exists():
        raise RuntimeError(f"fixture {path.name} failed: {proc.tail()}")
    return path


def build_sources(work: Path) -> Dict[str, Path]:
    """Deterministic fixtures shared by every document."""
    work.mkdir(parents=True, exist_ok=True)
    return {
        "blue": _encode_video(
            work / "blue.mp4", f"color=c=0x0000FF:s={WIDTH}x{HEIGHT}:r={FPS}:d=2"
        ),
        "red": _encode_video(
            work / "red.mp4", f"color=c=0xFF0000:s={WIDTH}x{HEIGHT}:r={FPS}:d=2"
        ),
        "green": _encode_video(
            work / "green.mp4", f"color=c=0x00FF00:s={WIDTH}x{HEIGHT}:r={FPS}:d=2"
        ),
        "grey64": _encode_video(
            work / "grey64.mp4", f"color=c=0x404040:s={WIDTH}x{HEIGHT}:r={FPS}:d=2"
        ),
        "grey128": _encode_video(
            work / "grey128.mp4", f"color=c=0x808080:s={WIDTH}x{HEIGHT}:r={FPS}:d=2"
        ),
        "changing": _encode_video(
            work / "changing.mp4", f"testsrc2=s={WIDTH}x{HEIGHT}:r={FPS}:d=2"
        ),
        "two_tone": _encode_two_tone(work / "two_tone.mp4"),
        "speech": _encode_tone(work / "speech.wav", 300, 1.0),
        "bed": _encode_tone(work / "bed.wav", 220, 2.0),
    }


# ------------------------------------------------------------- documents


def _document(clips: Sequence[Mapping[str, Any]], **extra: Any) -> edl3.Timeline3:
    data: Dict[str, Any] = {
        "version": edl3.EDL3_VERSION,
        "fps": FPS,
        "size": [WIDTH, HEIGHT],
        "clips": list(clips),
    }
    data.update(extra)
    return edl3.Timeline3.from_dict(data)


def _clip(
    cid: str,
    source: Any = None,
    *,
    src_in: float = 0.0,
    src_out: float = 2.0,
    layer: int = 0,
    dst: Tuple[float, float] | None = None,
    sequence: str | None = None,
    **extra: Any,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": cid,
        "src_in": src_in,
        "src_out": src_out,
        "layer": layer,
        "dst_in": None if dst is None else dst[0],
        "dst_out": None if dst is None else dst[1],
    }
    if source is not None:
        data["source"] = str(source)
    if sequence is not None:
        data["sequence"] = sequence
    data.update(extra)
    return data


def _effect(kind: str, **params: Any) -> Dict[str, Any]:
    return {"kind": kind, "params": params}


def doc_fade_in(sources: Mapping[str, Path]) -> edl3.Timeline3:
    return _document(
        [
            _clip(
                "c1",
                sources["changing"],
                src_out=1.5,
                transition_in={"kind": "fade_in", "duration": 0.6},
            )
        ]
    )


def doc_opacity(sources: Mapping[str, Path]) -> edl3.Timeline3:
    return _document(
        [
            _clip(
                "c1",
                sources["changing"],
                src_out=1.5,
                effects=[_effect("transform", opacity=0.5)],
            )
        ]
    )


def doc_crop(sources: Mapping[str, Path]) -> edl3.Timeline3:
    return _document(
        [
            _clip(
                "c1",
                sources["changing"],
                src_out=1.5,
                effects=[_effect("crop", x=0.25, y=0.2, width=0.5, height=0.6)],
            )
        ]
    )


def doc_colour(sources: Mapping[str, Path]) -> edl3.Timeline3:
    return _document(
        [
            _clip(
                "c1",
                sources["changing"],
                src_out=1.5,
                effects=[
                    _effect("color", brightness=0.1, contrast=1.4, saturation=0.5)
                ],
            )
        ]
    )


def doc_blur(sources: Mapping[str, Path]) -> edl3.Timeline3:
    return _document(
        [
            _clip(
                "c1",
                sources["changing"],
                src_out=1.5,
                effects=[_effect("blur", sigma=6.0)],
            )
        ]
    )


def doc_reverse(sources: Mapping[str, Path]) -> edl3.Timeline3:
    return _document([_clip("c1", sources["two_tone"], src_out=2.0, reverse=True)])


def doc_freeze(sources: Mapping[str, Path]) -> edl3.Timeline3:
    return _document(
        [
            _clip(
                "c1",
                sources["changing"],
                src_out=2.0,
                freeze={"t": 0.5, "hold": 0.6},
            )
        ]
    )


def doc_speed_2x(sources: Mapping[str, Path]) -> edl3.Timeline3:
    return _document([_clip("c1", sources["changing"], src_out=2.0, speed=2.0)])


def doc_pip(sources: Mapping[str, Path]) -> edl3.Timeline3:
    return _document(
        [
            _clip("base", sources["blue"], dst=(0.0, 2.0), layer=0),
            _clip(
                "fg",
                sources["red"],
                src_out=1.0,
                dst=(0.0, 1.0),
                layer=1,
                compose={"op": "pip", "x": 10, "y": 10, "scale": 0.4},
            ),
        ]
    )


def doc_mask(sources: Mapping[str, Path]) -> edl3.Timeline3:
    return _document(
        [
            _clip("base", sources["blue"], dst=(0.0, 2.0), layer=0),
            _clip(
                "fg",
                sources["red"],
                src_out=1.0,
                dst=(0.0, 1.0),
                layer=1,
                compose={
                    "op": "mask",
                    "shape": "rect",
                    "x": 0,
                    "y": 0,
                    "width": WIDTH // 2,
                    "height": HEIGHT,
                },
            ),
        ]
    )


def doc_blend_screen(sources: Mapping[str, Path]) -> edl3.Timeline3:
    return _document(
        [
            _clip("base", sources["grey64"], dst=(0.0, 2.0), layer=0),
            _clip(
                "fg",
                sources["grey128"],
                src_out=1.0,
                dst=(0.5, 1.5),
                layer=1,
                compose={"op": "blend", "mode": "screen"},
            ),
        ]
    )


def doc_chroma_green(sources: Mapping[str, Path]) -> edl3.Timeline3:
    return _document(
        [
            _clip("base", sources["blue"], dst=(0.0, 2.0), layer=0),
            _clip(
                "fg",
                sources["green"],
                src_out=1.0,
                dst=(0.0, 1.0),
                layer=1,
                compose={"op": "chroma", "color": "green", "similarity": 0.35},
            ),
        ]
    )


def doc_overlay_multi(sources: Mapping[str, Path]) -> edl3.Timeline3:
    return _document(
        [
            _clip("base", sources["blue"], dst=(0.0, 2.0), layer=0),
            _clip(
                "fg1",
                sources["red"],
                src_out=0.6,
                dst=(0.2, 0.8),
                layer=1,
            ),
            _clip(
                "fg2",
                sources["green"],
                src_out=0.6,
                dst=(1.0, 1.6),
                layer=2,
            ),
        ]
    )


def doc_audio_ducking(sources: Mapping[str, Path]) -> edl3.Timeline3:
    return _document(
        [_clip("base", sources["grey64"], dst=(0.0, 2.0), layer=0)],
        audio=[
            {
                "role": "dialogue",
                "clips": [
                    {
                        "source": str(sources["speech"]),
                        "src_in": 0.0,
                        "src_out": 1.0,
                        "dst_in": 0.0,
                        "dst_out": 1.0,
                        "gain_db": 0.0,
                    }
                ],
            },
            {
                "role": "music",
                "clips": [
                    {
                        "source": str(sources["bed"]),
                        "src_in": 0.0,
                        "src_out": 2.0,
                        "dst_in": 0.0,
                        "dst_out": 2.0,
                        "gain_db": -6.0,
                    }
                ],
            },
        ],
        ducking={
            "sidechain_role": "dialogue",
            "target_role": "music",
            "threshold": 0.01,
            "ratio": 8.0,
        },
    )


def doc_nested(sources: Mapping[str, Path]) -> edl3.Timeline3:
    inner = {
        "version": edl3.EDL3_VERSION,
        "fps": FPS,
        "size": [WIDTH, HEIGHT],
        "clips": [_clip("i1", sources["red"], src_in=0.0, src_out=1.0)],
    }
    return _document(
        [
            _clip("o1", sequence="inner", src_out=1.0),
            _clip("o2", sequence="inner", src_out=1.0),
        ],
        sequences={"inner": {"id": "inner", "timeline": inner}},
    )


# ------------------------------------------------------------ effect table


EFFECTS: Dict[str, Dict[str, Any]] = {
    "fade_in": {
        "doc_factory": doc_fade_in,
        "times": [0.2, 0.5, 1.0],
        "pixel_tolerance": 0,
        "audio": "refuse",
    },
    "opacity": {
        "doc_factory": doc_opacity,
        "times": [0.3, 0.9],
        "pixel_tolerance": 0,
        "audio": "refuse",
    },
    "crop": {
        "doc_factory": doc_crop,
        "times": [0.3, 0.9],
        "pixel_tolerance": 0,
        "audio": "refuse",
    },
    "colour": {
        "doc_factory": doc_colour,
        "times": [0.3, 0.9],
        "pixel_tolerance": 0,
        "audio": "refuse",
    },
    "blur": {
        "doc_factory": doc_blur,
        "times": [0.3, 0.9],
        "pixel_tolerance": 0,
        "audio": "refuse",
    },
    "reverse": {
        "doc_factory": doc_reverse,
        "times": [0.3, 1.5],
        "pixel_tolerance": 2.0,
        "audio": "refuse",
    },
    "freeze": {
        "doc_factory": doc_freeze,
        "times": [0.2, 0.8, 1.8],
        "pixel_tolerance": 0,
        "audio": "refuse",
    },
    "speed_2x": {
        "doc_factory": doc_speed_2x,
        "times": [0.2, 0.8],
        "pixel_tolerance": 2.0,
        "audio": "refuse",
    },
    "pip": {
        "doc_factory": doc_pip,
        "times": [0.3, 1.5],
        "pixel_tolerance": 0,
        "audio": "refuse",
    },
    "mask": {
        "doc_factory": doc_mask,
        "times": [0.3, 1.5],
        "pixel_tolerance": 0,
        "audio": "refuse",
    },
    "blend_screen": {
        "doc_factory": doc_blend_screen,
        "times": [0.3, 1.0, 1.8],
        "pixel_tolerance": 0,
        "audio": "refuse",
    },
    "chroma_green": {
        "doc_factory": doc_chroma_green,
        "times": [0.3, 1.5],
        "pixel_tolerance": 0,
        "audio": "refuse",
    },
    "overlay_multi": {
        "doc_factory": doc_overlay_multi,
        "times": [0.1, 0.5, 1.3],
        "pixel_tolerance": 0,
        "audio": "refuse",
    },
    "audio_ducking": {
        "doc_factory": doc_audio_ducking,
        "times": [0.3, 1.5],
        "pixel_tolerance": 0,
        "audio": "mix",
    },
    "nested": {
        "doc_factory": doc_nested,
        "times": [0.3, 1.3],
        "pixel_tolerance": 0,
        "audio": "refuse",
    },
}


# ------------------------------------------------------------ render/capture


def render_and_capture(
    name: str,
    spec: Mapping[str, Any],
    sources: Mapping[str, Path],
    work: Path,
) -> Dict[str, Any]:
    """Render one effect and return the capture manifest for its times."""
    factory: Callable[[Mapping[str, Path]], edl3.Timeline3] = spec["doc_factory"]
    document = factory(sources)
    work.mkdir(parents=True, exist_ok=True)
    video = work / f"{name}.mp4"
    edl3_render.render(
        document,
        video,
        workdir=work / "stage",
        audio=str(spec.get("audio", "refuse")),
    )
    return golden.capture(video, list(spec["times"]), work / "frames")


def _mean_luma(path: Path) -> float:
    proc = run_ff(
        [
            "-i",
            str(path),
            "-vf",
            "signalstats,metadata=mode=print:file=-",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        loglevel="error",
    )
    match = _YAVG.search(proc.stdout or "")
    if not proc.ok or match is None:
        raise RuntimeError(f"mean luma of {path.name} unreadable: {proc.tail()}")
    return round(float(match.group(1)), 3)


def ffmpeg_version() -> str:
    proc = run([ffmpeg_binary(), "-version"], step="probe")
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    if not proc.ok or not lines:
        raise RuntimeError(f"ffmpeg version unreadable: {proc.tail()}")
    return lines[0]


# ------------------------------------------------------------------- update


def _write_effect(
    effect_dir: Path, manifest: Mapping[str, Any]
) -> Tuple[Dict[str, Any], List[float]]:
    effect_dir.mkdir(parents=True, exist_ok=True)
    for stale in effect_dir.glob("*.png"):
        stale.unlink()
    frames: List[Dict[str, Any]] = []
    hashes: Dict[str, str] = {}
    luma: List[float] = []
    for frame in manifest["frames"]:
        value = round(float(frame["time"]), 6)
        target = effect_dir / f"{value:.3f}.png"
        shutil.copyfile(str(frame["path"]), str(target))
        digest = _sha256_file(target)
        size_bytes = target.stat().st_size
        frames.append(
            {
                "time": value,
                "file": target.name,
                "sha256": digest,
                "size_bytes": size_bytes,
            }
        )
        hashes[f"{value:.6f}"] = digest
        luma.append(_mean_luma(target))
    payload: Dict[str, Any] = {
        "version": golden.MANIFEST_VERSION,
        "video_sha256": manifest["video_sha256"],
        "times": [frame["time"] for frame in frames],
        "size": list(manifest["size"]),
        "frame_sha256": hashes,
        "frames": frames,
    }
    _write_json(effect_dir / "manifest.json", payload)
    return payload, luma


def update(fixtures: Path = FIXTURES) -> List[Tuple[str, int]]:
    """Render every effect, write its frames plus the index, print what it wrote."""
    fixtures.mkdir(parents=True, exist_ok=True)
    for child in fixtures.iterdir():
        if child.is_dir() and child.name not in EFFECTS:
            shutil.rmtree(child)

    written: List[Tuple[str, int]] = []
    index_entries: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="vx_golden_edl3_") as tmp:
        work = Path(tmp)
        sources = build_sources(work / "sources")
        for name, spec in EFFECTS.items():
            manifest = render_and_capture(name, spec, sources, work / "render" / name)
            effect_dir = fixtures / name
            payload, luma = _write_effect(effect_dir, manifest)
            times = payload["times"]
            index_entries.append(
                {
                    "name": name,
                    "times": times,
                    "frame_sha256": [
                        payload["frame_sha256"][f"{float(value):.6f}"]
                        for value in times
                    ],
                    "mean_luma": luma,
                }
            )
            written.append((name, len(times)))
            print(
                f"wrote {effect_dir.relative_to(ROOT).as_posix()}: "
                f"{len(times)} frame(s)"
            )

    index = {
        "ffmpeg_version": ffmpeg_version(),
        "encoder_settings": ENCODER_SETTINGS,
        "effects": index_entries,
    }
    _write_json(fixtures / "index.json", index)
    print(f"wrote {(fixtures / 'index.json').relative_to(ROOT).as_posix()}")
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="render and rewrite the golden artefacts (without it nothing is written)",
    )
    args = parser.parse_args(argv)
    if not args.update:
        parser.print_help()
        return 2
    written = update()
    total = sum(count for _name, count in written)
    print(f"\n{len(written)} effect(s), {total} frame(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
