"""Native ingest: stills become Ken-Burns clips, audio becomes a normalised asset."""

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


def _frame_bytes(path: Path, seconds: float) -> bytes:
    proc = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ss",
            f"{seconds:g}",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
    )
    return proc.stdout


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-ingest-native-") as raw:
        tmp = Path(raw)
        stage = tmp / "stage"
        photo = tmp / "photo.png"
        pattern = tmp / "pattern.png"
        music = tmp / "music.m4a"
        clip = tmp / "clip.mp4"

        ok_photo = _make(
            photo,
            ["-f", "lavfi", "-i", "color=c=red:s=640x360", "-frames:v", "1"],
        )
        ok_pattern = _make(
            pattern,
            ["-f", "lavfi", "-i", "testsrc=size=640x360", "-frames:v", "1"],
        )
        ok_music = _make(
            music,
            ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac"],
        )
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
        if not (ok_photo and ok_pattern and ok_music and ok_clip):
            skip("fixture", "ffmpeg non disponibile")
            print(f"\n{PASS} PASS / {FAIL} FAIL")
            return 0

        before = {
            p.name: _sha256(p) for p in (photo, pattern, music, clip) if p.is_file()
        }

        photo_info = probe.probe_asset(photo).to_dict()
        image = ingest.normalize_image(
            photo,
            photo_info,
            stage,
            width=320,
            height=180,
            fps=25.0,
            duration=3.0,
            label="photo",
        )
        check("immagine kind image", image.kind == "image", image.kind)
        check("immagine stagiata", image.is_staged is True, image.is_staged)
        check(
            "immagine to_dict porta kind",
            image.to_dict().get("kind") == "image",
            image.to_dict().get("kind"),
        )
        staged_image = Path(image.staged_path)
        check(
            "immagine output mp4",
            staged_image.is_file() and staged_image.suffix == ".mp4",
            staged_image.name,
        )
        if staged_image.is_file():
            out = probe.probe(str(staged_image))
            check(
                "immagine geometria 320x180",
                (out.width, out.height) == (320, 180),
                (out.width, out.height),
            )
            check("immagine fps 25", abs(out.fps - 25.0) < 0.01, out.fps)
            check("immagine senza audio", out.has_audio is False, out.has_audio)
            check(
                "immagine durata 3.0",
                abs(out.duration - 3.0) <= 0.15,
                out.duration,
            )
            counted = probe.probe(str(staged_image), count_frames=True)
            check(
                "immagine piu di un frame",
                (counted.nb_frames or 0) > 1,
                counted.nb_frames,
            )

        # A solid colour is invariant under zoom, so the Ken-Burns comparison
        # uses a patterned still. The sample is deterministic: one frame at a
        # fixed timestamp decoded to a fixed pixel format by the same ffmpeg.
        pattern_info = probe.probe_asset(pattern).to_dict()
        zoom_in = ingest.normalize_image(
            pattern,
            pattern_info,
            stage,
            width=320,
            height=180,
            fps=25.0,
            duration=3.0,
            treatment="ken_burns_zoom_in",
            label="zoom_in",
        )
        static = ingest.normalize_image(
            pattern,
            pattern_info,
            stage,
            width=320,
            height=180,
            fps=25.0,
            duration=3.0,
            treatment="none",
            label="static",
        )
        zoom_frame = _frame_bytes(Path(zoom_in.staged_path), 2.0)
        static_frame = _frame_bytes(Path(static.staged_path), 2.0)
        check(
            "ken_burns cambia il contenuto",
            len(zoom_frame) > 0
            and len(static_frame) > 0
            and zoom_frame != static_frame,
            (len(zoom_frame), len(static_frame)),
        )

        music_info = probe.probe_asset(music).to_dict()
        audio = ingest.normalize_audio(music, music_info, stage, label="music")
        check("audio kind audio", audio.kind == "audio", audio.kind)
        check("audio stagiato", audio.is_staged is True, audio.is_staged)
        check(
            "audio to_dict porta kind",
            audio.to_dict().get("kind") == "audio",
            audio.to_dict().get("kind"),
        )
        staged_audio = Path(audio.staged_path)
        check(
            "audio output m4a",
            staged_audio.is_file() and staged_audio.suffix == ".m4a",
            staged_audio.name,
        )
        if staged_audio.is_file():
            aout = probe.probe_asset(str(staged_audio))
            check("audio senza video", aout.kind == "audio", aout.kind)
            check("audio video_codec vuoto", aout.video_codec == "", aout.video_codec)
            check("audio 48000 Hz", aout.sample_rate == 48000, aout.sample_rate)
            check("audio 2 canali", aout.channels == 2, aout.channels)
            check(
                "audio durata 2.0",
                abs(aout.duration - 2.0) <= 0.2,
                aout.duration,
            )

        combined = ingest.ingest_sources(
            {"src_photo": photo, "src_music": music, "src_clip": clip},
            tmp / "stage_combined",
            specs={
                "src_photo": {
                    "width": 320,
                    "height": 180,
                    "fps": 25.0,
                    "duration": 3.0,
                }
            },
        )
        check(
            "ingest_sources tre risultati",
            list(combined) == ["src_photo", "src_music", "src_clip"],
            list(combined),
        )
        check(
            "ingest_sources kind immagine",
            combined["src_photo"].kind == "image",
            combined["src_photo"].kind,
        )
        check(
            "ingest_sources kind audio",
            combined["src_music"].kind == "audio",
            combined["src_music"].kind,
        )
        check(
            "ingest_sources kind video",
            combined["src_clip"].kind == "video",
            combined["src_clip"].kind,
        )
        check(
            "ingest_sources output presenti",
            all(Path(result.staged_path).is_file() for result in combined.values()),
            [result.staged_path for result in combined.values()],
        )

        try:
            ingest.ingest_sources(
                {"src_photo": photo},
                tmp / "stage_bad_kind",
                specs={
                    "src_photo": {
                        "kind": "video",
                        "width": 320,
                        "height": 180,
                        "fps": 25.0,
                        "duration": 3.0,
                    }
                },
            )
        except ingest.IngestError as exc:
            check("spec kind contraddittorio rifiutato", True, str(exc)[:60])
        except Exception as exc:  # noqa: BLE001
            check(
                "spec kind contraddittorio rifiutato",
                False,
                type(exc).__name__,
            )
        else:
            check("spec kind contraddittorio rifiutato", False, "accettato")

        try:
            ingest.ingest_sources({"src_photo": photo}, tmp / "stage_no_spec")
        except ingest.IngestError as exc:
            check("immagine senza specs rifiutata", True, str(exc)[:60])
        except Exception as exc:  # noqa: BLE001
            check("immagine senza specs rifiutata", False, type(exc).__name__)
        else:
            check("immagine senza specs rifiutata", False, "accettata")

        after = {
            p.name: _sha256(p) for p in (photo, pattern, music, clip) if p.is_file()
        }
        check("sorgenti intatte", before == after, sorted(before))

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
