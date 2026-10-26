"""Native probing: probe_asset classifies video, image and audio; probe stays video-only."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import probe  # noqa: E402
from vx.contract import MissingDependency  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _make(path: Path, args: list) -> bool:
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args, str(path)],
        capture_output=True,
    )
    return proc.returncode == 0 and path.is_file() and path.stat().st_size > 0


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-probe-") as raw:
        tmp = Path(raw)
        clip = tmp / "clip.mp4"
        still = tmp / "still.png"
        tone = tmp / "tone.m4a"
        anim = tmp / "anim.gif"

        ok_clip = _make(
            clip,
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x180:r=25",
                "-t",
                "1",
                "-pix_fmt",
                "yuv420p",
            ],
        )
        ok_still = _make(
            still,
            ["-f", "lavfi", "-i", "color=c=red:s=320x180", "-frames:v", "1"],
        )
        ok_tone = _make(
            tone,
            ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac"],
        )
        if not (ok_clip and ok_still and ok_tone):
            skip("fixture", "ffmpeg non disponibile")
            print(f"\n{PASS} PASS / {FAIL} FAIL")
            return 0

        ok_anim = _make(
            anim,
            ["-f", "lavfi", "-i", "testsrc=size=64x64:rate=5", "-t", "1"],
        )

        before = {p.name: _sha256(p) for p in (clip, still, tone, anim) if p.is_file()}

        info = probe.probe_asset(clip)
        check("mp4 kind video", info.kind == "video", info.kind)
        check(
            "mp4 geometria",
            (info.width, info.height) == (320, 180),
            (info.width, info.height),
        )
        check("mp4 has_audio False", info.has_audio is False, info.has_audio)
        check(
            "mp4 to_dict porta kind",
            info.to_dict().get("kind") == "video",
            info.to_dict().get("kind"),
        )
        legacy = probe.probe(clip)
        check("probe() mp4 kind video", legacy.kind == "video", legacy.kind)
        check(
            "probe() mp4 geometria",
            (legacy.width, legacy.height) == (320, 180),
            (legacy.width, legacy.height),
        )

        img = probe.probe_asset(still)
        check("png kind image", img.kind == "image", img.kind)
        check("png width 320", img.width == 320, img.width)
        check("png height 180", img.height == 180, img.height)
        check("png has_audio False", img.has_audio is False, img.has_audio)
        check("png duration 0", img.duration == 0.0, img.duration)

        aud = probe.probe_asset(tone)
        check("m4a kind audio", aud.kind == "audio", aud.kind)
        check("m4a width 0", aud.width == 0, aud.width)
        check("m4a height 0", aud.height == 0, aud.height)
        check("m4a video_codec vuoto", aud.video_codec == "", aud.video_codec)
        check("m4a has_audio True", aud.has_audio is True, aud.has_audio)
        check("m4a durata ~1.0", abs(aud.duration - 1.0) <= 0.2, aud.duration)
        check("m4a sample_rate", aud.sample_rate > 0, aud.sample_rate)
        check("m4a channels", aud.channels > 0, aud.channels)

        if ok_anim:
            gif = probe.probe_asset(anim)
            check("gif animato kind video", gif.kind == "video", gif.kind)
        else:
            skip("gif animato kind video", "encoder gif non disponibile")

        try:
            probe.probe(tone)
        except MissingDependency as exc:
            check("probe() su audio solleva MissingDependency", True, str(exc)[:60])
        except Exception as exc:  # noqa: BLE001
            check(
                "probe() su audio solleva MissingDependency", False, type(exc).__name__
            )
        else:
            check(
                "probe() su audio solleva MissingDependency", False, "nessuna eccezione"
            )

        ghost = tmp / "ghost.mp4"
        try:
            probe.probe_asset(ghost)
        except (MissingDependency, OSError) as exc:
            check("probe_asset() path inesistente", True, type(exc).__name__)
        except Exception as exc:  # noqa: BLE001
            check("probe_asset() path inesistente", False, type(exc).__name__)
        else:
            check(
                "probe_asset() path inesistente", False, "ha restituito un ProbeResult"
            )

        after = {p.name: _sha256(p) for p in (clip, still, tone, anim) if p.is_file()}
        check("sorgenti intatte", before == after, sorted(before))

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
