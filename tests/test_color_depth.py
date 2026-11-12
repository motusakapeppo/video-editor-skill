"""F26-T05 gate -- a profile's declared colour reaches the delivered file.

The registry declares ``bit_depth`` and ``transfer`` per profile
(:mod:`vx.profiles`); this file proves the renderer honours that declaration in
the pixels and the tags it writes, and that SDR stays the default.

Everything is measured on the produced file with ffprobe taken from
``VX_FFMPEG``'s own directory, never on the values the document declared:

a. the toolchain can encode 10 bit at all (libx264 lists ``yuv420p10le``), so
   the pixel format below is written, not faked;
b. an SDR profile (``film``) is 8 bit ``yuv420p`` with no BT.2100 tags;
c. ``film_hdr`` (HLG) is 10 bit with ``color_transfer=arib-std-b67`` and
   ``color_primaries=bt2020``;
d. a document with no profile is 8 bit, and its picture is byte-identical to
   the SDR profile's -- the profile's declared 10 bit is its graded negative,
   never a silent up-conversion of the mp4;
e. the SDR document still hashes to the value captured from the renderer
   *before* the change (``BASE_SDR_SHA256``), so no SDR byte moved.

    python tests/test_color_depth.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from dataclasses import replace
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, edl3_render, profiles  # noqa: E402
from vx.ffmpeg import ffmpeg, run_ff  # noqa: E402

CANVAS: Tuple[int, int] = (2048, 858)
FPS = 24.0
SECONDS = 1.0
WANT_FRAMES = 24
#: The sha256 of the SDR render of ``document(source, "film")`` -- and of
#: ``document(source, None)``, which produced the same bytes -- captured on the
#: tree BEFORE this task's change. It is the whole proof that SDR did not move:
#: if it stops matching, the renderer changed an SDR byte and must be fixed.
BASE_SDR_SHA256 = "087f94752ce1cf62db3b085e53214bd0395bbc81d798e0ccf4512f0807c6ae56"
ABSENT_TAG = (None, "", "unknown", "reserved")

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def ffprobe_path() -> Path:
    """ffprobe next to the configured ffmpeg binary, never a second install."""
    return ffmpeg_path().with_name(
        "ffprobe.exe" if ffmpeg_path().suffix.lower() == ".exe" else "ffprobe"
    )


def ffmpeg_path() -> Path:
    declared = os.environ.get("VX_FFMPEG")
    if declared:
        # "ffmpeg" e' un nome, non un percorso: senza risolverlo, `with_name`
        # costruisce un parente relativo che non esiste (su un runner il
        # binario sta in una cartella di sistema, non nella cwd).
        found = shutil.which(declared)
        return Path(found or declared)
    return Path(ffmpeg())


def encoder_pixel_formats() -> str:
    """The pixel formats this build's libx264 really advertises."""
    proc = subprocess.run(
        [str(ffmpeg_path()), "-hide_banner", "-h", "encoder=libx264"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    for line in f"{proc.stdout}\n{proc.stderr}".splitlines():
        if "Supported pixel formats:" in line:
            return line.split("Supported pixel formats:", 1)[1].strip()
    return ""


def measure(path: Path, probe: Path) -> Dict[str, Any]:
    """What ffprobe really finds on the muxed file."""
    proc = subprocess.run(
        [str(probe), "-hide_banner", "-loglevel", "error", "-select_streams", "v:0",
         "-count_frames", "-show_entries",
         "stream=pix_fmt,color_transfer,color_primaries,color_space,width,height,"
         "nb_read_frames", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path.name}: {proc.stderr.strip()[-300:]}")
    streams = (json.loads(proc.stdout or "{}").get("streams") or [])
    if not streams:
        raise RuntimeError(f"ffprobe read no video stream from {path.name}")
    stream = streams[0]
    return {
        "pix_fmt": stream.get("pix_fmt"),
        "color_transfer": stream.get("color_transfer"),
        "color_primaries": stream.get("color_primaries"),
        "color_space": stream.get("color_space"),
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "frames": int(stream.get("nb_read_frames", 0)),
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fixture(work: Path) -> Path:
    """One deterministic source at the document's own clock."""
    work.mkdir(parents=True, exist_ok=True)
    path = work / "src.mp4"
    if path.is_file() and path.stat().st_size > 0:
        return path
    proc = run_ff(
        ["-f", "lavfi", "-i",
         f"testsrc2=s={CANVAS[0]}x{CANVAS[1]}:r={FPS:g}:d=2",
         "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
         "-pix_fmt", "yuv420p", str(path)],
        loglevel="error",
    )
    if not proc.ok or not path.is_file():
        raise RuntimeError(f"fixture failed: {proc.tail()}")
    return path


def document(source: Path, profile_ref: Optional[str]) -> edl3.Timeline3:
    """The same document three times, only the profile reference differs."""
    doc: Dict[str, Any] = {
        "version": edl3.EDL3_VERSION,
        "fps": FPS,
        "size": [CANVAS[0], CANVAS[1]],
        "clips": [
            {
                "id": "c1",
                "source": str(source),
                "src_in": 0.0,
                "src_out": SECONDS,
                "layer": 0,
            }
        ],
    }
    if profile_ref is not None:
        doc["profile_ref"] = profile_ref
    return edl3.Timeline3.from_dict(doc)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx_color_depth_") as tmp:
        work = Path(tmp)
        probe = ffprobe_path()
        try:
            source = fixture(work / "sources")

            print("a. the toolchain can encode 10 bit, so the pixel format is real")
            formats = encoder_pixel_formats()
            check("a1. ffprobe sits next to the configured ffmpeg", probe.is_file(), str(probe))
            check(
                "a2. this libx264 advertises yuv420p10le",
                "yuv420p10le" in formats.split(),
                formats,
            )
            check(
                "a3. the 10-bit delivery encoder is libx264",
                edl3_render.delivery_colour("film_hdr")["encoder"] == "libx264",
                edl3_render.delivery_colour("film_hdr")["encoder"],
            )
            check(
                "a4. the HDR pixel format is the one the encoder advertises",
                edl3_render.delivery_colour("film_hdr")["pix_fmt"] in formats.split(),
                edl3_render.delivery_colour("film_hdr")["pix_fmt"],
            )

            print("\nb. the plan carries the declared colour, SDR first")
            sdr_colour = edl3_render.delivery_colour("film")
            plain_colour = edl3_render.delivery_colour(None)
            hdr_colour = edl3_render.delivery_colour("film_hdr")
            check("b1. no profile is the SDR delivery", plain_colour == edl3_render.SDR_DELIVERY, plain_colour)
            check("b2. film (10 bit row, SDR transfer) stays 8 bit", sdr_colour["bit_depth"] == 8, sdr_colour["bit_depth"])
            check("b3. film's pixel format is yuv420p", sdr_colour["pix_fmt"] == "yuv420p", sdr_colour["pix_fmt"])
            check("b4. film carries no stream tags", sdr_colour["tags"] == {}, sdr_colour["tags"])
            check("b5. film keeps its declared 10 bit as the negative", sdr_colour["declared_bit_depth"] == 10, sdr_colour["declared_bit_depth"])
            check("b6. film_hdr declares 10 bit", hdr_colour["bit_depth"] == 10, hdr_colour["bit_depth"])
            check("b7. film_hdr is yuv420p10le", hdr_colour["pix_fmt"] == "yuv420p10le", hdr_colour["pix_fmt"])
            check("b8. film_hdr tags bt2020 primaries", hdr_colour["tags"].get("color_primaries") == "bt2020", hdr_colour["tags"])
            check("b9. film_hdr tags the HLG transfer", hdr_colour["tags"].get("color_trc") == "arib-std-b67", hdr_colour["tags"])

            print("\nc. the registry declares the HDR variant and stays honest")
            row = profiles.resolve("film_hdr")
            check("c1. film_hdr resolves through profile_ref", row.id == "film_hdr", row.id)
            check("c2. film_hdr is a 10 bit HLG row", (row.bit_depth, row.transfer) == (10, "hlg"), (row.bit_depth, row.transfer))
            check("c3. film_hdr is 2048x858 at 24 fps", (row.width, row.height, row.fps) == (2048, 858, 24.0), (row.width, row.height, row.fps))
            check("c4. film_hdr carries ATSC A/85 loudness", (row.loudness_lufs, row.true_peak_db) == (-24.0, -2.0), (row.loudness_lufs, row.true_peak_db))
            check("c5. film_hdr delivers a 10-bit HEVC entry", "hevc10/mp4" in row.deliverables, row.deliverables)
            check("c6. film_hdr's notes say it is a pipeline, not a grade", "not a graded HDR master" in row.notes, row.notes[:60])
            check("c7. film_hdr passes check()", profiles.check(row) == (), profiles.check(row))
            check(
                "c8. an 8-bit HDR row is refused by check()",
                profiles.check(replace(row, bit_depth=8)) != (),
                profiles.check(replace(row, bit_depth=8)),
            )
            check("c9. the genre registry is still the nine F26-T01 rows", len(profiles.PROFILES) == 9, sorted(profiles.PROFILES))

            print("\nd. three real renders, measured on the files")
            rendered: Dict[str, Any] = {}
            for name, ref in (("sdr", "film"), ("plain", None), ("hdr", "film_hdr")):
                destination = work / f"{name}.mp4"
                report = edl3_render.render(
                    document(source, ref), destination, workdir=work / "stage" / name
                )
                rendered[name] = {
                    "path": destination,
                    "report": report,
                    "probe": measure(destination, probe),
                    "sha256": sha256(destination),
                }

            sdr = rendered["sdr"]["probe"]
            plain = rendered["plain"]["probe"]
            hdr = rendered["hdr"]["probe"]
            check("d1. the SDR file is 8 bit yuv420p", sdr["pix_fmt"] == "yuv420p", sdr["pix_fmt"])
            check("d2. the SDR file carries no transfer tag", sdr["color_transfer"] in ABSENT_TAG, sdr["color_transfer"])
            check("d3. the SDR file carries no primaries tag", sdr["color_primaries"] in ABSENT_TAG, sdr["color_primaries"])
            check("d4. the no-profile file is 8 bit yuv420p", plain["pix_fmt"] == "yuv420p", plain["pix_fmt"])
            check("d5. the no-profile file carries no transfer tag", plain["color_transfer"] in ABSENT_TAG, plain["color_transfer"])
            check("d6. the HDR file is 10 bit", hdr["pix_fmt"] == "yuv420p10le", hdr["pix_fmt"])
            check("d7. the HDR file carries color_transfer=arib-std-b67", hdr["color_transfer"] == "arib-std-b67", hdr["color_transfer"])
            check("d8. the HDR file carries color_primaries=bt2020", hdr["color_primaries"] == "bt2020", hdr["color_primaries"])
            check("d9. the HDR file carries colorspace=bt2020nc", hdr["color_space"] == "bt2020nc", hdr["color_space"])
            check("d10. the HDR file keeps the declared canvas", (hdr["width"], hdr["height"]) == CANVAS, (hdr["width"], hdr["height"]))
            check(f"d11. the HDR file keeps its {WANT_FRAMES} frames", hdr["frames"] == WANT_FRAMES, hdr["frames"])
            check(f"d12. the SDR file keeps its {WANT_FRAMES} frames", sdr["frames"] == WANT_FRAMES, sdr["frames"])

            print("\ne. SDR did not move: the pre-change hash still matches")
            check("e1. the film document hashes to the pre-change SDR bytes", rendered["sdr"]["sha256"] == BASE_SDR_SHA256, rendered["sdr"]["sha256"])
            check("e2. the no-profile document is those same bytes", rendered["plain"]["sha256"] == BASE_SDR_SHA256, rendered["plain"]["sha256"])
            check("e3. an HDR delivery is different bytes", rendered["hdr"]["sha256"] != BASE_SDR_SHA256, rendered["hdr"]["sha256"][:16] + "...")
            check("e4. the render report states the SDR colour", rendered["sdr"]["report"]["color"]["pix_fmt"] == "yuv420p", rendered["sdr"]["report"]["color"])
            check("e5. the render report states the HDR colour", rendered["hdr"]["report"]["color"]["pix_fmt"] == "yuv420p10le", rendered["hdr"]["report"]["color"]["pix_fmt"])
            check("e6. the render report names the HDR transfer", rendered["hdr"]["report"]["color"]["transfer"] == "hlg", rendered["hdr"]["report"]["color"]["transfer"])
        except Exception:  # noqa: BLE001 - a crash is a failure, never a skip
            traceback.print_exc()
            print("\nharness crashed before the colour matrix was measured")
            return 1

    print(f"\n{PASS} PASS / {FAIL} FAIL / 0 SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())



